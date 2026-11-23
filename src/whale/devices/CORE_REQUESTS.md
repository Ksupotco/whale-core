# Requests to core (outside `devices/`)

Status 2026-11-23 (clean break): items 1/2/6 are **done** — `contracts/device/` ships as package data
(`pyproject` force-include), `DevicesConfig` lives in `kernel/config.py`, and the console devices page exists.
Remaining notes below are non-blocking.

1. ~~**Packaging.**~~ Done: `devices/contract.py` looks in `<repo>/contracts/device`, then
   `<whale>/contracts/device`, then `$WHALE_CONTRACTS_DIR`; missing schemas fall back to a structural validator
   (envelope shape + required payload keys). `cryptography` is a hard dependency.
2. ~~**Config section.**~~ Done: `DevicesConfig` (`heartbeat_interval_s=30`, `heartbeat_timeout_s=90`,
   `challenge_ttl_s=30`, `receipt_grace_s=3`, `pair_ttl_s=600`).
3. **Public pairing endpoint.** `POST /api/devices/pair` needs no token, because the 6-digit code is the
   credential. It is single use, expires after 10 minutes, and stops accepting codes after 20 failures in 10
   minutes. The default `SecurityHeaders` already apply. If the server is exposed beyond tailscale, consider a
   per-IP rate limit in `server/http.py`.
4. **Memory pipeline.** `device.event` payloads are ExperienceEvents (contracts/device §9) and already flow into
   the phone digest → memory; never ingest `owner_sensitive` by default.
5. **Approvals from notification buttons.** `device.notification_action` is emitted with `category` and `target`.
   If core wants `approval.requested` notifications to carry approve/reject buttons, `Approvals` should subscribe.
   It should check that `target.type == "approval"`, then resolve the approval once and only through
   `approvals.resolve`. Per §13.3, a button press is a request and not an authorization by itself.
6. ~~**Console.**~~ Done: the devices page uses `devices.list` / `devices.get` / `devices.pair_start` /
   `devices.action` / `devices.query` and the `device.*` bus topics. (2026-11-23: device-first pairing and
   `devices.pair_approve` / `pending_pairings` were removed with the version-prefixed paths.)
