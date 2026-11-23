import { useCallback, useEffect, useState } from 'react';
import {
  AudioLines,
  BookOpen,
  CalendarDays,
  Compass,
  Database,
  File,
  Globe2,
  Image,
  LoaderCircle,
  MessageSquare,
  Plus,
  Puzzle,
  Search,
  Trash2,
} from 'lucide-react';
import {
  fetchPlugin,
  pluginHealth,
  pluginTest,
  request,
  setPluginConfig,
} from './api';
import type { Connection } from './api';
import type { Plugin } from './data';
import { Banner, Button, FocusCardTitle, FocusGrid, Input, Switch, Tag } from './ds';
import { Empty, PageHeading, SearchField } from './ui';
import { useWorkspace } from './workspace';

const pluginIcons: Record<string, typeof Puzzle> = {
  search: Globe2,
  memory: Database,
  read: BookOpen,
  workflow: Compass,
  voice: AudioLines,
  file: File,
  calendar: CalendarDays,
  recall: Search,
  weather: Compass,
  image: Image,
  chat: MessageSquare,
};

type KvRow = { key: string; value: string; masked: boolean };
function configToRows(config: Record<string, unknown>): KvRow[] {
  return Object.entries(config).map(([key, value]) => ({
    key,
    value: value === '***' ? '' : typeof value === 'string' ? value : JSON.stringify(value),
    masked: value === '***',
  }));
}
function parseValue(raw: string): unknown {
  const t = raw.trim();
  if (t === '') return '';
  if (t === 'true') return true;
  if (t === 'false') return false;
  if (t === 'null') return null;
  if (/^-?\d+(\.\d+)?$/.test(t)) return Number(t);
  if ((t.startsWith('{') && t.endsWith('}')) || (t.startsWith('[') && t.endsWith(']'))) {
    try {
      return JSON.parse(t);
    } catch {
      return raw;
    }
  }
  return raw;
}

/** 配置编辑面板（脱敏视图 + 补丁式保存：未动的掩码密钥不替换、null 删除移除键）。 */
function ConfigPanel({
  pluginId,
  connection,
  onSaved,
}: {
  pluginId: string;
  connection: Connection;
  onSaved: () => void;
}) {
  const [rows, setRows] = useState<KvRow[] | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    try {
      const detail = await fetchPlugin(connection, pluginId);
      setRows(configToRows(detail.config ?? {}));
      setError('');
    } catch (e) {
      setError((e as Error).message);
    }
  }, [connection, pluginId]);
  useEffect(() => {
    void load();
  }, [load]);

  const submit = async () => {
    if (!rows) return;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const original = (await fetchPlugin(connection, pluginId)).config ?? {};
      const originalRows = new Map(configToRows(original).map((r) => [r.key, r]));
      const currentKeys = new Set<string>();
      const config: Record<string, unknown> = Object.create(null);
      for (const row of rows) {
        const key = row.key.trim();
        if (!key) continue;
        if (currentKeys.has(key)) throw new Error(`配置字段重复：${key}`);
        currentKeys.add(key);
        if (row.masked && row.value === '') continue; // 掩码且未动 → 保持
        const oldRow = originalRows.get(key);
        if (oldRow && oldRow.value === row.value && oldRow.masked === row.masked) continue;
        config[key] = parseValue(row.value);
      }
      for (const key of Object.keys(original)) {
        if (!currentKeys.has(key)) config[key] = null;
      }
      if (Object.keys(config).length === 0) {
        setNotice('配置没有改动。');
        return;
      }
      await setPluginConfig(connection, pluginId, config);
      setNotice('配置已保存；未修改的敏感字段保持原值。');
      await load();
      onSaved();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (error && !rows) return <Banner tone="error">{error}</Banner>;
  if (!rows) {
    return (
      <p className="muted small-text">
        <LoaderCircle size={13} className="spin" /> 正在读取配置…
      </p>
    );
  }
  return (
    <div className="config-panel">
      {rows.map((row, i) => (
        <div className="config-row" key={i}>
          <Input
            aria-label={`配置键 ${i + 1}`}
            value={row.key}
            placeholder="字段名"
            onChange={(e) =>
              setRows(rows.map((r, j) => (j === i ? { ...r, key: e.target.value } : r)))
            }
          />
          <Input
            aria-label={`配置值 ${row.key || i + 1}`}
            value={row.value}
            placeholder={row.masked ? '***（已配置，留空保持）' : '值'}
            type={row.masked ? 'password' : 'text'}
            autoComplete="off"
            onChange={(e) =>
              setRows(rows.map((r, j) => (j === i ? { ...r, value: e.target.value } : r)))
            }
          />
          <Button
            variant="ghost"
            size="sm"
            icon={<Trash2 size={13} />}
            aria-label={`删除配置 ${row.key}`}
            onClick={() => setRows(rows.filter((_, j) => j !== i))}
          />
        </div>
      ))}
      {error && <Banner tone="error">{error}</Banner>}
      {notice && <Banner tone="ok">{notice}</Banner>}
      <div className="model-form-actions">
        <Button
          variant="ghost"
          size="sm"
          icon={<Plus size={13} />}
          onClick={() => setRows([...rows, { key: '', value: '', masked: false }])}
        >
          添加字段
        </Button>
        <span className="model-form-gap" />
        <Button variant="primary" size="sm" busy={busy} onClick={submit}>
          保存配置
        </Button>
      </div>
    </div>
  );
}

