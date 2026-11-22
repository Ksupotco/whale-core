import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ArrowDownLeft,
  ArrowRight,
  ArrowUpRight,
  Bell,
  BookOpen,
  Box,
  Check,
  CheckCheck,
  ChevronRight,
  ChevronsUpDown,
  CircleAlert,
  CircleCheck,
  Command,
  ExternalLink,
  Layers2,
  Menu,
  PanelLeft,
  Search,
  Settings2,
  X,
} from 'lucide-react';
import { pages, relativeTime, type Activity, type Page } from './data';
import { ActivityIcon, Empty, Modal, pageIcons, WhaleLogo } from './ui';
import { WorkspaceProvider, useWorkspace } from './workspace';
import Dashboard, { EventDetail } from './Dashboard';
import { LogsPage, TasksPage } from './Management';
import MemoryPage from './Memory';
import PluginsPage from './Plugins';
import ChatPage from './Chat';
import { WorkflowsPage } from './Tools';
import DevicesPage from './Devices';
import ApprovalsPage from './Approvals';
import EventsPage from './Events';
import ModelsPage from './Models';
import ProvidersPage from './Providers';
import PersonasPage from './Personas';
import SettingsPage from './Settings';
import SetupPage from './Setup';

const groups: { label: string; items: Page[] }[] = [
  { label: '中枢', items: ['overview', 'chat', 'memory'] },
  { label: '能力中心', items: ['models', 'providers', 'personas', 'plugins', 'workflows', 'tasks', 'approvals'] },
  { label: '设备与事件', items: ['devices', 'events'] },
  { label: '系统管理', items: ['logs', 'settings'] },
];
// 窄屏底部导航（buocuo/mobile 移动适配并入）：五个主目的地 + 徽标
const bottomNav: Page[] = ['overview', 'chat', 'tasks', 'devices', 'approvals'];
function route() {
  const [path, params] = location.hash.replace(/^#\/?/, '').split('?');
  return {
    page: Object.prototype.hasOwnProperty.call(pages, path) ? (path as Page) : ('overview' as Page),
    query: new URLSearchParams(params).get('q') || '',
  };
}
function CommandPalette({
  close,
  jump,
}: {
  close: () => void;
  jump: (p: Page, text?: string) => void;
}) {
  const { data } = useWorkspace();
  const [q, setQ] = useState('');
  const input = useRef<HTMLInputElement>(null);
  const [index, setIndex] = useState(0);
  useEffect(() => {
    input.current?.focus();
  }, []);
  const matches = (Object.keys(pages) as Page[])
    .filter((p) => `${pages[p].name} ${pages[p].en}`.toLowerCase().includes(q.trim().toLowerCase()))
    .map((p) => ({ page: p, label: pages[p].name, meta: pages[p].en, query: '' }));
  if (q.trim()) {
    matches.push(
      ...data.memories
        .filter((m) => m.title.includes(q.trim()))
        .slice(0, 3)
        .map((m) => ({ page: 'memory' as Page, label: m.title, meta: '记忆', query: m.title })),
    );
    matches.push(
      ...data.tasks
        .filter((t) => t.title.includes(q.trim()))
        .slice(0, 3)
        .map((t) => ({ page: 'tasks' as Page, label: t.title, meta: '任务', query: t.title })),
    );
  }
  const open = (i: number) => {
    if (!matches[i]) return;
    jump(matches[i].page, matches[i].query);
    close();
  };
  return (
    <Modal title="搜索工作空间" className="command-dialog" onClose={close}>
      <div className="command-input">
        <Search size={19} />
        <input
          ref={input}
          autoFocus
          placeholder="搜索页面、记忆或任务..."
          aria-label="全局搜索"
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setIndex(0);
          }}
          onKeyDown={(e) => {
            if (e.key === 'ArrowDown') {
              e.preventDefault();
              setIndex((i) => Math.min(i + 1, matches.length - 1));
            }
            if (e.key === 'ArrowUp') {
              e.preventDefault();
              setIndex((i) => Math.max(0, i - 1));
            }
            if (e.key === 'Enter') {
              e.preventDefault();
              open(index);
            }
          }}
        />
      </div>
      <div className="command-caption">{q ? '搜索结果' : '快速导航'}</div>
      <div className="command-results">
        {matches.map((r, i) => {
          const Icon = pageIcons[r.page];
          return (
            <button
              key={`${r.page}-${i}`}
              className={index === i ? 'selected' : ''}
              onMouseEnter={() => setIndex(i)}
              onClick={() => open(i)}
            >
              <Icon size={17} />
              <strong>{r.label}</strong>
              <small>{r.meta}</small>
              <ChevronRight size={13} />
            </button>
          );
        })}
        {!matches.length && (
          <Empty title="没有找到匹配结果" text="可以搜索页面名称、记忆标题或任务名称。" />
        )}
      </div>
      <div className="command-footer">
        <span>
          <kbd>↑</kbd>
          <kbd>↓</kbd>选择
        </span>
        <span>
          <kbd>Enter</kbd>打开
        </span>
        <span>
          <kbd>Esc</kbd>关闭
        </span>
      </div>
    </Modal>
  );
}
function Shell({
  page,
  query,
  jump,
}: {
  page: Page;
  query: string;
  jump: (p: Page, text?: string) => void;
}) {
  const { data, connection, navigate, status, phase } = useWorkspace();
  const [collapsed, setCollapsed] = useState(false);
  const [drawer, setDrawer] = useState(false);
  const [small, setSmall] = useState(() => window.matchMedia('(max-width: 820px)').matches);
  const [search, setSearch] = useState(false);
  const [popover, setPopover] = useState<'notifications' | 'profile' | null>(null);
  const [detail, setDetail] = useState<Activity | null>(null);
  const popRef = useRef<HTMLDivElement>(null);
  const [readAt, setReadAt] = useState(0);
  const [workspaceMenu, setWorkspaceMenu] = useState(false);
  const pending = data.tasks.filter((t) => ['pending', 'pending_approval'].includes(t.status)).length;
  const pendingApprovals = connection ? (status?.drafts_pending ?? 0) : 0;
  const unread = data.events.filter((e) => e.at > readAt).length;
  useEffect(() => {
    const media = window.matchMedia('(max-width: 820px)');
    const changed = () => setSmall(media.matches);
    media.addEventListener('change', changed);
    return () => media.removeEventListener('change', changed);
  }, []);
  useEffect(() => {
    setDrawer(false);
    setPopover(null);
  }, [page, query]);
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setSearch((s) => !s);
        setPopover(null);
        setDrawer(false);
      }
      if (e.key === 'Escape') {
        setPopover(null);
        setDrawer(false);
      }
    };
    window.addEventListener('keydown', key);
    return () => window.removeEventListener('keydown', key);
  }, []);
  useEffect(() => {
    if (!popover) return;
    const click = (e: MouseEvent) => {
      if (!popRef.current?.contains(e.target as Node)) setPopover(null);
    };
    document.addEventListener('mousedown', click);
    return () => document.removeEventListener('mousedown', click);
  }, [popover]);
  useEffect(() => {
    if (drawer && small) {
      const prev = document.body.style.overflow;
      document.body.style.overflow = 'hidden';
      document.querySelector<HTMLAnchorElement>('.sidebar [aria-current="page"]')?.focus();
      return () => {
        document.body.style.overflow = prev;
        document.querySelector<HTMLButtonElement>('.sidebar-toggle')?.focus();
      };
    }
  }, [drawer, small]);
  const content = () => {
    switch (page) {
      case 'overview':
        return <Dashboard />;
      case 'chat':
        return <ChatPage />;
      case 'memory':
        return <MemoryPage />;
      case 'models':
        return <ModelsPage />;
      case 'providers':
        return <ProvidersPage />;
      case 'personas':
        return <PersonasPage />;
      case 'tasks':
        return <TasksPage initialSearch={query} />;
      case 'plugins':
        return <PluginsPage />;
      case 'workflows':
        return <WorkflowsPage />;
      case 'devices':
        return <DevicesPage />;
      case 'approvals':
        return <ApprovalsPage />;
      case 'events':
        return <EventsPage />;
      case 'logs':
        return <LogsPage />;
      case 'settings':
        return <SettingsPage />;
    }
  };
  if (phase === 'boot')
    return (
      <div className="boot-splash">
        <WhaleLogo size={46} />
        <p>正在连接 Whale Core…</p>
      </div>
    );
  if (phase === 'setup') return <SetupPage />;
  return (
    <div className={`app-shell ${collapsed ? 'sidebar-collapsed' : ''} ${drawer ? 'drawer-open' : ''}`}>
      <a
        className="skip-link"
        href="#main-content"
        onClick={(e) => {
          e.preventDefault();
          document.getElementById('main-content')?.focus();
        }}
      >
        跳至主要内容
      </a>
      {drawer && small && (
        <button className="sidebar-shade" aria-label="关闭导航菜单" onClick={() => setDrawer(false)} />
      )}
      <aside className="sidebar" inert={small && !drawer} aria-label="主导航">
        <a
          className="brand"
          href="#overview"
          aria-label="Whale Core 首页"
          onClick={() => setDrawer(false)}
        >
          <WhaleLogo size={43} />
          <span className="brand-text">
            <strong>Whale Core</strong>
            <small>INTELLIGENCE CONSOLE</small>
          </span>
        </a>
        <div className="side-main">
          {groups.map((g) => (
            <div className="nav-group" key={g.label}>
              <div className="nav-group-title">{g.label}</div>
              <nav aria-label={g.label}>
                {g.items.map((p) => {
                  const Icon = pageIcons[p];
                  return (
                    <a
                      key={p}
                      href={`#${p}`}
                      onClick={() => setDrawer(false)}
                      className={`nav-item ${page === p ? 'active' : ''}`}
                      aria-current={page === p ? 'page' : undefined}
                      title={pages[p].name}
                    >
                      <Icon size={18} strokeWidth={1.65} />
                      <span>{pages[p].name}</span>
                      {p === 'tasks' && pending > 0 && <small className="nav-count">{pending}</small>}
                      {p === 'approvals' && pendingApprovals > 0 && (
                        <small className="nav-count">{pendingApprovals}</small>
                      )}
                      {page === p && <i className="nav-active-point" />}
                    </a>
                  );
                })}
              </nav>
            </div>
          ))}
        </div>
        <div className="side-bottom">
          <a
            className="docs-link"
            href="https://github.com/amwangfan/whale-core/blob/main/docs/README.md"
            target="_blank"
            rel="noreferrer"
            title="使用文档"
          >
            <BookOpen size={16} />
            <span>使用文档</span>
            <ArrowUpRight size={13} />
          </a>
          <button
            className="workspace-button"
            onClick={() => {
              setDrawer(false);
              setWorkspaceMenu(true);
            }}
            title="切换工作区"
          >
            <span className="workspace-symbol">
              <Box size={20} strokeWidth={1.55} />
            </span>
            <span>
              <strong>{data.name}</strong>
              <small>
                <i className="status-dot" />
                服务已连接
              </small>
            </span>
            <ChevronsUpDown size={13} />
          </button>
        </div>
      </aside>
      <div className="main-shell" inert={small && drawer}>
        <header className="topbar">
          <div className="breadcrumb">
            <button
              className="icon-button sidebar-toggle"
              aria-label={small ? '打开导航菜单' : collapsed ? '展开侧栏' : '收起侧栏'}
              aria-expanded={small ? drawer : !collapsed}
              onClick={() => (small ? setDrawer(!drawer) : setCollapsed(!collapsed))}
            >
              {small ? <Menu size={18} /> : <PanelLeft size={17} />}
            </button>
            <i className="vertical-rule" />
            <span className="breadcrumb-root">工作空间</span>
            <span className="breadcrumb-slash">/</span>
            <strong>{pages[page].name}</strong>
          </div>
          <div className="header-tools" ref={popRef}>
            <button className="header-search" onClick={() => setSearch(true)} aria-label="搜索工作空间">
              <Search size={15} />
              <span>搜索工作空间...</span>
              <kbd>
                <Command size={10} />K
              </kbd>
            </button>
            <span className="header-status">
              <i className="status-dot" />
              服务在线
            </span>
            <div className="notification-anchor">
              <button
                className={`icon-button bell ${popover === 'notifications' ? 'selected' : ''}`}
                aria-label="查看通知"
                aria-expanded={popover === 'notifications'}
                onClick={() => setPopover((s) => (s === 'notifications' ? null : 'notifications'))}
              >
                <Bell size={17} />
                {unread > 0 && <i />}
              </button>
            </div>
            <i className="vertical-rule" />
            <button
              className="avatar"
              aria-label="工作区账户菜单"
              aria-expanded={popover === 'profile'}
              onClick={() => setPopover((s) => (s === 'profile' ? null : 'profile'))}
            >
              W
            </button>
            {popover === 'notifications' && (
              <div className="header-popover notification-popover">
                <div className="popover-heading">
                  <h3>工作区通知</h3>
                  <button
                    className="text-link"
                    disabled={!unread}
                    onClick={() => setReadAt(Date.now())}
                  >
                    <CheckCheck size={13} />
                    {unread ? '全部已读' : '已全部阅读'}
                  </button>
                </div>
                {data.events.slice(0, 4).map((e) => (
                  <button
                    className="notification-row"
                    key={e.id}
                    onClick={() => {
                      setDetail(e);
                      setPopover(null);
                      setReadAt(Date.now());
                    }}
                  >
                    <ActivityIcon kind={e.kind} small />
                    <span>
                      <strong>{e.title}</strong>
                      <small>{relativeTime(e.at)}</small>
                    </span>
                    {e.at > readAt && <i className="unread-dot" />}
                  </button>
                ))}
                {!data.events.length && <p className="muted padded">暂无通知。</p>}
                <button
                  className="popover-footer"
                  onClick={() => {
                    setPopover(null);
                    navigate('logs');
                  }}
                >
                  查看运行日志
                  <ArrowRight size={13} />
                </button>
              </div>
            )}
            {popover === 'profile' && (
              <div className="header-popover profile-popover">
                <small className="section-eyebrow">WORKSPACE PROFILE</small>
                <h3>{data.name}</h3>
                <p>已连接 Whale Core</p>
                <button
                  onClick={() => {
                    setPopover(null);
                    navigate('settings');
                  }}
                >
                  <Settings2 size={15} />
                  工作区设置
                  <ChevronRight size={12} />
                </button>
                <a href="https://github.com/amwangfan/whale-core" target="_blank" rel="noreferrer">
                  <BookOpen size={15} />
                  项目仓库
                  <ExternalLink size={12} />
                </a>
              </div>
            )}
          </div>
        </header>
        <main
          id="main-content"
          tabIndex={-1}
          className="main-content"
          key={`${page}-${query}-${connection?.origin || 'local'}`}
        >
          {content()}
        </main>
      </div>
      <nav className="bottom-nav" aria-label="快捷导航">
        {bottomNav.map((p) => {
          const Icon = pageIcons[p];
          const badge =
            p === 'tasks' ? pending : p === 'approvals' ? pendingApprovals : 0;
          return (
            <a
              key={p}
              href={`#${p}`}
              className={page === p ? 'active' : ''}
              aria-current={page === p ? 'page' : undefined}
              onClick={() => setDrawer(false)}
            >
              <span className="bottom-nav-icon">
                <Icon size={20} strokeWidth={1.8} />
                {badge > 0 && <i aria-label={`${badge} 项待处理`} />}
              </span>
              <span>{pages[p].short}</span>
            </a>
          );
        })}
      </nav>
      {search && <CommandPalette close={() => setSearch(false)} jump={jump} />}{' '}
      {detail && <EventDetail event={detail} onClose={() => setDetail(null)} />}
      {workspaceMenu && (
        <Modal title="工作区" onClose={() => setWorkspaceMenu(false)}>
          <div className="workspace-summary">
            <span className="workspace-symbol">
              <Layers2 size={23} />
            </span>
            <div>
              <strong>{data.name}</strong>
              <small>{connection?.origin}</small>
            </div>
            <Check size={17} className="green-text" />
          </div>
          <div className="detail-meta">
            <span>当前模式</span>
            真实服务
          </div>
          <div className="detail-meta">
            <span>核心版本</span>v{data.version}
          </div>
          <div className="form-actions">
            <button
              className="btn primary"
              onClick={() => {
                setWorkspaceMenu(false);
                navigate('settings');
              }}
            >
              <ArrowDownLeft size={15} />
              管理服务会话
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}
export default function App() {
  const [locationState, setLocationState] = useState(route);
  const [toast, setToast] = useState<{ text: string; error: boolean; id: number } | null>(null);
  const notify = useCallback(
    (text: string, error = false) => setToast({ text, error, id: Date.now() }),
    [],
  );
  const navigate = useCallback((p: Page, text = '') => {
    location.hash = `${p}${text ? `?q=${encodeURIComponent(text)}` : ''}`;
    window.scrollTo({ top: 0, behavior: 'instant' });
  }, []);
  useEffect(() => {
    const onHash = () => {
      setLocationState(route());
      window.scrollTo({ top: 0, behavior: 'instant' });
    };
    window.addEventListener('hashchange', onHash);
    try {
      document.documentElement.dataset.motion = localStorage.getItem('whale.motion') || 'on';
    } catch {
      /* Browser preference is optional. */
    }
    return () => window.removeEventListener('hashchange', onHash);
  }, []);
  useEffect(() => {
    document.title = `${pages[locationState.page].name} · Whale Core`;
  }, [locationState.page]);
  useEffect(() => {
    if (toast) {
      const t = setTimeout(() => setToast(null), toast.error ? 6500 : 3500);
      return () => clearTimeout(t);
    }
  }, [toast]);
  return (
    <WorkspaceProvider notify={notify} navigate={navigate}>
      <Shell {...locationState} jump={navigate} />
      {toast && (
        <div className={`toast ${toast.error ? 'error' : ''}`} role={toast.error ? 'alert' : 'status'}>
          {toast.error ? <CircleAlert size={17} /> : <CircleCheck size={17} />}
          <span>{toast.text}</span>
          <button aria-label="关闭提示" onClick={() => setToast(null)}>
            <X size={14} />
          </button>
        </div>
      )}
    </WorkspaceProvider>
  );
}
