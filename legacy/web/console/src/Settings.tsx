import { useEffect, useState } from 'react';
import {
  Activity,
  CircleCheck,
  Download,
  ExternalLink,
  Link2,
  LoaderCircle,
  Monitor,
  ShieldCheck,
  Unplug,
  X,
} from 'lucide-react';
import { fetchSettings, request, saveSettings, type ModelSettings } from './api';
import type { Connection } from './api';
import { download } from './data';
import { Banner, Button, CheckRow, Field, Input, Select, Switch, Tag, Textarea } from './ds';
import { PageHeading } from './ui';
import { useWorkspace } from './workspace';

// 扩展服务配置（与 classic 设置页同构，36 个 SERVICE_FIELDS 键分六组）
const SERVICES: { label: string; fields: [string, string][] }[] = [
  {
    label: '搜索',
    fields: [
      ['WHALE_SEARCH_PROVIDER', '搜索供应商（tavily / brave / ddg）'],
      ['TAVILY_API_KEY', 'Tavily API Key'],
      ['BRAVE_API_KEY', 'Brave API Key'],
    ],
  },
  {
    label: '向量记忆召回（可选）',
    fields: [
      ['WHALE_EMBEDDING_BASE_URL', 'Embedding API 端点'],
      ['WHALE_EMBEDDING_API_KEY', 'Embedding API Key'],
      ['WHALE_EMBEDDING_MODEL', 'Embedding 模型名称'],
    ],
  },
  {
    label: '图片生成',
    fields: [
      ['WHALE_IMAGE_BASE_URL', 'API 端点'],
      ['WHALE_IMAGE_API_KEY', 'API Key'],
      ['WHALE_IMAGE_MODEL', '模型名称'],
    ],
  },
  {
    label: '语音识别与合成',
    fields: [
      ['WHALE_SPEECH_BASE_URL', 'API 端点'],
      ['WHALE_SPEECH_API_KEY', 'API Key'],
      ['WHALE_ASR_MODEL', '识别模型'],
      ['WHALE_TTS_MODEL', '合成模型'],
      ['WHALE_TTS_VOICE', '音色'],
    ],
  },
  {
    label: '视频生成',
    fields: [
      ['WHALE_VIDEO_BASE_URL', 'API 端点'],
      ['WHALE_VIDEO_API_KEY', 'API Key'],
      ['WHALE_VIDEO_MODEL', '模型名称'],
      ['DASHSCOPE_API_KEY', 'DashScope API Key'],
    ],
  },
  {
    label: '实时语音',
    fields: [
      ['WHALE_REALTIME_URL', 'WebSocket 端点'],
      ['WHALE_REALTIME_API_KEY', 'API Key'],
      ['WHALE_REALTIME_MODEL', '模型名称'],
      ['WHALE_REALTIME_VOICE', '音色'],
      ['WHALE_REALTIME_PROTOCOL', '会话协议（ga / legacy）'],
    ],
  },
];

