import { useCallback, useEffect, useState } from 'react';
import { Compass, Cpu, ListTodo, Zap } from 'lucide-react';
import {
  fetchSettings,
  saveSettings,
  testProvider,
  type ModelSettings,
  type RoleModelConfig,
} from './api';
import {
  Banner,
  Button,
  CheckRow,
  Field,
  FocusCardTitle,
  FocusGrid,
  Input,
  Select,
  Switch,
  Tag,
} from './ds';
import type { Connection } from './api';
import { PageHeading } from './ui';
import { useWorkspace } from './workspace';

type RoleKey = 'reply' | 'intent' | 'executor';
type RoleDef = {
  key: RoleKey;
  name: string;
  desc: string;
  icon: typeof Zap;
};
const ROLES: RoleDef[] = [
  {
    key: 'reply',
    name: '回复模型',
    desc: '日常对话与任务的主回复脑',
    icon: Zap,
  },
  {
    key: 'intent',
    name: '意图模型',
    desc: '意图识别、路由与发送前审核（同一角色）',
    icon: Compass,
  },
  {
    key: 'executor',
    name: '任务执行模型',
    desc: '多轮工具循环的任务执行（部署侧 WHALE_EXECUTOR_ENABLED=1 启用）',
    icon: ListTodo,
  },
];

