// @ts-check
/**
 * 任务 Tasks: task list with status chips · create form with schedule · selected task FLOW
 * VIEW (plan checklist, step graph with sub-agent branches, result) · 调度 table.
 */
import { html, useState, useEffect } from '/vendor/preact-htm.js';
import { Icon } from '../components/icons.js';
import { Badge, Button, AsyncButton, Card, Chip, ConfirmButton, Empty, Field, PageHead, Segmented, Skeleton, Switch, ErrorBox, cx, When } from '../components/ui.js';
import { TaskFlow } from '../components/task-flow.js';
import { act, tryAct, useTopic, useStore } from '../lib/store.js';
import { useRpc, navigate, linkClick, useDebouncedCallback } from '../lib/hooks.js';
import { renderMarkdown } from '../lib/markdown.js';
import { TASK_STATUS, RUN_STATUS, timeAgo, timeUntil, fmtTime, clip } from '../lib/format.js';

const FILTERS = [
  { value: '', label: '全部' }, { value: 'active', label: '进行中' }, { value: 'scheduled', label: '已调度' },
  { value: 'done', label: '已完成' }, { value: 'failed', label: '失败' },
];
const HANDLERS = { 'task.run': '任务', 'autonomy.explore': '自主探索', 'phone.digest': '手机整理', 'memory.maintain': '记忆整理' };

/** Human description of a schedule spec. @param {any} spec */
export function describeSpec(spec) {
  if (!spec) return '—';
  if (spec.at) return `一次 · ${fmtTime(Number(spec.at))}`;
  if (spec.every_min) return `每 ${spec.every_min} 分钟`;
  if (spec.cron) {
    const m = String(spec.cron).match(/^(\d{1,2}) (\d{1,2}) \* \* \*$/);
    if (m) return `每天 ${m[2].padStart(2, '0')}:${m[1].padStart(2, '0')}`;
    const w = String(spec.cron).match(/^(\d{1,2}) (\d{1,2}) \* \* 1-5$/);
    if (w) return `工作日 ${w[2].padStart(2, '0')}:${w[1].padStart(2, '0')}`;
    return `cron ${spec.cron}`;
  }
  return JSON.stringify(spec);
}

/** Form state → schedule spec ({at} | {cron} | {every_min} | null). @param {{mode: string, at: string, time: string, every: string}} f */
export function buildSchedule(f) {
  if (f.mode === 'once') {
    const ms = new Date(f.at).getTime();
    if (!f.at || Number.isNaN(ms)) throw new Error('请选择执行时间');
    if (ms < Date.now()) throw new Error('执行时间已经过去了');
    return { at: ms };
  }
  if (f.mode === 'daily') {
    const m = /^(\d{1,2}):(\d{2})$/.exec(f.time || '');
    if (!m) throw new Error('请选择每天的时间');
    return { cron: `${Number(m[2])} ${Number(m[1])} * * *` };
  }
  if (f.mode === 'every') {
    const n = Number(f.every);
    if (!Number.isFinite(n) || n < 1) throw new Error('间隔至少 1 分钟');
    return { every_min: Math.round(n) };
  }
  return null;
}

