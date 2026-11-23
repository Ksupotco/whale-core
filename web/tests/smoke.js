/* Smoke test: render every page and shared component to a string with mock data shaped like the
   backend. Any exception fails the run. React warnings are collected and summarised. */
(function () {
  const React = require('react');
  const { renderToString } = require('react-dom/server');
  const results = [];
  const warnings = new Map();
  const origError = console.error;
  const origWarn = console.warn;
  console.error = (...a) => { const k = String(a[0]).slice(0, 110); warnings.set(k, (warnings.get(k) || 0) + 1); };
  console.warn = console.error;

  // -- minimal browser globals ------------------------------------------------------------------
  const ls = new Map();
  globalThis.localStorage = { getItem: (k) => (ls.has(k) ? ls.get(k) : null), setItem: (k, v) => ls.set(k, String(v)), removeItem: (k) => ls.delete(k) };
  let wide = false;
  globalThis.matchMedia = (q) => ({ matches: wide && /min-width/.test(q), addEventListener() {}, removeEventListener() {} });

  // -- RPC mock: useRpc returns mock data synchronously ------------------------------------------
  const realHooks = __req('app/lib/hooks.js');
  const calls = [];
  function useRpc(method, params) {
    if (!method) return { data: undefined, error: null, loading: false, reload: async () => undefined, setData() {} };
    calls.push(method);
    const fn = MOCK.R[method];
    if (!fn) throw new Error('no mock for ' + method);
    return { data: fn(params || {}), error: null, loading: false, reload: async () => undefined, setData() {} };
  }
  __overrides['app/lib/hooks.js'] = Object.assign({}, realHooks, { useRpc });
  const api = __req('app/lib/api.js');
  const { html } = __req('vendor');
  const { store } = __req('app/lib/store.js');

  function check(name, fn) {
    const t0 = Date.now();
    try {
      const out = fn();
      if (typeof out !== 'string' || out.length < 20) throw new Error('suspiciously small output: ' + String(out).slice(0, 80));
      if (/\[object Object\]/.test(out)) throw new Error('rendered [object Object]');
      if (/undefined|NaN/.test(out.replace(/<[^>]+>/g, ' ').replace(/data-[a-z-]+="[^"]*"/g, ''))) {
        const text = out.replace(/<[^>]+>/g, ' ');
        const m = text.match(/.{0,40}(undefined|NaN).{0,40}/);
        throw new Error('visible "undefined"/NaN in text: …' + (m ? m[0] : '') + '…');
      }
      results.push({ name, ok: true, bytes: out.length, ms: Date.now() - t0 });
    } catch (e) {
      results.push({ name, ok: false, error: (e && e.stack ? e.stack.split('\n').slice(0, 4).join(' | ') : String(e)) });
    }
  }
  const render = (el) => renderToString(el);

  // -- store snapshot like after snapshot() ----------------------------------------------------------
  const now = Date.now();
  store.set({
    token: 'tok', conn: 'online', status: MOCK.status, agent: MOCK.status.agent,
    approvals: MOCK.R['approvals.list']().approvals,
    activity: [
      { id: 'a1', topic: 'memory.created', ts: now - 1000, data: { item: MOCK.memories[0] } },
      { id: 'a2', topic: 'run.completed', ts: now - 2000, data: { run: MOCK.run('run_01') } },
      { id: 'a3', topic: 'device.event_filtered', ts: now - 86400000 * 2, data: { device_id: 'x', reason: 'excluded_app' } },
      { id: 'a4', topic: 'approval.requested', ts: now - 3000, data: { title: '发送 QQ 消息', capability: 'qq.send' } },
      { id: 'a5', topic: 'device.digest', ts: now - 4000, data: { devices: 1, segments: 2, episodes: 2, facts: 1 } },
      { id: 'a6', topic: 'autonomy.proactive', ts: now - 5000, data: { text: 'hi', sent: false, why: '静默时段' } },
      { id: 'a7', topic: 'weird.topic', ts: now - 6000, data: {} },
    ],
    runs: {
      run_01: { run: MOCK.run('run_01'), steps: MOCK.steps, text: '', plan: [], progress: '', children: [], loadedAt: now },
      run_live: { run: MOCK.run('run_live', { status: 'running', input: '帮我查天气', stats: {} }), steps: MOCK.steps.slice(0, 2).map((s) => ({ ...s, run_id: 'run_live' })), text: '正在查**天气**', plan: [], progress: '', children: [] },
    },
    toasts: [{ id: 't1', kind: 'notify', title: '鲸鱼', body: '早上好', action: { label: '查看', href: '/chat' } }, { id: 't2', kind: 'error', title: '操作失败', body: 'x' }],
  });

  const { parseRoute, navigate } = realHooks;
  const pages = [
    ['app/pages/bridge.js', 'BridgePage', ['/', '/?focus=memory', '/?focus=caps', '/?focus=tasks', '/?focus=devices']],
    ['app/pages/chat.js', 'ChatPage', ['/chat', '/chat/conv_1', '/chat/new?draft=你好', '/chat/new']],
    ['app/pages/memory.js', 'MemoryPage', ['/memory', '/memory/mem_1', '/memory?q=猫', '/memory?view=inbox', '/memory/mem_4?view=inbox', '/memory/mem_3']],
    ['app/pages/tasks.js', 'TasksPage', ['/tasks', '/tasks/task_1', '/tasks?new=1']],
    ['app/pages/capabilities.js', 'CapabilitiesPage', ['/capabilities']],
    ['app/pages/devices.js', 'DevicesPage', ['/devices', '/devices/ba4bb14c75917cfc0e69fb7a1caf8ba3', '/devices/cc11dd22ee33ff44aa55bb66cc77dd88', '/devices?pair=1']],
    ['app/pages/persona.js', 'PersonaPage', ['/persona']],
    ['app/pages/settings.js', 'SettingsPage', ['/settings', '/settings/models', '/settings/autonomy']],
  ];
  for (const pass of ['narrow', 'wide']) {
    wide = pass === 'wide';
    for (const [id, exp, routes] of pages) {
      let mod;
      try { mod = __req(id); } catch (e) { results.push({ name: id + ' (load)', ok: false, error: String(e && e.stack ? e.stack.split('\n').slice(0, 3).join(' | ') : e) }); continue; }
      for (const r of routes) {
        navigate(r);
        check(`${pass} ${exp} ${r}`, () => render(html`<${mod[exp]} route=${parseRoute(r)} />`));
        if (id.endsWith('memory.js') && r === '/memory') {
          ls.set('whale.ui.memory.view', JSON.stringify('list'));
          check(`${pass} ${exp} ${r} [list view]`, () => render(html`<${mod[exp]} route=${parseRoute(r)} />`));
          ls.set('whale.ui.memory.view', JSON.stringify('graph'));
        }
      }
    }
  }

  // -- shell, login, overlays ---------------------------------------------------------------------------
  try {
    const main = __req('app/main.js');
    const live = __req('app/components/live.js');
    const shell = __req('app/components/shell.js');
    const trace = __req('app/components/trace.js');
    navigate('/');
    check('App (signed in, shell)', () => render(html`<${main.App} />`));
    store.set({ trayOpen: true, paletteOpen: true });
    check('Approval tray open', () => render(html`<${live.ApprovalTray} />`));
    check('Palette open', () => render(html`<${shell.Palette} />`));
    check('Toasts', () => render(html`<${live.Toasts} />`));
    check('StepList', () => render(html`<${trace.StepList} steps=${MOCK.steps} run=${MOCK.run('run_01')} progress="子 Agent：调研" />`));
    check('Orb phases', () => ['idle', 'thinking', 'acting', 'waiting', 'bogus'].map((p) => render(html`<${live.Orb} phase=${p} avatar=${p === 'waiting' ? 'data:image/png;base64,AA' : undefined} label />`)).join(''));
    store.set({ conn: 'reconnecting', connInfo: { attempt: 2, retryAt: Date.now() + 3000 } });
    check('Conn indicator reconnecting', () => render(html`<${shell.ConnIndicator} />`));
    store.set({ token: '' });
    check('App (signed out → login)', () => render(html`<${main.App} />`));
  } catch (e) { results.push({ name: 'shell (load)', ok: false, error: String(e && e.stack ? e.stack.split('\n').slice(0, 3).join(' | ') : e) }); }

  // -- libraries ---------------------------------------------------------------------------------------------
  const md = __req('app/lib/markdown.js');
  const fmt = __req('app/lib/format.js');
  check('markdown safety', () => {
    const out = render(html`<div>${md.renderMarkdown('[x](javascript:alert(1)) <img src=x onerror=alert(1)> **b** `c` ```\nunterminated')}</div>`);
    if (/href="javascript/i.test(out) || /<img/i.test(out)) throw new Error('unsafe markup: ' + out);
    return out;
  });
  check('humanize all topics', () => ['run.started', 'run.completed', 'run.failed', 'run.cancelled', 'run.step', 'run.plan', 'memory.created', 'memory.updated', 'memory.reinforced',
    'memory.superseded', 'memory.forgotten', 'memory.extracted', 'memory.maintained', 'approval.requested', 'approval.resolved', 'task.created', 'task.updated', 'task.deleted',
    'schedule.fired', 'schedule.changed', 'notify', 'device.connected', 'device.disconnected', 'device.paired', 'device.revoked', 'device.updated', 'device.event',
    'device.event_filtered', 'device.action', 'device.notification', 'device.pair_requested', 'device.digest', 'autonomy.exploring', 'autonomy.explored',
    'autonomy.proactive', 'plugin.changed', 'config.changed', 'persona.activated', 'persona.saved', 'system.started', 'x.y']
    .map((t) => { const h = fmt.humanize({ topic: t, data: {} }); if (!h.text || !h.icon) throw new Error('bad humanize ' + t); return h.text; }).join('\n'));
  check('glob match', () => {
    const ok = api.topicMatches('run.step', 'run.*') && !api.topicMatches('runx.step', 'run.*') && api.topicMatches('device.digest', ['memory.*', 'device.*']) && api.topicMatches('a.b', '*');
    if (!ok) throw new Error('glob mismatch');
    return 'glob ok glob ok glob ok glob ok';
  });

  console.error = origError;
  console.warn = origWarn;
  const failed = results.filter((r) => !r.ok);
  console.log(`SMOKE: ${results.length - failed.length}/${results.length} passed, ${__stats.elements} elements created, rpc mocks used: ${[...new Set(calls)].length}`);
  for (const r of failed) console.log('FAIL', r.name, '\n   ', r.error);
  console.log('React warnings (unique):', warnings.size);
  for (const [k, n] of warnings) console.log(`  ${n}× ${k}`);
})();
