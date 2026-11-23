// @ts-check
/**
 * Form generated from a plugin manifest: `config_schema` ({key: {type, title, description, default, enum}})
 * plus `secrets_schema` ({key: label}). Secrets are write-only: the server only says whether one is set.
 */
import { html, useState } from '/vendor/preact-htm.js';
import { Field, Switch, TagEditor, Segmented, AsyncButton, Badge } from './ui.js';

/**
 * @param {{schema: Record<string, any>, secretsSchema?: Record<string, string>, secretsSet?: Record<string, boolean>,
 *   value: Record<string, any>, onSave: (config: any, secrets: Record<string, string>) => Promise<any>, saveLabel?: string}} p
 */
export function SchemaForm({ schema, secretsSchema = {}, secretsSet = {}, value, onSave, saveLabel = '保存配置' }) {
  const initial = () => Object.fromEntries(Object.entries(schema || {}).map(([k, s]) => [k, value?.[k] ?? s.default ?? (s.type === 'array' ? [] : s.type === 'boolean' ? false : '')]));
  const [form, setForm] = useState(initial);
  const [secrets, setSecrets] = useState(/** @type {Record<string, string>} */ ({}));
  const [errors, setErrors] = useState(/** @type {Record<string, string>} */ ({}));
  const set = (k, v) => setForm({ ...form, [k]: v });
  const entries = Object.entries(schema || {});

  const submit = async () => {
    const out = {};
    const errs = {};
    for (const [k, s] of entries) {
      let v = form[k];
      if (s.type === 'integer' || s.type === 'number') {
        if (v === '' || v == null) { v = s.default ?? null; } else {
          const n = Number(v);
          if (Number.isNaN(n) || (s.type === 'integer' && !Number.isInteger(n))) { errs[k] = '请输入' + (s.type === 'integer' ? '整数' : '数字'); continue; }
          v = n;
        }
      }
      out[k] = v;
    }
    setErrors(errs);
    if (Object.keys(errs).length) return;
    const sec = Object.fromEntries(Object.entries(secrets).filter(([, v]) => v !== ''));
    await onSave(out, sec);
    setSecrets({});
  };

  return html`<form class="schema-form" onSubmit=${(e) => { e.preventDefault(); submit(); }}>
    ${entries.map(([k, s]) => {
      const label = s.title || k;
      let control;
      if (s.type === 'boolean') control = html`<${Switch} checked=${!!form[k]} label=${label} onChange=${(v) => set(k, v)} />`;
      else if (s.type === 'array') control = html`<${TagEditor} value=${Array.isArray(form[k]) ? form[k].map(String) : []} label=${label} onChange=${(v) => set(k, v)} />`;
      else if (Array.isArray(s.enum) && s.enum.length <= 4) control = html`<${Segmented} size="sm" label=${label} value=${form[k]} options=${s.enum.map((o) => ({ value: o, label: o }))} onChange=${(v) => set(k, v)} />`;
      else if (Array.isArray(s.enum)) control = html`<select class="input" value=${form[k]} aria-label=${label} onChange=${(e) => set(k, e.currentTarget.value)}>${s.enum.map((o) => html`<option key=${o} value=${o}>${o}</option>`)}</select>`;
      else control = html`<input class="input" type=${s.type === 'integer' || s.type === 'number' ? 'number' : 'text'} value=${form[k] ?? ''}
        placeholder=${s.default !== undefined && s.default !== '' ? String(s.default) : ''} onInput=${(e) => set(k, e.currentTarget.value)} />`;
      return html`<${Field} key=${k} label=${label} hint=${s.description} error=${errors[k]} inline=${s.type === 'boolean'}>${control}<//>`;
    })}
    ${Object.entries(secretsSchema || {}).map(([k, label]) => html`<${Field} key=${'s:' + k} label=${html`${label} ${secretsSet[k] ? html`<${Badge} tone="ok">已设置<//>` : html`<${Badge} tone="warn">未设置<//>`}`}
      hint=${secretsSet[k] ? '已加密保存在服务器上；留空表示不修改，输入新值将覆盖。' : '只会保存在服务器的 secrets.json，控制台永远不会再显示它。'}>
      <input class="input mono" type="password" autocomplete="new-password" value=${secrets[k] || ''} placeholder=${secretsSet[k] ? '••••••••（不修改）' : '粘贴密钥'}
        onInput=${(e) => setSecrets({ ...secrets, [k]: e.currentTarget.value })} />
    <//>`)}
    <div class="form-actions"><${AsyncButton} variant="primary" icon="check" onClick=${submit}>${saveLabel}<//></div>
  </form>`;
}
