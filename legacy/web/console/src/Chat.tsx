import { useEffect, useRef, useState } from 'react';
import {
  ArrowUp,
  Check,
  Copy,
  Database,
  History,
  ListTodo,
  LoaderCircle,
  MessageSquare,
  Plus,
  Sparkles,
  Trash2,
  X,
} from 'lucide-react';
import { uid, type Message } from './data';
import { request } from './api';
import { Modal, PageHeading, WhaleLogo } from './ui';
import { useWorkspace } from './workspace';

export default function ChatPage() {
  const { data, update, connection, notify, refresh } = useWorkspace();
  const [selected, setSelected] = useState('console');
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [history, setHistory] = useState(false);
  const [deleteThread, setDeleteThread] = useState<(typeof data.threads)[number] | null>(null);
  const [copied, setCopied] = useState('');
  const lock = useRef(false);
  const end = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const thread = data.threads.find((t) => t.id === selected);
  const messages = thread?.messages || [];
  useEffect(() => {
    const el = end.current?.parentElement;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
  }, [messages.length, selected]);
  const fresh = () => {
    setSelected('new');
    setDraft('');
    setError('');
    setHistory(false);
    input.current?.focus();
  };
  const send = async (value = draft) => {
    const text = value.trim();
    if (!text || lock.current || !connection) return;
    lock.current = true;
    setBusy(true);
    setError('');
    setDraft('');
    const id = selected === 'new' ? `console-${uid()}` : selected;
    const userMessage: Message = { id: uid(), role: 'user', text };
    setSelected(id);
    update((s) => ({
      ...s,
      threads: s.threads.some((t) => t.id === id)
        ? s.threads.map((t) => (t.id === id ? { ...t, messages: [...t.messages, userMessage] } : t))
        : [{ id, title: text.slice(0, 24), messages: [userMessage] }, ...s.threads],
    }));
    try {
      const result = await request<{ reply: string; status: string }>(connection, '/chat', 'POST', {
        text,
        conversation_id: id,
      });
      if (!result.reply) throw new Error(`服务未返回回复，状态：${result.status}`);
      const message: Message = { id: uid(), role: 'assistant', text: result.reply };
      update((s) => ({
        ...s,
        threads: s.threads.map((t) => (t.id === id ? { ...t, messages: [...t.messages, message] } : t)),
      }));
      void refresh().catch(() => notify('回复已收到，服务概览暂未刷新。', true));
    } catch (e) {
      setError((e as Error).message);
      setDraft(text);
    } finally {
      setBusy(false);
      lock.current = false;
      input.current?.focus();
    }
  };
  const remove = async () => {
    if (!deleteThread || !connection) return;
    try {
      await request(connection, `/conversations/${encodeURIComponent(deleteThread.id)}`, 'DELETE');
      update((s) => ({ ...s, threads: s.threads.filter((t) => t.id !== deleteThread.id) }));
      if (selected === deleteThread.id) fresh();
      setDeleteThread(null);
      notify('会话已删除。');
    } catch (e) {
      notify((e as Error).message, true);
    }
  };
  return (
    <div className="page-enter">
      <PageHeading
        page="chat"
        action={
          <>
            <button className="btn" onClick={() => setHistory(true)} disabled={busy}>
              <History size={15} />
              历史会话
            </button>
            <button className="btn primary" disabled={busy} onClick={fresh}>
              <Plus size={15} />
              新对话
            </button>
          </>
        }
      />
      <section className="chat-surface">
        <div className="chat-top">
          <span>
            <i className="status-dot" />
            {thread?.title || '新的对话'}
          </span>
          <small>Whale Core 服务</small>
        </div>
        {!messages.length ? (
          <div className="chat-welcome">
            <div className="whale-orb">
              <WhaleLogo size={58} />
            </div>
            <div className="section-eyebrow">WHALE COMPANION</div>
            <h2>一起想点什么？</h2>
            <p>从一个问题开始，和鲸鱼展开对话。</p>
            <div className="prompt-options">
              {[
                { icon: Sparkles, text: '今天有什么值得关注的动态？' },
                { icon: Database, text: '回顾最近保存的记忆' },
                { icon: ListTodo, text: '当前有哪些待办任务？' },
              ].map((p) => (
                <button key={p.text} onClick={() => send(p.text)}>
                  <p.icon size={15} />
                  {p.text}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="chat-messages">
            {messages.map((m) => (
              <div key={m.id} className={`message ${m.role}`}>
                <span className="message-avatar">
                  {m.role === 'assistant' ? <WhaleLogo size={25} /> : 'W'}
                </span>
                <div className="message-content">
                  <b>{m.role === 'assistant' ? 'Whale Core' : '你'}</b>
                  <p>{m.text}</p>
                  {m.role === 'assistant' && (
                    <button
                      className="copy-button"
                      aria-label="复制回复"
                      onClick={async () => {
                        try {
                          await navigator.clipboard.writeText(m.text);
                          setCopied(m.id);
                          setTimeout(() => setCopied(''), 1800);
                        } catch {
                          notify('无法使用剪贴板，请选中文字复制。', true);
                        }
                      }}
                    >
                      {copied === m.id ? <Check size={13} /> : <Copy size={13} />}
                    </button>
                  )}
                </div>
              </div>
            ))}
            {busy && (
              <div className="thinking">
                <LoaderCircle className="spin" size={15} />
                等待服务回复...
              </div>
            )}
            <div ref={end} />
          </div>
        )}
        <div className="composer-area">
          {error && (
            <div className="inline-error">
              {error}
              <button aria-label="关闭错误提示" onClick={() => setError('')}>
                <X size={14} />
              </button>
            </div>
          )}
          <form
            className="composer"
            onSubmit={(e) => {
              e.preventDefault();
              send();
            }}
          >
            <textarea
              ref={input}
              aria-label="对话消息"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              maxLength={32000}
              placeholder="给鲸鱼发一条消息..."
              rows={2}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  send();
                }
              }}
            />
            <div className="composer-tools">
              <span>
                <MessageSquare size={12} />
                模型对话
              </span>
              <button aria-label="发送消息" className="send-button" disabled={!draft.trim() || busy}>
                {busy ? <LoaderCircle size={17} className="spin" /> : <ArrowUp size={18} />}
              </button>
            </div>
          </form>
          <p className="composer-note">Enter 发送，Shift + Enter 换行</p>
        </div>
      </section>
      {history && (
        <Modal title="历史会话" onClose={() => setHistory(false)}>
          <div className="history-list">
            {data.threads.map((t) => (
              <div key={t.id}>
                <button
                  onClick={() => {
                    setSelected(t.id);
                    setHistory(false);
                    setError('');
                  }}
                >
                  <MessageSquare size={17} />
                  <span>
                    <strong>{t.title}</strong>
                    <small>{t.messages.length} 条消息</small>
                  </span>
                </button>
                <button
                  className="icon-button"
                  aria-label={`删除会话 ${t.title}`}
                  onClick={() => {
                    setHistory(false);
                    setDeleteThread(t);
                  }}
                >
                  <Trash2 size={15} />
                </button>
              </div>
            ))}
            {!data.threads.length && <p className="muted">尚未创建会话。</p>}
          </div>
        </Modal>
      )}
      {deleteThread && (
        <Modal title="删除会话" onClose={() => setDeleteThread(null)}>
          <p className="detail-text">
            确定删除「{deleteThread.title}」及其消息记录吗？此操作无法撤销。
          </p>
          <div className="form-actions">
            <button className="btn" onClick={() => setDeleteThread(null)}>
              取消
            </button>
            <button className="btn danger" onClick={remove}>
              确认删除
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}
