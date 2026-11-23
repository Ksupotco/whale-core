import { useCallback, useEffect, useState } from 'react';
import {
  Check,
  CircleCheck,
  ListTodo,
  LoaderCircle,
  RefreshCw,
  Send,
  ShieldAlert,
  X,
} from 'lucide-react';
import { decideApproval, fetchApprovals, type ApprovalItem } from './api';
import { relativeTime } from './data';
import { Empty, PageHeading } from './ui';
import { useWorkspace } from './workspace';

type Decided = ApprovalItem & { decided: 'approve' | 'reject'; at: number };

export default function ApprovalsPage() {
  const { connection, notify, refresh } = useWorkspace();
  const [items, setItems] = useState<ApprovalItem[]>([]);
  const [decided, setDecided] = useState<Decided[]>([]);
  const [loading, setLoading] = useState(false);
  const [busyId, setBusyId] = useState('');
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    if (!connection) return;
    setLoading(true);
    setError('');
    try {
      setItems(await fetchApprovals(connection));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [connection]);
  useEffect(() => {
    void load();
  }, [load]);

  const decide = async (item: ApprovalItem, decision: 'approve' | 'reject') => {
    if (!connection) return;
    setBusyId(item.draft_id);
    try {
      await decideApproval(connection, item, decision);
      await load();
      void refresh(); // 同步 /status 的待审批计数（导航徽标）
      setDecided((list) => [{ ...item, decided: decision, at: Date.now() }, ...list].slice(0, 20));
      notify(
        decision === 'approve'
          ? '已批准，核心将按真实渠道尝试执行。'
          : '已拒绝，该动作不会被派发。',
      );
    } catch (e) {
      notify((e as Error).message, true);
    } finally {
      setBusyId('');
    }
  };

  return (
    <div className="page-enter">
      <PageHeading
        page="approvals"
        action={
          <button className="btn" disabled={loading} onClick={() => void load()}>
            <RefreshCw size={15} className={loading ? 'spin' : ''} />
            刷新队列
          </button>
        }
      />
      <div className="info-note approval-note">
        <ShieldAlert size={16} />
        <span>
          高风险任务与主动外发草稿会经策略层挂起。批准代表允许尝试执行，实际发送状态以回执为准。
        </span>
      </div>
      {error && (
        <p className="inline-error" role="alert">
          {error}
        </p>
      )}

      <section className="panel list-panel">
        <div className="panel-heading device-list-head">
          <h2>等待你确认（{items.length}）</h2>
          {connection && loading && <LoaderCircle size={15} className="spin muted" />}
        </div>
        <div className="approval-list">
          {items.map((item) => (
            <article className="approval-card" key={`${item.kind}-${item.draft_id}`}>
              <div className="approval-head">
                <span className={`tag ${item.kind === 'task' ? 'blue' : 'amber'}`}>
                  {item.kind === 'task' ? (
                    <>
                      <ListTodo size={11} /> 高风险任务
                    </>
                  ) : (
                    <>
                      <Send size={11} /> 主动外发草稿
                    </>
                  )}
                </span>
                {item.channel && <span className="tag neutral">{item.channel}</span>}
                <code className="approval-id">{item.draft_id}</code>
              </div>
              <p className="approval-text">{item.text || '（无正文）'}</p>
              <div className="approval-actions">
                <button
                  className="btn danger-ghost"
                  disabled={busyId === item.draft_id}
                  onClick={() => void decide(item, 'reject')}
                >
                  <X size={14} />
                  拒绝
                </button>
                <button
                  className="btn primary"
                  disabled={busyId === item.draft_id}
                  onClick={() => void decide(item, 'approve')}
                >
                  {busyId === item.draft_id ? (
                    <LoaderCircle size={14} className="spin" />
                  ) : (
                    <Check size={14} />
                  )}
                  批准并投递
                </button>
              </div>
            </article>
          ))}
          {!items.length && !loading && (
            <div className="approval-empty">
              <Empty
                title="当前没有待审批项"
                text="当鲸鱼需要外发主动消息或执行高风险任务时，会停在这里等你确认。"
              />
            </div>
          )}
          {connection && loading && !items.length && (
            <p className="muted padded">
              <LoaderCircle size={14} className="spin" /> 正在读取审批队列…
            </p>
          )}
        </div>
      </section>

      {decided.length > 0 && (
        <section className="panel list-panel">
          <div className="panel-heading device-list-head">
            <h2>本次会话已处理（{decided.length}）</h2>
            <span className="muted small-text">刷新页面后清空；历史以服务端事件与日志为准</span>
          </div>
          <div className="record-list">
            {decided.map((item) => (
              <div className="record-row decided-row" key={`decided-${item.draft_id}-${item.at}`}>
                <span className={`color-icon small ${item.decided === 'approve' ? 'mint' : 'neutral'}`}>
                  {item.decided === 'approve' ? <CircleCheck size={16} /> : <X size={15} />}
                </span>
                <span className="record-info">
                  <strong>{item.text || item.draft_id}</strong>
                  <small>
                    {item.kind === 'task' ? '高风险任务' : '主动外发草稿'}
                    {item.channel ? ` · ${item.channel}` : ''}
                  </small>
                </span>
                <span className={`tag ${item.decided === 'approve' ? 'mint' : 'neutral'}`}>
                  {item.decided === 'approve' ? '已批准' : '已拒绝'}
                </span>
                <span className="record-date">{relativeTime(item.at)}</span>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
