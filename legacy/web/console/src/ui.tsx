import { useEffect, useId, useRef, type ReactNode } from 'react';
import {
  Activity,
  Brain,
  CheckCheck,
  Compass,
  Cpu,
  Database,
  FileText,
  LayoutDashboard,
  ListTodo,
  MessageSquare,
  Network,
  Puzzle,
  Radio,
  Search,
  Server,
  Settings2,
  ShieldCheck,
  Smartphone,
  Sparkles,
  UserRound,
  X,
} from 'lucide-react';
import { pages, type Kind, type Page } from './data';

export const pageIcons = {
  overview: LayoutDashboard,
  chat: MessageSquare,
  memory: Database,
  personas: UserRound,
  models: Cpu,
  providers: Server,
  tasks: ListTodo,
  plugins: Puzzle,
  workflows: Network,
  devices: Smartphone,
  approvals: ShieldCheck,
  events: Radio,
  logs: FileText,
  settings: Settings2,
};
export const kindIcons = {
  chat: MessageSquare,
  memory: Brain,
  tasks: CheckCheck,
  plugins: Puzzle,
  workflows: Compass,
  system: Activity,
};
export const kindColors: Record<Kind, string> = {
  chat: 'violet',
  memory: 'mint',
  tasks: 'blue',
  plugins: 'amber',
  workflows: 'violet',
  system: 'slate',
};
export function WhaleLogo({ size = 40, className = '' }: { size?: number; className?: string }) {
  return (
    <svg
      width={size}
      height={size * 0.84}
      viewBox="0 0 50 42"
      fill="none"
      className={className}
      aria-hidden="true"
    >
      <path
        d="M2.8 22.5c0-7 7.5-12.1 16.7-12.1 8.1 0 13.2 4.9 18.2 8.3 3.1-1.7 5.1-4.2 5.5-7.5-4.6-.3-6.8-3.3-7.1-7.4 4.4-.3 7.4 1.1 9 4.7 1.4-2.6 3.1-3.7 4.6-4.1.4 5.1-.6 8.1-4 10.2-.2 7.8-6.9 15.3-16.3 18.7-11.1 4-24.7-.5-26.6-10.8Z"
        fill="currentColor"
      />
      <path d="M18 27.5c2.8 5.1 7.4 10.9 12.5 11.4 1.2-2.1-.1-7-2.4-11.3" fill="currentColor" />
      <path
        d="M4.6 25.1c3.1 2.1 7.5 2.7 11.8 1.3"
        stroke="white"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
      <circle cx="12.1" cy="19.3" r="1.8" fill="white" />
      <path
        d="m16.1 7.6-1.4-3.3m5.4 2.5 1.1-3.9"
        stroke="currentColor"
        strokeWidth="2.6"
        strokeLinecap="round"
      />
    </svg>
  );
}
export function Modal({
  title,
  children,
  onClose,
  wide = false,
  className = '',
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  wide?: boolean;
  className?: string;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const id = useId();
  useEffect(() => {
    const el = ref.current;
    el?.showModal();
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      el?.close();
      document.body.style.overflow = prev;
    };
  }, []);
  return (
    <dialog
      ref={ref}
      className={`modal ${wide ? 'wide' : ''} ${className}`}
      aria-labelledby={id}
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      onClick={(e) => {
        if (e.target === ref.current) {
          const r = ref.current!.getBoundingClientRect();
          if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom)
            onClose();
        }
      }}
    >
      <div className="modal-head">
        <h2 id={id}>{title}</h2>
        <button className="icon-button" onClick={onClose} aria-label="关闭弹窗">
          <X size={18} />
        </button>
      </div>
      <div className="modal-body">{children}</div>
    </dialog>
  );
}
export function PageHeading({ page, action }: { page: Page; action?: ReactNode }) {
  return (
    <div className="page-heading">
      <div>
        <div className="heading-line">
          <h1>{pages[page].name}</h1>
          <span className="heading-english">{pages[page].en}</span>
        </div>
        <p>{pages[page].description}</p>
      </div>
      {action && <div className="heading-action">{action}</div>}
    </div>
  );
}
export function SearchField({
  value,
  onChange,
  placeholder,
}: {
  value: string;
  onChange: (s: string) => void;
  placeholder: string;
}) {
  return (
    <div className="search-field">
      <Search size={16} />
      <input
        aria-label={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
      />
      {value && (
        <button onClick={() => onChange('')} aria-label="清空搜索">
          <X size={14} />
        </button>
      )}
    </div>
  );
}
export function Empty({
  title = '暂无相关内容',
  text = '试试其他关键词，或调整筛选条件。',
}: {
  title?: string;
  text?: string;
}) {
  return (
    <div className="empty-state">
      <span className="empty-icon">
        <Sparkles size={23} />
      </span>
      <h3>{title}</h3>
      <p>{text}</p>
    </div>
  );
}
export function ActivityIcon({ kind, small = false }: { kind: Kind; small?: boolean }) {
  const Icon = kindIcons[kind];
  return (
    <span className={`color-icon ${kindColors[kind]} ${small ? 'small' : ''}`}>
      <Icon size={small ? 15 : 18} strokeWidth={1.65} />
    </span>
  );
}
