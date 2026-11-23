// @ts-check
/**
 * 能力 Capabilities: everything the model can call, grouped by provider (内置 / 插件 / 设备 / 客户端),
 * with risk, "仅主人会话", stats and a per-capability policy switch. Glob rules and plugins
 * (enable, status, generated config form) below.
 */
import { html, useMemo, useState } from '/vendor/preact-htm.js';
import { Icon } from '../components/icons.js';
import { Badge, Button, Card, Chip, Empty, PageHead, Segmented, Skeleton, Switch, ErrorBox, Disclosure, cx, AsyncButton } from '../components/ui.js';
import { SchemaForm } from '../components/schema-form.js';
import { act, tryAct, useTopic } from '../lib/store.js';
import { useRpc, useDebouncedCallback } from '../lib/hooks.js';
import { RISKS, VERDICTS, PLUGIN_STATUS, PROVIDER_GROUPS, providerGroup, fmtDuration } from '../lib/format.js';

const GROUP_ORDER = ['builtin', 'plugin', 'device', 'client'];

/** @param {{cap: any, rule: string|undefined, onPolicy: (v: string|null) => void}} p */
function CapCard({ cap, rule, onPolicy }) {
  const risk = RISKS[cap.risk] || RISKS.read;
  const st = cap.stats || {};
  const avg = st.calls ? st.total_ms / st.calls : null;
  const value = rule || 'default';
  return html`<article class=${cx('cap', `risk-${cap.risk}`, !cap.available && 'is-unavailable')}>
    <header class="cap-head">
      <div class="cap-titles"><strong>${cap.title}</strong><code>${cap.name}</code></div>
      <div class="cap-badges">
        <${Badge} tone=${risk.tone} title=${risk.hint}>${risk.label}<//>
        ${cap.owner_only ? html`<${Badge} tone="violet" icon="lock" title="只在与主人的对话中提供，群聊和他人私聊里永远看不到">仅主人会话<//>` : null}
        ${!cap.available ? html`<${Badge} tone="muted">不可用<//>` : null}
      </div>
    </header>
    <p class="cap-desc">${cap.description}</p>
    <footer class="cap-foot">
      <${Segmented} size="sm" label=${'策略：' + cap.name} value=${value} onChange=${(v) => onPolicy(v === 'default' ? null : v)}
        options=${[{ value: 'default', label: `默认·${VERDICTS[cap.policy]?.label || cap.policy}`, title: '按风险等级与通配规则决定' },
          { value: 'allow', label: '允许', tone: 'ok' }, { value: 'ask', label: '询问', tone: 'warn' }, { value: 'deny', label: '禁止', tone: 'danger' }]} />
      <span class="cap-stats">${st.calls ? html`${st.calls} 次${st.errors ? html` · <span class="tone-text-danger">${st.errors} 错</span>` : ''} · 平均 ${fmtDuration(avg)}` : html`<span class="muted">未调用</span>`}</span>
    </footer>
  </article>`;
}

function Rules({ rules, onSet }) {
  const globs = Object.entries(rules || {}).filter(([k]) => /[*?]/.test(k));
  const [pattern, setPattern] = useState('');
  const [verdict, setVerdict] = useState('ask');
  return html`<${Card} title="通配规则" icon="filter" subtitle="按名字模式批量设定，例如 device.* 全部询问、web.* 全部允许。越具体的规则优先。">
    ${globs.length ? html`<ul class="rule-list">${globs.map(([k, v]) => html`<li key=${k}><code>${k}</code><${Badge} tone=${VERDICTS[v]?.tone}>${VERDICTS[v]?.label || v}<//>
      <${Button} size="sm" variant="ghost" icon="trash" aria-label=${'删除规则 ' + k} onClick=${() => onSet(k, null)} /></li>`)}</ul>` : html`<p class="muted small">还没有通配规则。</p>`}
    <form class="inline-form" onSubmit=${(e) => { e.preventDefault(); if (pattern.trim()) { onSet(pattern.trim(), verdict); setPattern(''); } }}>
      <input class="input mono" value=${pattern} placeholder="例如 device.*" aria-label="能力名模式" onInput=${(e) => setPattern(e.currentTarget.value)} />
      <${Segmented} size="sm" label="规则结论" value=${verdict} onChange=${setVerdict} options=${Object.entries(VERDICTS).map(([k, x]) => ({ value: k, label: x.label, tone: x.tone }))} />
      <${Button} variant="primary" size="sm" icon="plus" disabled=${!pattern.trim()} onClick=${() => { if (pattern.trim()) { onSet(pattern.trim(), verdict); setPattern(''); } }}>添加<//>
    </form>
  <//>`;
}

