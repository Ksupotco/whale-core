import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Activity,
  ChevronDown,
  ChevronRight,
  LoaderCircle,
  Pause,
  Play,
  Radio,
  RefreshCw,
  Trash2,
} from 'lucide-react';
import {
  fetchTrace,
  fetchTraces,
  streamEvents,
  type TraceDetail,
  type TraceItem,
  type WhaleEvent,
} from './api';
import { Select } from './ds';
import { Empty, PageHeading } from './ui';
import { useWorkspace } from './workspace';

const timeOf = (seconds: number) =>
  new Date(seconds * 1000).toLocaleTimeString('zh-CN', { hour12: false });

const TRACE_STATUS: Record<string, { text: string; tone: string }> = {
  replied: { text: '已回复', tone: 'mint' },
  partial: { text: '部分完成', tone: 'amber' },
  failed: { text: '失败', tone: 'danger' },
  planned: { text: '已规划', tone: 'blue' },
};

export default function EventsPage() {
  const [subTab, setSubTab] = useState<'traces' | 'stream'>('traces');
  return (
    <div className="page-enter">
      <PageHeading page="events" />
      <div className="segment-control events-subtabs" role="tablist">
        <button
          role="tab"
          aria-selected={subTab === 'traces'}
          className={subTab === 'traces' ? 'active' : ''}
          onClick={() => setSubTab('traces')}
        >
          <Activity size={14} />
          Trace 账本
        </button>
        <button
          role="tab"
          aria-selected={subTab === 'stream'}
          className={subTab === 'stream' ? 'active' : ''}
          onClick={() => setSubTab('stream')}
        >
          <Radio size={14} />
          实时事件流
        </button>
      </div>
      {subTab === 'traces' ? <TraceLedger /> : <EventStream />}
    </div>
  );
}

