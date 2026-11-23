// @ts-check
/** Agent status orb, approvals (card + tray), toasts and the activity stream. */
import { html, useState } from '/vendor/preact-htm.js';
import { Icon } from './icons.js';
import { Button, Badge, Empty, cx, When } from './ui.js';
import { store, useStore, act, dismissToast } from '../lib/store.js';
import { PHASES, RISKS, humanize, timeAgo, brief, fmtTime } from '../lib/format.js';
import { linkClick, navigate } from '../lib/hooks.js';

/**
 * Animated status orb. Idle breathes, thinking swirls, acting ripples, waiting blinks amber.
 * @param {{phase?: string, size?: 'sm'|'md'|'lg', avatar?: string, label?: boolean}} p
 */
export function Orb({ phase = 'idle', size = 'md', avatar, label = false }) {
  const ph = PHASES[phase] ? phase : 'idle';
  const isImg = avatar && /^(data:image\/|https?:\/\/|\/)/.test(avatar);
  return html`<div class=${cx('orb', `orb-${size}`, `orb-${ph}`)} role="img" aria-label=${`鲸鱼状态：${PHASES[ph].label}`}>
    <span class="orb-ring orb-ring-1"></span><span class="orb-ring orb-ring-2"></span><span class="orb-ring orb-ring-3"></span>
    <span class="orb-core">
      <span class="orb-swirl"></span>
      ${isImg ? html`<img class="orb-avatar" src=${avatar} alt="" />` : html`<span class="orb-glyph">${avatar || '🐋'}</span>`}
    </span>
    ${label ? html`<span class="orb-label">${PHASES[ph].label}</span>` : null}
  </div>`;
}

/** @param {{a: any, compact?: boolean}} p */
export function ApprovalCard({ a, compact }) {
  const [busy, setBusy] = useState('');
  const risk = RISKS[a.risk] || RISKS.external;
  const resolve = async (approve, remember = false) => {
    setBusy(approve ? (remember ? 'always' : 'yes') : 'no');
    try {
      await act('approvals.resolve', { id: a.id, approve, remember }, { ok: approve ? (remember ? '已批准，并从此始终允许' : '已批准') : '已拒绝' });
      store.set((s) => ({ approvals: s.approvals.filter((x) => x.id !== a.id) }));
    } catch { /* toasted */ } finally { setBusy(''); }
  };
  const args = a.args && Object.keys(a.args).length ? a.args : null;
  return html`<article class=${cx('approval', compact && 'approval-compact')} aria-label=${'审批：' + (a.title || a.capability)}>
    <header class="approval-head">
      <span class="approval-icon"><${Icon} name="shield" /></span>
      <div class="approval-titles">
        <strong>${a.title || a.capability}</strong>
        <span class="approval-meta"><code>${a.capability}</code> · <${Badge} tone=${risk.tone}>${risk.label}<//> · <${When} ms=${a.created_at} fmt=${timeAgo} /></span>
      </div>
    </header>
    ${a.reason ? html`<p class="approval-reason">${a.reason}</p>` : null}
    ${args ? html`<dl class="approval-args">${Object.entries(args).slice(0, 6).map(([k, v]) => html`<div key=${k}><dt>${k}</dt><dd>${brief(v, 140)}</dd></div>`)}</dl>` : null}
    <footer class="approval-actions">
      <${Button} variant="primary" size="sm" icon="check" loading=${busy === 'yes'} disabled=${!!busy} onClick=${() => resolve(true)}>批准<//>
      <${Button} variant="ghost" size="sm" icon="x" loading=${busy === 'no'} disabled=${!!busy} onClick=${() => resolve(false)}>拒绝<//>
      <${Button} variant="plain" size="sm" loading=${busy === 'always'} disabled=${!!busy} title="批准，并为该能力设置“允许”策略" onClick=${() => resolve(true, true)}>始终允许<//>
    </footer>
  </article>`;
}

export function ApprovalTray() {
  const open = useStore((s) => s.trayOpen);
  const approvals = useStore((s) => s.approvals);
  const close = () => store.set({ trayOpen: false });
  return html`<div class=${cx('tray', open && 'is-open')} aria-hidden=${open ? 'false' : 'true'}>
    <div class="tray-scrim" onClick=${close}></div>
    <aside class="tray-panel" role="dialog" aria-label="待批准">
      <header class="tray-head">
        <h2><${Icon} name="shield" /> 待批准 <span class="tray-count">${approvals.length}</span></h2>
        <button type="button" class="icon-btn" aria-label="关闭" onClick=${close}><${Icon} name="x" /></button>
      </header>
      <p class="tray-hint">对外发消息、操作设备等动作默认需要你确认。鲸鱼会在这里等你，任何已连接的客户端都可以处理。</p>
      <div class="tray-list">
        ${approvals.length ? approvals.map((a) => html`<${ApprovalCard} key=${a.id} a=${a} />`)
          : html`<${Empty} icon="shield" title="没有待批准的动作" hint="需要确认时会在这里出现，并在顶栏亮起提示。" compact />`}
      </div>
    </aside>
  </div>`;
}

export function Toasts() {
  const toasts = useStore((s) => s.toasts);
  return html`<div class="toasts" role="region" aria-label="通知" aria-live="polite">
    ${toasts.map((t) => html`<div key=${t.id} class=${cx('toast', `toast-${t.kind}`)} role=${t.kind === 'error' ? 'alert' : 'status'}>
      <span class="toast-icon"><${Icon} name=${{ ok: 'check', error: 'alert', warn: 'shield', notify: 'bell', info: 'info' }[t.kind] || 'info'} /></span>
      <div class="toast-body"><strong>${t.title}</strong>${t.body ? html`<p>${t.body}</p>` : null}</div>
      ${t.action ? html`<button type="button" class="toast-action" onClick=${() => { if (t.action?.run) t.action.run(); else if (t.action?.href) navigate(t.action.href); dismissToast(t.id); }}>${t.action.label}</button>` : null}
      <button type="button" class="toast-x" aria-label="关闭通知" onClick=${() => dismissToast(t.id)}><${Icon} name="x" size=${14} /></button>
    </div>`)}
  </div>`;
}

/** Live humanised activity. @param {{items: any[], limit?: number, dense?: boolean}} p */
export function ActivityStream({ items, limit = 40, dense }) {
  if (!items.length) return html`<${Empty} icon="wave" title="海面很平静" hint="鲸鱼的一举一动都会实时出现在这里：回忆、思考、调用能力、记住新东西。" compact />`;
  let lastDay = '';
  return html`<ol class=${cx('activity', dense && 'activity-dense')}>
    ${items.slice(0, limit).map((ev) => {
      const hu = humanize(ev);
      const day = new Date(ev.ts).toDateString();
      const sep = day !== lastDay && lastDay !== '';
      lastDay = day;
      const body = html`<span class=${cx('act-icon', `tone-${hu.tone}`)}><${Icon} name=${hu.icon} size=${14} /></span>
        <span class="act-text">${hu.text}</span><span class="act-time"><${When} ms=${ev.ts} fmt=${(ms) => fmtTime(ms)} /></span>`;
      return html`<li key=${ev.id} class=${cx('act', sep && 'act-daysep')}>
        ${hu.href ? html`<a class="act-row" href=${hu.href} onClick=${linkClick}>${body}</a>` : html`<div class="act-row">${body}</div>`}
      </li>`;
    })}
  </ol>`;
}