/** @param {{p: any, onChanged: () => void}} p */
function PluginCard({ p, onChanged }) {
  const st = PLUGIN_STATUS[p.status] || { label: p.status, tone: 'muted' };
  const needs = p.status === 'needs_config' || Object.values(p.secrets || {}).some((v) => !v);
  const [open, setOpen] = useState(false);
  const hasForm = Object.keys(p.config_schema || {}).length || Object.keys(p.secrets_schema || {}).length;
  return html`<article class=${cx('plugin', `is-${p.status}`)}>
    <header class="plugin-head">
      <span class="plugin-icon" aria-hidden="true">${p.icon || '🧩'}</span>
      <div class="plugin-titles"><strong>${p.name}</strong><span class="muted small">v${p.version} · ${p.builtin ? '内置插件' : '用户插件'}</span></div>
      <${Badge} tone=${st.tone} dot=${p.status === 'active'}>${st.label}<//>
      <${Switch} checked=${p.enabled} label=${(p.enabled ? '停用 ' : '启用 ') + p.name}
        onChange=${async (v) => { await tryAct('plugins.set_enabled', { id: p.id, enabled: v }, { ok: v ? `已启用 ${p.name}` : `已停用 ${p.name}` }); onChanged(); }} />
    </header>
    <p class="plugin-desc">${p.description}</p>
    ${p.error ? html`<p class="step-err"><${Icon} name="alert" size=${14} /><code>${p.error}</code></p>` : null}
    ${p.capabilities?.length ? html`<div class="chips">${p.capabilities.map((c) => html`<${Chip} key=${c}><code>${c}</code><//>`)}</div>` : null}
    ${hasForm ? html`<div class="plugin-config">
      <button type="button" class=${cx('linkish', needs && 'tone-text-warn')} aria-expanded=${open ? 'true' : 'false'} onClick=${() => setOpen(!open)}>
        <${Icon} name="gear" size=${14} />${open ? '收起配置' : needs ? '需要配置' : '配置'}</button>
      ${open ? html`<${SchemaForm} schema=${p.config_schema} secretsSchema=${p.secrets_schema} secretsSet=${p.secrets} value=${p.config}
        onSave=${async (config, secrets) => { await act('plugins.configure', { id: p.id, config, secrets }, { ok: '配置已保存' + (p.enabled ? '，插件已重新加载' : '') }); onChanged(); }} />` : null}
    </div>` : null}
  </article>`;
}

