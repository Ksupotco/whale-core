// @ts-check
/**
 * 人格 Persona: persona gallery (activate / edit / delete), Character Card V2 import (JSON or PNG
 * with an embedded `chara` chunk), and the habits the agent formed by itself (style memories).
 */
import { html, useRef, useState } from '/vendor/preact-htm.js';
import { Icon } from '../components/icons.js';
import { Badge, Button, AsyncButton, Card, ConfirmButton, Empty, Field, PageHead, Skeleton, ErrorBox, Modal, cx } from '../components/ui.js';
import { act, useTopic, toast, refreshStatusSoon } from '../lib/store.js';
import { useRpc, useDebouncedCallback } from '../lib/hooks.js';
import { readImage } from './chat.js';
import { timeAgo, clip } from '../lib/format.js';

const FIELDS = [
  ['description', '身份', '他/她是谁，背景与经历', 3], ['personality', '性格', '性格特点、价值观', 2], ['style', '说话方式', '语气、用词、长短', 2],
  ['scenario', '关系与情境', '和你是什么关系，在什么情境下', 2], ['greeting', '开场白', '第一次见面说的话', 2],
  ['examples', '示例对话', '几段示例，帮助模型抓住感觉', 4], ['boundaries', '边界', '不想让他/她做或说的事', 2],
];

/** @param {{avatar?: string, size?: number}} p */
export function Avatar({ avatar, size = 56 }) {
  const img = avatar && /^(data:image\/|https?:\/\/|\/)/.test(avatar);
  return html`<span class="avatar" style=${{ width: `${size}px`, height: `${size}px`, fontSize: `${Math.round(size * 0.55)}px` }}>
    ${img ? html`<img src=${avatar} alt="" />` : avatar || '🐋'}</span>`;
}

/** Extract the Character Card JSON from a PNG `tEXt` chunk named "chara" (base64). @param {ArrayBuffer} buf */
export function cardFromPng(buf) {
  const bytes = new Uint8Array(buf);
  const view = new DataView(buf);
  let off = 8;
  while (off + 8 < bytes.length) {
    const len = view.getUint32(off);
    const type = String.fromCharCode(...bytes.slice(off + 4, off + 8));
    if (type === 'tEXt' || type === 'iTXt') {
      const data = bytes.slice(off + 8, off + 8 + len);
      const zero = data.indexOf(0);
      const key = String.fromCharCode(...data.slice(0, zero));
      if (key === 'chara' || key === 'ccv3') {
        let rest = data.slice(zero + 1);
        if (type === 'iTXt') { let z = 0; let i = 2; while (z < 2 && i < rest.length) { if (rest[i] === 0) z++; i++; } rest = rest.slice(i); }
        const b64 = new TextDecoder().decode(rest).trim();
        const bin = atob(b64);
        const u8 = Uint8Array.from(bin, (c) => c.charCodeAt(0));
        return JSON.parse(new TextDecoder().decode(u8));
      }
    }
    if (type === 'IEND') break;
    off += 12 + len;
  }
  throw new Error('这张 PNG 里没有角色卡数据（chara）');
}