function RuntimeConfigSection({
  connection,
}: {
  connection: Connection;
}) {
  const [settings, setSettings] = useState<ModelSettings | null>(null);
  const [taboos, setTaboos] = useState('');
  const [budget, setBudget] = useState({ model_calls: 200, media_jobs: 50 });
  const [services, setServices] = useState<Record<string, string | null>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  useEffect(() => {
    fetchSettings(connection)
      .then((s) => {
        setSettings(s);
        setTaboos((s.persona_taboos ?? []).join('\n'));
        if (s.budget) setBudget({ ...s.budget.limits });
        setServices(
          Object.fromEntries(
            Object.entries(s.services ?? {}).map(([k, v]) => [
              k,
              typeof v === 'string' ? v : '',
            ]),
          ),
        );
      })
      .catch((e) => setError((e as Error).message));
  }, [connection]);

  if (error && !settings) return <Banner tone="error">{error}</Banner>;
  if (!settings)
    return (
      <p className="muted padded">
        <LoaderCircle size={14} className="spin" /> 正在读取运行配置…
      </p>
    );

  const save = async () => {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const validBudget = (n: number) => Number.isInteger(n) && n >= 0 && n <= 100000;
      if (!validBudget(budget.model_calls) || !validBudget(budget.media_jobs)) {
        setError('请求预算必须是 0–100000 的整数（0 = 关闭该能力）。');
        return;
      }
      await saveSettings(connection, {
        persona_taboos: taboos.split('\n').map((l) => l.trim()).filter(Boolean),
        budget: { model_calls: budget.model_calls, media_jobs: budget.media_jobs },
        services,
      });
      setNotice('运行配置已保存并生效。');
      setSettings(await fetchSettings(connection));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel settings-panel">
      <div className="settings-title">
        <span className="color-icon blue">
          <Monitor size={20} />
        </span>
        <div>
          <h2>运行配置</h2>
          <p>预算、人格禁忌、受控 Shell 与扩展服务。补丁式保存，不影响其他配置。</p>
        </div>
      </div>
      <div className="model-form">
        <div className="model-form-grid">
          <Field
            label="模型调用预算（次/天）"
            hint={`今日已用 ${settings.budget?.used.model_calls ?? 0} 次（预算日 ${settings.budget?.day ?? '—'}，次日自动重置）。0 = 关闭。`}
          >
            <Input
              aria-label="模型调用预算"
              type="number"
              min={0}
              max={100000}
              value={budget.model_calls}
              onChange={(e) => setBudget({ ...budget, model_calls: e.target.valueAsNumber })}
            />
          </Field>
          <Field label="媒体生成预算（次/天）" hint={`今日已用 ${settings.budget?.used.media_jobs ?? 0} 次。`}>
            <Input
              aria-label="媒体生成预算"
              type="number"
              min={0}
              max={100000}
              value={budget.media_jobs}
              onChange={(e) => setBudget({ ...budget, media_jobs: e.target.valueAsNumber })}
            />
          </Field>
        </div>
        <Field label="人格禁忌（用户自设）" hint="每行一条，并入当前角色卡的边界，保存后立即生效。">
          <Textarea
            aria-label="人格禁忌"
            rows={4}
            value={taboos}
            placeholder={'每行一条，例如：\n不谈论政治\n23 点后提醒睡觉'}
            onChange={(e) => setTaboos(e.target.value)}
          />
        </Field>
        <div className="model-intent-toggle">
          <div>
            <strong>启用受控 Shell（Linux）</strong>
            <small>命令逐次审批，在系统沙箱中执行；沙箱不可用时拒绝。</small>
          </div>
          <Switch
            label="启用受控 Shell"
            checked={services.WHALE_SHELL_ENABLED === '1'}
            onChange={(v) => setServices({ ...services, WHALE_SHELL_ENABLED: v ? '1' : '0' })}
          />
        </div>
        <Field
          label="Shell 权限档位"
          hint="默认 managed。personal 档仍须逐次审批并经过系统沙箱。"
        >
          <Select
            aria-label="Shell 权限档位"
            value={services.WHALE_SHELL_TIER || 'managed'}
            onChange={(e) => setServices({ ...services, WHALE_SHELL_TIER: e.target.value })}
          >
            <option value="managed">managed · 受管命令</option>
            <option value="safe">safe · 只读命令</option>
            <option value="personal">personal · 个人自由命令</option>
          </Select>
        </Field>
        <div className="service-groups">
          {SERVICES.map((group) => (
            <details key={group.label} className="model-advanced">
              <summary>{group.label}</summary>
              <div className="model-advanced-body">
                {group.fields.map(([name, label]) => (
                  <Field
                    key={name}
                    label={
                      <>
                        {label}{' '}
                        {typeof settings.services?.[name] === 'boolean' && settings.services[name] && (
                          <Tag tone="mint">已配置</Tag>
                        )}
                      </>
                    }
                  >
                    <Input
                      aria-label={label}
                      type={name.endsWith('_KEY') ? 'password' : 'text'}
                      autoComplete="off"
                      value={services[name] ?? ''}
                      disabled={services[name] === null}
                      placeholder={name.endsWith('_KEY') ? '留空保留原值' : name}
                      onChange={(e) => setServices({ ...services, [name]: e.target.value })}
                    />
                    {name.endsWith('_KEY') && (
                      <CheckRow
                        label="保存时清除此密钥"
                        checked={services[name] === null}
                        onChange={(v) => setServices({ ...services, [name]: v ? null : '' })}
                      />
                    )}
                  </Field>
                ))}
              </div>
            </details>
          ))}
        </div>
        {error && <Banner tone="error">{error}</Banner>}
        {notice && <Banner tone="ok">{notice}</Banner>}
        <div className="model-form-actions">
          <span className="model-form-gap" />
          <Button variant="primary" size="sm" busy={busy} onClick={save}>
            保存运行配置
          </Button>
        </div>
      </div>
    </section>
  );
}