function CreateTask({ onCreated, onCancel }) {
  const [f, setF] = useState({ goal: '', title: '', mode: 'none', at: '', time: '08:00', every: '60' });
  const [err, setErr] = useState('');
  const submit = async () => {
    setErr('');
    let schedule;
    try { schedule = buildSchedule(f); } catch (e) { setErr(/** @type {any} */ (e).message); return; }
    const task = await act('tasks.create', { goal: f.goal.trim(), title: f.title.trim(), schedule, start: true },
      { ok: schedule ? '任务已调度' : '任务已创建，鲸鱼开始工作' });
    onCreated(task);
  };
  return html`<${Card} title="交给鲸鱼一个目标" icon="flag" class="create-task" actions=${html`<button type="button" class="icon-btn" aria-label="关闭" onClick=${onCancel}><${Icon} name="x" /></button>`}>
    <p class="muted small">目标模式下，鲸鱼会先写计划，再逐步执行；能并行的子问题会交给子 Agent。需要对外动作时会停下来请你批准。</p>
    <${Field} label="目标"><textarea class="input" rows="3" value=${f.goal} placeholder="例如：调研三款家用 NAS，按价格、噪音、功耗对比，给出推荐" onInput=${(e) => setF({ ...f, goal: e.currentTarget.value })}></textarea><//>
    <${Field} label="标题（可选）"><input class="input" value=${f.title} placeholder="默认取目标开头" onInput=${(e) => setF({ ...f, title: e.currentTarget.value })} /><//>
    <${Field} label="什么时候做">
      <${Segmented} label="调度" value=${f.mode} onChange=${(mode) => setF({ ...f, mode })}
        options=${[{ value: 'none', label: '现在' }, { value: 'once', label: '定时一次' }, { value: 'daily', label: '每天' }, { value: 'every', label: '每隔' }]} />
    <//>
    ${f.mode === 'once' ? html`<${Field} label="执行时间"><input class="input" type="datetime-local" value=${f.at} onInput=${(e) => setF({ ...f, at: e.currentTarget.value })} /><//>` : null}
    ${f.mode === 'daily' ? html`<${Field} label="每天几点"><input class="input" type="time" value=${f.time} onInput=${(e) => setF({ ...f, time: e.currentTarget.value })} /><//>` : null}
    ${f.mode === 'every' ? html`<${Field} label="间隔（分钟）"><input class="input" type="number" min="1" value=${f.every} onInput=${(e) => setF({ ...f, every: e.currentTarget.value })} /><//>` : null}
    ${err ? html`<p class="field-error" role="alert">${err}</p>` : null}
    <div class="form-actions"><${AsyncButton} variant="primary" icon=${f.mode === 'none' ? 'play' : 'clock'} disabled=${!f.goal.trim()} onClick=${submit}>${f.mode === 'none' ? '开始执行' : '创建调度'}<//>
      <${Button} variant="ghost" onClick=${onCancel}>取消<//></div>
  <//>`;
}

/** @param {{plan: any[]}} p */
function Plan({ plan }) {
  if (!plan?.length) return html`<p class="muted small">还没有计划。鲸鱼开始执行时会先写下计划。</p>`;
  const done = plan.filter((p) => p.status === 'done').length;
  return html`<div class="plan">
    <div class="plan-progress"><span style=${{ width: `${(done / plan.length) * 100}%` }}></span></div>
    <ol class="plan-list">${plan.map((p, i) => html`<li key=${i} class=${cx('plan-item', `is-${p.status || 'todo'}`)}>
      <span class="plan-check" aria-hidden="true">${p.status === 'done' ? html`<${Icon} name="check" size=${13} />` : p.status === 'doing' ? html`<span class="pulse-dot"></span>` : null}</span>
      <span>${p.step}</span><span class="sr-only">${{ done: '已完成', doing: '进行中', todo: '待办' }[p.status] || ''}</span></li>`)}</ol>
  </div>`;
}

