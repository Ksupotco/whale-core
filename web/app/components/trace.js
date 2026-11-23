// @ts-check
/**
 * "思维轨迹": how a run unfolded, step by step. Shared by the chat drawer and the task flow.
 * Each kind explains itself: recall shows which memories and why, think shows the decision
 * and latency, tool shows the capability, arguments and truthful status, approval can be
 * resolved inline.
 */
import { html, useState } from '/vendor/preact-htm.js';
import { Icon } from './icons.js';
import { Badge, Chip, cx } from './ui.js';
import { ApprovalCard } from './live.js';
import { useStore } from '../lib/store.js';
import { navigate } from '../lib/hooks.js';
import { KINDS, STEP_KINDS, STEP_STATUS, RUN_STATUS, kindLabel, fmtDuration, brief, clip } from '../lib/format.js';

/** @param {any} step */
export function stepDuration(step) {
  if (step.output?.latency_ms != null) return step.output.latency_ms;
  if (step.started_at && step.ended_at) return step.ended_at - step.started_at;
  return null;
}

/** @param {{v: any, max?: number}} p */
function Json({ v, max = 2400 }) {
  let text = '';
  try { text = typeof v === 'string' ? v : JSON.stringify(v, null, 2); } catch { text = String(v); }
  return html`<pre class="json">${text.length > max ? text.slice(0, max) + '\n…' : text}</pre>`;
}

/** @param {{m: any}} p */
export function MemoryHit({ m }) {
  const kind = KINDS[m.kind];
  return html`<button type="button" class="mem-hit" style=${{ '--kind': kind?.color || 'var(--accent)' }}
    title="在记忆中查看" onClick=${() => navigate(`/memory/${m.id}`)}>
    <span class="mem-hit-kind">${kindLabel(m.kind)}</span>
    <span class="mem-hit-text">${clip(m.content, 120)}</span>
    <span class="mem-hit-why">
      ${m.score != null ? html`<span class="mem-hit-score" title="综合得分">${Number(m.score).toFixed(2)}</span>` : null}
      ${(m.why || []).slice(0, 4).map((w) => html`<span class="why" key=${w}>${w}</span>`)}
      ${m.scope && m.scope !== 'owner' ? html`<span class="why why-scope">${m.scope === 'shared' ? '共享' : m.scope}</span>` : null}
    </span>
  </button>`;
}

