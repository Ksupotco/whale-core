"""日程提醒：5min 级 AI 判断 loop（替代固定定时——本仓库本无固定定时，
约束③记为无需迁移）。Tob 约束②：发送走 notify 共享配额/账本（与
proactive_greeting 同桶协调），不双发不绕频控。

每 tick（默认 300s，env 可调）：拼 prompt（前台活动 facts + 当前日程
临近/过期 + 上轮新消息 + 上海时区时间锚）→ 轻量模型三态判断
（发消息/弹提醒/沉默）→ 发走 notify 网关（共享频控/台账）；
静默时段（默认 23:00-08:00）一律沉默。

默认关闭（WHALE_SCHEDULE_REMIND=1 开启）——与 proactive_greeting 同纪律。
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, time as dtime

from whale_core.life.schedule import SHANGHAI, list_items

DEFAULT_INTERVAL_S = 300
QUIET_START = dtime(23, 0)
QUIET_END = dtime(8, 0)
TRI_STATES = ("发消息", "弹提醒", "沉默")

JUDGE_PROMPT = """你是日程提醒判断器。只回答一个词：发消息 / 弹提醒 / 沉默。

规则：
- 有日程即将开始（30 分钟内）或刚开始没提示过 → 发消息；
- 有日程临近（2 小时内）且重要 → 弹提醒；
- 其余 → 沉默。
日程：{items}
当前时间：{now}
前台近况：{facts}
"""


def _quiet_now(now: datetime, quiet_start: dtime, quiet_end: dtime) -> bool:
    t = now.time()
    if quiet_start <= quiet_end:
        return quiet_start <= t < quiet_end
    return t >= quiet_start or t < quiet_end


class ScheduleReminderLoop:
    """可单测的 tick + 可选后台循环；默认不启动（WHALE_SCHEDULE_REMIND=1 开）。"""

    def __init__(self, *, store, judge, notify, facts_hint=None,
                 interval_s: float = DEFAULT_INTERVAL_S,
                 quiet_start: dtime = QUIET_START, quiet_end: dtime = QUIET_END,
                 session_id: str = "schedule-loop", clock=time.monotonic):
        self.store = store
        self.judge = judge          # callable(prompt) -> str
        self.notify = notify        # callable(text, channel) -> receipt dict
        self.facts_hint = facts_hint  # callable() -> str（前台活动 facts）
        self.interval_s = max(30.0, float(interval_s))
        self.quiet_start, self.quiet_end = quiet_start, quiet_end
        self.session_id = session_id
        self.clock = clock
        self.last_verdict = ""
        self.last_items_count = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def tick(self) -> str:
        """跑一拍：返回 verdict（沉默/发消息/弹提醒/跳过）。"""
        now = datetime.now(SHANGHAI)
        if _quiet_now(now, self.quiet_start, self.quiet_end):
            self.last_verdict = "沉默"
            return "沉默"
        items = list_items(self.store, from_date=now.date().isoformat(),
                           to_date=(now.date()).isoformat())
        self.last_items_count = len(items)
        if not items:
            self.last_verdict = "沉默"
            return "沉默"
        items_text = "；".join(
            f"{it.get('time') or '全天'} {it.get('title')}" for it in items)[:400]
        facts = ""
        if self.facts_hint is not None:
            try:
                facts = str(self.facts_hint() or "")[:300]
            except Exception:
                facts = ""
        prompt = JUDGE_PROMPT.format(
            items=items_text, now=now.strftime("%Y-%m-%d %H:%M 星期"),
            facts=facts or "（无）")
        try:
            raw = str(self.judge(prompt) or "").strip()
        except Exception:
            self.last_verdict = "沉默"
            return "沉默"
        verdict = next((s for s in TRI_STATES if s in raw), "沉默")
        self.last_verdict = verdict
        if verdict == "沉默":
            return verdict
        text = "、".join(f"{it.get('time') or '全天'} {it.get('title')}"
                         for it in items)[:300]
        channel = "qq" if verdict == "发消息" else "phone"
        try:
            self.notify(f"日程提醒：{text}", channel)
        except Exception:
            pass  # 发送失败 fail-soft（频控/通道故障不炸 loop）
        return verdict

    def start_background(self) -> bool:
        if self._thread is not None:
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="whale-schedule-remind")
        self._thread.start()
        return True

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                pass
            self._stop.wait(self.interval_s)

    def stop_background(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