function TaskDetail({ id, onChanged }) {
  const res = useRpc('tasks.get', { id }, [id]);
  const refresh = useDebouncedCallback(() => res.reload(true), 400);
  useTopic(['task.updated', 'run.started', 'run.completed', 'run.failed', 'run.cancelled'], (ev) => {
    if (ev.topic === 'task.updated' ? ev.data.task?.id === id : ev.data.run?.task_id === id) refresh();
  });
  const liveRunId = res.data?.latest?.run?.id;
  const livePlan = useStore((s) => (liveRunId ? s.runs[liveRunId]?.plan : undefined));
  if (res.error) return html`<${ErrorBox} error=${res.error} onRetry=${() => res.reload()} />`;
  if (!res.data) return html`<${Skeleton} rows=${6} />`;
  const { task, runs, latest } = res.data;
  const st = TASK_STATUS[task.status] || { label: task.status, tone: 'muted' };
  const running = task.status === 'running' || task.status === 'waiting';
  return html`<div class="task-detail">
    <header class="task-head">
      <div>
        <h2>${task.title}</h2>
        <p class="task-meta"><${Badge} tone=${st.tone} dot=${running}>${st.label}<//>
          <span class="muted small">${task.origin === 'agent' ? '鲸鱼自己创建' : task.origin === 'subagent' ? '子任务' : '你创建'} · <${When} ms=${task.created_at} fmt=${timeAgo} /> · 运行 ${task.runs} 次</span>
          ${task.schedule ? html`<${Chip}><${Icon} name="clock" size=${12} /> ${describeSpec(task.schedule.spec)}${task.schedule.next_at ? ` · ${timeUntil(task.schedule.next_at)}` : ''}<//>` : null}</p>
      </div>
      <div class="task-actions">
        ${running ? html`<${AsyncButton} variant="danger" icon="stop" onClick=${async () => { await act('tasks.cancel', { id }, { ok: '已取消' }); res.reload(true); onChanged(); }}>取消<//>`
          : html`<${AsyncButton} variant="primary" icon="play" onClick=${async () => { await act('tasks.start', { id }, { ok: '已开始' }); res.reload(true); onChanged(); }}>${task.runs ? '再执行一次' : '开始'}<//>`}
        <${ConfirmButton} icon="trash" confirmText="删除任务？" onConfirm=${async () => { await act('tasks.delete', { id }, { ok: '任务已删除' }); onChanged(); navigate('/tasks', { replace: true }); }}>删除<//>
      </div>
    </header>
    <p class="task-goal"><${Icon} name="flag" size=${14} />${task.goal}</p>
    <div class="task-grid">
      <section><h4>计划</h4><${Plan} plan=${livePlan?.length ? livePlan : task.plan} /></section>
      <section><h4>结果</h4>
        ${task.error ? html`<p class="step-err"><${Icon} name="alert" size=${14} />${task.error}</p>` : null}
        ${task.result ? html`<div class="md task-result">${renderMarkdown(task.result)}</div>` : html`<p class="muted small">${running ? '还在进行中，完成后会通知你。' : '暂无结果。'}</p>`}
      </section>
    </div>
    <h4>流程</h4>
    <${TaskFlow} key=${latest?.run?.id || 'none'} run=${latest?.run} steps=${latest?.steps || []} children=${latest?.children || []} />
    ${runs.length > 1 ? html`<details class="runs-history"><summary>历史运行（${runs.length}）</summary>
      <ul class="mini-list">${runs.map((r) => html`<li key=${r.id}><${Badge} tone=${RUN_STATUS[r.status]?.tone}>${RUN_STATUS[r.status]?.label || r.status}<//>
        <span>${fmtTime(r.created_at)}</span><span class="muted">${r.stats?.latency_ms ? `${Math.round(r.stats.latency_ms / 1000)} 秒` : ''}</span><span class="muted">${clip(r.output || r.error, 50)}</span></li>`)}</ul></details>` : null}
  </div>`;
}

function Schedule() {
  const jobs = useRpc('schedule.list');
  const refresh = useDebouncedCallback(() => jobs.reload(true), 400);
  useTopic('schedule.*', refresh);
  const list = jobs.data?.jobs || [];
  return html`<${Card} title="调度" icon="clock" subtitle="系统里唯一的时钟：任务、记忆整理、自主探索、手机整理都在这里排队" pad=${false} id="schedule">
    ${jobs.error ? html`<${ErrorBox} error=${jobs.error} onRetry=${() => jobs.reload()} />` : null}
    ${!jobs.data ? html`<div class="pad"><${Skeleton} rows=${3} /></div>` : list.length ? html`<div class="table-wrap"><table class="table">
      <thead><tr><th>名称</th><th>频率</th><th>下次</th><th>上次</th><th>状态</th><th class="ta-r">操作</th></tr></thead>
      <tbody>${list.map((j) => html`<tr key=${j.id} class=${cx(!j.enabled && 'is-off')}>
        <td><strong>${j.name}</strong> ${j.system ? html`<${Badge} tone="info">系统<//>` : null}<br /><span class="muted small">${HANDLERS[j.handler] || j.handler}${j.args?.task_id ? html` · <a href=${`/tasks/${j.args.task_id}`} onClick=${linkClick}>查看任务</a>` : null}</span></td>
        <td>${describeSpec(j.spec)}</td>
        <td>${j.enabled && j.next_at ? html`<span title=${fmtTime(j.next_at)}>${timeUntil(j.next_at)}</span>` : html`<span class="muted">—</span>`}</td>
        <td>${j.last_at ? html`<${When} ms=${j.last_at} fmt=${timeAgo} />` : html`<span class="muted">从未</span>`} <span class="muted small">· ${j.runs} 次</span></td>
        <td>${j.last_status === 'error' ? html`<${Badge} tone="danger" title=${j.last_error}>出错<//><br /><span class="muted small">${clip(j.last_error, 40)}</span>` : j.last_status === 'ok' ? html`<${Badge} tone="ok">正常<//>` : html`<span class="muted">—</span>`}</td>
        <td class="ta-r"><span class="row-actions">
          <${Switch} size="sm" checked=${j.enabled} label=${(j.enabled ? '停用 ' : '启用 ') + j.name} onChange=${async (v) => { await tryAct('schedule.set_enabled', { id: j.id, enabled: v }, { ok: v ? '已启用' : '已停用' }); jobs.reload(true); }} />
          <${AsyncButton} size="sm" variant="ghost" icon="play" title="立即运行一次" onClick=${() => tryAct('schedule.run_now', { id: j.id }, { ok: '已触发' })} aria-label=${'立即运行 ' + j.name} />
          ${!j.system ? html`<${ConfirmButton} icon="trash" confirmText="删除？" onConfirm=${async () => { await act('schedule.delete', { id: j.id }, { ok: '已删除' }); jobs.reload(true); }} aria-label=${'删除 ' + j.name} />` : null}
        </span></td>
      </tr>`)}</tbody></table></div>` : html`<${Empty} icon="clock" title="没有调度" compact />`}
  <//>`;
}

