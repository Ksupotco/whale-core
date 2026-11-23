// @ts-check
/** First screen: ask for the access token printed by `whale serve` (or `whale token`). */
import { html, useState } from '/vendor/preact-htm.js';
import { Icon } from '../components/icons.js';
import { Orb } from '../components/live.js';
import { Button } from '../components/ui.js';
import { rpc, setToken, errorText } from '../lib/api.js';
import { store, useStore, startLive } from '../lib/store.js';

export function Login() {
  const reason = useStore((s) => s.authReason);
  const [token, setTok] = useState('');
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const submit = async (e) => {
    e?.preventDefault?.();
    const t = token.trim();
    if (!t) { setErr('请粘贴访问令牌'); return; }
    setBusy(true); setErr('');
    try {
      const status = await rpc('system.status', {}, { token: t, timeout: 10000 });
      setToken(t);
      store.set({ token: t, status, authReason: '' });
      startLive();
    } catch (ex) {
      setErr(/** @type {any} */ (ex).code === 'unauthorized' ? '令牌不正确。请复制 `whale token` 输出的完整令牌。' : errorText(ex));
    } finally { setBusy(false); }
  };
  return html`<div class="login">
    <div class="login-sea" aria-hidden="true"><span></span><span></span><span></span></div>
    <form class="login-card" onSubmit=${submit} aria-labelledby="login-title">
      <${Orb} phase=${busy ? 'thinking' : 'idle'} size="lg" />
      <h1 id="login-title">连接你的鲸鱼</h1>
      <p class="login-lead">Whale 是一个常驻在你电脑上的智能体。控制台需要访问令牌才能和它对话、查看它的记忆与行动。</p>
      ${reason ? html`<p class="login-reason" role="alert"><${Icon} name="alert" size=${15} />${reason}</p>` : null}
      <label class="field">
        <span class="field-label">访问令牌</span>
        <span class="login-input">
          <input class="input mono" type=${show ? 'text' : 'password'} value=${token} autocomplete="current-password" spellcheck="false"
            placeholder="粘贴令牌，例如 Xk3…" autofocus onInput=${(e) => setTok(e.currentTarget.value)} aria-invalid=${err ? 'true' : 'false'} />
          <button type="button" class="icon-btn" aria-label=${show ? '隐藏令牌' : '显示令牌'} onClick=${() => setShow(!show)}><${Icon} name=${show ? 'lock' : 'eye'} /></button>
        </span>
        ${err ? html`<span class="field-error" role="alert">${err}</span>` : null}
      </label>
      <${Button} variant="primary" size="lg" icon="link" loading=${busy} onClick=${submit} class="login-btn">连接<//>
      <div class="login-help">
        <p><strong>令牌在哪里？</strong></p>
        <ol>
          <li>运行 <code>whale serve</code> 时，终端会打印“访问令牌”。在地址后加 <code>?token=令牌</code> 打开也能自动登录。</li>
          <li>或运行 <code>whale token</code> 查看（加 <code>--rotate</code> 轮换）。</li>
        </ol>
        <p class="muted">令牌只保存在本浏览器中。轮换后需要重新输入。</p>
      </div>
    </form>
  </div>`;
}