export default function SettingsPage() {
  const { connection, disconnect, status, data, notify, refresh, sessionExpiresAt } =
    useWorkspace();
  const [tab, setTab] = useState('服务会话');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [motion, setMotion] = useState(document.documentElement.dataset.motion !== 'off');
  const [checks, setChecks] = useState<{ name: string; ok: boolean; detail: string }[] | null>(null);
  const diagnose = async () => {
    if (!connection) return;
    setBusy(true);
    setError('');
    try {
      const r = await request<{ checks: { name: string; status: string; detail: string }[] }>(
        connection,
        '/diagnostics',
      );
      setChecks(
        r.checks.map((c) => ({
          name: c.name,
          ok: ['ok', 'healthy', 'ready', 'pass'].includes(c.status),
          detail: c.detail || c.status,
        })),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="page-enter">
      <PageHeading page="settings" />
      <div className="settings-tabs segment-control">
        {['服务会话', '界面偏好', '运行配置', '运行诊断'].map((t) => (
          <button
            key={t}
            className={tab === t ? 'active' : ''}
            onClick={() => {
              setTab(t);
              setError('');
            }}
          >
            {t}
          </button>
        ))}
      </div>
      {tab === '服务会话' && (
        <section className="panel settings-panel">
          <div className="settings-title">
            <span className="color-icon violet">
              <Link2 size={20} />
            </span>
            <div>
              <h2>Whale Core 服务</h2>
              <p>控制台经同源会话连接真实服务，凭据不离开服务端。</p>
            </div>
          </div>
          <div className="connected-info">
            <div>
              <i className="status-dot" />
              <strong>已连接到服务</strong>
            </div>
            <p>{connection?.origin}</p>
            <div className="detail-meta">
              <span>服务版本</span>v{status?.version}
            </div>
            <div className="detail-meta">
              <span>运行配置</span>
              {status?.profile}
            </div>
            <div className="detail-meta">
              <span>会话有效至</span>
              {sessionExpiresAt
                ? new Date(sessionExpiresAt).toLocaleString('zh-CN', { hour12: false })
                : '—'}
            </div>
            <div className="form-actions">
              <button
                className="btn"
                onClick={() => {
                  disconnect();
                  notify('会话已注销。');
                }}
              >
                <Unplug size={15} />
                退出登录
              </button>
              <button
                className="btn primary"
                disabled={busy}
                onClick={async () => {
                  setBusy(true);
                  try {
                    await refresh();
                    notify('服务数据已刷新。');
                  } catch (e) {
                    setError((e as Error).message);
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                刷新数据
              </button>
            </div>
          </div>
          {error && (
            <p className="inline-error" role="alert">
              {error}
            </p>
          )}
          <div className="settings-security">
            <ShieldCheck size={15} />
            会话 cookie 为 HttpOnly + SameSite=Strict，12 小时有效；不会进入 URL、日志或 localStorage。
          </div>
        </section>
      )}
      {tab === '服务会话' && (
        <section className="panel settings-panel advanced-console">
          <div>
            <h2>高级能力</h2>
            <p>在原生控制台管理角色、模型配置、Studio、主动行为与实时语音。新窗口需要单独验证令牌。</p>
          </div>
          <a className="btn" href="/classic/index.html" target="_blank" rel="noreferrer">
            打开原生控制台
            <ExternalLink size={14} />
          </a>
        </section>
      )}
      {tab === '界面偏好' && (
        <section className="panel settings-panel">
          <div className="settings-title">
            <span className="color-icon blue">
              <Monitor size={20} />
            </span>
            <div>
              <h2>界面偏好</h2>
              <p>只影响当前浏览器的显示与动效。</p>
            </div>
          </div>
          <div className="setting-row">
            <div>
              <strong>界面动效</strong>
              <small>启用页面过渡和趋势线绘制动画。</small>
            </div>
            <button
              className={`switch ${motion ? 'on' : ''}`}
              role="switch"
              aria-label="界面动效"
              aria-checked={motion}
              onClick={() => {
                const next = !motion;
                setMotion(next);
                document.documentElement.dataset.motion = next ? 'on' : 'off';
                try {
                  localStorage.setItem('whale.motion', next ? 'on' : 'off');
                } catch {
                  /* 偏好仅当前标签页生效 */
                }
              }}
            >
              <span />
            </button>
          </div>
          <div className="setting-row">
            <div>
              <strong>导出工作区</strong>
              <small>下载当前服务返回的记忆、任务、会话与活动数据。</small>
            </div>
            <button
              className="btn"
              onClick={() => {
                download('whale-workspace.json', JSON.stringify(data, null, 2));
                notify('工作区文件已导出。');
              }}
            >
              <Download size={14} />
              导出
            </button>
          </div>
        </section>
      )}
      {tab === '运行配置' && connection && (
        <RuntimeConfigSection connection={connection} />
      )}
      {tab === '运行诊断' && (
        <section className="panel settings-panel">
          <div className="settings-title">
            <span className="color-icon mint">
              <Activity size={20} />
            </span>
            <div>
              <h2>服务运行诊断</h2>
              <p>从服务端获取当前健康检查结果。</p>
            </div>
          </div>
          <button className="btn primary" disabled={busy} onClick={diagnose}>
            <Activity size={15} />
            {busy ? '正在检查...' : '开始诊断'}
          </button>
          {checks && (
            <div className="diagnostic-list">
              {checks.map((c, i) => (
                <div key={i}>
                  {c.ok ? (
                    <CircleCheck className="green-text" size={18} />
                  ) : (
                    <X className="danger-text" size={18} />
                  )}
                  <span>
                    <strong>{c.name}</strong>
                    <small>{c.detail}</small>
                  </span>
                </div>
              ))}
              {!checks.length && <p className="muted">服务未返回检查项目。</p>}
            </div>
          )}
          {error && (
            <p role="alert" className="inline-error">
              {error}
            </p>
          )}
        </section>
      )}
    </div>
  );
}
