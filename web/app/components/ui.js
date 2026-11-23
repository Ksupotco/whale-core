// @ts-check
/** UI primitives: buttons, switches, segmented controls, badges, cards, modal/drawer, tag editor, fields. */
import { html, useEffect, useRef, useState } from '/vendor/preact-htm.js';
import { Icon } from './icons.js';
import { useEscape, linkClick } from '../lib/hooks.js';

/** @param {...any} parts */
export const cx = (...parts) => parts.filter(Boolean).join(' ');

/**
 * @param {{variant?: 'primary'|'ghost'|'soft'|'danger'|'plain', size?: 'sm'|'md'|'lg', icon?: string, loading?: boolean,
 *   href?: string, class?: string, children?: any, [k: string]: any}} props
 */
export function Button({ variant = 'soft', size = 'md', icon, loading, href, class: cls, children, ...rest }) {
  const c = cx('btn', `btn-${variant}`, `btn-${size}`, loading && 'is-loading', !children && 'btn-icon', cls);
  const inner = html`${loading ? html`<span class="spinner" aria-hidden="true"></span>` : icon ? html`<${Icon} name=${icon} size=${size === 'sm' ? 15 : 17} />` : null}${children ? html`<span>${children}</span>` : null}`;
  if (href) return html`<a ...${rest} class=${c} href=${href} onClick=${href.startsWith('/') ? linkClick : undefined}>${inner}</a>`;
  return html`<button type="button" ...${rest} class=${c} disabled=${!!(rest.disabled || loading)} aria-busy=${loading ? 'true' : undefined}>${inner}</button>`;
}

/** Async-aware button: shows a spinner while `onClick` resolves. */
export function AsyncButton({ onClick, ...rest }) {
  const [busy, setBusy] = useState(false);
  const mounted = useRef(true);
  useEffect(() => () => { mounted.current = false; }, []);
  const run = async (e) => {
    if (busy) return;
    setBusy(true);
    try { await onClick?.(e); } catch { /* act() already toasted */ } finally { if (mounted.current) setBusy(false); }
  };
  return html`<${Button} ...${rest} loading=${busy} onClick=${run} />`;
}

/** @param {{checked: boolean, onChange: (v: boolean) => void, label?: string, disabled?: boolean, size?: 'sm'|'md'}} p */
export function Switch({ checked, onChange, label, disabled, size = 'md' }) {
  return html`<button type="button" role="switch" aria-checked=${checked ? 'true' : 'false'} aria-label=${label}
    class=${cx('switch', `switch-${size}`, checked && 'is-on')} disabled=${disabled}
    onClick=${(e) => { e.stopPropagation(); onChange(!checked); }}><span class="switch-knob"></span></button>`;
}

/**
 * @param {{options: {value: any, label: any, tone?: string, title?: string}[], value: any, onChange: (v: any) => void, size?: 'sm'|'md', label?: string, class?: string}} p
 */
export function Segmented({ options, value, onChange, size = 'md', label, class: cls }) {
  const onKey = (e) => {
    const i = options.findIndex((o) => o.value === value);
    if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
      e.preventDefault();
      const n = options[(i + (e.key === 'ArrowRight' ? 1 : options.length - 1)) % options.length];
      onChange(n.value);
    }
  };
  return html`<div class=${cx('seg', `seg-${size}`, cls)} role="radiogroup" aria-label=${label} onKeyDown=${onKey}>
    ${options.map((o) => html`<button type="button" role="radio" key=${String(o.value)} aria-checked=${o.value === value ? 'true' : 'false'}
      tabindex=${o.value === value ? 0 : -1} title=${o.title}
      class=${cx('seg-btn', o.value === value && 'is-on', o.tone && `tone-${o.tone}`)} onClick=${() => onChange(o.value)}>${o.label}</button>`)}
  </div>`;
}

/** @param {{tone?: string, children?: any, icon?: string, class?: string, title?: string, dot?: boolean}} p */
export function Badge({ tone = 'muted', children, icon, class: cls, title, dot }) {
  return html`<span class=${cx('badge', `tone-${tone}`, cls)} title=${title}>${dot ? html`<i class="badge-dot"></i>` : null}${icon ? html`<${Icon} name=${icon} size=${12} />` : null}${children}</span>`;
}

