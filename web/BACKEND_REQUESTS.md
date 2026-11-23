# Backend requests from the web console

The console works against the current backend. Every item below already has a client-side
workaround. Items are ordered by user impact.

## 1. Serve `.js` as `text/javascript` (important on Windows)

**Where:** `server/http.py`, at import time.

On Windows, Python's `mimetypes` reads the registry, and that often maps `.js` to `text/plain`.
The server also sends `X-Content-Type-Options: nosniff`. With both, the browser refuses to
run `/app/main.js` as a module, and the console stays on the boot splash.

```python
import mimetypes
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/svg+xml", ".svg")
```

**Workaround:** none possible from the client.

## 2. `chat.send {wait:false}` should return the `run_id`

The response is currently only `{conversation_id, accepted}`.

**Workaround:** the console finds its run among `run.started` events. It matches on
`mode == "chat"`, `input == text`, the conversation, and `created_at` within 10 s. Two identical
messages sent within the same second to a brand-new conversation could be confused.

**Proposal:** create the run id before `asyncio.create_task`, for example with
`RunRequest(run_id=...)`, and return `{conversation_id, run_id, accepted}`.

## 3. Approval steps should carry the `approval_id` from the start

The `run.step` event for an `approval` step is emitted before the Approval is created. Its
`input` is only `{capability, args}`, and `approval_id` first appears in `output` when the step
ends.

**Workaround:** the inline approve/deny buttons in 思维轨迹 look up the pending approval by
`run_id` and `capability`.

**Proposal:** create the approval first and include `approval_id` in the step `input`.

## 4. Include `ts` in live event frames

The WS `event` payload is `{topic, data, event_id}`. `events.query` rows carry `ts`, but live
frames do not.

**Workaround:** the console stamps the receive time on live frames.

**Proposal:** add `"ts": ev.ts` to the payload in `http.py` (`offer`).

## 5. `memory.list`: a `scope` filter and more sort orders

`MemoryStore.list` already supports `scope=`, but `MemList` does not expose it.

**Workaround:** the scope chips (主人 / 共享 / 群聊 / 私聊) filter on the client, over at most
300 items.

**Proposal:**
- Add `scope: str | None`, accepting `owner`, `shared`, `group:*` and `person`, the same
  categories as `stats().by_scope`.
- Allow `access_count DESC` and `last_access_at DESC` in the order whitelist. Today "最常想起"
  is sorted on the client.

## 6. `models.test` should accept `role: "embedding"`

`Role` is `Literal["chat","fast","vision"]`, so the 测试连接 button is hidden for the 向量 role.

**Proposal:** allow `embedding` and probe it with a one-word embedding call.

## 7. Apply `devices.digest_interval_min` live

`sys_phone_digest` is only ensured in `DeviceService.start()`. Changing the interval in
设置 → 设备 takes effect after a restart, and the console says so.

**Proposal:** in `App._on_config`, when `"devices" in patch`, call the same `ensure_system`.

## 8. Return the observation from owner-initiated `device.observe`

`devices.action` returns the ledger view: status, summary and `output_ref`. The 观察屏幕 quick
action can therefore show only the status and summary.

**Proposal:** return `compact_observation(...)` as `observation` for `device.observe` when
`origin == "owner"`. The console already renders `elements` and `foreground_package` if they are
present.

## 9. Print a one-click console URL

`whale serve` prints the URL and the token on separate lines. The console accepts
`?token=` once, stores it in localStorage and strips it from the address bar.

**Proposal:** print `http://host:port/?token=<token>`, so the owner only needs to click it.

## 10. Small consistency items

- `system.status.memory` lacks `queue_processed`; only `memory.stats` has it. The bridge tile shows
  `—`.
- `ProviderStats.last_ok_at` is in seconds, while every other timestamp is in milliseconds. The
  console does not display it yet.
- `conversations.list` could include a last-message preview for the list.
- `personas.save` accepts arbitrary `avatar` data URLs. The console downscales uploads to at most
  1600 px JPEG, but a server-side size cap such as 512 KB would be safer.


---

## Status (core, 2026-11-23)

All items are implemented in the backend; the client workarounds can stay as fallbacks.

| # | Done |
| --- | --- |
| 1 | `server/http.py` registers `.js/.mjs → text/javascript`, `.css`, `.svg`, `.json`, `.wasm` at import time. Covered by `test_console_is_served_with_module_mime_types`. |
| 2 | `chat.send {wait:false}` returns `{conversation_id, run_id, accepted}` (`RunRequest.run_id` is pre-assigned). |
| 3 | The approval is created first; the `approval` step `input` carries `approval_id`. |
| 4 | Live WS event payloads include `ts`. |
| 5 | `memory.list {scope}` accepts `owner`, `shared`, `group`, `person` or an exact scope; orders `access_count DESC`, `last_access_at DESC` allowed. |
| 6 | `models.test {role:"embedding"}` → `{ok, model, dimensions, offline, latency_ms}`. |
| 7 | Changing `devices.digest_interval_min` reschedules `sys_phone_digest` live. |
| 8 | Owner `devices.action` for observe/shell/file/logcat attaches `observation` / `output`. |
| 9 | `whale serve` prints `http://host:port/?token=…`. |
| 10 | `system.status.memory.queue_processed`; `last_ok_at` now in ms; `conversations.list` items carry `preview {role, text}`; persona `avatar` capped at ~512 KB. |
