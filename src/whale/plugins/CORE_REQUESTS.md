# Core requests from the built-in plugins

Requests from `plugins/builtin/{qq,qweather,media}`, most important first.
Items 1–2 were **delivered** during this round and the plugins use them. The rest are still open;
the plugins work today without them and use the workaround described under each.

## 1. Plugin HTTP routes: DELIVERED (`p.webhook(path)`)

Needed by QQ inbound (OneBot v11 "HTTP POST" reverse reporting).

- `p.webhook("onebot")` registers `async def handler(request: starlette.requests.Request) -> Response`
  at `GET|POST|PUT /api/hooks/<plugin_id>/<path>`. It is removed automatically on disable.
- The core does **not** authenticate the route. The QQ plugin checks `Authorization: Bearer <access_token>`,
  `?access_token=` or `X-Signature: sha1=<HMAC-SHA1(access_token, body)>`. With no token set, it only accepts
  loopback callers.
- Follow-up wish: let hooks declare `auth="token"` so the core can reuse the API-token check for
  hooks that are meant for Whale's own clients. Also add a per-hook body size limit (the QQ plugin caps at 1 MB itself).

## 2. Artifacts: DELIVERED (`p.save_artifact`, `GET /api/artifacts/{id}`)

Needed by `media` (generated images and audio).

- `p.save_artifact(data, content_type=, name=, meta=)` returns `{id, name, content_type, size, path, url}`.
  Files go under `<data_dir>/artifacts/`, and the route requires authentication.
- Follow-up wishes:
  - an `artifacts.list` / `artifacts.delete` RPC, plus retention (e.g. keep 30 days or N MB);
  - `?token=` support on the artifact route so `<img src>` / `<audio src>` work in the console
    (it already goes through `_bearer`, so it may already work: please confirm and document);
  - a `RunOutcome.attachments` list, so a channel (QQ) can send the artifacts a run produced
    as images or voice. Today QQ only sends the reply text.

## 3. A shared, injectable outbound HTTP transport (open)

Plugins build clients with `whale.plugins.builtin._common.http_client(app, ...)`. That helper reads an
**informal** `app.http_transport` attribute (an `httpx` transport, set by tests to `httpx.MockTransport`).
Please make this official, e.g. `App.http_transport: httpx.AsyncBaseTransport | None = None`, and add
`PluginContext.http(timeout=..., headers=...) -> httpx.AsyncClient`. Then plugins, builtin `web.*` and
model providers all share one proxy setting and one test seam. The QQ forward WebSocket uses the same
pattern through `app.ws_connect` (an `async (url, headers) -> async-context websocket`).

## 4. Peer-aware memory extraction (open)

`AgentRuntime` feeds every completed chat turn to `memory.observe` with the user text labelled "用户".
For `peer != "owner"` (QQ strangers, group members), extracted facts are wrongly attributed to the owner.
Request: pass `peer` into `observe(..., source={"peer": ...})`, and in the extractor either (a) store
facts about non-owner peers with `subject=<peer>`, or (b) skip preference/fact extraction about "the user"
when the peer is not the owner.
Workaround: QQ group runs use `remember=False` unless the sender is the owner. Private chats with
strangers keep `remember=True`, as specified, so they hit this problem.

## 5. `RunRequest.meta` / sender identity (open)

Group conversations have many speakers, but `Conversation.peer` is fixed when the conversation is created.
Request: add `RunRequest.meta: dict` (persisted onto the user message's `meta`), e.g.
`{"sender": "qq:30003", "nick": "群友A", "message_id": "…"}`. The console could then render who said
what, and history could carry names.
Workaround: the plugin prefixes the user text with `昵称：`.

## 6. Plugin status reporting for long-lived connections (open)

`PluginRecord.status` is only computed at enable time (`active` / `needs_config` / `error`). The QQ forward
WS can be disconnected while the plugin shows `active`. Request: `p.set_status(status, detail="")`
(e.g. `degraded: OneBot WS 断开，重连中`), which emits `plugin.changed`.

## 7. Delivery receipts as events (open, minor)

The QQ plugin emits `qq.delivery` `{kind, target, status: ok|error|unknown, sent, total, message_ids, error}`.
A core-level `delivery.*` topic convention, shown next to the run in the console, would let every channel
report "送到没有" the same way.


## Core response (2026-11-23)
- #2 `?token=[REDACTED_PARAM] already accepted by `GET /api/artifacts/{id}` (same bearer helper as RPC).
- #3 `app.http_transport` is now an official App attribute (default None).
- #4 Done in core: extraction and episodes are stored in the speaker's scope (`RunRequest.peer`, e.g. `qq:123`); owner retrieval never sees them.
- #5 `RunRequest.meta` added.
- #6 `p.set_status(status, error)` added.
- Open: hook token mode / body limit, artifact list/delete + retention, `RunOutcome.attachments`, `delivery.*` convention.