export function CapabilitiesPage() {
  const caps = useRpc('capabilities.list');
  const plugins = useRpc('plugins.list');
  const [q, setQ] = useState('');
  const [risk, setRisk] = useState('');
  const refresh = useDebouncedCallback(() => { caps.reload(true); plugins.reload(true); }, 500);
  useTopic(['capability.*', 'plugin.changed', 'config.changed'], refresh);

  const rules = caps.data?.rules || {};
  const setPolicy = async (pattern, verdict) => {
    const res = await tryAct('policy.set', { pattern, verdict }, { ok: verdict ? `「${pattern}」→ ${VERDICTS[verdict].label}` : `已恢复「${pattern}」的默认策略` });
    if (res) caps.reload(true);
  };
  const groups = useMemo(() => {
    const t = q.trim().toLowerCase();
    const out = {};
    for (const c of caps.data?.capabilities || []) {
      if (risk && c.risk !== risk) continue;
      if (t && !(c.name + c.title + c.description + c.group).toLowerCase().includes(t)) continue;
      const g = providerGroup(c.provider);
      out[g] = out[g] || {};
      const sub = g === 'builtin' ? (c.group || 'builtin') : (c.group || c.provider);
      (out[g][sub] = out[g][sub] || []).push(c);
    }
    return out;
  }, [caps.data, q, risk]);
  const keys = [...GROUP_ORDER.filter((g) => groups[g]), ...Object.keys(groups).filter((g) => !GROUP_ORDER.includes(g))];

  return html`<div class="page capabilities">
    <${PageHead} title="能力" icon="bolt" subtitle="鲸鱼能调用的一切：内置工具、插件动作、手机身体、客户端声明的能力，都在同一个权限模型下" />
    <div class="caps-legend">
      ${Object.entries(RISKS).map(([k, r]) => html`<span key=${k} class="legend-item"><${Badge} tone=${r.tone}>${r.label}<//><span class="muted small">${r.hint}</span></span>`)}
    </div>
    <div class="toolbar">
      <div class="search-box"><${Icon} name="search" size=${16} /><input type="search" value=${q} placeholder="搜索能力" aria-label="搜索能力" onInput=${(e) => setQ(e.currentTarget.value)} /></div>
      <div class="chips">${[['', '全部'], ...Object.entries(RISKS).map(([k, r]) => [k, r.label])].map(([k, l]) => html`<${Chip} key=${k} active=${risk === k} onClick=${() => setRisk(k)}>${l}<//>`)}</div>
    </div>
    ${caps.error ? html`<${ErrorBox} error=${caps.error} onRetry=${() => caps.reload()} />` : null}
    ${!caps.data ? html`<${Skeleton} rows=${6} height=${60} />` : !keys.length ? html`<${Empty} icon="bolt" title="没有匹配的能力" compact />` : keys.map((g) => html`<section key=${g} class="cap-group">
      <header class="cap-group-head"><h2>${PROVIDER_GROUPS[g]?.label || g}</h2><span class="muted small">${PROVIDER_GROUPS[g]?.hint || ''}</span></header>
      ${Object.entries(groups[g]).map(([sub, list]) => html`<div key=${sub} class="cap-sub">
        <h3 class="cap-sub-title">${sub}<span class="muted"> · ${list.length}</span></h3>
        <div class="cap-grid">${list.map((c) => html`<${CapCard} key=${c.name} cap=${c} rule=${rules[c.name]} onPolicy=${(v) => setPolicy(c.name, v)} />`)}</div>
      </div>`)}
    </section>`)}
    <${Rules} rules=${rules} onSet=${setPolicy} />
    <section class="cap-group">
      <header class="cap-group-head"><h2>插件</h2><span class="muted small">一组能力 + 可选的生命周期。启用/停用即时生效。</span>
        <${AsyncButton} size="sm" variant="ghost" icon="refresh" onClick=${() => plugins.reload()}>刷新<//></header>
      ${plugins.error ? html`<${ErrorBox} error=${plugins.error} onRetry=${() => plugins.reload()} />` : null}
      ${!plugins.data ? html`<${Skeleton} rows=${3} height=${60} />` : plugins.data.plugins.length ? html`<div class="plugin-grid">${plugins.data.plugins.map((p) => html`<${PluginCard} key=${p.id} p=${p} onChanged=${() => { plugins.reload(true); caps.reload(true); }} />`)}</div>`
        : html`<${Empty} icon="plug" title="没有发现插件" hint="把插件目录放到 <数据目录>/plugins/ 下即可被发现。" compact />`}
      <${Disclosure} title="如何编写插件"><p class="muted small">一个插件 = 目录里的 <code>plugin.json</code>（id、name、config、secrets）+ <code>plugin.py</code> 的 <code>setup(p)</code>，用 <code>@p.capability(...)</code> 注册能力。配置项会自动生成上面的表单。</p><//>
    </section>
  </div>`;
}
