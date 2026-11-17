"""眼镜面 ``/v1/agent``：一句话 → 带门控的智能体回合（交接单 §2/§6/§7）。

门控模型（硬性，不可绕）：

- Bearer token（WHALE_GLASSES_TOKEN）即 owner 渠道身份；身份绝不取自请求体。
- 聊天 / 只读工具（时间、日程、服务日志摘要）→ 直接执行，``actions[].status=ok``。
- 自由 shell / 写文件 / 自修改 → **一律 pending 两阶段确认**（任何档位无例外；
  本模块不读 ``WHALE_SHELL_TIER=personal`` 来跳过确认——档位环境只管其它面）。
  ``pending.exact`` 是将要执行的确切内容，镜片原样显示，防 ASR「说 A 执行 B」。
- 命中 :data:`HIGH_RISK_PATTERNS` → ``denied``，**不给确认入口**（无 confirmId）。
  说明：issue #59 后共享层 shell_policy 不再维护字符串黑名单（真实隔离边界是
  Bubblewrap 执行器，fail-closed）；本清单是眼镜语音场景在两阶段确认之上叠加的
  纵深防御——挡「ASR 误识别 + 顺手确认」，不冒充安全边界。
- confirmId 一次性 + TTL 10 分钟 + 绑定 sessionId；确认回合忽略请求体里的
  任何新指令，只执行 pending 时登记的确切载荷。

回合时长模型（owner 2026-11-17 拍板，「可以接受超长后台任务」，门控链条
替代超时掐断，安全口径不松）：

- **interactive（默认）**：软限 ``WHALE_AGENT_SOFT_LIMIT_S``（默认 60s）。到软限
  **不杀**：未完成部分转后台续跑，同步回「已受理转后台，完成即推送」
  （actions 记 held+说明），结果经 deliver_result 投递。步数上限 6。
- **background（自修改确认回合等多步长任务）**：默认无硬截断——
  ``WHALE_AGENT_BG_TIMEOUT_S=0`` 表示不限（正数秒才启用截断，作用于自修改的
  全量 pytest 子进程）；步数上限 ``WHALE_AGENT_BG_MAX_STEPS``（默认 32，0=不限）。
- 自修改门控不变：两阶段回声确认 + git commit 前置 + 全量 pytest 全绿才生效 +
  失败自动回滚（全量约 268s 在后台回合跑到完成，不设截断）。
- 台账：每回合记 sessionId/意图摘要/动作裁决/结果摘要/回合类型/实际时长/步数
  （各 ≤200 字），风格对齐 ``ShellPolicy.audit``；**不记命令原始输出**。
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
import subprocess
import threading
import time
from datetime import datetime, timezone

MAX_TEXT_CHARS = 4000
MAX_EXACT_CHARS = 2000          # 与 shell_policy.MAX_COMMAND_CHARS 对齐
MAX_DESCRIPTION_CHARS = 200
MAX_DIGEST_CHARS = 200
MAX_WRITE_BYTES = 16 * 1024
PENDING_TTL_S = 600.0           # confirmId 一次性 + 10 分钟 TTL
PENDING_CAP = 64
LEDGER_CAP = 500
MAX_TURN_STEPS = 6              # interactive 步数上限
SOFT_LIMIT_S = 60.0             # interactive 软限（WHALE_AGENT_SOFT_LIMIT_S）
BG_MAX_STEPS = 32               # background 步数上限（WHALE_AGENT_BG_MAX_STEPS，0=不限）
BG_TIMEOUT_S = 0.0              # background 硬截断（WHALE_AGENT_BG_TIMEOUT_S，0=不限）

# ---------------------------------------------------------------------------
# 高危模式（眼镜面纵深防御；约 30 类，对照 cloud-operations §3.2）
# 命中 → denied，无确认入口。边界说明见模块 docstring。
# ---------------------------------------------------------------------------
_HIGH_RISK_RAW: tuple[tuple[str, str], ...] = (
    ("破坏文件系统", r"\brm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)+/\s*$"),
    ("破坏文件系统", r"\brm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)+/\*"),
    ("破坏文件系统", r"\brm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)+\~"),
    ("破坏文件系统", r"\bmkfs\b"),
    ("破坏文件系统", r"\bdd\b[^|;&]*\bof=/dev/"),
    ("破坏文件系统", r"\b(shred|wipefs)\b"),
    ("电源/init", r"\b(shutdown|poweroff|halt)\b"),
    ("电源/init", r"\breboot\b"),
    ("电源/init", r"\bkill\s+(-9\s+)?1\s*$"),
    ("fork bomb", r":\(\)\s*\{[^}]*:\s*\|\s*:"),
    ("提权", r"\bsudo\b"),
    ("提权", r"\bsu\b\s*(-|root)"),
    ("提权", r"\bchmod\s+(-[a-zA-Z]+\s+)*777\s+/"),
    ("提权", r"\bchown\s+(-[a-zA-Z]+\s+)+\S+\s+/etc"),
    ("凭据材料", r"/etc/shadow"),
    ("凭据材料", r"/etc/gshadow"),
    ("凭据材料", r"\.ssh\b"),
    ("凭据材料", r"authorized_keys"),
    ("凭据材料", r"\bprintenv\b"),
    ("凭据材料", r"\$\{?[A-Za-z_]*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)"),
    ("凭据材料", r"/opt/whale/secrets"),
    ("远程管道执行", r"\b(curl|wget)\b[^|;&]*\|\s*(sudo\s+)?(sh|bash|zsh)\b"),
    ("容器/命名空间", r"\b(docker|podman|kubectl|nsenter)\b"),
    ("自身服务破坏", r"\bsystemctl\s+(stop|disable|mask|restart|reload|kill)\b"),
    ("审计破坏", r"\bcrontab\s+(-[a-zA-Z]*r[a-zA-Z]*)\b"),
    ("审计破坏", r"\bhistory\s+-c\b"),
    ("审计破坏", r"\bjournalctl\b[^|;&]*--vacuum"),
    ("防火墙削弱", r"\biptables\s+(-[a-zA-Z]+\s+)*-F\b"),
    ("防火墙削弱", r"\bufw\s+disable\b"),
    ("shell rc 持久化", r">{1,2}\s*\S*(\.bashrc|\.zshrc|\.bash_profile|\.profile|\.zprofile)"),
    ("shell rc 持久化", r">{1,2}\s*/etc/profile"),
    ("git 远程不可逆", r"\bgit\s+push\b[^|;&]*--force\b"),
    ("git 远程不可逆", r"\bgit\s+push\b[^|;&]*\s-f\b"),
    ("git 远程不可逆", r"\bgit\s+push\b[^|;&]*--delete\b"),
    ("生产配置", r"/etc/systemd"),
    ("生产配置", r"\.env\b"),
)

HIGH_RISK_PATTERNS: tuple[tuple[str, "re.Pattern[str]"], ...] = tuple(
    (label, re.compile(pattern)) for label, pattern in _HIGH_RISK_RAW)


def high_risk_reason(command: str) -> str | None:
    """命中高危模式 → 返回原因标签；否则 None。"""
    text = command or ""
    for label, pattern in HIGH_RISK_PATTERNS:
        if pattern.search(text):
            return label
    return None


# ---------------------------------------------------------------------------
# 两阶段确认登记簿：一次性 confirmId + TTL + 绑定 session
# ---------------------------------------------------------------------------
class PendingConfirms:
    """线程安全（ThreadingHTTPServer）；cap 上限防内存膨胀。"""

    def __init__(self, *, ttl_s: float = PENDING_TTL_S, cap: int = PENDING_CAP,
                 clock=time.monotonic) -> None:
        self.ttl_s = float(ttl_s)
        self.cap = int(cap)
        self._clock = clock
        self._lock = threading.Lock()
        self._items: dict[str, dict] = {}

    def create(self, *, session_id: str, kind: str, description: str,
               exact: str, payload: dict) -> str:
        confirm_id = secrets.token_hex(16)
        now = self._clock()
        with self._lock:
            expired = [k for k, v in self._items.items() if v["expires"] <= now]
            for key in expired:
                self._items.pop(key, None)
            while len(self._items) >= self.cap:
                self._items.pop(next(iter(self._items)))
            self._items[confirm_id] = {
                "session": session_id, "kind": kind,
                "description": description[:MAX_DESCRIPTION_CHARS],
                "exact": exact[:MAX_EXACT_CHARS],
                "payload": payload, "expires": now + self.ttl_s}
        return confirm_id

    def consume(self, session_id: str, confirm_id: str) -> dict | None:
        """一次性：无论会话不符/过期与否，pop 即作废（防探测防重放）。"""
        with self._lock:
            item = self._items.pop(confirm_id, None)
        if item is None:
            return None
        if item["session"] != session_id:
            return None
        if item["expires"] <= self._clock():
            return None
        return item


# ---------------------------------------------------------------------------
# 台账（风格对齐 ShellPolicy.audit：只记摘要，不记原始输出）
# ---------------------------------------------------------------------------
def _c200(value) -> str:
    return str(value or "")[:200]


class StoreLedger:
    """每回合一条：sessionId/意图/动作/裁决/结果/回合类型/时长/步数。"""

    def __init__(self, store, *, cap: int = LEDGER_CAP) -> None:
        self.store = store
        self.cap = int(cap)

    def __call__(self, record: dict) -> None:
        try:
            key = "agentlog:{}:{}".format(
                datetime.now(timezone.utc).isoformat(), secrets.token_hex(4))
            self.store.put(key, {k: _c200(v) for k, v in record.items()})
            keys = [k for k in self.store.keys()
                    if str(k).startswith("agentlog:")]
            if len(keys) > self.cap:
                for old in sorted(keys)[: len(keys) - self.cap]:
                    self.store.delete(old)
        except Exception:
            pass  # 台账失败不阻塞回合（台账内容本身也绝不进 reply）


def _in_memory_ledger(records: list):
    return lambda record: records.append(
        {k: _c200(v) for k, v in record.items()})


# ---------------------------------------------------------------------------
# 自修改闭环：checkpoint commit → patch 落 → 全量测试 → 生效 | 自动回滚
# ---------------------------------------------------------------------------
#: 自修改禁止触碰的路径（安全策略/本服务自身门禁/生产配置/凭据形状）
PROTECTED_PATHS: tuple[str, ...] = (
    "src/whale_core/cloud/shell_policy.py",
    "src/whale_core/cloud/shell_executor.py",
    "src/whale_core/plugins/policy.py",
    "src/whale_core/plugins/shell_tool.py",
    "src/whale_core/plugins/gateway.py",
    "src/whale_core/api/glasses.py",
    "src/whale_core/api/glasses_agent.py",
)
_PROTECTED_MARKERS = ("secrets/", "/secrets/", ".env", "/etc/systemd")


def _default_runner(argv, cwd, timeout_s) -> tuple[int, str]:
    proc = subprocess.run([str(a) for a in argv], cwd=str(cwd),
                          capture_output=True, text=True, timeout=timeout_s)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


class SelfModifier:
    """patch_fn 是唯一缺口：指令 → {"summary": str, "files": {相对路径: 全文}}。

    未配置 patch_fn 时 fail-closed（plan() 返回 None）。runner 同时服务 git 与
    测试命令（测试注入假件）。``test_timeout_s=None``（默认）= 全量测试不设
    硬截断（owner 2026-11-17 拍板：后台类回合跑完为止；>0 才截断）。
    回滚 = 逐文件恢复备份 + git reset --hard 前置 commit；绝不 git clean
    （仓库里有他人预置的未跟踪文件）。
    """

    def __init__(self, repo_dir, *, patch_fn=None, runner=None,
                 test_cmd=("python3", "-m", "pytest", "-q"),
                 test_timeout_s: float | None = None,
                 protected: tuple[str, ...] = PROTECTED_PATHS) -> None:
        self.repo_dir = os.path.realpath(str(repo_dir))
        self.patch_fn = patch_fn
        self.runner = runner or _default_runner
        self.test_cmd = tuple(test_cmd)
        self.test_timeout_s = test_timeout_s
        self.protected = tuple(protected)

    # -- git helpers --------------------------------------------------------
    def _git(self, *argv, timeout_s: float = 60.0) -> tuple[int, str]:
        return self.runner(("git", *argv), self.repo_dir, timeout_s)

    def _head(self) -> str:
        rc, out = self._git("rev-parse", "HEAD")
        return out.strip() if rc == 0 else ""

    # -- 路径门禁 ------------------------------------------------------------
    def _resolve(self, relpath: str) -> str | None:
        rel = str(relpath or "").strip().lstrip("/")
        if not rel or "\x00" in rel:
            return None
        full = os.path.realpath(os.path.join(self.repo_dir, rel))
        if full != self.repo_dir and not full.startswith(self.repo_dir + os.sep):
            return None
        return full

    def _protected(self, relpath: str) -> str | None:
        rel = str(relpath or "").replace("\\", "/").lstrip("/")
        for marker in _PROTECTED_MARKERS:
            if marker in rel:
                return f"protected marker {marker.strip('/')}"
        for path in self.protected:
            if rel == path:
                return f"protected path {path}"
        return None

    # -- 计划与执行 -----------------------------------------------------------
    def plan(self, instruction: str) -> dict | None:
        if self.patch_fn is None:
            return None
        plan = self.patch_fn(instruction)
        if not isinstance(plan, dict):
            return None
        files = plan.get("files")
        if not isinstance(files, dict) or not files:
            return None
        normalized = {}
        for rel, content in files.items():
            if not isinstance(content, str) or self._resolve(rel) is None:
                return None
            normalized[str(rel)] = content
        return {"summary": _c200(plan.get("summary") or instruction),
                "files": normalized}

    def describe(self, plan: dict) -> str:
        """pending.exact：文件清单 + 每文件 sha256/字节数（镜片原样显示）。"""
        lines = [f"自修改：{plan['summary']}"]
        for rel, content in sorted(plan["files"].items()):
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
            lines.append(f"{rel} ({len(content.encode('utf-8'))}B sha256:{digest})")
        return "\n".join(lines)[:MAX_EXACT_CHARS]

    def run(self, plan: dict) -> dict:
        files = plan.get("files") or {}
        for rel in files:
            reason = self._protected(rel)
            if reason:
                return {"ok": False, "detail": f"refused: {reason}"}
            if self._resolve(rel) is None:
                return {"ok": False, "detail": f"refused: bad path {rel}"}
        # 1) checkpoint：已跟踪改动先入库（未跟踪文件原样保留，不属于本次状态）
        rc, out = self._git("status", "--porcelain", "--untracked-files=no")
        if rc != 0:
            return {"ok": False, "detail": "git unavailable"}
        if out.strip():
            rc, out = self._git("add", "-u")
            if rc == 0:
                rc, out = self._git(
                    "commit", "-m", "chore: agent self-modify checkpoint")
            if rc != 0:
                return {"ok": False, "detail": "checkpoint commit failed"}
        checkpoint = self._head()
        if not checkpoint:
            return {"ok": False, "detail": "no HEAD commit"}
        # 2) 逐文件备份并落 patch
        backups: dict[str, bytes | None] = {}
        try:
            for rel, content in files.items():
                full = self._resolve(rel)
                existed = os.path.exists(full)
                backups[rel] = (open(full, "rb").read() if existed else None)
                os.makedirs(os.path.dirname(full), exist_ok=True)
                with open(full, "w", encoding="utf-8") as fh:
                    fh.write(content)
        except OSError as exc:
            self._restore(backups)
            self._git("reset", "--hard", checkpoint)
            return {"ok": False, "detail": f"apply failed: {type(exc).__name__}",
                    "rolled_back_to": checkpoint}
        # 3) 全量测试（后台类回合：默认无硬截断，跑完为止）
        try:
            rc, out = self.runner(self.test_cmd, self.repo_dir,
                                  self.test_timeout_s)
        except Exception as exc:
            rc, out = 1, f"{type(exc).__name__}"
        if rc != 0:
            self._restore(backups)
            self._git("reset", "--hard", checkpoint)
            return {"ok": False, "detail": "tests failed; rolled back",
                    "rolled_back_to": checkpoint,
                    "test_tail": _c200(out[-400:] if isinstance(out, str) else "")}
        # 4) 生效：只入库本次 patch 触及的文件
        rc, out = self._git("add", "--", *[rel for rel in files])
        if rc == 0:
            rc, out = self._git(
                "commit", "-m", f"agent self-modify: {plan['summary'][:72]}")
        if rc != 0:
            self._restore(backups)
            self._git("reset", "--hard", checkpoint)
            return {"ok": False, "detail": "final commit failed; rolled back",
                    "rolled_back_to": checkpoint}
        return {"ok": True, "detail": "committed", "commit": self._head()}

    def _restore(self, backups: dict) -> None:
        for rel, data in backups.items():
            full = self._resolve(rel)
            if full is None:
                continue
            try:
                if data is None:
                    if os.path.exists(full):
                        os.remove(full)
                else:
                    with open(full, "wb") as fh:
                        fh.write(data)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# 智能体本体
# ---------------------------------------------------------------------------
_CMD_PREFIXES = ("cmd:", "shell:", "执行:")
_SELFMODIFY_PREFIXES = ("selfmodify:", "self-modify:", "自修改:")
_WRITE_PREFIXES = ("write:", "写文件:")

_TIME_KEYS = ("几点", "现在时间", "what time")
_SCHEDULE_KEYS = ("日程", "安排", "行程", "日历")
_LOG_KEYS = ("日志", "报错", "最近错误")


def _classify(text: str) -> tuple[str, object]:
    stripped = (text or "").strip()
    lower = stripped.lower()
    for prefix in _CMD_PREFIXES:
        if lower.startswith(prefix):
            command = stripped[len(prefix):].strip()
            return ("shell", command) if command else ("chat", stripped)
    for prefix in _SELFMODIFY_PREFIXES:
        if lower.startswith(prefix) or stripped.startswith(prefix):
            instruction = stripped[len(prefix):].strip()
            return ("self_modify", instruction) if instruction else ("chat", stripped)
    for prefix in _WRITE_PREFIXES:
        if lower.startswith(prefix) or stripped.startswith(prefix):
            rest = stripped[len(prefix):].lstrip()
            head, sep, content = rest.partition("\n")
            relpath = head.strip()
            if relpath and sep:
                return ("write", (relpath, content))
            return ("chat", stripped)
    if any(k in stripped for k in _TIME_KEYS) or "what time" in lower:
        return ("tool", "what_time")
    if any(k in stripped for k in _SCHEDULE_KEYS):
        return ("tool", "schedule")
    if any(k in stripped for k in _LOG_KEYS):
        return ("tool", "log_tail")
    return ("chat", stripped)


def _env_float(env: dict, key: str, default: float) -> float:
    try:
        return float(env.get(key) or default)
    except (TypeError, ValueError):
        return default


def _env_int(env: dict, key: str, default: int) -> int:
    try:
        return int(env.get(key) or default)
    except (TypeError, ValueError):
        return default


class GlassesAgent:
    """依赖全部可注入：store/chat/executor/ledger/pending/self_modifier/deliver。

    回合模型见模块 docstring：interactive 软限转后台 + deliver 推送；
    自修改确认回合为 background 类，同步跑完、默认无硬截断。
    """

    def __init__(self, *, store=None, chat=None, executor=None, ledger=None,
                 pending: PendingConfirms | None = None, self_modifier=None,
                 workspace=None, deliver=None,
                 max_steps: int | None = None, bg_max_steps: int | None = None,
                 soft_limit_s: float | None = None,
                 bg_timeout_s: float | None = None,
                 clock=time.monotonic, log_reader=None, env=None) -> None:
        env = dict(env or {})
        self.store = store
        self.chat = chat
        self._executor = executor
        if ledger is not None:
            self.ledger = ledger
        elif store is not None:
            self.ledger = StoreLedger(store)
        else:
            self.ledger = _in_memory_ledger([])
        self.pending = pending or PendingConfirms()
        self.self_modifier = self_modifier
        self.workspace = str(workspace) if workspace else ""
        self.deliver = deliver
        self.max_steps = (max_steps if max_steps is not None
                          else _env_int(env, "WHALE_AGENT_MAX_STEPS", MAX_TURN_STEPS))
        self.bg_max_steps = (bg_max_steps if bg_max_steps is not None
                             else _env_int(env, "WHALE_AGENT_BG_MAX_STEPS",
                                           BG_MAX_STEPS))
        self.soft_limit_s = (soft_limit_s if soft_limit_s is not None
                             else _env_float(env, "WHALE_AGENT_SOFT_LIMIT_S",
                                             SOFT_LIMIT_S))
        self.bg_timeout_s = (bg_timeout_s if bg_timeout_s is not None
                             else _env_float(env, "WHALE_AGENT_BG_TIMEOUT_S",
                                             BG_TIMEOUT_S))
        self.clock = clock
        self.log_reader = log_reader
        self.env = env

    # -- 懒装配：真实沙箱执行器（fail-closed；root 时按 shell_tool 先例降权） --
    def _real_executor(self):
        if self._executor is None:
            from whale_core.cloud.shell_executor import ShellExecutor
            from whale_core.cloud.shell_policy import ShellPolicy, ShellTier
            drop = None
            geteuid = getattr(os, "geteuid", None)
            if callable(geteuid) and geteuid() == 0:
                drop = "nobody"
            workspace = self.workspace or os.getcwd()
            os.makedirs(workspace, exist_ok=True)
            # 眼镜面授权 = Bearer owner + 两阶段回声确认；此处显式构造 owner 级
            # 策略对象与环境档解耦——不是读取 personal 档配置跳过确认（本模块
            # 任何路径都不跳过 pending 两阶段）。
            self._executor = ShellExecutor(
                ShellPolicy(tier=ShellTier.PERSONAL),
                allowed_cwds=(workspace,), sandbox="bubblewrap",
                drop_to_user=drop)
        return self._executor

    # -- 主入口 ---------------------------------------------------------------
    def handle_turn(self, text: str, session_id: str,
                    confirm_id: str | None = None) -> dict:
        started = self.clock()
        session_id = str(session_id or "")[:64]
        if confirm_id:
            item = self.pending.consume(session_id, str(confirm_id))
            if item is None:
                self._audit(session_id, "confirm", str(confirm_id)[:16],
                            "fail", "confirmId 无效/过期/已使用",
                            turn_class="interactive", started=started)
                return self._response(
                    session_id, "这个确认已过期或不存在，请重新发起指令。",
                    actions=[{"kind": "confirm", "summary": "确认执行",
                              "status": "fail",
                              "output_digest": "confirmId 无效/过期/已使用"}])
            if item["kind"] == "self_modify":
                return self._confirm_self_modify(session_id, item, started)
            # shell/write 确认 = interactive 类（软限转后台，完成即推送）
            return self._interactive(
                session_id, item["kind"], item["description"], started,
                lambda: self._exec_confirmed(session_id, item, started))
        intent, arg = _classify(text)
        if intent == "chat":
            return self._interactive(
                session_id, "chat", "companion", started,
                lambda: self._chat_turn(str(arg), session_id, started))
        if intent == "tool":
            return self._interactive(
                session_id, f"tool:{arg}", str(arg), started,
                lambda: self._readonly_turn(str(arg), session_id, started))
        if intent == "shell":
            return self._gate_shell(str(arg), session_id, started)
        if intent == "write":
            relpath, content = arg  # type: ignore[misc]
            return self._gate_write(relpath, content, session_id, started)
        return self._gate_self_modify(str(arg), session_id, started)

    # -- interactive 软限转后台 -------------------------------------------------
    def _interactive(self, session_id: str, kind: str, summary: str,
                     started: float, fn) -> dict:
        """软限内完成直接返回；超限**不杀**：后台续跑，完成经 deliver 推送。"""
        done = threading.Event()
        box: dict = {}

        def work():
            try:
                box["resp"] = fn()
            except Exception as exc:  # noqa: BLE001 — 回合故障不炸连接
                box["resp"] = self._response(
                    session_id, f"回合内部故障：{type(exc).__name__}。")
                try:
                    self._audit(session_id, kind, summary, "fail",
                                type(exc).__name__, turn_class="interactive",
                                started=started)
                except Exception:
                    pass
            finally:
                done.set()

        threading.Thread(target=work, daemon=True).start()
        if done.wait(max(0.0, self.soft_limit_s)):
            return box["resp"]
        self._audit(session_id, kind, summary, "held",
                    f"超软限 {int(self.soft_limit_s)}s 转后台",
                    turn_class="interactive", started=started)

        def push():
            done.wait()
            resp = box.get("resp") or {}
            text = str(resp.get("reply") or "").strip()
            pushed = False
            if text and self.deliver is not None:
                try:
                    self.deliver(text, session_id)
                    pushed = True
                except Exception:
                    pushed = False
            self._audit(session_id, kind, summary,
                        "ok" if pushed else "fail",
                        "后台完成" + ("已推送" if pushed else "（无推送通道）"),
                        turn_class="background", started=started)

        threading.Thread(target=push, daemon=True).start()
        note = (f"超过软限 {int(self.soft_limit_s)}s，已受理转后台，完成即推送。"
                if self.deliver is not None else
                f"超过软限 {int(self.soft_limit_s)}s，已受理转后台"
                "（本通道无推送，结果不回流，请稍后主动询问）。")
        return self._response(
            session_id, note,
            actions=[{"kind": kind, "summary": _c200(summary),
                      "status": "held", "output_digest": _c200(note)}])

    # -- 回合步数预算 ------------------------------------------------------------
    def _steps_over(self, steps: int, turn_class: str) -> str | None:
        limit = self.max_steps if turn_class == "interactive" else self.bg_max_steps
        if limit and steps > limit:
            return f"超过单回合步数上限 {limit}"
        return None

    # -- 聊天 / 只读 ------------------------------------------------------------
    def _chat_turn(self, text: str, session_id: str, started: float) -> dict:
        if self.chat is None:
            reply = "我在，但聊天链路未装配。"
        else:
            reply = str(self.chat(text, f"glasses:{session_id}") or "").strip() \
                or "（空回复）"
        self._audit(session_id, "chat", "companion", "ok", reply,
                    turn_class="interactive", started=started)
        return self._response(session_id, reply)

    def _readonly_turn(self, tool: str, session_id: str,
                       started: float) -> dict:
        held = self._steps_over(1, "interactive")
        if held:
            return self._held(session_id, tool, tool, held, started)
        if tool == "what_time":
            from whale_core.api.glasses_context import SHANGHAI
            now = datetime.now(SHANGHAI)
            reply = "现在 {}（服务器时间）。".format(
                now.strftime("%Y-%m-%d %H:%M"))
            digest = reply
        elif tool == "schedule":
            from whale_core.api.glasses_context import request_context_hint
            hint = request_context_hint(self.store) if self.store is not None else ""
            reply = ("今明日程：" + hint.replace("当轮手机事实资料：\n", "").strip()) \
                if hint else "今天和明天都没有同步到日程。"
            digest = _c200(reply)
        else:  # log_tail
            ok, digest = self._read_logs()
            reply = ("最近的异常日志摘要：\n" + digest) if ok else \
                    f"读不到服务日志（{digest}）。"
        self._audit(session_id, f"tool:{tool}", tool, "ok", digest,
                    turn_class="interactive", started=started)
        return self._response(
            session_id, reply,
            actions=[{"kind": "read", "summary": _tool_summary(tool),
                      "status": "ok", "output_digest": _c200(digest)}])

    def _read_logs(self) -> tuple[bool, str]:
        reader = self.log_reader or self._journalctl_tail
        try:
            text = str(reader() or "")
        except Exception as exc:
            return False, type(exc).__name__
        if not text.strip():
            return False, "empty log"
        interesting = [line for line in text.splitlines()
                       if re.search(r"error|fail|exception|traceback", line, re.I)]
        tail = interesting[-5:] if interesting else text.splitlines()[-5:]
        return True, _c200("\n".join(tail))

    @staticmethod
    def _journalctl_tail() -> str:
        # 固定 argv 只读诊断（等价 managed 白名单 tail_bridge_log）；无用户输入拼接
        proc = subprocess.run(
            ["journalctl", "-u", "whale-glasses.service", "-n", "80",
             "--no-pager", "-o", "cat"],
            capture_output=True, text=True, timeout=10)
        return proc.stdout or proc.stderr or ""

    # -- 门控：shell ------------------------------------------------------------
    def _gate_shell(self, command: str, session_id: str, started: float) -> dict:
        summary = f"执行命令：{command[:80]}"
        reason = high_risk_reason(command)
        if reason:
            self._audit(session_id, "shell", summary, "denied", reason,
                        turn_class="interactive", started=started)
            return self._response(
                session_id,
                f"这条命令属于高危操作（{reason}），我不能执行，也没有确认入口。",
                actions=[{"kind": "shell", "summary": _c200(summary),
                          "status": "denied",
                          "output_digest": _c200(f"高危拒绝：{reason}")}])
        confirm_id = self.pending.create(
            session_id=session_id, kind="shell",
            description="在受控沙箱工作区执行 shell 命令",
            exact=command, payload={"command": command})
        self._audit(session_id, "shell", summary, "held", "等待确认",
                    turn_class="interactive", started=started)
        return self._response(
            session_id,
            f"这需要执行一条 shell 命令，请确认镜片上的内容无误后我再执行：{command}",
            actions=[{"kind": "shell", "summary": _c200(summary),
                      "status": "held", "output_digest": "等待确认"}],
            pending={"confirmId": confirm_id, "kind": "shell",
                     "description": "在受控沙箱工作区执行 shell 命令",
                     "exact": command[:MAX_EXACT_CHARS]})

    # -- 门控：写文件 ------------------------------------------------------------
    def _gate_write(self, relpath: str, content: str, session_id: str,
                    started: float) -> dict:
        summary = f"写文件：{relpath[:80]}"
        reason = self._write_refusal(relpath, content)
        if reason:
            denied = reason.startswith("高危") or "路径" in reason
            verdict = "denied" if denied else "fail"
            self._audit(session_id, "write", summary, verdict, reason,
                        turn_class="interactive", started=started)
            reply = (f"这个写入目标我不能碰（{reason}），没有确认入口。"
                     if denied else f"写入请求无效：{reason}。")
            return self._response(
                session_id, reply,
                actions=[{"kind": "write", "summary": _c200(summary),
                          "status": "denied" if denied else "fail",
                          "output_digest": _c200(reason)}])
        exact = f"write {relpath} ({len(content.encode('utf-8'))}B)\n{content}"[
            :MAX_EXACT_CHARS]
        confirm_id = self.pending.create(
            session_id=session_id, kind="write",
            description=f"写入工作区文件 {relpath}",
            exact=exact,
            payload={"relpath": relpath, "content": content})
        self._audit(session_id, "write", summary, "held", "等待确认",
                    turn_class="interactive", started=started)
        return self._response(
            session_id,
            f"这需要写入文件 {relpath}，请确认镜片上的内容后我再写入。",
            actions=[{"kind": "write", "summary": _c200(summary),
                      "status": "held", "output_digest": "等待确认"}],
            pending={"confirmId": confirm_id, "kind": "write",
                     "description": f"写入工作区文件 {relpath}",
                     "exact": exact})

    def _write_refusal(self, relpath: str, content: str) -> str | None:
        if not self.workspace:
            return "工作区未配置"
        rel = str(relpath or "").strip().lstrip("/")
        if not rel or ".." in rel.split("/") or "\x00" in rel:
            return "路径非法"
        risk = high_risk_reason(rel)
        if risk:
            return f"高危目标：{risk}"
        base = os.path.realpath(self.workspace)
        full = os.path.realpath(os.path.join(base, rel))
        if full != base and not full.startswith(base + os.sep):
            return "路径越界"
        if len(content.encode("utf-8")) > MAX_WRITE_BYTES:
            return f"内容超过 {MAX_WRITE_BYTES} 字节上限"
        return None

    # -- 门控：自修改 ------------------------------------------------------------
    def _gate_self_modify(self, instruction: str, session_id: str,
                          started: float) -> dict:
        summary = f"自修改：{instruction[:80]}"
        if self.self_modifier is None:
            self._audit(session_id, "self_modify", summary, "fail", "未启用",
                        turn_class="interactive", started=started)
            return self._response(
                session_id, "自修改能力未启用。",
                actions=[{"kind": "self_modify", "summary": _c200(summary),
                          "status": "fail", "output_digest": "未启用"}])
        plan = self.self_modifier.plan(instruction)
        if plan is None:
            self._audit(session_id, "self_modify", summary, "fail",
                        "补丁生成不可用", turn_class="interactive",
                        started=started)
            return self._response(
                session_id, "现在没法为这条改动生成补丁，未执行任何修改。",
                actions=[{"kind": "self_modify", "summary": _c200(summary),
                          "status": "fail",
                          "output_digest": "补丁生成不可用"}])
        refused = next((self.self_modifier._protected(rel)
                        for rel in plan["files"]), None)
        refused = refused or next(
            (f"bad path {rel}" for rel in plan["files"]
             if self.self_modifier._resolve(rel) is None), None)
        if refused:
            self._audit(session_id, "self_modify", summary, "denied", refused,
                        turn_class="interactive", started=started)
            return self._response(
                session_id,
                f"这个改动要碰受保护的位置（{refused}），我不能执行，也没有确认入口。",
                actions=[{"kind": "self_modify", "summary": _c200(summary),
                          "status": "denied", "output_digest": _c200(refused)}])
        exact = self.self_modifier.describe(plan)
        confirm_id = self.pending.create(
            session_id=session_id, kind="self_modify",
            description=f"自修改 whale-core：{plan['summary']}",
            exact=exact, payload={"plan": plan})
        self._audit(session_id, "self_modify", summary, "held", "等待确认",
                    turn_class="interactive", started=started)
        return self._response(
            session_id,
            "这需要修改我自己的代码，改动清单已显示在镜片上，确认后我会先备份、"
            "改完跑全量测试，绿了才生效，失败自动回滚（长任务，可能需要几分钟）。",
            actions=[{"kind": "self_modify", "summary": _c200(summary),
                      "status": "held", "output_digest": "等待确认"}],
            pending={"confirmId": confirm_id, "kind": "self_modify",
                     "description": f"自修改 whale-core：{plan['summary']}",
                     "exact": exact})

    # -- 确认执行 ---------------------------------------------------------------
    def _exec_confirmed(self, session_id: str, item: dict,
                        started: float) -> dict:
        held = self._steps_over(1, "interactive")
        if held:
            return self._held(session_id, item["kind"], item["description"],
                              held, started)
        if item["kind"] == "shell":
            return self._exec_shell(session_id, item, started)
        return self._exec_write(session_id, item, started)

    def _exec_shell(self, session_id: str, item: dict, started: float) -> dict:
        command = item["payload"]["command"]
        try:
            result = self._real_executor().run(
                command, cwd=self.workspace or None, is_owner=True)
        except Exception as exc:
            self._audit(session_id, "shell", item["description"], "fail",
                        type(exc).__name__, turn_class="interactive",
                        started=started)
            return self._response(
                session_id, f"执行器不可用（{type(exc).__name__}），命令没有执行。",
                actions=[{"kind": "shell", "summary": _c200(item["description"]),
                          "status": "fail",
                          "output_digest": type(exc).__name__}])
        ok = result.status == "success"
        digest = _c200((result.stdout or result.stderr
                        or result.verdict_reason or result.status))
        self._audit(session_id, "shell", item["description"],
                    "ok" if ok else "fail", f"status={result.status}",
                    turn_class="interactive", started=started)
        reply = ("执行完成：" if ok else "执行未成功：") + (digest or "（无输出）")
        return self._response(
            session_id, reply,
            actions=[{"kind": "shell", "summary": _c200(item["description"]),
                      "status": "ok" if ok else "fail",
                      "output_digest": digest}])

    def _exec_write(self, session_id: str, item: dict, started: float) -> dict:
        relpath = item["payload"]["relpath"]
        content = item["payload"]["content"]
        base = os.path.realpath(self.workspace)
        full = os.path.realpath(os.path.join(base, relpath.lstrip("/")))
        try:
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8") as fh:
                fh.write(content)
        except OSError as exc:
            self._audit(session_id, "write", item["description"], "fail",
                        type(exc).__name__, turn_class="interactive",
                        started=started)
            return self._response(
                session_id, f"写入失败：{type(exc).__name__}。",
                actions=[{"kind": "write", "summary": _c200(item["description"]),
                          "status": "fail",
                          "output_digest": type(exc).__name__}])
        self._audit(session_id, "write", item["description"], "ok",
                    f"{len(content.encode('utf-8'))}B",
                    turn_class="interactive", started=started)
        return self._response(
            session_id, f"已写入 {relpath}（{len(content.encode('utf-8'))} 字节）。",
            actions=[{"kind": "write", "summary": _c200(item["description"]),
                      "status": "ok",
                      "output_digest": f"{len(content.encode('utf-8'))}B written"}])

    def _confirm_self_modify(self, session_id: str, item: dict,
                             started: float) -> dict:
        # background 类回合（owner 2026-11-17 拍板）：同步跑完全量测试，
        # 默认无硬截断（WHALE_AGENT_BG_TIMEOUT_S>0 才截断，作用于 pytest 子进程）；
        # 步数上限 bg_max_steps。台账记 turnClass=background 与实际时长。
        held = self._steps_over(1, "background")
        if held:
            return self._held(session_id, "self_modify", item["description"],
                              held, started, turn_class="background")
        result = self.self_modifier.run(item["payload"]["plan"])
        ok = bool(result.get("ok"))
        digest = _c200(result.get("detail") or "")
        self._audit(session_id, "self_modify", item["description"],
                    "ok" if ok else "fail", digest,
                    turn_class="background", started=started)
        if ok:
            reply = "修改完成，全量测试通过，已提交生效。"
        else:
            reply = f"自修改没有生效（{digest}），代码已回滚到修改前状态。"
        return self._response(
            session_id, reply,
            actions=[{"kind": "self_modify",
                      "summary": _c200(item["description"]),
                      "status": "ok" if ok else "fail",
                      "output_digest": digest}])

    # -- 公共件 -----------------------------------------------------------------
    def _held(self, session_id: str, kind: str, summary: str, reason: str,
              started: float, *, turn_class: str = "interactive") -> dict:
        self._audit(session_id, kind, summary, "held", reason,
                    turn_class=turn_class, started=started)
        return self._response(
            session_id, f"这一步被安全上限拦下：{reason}。",
            actions=[{"kind": kind, "summary": _c200(summary),
                      "status": "held", "output_digest": _c200(reason)}])

    def _audit(self, session_id: str, intent: str, action: str,
               verdict: str, result: str, *, turn_class: str,
               started: float | None, steps: int = 1) -> None:
        duration_ms = 0
        if started is not None:
            try:
                duration_ms = max(0, int((self.clock() - started) * 1000))
            except Exception:
                duration_ms = 0
        self.ledger({"sessionId": _c200(session_id), "intent": _c200(intent),
                     "action": _c200(action), "verdict": _c200(verdict),
                     "result": _c200(result),
                     "turnClass": _c200(turn_class),
                     "durationMs": _c200(duration_ms),
                     "steps": _c200(steps),
                     "at": datetime.now(timezone.utc).isoformat()})

    @staticmethod
    def _response(session_id: str, reply: str, *, actions: list | None = None,
                  pending: dict | None = None) -> dict:
        return {"sessionId": session_id, "reply": reply,
                "actions": list(actions or []), "pending": pending}


def _tool_summary(tool: str) -> str:
    return {"what_time": "查询当前时间", "schedule": "查询今明日程",
            "log_tail": "读取服务日志摘要"}.get(tool, tool)