function endpointHost(url?: string) {
  if (!url) return '';
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

/** 单个角色卡的草稿与操作（在 ModelsPage 顶层以固定顺序调用，满足 Hooks 规则）。 */
function useRoleCard(
  def: RoleDef,
  settings: ModelSettings | null,
  connection: Connection | null,
  onSaved: (next: ModelSettings, message: string) => void,
) {
  const isMain = def.key === 'reply';
  const current: RoleModelConfig = isMain
    ? (settings?.model ?? { base_url: '', model: '' })
    : (settings?.models?.[def.key] ?? {});
  const [baseUrl, setBaseUrl] = useState(current.base_url ?? '');
  const [model, setModel] = useState(current.model ?? '');
  const [apiKey, setApiKey] = useState('');
  const [clearKey, setClearKey] = useState(false);
  const [profile, setProfile] = useState<'cloud' | 'local'>(settings?.profile ?? 'cloud');
  const [intentEnabled, setIntentEnabled] = useState(settings?.intent_model_enabled ?? false);
  const [busy, setBusy] = useState<'save' | 'test' | ''>('');
  const [error, setError] = useState('');
  const [testResult, setTestResult] = useState('');

  // 服务端数据变化（保存后回读/外部变更）时同步草稿
  useEffect(() => {
    setBaseUrl(current.base_url ?? '');
    setModel(current.model ?? '');
    setApiKey('');
    setClearKey(false);
    setProfile(settings?.profile ?? 'cloud');
    setIntentEnabled(settings?.intent_model_enabled ?? false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settings]);

  const dirty =
    baseUrl !== (current.base_url ?? '') ||
    model !== (current.model ?? '') ||
    apiKey !== '' ||
    clearKey ||
    (isMain && profile !== settings?.profile) ||
    (def.key === 'intent' && intentEnabled !== settings?.intent_model_enabled);

  const reset = () => {
    setBaseUrl(current.base_url ?? '');
    setModel(current.model ?? '');
    setApiKey('');
    setClearKey(false);
    setProfile(settings?.profile ?? 'cloud');
    setIntentEnabled(settings?.intent_model_enabled ?? false);
    setError('');
    setTestResult('');
  };

  const save = async () => {
    if (!connection) return;
    setBusy('save');
    setError('');
    setTestResult('');
    try {
      if (isMain && (!baseUrl.trim() || !model.trim()))
        throw new Error('主回复模型的端点与模型名不能为空。');
      const keyPart = clearKey
        ? { api_key: null as string | null }
        : apiKey.trim()
          ? { api_key: apiKey.trim() }
          : {};
      const patch: Parameters<typeof saveSettings>[1] = isMain
        ? { profile, model: { base_url: baseUrl.trim(), model: model.trim(), ...keyPart } }
        : {
            models: {
              [def.key]: {
                // 角色覆盖：留空项不写入补丁（保持现状；未配置角色跟随主模型）
                ...(baseUrl.trim() ? { base_url: baseUrl.trim() } : {}),
                ...(model.trim() ? { model: model.trim() } : {}),
                ...keyPart,
              },
            },
            ...(def.key === 'intent' ? { intent_model_enabled: intentEnabled } : {}),
          };
      onSaved(await saveSettings(connection, patch), `${def.name}配置已保存并生效。`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy('');
    }
  };

  const test = async () => {
    if (!connection) return;
    setBusy('test');
    setError('');
    setTestResult('');
    try {
      const r = await testProvider(connection);
      if (r.ok) setTestResult(`主模型连接成功 · ${r.latency_ms} ms`);
      else setError(`主模型连接失败：${r.error_kind || '无有效回复'}。请检查端点、模型名与密钥。`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy('');
    }
  };

  const head = (
    <FocusCardTitle
      icon={<def.icon size={19} />}
      name={def.name}
      desc={def.desc}
      aside={
        isMain ? (
          <Tag tone={current.api_key_configured || current.base_url ? 'mint' : 'amber'} dot>
            {current.api_key_configured || current.base_url ? '已配置' : '未配置'}
          </Tag>
        ) : current.model || current.base_url ? (
          <Tag tone="blue" dot>
            已覆盖
          </Tag>
        ) : (
          <Tag tone="slate">跟随主模型</Tag>
        )
      }
    />
  );

  const focusedBody = (
    <div className="model-form">
      {isMain && (
        <Field label="运行方式" hint="切换运行方式后，另一配置档的旧密钥不会沿用。">
          <Select
            aria-label="运行方式"
            value={profile}
            onChange={(e) => setProfile(e.target.value as 'cloud' | 'local')}
          >
            <option value="cloud">云端模型 / OpenAI 兼容服务</option>
            <option value="local">本地模型 / OpenAI 兼容服务</option>
          </Select>
        </Field>
      )}
      {isMain && settings && profile !== settings.profile && (
        <Banner tone="warn">
          切换运行方式后，另一配置档的旧密钥不会沿用。请核对端点、模型，并按需填写新的 API 密钥。
        </Banner>
      )}
      <div className="model-form-grid">
        <Field label="供应商端点" hint={isMain ? 'OpenAI 兼容服务的基础 URL。' : '留空跟随主模型端点。'}>
          <Input
            aria-label={`${def.name}供应商端点`}
            type="url"
            value={baseUrl}
            placeholder={isMain ? 'https://api.deepseek.com' : '留空跟随主模型'}
            onChange={(e) => setBaseUrl(e.target.value)}
          />
        </Field>
        <Field label="模型名" hint={isMain ? 'OpenAI 兼容模型 ID。' : '留空跟随主回复模型。'}>
          <Input
            aria-label={`${def.name}模型名`}
            value={model}
            placeholder={isMain ? 'deepseek-chat' : '留空跟随主模型'}
            onChange={(e) => setModel(e.target.value)}
          />
        </Field>
      </div>
      <Field
        label={
          <>
            API 密钥{' '}
            <Tag tone={current.api_key_configured ? 'mint' : 'slate'}>
              {current.api_key_configured ? '已配置' : '未配置'}
            </Tag>
          </>
        }
        hint="密钥保存后不回显。输入新密钥将替换原值；留空保持原值。"
      >
        <Input
          aria-label={`${def.name} API 密钥`}
          type="password"
          autoComplete="new-password"
          value={apiKey}
          disabled={clearKey}
          placeholder={current.api_key_configured ? '留空保留已配置的密钥' : '输入 API Key'}
          onChange={(e) => setApiKey(e.target.value)}
        />
      </Field>
      {current.api_key_configured ? (
        <CheckRow
          label="保存时清除已配置的密钥"
          checked={clearKey}
          onChange={(v) => {
            setClearKey(v);
            if (v) setApiKey('');
          }}
        />
      ) : null}
      {def.key === 'intent' && (
        <div className="model-intent-toggle">
          <div>
            <strong>启用模型意图规划</strong>
            <small>让模型辅助理解工具与复杂任务；会产生额外模型调用。</small>
          </div>
          <Switch label="启用模型意图规划" checked={intentEnabled} onChange={setIntentEnabled} />
        </div>
      )}
      {def.key === 'executor' && (
        <Banner tone="info">
          任务执行模型的生效还需部署侧设置 WHALE_EXECUTOR_ENABLED=1；此处配置其端点与模型覆盖。
        </Banner>
      )}
      {error && <Banner tone="error">{error}</Banner>}
      {testResult && <Banner tone="ok">{testResult}</Banner>}
      <div className="model-form-actions">
        {isMain && (
          <Button variant="secondary" size="sm" busy={busy === 'test'} onClick={test}>
            测试主模型连接
          </Button>
        )}
        <span className="model-form-gap" />
        <Button variant="ghost" size="sm" disabled={!dirty || busy !== ''} onClick={reset}>
          重置
        </Button>
        <Button variant="primary" size="sm" busy={busy === 'save'} disabled={!dirty} onClick={save}>
          保存配置
        </Button>
      </div>
    </div>
  );

  const compactBody = (
    <div className="model-compact">
      <span className="model-compact-row">
        <em>供应商</em>
        {endpointHost(current.base_url) || (isMain ? '未配置' : '跟随主模型')}
      </span>
      <span className="model-compact-row">
        <em>模型</em>
        {current.model || (isMain ? '未配置' : '跟随主模型')}
      </span>
    </div>
  );

  return { def, head, focusedBody, compactBody };
}

export default function ModelsPage() {
  const { connection, notify } = useWorkspace();
  const [settings, setSettings] = useState<ModelSettings | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [focused, setFocused] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!connection) return;
    setLoading(true);
    setError('');
    try {
      setSettings(await fetchSettings(connection));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [connection]);
  useEffect(() => {
    void load();
  }, [load]);
  const onSaved = useCallback(
    (next: ModelSettings, message: string) => {
      setSettings(next);
      notify(message);
    },
    [notify],
  );

  const reply = useRoleCard(ROLES[0], settings, connection, onSaved);
  const intent = useRoleCard(ROLES[1], settings, connection, onSaved);
  const executor = useRoleCard(ROLES[2], settings, connection, onSaved);
  const cards = [reply, intent, executor];

  return (
    <div className="page-enter">
      <PageHeading page="models" />
      <Banner tone="info">
        当前运行方式：{settings?.profile === 'local' ? '本地模型' : '云端模型'}
        。模型注册表（快/慢档与视觉角色）、独立供应商管理与逐角色测试为已裁决的后端扩展项，将逐条报批后开放。
      </Banner>
      {error && <Banner tone="error">{error}</Banner>}
      {loading && !settings && <p className="muted padded">正在读取模型配置…</p>}
      {settings && (
        <FocusGrid
          focused={focused}
          onFocus={setFocused}
          items={cards.map((card) => ({
            key: card.def.key,
            ariaLabel: card.def.name,
            head: () => card.head,
            body: (isFocused) => (isFocused ? card.focusedBody : card.compactBody),
          }))}
        />
      )}
      <p className="footnote">
        <Cpu size={13} />
        保存采用补丁合并：只写入本卡片修改的字段，其他配置（预算、人格禁忌、服务密钥等）不受影响。
      </p>
    </div>
  );
}
