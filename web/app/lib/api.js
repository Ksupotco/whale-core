// @ts-check
/**
 * Whale Link v1 client: HTTP RPC + one auto-reconnecting WebSocket.
 *
 *   rpc(method, params)            POST /api/rpc/{method} → result | throws RpcError
 *   new Link({token}).connect()    WS /api/link: hello → welcome, events, rpc over WS
 *
 * No framework imports: this module is usable from tests and scripts.
 */

const TOKEN_KEY = 'whale.token';
const PROTOCOL = '1.0';

/** @typedef {{code: string, message: string, retryable?: boolean, details?: any}} ErrorBody */
/** @typedef {{topic: string, data: any, event_id?: string, ts: number}} LinkEvent */
/** @typedef {'idle'|'connecting'|'online'|'reconnecting'|'offline'|'unauthorized'} LinkState */

export class RpcError extends Error {
  /** @param {ErrorBody} body @param {number} [status] */
  constructor(body, status = 0) {
    super(body?.message || body?.code || '请求失败');
    // defineProperty: plain assignment throws where Error.prototype is frozen (hardened realms)
    Object.defineProperty(this, 'name', { value: 'RpcError', configurable: true });
    this.code = body?.code || 'internal';
    this.retryable = !!body?.retryable;
    this.details = body?.details;
    this.status = status;
  }
}

/** Human Chinese text for an error (used by toasts). @param {any} err */
export function errorText(err) {
  if (!err) return '未知错误';
  const map = {
    unauthorized: '访问令牌无效或已过期', forbidden: '没有权限或被策略禁止', not_found: '对象不存在',
    conflict: '状态冲突', invalid_params: '参数不合法', rate_limited: '超出今日模型请求预算',
    unavailable: '依赖暂不可用', timeout: '超时（外部动作结果未知，不会自动重试）', internal: '服务内部错误',
    network: '网络连接失败',
  };
  const head = map[err.code] || '';
  const msg = err.message && err.message !== err.code ? err.message : '';
  return head && msg ? `${head}：${msg}` : head || msg || String(err);
}

// -- token ---------------------------------------------------------------------------------------
const mem = { token: '' };
function storage() {
  try { return globalThis.localStorage || null; } catch { return null; }
}
export function getToken() {
  return mem.token || storage()?.getItem(TOKEN_KEY) || '';
}
/** @param {string} token */
export function setToken(token) {
  mem.token = token || '';
  const s = storage();
  if (!s) return;
  if (token) s.setItem(TOKEN_KEY, token); else s.removeItem(TOKEN_KEY);
}
export function clearToken() { setToken(''); }

/** Accept `?token=…` once (printed by `whale serve`), then strip it from the address bar. */
export function consumeUrlToken() {
  const loc = globalThis.location;
  if (!loc) return false;
  const url = new URL(loc.href);
  const t = url.searchParams.get('token');
  if (!t) return false;
  setToken(t.trim());
  url.searchParams.delete('token');
  globalThis.history?.replaceState(null, '', url.pathname + (url.search || '') + url.hash);
  return true;
}

/** URL for <img>/<audio> of a generated artifact (query token: tags cannot send headers). @param {string} id */
export function artifactUrl(id) {
  return `/api/artifacts/${encodeURIComponent(id)}?token=${encodeURIComponent(getToken())}`;
}

// -- HTTP RPC --------------------------------------------------------------------------------------
/** @type {Set<(err: RpcError) => void>} */
const authListeners = new Set();
/** Called whenever the server rejects the token. @param {(err: RpcError) => void} fn */
export function onUnauthorized(fn) { authListeners.add(fn); return () => authListeners.delete(fn); }

/**
 * @param {string} method
 * @param {Record<string, any>} [params]
 * @param {{timeout?: number, token?: string, signal?: AbortSignal}} [opts]
 * @returns {Promise<any>}
 */
export async function rpc(method, params = {}, opts = {}) {
  const ctrl = new AbortController();
  const timeout = opts.timeout ?? 30000;
  const timer = setTimeout(() => ctrl.abort(), timeout);
  if (opts.signal) opts.signal.addEventListener('abort', () => ctrl.abort(), { once: true });
  let res;
  try {
    res = await fetch(`/api/rpc/${encodeURIComponent(method)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${opts.token ?? getToken()}` },
      body: JSON.stringify(params || {}),
      signal: ctrl.signal,
      cache: 'no-store',
    });
  } catch (e) {
    clearTimeout(timer);
    const aborted = /** @type {any} */ (e)?.name === 'AbortError';
    throw new RpcError({ code: aborted ? 'timeout' : 'network', message: aborted ? '请求超时' : '无法连接到 Whale 服务', retryable: true });
  }
  clearTimeout(timer);
  let body = null;
  try { body = await res.json(); } catch { /* non-JSON */ }
  if (body && body.ok === true) return body.result;
  const err = new RpcError(body?.error || { code: res.status === 401 ? 'unauthorized' : 'internal', message: `HTTP ${res.status}` }, res.status);
  if (err.code === 'unauthorized') authListeners.forEach((fn) => fn(err));
  throw err;
}