function PluginCard({
  plugin,
  focused,
  connection,
  notify,
  onChanged,
}: {
  plugin: Plugin;
  focused: boolean;
  connection: Connection;
  notify: (t: string, e?: boolean) => void;
  onChanged: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [health, setHealth] = useState<{ status: string; detail?: string } | null>(null);
  const [testMsg, setTestMsg] = useState('');
  const toggle = async () => {
    setBusy(true);
    try {
      await request(
        connection,
        `/plugins/${encodeURIComponent(plugin.id)}/${plugin.enabled ? 'disable' : 'enable'}`,
        'POST',
      );
      await onChanged();
      notify(`${plugin.name}已${plugin.enabled ? '停用' : '启用'}。`);
    } catch (e) {
      notify((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => {
    if (!focused) return;
    pluginHealth(connection, plugin.id)
      .then(setHealth)
      .catch(() => setHealth(null));
  }, [focused, connection, plugin.id]);
  if (!focused) {
    return (
      <div className="model-compact">
        <span className="model-compact-row">
          <em>版本</em>v{plugin.version}
        </span>
        <span className="model-compact-row">
          <em>类型</em>
          {plugin.kind}
        </span>
      </div>
    );
  }
  return (
    <div className="model-form">
      <p className="plugin-desc">{plugin.description}</p>
      {(plugin.inputs?.length || plugin.actions?.length || plugin.provides?.length) && (
        <div className="plugin-caps">
          {plugin.inputs?.map((x) => (
            <Tag tone="blue" key={`i-${x}`}>输入 {x}</Tag>
          ))}
          {plugin.actions?.map((x) => (
            <Tag tone="violet" key={`a-${x}`}>动作 {x}</Tag>
          ))}
          {plugin.provides?.map((x) => (
            <Tag tone="mint" key={`p-${x}`}>能力 {x}</Tag>
          ))}
        </div>
      )}
      <div className="model-intent-toggle">
        <div>
          <strong>启用插件</strong>
          <small>
            {health
              ? `健康：${health.status}${health.detail ? ` · ${health.detail}` : ''}`
              : '状态读取中…'}
          </small>
        </div>
        <Switch
          label={`${plugin.enabled ? '停用' : '启用'}${plugin.name}`}
          checked={plugin.enabled}
          disabled={busy}
          onChange={() => void toggle()}
        />
      </div>
      {testMsg && <Banner tone={testMsg.startsWith('失败') ? 'error' : 'ok'}>{testMsg}</Banner>}
      <div className="model-form-actions" style={{ borderTop: 0, paddingTop: 0 }}>
        <Button
          variant="secondary"
          size="sm"
          disabled={!plugin.enabled || busy}
          onClick={async () => {
            setBusy(true);
            setTestMsg('');
            try {
              const r = await pluginTest(connection, plugin.id);
              setTestMsg(
                r.ok
                  ? `测试通过 · ${r.latency_ms} ms${r.via ? ` · 经 ${r.via}` : ''}`
                  : `失败：${r.error_kind || '无有效回复'}`,
              );
            } catch (e) {
              setTestMsg(`失败：${(e as Error).message}`);
            } finally {
              setBusy(false);
            }
          }}
        >
          测试插件
        </Button>
      </div>
      <details className="model-advanced">
        <summary>插件配置</summary>
        <div className="model-advanced-body">
          <ConfigPanel pluginId={plugin.id} connection={connection} onSaved={onChanged} />
        </div>
      </details>
    </div>
  );
}

export default function PluginsPage() {
  const { data, connection, refresh, notify } = useWorkspace();
  const [q, setQ] = useState('');
  const [tab, setTab] = useState('全部插件');
  const [focused, setFocused] = useState<string | null>(null);
  if (!connection) return null;
  const items = data.plugins.filter(
    (p) =>
      (tab === '全部插件' || p.enabled === (tab === '已启用')) &&
      `${p.name} ${p.english}`.toLowerCase().includes(q.toLowerCase()),
  );
  return (
    <div className="page-enter">
      <PageHeading page="plugins" />
      <div className="plugin-toolbar">
        <div className="segment-control">
          {['全部插件', '已启用', '未启用'].map((v) => (
            <button key={v} className={tab === v ? 'active' : ''} onClick={() => setTab(v)}>
              {v}
            </button>
          ))}
        </div>
        <SearchField value={q} onChange={setQ} placeholder="搜索插件..." />
      </div>
      {!items.length && <Empty />}
      {items.length > 0 && (
        <FocusGrid
          focused={focused}
          onFocus={setFocused}
          items={items.map((p) => ({
            key: p.id,
            ariaLabel: p.name,
            head: () => (
              <FocusCardTitle
                icon={(() => {
                  const Icon = pluginIcons[p.kind] || Puzzle;
                  return <Icon size={19} />;
                })()}
                name={p.name}
                desc={p.english}
                aside={
                  <Tag tone={p.enabled ? 'mint' : 'slate'} dot>
                    {p.enabled ? '已启用' : '未启用'}
                  </Tag>
                }
              />
            ),
            body: (f) => (
              <PluginCard
                plugin={p}
                focused={f}
                connection={connection}
                notify={notify}
                onChanged={refresh}
              />
            ),
          }))}
        />
      )}
    </div>
  );
}
