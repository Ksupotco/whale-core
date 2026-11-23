/* Mock RPC results, shaped exactly like the backend responses (see src/whale/server/api.py,
   devices/service.py, memory/store.py MemoryItem.to_dict, agent/records.py). */
var MOCK = (function () {
  const now = Date.now();
  const mem = (id, kind, content, extra = {}) => ({
    id, kind, content, subject: 'user', status: 'active', importance: 0.6, confidence: 0.8, strength: 0.7,
    source: { run_id: 'run_01', conversation_id: 'conv_1', quote: '我养了一只叫年糕的橘猫', channel: 'console' }, tags: ['宠物'], scope: 'owner',
    created_at: now - 86400000, updated_at: now - 3600000, last_access_at: now - 600000, access_count: 3, strength_now: 0.62, ...extra,
  });
  const memories = [
    mem('mem_1', 'fact', '用户养了一只叫年糕的橘猫'),
    mem('mem_2', 'preference', '用户喜欢喝无糖乌龙茶', { importance: 0.8, scope: 'shared' }),
    mem('mem_3', 'episode', '11-20 21:00–22:10 用户在手机上看了 AI 机器人视频', { scope: 'owner', source: { device_id: 'ba4bb14c75917cfc0e69fb7a1caf8ba3', device: 'Pixel 9', role: 'personal', span: '11-20 21:00–22:10', event_ids: ['evt_1', 'evt_2'], from: '手机' } }),
    mem('mem_4', 'knowledge', 'Rust 2024 edition 默认启用新的 RPIT 捕获规则', { status: 'candidate', source: { run_id: 'run_x', topic: 'Rust', autonomy: true, url: 'https://blog.rust-lang.org/' } }),
    mem('mem_5', 'style', '说话时偶尔用“嗯哼”开头', { scope: 'shared', subject: 'self' }),
    mem('mem_6', 'fact', '群里的小王是前端工程师', { scope: 'group:123456' }),
    mem('mem_7', 'person', '李雷是用户的大学室友', { scope: 'alice' }),
    mem('mem_8', 'fact', '用户养了一只猫', { status: 'superseded' }),
    mem('mem_9', 'diary', '今天读了关于深海鲸落生态的文章，很震撼。', { scope: 'shared', subject: 'self' }),
    mem('mem_10', 'skill', '整理周报时先列出完成项再写风险', { scope: 'shared' }),
  ];
  const edges = [
    { src: 'mem_1', dst: 'mem_8', relation: 'supersedes', weight: 0.9, created_at: now },
    { src: 'mem_1', dst: 'mem_2', relation: 'related', weight: 0.4, created_at: now },
    { src: 'mem_3', dst: 'mem_9', relation: 'related', weight: 0.5, created_at: now },
  ];
  const steps = [
    { id: 'step_1', run_id: 'run_01', seq: 1, kind: 'recall', title: '回忆到 2 条记忆', status: 'done', input: { query: '我的猫叫什么' },
      output: { memories: [{ id: 'mem_1', kind: 'fact', content: '用户养了一只叫年糕的橘猫', score: 0.83, why: ['关键词', '语义', '重要'] }] }, started_at: now - 5000, ended_at: now - 4900 },
    { id: 'step_2', run_id: 'run_01', seq: 2, kind: 'think', title: '决定调用 web.search', status: 'done', input: { turn: 1, model: 'deepseek-chat' },
      output: { latency_ms: 812, decision: 'tools', tools: ['web.search'], text: '', reasoning: '需要查一下' }, started_at: now - 4900, ended_at: now - 4000 },
    { id: 'step_3', run_id: 'run_01', seq: 3, kind: 'approval', title: '等待批准：发送 QQ 消息', status: 'running', input: { capability: 'qq.send', args: { target: '12345', text: '你好' } },
      output: {}, started_at: now - 4000, ended_at: null },
    { id: 'step_4', run_id: 'run_01', seq: 4, kind: 'tool', title: '网页搜索', status: 'done', input: { capability: 'web.search', args: { query: '橘猫' } },
      output: { status: 'ok', data: { results: [] }, summary: '找到 5 条结果', error: '', latency_ms: 420 }, started_at: now - 3000, ended_at: now - 2500 },
    { id: 'step_5', run_id: 'run_01', seq: 5, kind: 'tool', title: '按键', status: 'unknown', input: { capability: 'device.pixel_9.device.key', args: { key: 'home' } },
      output: { status: 'unknown', data: null, summary: '', error: 'timeout', latency_ms: 15000 }, started_at: now - 2500, ended_at: now - 2000 },
    { id: 'step_6', run_id: 'run_01', seq: 6, kind: 'reply', title: '回复', status: 'done', input: {}, output: { text: '你的猫叫**年糕**。' }, started_at: now - 2000, ended_at: now - 1990 },
  ];
  const run = (id, extra = {}) => ({ id, conversation_id: 'conv_1', parent_id: null, task_id: null, mode: 'chat', status: 'completed', input: '我的猫叫什么',
    output: '你的猫叫年糕。', error: '', channel: 'console', model: 'deepseek-chat', created_at: now - 5000, ended_at: now - 1990,
    stats: { latency_ms: 3010, memories: 1, tools: 2 }, ...extra });
  const task = (id, status, extra = {}) => ({ id, title: '整理本周 AI 新闻', goal: '整理本周 AI 新闻要点，给我 5 条', status,
    plan: [{ step: '搜索新闻', status: 'done' }, { step: '阅读并筛选', status: 'doing' }, { step: '写总结', status: 'todo' }],
    result: status === 'done' ? '1. …\n2. …' : '', error: '', origin: 'user', parent_id: null, conversation_id: null, job_id: null, notify: true,
    runs: 1, last_run_id: 'run_goal', created_at: now - 7200000, updated_at: now - 60000, ...extra });
  const job = (id, name, extra = {}) => ({ id, name, handler: 'task.run', args: {}, spec: { every_min: 30 }, enabled: true, system: false,
    next_at: now + 1800000, last_at: now - 600000, last_status: 'ok', last_error: '', runs: 4, created_at: now - 86400000, ...extra });
  const device = (id, name, extra = {}) => ({ id, name, model: 'Pixel 9', flavor: 'daily', app_version: '2.3.0', android: '15', online: true, status: 'online',
    battery: 76, network: 'wifi', foreground: 'com.tencent.mm', capabilities: ['device.status', 'device.observe', 'device.key', 'phone.notification.post', 'screen.context.read'],
    tools: ['device.pixel_9.device.observe', 'device.pixel_9.device.key'], features: { screen_memo: true }, scopes: ['device.status', 'device.observe', 'device.key', 'phone.notification.post'],
    last_seen: now - 10000, connected_at: now - 3600000, session_id: 'sess_1', revoked: false, created_at: now - 864000000, paired_via: 'owner',
    role: 'personal', role_explicit: false, privacy: { excluded_apps: ['com.bank.app'], excluded_domains: ['*.bank.com'], redact_secrets: true, drop_sensitive: true }, ...extra });
  const devA = device('ba4bb14c75917cfc0e69fb7a1caf8ba3', 'Pixel 9');
  const devB = device('cc11dd22ee33ff44aa55bb66cc77dd88', '鲸鱼的小米', { role: 'agent', flavor: 'root', online: false, status: 'offline', battery: null, network: null, foreground: null, capabilities: [], tools: [], privacy: null, last_seen: now - 7200000 });
  const caps = [
    { name: 'memory.remember', title: '记住', description: '记住一件事', params: {}, risk: 'write', provider: 'builtin', group: 'memory', modes: ['chat'], available: true, owner_only: false, policy: 'allow', stats: { calls: 12, errors: 0, total_ms: 120 } },
    { name: 'web.search', title: '网页搜索', description: '搜索网页', params: {}, risk: 'read', provider: 'builtin', group: 'web', modes: ['chat', 'explore'], available: true, owner_only: false, policy: 'allow', stats: { calls: 30, errors: 2, total_ms: 9000 } },
    { name: 'shell.run', title: '执行命令', description: '在主机上执行命令', params: {}, risk: 'dangerous', provider: 'builtin', group: 'shell', modes: ['chat'], available: true, owner_only: true, policy: 'ask', stats: { calls: 0, errors: 0, total_ms: 0 } },
    { name: 'weather.now', title: '实时天气', description: '查询天气', params: {}, risk: 'read', provider: 'plugin:qweather', group: '和风天气', modes: ['chat'], available: true, owner_only: false, policy: 'allow', stats: { calls: 3, errors: 0, total_ms: 600 } },
    { name: 'device.pixel_9.device.key', title: 'Press key', description: 'Press a system key', params: {}, risk: 'external', provider: 'device:ba4bb14c', group: 'Pixel 9', modes: ['chat', 'goal'], available: true, owner_only: true, policy: 'deny', stats: { calls: 1, errors: 1, total_ms: 15000 } },
    { name: 'client.glasses.card', title: '眼镜卡片', description: '在眼镜上显示卡片', params: {}, risk: 'external', provider: 'client:cli_1', group: 'glasses', modes: ['chat'], available: false, owner_only: false, policy: 'ask', stats: { calls: 0, errors: 0, total_ms: 0 } },
  ];
  const plugins = [
    { id: 'qweather', name: '和风天气', version: '1.0.0', description: '查询天气', enabled: true, status: 'active', error: '', capabilities: ['weather.now'],
      config_schema: { api_host: { type: 'string', title: 'API Host', description: '专属域名', default: '' }, default_city: { type: 'string', title: '默认城市', default: '' } },
      secrets_schema: { key: 'API KEY' }, secrets: { key: true }, config: { enabled: true, api_host: 'abc.re.qweatherapi.com' }, builtin: true, icon: '🌤️' },
    { id: 'qq', name: 'QQ', version: '1.0.0', description: 'OneBot v11', enabled: false, status: 'disabled', error: '', capabilities: [],
      config_schema: { api_base: { type: 'string', title: 'OneBot HTTP API', default: '' }, group_mode: { type: 'string', title: '群聊模式', enum: ['off', 'mention', 'smart'], default: 'mention' },
        allowed_groups: { type: 'array', title: '允许的群', default: [] }, reply_split: { type: 'boolean', title: '分条发送', default: true }, bubble_delay_ms: { type: 'integer', title: '气泡间隔(毫秒)', default: 800 } },
      secrets_schema: { access_token: 'OneBot Access Token' }, secrets: { access_token: false }, config: {}, builtin: true, icon: '🐧' },
    { id: 'media', name: '生图与语音', version: '1.0.0', description: '文生图', enabled: true, status: 'error', error: "RuntimeError('no key')", capabilities: [],
      config_schema: { provider: { type: 'string', title: '服务商', enum: ['openai', 'dashscope'], default: 'openai' } }, secrets_schema: { api_key: 'API Key' }, secrets: { api_key: false }, config: {}, builtin: true, icon: '🎨' },
  ];
  const config = {
    server: { host: '127.0.0.1', port: 8080, cors_origins: [] },
    models: { providers: [{ id: 'deepseek', kind: 'openai', base_url: 'https://api.deepseek.com/v1', label: 'DeepSeek' }],
      chat: { provider: 'deepseek', model: 'deepseek-chat', temperature: 0.7, max_tokens: 1024 }, fast: { provider: '', model: '', temperature: 0.2, max_tokens: 800 },
      vision: { provider: '', model: '', temperature: 0.7, max_tokens: 1024 }, embedding: { provider: '', model: '', temperature: 0.7, max_tokens: 1024 }, daily_request_budget: 2000 },
    agent: { name: '鲸鱼', persona_id: 'default', timezone: 'Asia/Shanghai', language: 'zh-CN', max_steps: 12, history_turns: 16, context_budget_chars: 24000 },
    memory: { extraction: true, consolidation_interval_min: 30, half_life_days: 30, recall_limit: 8 },
    autonomy: { explore: false, explore_interval_min: 180, interests: ['AI', '深海'], proactive: false, quiet_hours: [23, 8], proactive_daily_limit: 3 },
    policy: { rules: { 'device.*': 'deny', 'shell.run': 'ask' } },
    devices: { heartbeat_interval_s: 30, heartbeat_timeout_s: 90, challenge_ttl_s: 30, receipt_grace_s: 3, pair_ttl_s: 600, digest_interval_min: 30 },
    plugins: { qweather: { enabled: true } }, secrets: { 'provider.deepseek': true, 'plugin.qweather.key': true },
  };
  const modelsStatus = { roles: { chat: { provider: 'deepseek', model: 'deepseek-chat', configured: true }, fast: { provider: 'deepseek', model: 'deepseek-chat', configured: true },
    vision: { provider: '', model: '', configured: false }, embedding: { provider: '', model: '', configured: false } }, offline: false,
    budget: { used: 123, limit: 2000 }, providers: { deepseek: { requests: 120, errors: 2, avg_ms: 830.5, last_error: '', last_ok_at: now / 1000, tokens_in: 52000, tokens_out: 9000 } } };
  const clients = [{ id: 'cli_1', role: 'console', name: 'Web 控制台', connected_at: now - 60000, subscriptions: ['*'], capabilities: [], meta: {} },
    { id: 'cli_2', role: 'glasses', name: 'Rokid', connected_at: now - 600000, subscriptions: ['notify'], capabilities: ['card'], meta: { version: '1.0' } }];
  const status = { version: '1.0.0', uptime_s: 7322, python: '3.12.1',
    agent: { name: '鲸鱼', persona: { id: 'default', name: '鲸鱼', avatar: '🐋', tagline: '长期陪伴你的个人智能体', color: '#3b82f6' }, phase: 'thinking',
      active: [{ run_id: 'run_live', mode: 'chat', status: 'thinking', conversation_id: 'conv_1', title: '帮我查天气', channel: 'console' }] },
    models: { ...modelsStatus, offline: true },
    memory: { by_kind: { fact: 3, preference: 1, episode: 1, style: 1 }, by_status: { active: 8, candidate: 1, superseded: 1 }, links: 3, by_scope: { owner: 4, shared: 3, group: 1, person: 1 }, active: 8, candidates: 1 },
    capabilities: { total: 6, by_provider: { builtin: 3, plugin: 1, device: 1, client: 1 } },
    tasks: { total: 3, active: 1, scheduled: 1 }, approvals: { pending: 1 }, devices: { total: 2, online: 1 }, clients,
    autonomy: { explore: false, proactive: false } };
  const personas = [{ id: 'default', name: '鲸鱼', tagline: '长期陪伴你的个人智能体', description: '一个有长期记忆的 AI', personality: '温和', style: '自然', scenario: '', greeting: '', examples: '', boundaries: '', avatar: '🐋', color: '#3b82f6', builtin: true, extra: {} },
    { id: 'persona_x', name: '小鲸', tagline: '活泼', description: '', personality: '', style: '', scenario: '', greeting: '嗨', examples: '', boundaries: '', avatar: 'data:image/png;base64,AAAA', color: '#e11d48', builtin: false, extra: {} }];
  const R = {
    'system.status': () => status,
    'events.query': () => ({ events: [{ id: 'e1', topic: 'memory.created', ts: now - 5000, data: { item: memories[0] } }] }),
    'conversations.list': () => ({ conversations: [{ id: 'conv_1', title: '我的猫叫什么', channel: 'console', peer: 'owner', persona_id: null, created_at: now - 86400000, updated_at: now - 60000, turns: 3, meta: {} },
      { id: 'conv_2', title: '群聊', channel: 'bridge', peer: 'group:1', persona_id: null, created_at: now - 86400000, updated_at: now - 600000, turns: 9, meta: {} }] }),
    'conversations.messages': () => ({ conversation: { id: 'conv_1', title: '我的猫叫什么', channel: 'console', peer: 'owner', persona_id: null, created_at: now - 86400000, updated_at: now, turns: 3, meta: {} },
      messages: [{ id: 'msg_1', conversation_id: 'conv_1', role: 'user', content: '我的猫叫什么？\n第二行', run_id: 'run_01', ts: now - 6000, meta: { images: 1 } },
        { id: 'msg_2', conversation_id: 'conv_1', role: 'assistant', content: '你的猫叫**年糕**。\n\n```py\nprint(1)\n```\n- a\n- [链接](https://example.com)', run_id: 'run_01', ts: now - 2000, meta: { memories: ['mem_1'], tools: [{ name: 'web.search', status: 'ok' }] } },
        { id: 'msg_3', conversation_id: 'conv_1', role: 'system', content: '⚠ model failed', run_id: 'run_02', ts: now - 1000, meta: {} }] }),
    'runs.get': () => ({ run: run('run_01'), steps, children: [] }),
    'memory.list': (p) => { const items = memories.filter((m) => (!p.status || m.status === p.status) && (!p.kind || m.kind === p.kind)); return { items, total: items.length }; },
    'memory.get': (p) => ({ item: memories.find((m) => m.id === p.id) || memories[0], links: edges.filter((e) => e.src === p.id || e.dst === p.id), related: memories.slice(1, 3) }),
    'memory.search': () => ({ hits: memories.slice(0, 3).map((m) => ({ ...m, score: 0.71, why: ['关键词', '近期'] })) }),
    'memory.graph': () => ({ nodes: memories, edges }),
    'memory.stats': () => ({ ...status.memory, queue_processed: 12 }),
    'capabilities.list': () => ({ capabilities: caps, rules: config.policy.rules }),
    'approvals.list': () => ({ approvals: [{ id: 'aprv_1', run_id: 'run_01', capability: 'qq.send', args: { target: '12345', text: '你好' }, reason: 'console · 帮我发消息', risk: 'external', status: 'pending', created_at: now - 30000, resolved_at: null, resolved_by: null, note: null }] }),
    'tasks.list': () => ({ tasks: [task('task_1', 'running'), task('task_2', 'scheduled', { job_id: 'job_2', schedule: job('job_2', '任务：每日简报', { spec: { cron: '0 8 * * *' } }) }), task('task_3', 'done'), task('task_4', 'failed', { error: 'model failed' })] }),
    'tasks.get': (p) => ({ task: task(p.id || 'task_1', 'running'), runs: [run('run_goal', { mode: 'goal', task_id: 'task_1', status: 'running', conversation_id: null })],
      latest: { run: run('run_goal', { mode: 'goal', task_id: 'task_1', status: 'running', conversation_id: null }),
        steps: steps.concat([{ id: 'step_d', run_id: 'run_goal', seq: 7, kind: 'tool', title: '委派子 Agent', status: 'done', input: { capability: 'agent.delegate', args: { objective: '调研 A' } }, output: { status: 'ok', data: { status: 'completed', result: 'A 的结论', run_id: 'run_child' }, summary: '', error: '', latency_ms: 9000 }, started_at: now - 9000, ended_at: now }]).map((s) => ({ ...s, run_id: 'run_goal' })),
        children: [run('run_child', { mode: 'goal', parent_id: 'run_goal', channel: 'subagent', input: '调研 A', conversation_id: null })] } }),
    'schedule.list': () => ({ jobs: [job('sys_explore', '自主探索', { system: true, handler: 'autonomy.explore', enabled: false, spec: { every_min: 180 } }), job('sys_phone_digest', '手机经历整理成记忆', { system: true, handler: 'phone.digest' }),
      job('job_2', '任务：每日简报', { spec: { cron: '0 8 * * *' }, args: { task_id: 'task_2' } }), job('job_3', '提醒开会', { spec: { at: now + 3600000 }, last_status: 'error', last_error: 'boom' })] }),
    'personas.list': () => ({ personas, active: 'default' }),
    'plugins.list': () => ({ plugins }),
    'config.get': () => config,
    'models.status': () => modelsStatus,
    'models.list_remote': () => ({ models: ['deepseek-chat', 'deepseek-reasoner'] }),
    'autonomy.status': () => ({ config: config.autonomy, proactive_ready: false, proactive_reason: '主动消息未开启', topics: ['AI', '深海'],
      recent: [{ id: 'ev1', topic: 'autonomy.explored', ts: now - 3600000, data: { topic: '深海', run_id: 'run_e', status: 'completed', diary: 'mem_9', findings: ['mem_4'] } }] }),
    'link.clients': () => ({ clients }),
    'devices.list': () => ({ devices: [devA, devB],
      capabilities: ['device.status', 'device.observe', 'device.key', 'phone.notification.post'], ws_path: '/api/devices/ws' }),
    'devices.get': (p) => ({ ...(p.id === devB.id ? devB : devA), events: [{ event_id: 'evt_1', kind: 'screen.segment_ready', occurred_at: now - 600000, device_id: devA.id, source: 'screen_memo', summary: '浏览了一个关于 AI 机器人的视频', privacy_class: 'owner_private', extras: { package: 'com.example.video', app_name: '视频' }, received_at: now - 590000 },
      { event_id: 'evt_d', kind: 'dropped', dropped: 'system_private_app', received_at: now - 500000 }],
      actions: [{ action_id: 'act_1', device_id: devA.id, capability: 'device.key', idempotency_key: 'owner:abc12345', arguments: { key: 'home' }, origin: 'owner', run_id: null, status: 'succeeded', outcome_source: 'device', error_code: null, summary: '已按下 home', created_at: now - 100000, updated_at: now - 99000, expires_at: now }],
      event_count: 42 }),
    'devices.privacy': () => ({ role: 'personal', settings: devA.privacy, builtin: { system_packages: ['com.android.systemui', 'com.android.settings'], ime_patterns: ['inputmethod', 'keyboard'], screen_flags: ['locked', 'secure'], redaction: ['一次性验证码', '银行卡号', '身份证号'] },
      filtered: { system_private_app: 12, excluded_app: 3, phone_marked_sensitive: 1 } }),
    'devices.search_events': () => ({ events: [{ device: 'Pixel 9', time: '11-20 21:03', app: '视频', summary: '浏览了一个关于 AI 机器人的视频' }] }),
  };
  return { R, memories, steps, run, status, config, devA, devB };
})();
