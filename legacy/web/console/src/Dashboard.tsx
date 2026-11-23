import { useEffect, useState } from 'react';
import {
  ArrowRight,
  ArrowUpRight,
  CalendarDays,
  ChevronDown,
  ChevronRight,
  Circle,
  Cpu,
  Database,
  MessageSquare,
  Network,
  Puzzle,
  RefreshCw,
  ShieldAlert,
  Smartphone,
  Sparkles,
} from 'lucide-react';
import { useWorkspace } from './workspace';
import { ActivityIcon, Empty, Modal, PageHeading } from './ui';
import {
  DAY,
  dayKey,
  kindNames,
  relativeTime,
  type Activity,
  type Day,
  type Page,
} from './data';
import {
  fetchDevices,
  fetchDiagnostics,
  fetchSettings,
  type DevicesReport,
  type DiagnosticsReport,
  type ModelSettings,
} from './api';

function smoothPath(points: [number, number][]) {
  if (!points.length) return '';
  let path = `M${points[0][0]},${points[0][1]}`;
  for (let i = 1; i < points.length; i++) {
    const p0 = points[Math.max(0, i - 2)],
      p1 = points[i - 1],
      p2 = points[i],
      p3 = points[Math.min(points.length - 1, i + 1)];
    path += ` C${p1[0] + (p2[0] - p0[0]) / 6},${p1[1] + (p2[1] - p0[1]) / 6} ${p2[0] - (p3[0] - p1[0]) / 6},${p2[1] - (p3[1] - p1[1]) / 6} ${p2[0]},${p2[1]}`;
  }
  return path;
}
function TrendChart({ days }: { days: Day[] }) {
  const [range, setRange] = useState(7);
  const [hover, setHover] = useState<number | null>(null);
  const [showMessages, setShowMessages] = useState(true);
  const [showMemories, setShowMemories] = useState(true);
  const selected = Array.from({ length: range }, (_, i) => {
    const date = dayKey(Date.now() - (range - 1 - i) * DAY);
    return days.find((d) => d.date === date) || { date, messages: 0, memories: 0 };
  });
  const max = Math.max(
    40,
    Math.ceil(
      Math.max(
        ...selected.map((d) => Math.max(showMessages ? d.messages : 0, showMemories ? d.memories : 0)),
      ) / 40,
    ) * 40,
  );
  const points = (key: 'messages' | 'memories') =>
    selected.map((d, i): [number, number] => [
      (i * 1000) / (selected.length - 1),
      160 - (d[key] / max) * 160,
    ]);
  const msg = smoothPath(points('messages')),
    mem = smoothPath(points('memories'));
  return (
    <section className="panel trend-panel">
      <div className="panel-heading">
        <h2>交互趋势</h2>
        <label className="range-select">
          <select
            aria-label="趋势时间范围"
            value={range}
            onChange={(e) => {
              setRange(Number(e.target.value));
              setHover(null);
            }}
          >
            <option value={7}>近 7 天</option>
            <option value={14}>近 14 天</option>
            <option value={30}>近 30 天</option>
          </select>
          <ChevronDown size={12} />
        </label>
      </div>
      <div className="chart-legend">
        <button
          aria-pressed={showMessages}
          className={!showMessages ? 'off' : ''}
          onClick={() => {
            if (showMemories) setShowMessages(!showMessages);
          }}
        >
          <i className="legend-dot purple" />
          交互事件
        </button>
        <button
          aria-pressed={showMemories}
          className={!showMemories ? 'off' : ''}
          onClick={() => {
            if (showMessages) setShowMemories(!showMemories);
          }}
        >
          <i className="legend-dot lilac" />
          记忆写入
        </button>
      </div>
      <div className="chart-wrap">
        <div className="plot" onMouseLeave={() => setHover(null)}>
          {[0, 1, 2, 3, 4].map((i) => (
            <div key={i} className="grid-line" style={{ top: `${i * 25}%` }}>
              <span>{(max * (4 - i)) / 4}</span>
            </div>
          ))}
          <svg
            viewBox="0 0 1000 160"
            preserveAspectRatio="none"
            className="chart-svg"
            role="img"
            aria-label={`近 ${range} 天的对话与记忆趋势，使用下方图表节点查看每日数据。`}
          >
            <defs>
              <linearGradient id="trend-fill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#9382e9" stopOpacity=".32" />
                <stop offset="100%" stopColor="#afa2ef" stopOpacity=".025" />
              </linearGradient>
            </defs>
            {showMessages && (
              <>
                <path d={`${msg} L1000,160 L0,160Z`} fill="url(#trend-fill)" />
                <path
                  key={range}
                  d={msg}
                  fill="none"
                  stroke="#8a75da"
                  strokeWidth="2.7"
                  strokeLinecap="round"
                  vectorEffect="non-scaling-stroke"
                  className="trend-line"
                  pathLength="1"
                />
              </>
            )}
            {showMemories && (
              <path
                d={mem}
                fill="none"
                stroke="#a7b6e9"
                strokeWidth="2.1"
                strokeDasharray="4 4"
                strokeLinecap="round"
                vectorEffect="non-scaling-stroke"
              />
            )}
          </svg>
          <div className="plot-hit-areas">
            {selected.map((d, i) => (
              <button
                key={d.date}
                aria-label={`${d.date}，交互 ${d.messages}，记忆 ${d.memories}`}
                onMouseEnter={() => setHover(i)}
                onFocus={() => setHover(i)}
                onBlur={() => setHover(null)}
                onClick={() => setHover(hover === i ? null : i)}
              />
            ))}
          </div>
          {hover !== null && selected[hover] && (
            <div
              className="chart-tooltip"
              style={{ left: `${Math.min(85, Math.max(13, (hover * 100) / (range - 1)))}%` }}
            >
              <b>{selected[hover].date}</b>
              <span>
                交互 {selected[hover].messages} · 记忆 {selected[hover].memories}
              </span>
            </div>
          )}
        </div>
        <div className="chart-xlabels">
          {selected
            .filter(
              (_, i) => range === 7 || i === selected.length - 1 || i % Math.ceil(range / 6) === 0,
            )
            .map((d) => (
              <span key={d.date}>{d.date.slice(5).replace('-', '/')}</span>
            ))}
        </div>
      </div>
    </section>
  );
}
export function EventDetail({ event, onClose }: { event: Activity; onClose: () => void }) {
  return (
    <Modal title="动态详情" onClose={onClose}>
      <div className="detail-title">
        <ActivityIcon kind={event.kind} />
        <div>
          <small>{kindNames[event.kind]}动态</small>
          <h3>{event.title}</h3>
        </div>
      </div>
      <p className="detail-text">{event.description}</p>
      <div className="detail-meta">
        <span>记录时间</span>
        <b>{new Date(event.at).toLocaleString('zh-CN', { hour12: false })}</b>
      </div>
      <div className="detail-meta">
        <span>记录编号</span>
        <code>{event.id.slice(0, 20)}</code>
      </div>
    </Modal>
  );
}
function uptimeText(s?: number) {
  if (!s && s !== 0) return '—';
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  return d > 0 ? `${d} 天 ${h} 小时` : h > 0 ? `${h} 小时 ${m} 分` : `${m} 分`;
}
export default function Dashboard() {
  const { data, navigate, status, connection } = useWorkspace();
  const [detail, setDetail] = useState<Activity | null>(null);
  const [devices, setDevices] = useState<DevicesReport | null>(null);
  const [diag, setDiag] = useState<DiagnosticsReport | null>(null);
  const [models, setModels] = useState<ModelSettings | null>(null);
  const [opsError, setOpsError] = useState('');
  const [opsAt, setOpsAt] = useState(0);
  const loadOps = async (quiet = true) => {
    if (!connection) return;
    try {
      const [dv, dg, st] = await Promise.all([
        fetchDevices(connection),
        fetchDiagnostics(connection),
        fetchSettings(connection),
      ]);
      setDevices(dv);
      setDiag(dg);
      setModels(st);
      setOpsError('');
      setOpsAt(Date.now());
    } catch (e) {
      setOpsError((e as Error).message);
    }
    if (!quiet) setOpsAt(Date.now());
  };
  useEffect(() => {
    void loadOps();
    const id = setInterval(() => {
      if (!document.hidden) void loadOps();
    }, 30000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connection]);
  const pending = data.tasks.filter((t) =>
    ['pending', 'pending_approval', 'running'].includes(t.status),
  ).length;
  const drafts = status?.drafts_pending ?? 0;
  const online = devices?.devices.filter((d) => d.online).length ?? 0;
  const deviceTotal = devices?.devices.length ?? 0;
  const worker = diag?.memory_worker;
  const mainModel = models?.model.model || '未配置';
  const mainHost = (() => {
    try {
      return new URL(models?.model.base_url || '').host;
    } catch {
      return models?.model.base_url || '';
    }
  })();
  const ops = [
    {
      key: 'approvals',
      label: '待处理事项',
      icon: ShieldAlert,
      color: 'amber',
      page: 'approvals' as Page,
      value: drafts + pending,
      lines: [`审批待决 ${drafts}`, `待处理任务 ${pending}`],
      tone: drafts + pending > 0 ? 'amber' : 'mint',
      foot: drafts + pending > 0 ? '需要你的确认' : '全部清空',
    },
    {
      key: 'devices',
      label: '设备与连接',
      icon: Smartphone,
      color: 'blue',
      page: 'devices' as Page,
      value: `${online}/${deviceTotal}`,
      lines: [
        `在线 ${online} · 共 ${deviceTotal}`,
        `记录清理 ${devices?.retention?.state ?? '—'}`,
      ],
      tone: deviceTotal === 0 || online > 0 ? 'blue' : 'amber',
      foot: deviceTotal === 0 ? '尚无配对设备' : online > 0 ? '有设备在线' : '全部离线',
    },
    {
      key: 'memory',
      label: '记忆后台',
      icon: Database,
      color: 'mint',
      page: 'memory' as Page,
      value: worker ? `${worker.queue_depth ?? 0}` : '—',
      lines: [
        `队列 ${worker?.queue_depth ?? '—'} · 完成 ${worker?.completed ?? '—'}`,
        `情节 ${status?.memory.episodes ?? 0} · 偏好 ${status?.memory.preferences ?? 0}`,
      ],
      tone: worker?.failed ? 'red' : 'mint',
      foot: worker
        ? worker.in_flight || worker.model_busy
          ? '正在提炼'
          : `空闲${worker.failed ? ` · 失败 ${worker.failed}` : ''}`
        : '诊断未装配',
    },
    {
      key: 'models',
      label: '模型运行',
      icon: Cpu,
      color: 'violet',
      page: 'models' as Page,
      value: mainModel,
      lines: [
        mainHost || '—',
        `意图规划 ${models?.intent_model_enabled ? '开' : '关'}`,
      ],
      tone: models?.model.model ? 'violet' : 'amber',
      foot: models?.profile === 'local' ? '本地模型' : '云端模型',
    },
  ];
  return (
    <div className="page-enter dashboard">
      <PageHeading
        page="overview"
        action={
          <div className="today-date">
            <button
              className="icon-button"
              aria-label="刷新运行状态"
              onClick={() => void loadOps(false)}
            >
              <RefreshCw size={15} />
            </button>
            <CalendarDays size={15} />
            <span>
              {new Date().toLocaleDateString('zh-CN', {
                month: 'long',
                day: 'numeric',
                weekday: 'long',
              })}
            </span>
          </div>
        }
      />
      <section className="whale-banner" aria-label="Whale Core 智能工作空间">
        <div className="banner-art" />
        <div className="banner-copy">
          <div className="banner-eyebrow">
            <span />
            INTELLIGENCE WORKSPACE
          </div>
          <h2>
            Whale Core<span className="brand-period">.</span>
          </h2>
          <p>
            你的专属智能中枢<span className="banner-long-copy">，连接对话、记忆与行动</span>。
          </p>
          <button className="banner-cta" onClick={() => navigate('chat')}>
            <MessageSquare size={15} />
            开始对话
            <ArrowRight size={15} />
          </button>
        </div>
      </section>
      <section className="status-strip panel" aria-label="服务运行状态">
        <span className="status-pill">
          <i className="dot on" />
          服务运行中
        </span>
        <span className="status-strip-item">
          v{status?.version ?? '—'} · {status?.profile ?? '—'}
        </span>
        <span className="status-strip-item">运行 {uptimeText(status?.uptime_s)}</span>
        <span className="status-strip-item muted">
          {opsAt ? `同步于 ${new Date(opsAt).toLocaleTimeString('zh-CN', { hour12: false })}` : ''}
        </span>
      </section>
      {opsError && (
        <p className="inline-error" role="alert">
          运行状态读取失败：{opsError}
        </p>
      )}
      <div className="metrics-grid">
        {ops.map((m, i) => (
          <button
            className="metric"
            key={m.key}
            onClick={() => navigate(m.page)}
            style={{ animationDelay: `${i * 45}ms` }}
          >
            <div className="metric-head">
              <span className={`color-icon small ${m.color}`}>
                <m.icon size={16} />
              </span>
              <span>{m.label}</span>
              <ArrowUpRight className="metric-arrow" size={12} />
            </div>
            <div className="metric-value-row">
              <strong>{m.value}</strong>
            </div>
            <div className="metric-note">
              <i className={`tiny-dot ${m.tone === 'mint' ? 'blue' : m.tone === 'red' ? 'amber' : m.color === 'amber' ? 'amber' : 'blue'}`} />
              <b>{m.foot}</b>
              <span>{m.lines[0]}</span>
            </div>
            <div className="metric-note metric-note-sub">
              <span>{m.lines[1]}</span>
            </div>
          </button>
        ))}
      </div>
      <div className="dashboard-lower">
        <TrendChart days={data.days} />
        <section className="panel activity-panel">
          <div className="panel-heading">
            <h2>最近动态</h2>
            <button className="text-link" onClick={() => navigate('logs')}>
              查看全部
              <ChevronRight size={13} />
            </button>
          </div>
          <div className="event-list">
            {data.events.slice(0, 4).map((e) => (
              <button key={e.id} className="event-row" onClick={() => setDetail(e)}>
                <ActivityIcon kind={e.kind} small />
                <span className="event-copy">
                  <strong>{e.title}</strong>
                  <small>{e.description}</small>
                </span>
                <time>{relativeTime(e.at)}</time>
              </button>
            ))}
            {!data.events.length && (
              <Empty title="还没有运行动态" text="服务产生新事件后，将显示在这里。" />
            )}
          </div>
        </section>
      </div>
      <div className="quick-heading">
        <h2>快捷入口</h2>
        <span>QUICK ACCESS</span>
      </div>
      <div className="quick-links">
        {(
          [
            {
              page: 'memory',
              Icon: Database,
              title: '浏览记忆空间',
              text: '回顾与管理长期记忆',
              color: 'mint',
            },
            {
              page: 'workflows',
              Icon: Network,
              title: '探索工作流',
              text: '运行你的自动化流程',
              color: 'violet',
            },
            {
              page: 'plugins',
              Icon: Puzzle,
              title: '管理能力插件',
              text: '扩展鲸鱼的能力边界',
              color: 'amber',
            },
          ] as const
        ).map((x) => (
          <button key={x.page} onClick={() => navigate(x.page)}>
            <span className={`color-icon ${x.color}`}>
              <x.Icon size={18} />
            </span>
            <span>
              <strong>{x.title}</strong>
              <small>{x.text}</small>
            </span>
            <ArrowUpRight size={15} />
          </button>
        ))}
      </div>
      <div className="page-footer">
        <span>
          <Sparkles size={12} />
          Whale Core Console
        </span>
        <span>
          <Circle size={6} fill="currentColor" />
          已连接服务
        </span>
      </div>
      {detail && <EventDetail event={detail} onClose={() => setDetail(null)} />}
    </div>
  );
}
