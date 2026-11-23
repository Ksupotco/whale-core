# Whale-Core v1 · Web 控制台

The console is a no-build web app made of native ES modules, Preact and htm, and handwritten CSS.
It is served statically by `whale serve`. Its UI language is Simplified Chinese.

```
web/
  index.html                 shell page (no inline script; CSP script-src 'self')
  vendor/preact-htm.js       htm 3.1.1 Preact standalone ESM (vendored, do not edit)
  app/
    main.js                  bootstrap · router · shell · page error boundary
    styles.css               entry: @imports css/* in order
    icon.svg
    css/                     tokens · base · shell · ui · bridge · chat · memory · tasks · devices · pages
    lib/
      api.js                 rpc() over HTTP, Link (WS: hello/welcome, events, rpc, ping, backoff), token, globs
      store.js               global store + live reducers (agent, approvals, runs, activity, toasts), useStore/useTopic, act()
      hooks.js               useRpc, useNow, useMedia, useDebounced*, useLocal, useEscape, history router
      format.js              vocabulary (kinds, scopes, statuses, risks…), time formats, humanize(event)
      markdown.js            safe markdown-lite → vnodes (never HTML strings; http/https/mailto links only)
      graph.js               ForceGraph: own force simulation + canvas renderer, pan/zoom/drag/hover
    components/
      icons.js               inline SVG icon set
      ui.js                  Button, Switch, Segmented, Badge, Chip, Card, Modal, Drawer, TagEditor, Field, …
      shell.js               Nav, Topbar, ConnIndicator, Palette (Ctrl/⌘+K)
      live.js                Orb, ApprovalCard/ApprovalTray, Toasts, ActivityStream
      trace.js               思维轨迹 step list (recall/think/tool/approval/reply), shared by chat & tasks
      task-flow.js           task FLOW VIEW (steps as nodes, sub-agent branches, SVG connectors)
      memory-graph.js        graph canvas + legend + tooltip
      memory-detail.js       memory detail (edit/evidence/relations/lifecycle) + add form
      phone.js               CSS phone illustration + battery gauge
      device-privacy.js      personal-phone privacy panel
      schema-form.js         plugin config form from config_schema + secrets_schema
    pages/                   login · bridge · chat · memory · tasks · capabilities · devices · persona · settings
  tests/                     verification tooling (see “Verification”)
  BACKEND_REQUESTS.md        backend changes the console would benefit from (all degrade gracefully)
```

## Runtime model

The server mounts `/app` → `web/app` and `/vendor` → `web/vendor`. Every other path that does
not start with `/api` serves `index.html`, so history routing works.

**Auth.** The token is accepted from `?token=` once and then stripped from the URL, or typed on
the first screen. It is kept in `localStorage['whale.token']`. Requests send
`Authorization: Bearer …` to `POST /api/rpc/{method}`. Artifacts use
`/api/artifacts/{id}?token=…` (`artifactUrl()`). A 401 or a WS close with code 4401 returns the
console to the token screen.

**Realtime.** There is one `Link` WebSocket to `/api/link`:

- `hello` sends `{token, role:"console", name:"Web 控制台", subscribe:["*"]}`, and the server answers `welcome`.
- Events are dispatched to glob subscribers through `link.on('run.*', fn)` or `useTopic()`.
- After a drop, the link reconnects with exponential backoff and jitter. It also reconnects when
  the tab becomes visible again or the browser comes back online.
- A heartbeat ping is sent every 20 s, and the link reconnects after 65 s of silence.
- `chat.send` goes over WS when the link is online, so the channel is `console`; otherwise it uses HTTP.

**Store.** `store.js` holds only cross-page state:

- connection state and `system.status`
- `agent.state`
- pending approvals
- recent runs: steps, streamed text, plan, progress
- the activity feed
- toasts
- theme

`snapshot()` runs on every `welcome`. It calls `system.status`, `approvals.list` and
`events.query`, then live events keep the state current. Pages load their own data with
`useRpc` and refresh on relevant topics, debounced.

## Page → RPC map