/** @param {{children?: any, active?: boolean, onClick?: Function, color?: string, count?: number, class?: string, title?: string}} p */
export function Chip({ children, active, onClick, color, count, class: cls, title }) {
  const style = color ? { '--chip': color } : undefined;
  if (!onClick) return html`<span class=${cx('chip', color && 'chip-color', cls)} style=${style} title=${title}>${color ? html`<i class="chip-dot"></i>` : null}${children}</span>`;
  return html`<button type="button" class=${cx('chip', 'chip-btn', active && 'is-on', color && 'chip-color', cls)} style=${style} title=${title}
    aria-pressed=${active ? 'true' : 'false'} onClick=${onClick}>${color ? html`<i class="chip-dot"></i>` : null}${children}${count != null ? html`<span class="chip-count">${count}</span>` : null}</button>`;
}

/** @param {{title?: any, subtitle?: any, icon?: string, actions?: any, children?: any, class?: string, pad?: boolean, id?: string}} p */
export function Card({ title, subtitle, icon, actions, children, class: cls, pad = true, id }) {
  return html`<section class=${cx('card', cls)} id=${id}>
    ${title || actions ? html`<header class="card-head">
      ${icon ? html`<span class="card-icon"><${Icon} name=${icon} size=${18} /></span>` : null}
      <div class="card-titles">${title ? html`<h3 class="card-title">${title}</h3>` : null}${subtitle ? html`<p class="card-sub">${subtitle}</p>` : null}</div>
      ${actions ? html`<div class="card-actions">${actions}</div>` : null}
    </header>` : null}
    <div class=${pad ? 'card-body' : 'card-body-flush'}>${children}</div>
  </section>`;
}

/** @param {{icon?: string, title: any, hint?: any, children?: any, compact?: boolean}} p */
export function Empty({ icon = 'wave', title, hint, children, compact }) {
  return html`<div class=${cx('empty', compact && 'empty-compact')}>
    <div class="empty-icon"><${Icon} name=${icon} size=${compact ? 20 : 28} /></div>
    <p class="empty-title">${title}</p>${hint ? html`<p class="empty-hint">${hint}</p>` : null}${children ? html`<div class="empty-actions">${children}</div>` : null}
  </div>`;
}

export function Spinner({ label = '加载中' }) {
  return html`<span class="spinner-wrap" role="status"><span class="spinner"></span><span class="sr-only">${label}</span></span>`;
}

/** Shimmer placeholder rows. @param {{rows?: number, height?: number}} p */
export function Skeleton({ rows = 3, height = 16 }) {
  return html`<div class="skeleton" aria-hidden="true">${Array.from({ length: rows }, (_, i) => html`<div key=${i} class="skeleton-row" style=${{ height: `${height}px`, width: `${92 - (i % 3) * 17}%` }}></div>`)}</div>`;
}

/** Error box for failed loads. @param {{error: any, onRetry?: Function}} p */
export function ErrorBox({ error, onRetry }) {
  return html`<div class="errbox" role="alert"><${Icon} name="alert" /><span>${error?.message || String(error)}</span>${onRetry ? html`<${Button} size="sm" variant="ghost" icon="refresh" onClick=${() => onRetry()}>重试<//>` : null}</div>`;
}

/**
 * @param {{open: boolean, onClose: () => void, title?: any, children?: any, footer?: any, wide?: boolean, class?: string}} p
 */
