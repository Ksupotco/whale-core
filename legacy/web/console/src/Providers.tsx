import { useCallback, useEffect, useState } from 'react';
import { Cloud, Plus, Server, Trash2 } from 'lucide-react';
import {
  createProvider,
  deleteProvider,
  fetchProviderModels,
  fetchProviders,
  testProviderConn,
  updateProvider,
  type Provider,
} from './api';
import type { Connection } from './api';
import {
  Banner,
  Button,
  CheckRow,
  Field,
  FocusCardTitle,
  FocusGrid,
  Input,
  Switch,
  Tag,
} from './ds';
import { Empty, Modal, PageHeading } from './ui';
import { useWorkspace } from './workspace';

function host(url: string) {
  try {
    return new URL(url).host;
  } catch {
    return url || '—';
  }
}

function ProviderCard({
  provider,
  focused,
  connection,
  onChanged,
  onDelete,
  notify,
}: {
  provider: Provider;
  focused: boolean;
  connection: Connection;
  onChanged: () => Promise<void>;
  onDelete: (p: Provider) => void;
  notify: (t: string, e?: boolean) => void;
}) {
  const [name, setName] = useState(provider.name);
  const [baseUrl, setBaseUrl] = useState(provider.base_url);
  const [apiKey, setApiKey] = useState('');
  const [clearKey, setClearKey] = useState(false);
  const [enabled, setEnabled] = useState(provider.enabled);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [testMsg, setTestMsg] = useState('');
  const [models, setModels] = useState<{ list: string[]; cached: boolean } | null>(null);
  const [modelsLoading, setModelsLoading] = useState(false);

  useEffect(() => {
    setName(provider.name);
    setBaseUrl(provider.base_url);
    setApiKey('');
    setClearKey(false);
    setEnabled(provider.enabled);
    setError('');
    setTestMsg('');
    setModels(null);
  }, [provider]);

  if (!focused) {
    return (
      <div className="model-compact">
        <span className="model-compact-row">
          <em>端点</em>
          {host(provider.base_url)}
        </span>
        <span className="model-compact-row">
          <em>密钥</em>
          {provider.api_key_configured ? '已配置' : '未配置'}
        </span>
      </div>
    );
  }

  const dirty =
    name !== provider.name ||
    baseUrl !== provider.base_url ||
    apiKey !== '' ||
    clearKey ||
    enabled !== provider.enabled;

  const save = async () => {
    setBusy('save');
    setError('');
    try {
      await updateProvider(connection, provider.id, {
        ...(name !== provider.name ? { name: name.trim() } : {}),
        ...(baseUrl !== provider.base_url ? { base_url: baseUrl.trim() } : {}),
        ...(clearKey ? { api_key: null } : apiKey.trim() ? { api_key: apiKey.trim() } : {}),
        ...(enabled !== provider.enabled ? { enabled } : {}),
      });
      await onChanged();
      notify('供应商配置已保存。');
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy('');
    }
  };
  const test = async () => {
    setBusy('test');
    setTestMsg('');
    try {
      const r = await testProviderConn(connection, provider.id);
      setTestMsg(
        r.ok
          ? `连接成功 · ${r.latency_ms} ms${r.models !== undefined ? ` · ${r.models} 个模型` : ''}`
          : `连接失败：${r.error_kind || '无有效回复'}（请检查端点与密钥）`,
      );
    } catch (e) {
      setTestMsg(`连接失败：${(e as Error).message}`);
    } finally {
      setBusy('');
    }
  };
  const loadModels = async () => {
    setModelsLoading(true);
    setError('');
    try {
      const r = await fetchProviderModels(connection, provider.id);
      setModels({ list: r.models, cached: r.cached });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setModelsLoading(false);
    }
  };

  return (
    <div className="model-form">
      <div className="model-form-grid">
        <Field label="供应商名称">
          <Input aria-label="供应商名称" value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="Base URL" hint="OpenAI 兼容服务的基础地址。">
          <Input aria-label="Base URL" type="url" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
        </Field>
      </div>
      <Field
        label={
          <>
            API 密钥{' '}
            <Tag tone={provider.api_key_configured ? 'mint' : 'slate'}>
              {provider.api_key_configured ? '已配置' : '未配置'}
            </Tag>
          </>
        }
        hint="密钥只进不出：保存后不回显；留空保持原值。"
      >
        <Input
          aria-label="API 密钥"
          type="password"
          autoComplete="new-password"
          value={apiKey}
          disabled={clearKey}
          placeholder={provider.api_key_configured ? '留空保留已配置的密钥' : '输入 API Key'}
          onChange={(e) => setApiKey(e.target.value)}
        />
      </Field>
      {provider.api_key_configured && (
        <CheckRow
          label="保存时清除已配置的密钥"
          checked={clearKey}
          onChange={(v) => {
            setClearKey(v);
            if (v) setApiKey('');
          }}
        />
      )}
      <div className="model-intent-toggle">
        <div>
          <strong>启用供应商</strong>
          <small>停用后模型配置不再选用它（已有引用需先调整）。</small>
        </div>
        <Switch label="启用供应商" checked={enabled} onChange={setEnabled} />
      </div>
      {error && <Banner tone="error">{error}</Banner>}
      {testMsg && (
        <Banner tone={testMsg.startsWith('连接成功') ? 'ok' : 'error'}>{testMsg}</Banner>
      )}
      {models && (
        <div className="provider-models">
          <strong className="muted small-text">
            可用模型（{models.list.length}）{models.cached ? '· 缓存' : '· 实时'}
          </strong>
          <div className="provider-model-tags">
            {models.list.map((m) => (
              <Tag tone="violet" key={m}>
                {m}
              </Tag>
            ))}
            {!models.list.length && <span className="muted small-text">供应商未返回模型清单。</span>}
          </div>
        </div>
      )}
      <div className="model-form-actions">
        <Button variant="secondary" size="sm" busy={busy === 'test'} onClick={test}>
          连接测试
        </Button>
        <Button variant="secondary" size="sm" busy={modelsLoading} onClick={loadModels}>
          获取模型列表
        </Button>
        <span className="model-form-gap" />
        <Button variant="ghost" size="sm" icon={<Trash2 size={13} />} onClick={() => onDelete(provider)}>
          删除
        </Button>
        <Button variant="primary" size="sm" busy={busy === 'save'} disabled={!dirty} onClick={save}>
          保存配置
        </Button>
      </div>
    </div>
  );
}