| Page | Reads | Writes / actions | Live topics |
| --- | --- | --- | --- |
| 总览 Bridge `/` (`?focus=memory\|caps\|tasks\|devices`) | `system.status`, `events.query`; tiles: `memory.list`, `capabilities.list`, `tasks.list`, `schedule.list`, `devices.list` | `memory.update` (adopt candidate), `memory.maintain`, `tasks.create` | `agent.state`, everything (activity), `memory.*`, `task.*`, `schedule.*`, `device.*`, `capability.*` |
| 对话 Chat `/chat[/:id]` (`/chat/new?draft=` or `?send=`) | `conversations.list`, `conversations.messages`, `runs.get` | `chat.send {wait:false, images[]}`, `chat.cancel {run_id}`, `conversations.update/delete`, `approvals.resolve` | `run.started/step/delta/completed/failed/cancelled`, `run.status`, `approval.*` |
| 记忆 Memory `/memory[/:id]` (`?q=`, `?view=inbox`, `?add=1`) | `memory.stats`, `memory.graph`, `memory.list`, `memory.search`, `memory.get` | `memory.remember`, `memory.update` (content/kind/importance/tags/status), `memory.forget`, `memory.maintain` | `memory.*` |
| 任务 Tasks `/tasks[/:id]` (`?new=1`) | `tasks.list`, `tasks.get`, `runs.get` (children), `schedule.list` | `tasks.create {goal,title,schedule}`, `tasks.start/cancel/delete`, `schedule.set_enabled/run_now/delete` | `task.*`, `run.*`, `run.plan`, `run.progress`, `schedule.*` |
| 能力 Capabilities `/capabilities` | `capabilities.list`, `plugins.list` | `policy.set {pattern, verdict\|null}`, `plugins.set_enabled`, `plugins.configure {id, config, secrets}` | `capability.*`, `plugin.changed`, `config.changed` |
| 设备 Devices `/devices[/:id]` (`?pair=1`) | `devices.list`, `devices.get`, `devices.privacy`, `link.clients` | `devices.set_role`, `devices.action` (observe, key home/back), `devices.notify`, `devices.set_scopes`, `devices.set_privacy`, `devices.rename/revoke`, `devices.pair_start`, `devices.digest_now`, `devices.search_events` | `device.*`, `clients.changed` |
| 人格 Persona `/persona` | `personas.list`, `memory.list {kind:"style"}` | `personas.save/activate/delete`, `personas.import {card}` (JSON or PNG with `chara` chunk), `memory.update/forget` | `persona.*`, `memory.*` |
| 设置 Settings `/settings[/:section]` | `config.get`, `models.status`, `autonomy.status`, `link.clients` | `config.update {patch}`, `models.upsert_provider/remove_provider/list_remote/test`, `memory.maintain`, `autonomy.explore` | `config.changed`, `autonomy.*`, `clients.changed` |
| Global | `approvals.list` | `approvals.resolve {id, approve, remember}` | `approval.*`, `notify` (toast) |

## Design

The visual identity is "deep ocean". Dark mode is the default: a navy base, glass panels and a
luminous cyan/teal accent. Light mode is a clean daylight variant. The theme is either `auto`,
which follows `prefers-color-scheme`, or a manual choice from the top bar or Settings. The choice
is saved in `localStorage['whale.theme']`.

| Token | Dark | Light | Use |
| --- | --- | --- | --- |
| `--bg` / `--bg-grad` | `#050e1c` + radial glows | `#f4f8fb` | page |
| `--panel` | `rgba(14,33,60,.62)` + blur | `rgba(255,255,255,.78)` | glass cards |
| `--text` / `-2` / `-3` | `#e3f1fb` / `#a9c2d8` / `#6f8aa5` | `#0c2236` / `#3d5a73` / `#6d8499` | text hierarchy |
| `--accent` / `--accent-2` | `#2ee6d6` / `#38bdf8` | `#0d9488` / `#0284c7` | primary, focus, live |
| `--ok --warn --danger --info --violet` (+ `-soft`) | | | status tones |
| `--k-<kind>` | fact `#38bdf8` · preference `#f472b6` · episode `#fbbf24` · knowledge `#34d399` · diary `#a78bfa` · person `#fb923c` · skill `#2ee6d6` · style `#f9a8d4` | darker equivalents | memory kinds (graph, chips) |
| `--sc-<scope>` | owner teal · shared gold · group violet · person rose | | memory scope rings |
| `--s1…--s8` | 4 · 8 · 12 · 16 · 24 · 32 · 48 · 64 px | | 8px grid |
| `--r1…--r4` | 6 · 10 · 14 · 20 px | | radii |
| `--t-fast / --t / --t-slow` | 160 / 240 / 320 ms, `--ease` = cubic-bezier(.2,.8,.2,1) | | motion |

