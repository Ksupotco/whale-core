import { useCallback, useEffect, useState } from 'react';
import {
  Ban,
  ChevronLeft,
  ChevronRight,
  Database,
  Download,
  LoaderCircle,
  Pencil,
  Pause,
  Play,
  Search,
  Trash2,
  Users,
  Vault,
} from 'lucide-react';
import {
  memoryBrowse,
  memoryCorrect,
  memoryDelete,
  memoryExpire,
  memoryPause,
  memorySubjects,
  request,
  searchMemories,
  type MemoryBrowseItem,
  type MemoryBrowseResult,
  type MemorySubjects,
} from './api';
import type { Connection } from './api';
import { download, relativeTime } from './data';
import { Banner, Button, Field, Input, Select, Tag, Textarea } from './ds';
import { Empty, Modal, PageHeading, SearchField } from './ui';
import { useWorkspace } from './workspace';

const KIND_NAMES: Record<string, string> = {
  episode: '情节',
  preference: '偏好',
  persona_continuity: '人格延续',
};
const kindName = (k: string) => KIND_NAMES[k] || k;

export default function MemoryPage() {
  const { connection, notify } = useWorkspace();
  const [view, setView] = useState<'subjects' | 'search'>('subjects');
  if (!connection) return null;
  return (
    <div className="page-enter">
      <PageHeading page="memory" />
      <div className="segment-control events-subtabs" role="tablist">
        <button
          role="tab"
          aria-selected={view === 'subjects'}
          className={view === 'subjects' ? 'active' : ''}
          onClick={() => setView('subjects')}
        >
          <Users size={14} />
          按主体浏览
        </button>
        <button
          role="tab"
          aria-selected={view === 'search'}
          className={view === 'search' ? 'active' : ''}
          onClick={() => setView('search')}
        >
          <Search size={14} />
          全局关键词搜索
        </button>
      </div>
      {view === 'subjects' ? (
        <SubjectsBrowser connection={connection} notify={notify} />
      ) : (
        <GlobalSearch connection={connection} notify={notify} />
      )}
    </div>
  );
}

// ---------------------------------------------------------------- 按主体浏览

function SubjectsBrowser({
  connection,
  notify,
}: {
  connection: Connection;
  notify: (t: string, e?: boolean) => void;
}) {
  const [subjects, setSubjects] = useState<MemorySubjects | null>(null);
  const [error, setError] = useState('');
  const [spaceId, setSpaceId] = useState('');
  const [spaceLabel, setSpaceLabel] = useState('');
  const [paused, setPaused] = useState(false);

  const load = useCallback(async () => {
    try {
      setSubjects(await memorySubjects(connection));
      setError('');
    } catch (e) {
      setError((e as Error).message);
    }
  }, [connection]);
  useEffect(() => {
    void load();
  }, [load]);

  const pick = (id: string, label: string, isPaused: boolean) => {
    setSpaceId(id);
    setSpaceLabel(label);
    setPaused(isPaused);
  };

  const togglePause = async (id: string, next: boolean) => {
    try {
      await memoryPause(connection, id, next);
      notify(next ? '空间已暂停提炼。' : '空间已恢复提炼。');
      await load();
      if (id === spaceId) setPaused(next);
    } catch (e) {
      notify((e as Error).message, true);
    }
  };

  const groups: { title: string; icon: typeof Users; items: { id: string; name: string; count: number; paused: boolean; note?: string }[] }[] = [];
  if (subjects) {
    groups.push({
      title: '主人',
      icon: Vault,
      items: subjects.owner.spaces.map((s) => ({
        id: s.space_id,
        name: s.name || s.space_id,
        count: s.record_count,
        paused: s.paused,
        note: s.scope_note,
      })),
    });
    if (subjects.members.length)
      groups.push({
        title: '群友',
        icon: Users,
        items: subjects.members.map((m) => ({
          id: m.space_id,
          name: m.principal_id,
          count: m.record_count,
          paused: m.paused,
          note: m.opted_out ? '已退出授权' : m.consent_active ? '授权有效' : undefined,
        })),
      });
    if (subjects.groups.length)
      groups.push({
        title: '群聊',
        icon: Users,
        items: subjects.groups.map((g) => ({
          id: g.space_id,
          name: g.name || g.space_id,
          count: g.record_count,
          paused: g.paused,
        })),
      });
    if (subjects.forks.length)
      groups.push({
        title: '副本空间',
        icon: Database,
        items: subjects.forks.map((f) => ({
          id: f.space_id,
          name: f.name || f.space_id,
          count: f.record_count,
          paused: f.paused,
        })),
      });
  }

  return (
    <div className="memory-browser">
      <aside className="panel memory-subjects">
        <div className="panel-heading">
          <h2>主体</h2>
          <button className="text-link" onClick={() => void load()}>
            刷新
          </button>
        </div>
        {error && <Banner tone="error">{error}</Banner>}
        {subjects?.owner.vault_present && (
          <p className="memory-vault-note">个人原始库（vault）在保护中：仅能经 Fork 向导创建受控副本后使用。</p>
        )}
        {groups.map((g) => (
          <div className="memory-subject-group" key={g.title}>
            <div className="memory-subject-title">{g.title}</div>
            {g.items.map((s) => (
              <div
                key={s.id}
                className={`memory-subject ${spaceId === s.id ? 'active' : ''}`}
              >
                <button className="memory-subject-main" onClick={() => pick(s.id, s.name, s.paused)}>
                  <span className="record-info">
                    <strong>{s.name}</strong>
                    <small>
                      {s.count} 条{s.note ? ` · ${s.note}` : ''}
                    </small>
                  </span>
                  {s.paused && <Tag tone="amber">已暂停</Tag>}
                </button>
                <button
                  className="ds-icon-btn"
                  aria-label={s.paused ? `恢复 ${s.name}` : `暂停 ${s.name}`}
                  title={s.paused ? '恢复' : '暂停'}
                  onClick={() => void togglePause(s.id, !s.paused)}
                >
                  {s.paused ? <Play size={14} /> : <Pause size={14} />}
                </button>
              </div>
            ))}
            {!g.items.length && <p className="muted small-text padded">暂无</p>}
          </div>
        ))}
        {subjects !== null && subjects.legacy_unscoped_records > 0 && (
          <p className="muted small-text padded">
            另有 {subjects.legacy_unscoped_records} 条旧版未归域记录（可用全局搜索触达）。
          </p>
        )}
      </aside>
      <section className="panel memory-records">
        {!spaceId ? (
          <Empty title="选择一个主体" text="左侧选择主人、群友、群聊或副本空间，浏览其中的记忆记录。" />
        ) : (
          <RecordsView
            key={spaceId}
            connection={connection}
            spaceId={spaceId}
            spaceLabel={spaceLabel}
            paused={paused}
            notify={notify}
            onChanged={load}
          />
        )}
      </section>
    </div>
  );
}

