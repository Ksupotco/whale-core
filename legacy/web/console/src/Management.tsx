import { useState } from 'react';
import {
  CheckCheck,
  ChevronLeft,
  ChevronRight,
  Download,
  FileText,
  ListTodo,
  Plus,
  RotateCcw,
} from 'lucide-react';
import { request, fetchTaskTimeline, resumeTask, type TaskTimeline } from './api';
import {
  DAY,
  download,
  kindNames,
  relativeTime,
  shortDate,
  taskLabel,
  type Activity,
  type Task,
} from './data';
import { ActivityIcon, Empty, Modal, PageHeading, SearchField } from './ui';
import { Banner } from './ds';
import { EventDetail } from './Dashboard';
import { useWorkspace } from './workspace';

function Pagination({
  page,
  total,
  setPage,
  size = 8,
}: {
  page: number;
  total: number;
  setPage: (n: number) => void;
  size?: number;
}) {
  const last = Math.max(1, Math.ceil(total / size));
  return (
    <div className="pagination">
      <span>
        共 <b>{total}</b> 条记录
      </span>
      <div>
        <button
          className="icon-button"
          disabled={page <= 1}
          aria-label="上一页"
          onClick={() => setPage(page - 1)}
        >
          <ChevronLeft size={16} />
        </button>
        <span>
          {page} / {last}
        </span>
        <button
          className="icon-button"
          disabled={page >= last}
          aria-label="下一页"
          onClick={() => setPage(page + 1)}
        >
          <ChevronRight size={16} />
        </button>
      </div>
    </div>
  );
}
export function TasksPage({ initialSearch = '' }: { initialSearch?: string }) {
  const { data, connection, notify, refresh, navigate } = useWorkspace();
  const [q, setQ] = useState(initialSearch);
  const [tab, setTab] = useState('全部任务');
  const [page, setPage] = useState(1);
  const [detail, setDetail] = useState<Task | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmAction, setConfirmAction] = useState<string | null>(null);
  const [timeline, setTimeline] = useState<TaskTimeline | null>(null);
  const [timelineLoading, setTimelineLoading] = useState(false);
  const loadTimeline = async (id: string) => {
    if (!connection) return;
    setTimelineLoading(true);
    try {
      setTimeline(await fetchTaskTimeline(connection, id));
    } catch (e) {
      notify((e as Error).message, true);
    } finally {
      setTimelineLoading(false);
    }
  };
  const resume = async (id: string) => {
    if (!connection) return;
    setBusy(true);
    try {
      await resumeTask(connection, id);
      await refresh();
      setDetail(null);
      setTimeline(null);
      notify('任务已恢复执行。');
    } catch (e) {
      notify((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  };
  const items = data.tasks.filter(
    (t) =>
      (tab === '全部任务' ||
        (tab === '待处理'
          ? ['pending', 'pending_approval', 'queued', 'running', 'clarify'].includes(t.status)
          : taskLabel(t.status) === tab)) &&
      t.title.toLowerCase().includes(q.toLowerCase()),
  );
  const act = async (action: string) => {
    if (!detail || !connection) return;
    setBusy(true);
    try {
      await request(
        connection,
        `/tasks/${encodeURIComponent(detail.id)}/${action === 'cancelled' ? 'cancel' : 'decision'}`,
        'POST',
        action === 'cancelled' ? undefined : { decision: action },
      );
      await refresh();
      setDetail(null);
      setConfirmAction(null);
      notify('任务状态已更新。');
    } catch (e) {
      notify((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="page-enter">
      <PageHeading
        page="tasks"
        action={
          <button className="btn primary" onClick={() => navigate('chat')}>
            <Plus size={16} />
            通过对话创建任务
          </button>
        }
      />
      <section className="panel list-panel">
        <div className="toolbar">
          <SearchField
            value={q}
            onChange={(v) => {
              setQ(v);
              setPage(1);
            }}
            placeholder="搜索任务..."
          />
          <span className="muted small-text">{data.tasks.length} 个任务</span>
        </div>
        <div className="tabs">
          {['全部任务', '待处理', '已完成', '已取消'].map((t) => (
            <button
              className={tab === t ? 'active' : ''}
              key={t}
              onClick={() => {
                setTab(t);
                setPage(1);
              }}
            >
              {t}
            </button>
          ))}
        </div>
        <div className="record-list">
          {items.slice((page - 1) * 8, page * 8).map((t) => (
            <button
              className="record-row"
              key={t.id}
              onClick={() => {
                setDetail(t);
                setConfirmAction(null);
                setTimeline(null);
              }}
            >
              <span className={`color-icon ${taskLabel(t.status) === '已完成' ? 'mint' : 'blue'}`}>
                {taskLabel(t.status) === '已完成' ? <CheckCheck size={18} /> : <ListTodo size={18} />}
              </span>
              <span className="record-info">
                <strong>{t.title}</strong>
                <small>{t.description}</small>
              </span>
              <span className={`tag ${taskLabel(t.status) === '已完成' ? 'mint' : 'neutral'}`}>
                {taskLabel(t.status)}
              </span>
              <span className="record-date">{shortDate(t.at)}</span>
              <ChevronRight size={15} />
            </button>
          ))}
          {!items.length && <Empty title="没有匹配的任务" />}
        </div>
        <Pagination total={items.length} page={page} setPage={setPage} />
      </section>
      {detail && (
        <Modal
          title={confirmAction ? '确认任务操作' : '任务详情'}
          wide
          onClose={() => {
            if (!busy) {
              setDetail(null);
              setTimeline(null);
            }
          }}
        >
          <span className="tag violet">{taskLabel(detail.status)}</span>
          <h3 className="detail-heading">{detail.title}</h3>
          <p className="detail-text">
            {confirmAction
              ? confirmAction === 'approve'
                ? '批准后，服务端可能立即执行此任务。请确认已经检查任务目标。'
                : '确定更新此任务的状态吗？'
              : detail.description}
          </p>
          <div className="detail-meta">
            <span>创建时间</span>
            {new Date(detail.at).toLocaleString('zh-CN')}
          </div>
          {!confirmAction && connection && (
            <div className="task-timeline-block">
              {!timeline ? (
                <button className="text-link" disabled={timelineLoading} onClick={() => void loadTimeline(detail.id)}>
                  {timelineLoading ? '正在读取时间线…' : '查看执行时间线'}
                </button>
              ) : (
                <div className="task-timeline">
                  <div className="task-current">
                    <strong>{timeline.current.label || timeline.phase}</strong>
                    <p>{timeline.current.explanation}</p>
                    {timeline.current.next_step && <small>下一步：{timeline.current.next_step}</small>}
                  </div>
                  <div className="span-list">
                    {timeline.timeline.map((ev, i) => (
                      <div className="span-row" key={i}>
                        <span className="span-phase">{ev.kind}</span>
                        <span className="span-info">
                          <strong>{ev.text}</strong>
                          <small>{ev.at_text}</small>
                        </span>
                      </div>
                    ))}
                  </div>
                  {timeline.receipts.length > 0 && (
                    <div className="task-receipts">
                      <strong className="muted small-text">回执</strong>
                      {timeline.receipts.map((r, i) => (
                        <code key={i}>{r}</code>
                      ))}
                    </div>
                  )}
                  <div className="detail-meta">
                    <span>送达</span>
                    {timeline.delivery.state} · 尝试 {timeline.delivery.attempts} 次
                    {timeline.delivery.last_error ? ` · ${timeline.delivery.last_error}` : ''}
                  </div>
                  {timeline.trace_ids.length > 0 && (
                    <div className="detail-meta">
                      <span>关联 Trace</span>
                      <code>{timeline.trace_ids.join('、')}</code>
                    </div>
                  )}
                  {timeline.retry.allowed && timeline.retry.kind !== 'none' && (
                    <Banner tone="info">
                      可{timeline.retry.kind === 'resume' ? '恢复执行' : timeline.retry.kind === 'redeliver' ? '重发结果' : '检查'}：
                      {timeline.retry.reason}
                    </Banner>
                  )}
                </div>
              )}
            </div>
          )}
          <div className="form-actions">
            {confirmAction ? (
              <>
                <button className="btn" disabled={busy} onClick={() => setConfirmAction(null)}>
                  返回
                </button>
                <button className="btn primary" disabled={busy} onClick={() => act(confirmAction)}>
                  {busy ? '处理中...' : '确认操作'}
                </button>
              </>
            ) : (
              <>
                {['failed', 'denied'].includes(detail.status) && (
                  <button className="btn" disabled={busy} onClick={() => void resume(detail.id)}>
                    <RotateCcw size={14} />
                    恢复执行
                  </button>
                )}
                {['pending', 'pending_approval', 'running', 'queued'].includes(detail.status) ? (
                  <>
                    <button className="btn" onClick={() => setConfirmAction('cancelled')}>
                      取消任务
                    </button>
                    {detail.status === 'pending_approval' && (
                      <>
                        <button className="btn" onClick={() => setConfirmAction('reject')}>
                          拒绝
                        </button>
                        <button className="btn primary" onClick={() => setConfirmAction('approve')}>
                          批准任务
                        </button>
                      </>
                    )}
                  </>
                ) : (
                  !['failed', 'denied'].includes(detail.status) && (
                    <button className="btn" onClick={() => setDetail(null)}>
                      关闭
                    </button>
                  )
                )}
              </>
            )}
          </div>
        </Modal>
      )}
    </div>
  );
}
export function LogsPage() {
  const { data, notify } = useWorkspace();
  const [q, setQ] = useState('');
  const [filter, setFilter] = useState('all');
  const [range, setRange] = useState('all');
  const [page, setPage] = useState(1);
  const [detail, setDetail] = useState<Activity | null>(null);
  const rangeMs = range === 'today' ? DAY : range === '3d' ? 3 * DAY : 0;
  const items = data.events.filter(
    (e) =>
      (filter === 'all' || e.kind === filter) &&
      (!rangeMs || e.at > Date.now() - rangeMs) &&
      `${e.title}${e.description}`.toLowerCase().includes(q.toLowerCase()),
  );
  return (
    <div className="page-enter">
      <PageHeading
        page="logs"
        action={
          <button
            className="btn"
            onClick={() => {
              download('whale-activity.json', JSON.stringify(items, null, 2));
              notify('日志文件已生成。');
            }}
          >
            <Download size={15} />
            导出日志
          </button>
        }
      />
      <section className="panel list-panel">
        <div className="toolbar">
          <SearchField
            value={q}
            onChange={(v) => {
              setQ(v);
              setPage(1);
            }}
            placeholder="搜索运行日志..."
          />
          <select
            className="select-control"
            aria-label="时间范围"
            value={range}
            onChange={(e) => {
              setRange(e.target.value);
              setPage(1);
            }}
          >
            <option value="all">全部时间</option>
            <option value="today">今天</option>
            <option value="3d">近 3 天</option>
          </select>
          <select
            className="select-control"
            aria-label="日志类型"
            value={filter}
            onChange={(e) => {
              setFilter(e.target.value);
              setPage(1);
            }}
          >
            <option value="all">全部类型</option>
            {Object.entries(kindNames).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
        </div>
        <div className="record-list">
          {items.slice((page - 1) * 8, page * 8).map((e) => (
            <button className="record-row" key={e.id} onClick={() => setDetail(e)}>
              <ActivityIcon kind={e.kind} />
              <span className="record-info">
                <strong>{e.title}</strong>
                <small>{e.description}</small>
              </span>
              <span className="tag neutral">{kindNames[e.kind]}</span>
              <time className="record-date">{relativeTime(e.at)}</time>
              <ChevronRight size={15} />
            </button>
          ))}
          {!items.length && <Empty />}
        </div>
        <Pagination page={page} setPage={setPage} total={items.length} />
      </section>
      {detail && <EventDetail event={detail} onClose={() => setDetail(null)} />}
      <p className="footnote">
        <FileText size={13} />
        日志来源于当前工作区，按发生时间倒序排列。
      </p>
    </div>
  );
}