export function Modal({ open, onClose, title, children, footer, wide, class: cls }) {
  useEscape(open, onClose);
  const box = useRef(/** @type {HTMLElement|null} */ (null));
  useEffect(() => {
    if (!open) return undefined;
    const prev = /** @type {HTMLElement|null} */ (globalThis.document?.activeElement);
    const el = box.current?.querySelector?.('input, textarea, select, button:not(.modal-x)');
    /** @type {any} */ (el)?.focus?.();
    return () => prev?.focus?.();
  }, [open]);
  if (!open) return null;
  return html`<div class="overlay" onClick=${(e) => { if (e.target === e.currentTarget) onClose(); }}>
    <div class=${cx('modal', wide && 'modal-wide', cls)} role="dialog" aria-modal="true" aria-label=${typeof title === 'string' ? title : undefined} ref=${box}>
      <header class="modal-head"><h2>${title}</h2><button type="button" class="modal-x icon-btn" aria-label="关闭" onClick=${onClose}><${Icon} name="x" /></button></header>
      <div class="modal-body">${children}</div>
      ${footer ? html`<footer class="modal-foot">${footer}</footer>` : null}
    </div>
  </div>`;
}

/** Side drawer (right). @param {{open: boolean, onClose: () => void, title?: any, children?: any, class?: string, actions?: any}} p */
export function Drawer({ open, onClose, title, children, class: cls, actions }) {
  useEscape(open, onClose);
  return html`<div class=${cx('drawer-wrap', open && 'is-open')} aria-hidden=${open ? 'false' : 'true'}>
    <div class="drawer-scrim" onClick=${onClose}></div>
    <aside class=${cx('drawer', cls)} role="dialog" aria-label=${typeof title === 'string' ? title : undefined}>
      <header class="drawer-head"><h2>${title}</h2>${actions}<button type="button" class="icon-btn" aria-label="关闭" onClick=${onClose}><${Icon} name="x" /></button></header>
      <div class="drawer-body">${open ? children : null}</div>
    </aside>
  </div>`;
}

/** Inline two-step confirmation (no blocking dialogs). */
export function ConfirmButton({ onConfirm, children, confirmText = '确定？', icon, variant = 'ghost', size = 'sm', ...rest }) {
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!armed) return undefined;
    const t = setTimeout(() => setArmed(false), 4000);
    return () => clearTimeout(t);
  }, [armed]);
  const click = async () => {
    if (!armed) { setArmed(true); return; }
    setBusy(true);
    try { await onConfirm(); } catch { /* toasted */ } finally { setBusy(false); setArmed(false); }
  };
  return html`<${Button} ...${rest} size=${size} icon=${icon} variant=${armed ? 'danger' : variant} loading=${busy} onClick=${click}>${armed ? confirmText : children}<//>`;
}

/** @param {{label: any, hint?: any, children?: any, inline?: boolean, class?: string, error?: string}} p */
export function Field({ label, hint, children, inline, class: cls, error }) {
  return html`<label class=${cx('field', inline && 'field-inline', cls)}>
    <span class="field-label">${label}</span>
    <span class="field-control">${children}</span>
    ${error ? html`<span class="field-error">${error}</span>` : hint ? html`<span class="field-hint">${hint}</span>` : null}
  </label>`;
}

/**
 * Tag editor: Enter/comma adds, Backspace on empty removes the last tag.
 * @param {{value: string[], onChange: (v: string[]) => void, placeholder?: string, validate?: (t: string) => boolean, label?: string, mono?: boolean}} p
 */
export function TagEditor({ value, onChange, placeholder = '输入后回车添加', validate, label, mono }) {
  const [draft, setDraft] = useState('');
  const [bad, setBad] = useState(false);
  const add = (raw) => {
    const parts = String(raw).split(/[,，\n]/).map((s) => s.trim()).filter(Boolean);
    if (!parts.length) return;
    const ok = parts.filter((t) => !validate || validate(t));
    setBad(ok.length !== parts.length);
    const next = [...value];
    ok.forEach((t) => { if (!next.includes(t)) next.push(t); });
    if (next.length !== value.length) onChange(next);
    setDraft('');
  };
  return html`<div class=${cx('tags', mono && 'tags-mono', bad && 'is-bad')} onClick=${(e) => e.currentTarget.querySelector('input')?.focus()}>
    ${value.map((t) => html`<span class="tag" key=${t}>${t}<button type="button" aria-label=${'移除 ' + t} onClick=${() => onChange(value.filter((x) => x !== t))}><${Icon} name="x" size=${12} /></button></span>`)}
    <input value=${draft} aria-label=${label || placeholder} placeholder=${value.length ? '' : placeholder}
      onInput=${(e) => { setBad(false); setDraft(e.currentTarget.value); }}
      onKeyDown=${(e) => {
        if (e.key === 'Enter' || e.key === ',' || e.key === '，') { e.preventDefault(); add(draft); }
        else if (e.key === 'Backspace' && !draft && value.length) onChange(value.slice(0, -1));
      }}
      onBlur=${() => draft && add(draft)} />
  </div>`;
}

