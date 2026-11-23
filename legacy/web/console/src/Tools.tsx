import { useCallback, useEffect, useState } from 'react';
import {
  BookOpen,
  CheckCheck,
  ChevronRight,
  Compass,
  Database,
  Download,
  Heart,
  LoaderCircle,
  Network,
  Play,
  Plus,
  Sparkles,
  ThumbsDown,
  ThumbsUp,
  Trash2,
  X,
} from 'lucide-react';
import {
  addInterest,
  explore,
  fetchLife,
  fetchWorkflowSettings,
  lifeFeedback,
  removeInterest,
  request,
  saveWorkflowSettings,
  type LifeState,
  type WorkflowSettings,
} from './api';
import type { Connection } from './api';
import { download } from './data';
import { Button, Field, Input, Switch } from './ds';
import { Empty, Modal, PageHeading } from './ui';
import { useWorkspace } from './workspace';

type Workflow = { name: string; description: string; key?: string };
export function WorkflowsPage() {
  const { connection, notify } = useWorkspace();
  const [flows, setFlows] = useState<Workflow[]>([]);
  const [selected, setSelected] = useState<Workflow | null>(null);
  const [input, setInput] = useState('');
  const [output, setOutput] = useState('');
  const [busy, setBusy] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    if (!connection) return;
    let alive = true;
    setLoadError('');
    setFlows([]);
    request<{ skills: Workflow[] }>(connection, '/workflows/skills')
      .then((r) => {
        if (alive) setFlows(r.skills);
      })
      .catch((e) => {
        if (alive) setLoadError(e.message);
      });
    return () => {
      alive = false;
    };
  }, [connection, retry]);
  const run = async () => {
    if (!selected || busy || !connection) return;
    setBusy(true);
    try {
      const r = await request<unknown>(
        connection,
        `/workflows/skills/${encodeURIComponent(selected.name)}/run`,
        'POST',
        { input_text: input },
      );
      setOutput(typeof r === 'string' ? r : JSON.stringify(r, null, 2));
    } catch (e) {
      notify((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  };
  if (!connection) return null;
  return (
    <div className="page-enter">
      <PageHeading page="workflows" />
      <div className="section-intro">
        <span className="section-eyebrow">WORKFLOW LIBRARY</span>
        <h2>服务工作流</h2>
        <p>以下工作流来自已连接的 Whale Core 服务，由服务端真实执行。</p>
      </div>
      {loadError && (
        <div className="inline-error">
          {loadError}
          <button onClick={() => setRetry((x) => x + 1)}>重新加载</button>
        </div>
      )}
      <div className="workflow-grid">
        {flows.map((f, i) => {
          const Icon = [BookOpen, CheckCheck, Compass][i % 3];
          return (
            <button
              key={f.name}
              className={`workflow-card flow-${i % 3}`}
              onClick={() => {
                setSelected(f);
                setInput('');
                setOutput('');
              }}
            >
              <div className="flow-illustration">
                <div className="flow-node">
                  <Database size={18} />
                </div>
                <i />
                <div className="flow-node central">
                  <Icon size={25} />
                </div>
                <i />
                <div className="flow-node">
                  <Network size={17} />
                </div>
              </div>
              <div className="workflow-copy">
                <span className="section-eyebrow">WORKFLOW {String(i + 1).padStart(2, '0')}</span>
                <h3>{f.name}</h3>
                <p>{f.description}</p>
                <span className="workflow-run">
                  打开工作流
                  <ChevronRight size={14} />
                </span>
              </div>
            </button>
          );
        })}
      </div>
      {!flows.length && !loadError && (
        <Empty title="暂无可用工作流" text="在服务端注册工作流后，重新加载即可查看。" />
      )}
      <LifeSection connection={connection} notify={notify} />
      <InitiativeSection connection={connection} notify={notify} />
      <ExploreSection connection={connection} notify={notify} />
      {selected && (
        <Modal
          title={selected.name}
          wide
          onClose={() => {
            if (!busy) setSelected(null);
          }}
        >
          <p className="muted">{selected.description}</p>
          {output ? (
            <>
              <pre className="flow-output">{output}</pre>
              <div className="form-actions">
                <button className="btn" onClick={() => setOutput('')}>
                  调整参数
                </button>
                <button
                  className="btn primary"
                  onClick={() => {
                    download(`whale-workflow-${selected.key || 'result'}.txt`, output, 'text/plain');
                    notify('结果文件已生成。');
                  }}
                >
                  <Download size={15} />
                  保存结果
                </button>
              </div>
            </>
          ) : (
            <form
              className="form-stack"
              onSubmit={(e) => {
                e.preventDefault();
                run();
              }}
            >
              <label>
                工作流输入
                <input
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  maxLength={4000}
                  placeholder="输入本次工作流的内容"
                />
              </label>
              <div className="info-note">
                <Network size={16} />
                运行后将由服务端执行已注册的工作流步骤。
              </div>
              <div className="form-actions">
                <button className="btn primary" disabled={busy}>
                  {busy ? <LoaderCircle className="spin" size={15} /> : <Play size={14} />}{' '}
                  {busy ? '正在执行...' : '运行工作流'}
                </button>
              </div>
            </form>
          )}
        </Modal>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- 一起探索（/workflows/explore）
function ExploreSection({
  connection,
  notify,
}: {
  connection: Connection;
  notify: (t: string, e?: boolean) => void;
}) {
  const [topics, setTopics] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState('');
  const run = async () => {
    const list = topics
      .split(/[,，\n]/)
      .map((t) => t.trim())
      .filter(Boolean);
    if (!list.length) return;
    setBusy(true);
    setResult('');
    try {
      const r = await explore(connection, list);
      setResult(typeof r === 'string' ? r : JSON.stringify(r, null, 2));
    } catch (e) {
      notify((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="panel settings-panel">
      <div className="settings-title">
        <span className="color-icon violet">
          <Compass size={19} />
        </span>
        <div>
          <h2>一起探索</h2>
          <p>给鲸鱼几个话题，由它去发现新事物。</p>
        </div>
      </div>
      <Field label="探索话题" hint="逗号或换行分隔多个话题。">
        <Input
          aria-label="探索话题"
          value={topics}
          onChange={(e) => setTopics(e.target.value)}
          placeholder="例如：座头鲸迁徙, 海洋声学"
        />
      </Field>
      <div className="form-actions">
        <Button variant="primary" size="sm" busy={busy} disabled={!topics.trim()} onClick={run} icon={<Sparkles size={14} />}>
          开始探索
        </Button>
      </div>
      {result && <pre className="flow-output">{result}</pre>}
    </section>
  );
}

// ---------------------------------------------------------------- 主动行为设置（/workflows/settings）
function InitiativeSection({
  connection,
  notify,
}: {
  connection: Connection;
  notify: (t: string, e?: boolean) => void;
}) {
  const [settings, setSettings] = useState<WorkflowSettings | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    fetchWorkflowSettings(connection).then(setSettings).catch(() => setSettings(null));
  }, [connection]);
  if (!settings) return null;
  const set = (patch: Partial<WorkflowSettings>) => setSettings({ ...settings, ...patch });
  const save = async () => {
    setBusy(true);
    try {
      setSettings(await saveWorkflowSettings(connection, settings));
      notify('主动行为设置已保存。');
    } catch (e) {
      notify((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="panel settings-panel">
      <div className="settings-title">
        <span className="color-icon amber">
          <Network size={19} />
        </span>
        <div>
          <h2>主动行为</h2>
          <p>鲸鱼主动发起对话的节奏与边界。</p>
        </div>
      </div>
      <div className="model-form-grid">
        <Field inline label="暂停主动行为">
          <Switch label="暂停主动行为" checked={settings.paused} onChange={(v) => set({ paused: v })} />
        </Field>
        <Field inline label="定时调度">
          <Switch
            label="定时调度"
            checked={settings.schedule_enabled ?? false}
            onChange={(v) => set({ schedule_enabled: v })}
          />
        </Field>
        <Field label="每日行动上限">
          <Input
            aria-label="每日行动上限"
            type="number"
            min={0}
            value={settings.daily_limit}
            onChange={(e) => set({ daily_limit: e.target.valueAsNumber || 0 })}
          />
        </Field>
        <Field label="探索间隔（秒）">
          <Input
            aria-label="探索间隔"
            type="number"
            min={0}
            value={settings.interval_s ?? 0}
            onChange={(e) => set({ interval_s: e.target.valueAsNumber || 0 })}
          />
        </Field>
        <Field label="静默开始">
          <Input aria-label="静默开始" value={settings.quiet_start} placeholder="23:00" onChange={(e) => set({ quiet_start: e.target.value })} />
        </Field>
        <Field label="静默结束">
          <Input aria-label="静默结束" value={settings.quiet_end} placeholder="07:30" onChange={(e) => set({ quiet_end: e.target.value })} />
        </Field>
        <Field label="时区">
          <Input aria-label="时区" value={settings.timezone} onChange={(e) => set({ timezone: e.target.value })} />
        </Field>
      </div>
      <div className="form-actions">
        <Button variant="primary" size="sm" busy={busy} onClick={save}>
          保存主动行为设置
        </Button>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------- 兴趣台账与探索日记（/life）
function LifeSection({
  connection,
  notify,
}: {
  connection: Connection;
  notify: (t: string, e?: boolean) => void;
}) {
  const [life, setLife] = useState<LifeState | null>(null);
  const [topic, setTopic] = useState('');
  const load = useCallback(async () => {
    try {
      setLife(await fetchLife(connection));
    } catch {
      setLife(null);
    }
  }, [connection]);
  useEffect(() => {
    void load();
  }, [load]);
  const feedback = async (t: string, signal: 'liked' | 'disliked' | 'ignored') => {
    try {
      await lifeFeedback(connection, t, signal);
      notify('反馈已记录。');
    } catch (e) {
      notify((e as Error).message, true);
    }
  };
  const remove = async (t: string) => {
    try {
      await removeInterest(connection, t);
      await load();
      notify('兴趣已移除。');
    } catch (e) {
      notify((e as Error).message, true);
    }
  };
  if (!life) return null;
  return (
    <section className="panel settings-panel">
      <div className="settings-title">
        <span className="color-icon mint">
          <Heart size={19} />
        </span>
        <div>
          <h2>兴趣台账</h2>
          <p>鲸鱼长期关注的兴趣与探索日记。</p>
        </div>
      </div>
      <div className="life-interests">
        {life.interests.map((i) => (
          <div className="life-interest" key={i.topic}>
            <strong>{i.topic}</strong>
            <span className="muted small-text">权重 {i.weight.toFixed(2)}</span>
            <span className="life-feedback">
              <button aria-label={`喜欢 ${i.topic}`} title="喜欢" onClick={() => void feedback(i.topic, 'liked')}>
                <ThumbsUp size={14} />
              </button>
              <button aria-label={`不喜欢 ${i.topic}`} title="不喜欢" onClick={() => void feedback(i.topic, 'disliked')}>
                <ThumbsDown size={14} />
              </button>
              <button aria-label={`忽略 ${i.topic}`} title="忽略" onClick={() => void feedback(i.topic, 'ignored')}>
                <X size={14} />
              </button>
            </span>
            <button className="ds-icon-btn" aria-label={`移除 ${i.topic}`} onClick={() => void remove(i.topic)}>
              <Trash2Icon />
            </button>
          </div>
        ))}
        {!life.interests.length && <p className="muted small-text">暂无兴趣记录。</p>}
      </div>
      <div className="form-actions" style={{ justifyContent: 'flex-start' }}>
        <Input
          aria-label="新兴趣话题"
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
          placeholder="添加兴趣话题"
        />
        <Button
          variant="secondary"
          size="sm"
          icon={<Plus size={14} />}
          disabled={!topic.trim()}
          onClick={async () => {
            try {
              await addInterest(connection, topic.trim());
              setTopic('');
              await load();
              notify('兴趣已添加。');
            } catch (e) {
              notify((e as Error).message, true);
            }
          }}
        >
          添加
        </Button>
      </div>
      {life.diary.length > 0 && (
        <div className="life-diary">
          <h3 className="muted small-text">探索日记</h3>
          {life.diary.map((d) => (
            <div className="life-diary-entry" key={d.day}>
              <strong>{d.day}</strong>
              <p>{d.note}</p>
              {!!d.sources.length && <small className="muted">来源：{d.sources.join('、')}</small>}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
function Trash2Icon() {
  return <Trash2 size={14} />;
}