// -- topic globs -------------------------------------------------------------------------------------
const globCache = new Map();
/** fnmatch-style: `*` any run, `?` one char. @param {string} pattern */
export function globToRegExp(pattern) {
  let re = globCache.get(pattern);
  if (!re) {
    re = new RegExp('^' + pattern.replace(/[.+^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*').replace(/\?/g, '.') + '$');
    globCache.set(pattern, re);
  }
  return re;
}
/** @param {string} topic @param {string|string[]} patterns */
export function topicMatches(topic, patterns) {
  const list = Array.isArray(patterns) ? patterns : [patterns];
  return list.some((p) => globToRegExp(p).test(topic));
}

let seq = 0;
export function newId(prefix = 'c') {
  seq = (seq + 1) % 1e6;
  return `${prefix}_${Date.now().toString(36)}${seq.toString(36)}${Math.random().toString(36).slice(2, 6)}`;
}

// -- Link ---------------------------------------------------------------------------------------------
export class Link {
  /** @param {{token?: () => string, name?: string, url?: string, subscribe?: string[]}} [opts] */
  constructor(opts = {}) {
    this.tokenFn = opts.token || getToken;
    this.name = opts.name || 'Web 控制台';
    this.subscribe = opts.subscribe || ['*'];
    this.url = opts.url || '';
    /** @type {WebSocket|null} */ this.ws = null;
    /** @type {LinkState} */ this.state = 'idle';
    this.attempt = 0;
    this.welcome = null;
    this.lastRx = 0;
    /** @type {Map<string, {resolve: Function, reject: Function, timer: any}>} */ this.pending = new Map();
    /** @type {Set<{patterns: string[], fn: (ev: LinkEvent) => void}>} */ this.handlers = new Set();
    /** @type {Set<(s: LinkState, info?: any) => void>} */ this.stateFns = new Set();
    this.timers = { retry: 0, ping: 0 };
    this.stopped = true;
    this.nextRetryAt = 0;
  }

  endpoint() {
    if (this.url) return this.url;
    const loc = globalThis.location;
    return `${loc.protocol === 'https:' ? 'wss' : 'ws'}://${loc.host}/api/link`;
  }

  /** @param {LinkState} s @param {any} [info] */
  setState(s, info) {
    if (this.state === s && !info) return;
    this.state = s;
    this.stateFns.forEach((fn) => { try { fn(s, info); } catch (e) { console.error(e); } });
  }

  /** @param {(s: LinkState, info?: any) => void} fn */
  onState(fn) { this.stateFns.add(fn); return () => this.stateFns.delete(fn); }

  /** Subscribe to topics (glob). Returns an unsubscribe function. @param {string|string[]} patterns @param {(ev: LinkEvent) => void} fn */
  on(patterns, fn) {
    const h = { patterns: Array.isArray(patterns) ? patterns : [patterns], fn };
    this.handlers.add(h);
    return () => this.handlers.delete(h);
  }

  connect() {
    this.stopped = false;
    clearTimeout(this.timers.retry);
    if (this.ws && (this.ws.readyState === 0 || this.ws.readyState === 1)) return;
    if (!this.tokenFn()) { this.setState('unauthorized'); return; }
    this.setState(this.attempt ? 'reconnecting' : 'connecting');
    let ws;
    try { ws = new WebSocket(this.endpoint()); } catch { this.scheduleRetry(); return; }
    this.ws = ws;
    const helloId = newId('h');
    ws.onopen = () => {
      ws.send(JSON.stringify({ v: PROTOCOL, id: helloId, type: 'hello', ts: Date.now(), corr: null, payload: {
        token: this.tokenFn(), role: 'console', name: this.name, subscribe: this.subscribe, platform: 'web' } }));
    };
    ws.onmessage = (msg) => {
      this.lastRx = Date.now();
      let env;
      try { env = JSON.parse(String(msg.data)); } catch { return; }
      this.handle(env, helloId);
    };
    ws.onclose = (ev) => {
      if (this.ws !== ws) return;
      this.ws = null;
      clearInterval(this.timers.ping);
      this.failPending('连接已断开');
      if (ev.code === 4401) { this.stopped = true; this.setState('unauthorized'); return; }
      if (!this.stopped) this.scheduleRetry();
      else this.setState('offline');
    };
    ws.onerror = () => { /* onclose follows */ };
  }

  /** @param {any} env @param {string} helloId */
  handle(env, helloId) {
    const p = env.payload || {};
    switch (env.type) {
      case 'welcome':
        this.attempt = 0;
        this.welcome = p;
        this.setState('online', p);
        clearInterval(this.timers.ping);
        this.timers.ping = setInterval(() => this.heartbeat(), 20000);
        break;
      case 'event': {
        /** @type {LinkEvent} */
        const ev = { topic: p.topic, data: p.data || {}, event_id: p.event_id, ts: Date.now() };
        this.handlers.forEach((h) => {
          if (topicMatches(ev.topic, h.patterns)) { try { h.fn(ev); } catch (e) { console.error('event handler', ev.topic, e); } }
        });
        break;
      }
      case 'rpc.response': {
        const pend = this.pending.get(env.corr);
        if (!pend) return;
        this.pending.delete(env.corr);
        clearTimeout(pend.timer);
        if (p.ok) pend.resolve(p.result); else pend.reject(new RpcError(p.error));
        break;
      }
      case 'rpc.request':
        // The console declares no capabilities; answer politely so the server never waits.
        this.send({ type: 'rpc.response', corr: env.id, payload: { ok: false, error: { code: 'not_found', message: 'console has no capabilities', retryable: false } } });
        break;
      case 'ping':
        this.send({ type: 'pong', corr: env.id, payload: {} });
        break;
      case 'error':
        if (p.code === 'unauthorized' || env.corr === helloId) {
          this.stopped = true;
          this.setState('unauthorized', p);
          this.ws?.close();
        }
        break;
      default:
    }
  }

  heartbeat() {
    if (!this.ws || this.ws.readyState !== 1) return;
    if (Date.now() - this.lastRx > 65000) { this.ws.close(); return; }
    this.send({ type: 'ping', payload: {} });
  }

  /** @param {{type: string, payload?: any, corr?: any, id?: string}} env */
  send(env) {
    if (!this.ws || this.ws.readyState !== 1) return false;
    this.ws.send(JSON.stringify({ v: PROTOCOL, id: env.id || newId('m'), ts: Date.now(), corr: env.corr ?? null, ...env }));
    return true;
  }

  get online() { return this.state === 'online' && !!this.ws && this.ws.readyState === 1; }

  /** RPC over the socket (channel = "console"). @param {string} method @param {any} [params] @param {number} [timeout] */
  call(method, params = {}, timeout = 30000) {
    return new Promise((resolve, reject) => {
      if (!this.online) { reject(new RpcError({ code: 'unavailable', message: '实时连接未就绪', retryable: true })); return; }
      const id = newId('r');
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new RpcError({ code: 'timeout', message: '请求超时', retryable: true }));
      }, timeout);
      this.pending.set(id, { resolve, reject, timer });
      this.send({ id, type: 'rpc.request', payload: { method, params } });
    });
  }

  /** @param {string} message */
  failPending(message) {
    this.pending.forEach((p) => { clearTimeout(p.timer); p.reject(new RpcError({ code: 'unavailable', message, retryable: true })); });
    this.pending.clear();
  }

  scheduleRetry() {
    this.attempt += 1;
    const base = Math.min(15000, 600 * 2 ** Math.min(this.attempt - 1, 5));
    const delay = Math.round(base * (0.75 + Math.random() * 0.5));
    this.nextRetryAt = Date.now() + delay;
    this.setState('reconnecting', { attempt: this.attempt, retryAt: this.nextRetryAt });
    clearTimeout(this.timers.retry);
    this.timers.retry = setTimeout(() => this.connect(), delay);
  }

  /** Reconnect immediately (e.g. after the tab becomes visible or a new token). */
  retryNow() {
    this.attempt = Math.max(this.attempt, 1);
    clearTimeout(this.timers.retry);
    if (this.ws) { try { this.ws.close(); } catch { /* ignore */ } this.ws = null; }
    this.connect();
  }

  close() {
    this.stopped = true;
    clearTimeout(this.timers.retry);
    clearInterval(this.timers.ping);
    if (this.ws) { const ws = this.ws; this.ws = null; try { ws.close(1000); } catch { /* ignore */ } }
    this.failPending('连接已关闭');
    this.setState('offline');
  }
}