function TraceLedger() {
  const { connection, notify } = useWorkspace();
  const [traces, setTraces] = useState<TraceItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [openId, setOpenId] = useState<string | null>(null);
  const [detail, setDetail] = useState<TraceDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [kindFilter, setKindFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState('');

  const load = useCallback(async () => {
    if (!connection) return;
    setLoading(true);
    setError('');
    try {
      const r = await fetchTraces(connection, 30, {
        kind: kindFilter || undefined,
        status: statusFilter || undefined,
      });
      setTraces(r.traces);
      setTotal(r.total);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [connection, kindFilter, statusFilter]);
  useEffect(() => {
    void load();
  }, [load]);

  const toggle = async (id: string) => {
    if (!connection) return;
    if (openId === id) {
      setOpenId(null);
      setDetail(null);
      return;
    }
    setOpenId(id);
    setDetail(null);
    setDetailLoading(true);
    try {
      setDetail(await fetchTrace(connection, id));
    } catch (e) {
      notify((e as Error).message, true);
      setOpenId(null);
    } finally {
      setDetailLoading(false);
    }
  };

  return (
    <section className="panel list-panel">
      <div className="toolbar">
        <span className="muted small-text">共 {total} 条 Trace</span>
        <div className="stream-actions">
          <Select
            aria-label="Trace 类型"
            value={kindFilter}
            onChange={(e) => setKindFilter(e.target.value)}
          >
            <option value="">全部类型</option>
            <option value="qq_inbound">qq_inbound</option>
            <option value="task">task</option>
            <option value="device_event">device_event</option>
            <option value="action">action</option>
          </Select>
          <Select
            aria-label="Trace 状态"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
          >
            <option value="">全部状态</option>
            <option value="replied">已回复</option>
            <option value="partial">部分完成</option>
            <option value="failed">失败</option>
            <option value="planned">已规划</option>
          </Select>
          <button className="btn" disabled={loading} onClick={() => void load()}>
            <RefreshCw size={14} className={loading ? 'spin' : ''} />
            刷新
          </button>
        </div>
      </div>
      {error && (
        <p className="inline-error" role="alert">
          {error}
        </p>
      )}
      <div className="record-list trace-list">
        {traces.map((t) => {
          const st = TRACE_STATUS[t.status] || { text: t.status, tone: 'neutral' };
          const isOpen = openId === t.trace_id;
          return (
            <div className={`trace-item ${isOpen ? 'open' : ''}`} key={t.trace_id}>
              <button className="record-row trace-row" onClick={() => void toggle(t.trace_id)}>
                <span className="trace-chevron">
                  {isOpen ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
                </span>
                <span className="tag violet trace-kind">{t.kind}</span>
                <span className="record-info">
                  <strong>{t.summary || t.trace_id}</strong>
                  <small>
                    {t.conversation_id || '—'}
                    {t.principal_id ? ` · ${t.principal_id}` : ''}
                  </small>
                </span>
                <span className="muted small-text trace-duration">
                  {Math.max(1, Math.round(t.ended_at - t.started_at))}s
                </span>
                <span className={`tag ${st.tone}`}>{st.text}</span>
                <span className="record-date">{timeOf(t.started_at)}</span>
              </button>
              {isOpen && (
                <div className="trace-detail">
                  {detailLoading && (
                    <p className="muted small-text padded">
                      <LoaderCircle size={13} className="spin" /> 正在读取 Span 瀑布…
                    </p>
                  )}
                  {detail && (
                    <>
                      <div className="trace-meta muted small-text">
                        Trace <code>{detail.trace_id}</code>
                        {detail.task_id ? (
                          <>
                            {' '}
                            · 任务 <code>{detail.task_id}</code>
                          </>
                        ) : null}
                      </div>
                      <div className="span-list">
                        {detail.spans.map((s) => (
                          <div className="span-row" key={s.span_id}>
                            <span className={`span-phase ${s.status}`}>{s.phase}</span>
                            <span className="span-info">
                              <strong>{s.reason_code || '—'}</strong>
                              <small>{s.detail}</small>
                            </span>
                            <span className="span-time muted">{s.duration_ms} ms</span>
                          </div>
                        ))}
                        {!detail.spans.length && (
                          <p className="muted small-text padded">此 Trace 没有记录 Span。</p>
                        )}
                      </div>
                    </>
                  )}
                </div>
              )}
            </div>
          );
        })}
        {!traces.length && !loading && (
          <Empty title="暂无 Trace 记录" text="服务处理消息与任务后，结构化轨迹会记录在这里。" />
        )}
        {loading && !traces.length && (
          <p className="muted padded">
            <LoaderCircle size={14} className="spin" /> 正在读取 Trace 账本…
          </p>
        )}
      </div>
    </section>
  );
}

function EventStream() {
  const { connection } = useWorkspace();
  const [events, setEvents] = useState<WhaleEvent[]>([]);
  const [paused, setPaused] = useState(false);
  const [live, setLive] = useState(false);
  const streamRef = useRef<{ close: () => void } | null>(null);

  useEffect(() => {
    if (!connection || paused) {
      setLive(false);
      return;
    }
    const stream = streamEvents(connection, {
      onOpen: () => setLive(true),
      onEvent: (ev) => setEvents((prev) => [ev, ...prev].slice(0, 120)),
      onError: () => setLive(false),
    });
    streamRef.current = stream;
    return () => {
      stream.close();
      streamRef.current = null;
      setLive(false);
    };
  }, [connection, paused]);

  return (
    <section className="panel event-stream-panel">
      <div className="toolbar">
        <span className="stream-status">
          <i className={`dot ${live && !paused ? 'on' : ''}`} />
          {paused ? '已暂停接收' : live ? 'SSE 实时连接' : '连接中…'}
        </span>
        <div className="stream-actions">
          <button className="btn" onClick={() => setPaused((p) => !p)}>
            {paused ? <Play size={14} /> : <Pause size={14} />}
            {paused ? '继续' : '暂停'}
          </button>
          <button className="btn" onClick={() => setEvents([])} disabled={!events.length}>
            <Trash2 size={14} />
            清屏
          </button>
        </div>
      </div>
      <div className="event-stream" role="log" aria-live="polite" aria-label="实时事件流">
        {!events.length && <p className="stream-empty muted">等待服务端推送实时事件…</p>}
        {events.map((ev, i) => (
          <div className="stream-row" key={`${ev.seq}-${i}`}>
            <span className="stream-time">{timeOf(ev.at)}</span>
            <code className="stream-kind">[{ev.kind}]</code>
            <span className="stream-detail">{ev.detail}</span>
          </div>
        ))}
      </div>
    </section>
  );
}
