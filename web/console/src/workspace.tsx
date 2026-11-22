import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import type { Workspace, Page } from './data';
import {
  createSession,
  deleteSession,
  fetchWorkspace,
  probeSession,
  sameOrigin,
  onSessionExpired,
  type Connection,
  type LiveStatus,
} from './api';

export type Notify = (message: string, error?: boolean) => void;
export type Phase = 'boot' | 'setup' | 'ready';
type Model = {
  phase: Phase;
  data: Workspace;
  update: (fn: (s: Workspace) => Workspace) => void;
  connection: Connection | null;
  status: LiveStatus | null;
  sessionExpiresAt: number | null;
  authenticate: (token: string) => Promise<void>;
  enterConsole: () => Promise<void>;
  disconnect: () => void;
  refresh: () => Promise<void>;
  notify: Notify;
  navigate: (p: Page) => void;
};
const Context = createContext<Model | null>(null);

export function WorkspaceProvider({
  children,
  notify,
  navigate,
}: {
  children: ReactNode;
  notify: Notify;
  navigate: (p: Page) => void;
}) {
  const [phase, setPhase] = useState<Phase>('boot');
  const [live, setLive] = useState<Workspace | null>(null);
  const [connection, setConnection] = useState<Connection | null>(null);
  const [status, setStatus] = useState<LiveStatus | null>(null);
  const [sessionExpiresAt, setSessionExpiresAt] = useState<number | null>(null);
  const generation = useRef(0);

  const enterReady = useCallback(async () => {
    // 会话有效 → 加载真实工作区（cookie 由浏览器携带，JS 只见元数据）
    const conn = sameOrigin();
    const key = ++generation.current;
    const result = await fetchWorkspace(conn);
    if (key !== generation.current) return;
    setConnection(conn);
    setLive(result.workspace);
    setStatus(result.status);
    setPhase('ready');
  }, []);

  useEffect(() => {
    // 首开：探测同源会话。200 → 直接真实数据；401 → 首开配置流；
    // 网络失败 → 同样进配置流（由设置页呈现不可达原因）。
    let cancelled = false;
    probeSession()
      .then(async (s) => {
        if (cancelled) return;
        setSessionExpiresAt(s.expires_at * 1000);
        await enterReady();
      })
      .catch(() => {
        if (!cancelled) setPhase('setup');
      });
    return () => {
      cancelled = true;
    };
  }, [enterReady]);

  useEffect(() => {
    // 运行中会话过期（任一请求 401）→ 回配置流
    onSessionExpired(() => {
      generation.current++;
      setConnection(null);
      setLive(null);
      setStatus(null);
      setSessionExpiresAt(null);
      setPhase('setup');
    });
    return () => onSessionExpired(null);
  }, []);

  const authenticate = useCallback(async (token: string) => {
    const s = await createSession(token); // 失败抛错，由表单呈现
    setSessionExpiresAt(s.expires_at * 1000);
  }, []);
  const enterConsole = useCallback(async () => {
    await enterReady();
  }, [enterReady]);
  const disconnect = useCallback(() => {
    void deleteSession();
    generation.current++;
    setConnection(null);
    setLive(null);
    setStatus(null);
    setSessionExpiresAt(null);
    setPhase('setup');
  }, []);
  const refresh = useCallback(async () => {
    if (!connection) return;
    const key = ++generation.current;
    const result = await fetchWorkspace(connection);
    if (key !== generation.current) return;
    setLive((s) => ({
      ...result.workspace,
      threads: [
        ...result.workspace.threads,
        ...(s?.threads.filter((thread) => thread.id !== 'console') || []),
      ],
    }));
    setStatus(result.status);
  }, [connection]);
  const update = useCallback(
    (fn: (s: Workspace) => Workspace) => {
      setLive((s) => (s ? fn(s) : s));
    },
    [],
  );
  return (
    <Context.Provider
      value={{
        phase,
        data: live || {
          name: 'Whale Core',
          version: '',
          days: [],
          memories: [],
          tasks: [],
          plugins: [],
          events: [],
          threads: [],
        },
        update,
        connection,
        status,
        sessionExpiresAt,
        authenticate,
        enterConsole,
        disconnect,
        refresh,
        notify,
        navigate,
      }}
    >
      {children}
    </Context.Provider>
  );
}
export function useWorkspace() {
  const context = useContext(Context);
  if (!context) throw new Error('Workspace not available');
  return context;
}
