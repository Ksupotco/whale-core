/**
 * Whale Core 控制台设计系统（第二阶段）· 核心组件。
 *
 * 纪律：
 * - 全部走 theme.css 语义令牌（明暗双主题自适应），不引入新依赖；
 * - FocusGrid 是本次重构的核心交互（规格书 §三）：平级卡片 + 点击聚焦展开，
 *   FLIP 平滑过渡、DOM 顺序不变（不重挂载 → 不丢未保存输入）、
 *   内部控件不触发折叠、键盘可达、移动端单列；
 * - Modal/Toast/Empty/SearchField 沿用 src/ui.tsx 既有实现（第三阶段逐页迁移换皮），
 *   此处不重复造。
 */
import {
  useLayoutEffect,
  useRef,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from 'react';
import { Check, ChevronDown, LoaderCircle, X } from 'lucide-react';

// ---------------------------------------------------------------- 按钮

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'secondary' | 'danger' | 'ghost';
  size?: 'sm' | 'md';
  busy?: boolean;
  icon?: ReactNode;
};
export function Button({
  variant = 'secondary',
  size = 'md',
  busy = false,
  icon,
  children,
  className = '',
  disabled,
  ...rest
}: ButtonProps) {
  return (
    <button
      className={`ds-btn ds-btn-${variant} ds-btn-${size} ${className}`}
      disabled={disabled || busy}
      {...rest}
    >
      {busy ? <LoaderCircle size={size === 'sm' ? 13 : 15} className="spin" /> : icon}
      {children}
    </button>
  );
}