export function TasksPage({ route }) {
  const [filter, setFilter] = useState('');
  const [creating, setCreating] = useState(route.query.new === '1');
  useEffect(() => { if (route.query.new === '1') setCreating(true); }, [route.query.new]);
  const tasks = useRpc('tasks.list', { limit: 200 });
  const refresh = useDebouncedCallback(() => tasks.reload(true), 400);
  useTopic(['task.*'], refresh);
  const all = tasks.data?.tasks || [];
  const counts = {};
  all.forEach((t) => { const k = t.status === 'running' || t.status === 'waiting' || t.status === 'queued' ? 'active' : t.status; counts[k] = (counts[k] || 0) + 1; });
  const shown = all.filter((t) => !filter || (filter === 'active' ? ['running', 'waiting', 'queued'].includes(t.status) : filter === 'failed' ? ['failed', 'interrupted', 'cancelled'].includes(t.status) : t.status === filter));
  const selected = route.id || '';
  return html`<div class="page tasks">
    <${PageHead} title="任务" icon="tasks" subtitle="交给鲸鱼的目标：它会计划、执行、委派子 Agent，并把每一步摊开给你看"
      actions=${html`<${Button} variant="ghost" icon="clock" href="#schedule" onClick=${(e) => { e.preventDefault(); globalThis.document?.getElementById('schedule')?.scrollIntoView({ behavior: 'smooth' }); }}>调度<//>
        <${Button} variant="primary" icon="plus" onClick=${() => setCreating(true)}>新建任务<//>`} />
    ${creating ? html`<${CreateTask} onCancel=${() => { setCreating(false); if (route.query.new) navigate('/tasks', { replace: true }); }}
      onCreated=${(t) => { setCreating(false); tasks.reload(true); if (t?.id) navigate(`/tasks/${t.id}`); }} />` : null}
    <div class="tasks-layout">
      <aside class="task-list card">
        <div class="chips pad-sm" role="group" aria-label="按状态筛选">${FILTERS.map((f) => html`<${Chip} key=${f.value} active=${filter === f.value} count=${f.value ? counts[f.value] || 0 : all.length} onClick=${() => setFilter(f.value)}>${f.label}<//>`)}</div>
        ${!tasks.data ? html`<div class="pad"><${Skeleton} rows=${5} height=${40} /></div>` : shown.length ? html`<ul>${shown.map((t) => {
          const st = TASK_STATUS[t.status] || { label: t.status, tone: 'muted' };
          return html`<li key=${t.id}><a href=${`/tasks/${t.id}`} onClick=${linkClick} class=${cx('task-item', t.id === selected && 'is-active')} aria-current=${t.id === selected ? 'page' : undefined}>
            <span class="task-item-top"><${Badge} tone=${st.tone} dot=${t.status === 'running'}>${st.label}<//>${t.schedule ? html`<span class="muted small"><${Icon} name="clock" size=${12} /> ${describeSpec(t.schedule.spec)}</span>` : null}</span>
            <strong>${t.title}</strong>
            ${t.plan?.length ? html`<span class="mini-plan" aria-label="计划进度"><span style=${{ width: `${(t.plan.filter((p) => p.status === 'done').length / t.plan.length) * 100}%` }}></span></span>` : null}
            <span class="muted small"><${When} ms=${t.updated_at} fmt=${timeAgo} /></span></a></li>`;
        })}</ul>` : html`<${Empty} icon="flag" title=${filter ? '这个分类下没有任务' : '还没有任务'} hint="试试“每天早上 8 点给我一份天气和日程简报”。" compact />`}
      </aside>
      <section class="task-main card">
        ${selected ? html`<${TaskDetail} key=${selected} id=${selected} onChanged=${() => tasks.reload(true)} />`
          : html`<${Empty} icon="branch" title="选择一个任务查看它的流程" hint="每个任务都会展开成一张流程图：回忆 → 思考 → 调用能力 → 委派子 Agent → 结果。">
            <${Button} variant="primary" icon="plus" onClick=${() => setCreating(true)}>新建任务<//><//>`}
      </section>
    </div>
    <${Schedule} />
  </div>`;
}