/** Horizontal meter. @param {{value: number, max?: number, tone?: string, label?: string}} p */
export function Meter({ value, max = 1, tone = 'accent', label }) {
  const pct = Math.max(0, Math.min(100, (value / (max || 1)) * 100));
  return html`<div class=${cx('meter', `tone-${tone}`)} role="meter" aria-valuenow=${value} aria-valuemin="0" aria-valuemax=${max} aria-label=${label}>
    <span style=${{ width: `${pct}%` }}></span></div>`;
}

/** Collapsible section. @param {{title: any, children?: any, open?: boolean, hint?: any}} p */
export function Disclosure({ title, children, open: initial = false, hint }) {
  const [open, setOpen] = useState(initial);
  return html`<div class=${cx('disclosure', open && 'is-open')}>
    <button type="button" class="disclosure-head" aria-expanded=${open ? 'true' : 'false'} onClick=${() => setOpen(!open)}>
      <${Icon} name="chevron" size=${14} class="disclosure-chev" /><span>${title}</span>${hint ? html`<span class="disclosure-hint">${hint}</span>` : null}
    </button>
    ${open ? html`<div class="disclosure-body">${children}</div>` : null}
  </div>`;
}

/** Page header shared by all pages. @param {{title: any, subtitle?: any, icon?: string, actions?: any, children?: any}} p */
export function PageHead({ title, subtitle, icon, actions, children }) {
  return html`<header class="page-head">
    <div class="page-head-main">
      ${icon ? html`<span class="page-icon"><${Icon} name=${icon} size=${22} /></span>` : null}
      <div><h1>${title}</h1>${subtitle ? html`<p class="page-sub">${subtitle}</p>` : null}</div>
    </div>
    ${actions ? html`<div class="page-actions">${actions}</div>` : null}
    ${children}
  </header>`;
}

/** Stat figure. @param {{label: any, value: any, hint?: any, tone?: string, icon?: string}} p */
export function Stat({ label, value, hint, tone, icon }) {
  return html`<div class=${cx('stat', tone && `tone-${tone}`)}>
    <span class="stat-label">${icon ? html`<${Icon} name=${icon} size=${14} />` : null}${label}</span>
    <span class="stat-value">${value}</span>${hint ? html`<span class="stat-hint">${hint}</span>` : null}
  </div>`;
}

/** Simple tab strip. @param {{tabs: {id: string, label: any, icon?: string, count?: number}[], value: string, onChange: (id: string) => void}} p */
export function Tabs({ tabs, value, onChange }) {
  return html`<div class="tabs" role="tablist">${tabs.map((t) => html`<button type="button" role="tab" key=${t.id} aria-selected=${t.id === value ? 'true' : 'false'}
    class=${cx('tab', t.id === value && 'is-on')} onClick=${() => onChange(t.id)}>${t.icon ? html`<${Icon} name=${t.icon} size=${15} />` : null}${t.label}${t.count ? html`<span class="tab-count">${t.count}</span>` : null}</button>`)}</div>`;
}

/** Relative time that re-renders itself (cheap). @param {{ms: number|null|undefined, fmt: (ms: any) => string}} p */
export function When({ ms, fmt }) {
  const [, tick] = useState(0);
  useEffect(() => { const t = setInterval(() => tick((x) => x + 1), 30000); return () => clearInterval(t); }, []);
  return html`<time title=${ms ? new Date(ms).toLocaleString('zh-CN') : ''}>${fmt(ms)}</time>`;
}