function RecordsView({
  connection,
  spaceId,
  spaceLabel,
  paused,
  notify,
  onChanged,
}: {
  connection: Connection;
  spaceId: string;
  spaceLabel: string;
  paused: boolean;
  notify: (t: string, e?: boolean) => void;
  onChanged: () => void;
}) {
  const [kind, setKind] = useState('');
  const [q, setQ] = useState('');
  const [includeSuperseded, setIncludeSuperseded] = useState(false);
  const [page, setPage] = useState(0);
  const [result, setResult] = useState<MemoryBrowseResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [detail, setDetail] = useState<MemoryBrowseItem | null>(null);
  const size = 10;

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      setResult(
        await memoryBrowse(connection, {
          space_id: spaceId,
          kind: kind || undefined,
          q: q.trim() || undefined,
          include_superseded: includeSuperseded,
          limit: size,
          offset: page * size,
        }),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [connection, spaceId, kind, q, includeSuperseded, page]);
  useEffect(() => {
    const t = setTimeout(() => void load(), 200);
    return () => clearTimeout(t);
  }, [load]);

  const totalPages = result ? Math.max(1, Math.ceil(result.total / size)) : 1;
  return (
    <>
      <div className="panel-heading">
        <h2>{spaceLabel}</h2>
        {paused && <Tag tone="amber">已暂停提炼</Tag>}
      </div>
      {result?.metadata_only && (
        <Banner tone="info">该空间为受限视图：仅展示元数据，摘要已脱敏。</Banner>
      )}
      <div className="memory-filters">
        <Select aria-label="类型" value={kind} onChange={(e) => { setKind(e.target.value); setPage(0); }}>
          <option value="">全部类型</option>
          <option value="episode">情节</option>
          <option value="preference">偏好</option>
          <option value="persona_continuity">人格延续</option>
        </Select>
        <Input
          aria-label="记录内搜索"
          placeholder="搜索摘要…"
          value={q}
          onChange={(e) => { setQ(e.target.value); setPage(0); }}
        />
        <label className="memory-superseded">
          <input
            type="checkbox"
            checked={includeSuperseded}
            onChange={(e) => { setIncludeSuperseded(e.target.checked); setPage(0); }}
          />
          含已取代
        </label>
      </div>
      {error && <Banner tone="error">{error}</Banner>}
      <div className="record-list">
        {loading && (
          <p className="muted padded">
            <LoaderCircle size={14} className="spin" /> 正在读取…
          </p>
        )}
        {!loading && result?.items.map((m) => (
          <button className="record-row" key={m.key} onClick={() => setDetail(m)}>
            <span className="color-icon mint">
              <Database size={16} />
            </span>
            <span className="record-info">
              <strong>{m.summary || `（受限记录 ${m.key.slice(0, 16)}…）`}</strong>
              <small>
                {kindName(m.kind)} · 置信 {m.confidence.toFixed(2)} · {m.source || '未知来源'}
                {m.source_group ? ` · ${m.source_group}` : ''}
              </small>
            </span>
            {m.superseded && <Tag tone="slate">已取代</Tag>}
            {m.restricted && <Tag tone="amber">受限</Tag>}
            <span className="record-date">
              {m.created_at ? relativeTime(new Date(m.created_at).getTime()) : '—'}
            </span>
            <ChevronRight size={15} />
          </button>
        ))}
        {!loading && result && !result.items.length && (
          <Empty title="没有匹配的记录" text="调整筛选条件，或换一个主体看看。" />
        )}
      </div>
      {result && result.total > 0 && (
        <div className="pagination">
          <span>
            共 <b>{result.total}</b> 条记录
          </span>
          <div>
            <button className="icon-button" disabled={page <= 0} aria-label="上一页" onClick={() => setPage(page - 1)}>
              <ChevronLeft size={16} />
            </button>
            <span>
              {page + 1} / {totalPages}
            </span>
            <button className="icon-button" disabled={page + 1 >= totalPages} aria-label="下一页" onClick={() => setPage(page + 1)}>
              <ChevronRight size={16} />
            </button>
          </div>
        </div>
      )}
      {detail && (
        <RecordDetail
          connection={connection}
          item={detail}
          onClose={() => setDetail(null)}
          notify={notify}
          onChanged={() => {
            setDetail(null);
            void load();
            onChanged();
          }}
        />
      )}
    </>
  );
}