/** @param {{step: any}} p */
function StepBody({ step }) {
  const out = step.output || {};
  const inp = step.input || {};
  const approvals = useStore((s) => s.approvals);
  const [more, setMore] = useState(false);
  switch (step.kind) {
    case 'recall': {
      const mems = out.memories || [];
      return html`<div class="step-body">
        ${inp.query ? html`<p class="step-q">以「${clip(inp.query, 60)}」为线索检索</p>` : null}
        ${mems.length ? html`<div class="mem-hits">${mems.map((m) => html`<${MemoryHit} key=${m.id} m=${m} />`)}</div>`
          : step.status === 'running' ? html`<p class="muted">正在回忆…</p>` : html`<p class="muted">没有找到相关记忆，这一轮只凭对话本身。</p>`}
      </div>`;
    }
    case 'think':
      return html`<div class="step-body">
        <div class="step-facts">
          ${inp.model ? html`<span><${Icon} name="spark" size=${13} />${inp.model}</span>` : null}
          ${inp.turn ? html`<span>第 ${inp.turn} 轮</span>` : null}
          ${out.decision ? html`<${Badge} tone=${out.decision === 'tools' ? 'info' : 'ok'}>${out.decision === 'tools' ? '决定调用能力' : '决定直接回答'}<//>` : null}
        </div>
        ${(out.tools || []).length ? html`<div class="chips">${out.tools.map((t) => html`<${Chip} key=${t}><code>${t}</code><//>`)}</div>` : null}
        ${out.text ? html`<p class="step-text">${clip(out.text, 300)}</p>` : null}
        ${out.reasoning ? html`<button type="button" class="linkish" onClick=${() => setMore(!more)}>${more ? '收起推理' : '查看推理过程'}</button>${more ? html`<p class="step-reason">${out.reasoning}</p>` : null}` : null}
      </div>`;
    case 'tool': {
      const cap = inp.capability || step.title;
      const args = inp.args ?? (inp.capability ? undefined : inp);
      const status = out.status || step.status;
      return html`<div class="step-body">
        <div class="step-facts"><code class="cap-name">${cap}</code>${status && status !== 'running' ? html`<${Badge} tone=${STEP_STATUS[status]?.tone || 'muted'}>${STEP_STATUS[status]?.label || status}<//>` : null}</div>
        ${args && Object.keys(args).length ? html`<dl class="kv">${Object.entries(args).slice(0, 8).map(([k, v]) => html`<div key=${k}><dt>${k}</dt><dd>${brief(v, 200)}</dd></div>`)}</dl>` : null}
        ${out.summary ? html`<p class="step-text">${out.summary}</p>` : null}
        ${out.error ? html`<p class="step-err"><${Icon} name="alert" size=${14} />${out.error}</p>` : null}
        ${status === 'unknown' ? html`<p class="step-warn">外部动作结果未知：不会自动重试，请到设备或对方处确认。</p>` : null}
        ${out.data != null ? html`<button type="button" class="linkish" onClick=${() => setMore(!more)}>${more ? '收起结果' : '查看返回数据'}</button>${more ? html`<${Json} v=${out.data} />` : null}` : null}
      </div>`;
    }
    case 'approval': {
      const pending = step.status === 'running' ? approvals.find((a) => a.run_id === step.run_id && (!inp.capability || a.capability === inp.capability)) : null;
      return html`<div class="step-body">
        <div class="step-facts"><code class="cap-name">${inp.capability}</code>
          ${out.approved === true ? html`<${Badge} tone="ok">已批准<//>` : out.approved === false ? html`<${Badge} tone="danger">未批准<//>` : html`<${Badge} tone="warn" dot>等待你的决定<//>`}</div>
        ${pending ? html`<${ApprovalCard} a=${pending} compact />` : inp.args ? html`<dl class="kv">${Object.entries(inp.args).slice(0, 6).map(([k, v]) => html`<div key=${k}><dt>${k}</dt><dd>${brief(v, 160)}</dd></div>`)}</dl>` : null}
      </div>`;
    }
    case 'reply':
      return out.text ? html`<div class="step-body"><p class="step-text">${clip(out.text, 240)}</p></div>` : null;
    default:
      return Object.keys(out).length ? html`<div class="step-body"><${Json} v=${out} max=${800} /></div>` : null;
  }
}

/** @param {{step: any, last?: boolean}} p */
export function StepItem({ step, last }) {
  const k = STEP_KINDS[step.kind] || { label: step.kind, icon: 'dot' };
  const dur = stepDuration(step);
  const status = step.kind === 'tool' && step.output?.status ? step.output.status : step.status;
  return html`<li class=${cx('step', `step-${step.kind}`, `is-${status}`, last && 'is-last')}>
    <span class="step-rail" aria-hidden="true"><span class="step-dot"><${Icon} name=${k.icon} size=${13} /></span></span>
    <div class="step-main">
      <header class="step-head">
        <span class="step-kind">${k.label}</span>
        <span class="step-title">${step.title}</span>
        <span class="step-meta">${status === 'running' ? html`<span class="pulse-dot" aria-label="进行中"></span>` : dur != null ? fmtDuration(dur) : ''}</span>
      </header>
      <${StepBody} step=${step} />
    </div>
  </li>`;
}

/** @param {{steps: any[], run?: any, progress?: string, empty?: any}} p */
export function StepList({ steps, run, progress, empty }) {
  if (!steps.length) return empty || html`<p class="muted pad">还没有步骤。</p>`;
  const total = run?.stats?.latency_ms;
  return html`<div class="trace">
    ${run ? html`<div class="trace-summary">
      <${Badge} tone=${RUN_STATUS[run.status]?.tone || 'muted'} dot=${run.status === 'running'}>${RUN_STATUS[run.status]?.label || run.status}<//>
      ${total != null ? html`<span>总耗时 ${fmtDuration(total)}</span>` : null}
      ${run.stats?.memories != null ? html`<span>${run.stats.memories} 条记忆</span>` : null}
      ${run.stats?.tools != null ? html`<span>${run.stats.tools} 次能力调用</span>` : null}
      ${run.model ? html`<span class="muted">${run.model}</span>` : null}
    </div>` : null}
    <ol class="steps">${steps.map((s, i) => html`<${StepItem} key=${s.id} step=${s} last=${i === steps.length - 1} />`)}</ol>
    ${progress && run?.status === 'running' ? html`<p class="trace-progress"><span class="pulse-dot"></span>${progress}</p>` : null}
    ${run?.error ? html`<p class="step-err"><${Icon} name="alert" size=${14} />${run.error}</p>` : null}
  </div>`;
}