export default function ProvidersPage() {
  const { connection, notify } = useWorkspace();
  const [providers, setProviders] = useState<Provider[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [focused, setFocused] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState<Provider | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [form, setForm] = useState({ name: '', base_url: '', api_key: '' });

  const load = useCallback(async () => {
    if (!connection) return;
    setLoading(true);
    setError('');
    try {
      setProviders(await fetchProviders(connection));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [connection]);
  useEffect(() => {
    void load();
  }, [load]);

  if (!connection) return null;
  const c = connection;
  return (
    <div className="page-enter">
      <PageHeading
        page="providers"
        action={
          <Button
            variant="primary"
            size="sm"
            icon={<Plus size={14} />}
            onClick={() => {
              setForm({ name: '', base_url: '', api_key: '' });
              setCreating(true);
            }}
          >
            新增供应商
          </Button>
        }
      />
      <Banner tone="info">
        密钥只进不出：服务端保存、视图不回显；模型列表由服务端代取（15s 超时 + 60s 缓存）。
      </Banner>
      {error && <Banner tone="error">{error}</Banner>}
      {loading && <p className="muted padded">正在读取供应商…</p>}
      {!loading && !providers.length && (
        <Empty title="还没有供应商" text="新增一个 OpenAI 兼容供应商，供模型配置选用。" />
      )}
      {providers.length > 0 && (
        <FocusGrid
          focused={focused}
          onFocus={setFocused}
          items={providers.map((p) => ({
            key: p.id,
            ariaLabel: p.name,
            head: () => (
              <FocusCardTitle
                icon={p.base_url.includes('localhost') || p.base_url.includes('127.0.0.1') ? <Server size={19} /> : <Cloud size={19} />}
                name={p.name}
                desc={host(p.base_url)}
                aside={
                  <Tag tone={p.api_key_configured ? 'mint' : 'slate'} dot>
                    {p.api_key_configured ? '已配置密钥' : '未配置密钥'}
                  </Tag>
                }
              />
            ),
            body: (f) => (
              <ProviderCard
                provider={p}
                focused={f}
                connection={c}
                notify={notify}
                onChanged={load}
                onDelete={setDeleting}
              />
            ),
          }))}
        />
      )}

      {creating && (
        <Modal title="新增供应商" onClose={() => setCreating(false)}>
          <div className="model-form">
            <Field label="供应商名称" hint="例如：DeepSeek、本地 vLLM。">
              <Input
                aria-label="新供应商名称"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
              />
            </Field>
            <Field label="Base URL">
              <Input
                aria-label="新供应商 Base URL"
                type="url"
                value={form.base_url}
                placeholder="https://api.deepseek.com"
                onChange={(e) => setForm({ ...form, base_url: e.target.value })}
              />
            </Field>
            <Field label="API 密钥" hint="可留空稍后配置；只进不出。">
              <Input
                aria-label="新供应商 API 密钥"
                type="password"
                autoComplete="new-password"
                value={form.api_key}
                onChange={(e) => setForm({ ...form, api_key: e.target.value })}
              />
            </Field>
            <div className="model-form-actions">
              <Button variant="ghost" size="sm" onClick={() => setCreating(false)}>
                取消
              </Button>
              <span className="model-form-gap" />
              <Button
                variant="primary"
                size="sm"
                busy={deleteBusy}
                disabled={!form.name.trim() || !form.base_url.trim()}
                onClick={async () => {
                  setDeleteBusy(true);
                  try {
                    const created = await createProvider(c, {
                      name: form.name.trim(),
                      base_url: form.base_url.trim(),
                      ...(form.api_key.trim() ? { api_key: form.api_key.trim() } : {}),
                    });
                    setCreating(false);
                    await load();
                    setFocused(created.id);
                    notify(`供应商「${created.name}」已创建。`);
                  } catch (e) {
                    notify((e as Error).message, true);
                  } finally {
                    setDeleteBusy(false);
                  }
                }}
              >
                创建
              </Button>
            </div>
          </div>
        </Modal>
      )}

      {deleting && (
        <Modal title="删除供应商" onClose={() => !deleteBusy && setDeleting(null)}>
          <p className="detail-text">
            确定删除供应商「{deleting.name}」吗？被模型注册表引用的供应商无法删除。
          </p>
          <div className="form-actions">
            <Button variant="ghost" size="sm" disabled={deleteBusy} onClick={() => setDeleting(null)}>
              取消
            </Button>
            <Button
              variant="danger"
              size="sm"
              busy={deleteBusy}
              onClick={async () => {
                setDeleteBusy(true);
                try {
                  await deleteProvider(c, deleting.id);
                  setDeleting(null);
                  setFocused(null);
                  await load();
                  notify('供应商已删除。');
                } catch (e) {
                  notify((e as Error).message, true);
                } finally {
                  setDeleteBusy(false);
                }
              }}
            >
              确认删除
            </Button>
          </div>
        </Modal>
      )}
    </div>
  );
}