function RecordDetail({
  connection,
  item,
  onClose,
  notify,
  onChanged,
}: {
  connection: Connection;
  item: MemoryBrowseItem;
  onClose: () => void;
  notify: (t: string, e?: boolean) => void;
  onChanged: () => void;
}) {
  const [correcting, setCorrecting] = useState(false);
  const [newSummary, setNewSummary] = useState(item.summary ?? '');
  const [confirm, setConfirm] = useState<'expire' | 'delete' | null>(null);
  const [busy, setBusy] = useState(false);
  const act = async (op: 'correct' | 'expire' | 'delete') => {
    setBusy(true);
    try {
      if (op === 'correct') await memoryCorrect(connection, item.key, newSummary.trim());
      if (op === 'expire') await memoryExpire(connection, item.key);
      if (op === 'delete') await memoryDelete(connection, item.key);
      notify(op === 'correct' ? '记忆已纠正（旧版本保留为已取代）。' : op === 'expire' ? '记忆已过期。' : '记忆已删除。');
      onChanged();
    } catch (e) {
      notify((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title="记忆详情" wide onClose={onClose}>
      <span className="tag mint">{kindName(item.kind)}</span>
      {item.superseded && <span className="tag neutral"> 已取代</span>}
      <h3 className="detail-heading">{item.summary || `（受限记录 ${item.key.slice(0, 16)}…）`}</h3>
      <div className="detail-meta">
        <span>记录键</span>
        <code>{item.key}</code>
      </div>
      <div className="detail-meta">
        <span>来源</span>
        {item.source || '—'}
        {item.source_group ? ` · ${item.source_group}` : ''}
      </div>
      <div className="detail-meta">
        <span>置信度</span>
        {item.confidence.toFixed(2)}
      </div>
      <div className="detail-meta">
        <span>创建时间</span>
        {item.created_at ? new Date(item.created_at).toLocaleString('zh-CN') : '—'}
      </div>
      {item.last_confirmed && (
        <div className="detail-meta">
          <span>最近确认</span>
          {new Date(item.last_confirmed).toLocaleString('zh-CN')}
        </div>
      )}
      {item.expires_at && (
        <div className="detail-meta">
          <span>过期时间</span>
          {new Date(item.expires_at).toLocaleString('zh-CN')}
        </div>
      )}
      {item.tags && item.tags.length > 0 && (
        <div className="detail-meta">
          <span>标签</span>
          {item.tags.join('、')}
        </div>
      )}
      {item.source_message_ids && item.source_message_ids.length > 0 && (
        <div className="detail-meta">
          <span>来源消息</span>
          {item.source_message_ids.length} 条
        </div>
      )}
      {correcting ? (
        <div className="form-stack">
          <Field label="纠正为新摘要" hint="纠正后旧版本标记为已取代，审计可追溯。">
            <Textarea rows={3} value={newSummary} onChange={(e) => setNewSummary(e.target.value)} aria-label="新摘要" />
          </Field>
          <div className="form-actions">
            <Button variant="ghost" size="sm" onClick={() => setCorrecting(false)}>
              取消
            </Button>
            <Button variant="primary" size="sm" busy={busy} disabled={!newSummary.trim()} onClick={() => void act('correct')}>
              保存纠正
            </Button>
          </div>
        </div>
      ) : confirm ? (
        <div className="form-actions">
          <Button variant="ghost" size="sm" onClick={() => setConfirm(null)}>
            返回
          </Button>
          <Button variant="danger" size="sm" busy={busy} onClick={() => void act(confirm)}>
            {confirm === 'expire' ? '确认过期' : '确认删除'}
          </Button>
        </div>
      ) : (
        <div className="form-actions">
          {!item.restricted && (
            <Button variant="secondary" size="sm" icon={<Pencil size={13} />} onClick={() => setCorrecting(true)}>
              纠正
            </Button>
          )}
          <Button variant="secondary" size="sm" icon={<Ban size={13} />} onClick={() => setConfirm('expire')}>
            过期
          </Button>
          <Button variant="danger" size="sm" icon={<Trash2 size={13} />} onClick={() => setConfirm('delete')}>
            删除
          </Button>
        </div>
      )}
    </Modal>
  );
}

// ---------------------------------------------------------------- 全局关键词搜索

function GlobalSearch({
  connection,
  notify,
}: {
  connection: Connection;
  notify: (t: string, e?: boolean) => void;
}) {
  const { refresh } = useWorkspace();
  const [q, setQ] = useState('');
  const [hits, setHits] = useState<{ id: string; title: string; body: string; category: string; at: number }[]>([]);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState('');
  const [detail, setDetail] = useState<(typeof hits)[number] | null>(null);
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!q.trim()) {
      setHits([]);
      return;
    }
    let active = true;
    setSearching(true);
    setError('');
    const t = setTimeout(() => {
      searchMemories(connection, q.trim())
        .then((h) => active && setHits(h))
        .catch((e) => active && setError(e.message))
        .finally(() => active && setSearching(false));
    }, 260);
    return () => {
      active = false;
      clearTimeout(t);
    };
  }, [connection, q]);

  const exportMemories = async () => {
    try {
      const content = await request<string>(connection, '/memory/export', 'POST', undefined, true);
      download('whale-memories.jsonl', content, 'application/x-ndjson');
      notify('记忆导出文件已生成。');
    } catch (e) {
      notify((e as Error).message, true);
    }
  };
  const remove = async () => {
    if (!detail) return;
    setBusy(true);
    try {
      const r = await request<{ deleted: boolean }>(
        connection,
        `/memory/${encodeURIComponent(detail.id)}`,
        'DELETE',
      );
      if (!r.deleted) throw new Error('服务未删除这条记忆。');
      await refresh();
      setDetail(null);
      setConfirm(false);
      setQ('');
      notify('记忆已删除。');
    } catch (e) {
      notify((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="panel list-panel">
      <div className="toolbar">
        <SearchField value={q} onChange={setQ} placeholder="搜索记忆标题或内容..." />
        <Button variant="secondary" size="sm" icon={<Download size={14} />} onClick={exportMemories}>
          导出记忆
        </Button>
      </div>
      {error && <Banner tone="error">{error}</Banner>}
      <div className="record-list">
        {hits.map((m) => (
          <button className="record-row" key={m.id} onClick={() => setDetail(m)}>
            <span className="color-icon mint">
              <Database size={17} />
            </span>
            <span className="record-info">
              <strong>{m.title}</strong>
              <small>{m.body}</small>
            </span>
            <span className="tag neutral">{m.category}</span>
            <span className="record-date">{relativeTime(m.at)}</span>
            <ChevronRight size={15} />
          </button>
        ))}
        {!hits.length && (
          <Empty
            title={searching ? '正在搜索...' : q ? '暂无相关记忆' : '输入关键词开始搜索'}
            text="全局搜索跨主体与空间检索服务端记忆。"
          />
        )}
      </div>
      {detail && (
        <Modal title={confirm ? '确认删除记忆' : '记忆详情'} onClose={() => !busy && setDetail(null)}>
          <span className="tag mint">{detail.category}</span>
          <h3 className="detail-heading">{detail.title}</h3>
          <p className="detail-text">{confirm ? '删除后无法恢复。确定删除这条记忆吗？' : detail.body}</p>
          <div className="form-actions">
            {confirm ? (
              <>
                <Button variant="ghost" size="sm" disabled={busy} onClick={() => setConfirm(false)}>
                  保留记忆
                </Button>
                <Button variant="danger" size="sm" busy={busy} onClick={remove}>
                  确认删除
                </Button>
              </>
            ) : (
              <Button variant="danger" size="sm" icon={<Trash2 size={13} />} onClick={() => setConfirm(true)}>
                删除
              </Button>
            )}
          </div>
        </Modal>
      )}
    </section>
  );
}