function newId() { return 'persona_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6); }

/** @param {{card: any, onClose: () => void, onSaved: () => void}} p */
function PersonaEditor({ card, onClose, onSaved }) {
  const [f, setF] = useState(() => ({ tagline: '', description: '', personality: '', style: '', scenario: '', greeting: '', examples: '', boundaries: '', avatar: '🐋', color: '#0ea5b7', extra: {}, ...card }));
  const fileRef = useRef(/** @type {HTMLInputElement|null} */ (null));
  const save = async () => {
    await act('personas.save', { ...f, name: f.name.trim(), builtin: false }, { ok: `已保存「${f.name}」` });
    onSaved(); onClose();
  };
  return html`<${Modal} open onClose=${onClose} wide title=${card.name ? `编辑人格 · ${card.name}` : '新建人格'} footer=${html`
    <${Button} variant="ghost" onClick=${onClose}>取消<//><${AsyncButton} variant="primary" icon="check" disabled=${!f.name?.trim()} onClick=${save}>保存<//>`}>
    ${card.builtin ? html`<p class="banner banner-info small"><${Icon} name="info" size=${15} />这是内置人格，保存后会成为你的自定义版本。</p>` : null}
    <div class="persona-edit-top">
      <div class="avatar-pick">
        <${Avatar} avatar=${f.avatar} size=${72} />
        <input ref=${fileRef} type="file" accept="image/*" hidden onChange=${async (e) => { const fl = e.currentTarget.files?.[0]; if (fl) setF({ ...f, avatar: await readImage(fl) }); }} />
        <${Button} size="sm" variant="ghost" icon="upload" onClick=${() => fileRef.current?.click()}>上传头像<//>
      </div>
      <div class="form-grid grow">
        <${Field} label="名字"><input class="input" value=${f.name || ''} onInput=${(e) => setF({ ...f, name: e.currentTarget.value })} /><//>
        <${Field} label="一句话介绍"><input class="input" value=${f.tagline} onInput=${(e) => setF({ ...f, tagline: e.currentTarget.value })} /><//>
        <${Field} label="头像（emoji 或上传图片）"><input class="input" value=${f.avatar?.startsWith('data:') ? '（已上传图片）' : f.avatar} disabled=${f.avatar?.startsWith('data:')} onInput=${(e) => setF({ ...f, avatar: e.currentTarget.value })} /><//>
        <${Field} label="主题色"><span class="color-pick"><input type="color" value=${f.color} aria-label="主题色" onInput=${(e) => setF({ ...f, color: e.currentTarget.value })} /><code>${f.color}</code></span><//>
      </div>
    </div>
    ${FIELDS.map(([k, label, hint, rows]) => html`<${Field} key=${k} label=${label} hint=${hint}>
      <textarea class="input" rows=${rows} value=${f[k] || ''} onInput=${(e) => setF({ ...f, [k]: e.currentTarget.value })}></textarea><//>`)}
  <//>`;
}

function Habits() {
  const res = useRpc('memory.list', { kind: 'style', status: 'active', limit: 100, order: 'updated_at DESC' });
  const [edit, setEdit] = useState(/** @type {{id: string, content: string}|null} */ (null));
  const refresh = useDebouncedCallback(() => res.reload(true), 500);
  useTopic('memory.*', refresh);
  const items = res.data?.items || [];
  return html`<${Card} title="相处中形成的习惯" icon="sparkles" subtitle="鲸鱼在和大家相处时，自己总结出的性格与口癖。它们是唯一在所有对话（包括群聊、他人私聊）中共享的记忆，每次回复都会用到。">
    ${res.error ? html`<${ErrorBox} error=${res.error} onRetry=${() => res.reload()} />` : null}
    ${!res.data ? html`<${Skeleton} rows=${3} />` : items.length ? html`<ul class="habits">${items.map((m) => html`<li key=${m.id}>
      ${edit?.id === m.id ? html`<form class="inline-form grow" onSubmit=${async (e) => { e.preventDefault(); await act('memory.update', { id: m.id, content: edit.content }, { ok: '已更新' }); setEdit(null); res.reload(true); }}>
        <input class="input" value=${edit.content} aria-label="习惯内容" onInput=${(e) => setEdit({ id: m.id, content: e.currentTarget.value })} />
        <${Button} size="sm" variant="primary" icon="check" type="submit" aria-label="保存" /><${Button} size="sm" variant="ghost" icon="x" aria-label="取消" onClick=${() => setEdit(null)} /></form>`
        : html`<span class="habit-text">“${m.content}”</span><span class="muted small">${timeAgo(m.updated_at)} · 想起 ${m.access_count} 次</span>
          <span class="row-actions"><${Button} size="sm" variant="ghost" icon="edit" aria-label="编辑" onClick=${() => setEdit({ id: m.id, content: m.content })} />
          <${ConfirmButton} icon="trash" confirmText="遗忘？" aria-label="遗忘" onConfirm=${async () => { await act('memory.forget', { id: m.id }, { ok: '已遗忘这个习惯' }); res.reload(true); }} /></span>`}
    </li>`)}</ul>` : html`<${Empty} icon="sparkles" title="还没有形成习惯" hint="多聊一段时间，鲸鱼会慢慢总结出自己的说话方式。" compact />`}
  <//>`;
}

export function PersonaPage() {
  const res = useRpc('personas.list');
  const [editing, setEditing] = useState(/** @type {any} */ (null));
  const fileRef = useRef(/** @type {HTMLInputElement|null} */ (null));
  useTopic(['persona.*'], () => res.reload(true));
  const reload = () => { res.reload(true); refreshStatusSoon(100); };
  const importFile = async (file) => {
    try {
      let card;
      if (file.type === 'image/png' || /\.png$/i.test(file.name)) {
        card = cardFromPng(await file.arrayBuffer());
        const avatar = await readImage(file);
        const d = card.data || card;
        if (!d.avatar && avatar.length < 400000) d.avatar = avatar;
      } else card = JSON.parse(await file.text());
      const saved = await act('personas.import', { card }, { ok: '角色卡已导入' });
      reload();
      if (saved) setEditing(saved);
    } catch (e) {
      // RPC errors were already shown by act(); parsing errors are shown here
      if (/** @type {any} */ (e)?.name !== 'RpcError') toast({ kind: 'error', title: '导入失败', body: String(/** @type {any} */ (e)?.message || e) });
    }
  };
  const personas = res.data?.personas || [];
  const active = res.data?.active;
  return html`<div class="page persona">
    <${PageHead} title="人格" icon="user" subtitle="鲸鱼是谁：人格卡是数据，不写死在提示词里。兼容 SillyTavern / Character Card V2。"
      actions=${html`<input ref=${fileRef} type="file" accept=".json,application/json,image/png" hidden onChange=${(e) => { const f = e.currentTarget.files?.[0]; if (f) importFile(f); e.currentTarget.value = ''; }} />
        <${Button} variant="ghost" icon="upload" onClick=${() => fileRef.current?.click()}>导入角色卡<//>
        <${Button} variant="primary" icon="plus" onClick=${() => setEditing({ id: newId(), name: '' })}>新建人格<//>`} />
    ${res.error ? html`<${ErrorBox} error=${res.error} onRetry=${() => res.reload()} />` : null}
    ${!res.data ? html`<${Skeleton} rows=${3} height=${90} />` : html`<div class="persona-grid">${personas.map((p) => html`<article key=${p.id}
      class=${cx('persona-card', p.id === active && 'is-active')} style=${{ '--persona': p.color || 'var(--accent)' }}>
      <div class="persona-card-top"><${Avatar} avatar=${p.avatar} size=${64} />
        <div><h3>${p.name}</h3><p class="muted small">${p.tagline || clip(p.description, 40) || '—'}</p></div>
        ${p.id === active ? html`<${Badge} tone="accent" icon="check">当前<//>` : null}</div>
      <p class="persona-desc">${clip(p.personality || p.description || p.style, 110) || html`<span class="muted">还没有描述</span>`}</p>
      ${p.greeting ? html`<p class="persona-greet">“${clip(p.greeting, 60)}”</p>` : null}
      <footer class="persona-actions">
        ${p.id !== active ? html`<${AsyncButton} size="sm" variant="primary" icon="check" onClick=${async () => { await act('personas.activate', { id: p.id }, { ok: `已切换为「${p.name}」` }); reload(); }}>启用<//>` : html`<span class="muted small">所有新对话都以这个人格进行</span>`}
        <${Button} size="sm" variant="ghost" icon="edit" onClick=${() => setEditing(p)}>编辑<//>
        ${p.id !== 'default' ? html`<${ConfirmButton} icon="trash" confirmText="删除？" onConfirm=${async () => { await act('personas.delete', { id: p.id }, { ok: '已删除' }); reload(); }}>删除<//>` : null}
      </footer>
    </article>`)}
      <button type="button" class="persona-card persona-new" onClick=${() => setEditing({ id: newId(), name: '' })}><${Icon} name="plus" size=${26} /><span>新建人格</span><small>也可以导入 JSON / PNG 角色卡</small></button>
    </div>`}
    <${Habits} />
    ${editing ? html`<${PersonaEditor} card=${editing} onClose=${() => setEditing(null)} onSaved=${reload} />` : null}
  </div>`;
}

