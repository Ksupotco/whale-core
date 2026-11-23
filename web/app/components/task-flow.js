// @ts-check
/**
 * Task FLOW VIEW: the latest run of a task as a left→right node graph. Each step is a node;
 * `agent.delegate` steps branch into lanes for their child sub-agent runs. Layout is
 * computed (fixed node size), connectors are one SVG layer — no DOM measuring needed.
 */
import { html, useEffect, useState } from '/vendor/preact-htm.js';
import { Icon } from './icons.js';
import { StepItem, stepDuration } from './trace.js';
import { Badge, cx } from './ui.js';
import { useStore, loadRun } from '../lib/store.js';
import { STEP_KINDS, STEP_STATUS, RUN_STATUS, fmtDuration, clip } from '../lib/format.js';

const W = 176; const H = 76; const GX = 44; const GY = 46; const PAD = 16;

/** @param {any} s */
function stepStatus(s) { return s.kind === 'tool' && s.output?.status ? s.output.status : s.status; }

/**
 * @param {{run: any, steps: any[], children: any[]}} p
 */
export function TaskFlow({ run, steps: initialSteps, children }) {
  const live = useStore((s) => (run ? s.runs[run.id] : undefined));
  const childViews = useStore((s) => children.map((c) => s.runs[c.id]), (a, b) => a.length === b.length && a.every((x, i) => x === b[i]));
  const [sel, setSel] = useState(/** @type {any} */ (null));
  useEffect(() => {
    if (!run) return;
    loadRun(run.id).catch(() => {});
    children.forEach((c) => loadRun(c.id).catch(() => {}));
  }, [run?.id, children.map((c) => c.id).join()]);

  const steps = live?.steps?.length ? live.steps : initialSteps;
  const runState = live?.run?.status ? { ...run, ...live.run } : run;
  if (!run) return html`<p class="muted pad">这个任务还没有运行过。</p>`;

  // lanes: 0 = main run; each child gets its own lane, anchored at its delegate step
  const delegates = steps.filter((s) => s.kind === 'tool' && (s.input?.capability === 'agent.delegate' || s.kind === 'delegate'));
  const lanes = children.map((c, i) => {
    const anchor = delegates.find((d) => d.output?.data?.run_id === c.id) || delegates[i] || null;
    const col = anchor ? Math.max(0, steps.indexOf(anchor)) : 0;
    const cv = childViews[i];
    return { run: cv?.run?.status ? { ...c, ...cv.run } : c, steps: cv?.steps || [], col, lane: i + 1 };
  });
  /** @type {{key: string, x: number, y: number, step?: any, run?: any, lane: number}[]} */
  const nodes = [];
  steps.forEach((s, i) => nodes.push({ key: s.id, x: PAD + i * (W + GX), y: PAD, step: s, lane: 0 }));
  if (runState?.status === 'running' || runState?.status === 'waiting') nodes.push({ key: 'live', x: PAD + steps.length * (W + GX), y: PAD, lane: 0 });
  lanes.forEach((l) => {
    const y = PAD + l.lane * (H + GY);
    const shown = l.steps.length ? l.steps : [{ id: l.run.id + ':summary', kind: 'delegate', title: clip(l.run.input || '子 Agent', 40), status: l.run.status === 'completed' ? 'done' : l.run.status, output: {}, input: {} }];
    shown.forEach((s, j) => nodes.push({ key: l.run.id + s.id, x: PAD + (l.col + j) * (W + GX) + W / 2 + 28, y, step: s, lane: l.lane, run: l.run }));
  });
  const width = Math.max(...nodes.map((n) => n.x + W), W) + PAD;
  const height = PAD * 2 + (lanes.length + 1) * H + lanes.length * GY;

  const paths = [];
  for (let i = 1; i < nodes.length; i++) {
    const a = nodes[i - 1]; const b = nodes[i];
    if (a.lane === b.lane && (!a.run || a.run === b.run)) paths.push({ d: `M${a.x + W} ${a.y + H / 2} L${b.x} ${b.y + H / 2}`, cls: 'flow-edge' });
  }
  lanes.forEach((l) => {
    const anchor = nodes.find((n) => n.lane === 0 && steps.indexOf(n.step) === l.col);
    const first = nodes.find((n) => n.lane === l.lane);
    if (!anchor || !first) return;
    const x1 = anchor.x + W / 2; const y1 = anchor.y + H; const x2 = first.x; const y2 = first.y + H / 2;
    paths.push({ d: `M${x1} ${y1} C${x1} ${y2}, ${x1} ${y2}, ${x2} ${y2}`, cls: 'flow-edge flow-branch' });
  });

  const plan = live?.plan?.length ? live.plan : null;
  return html`<div class="flow">
    <div class="flow-head">
      <${Badge} tone=${RUN_STATUS[runState.status]?.tone || 'muted'} dot=${runState.status === 'running'}>${RUN_STATUS[runState.status]?.label || runState.status}<//>
      <span class="muted small">${steps.length} 步${lanes.length ? ` · ${lanes.length} 个子 Agent` : ''}${runState.stats?.latency_ms ? ` · ${fmtDuration(runState.stats.latency_ms)}` : ''}${runState.model ? ` · ${runState.model}` : ''}</span>
      ${live?.progress && runState.status === 'running' ? html`<span class="flow-progress"><span class="pulse-dot"></span>${live.progress}</span>` : null}
    </div>
    ${plan ? html`<p class="muted small">计划已实时更新（${plan.filter((p) => p.status === 'done').length}/${plan.length}）</p>` : null}
    <div class="flow-scroll" tabindex="0" aria-label="任务流程图，可横向滚动">
      <div class="flow-canvas" style=${{ width: `${width}px`, height: `${height}px` }}>
        <svg class="flow-svg" width=${width} height=${height} aria-hidden="true">
          ${paths.map((p, i) => html`<path key=${i} d=${p.d} class=${p.cls} />`)}
        </svg>
        ${lanes.map((l) => html`<span key=${'lane' + l.lane} class="flow-lane-label" style=${{ top: `${PAD + l.lane * (H + GY) - 18}px`, left: `${PAD + l.col * (W + GX) + W / 2 + 28}px` }}>
          <${Icon} name="branch" size=${12} />子 Agent · ${RUN_STATUS[l.run.status]?.label || l.run.status}</span>`)}
        ${nodes.map((n) => {
          if (!n.step) return html`<div key=${n.key} class="flow-node flow-node-live" style=${{ left: `${n.x}px`, top: `${n.y}px`, width: `${W}px`, height: `${H}px` }}>
            <span class="pulse-dot"></span><span>${runState.status === 'waiting' ? '等待你的批准' : '继续工作中…'}</span></div>`;
          const s = n.step;
          const st = stepStatus(s);
          const k = STEP_KINDS[s.kind] || { label: s.kind, icon: 'dot' };
          const dur = stepDuration(s);
          return html`<button type="button" key=${n.key} class=${cx('flow-node', `kind-${s.kind}`, `is-${st}`, sel?.id === s.id && 'is-sel', n.lane && 'is-child')}
            style=${{ left: `${n.x}px`, top: `${n.y}px`, width: `${W}px`, height: `${H}px` }} onClick=${() => setSel(sel?.id === s.id ? null : s)}
            aria-pressed=${sel?.id === s.id ? 'true' : 'false'}>
            <span class="flow-node-top"><${Icon} name=${k.icon} size=${14} /><span>${k.label}</span>
              <span class="flow-node-status">${st === 'running' ? html`<span class="pulse-dot"></span>` : STEP_STATUS[st]?.label || st}</span></span>
            <span class="flow-node-title">${clip(s.title || '', 34)}</span>
            <span class="flow-node-meta">${dur != null ? fmtDuration(dur) : ''}</span>
          </button>`;
        })}
      </div>
    </div>
    ${sel ? html`<div class="flow-detail card"><ol class="steps"><${StepItem} step=${sel} last /></ol></div>` : html`<p class="muted small">点击任意节点查看这一步的输入、输出与耗时。</p>`}
  </div>`;
}
