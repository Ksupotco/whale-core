// @ts-check
/** Privacy panel for a personal phone: what is never kept, what you excluded, what was filtered. */
import { html, useEffect, useState } from '/vendor/preact-htm.js';
import { Icon } from './icons.js';
import { Card, Disclosure, Field, Switch, TagEditor, Skeleton, ErrorBox, AsyncButton, Chip } from './ui.js';
import { act, useTopic } from '../lib/store.js';
import { useRpc, useDebouncedCallback } from '../lib/hooks.js';
import { FILTER_REASONS } from '../lib/format.js';

const APP_RE = /^[A-Za-z0-9_.]{2,128}$/;
const DOMAIN_RE = /^(\*\.)?[a-z0-9.-]{2,253}$/i;

/** @param {{id: string}} p */
export function PrivacyPanel({ id }) {
  const res = useRpc('devices.privacy', { id }, [id]);
  const [draft, setDraft] = useState(/** @type {any} */ (null));
  useEffect(() => { if (res.data) setDraft(res.data.settings); }, [res.data]);
  const refresh = useDebouncedCallback(() => res.reload(true), 800);
  useTopic('device.event_filtered', (ev) => { if (ev.data.device_id === id) refresh(); });
  if (res.error) return html`<${ErrorBox} error=${res.error} onRetry=${() => res.reload()} />`;
  if (!res.data || !draft) return html`<${Skeleton} rows=${4} />`;
  const { builtin, filtered } = res.data;
  const saved = res.data.settings;
  const dirty = JSON.stringify(saved) !== JSON.stringify(draft);
  const save = async (patch) => {
    const out = await act('devices.set_privacy', { id, ...patch }, { ok: '隐私设置已保存' });
    if (out?.settings) setDraft(out.settings);
    res.reload(true);
  };
  const total = Object.values(filtered || {}).reduce((a, b) => a + Number(b || 0), 0);
  return html`<${Card} title="隐私" icon="lock" class="privacy" subtitle="主人日常手机：默认都记录，但下面这些永远不会被记住。过滤发生在入库那一刻，被过滤的内容只留下无内容的墓碑。">
    <div class="privacy-filtered">
      <div class="privacy-total"><strong>${total}</strong><span>条经历被过滤</span></div>
      <ul class="privacy-reasons">${Object.keys(FILTER_REASONS).filter((k) => k !== 'other' || filtered?.other).map((k) => html`<li key=${k} class=${filtered?.[k] ? '' : 'is-zero'}>
        <span>${FILTER_REASONS[k]}</span><strong>${filtered?.[k] || 0}</strong></li>`)}</ul>
    </div>
    <div class="form-grid">
      <${Field} label="排除的应用" hint="填写 Android 包名，例如 com.bank.app。这些应用里的一切都不会被记录。">
        <${TagEditor} mono value=${draft.excluded_apps || []} placeholder="com.example.app" validate=${(t) => APP_RE.test(t)} label="排除的应用" onChange=${(v) => setDraft({ ...draft, excluded_apps: v })} /><//>
      <${Field} label="排除的网站" hint="精确域名，或用 *.example.com 包含所有子域名。">
        <${TagEditor} mono value=${draft.excluded_domains || []} placeholder="*.bank.com" validate=${(t) => DOMAIN_RE.test(t)} label="排除的网站" onChange=${(v) => setDraft({ ...draft, excluded_domains: v.map((x) => x.toLowerCase()) })} /><//>
    </div>
    ${dirty ? html`<div class="form-actions"><${AsyncButton} variant="primary" icon="check" onClick=${() => save({ excluded_apps: draft.excluded_apps, excluded_domains: draft.excluded_domains })}>保存排除列表<//></div>` : null}
    <div class="switch-rows">
      <div class="switch-row"><div><strong>隐藏敏感数字</strong><p class="muted small">保留的文字里，验证码、银行卡号、身份证号会被打码。</p></div>
        <${Switch} checked=${!!draft.redact_secrets} label="隐藏敏感数字" onChange=${(v) => save({ redact_secrets: v })} /></div>
      <div class="switch-row"><div><strong>丢弃手机标记为敏感的内容</strong><p class="muted small">手机 App 自己判定为 owner_sensitive 的经历直接丢弃。</p></div>
        <${Switch} checked=${!!draft.drop_sensitive} label="丢弃敏感内容" onChange=${(v) => save({ drop_sensitive: v })} /></div>
    </div>
    <${Disclosure} title="内置规则（始终生效，不可关闭）" hint=${`${builtin.system_packages?.length || 0} 个系统应用 · ${builtin.ime_patterns?.length || 0} 条输入法规则`}>
      <p class="muted small"><${Icon} name="shield" size=${13} />锁屏、通知栏、输入法（它们显示你正在输入的内容，包括密码）、密码框与安全窗口、权限与凭据对话框、密码管理器和验证器，永远不会被记录。</p>
      <h5>屏幕标记</h5><div class="chips">${(builtin.screen_flags || []).map((f) => html`<${Chip} key=${f}><code>${f}</code><//>`)}</div>
      <h5>输入法匹配</h5><div class="chips">${(builtin.ime_patterns || []).map((f) => html`<${Chip} key=${f}><code>${f}</code><//>`)}</div>
      <h5>打码</h5><div class="chips">${(builtin.redaction || []).map((f) => html`<${Chip} key=${f}>${f}<//>`)}</div>
      <h5>系统隐私应用</h5><div class="chips chips-dense">${(builtin.system_packages || []).map((f) => html`<${Chip} key=${f}><code>${f}</code><//>`)}</div>
    <//>
  <//>`;
}