export function IconButton({
  label,
  children,
  className = '',
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { label: string }) {
  return (
    <button className={`ds-icon-btn ${className}`} aria-label={label} title={label} {...rest}>
      {children}
    </button>
  );
}

// ---------------------------------------------------------------- 卡片

export function Card({
  title,
  subtitle,
  actions,
  children,
  className = '',
  pad = true,
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  pad?: boolean;
}) {
  return (
    <section className={`ds-card ${pad ? '' : 'ds-card-flush'} ${className}`}>
      {(title || actions) && (
        <header className="ds-card-head">
          <div className="ds-card-heading">
            {title && <h2>{title}</h2>}
            {subtitle && <p>{subtitle}</p>}
          </div>
          {actions && <div className="ds-card-actions">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

// ---------------------------------------------------------------- 状态标签

export type Tone = 'violet' | 'mint' | 'blue' | 'amber' | 'red' | 'slate';
export function Tag({
  tone = 'slate',
  dot = false,
  children,
}: {
  tone?: Tone;
  dot?: boolean;
  children: ReactNode;
}) {
  return (
    <span className={`ds-tag ds-tag-${tone}`}>
      {dot && <i className="ds-tag-dot" />}
      {children}
    </span>
  );
}

// ---------------------------------------------------------------- 表单

export function Field({
  label,
  hint,
  error,
  children,
  inline = false,
}: {
  label: ReactNode;
  hint?: ReactNode;
  error?: string;
  children: ReactNode;
  inline?: boolean;
}) {
  return (
    <div className={`ds-field ${inline ? 'ds-field-inline' : ''} ${error ? 'ds-field-error' : ''}`}>
      <div className="ds-field-label">{label}</div>
      <div className="ds-field-control">{children}</div>
      {error ? (
        <p className="ds-field-note ds-field-note-error" role="alert">
          {error}
        </p>
      ) : hint ? (
        <p className="ds-field-note">{hint}</p>
      ) : null}
    </div>
  );
}

export function Input({ className = '', ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={`ds-input ${className}`} {...rest} />;
}

export function Textarea({ className = '', ...rest }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={`ds-input ds-textarea ${className}`} {...rest} />;
}

export function Select({ className = '', children, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <span className={`ds-select ${className}`}>
      <select {...rest}>{children}</select>
      <ChevronDown size={13} aria-hidden />
    </span>
  );
}

export function Switch({
  checked,
  onChange,
  label,
  disabled = false,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      className={`ds-switch ${checked ? 'on' : ''}`}
      onClick={() => onChange(!checked)}
    >
      <span />
    </button>
  );
}

export function SegmentedControl<T extends string>({
  options,
  value,
  onChange,
  label,
}: {
  options: { value: T; label: ReactNode }[];
  value: T;
  onChange: (v: T) => void;
  label: string;
}) {
  return (
    <div className="ds-segment" role="radiogroup" aria-label={label}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={value === o.value}
          className={value === o.value ? 'active' : ''}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- 横幅

export function Banner({
  tone = 'info',
  children,
  onClose,
}: {
  tone?: 'info' | 'ok' | 'warn' | 'error';
  children: ReactNode;
  onClose?: () => void;
}) {
  return (
    <div className={`ds-banner ds-banner-${tone}`} role={tone === 'error' ? 'alert' : 'status'}>
      <span className="ds-banner-body">{children}</span>
      {onClose && (
        <button className="ds-icon-btn" aria-label="关闭提示" onClick={onClose}>
          <X size={14} />
        </button>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- FocusGrid（核心交互）

export type FocusGridItem = {
  key: string;
  /** 卡片头（紧凑态也显示）：图标/名称/摘要/状态 */
  head: (focused: boolean) => ReactNode;
  /** 卡片体：focused=false 时给紧凑摘要，true 时给完整编辑区 */
  body: (focused: boolean) => ReactNode;
  ariaLabel?: string;
};

const INTERACTIVE =
  'button, input, select, textarea, a, label, summary, [role="switch"], [role="tab"], [role="radio"], [contenteditable]';

export function FocusGrid({
  items,
  focused,
  onFocus,
  className = '',
}: {
  items: FocusGridItem[];
  focused: string | null;
  onFocus: (key: string | null) => void;
  className?: string;
}) {
  const gridRef = useRef<HTMLDivElement>(null);
  const prevRects = useRef(new Map<string, DOMRect>());

  const capture = () => {
    prevRects.current.clear();
    gridRef.current?.querySelectorAll<HTMLElement>('.focus-card').forEach((el) => {
      const key = el.dataset.fkey || '';
      if (key) prevRects.current.set(key, el.getBoundingClientRect());
    });
  };

  // FLIP：focused 变化后，把每张卡从旧位置/尺寸补间到新位置/尺寸。
  // DOM 顺序不动、组件不重挂载——未保存的输入天然保留。
  useLayoutEffect(() => {
    const grid = gridRef.current;
    if (!grid || !prevRects.current.size) return;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      prevRects.current.clear();
      return;
    }
    grid.querySelectorAll<HTMLElement>('.focus-card').forEach((el) => {
      const key = el.dataset.fkey || '';
      const prev = prevRects.current.get(key);
      if (!prev) return;
      const next = el.getBoundingClientRect();
      const dx = prev.left - next.left;
      const dy = prev.top - next.top;
      const sx = next.width ? prev.width / next.width : 1;
      const sy = next.height ? prev.height / next.height : 1;
      if (!dx && !dy && Math.abs(sx - 1) < 0.001 && Math.abs(sy - 1) < 0.001) return;
      el.animate(
        [
          { transform: `translate(${dx}px, ${dy}px) scale(${sx}, ${sy})`, transformOrigin: 'top left' },
          { transform: 'none', transformOrigin: 'top left' },
        ],
        { duration: 340, easing: 'cubic-bezier(.32,.72,.35,1)' },
      );
    });
    prevRects.current.clear();
  }, [focused]);

  const toggle = (key: string) => {
    capture();
    onFocus(focused === key ? null : key);
  };

  return (
    <div
      ref={gridRef}
      className={`focus-grid ${focused ? 'has-focus' : ''} ${className}`}
      data-count={items.length}
    >
      {items.map((item) => {
        const isFocused = focused === item.key;
        return (
          <section
            key={item.key}
            data-fkey={item.key}
            className={`focus-card ${isFocused ? 'focused' : ''}`}
            style={
              isFocused
                ? // 聚焦卡纵跨「其余卡数」行（隐式行高随内容，不撑高页面）
                  { gridRow: `1 / span ${Math.max(1, items.length - 1)}` }
                : undefined
            }
            aria-expanded={isFocused}
            aria-label={item.ariaLabel}
            tabIndex={0}
            onClick={(e) => {
              // 内部控件不触发折叠/展开
              if ((e.target as HTMLElement).closest(INTERACTIVE)) return;
              toggle(item.key);
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !(e.target as HTMLElement).closest(INTERACTIVE)) {
                e.preventDefault();
                toggle(item.key);
              }
            }}
          >
            <div className="focus-card-head">{item.head(isFocused)}</div>
            <div className="focus-card-body">{item.body(isFocused)}</div>
          </section>
        );
      })}
    </div>
  );
}

/** FocusGrid 卡片头推荐结构（可自由组合，非强制）。 */
export function FocusCardTitle({
  icon,
  name,
  desc,
  aside,
}: {
  icon: ReactNode;
  name: ReactNode;
  desc?: ReactNode;
  aside?: ReactNode;
}) {
  return (
    <div className="focus-card-title">
      <span className="focus-card-icon">{icon}</span>
      <span className="focus-card-name">
        <strong>{name}</strong>
        {desc && <small>{desc}</small>}
      </span>
      {aside && <span className="focus-card-aside">{aside}</span>}
    </div>
  );
}

// ---------------------------------------------------------------- 勾选行

export function CheckRow({
  checked,
  onChange,
  label,
  disabled = false,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: ReactNode;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={checked}
      disabled={disabled}
      className={`ds-checkrow ${checked ? 'on' : ''}`}
      onClick={() => onChange(!checked)}
    >
      <span className="ds-check">{checked && <Check size={11} />}</span>
      <span>{label}</span>
    </button>
  );
}