The font stack is Inter, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans SC" and system-ui.

**Reduced motion.** `prefers-reduced-motion` sets every duration to 1 ms. The memory graph then
lays itself out synchronously instead of animating.

**Accessibility:**

- focus rings via `:focus-visible`
- a skip link
- `aria-*` on switches, segmented radios, dialogs, the tray and the palette (combobox/listbox)
- keyboard: Ctrl/⌘+K, Esc, arrow keys in segmented controls and the palette, Enter in forms
- `aria-live` on the connection indicator, toasts and the chat stream

**Dynamic focus.** On the Bridge, the four tiles share one flex row. Opening a tile animates
`flex-grow` and `flex-basis` so it takes the working space, and the other tiles shrink into 76 px
vertical rails. On a phone the tiles stack instead, and the rails become one-line headers.

## Verification

There is no browser, node or npm here. The only JavaScript runtime is a VM sandbox that has no
`vm`, no `eval` and no `new Function`. Tooling (all under `tests/`):

- `bundle.js` — `makeBundler(fs, path)(webDir, testFiles, outName)` rewrites every module's
  import/export into a factory inside one script, `tests/out/<outName>`. `/vendor/preact-htm.js`
  becomes a shim in which `html` is the real htm parser, extracted verbatim from the vendored
  file and bound to `React.createElement`. A syntax error in any file fails compilation of that
  bundle, so running it is the syntax check. `bundle.py` is the same tool in Python.
- `static.js` — `staticChecks(fs, path, webDir)` checks that:
  - `index.html` has no inline script or handlers and loads `/app/main.js` (module) and `/app/styles.css`;
  - no `eval`, `new Function`, `innerHTML`, `dangerouslySetInnerHTML` or string timers are used;
  - every named import resolves to a file that exports that name, including the vendor exports;
  - CSS `@import` targets exist and braces balance;
  - files stay under 600 lines.
- `mocks.js` — RPC results shaped exactly like the backend, e.g. `MemoryItem.to_dict`, `devices._view`, `RunLedger` rows and `PluginRecord.to_dict`.
- `smoke.js` — renders every page and route variant to a string with `react-dom/server`, in narrow
  and wide layouts, with `useRpc` mocked. It also renders the shell, login, tray, palette, toasts,
  trace and orb phases. It fails on any exception, a visible `undefined` or `NaN`, or
  `[object Object]`.
- `unit.js` — covers:
  - `Link` against a fake WebSocket: hello shape, welcome, glob dispatch, RPC resolve/reject,
    ping/pong, backoff, 4401;
  - store reducers;
  - markdown safety and the URL token;
  - the schedule builders;
  - `ForceGraph` on a real `canvas` (convergence, no overlaps, hit-test, fit);
  - PNG Character Card extraction.

Run them from `run_javascript`. Bundles must be built with the JS bundler inside the sandbox,
because files written from Python reach the JS sandbox late:

```js
// 1) file: tests/bundle.js
makeBundler(require('fs'), require('path'))(WORKSPACE_DIR + '/tmp/whale/web', ['tests/mocks.js', 'tests/smoke.js'], 'smoke.js');
makeBundler(require('fs'), require('path'))(WORKSPACE_DIR + '/tmp/whale/web', ['tests/mocks.js', 'tests/unit.js'], 'unit.js');
// 2) file: tests/out/smoke.js            → prints "SMOKE: n/n passed"
// 3) file: tests/out/unit.js, code: await globalThis.__UNIT_DONE   → "UNIT: n/n passed"
// 4) file: tests/static.js, code: console.log(staticChecks(require('fs'), require('path'), WORKSPACE_DIR + '/tmp/whale/web'))
```

The smoke render goes through React, not Preact. Preact-specific behaviour is kept portable:
styles are always objects, and `class` and `stroke-width` are mapped by the shim. Live DOM
behaviour is still worth a manual pass in a real browser: canvas interaction, drag-and-drop,
WebSocket reconnects and CSS transitions.
