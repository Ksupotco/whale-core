"""#77 真机对账冒烟：多通道选路 + 持久化幂等 + 重启对账（手动运行，不进 CI）。

前置：
- whale-api.service 运行中（管理 REST 127.0.0.1:8180）；
- 目标机可达（SSH 通道；可选 ADB over tailscale）；
- token 从 /opt/whale/secrets/whale_api_token 读（只读，不回显）。

用法（N100 上）：
    python3 scripts/device_reconcile_smoke.py \
        --ssh-host 100.102.124.25 --ssh-key /root/.ssh/android \
        --ssh-host-key 'ssh-ed25519 AAAA...' \
        [--adb-serial 100.102.124.25:5555] [--label "magic8-silent"]

验证矩阵（全过才退出码 0）：
 1. 配对身份复用/新建（core 锚定 Ed25519 身份，主人侧 REST 批准仪式）；
 2. PUT /devices/transports 热装配 ssh(+adb) 通道；
 3. SSH 路由 device.shell 真实执行（设备侧探针文件行数证明）；
 4. 重复幂等键 → duplicate，设备侧探针行数不变（不重放）；
 5. systemctl restart whale-api 后：action/status 读出同一 action_id 同一
    终态（跨重启回执对账），重复键仍 duplicate 不重放；
 6.（可选 --adb-serial）adb 断开时 GUI 类动作 FAILED 落库（真实通道故障），
    adb 恢复后新键 SUCCEEDED（多通道回退/恢复），失败记录 first-wins 不变。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

API = "http://127.0.0.1:8180"
TOKEN_FILE = Path("/opt/whale/secrets/whale_api_token")
STATE_FILE = Path("/opt/whale/state/device_reconcile_smoke.json")
PROBE = "/data/local/tmp/whale_reconcile_probe.txt"
SCOPES = ["device.shell", "device.observe", "device.app.list",
          "device.logcat", "device.stop"]

FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILED.append(name)
    return ok


def api(token: str, method: str, path: str, body: dict | None = None,
        timeout: float = 70.0) -> tuple[int, dict]:
    req = urllib.request.Request(
        API + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode())
        except Exception:
            return exc.code, {}


def wait_api(token: str, timeout_s: float = 90.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            status, _ = api(token, "GET", "/api/v1/health", timeout=5)
            if status == 200:
                return True
        except Exception:
            pass
        time.sleep(1.0)
    return False


def ensure_device(token: str, label: str) -> str:
    """复用或新建 core 锚定的设备身份（配对码仪式走主人 REST）。"""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    state = {}
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except ValueError:
            state = {}
    if state.get("device_id"):
        status, body = api(token, "GET", f"/api/v1/devices/{state['device_id']}")
        if status == 200 and body.get("result", {}).get("status") == "active":
            print(f"[info] 复用既有设备身份 {state['device_id']}")
            return state["device_id"]
    key = Ed25519PrivateKey.generate()
    pubkey_hex = key.public_key().public_bytes_raw().hex()
    status, body = api(token, "POST", "/api/v1/devices/pair",
                       {"label": label, "pubkey_hex": pubkey_hex,
                        "scopes": SCOPES})
    if status != 200:
        raise SystemExit(f"pair 失败: {status} {body}")
    device_id = body["result"]["device_id"]
    status, body = api(token, "POST", "/api/v1/devices/pair/approve",
                       {"code": body["result"]["code"]})
    if status != 200:
        raise SystemExit(f"approve 失败: {status} {body}")
    STATE_FILE.write_text(json.dumps(
        {"device_id": device_id, "pubkey_hex": pubkey_hex, "label": label}),
        encoding="utf-8")
    print(f"[info] 新建设备身份 {device_id}（{label}）")
    return device_id


def shell(token: str, device_id: str, command: str, key: str,
          timeout: float = 30.0) -> tuple[int, dict]:
    return api(token, "POST", f"/api/v1/devices/{device_id}/action",
               {"capability": "device.shell",
                "arguments": {"command": command, "timeout_ms": 20000},
                "idempotency_key": key, "timeout": timeout})


def probe_lines(token: str, device_id: str, key: str) -> int:
    """探针文件行数（ssh 通道 shell summary 为 JSON：stdout_tail 取数）。"""
    status, body = shell(token, device_id, f"wc -l < {PROBE}", key)
    receipt = body.get("result", {}).get("receipt", {})
    summary = receipt.get("result", {}).get("summary", "")
    try:
        data = json.loads(summary)
        return int(str(data.get("stdout_tail", "")).strip())
    except (ValueError, AttributeError):
        pass
    try:
        return int(str(summary).strip().split()[0])
    except (ValueError, IndexError):
        return -1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ssh-host", required=True)
    parser.add_argument("--ssh-port", type=int, default=22)
    parser.add_argument("--ssh-user", default="root")
    parser.add_argument("--ssh-key", required=True)
    parser.add_argument("--ssh-host-key", required=True,
                        help="expected_host_key（pinning，ssh-keyscan 取得）")
    parser.add_argument("--adb-serial", default="")
    parser.add_argument("--label", default="reconcile-smoke")
    args = parser.parse_args()

    token = TOKEN_FILE.read_text(encoding="utf-8").strip()
    if not wait_api(token, 10):
        raise SystemExit("whale-api 不可达")

    # 1. 设备身份
    device_id = ensure_device(token, args.label)
    check("pair/identity", True, device_id)

    # 2. 通道配置热装配
    config: dict = {"ssh": {device_id: {
        "host": args.ssh_host, "port": args.ssh_port,
        "username": args.ssh_user, "key_path": args.ssh_key,
        "expected_host_key": args.ssh_host_key}}}
    if args.adb_serial:
        config["adb"] = {device_id: {"serial": args.adb_serial}}
    status, body = api(token, "PUT", "/api/v1/devices/transports",
                       {"config": config})
    registered = body.get("result", {}).get("registered", [])
    check("transports-put", status == 200 and "ssh" in registered,
          f"{status} registered={registered}")

    run_id = f"{int(time.time())}"
    key1 = f"recon:smoke:{run_id}:shell1"

    # 2.5 探针文件归零（setup 步骤，不计入检查；多次运行互不污染）
    shell(token, device_id, f": > {PROBE}", f"recon:smoke:{run_id}:reset")

    # 3. SSH 路由 shell 真实执行（探针追加一行）
    status, body = shell(token, device_id, f"echo MARK_{run_id} >> {PROBE}", key1)
    receipt = body.get("result", {}).get("receipt", {})
    check("ssh-shell-execute",
          status == 200 and receipt.get("status") == "succeeded",
          f"{status} {receipt.get('status')} {receipt.get('error_code') or ''}")
    action_id = receipt.get("action_id", "")

    # 设备侧证据：探针恰好 1 行
    lines = probe_lines(token, device_id, f"recon:smoke:{run_id}:wc1")
    check("probe-written-once", lines == 1, f"lines={lines}")

    # 4. 重复幂等键 → duplicate，不重放
    status, body = shell(token, device_id,
                         "echo SHOULD_NEVER_APPEND >> " + PROBE, key1)
    receipt2 = body.get("result", {}).get("receipt", {})
    check("idempotent-duplicate",
          status == 200 and receipt2.get("duplicate") is True
          and receipt2.get("action_id") == action_id,
          f"duplicate={receipt2.get('duplicate')}")
    lines = probe_lines(token, device_id, f"recon:smoke:{run_id}:wc2")
    check("no-replay-after-duplicate", lines == 1, f"lines={lines}")

    # 5. 重启对账
    print("[info] systemctl restart whale-api …")
    subprocess.run(["systemctl", "restart", "whale-api"], check=True)
    if not check("api-back-after-restart", wait_api(token, 90)):
        raise SystemExit(1)
    status, body = api(token, "POST",
                       f"/api/v1/devices/{device_id}/action/status",
                       {"idempotency_key": key1})
    record = body.get("result", {})
    check("restart-receipt-durable",
          status == 200 and record.get("state") == "succeeded"
          and record.get("action_id") == action_id,
          f"state={record.get('state')} action_id={record.get('action_id')}")
    # 重启后重复键仍不重放
    status, body = shell(token, device_id,
                         "echo SHOULD_NEVER_APPEND >> " + PROBE, key1)
    receipt3 = body.get("result", {}).get("receipt", {})
    check("restart-duplicate-no-replay",
          status == 200 and receipt3.get("duplicate") is True,
          f"duplicate={receipt3.get('duplicate')}")
    lines = probe_lines(token, device_id, f"recon:smoke:{run_id}:wc3")
    check("probe-still-once", lines == 1, f"lines={lines}")
    # 终极证据：探针里绝不含 SHOULD_NEVER_APPEND（三次重复键全程未重放）
    status, body = shell(token, device_id,
                         f"grep -c SHOULD_NEVER_APPEND {PROBE} || true",
                         f"recon:smoke:{run_id}:wc4")
    summary = body.get("result", {}).get("receipt", {}).get(
        "result", {}).get("summary", "")
    bad = -1
    try:
        bad = int(json.loads(summary).get("stdout_tail", "-1").strip())
    except (ValueError, AttributeError):
        pass
    check("never-replayed-marker-absent", bad == 0, f"bad_lines={bad}")

    # 6. ADB 通道故障/恢复（可选）。注意：adb disconnect 会被 transport 的
    # 懒重连自愈——真实故障须停掉端侧 adbd（经正交 SSH 通道）。
    if args.adb_serial:
        ssh_base = ["ssh", "-i", args.ssh_key, "-o", "BatchMode=yes",
                    "-o", "ConnectTimeout=8",
                    f"{args.ssh_user}@{args.ssh_host}"]
        subprocess.run(ssh_base + ["stop adbd"], check=False)
        time.sleep(2)
        subprocess.run(["adb", "disconnect", args.adb_serial], check=False)
        key_fail = f"recon:smoke:{run_id}:applist-down"
        status, body = api(token, "POST",
                           f"/api/v1/devices/{device_id}/action",
                           {"capability": "device.app.list",
                            "idempotency_key": key_fail, "timeout": 30})
        receipt_fail = body.get("result", {}).get("receipt", {})
        check("adb-down-fails-honest",
              status == 200 and receipt_fail.get("status") in ("failed", "timeout")
              and receipt_fail.get("error_code"),
              f"status={receipt_fail.get('status')} "
              f"error={receipt_fail.get('error_code')}")
        fail_action_id = receipt_fail.get("action_id", "")

        subprocess.run(ssh_base + ["start adbd"], check=False)
        time.sleep(3)
        subprocess.run(["adb", "connect", args.adb_serial], check=False)
        time.sleep(2)
        key_ok = f"recon:smoke:{run_id}:applist-up"
        status, body = api(token, "POST",
                           f"/api/v1/devices/{device_id}/action",
                           {"capability": "device.app.list",
                            "idempotency_key": key_ok, "timeout": 45})
        receipt_ok = body.get("result", {}).get("receipt", {})
        check("adb-recovered-succeeds",
              status == 200 and receipt_ok.get("status") == "succeeded",
              f"status={receipt_ok.get('status')} "
              f"error={receipt_ok.get('error_code') or ''}")
        # first-wins：失败记录不被后续成功翻案
        status, body = api(token, "POST",
                           f"/api/v1/devices/{device_id}/action/status",
                           {"idempotency_key": key_fail})
        record = body.get("result", {})
        check("failure-record-first-wins",
              record.get("state") in ("failed", "timeout")
              and record.get("action_id") == fail_action_id,
              f"state={record.get('state')}")

    print()
    if FAILED:
        print(f"FAILED: {len(FAILED)} 项未过: {FAILED}")
        return 1
    print("ALL PASS — #77 真机对账矩阵通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
