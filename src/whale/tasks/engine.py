"""Tasks (Goal mode) and sub-agents.

A task is a persisted goal the agent works on autonomously: it plans
(``plan.update``), acts, can delegate sub-problems to child agents
(``agent.delegate``) and finishes with a result. Tasks can be one-off,
scheduled (reminders, recurring reports) or created by the agent itself.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..capabilities.spec import Risk, ToolContext, capability, i, obj, s
from ..kernel.db import Database, dumps, loads, migration
from ..kernel.errors import WhaleError, not_found
from ..kernel.events import EventBus
from ..kernel.ids import new_id, now_ms

migration(
    "tasks.001",
    """CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY, title TEXT NOT NULL, goal TEXT NOT NULL, status TEXT NOT NULL,
        plan TEXT NOT NULL DEFAULT '[]', result TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
        origin TEXT NOT NULL DEFAULT 'user', parent_id TEXT, conversation_id TEXT, job_id TEXT,
        notify INTEGER NOT NULL DEFAULT 1, runs INTEGER NOT NULL DEFAULT 0, last_run_id TEXT,
        created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
       CREATE INDEX IF NOT EXISTS tasks_status ON tasks(status, updated_at);""",
)

ACTIVE = ("running", "waiting")


class TaskEngine:
    def __init__(self, app: Any) -> None:
        self.app = app
        self.db: Database = app.db
        self.bus: EventBus = app.bus
        self._running: dict[str, asyncio.Task[Any]] = {}

    # -- CRUD -------------------------------------------------------------------
    async def create(self, goal: str, *, title: str = "", schedule: dict[str, Any] | None = None,
                     origin: str = "user", parent_id: str | None = None, conversation_id: str | None = None,
                     start: bool = True, notify: bool = True) -> dict[str, Any]:
        goal = goal.strip()
        if not goal:
            raise WhaleError("invalid_params", "goal is empty")
        tid = new_id("task")
        now = now_ms()
        status = "scheduled" if schedule else ("queued" if start else "draft")
        self.db.execute(
            "INSERT INTO tasks(id,title,goal,status,origin,parent_id,conversation_id,notify,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (tid, title or goal[:40], goal, status, origin, parent_id, conversation_id, int(notify), now, now))
        if schedule:
            job = self.app.scheduler.add(f"任务：{title or goal[:30]}", "task.run", schedule, {"task_id": tid})
            self.db.execute("UPDATE tasks SET job_id=? WHERE id=?", (job["id"], tid))
        task = self.get(tid)
        await self.bus.emit("task.created", {"task": task})
        if start and not schedule:
            self.start(tid)
        return self.get(tid)

    def get(self, task_id: str) -> dict[str, Any]:
        r = self.db.one("SELECT * FROM tasks WHERE id=?", (task_id,))
        if not r:
            raise not_found("task", task_id)
        d = dict(r)
        d["plan"] = loads(d["plan"], [])
        d["notify"] = bool(d["notify"])
        if d["job_id"]:
            try:
                d["schedule"] = self.app.scheduler.get(d["job_id"])
            except WhaleError:
                d["schedule"] = None
        return d

    def list(self, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if status:
            rows = self.db.query("SELECT id FROM tasks WHERE status=? ORDER BY updated_at DESC LIMIT ?",
                                 (status, limit))
        else:
            rows = self.db.query("SELECT id FROM tasks ORDER BY updated_at DESC LIMIT ?", (limit,))
        return [self.get(r[0]) for r in rows]

    async def _update(self, task_id: str, **fields: Any) -> dict[str, Any]:
        sets, args = [], []
        for k, v in fields.items():
            sets.append(f"{k}=?")
            args.append(dumps(v) if k == "plan" else v)
        sets.append("updated_at=?")
        args.append(now_ms())
        self.db.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id=?", (*args, task_id))
        task = self.get(task_id)
        await self.bus.emit("task.updated", {"task": task})
        return task

    async def delete(self, task_id: str) -> None:
        task = self.get(task_id)
        await self.cancel(task_id)
        if task.get("job_id"):
            self.app.scheduler.remove(task["job_id"])
        self.db.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        await self.bus.emit("task.deleted", {"id": task_id})

    # -- execution ----------------------------------------------------------------
    def start(self, task_id: str) -> None:
        if task_id in self._running and not self._running[task_id].done():
            raise WhaleError("conflict", "task is already running")
        self._running[task_id] = asyncio.create_task(self.execute(task_id), name=f"task:{task_id}")

    async def cancel(self, task_id: str) -> bool:
        t = self._running.get(task_id)
        if t and not t.done():
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
            return True
        return False

    async def execute(self, task_id: str) -> dict[str, Any]:
        from ..agent.runtime import RunRequest

        task = self.get(task_id)
        await self._update(task_id, status="running", error="")
        plan_hint = ""
        if task["plan"]:
            plan_hint = "## 已有计划\n" + "\n".join(f"- [{p.get('status','todo')}] {p.get('step')}" for p in task["plan"])
        try:
            outcome = await self.app.agent.run(RunRequest(
                text=task["goal"], mode="goal", task_id=task_id, channel="task", remember=False,
                parent_id=None, system_extra=plan_hint))
        except asyncio.CancelledError:
            await self._update(task_id, status="cancelled")
            raise
        status = {"completed": "done", "cancelled": "cancelled"}.get(outcome.status, "failed")
        task = await self._update(task_id, status=status if not task.get("job_id") else
                                  ("scheduled" if status == "done" else status),
                                  result=outcome.text, error=outcome.run.get("error", ""),
                                  runs=task["runs"] + 1, last_run_id=outcome.run["id"])
        if status == "done":
            await self.app.memory.remember(
                f"完成任务「{task['title']}」：{outcome.text[:200]}", kind="episode", importance=0.35,
                source={"task_id": task_id, "run_id": outcome.run["id"]}, consolidate=False)
            if task["notify"] and task["origin"] != "subagent":
                await self.app.hub.notify(f"任务完成：{task['title']}", outcome.text[:500],
                                          {"task_id": task_id, "kind": "task"})
        return task

    async def delegate(self, objective: str, *, parent_run_id: str, timeout_s: float = 300) -> dict[str, Any]:
        from ..agent.runtime import RunRequest

        run = await asyncio.wait_for(self.app.agent.run(RunRequest(
            text=objective, mode="goal", parent_id=parent_run_id, channel="subagent", remember=False,
            max_steps=8, allowed=["web.*", "memory.search", "time.*", "plan.update"])), timeout_s)
        return {"status": run.status, "result": run.text, "run_id": run.run["id"]}

    async def recover(self) -> None:
        self.db.execute("UPDATE tasks SET status='interrupted', updated_at=? WHERE status IN ('running','waiting','queued')",
                        (now_ms(),))


def register(app: Any) -> None:
    reg = app.capabilities
    engine: TaskEngine = app.tasks

    app.scheduler.handler("task.run", lambda args: engine.execute(args["task_id"]))

    @capability(reg, "plan.update", "更新当前目标任务的计划。steps 为完整计划列表，每项 {step, status: todo|doing|done}。",
                params={"type": "object", "required": ["steps"], "properties": {"steps": {
                    "type": "array", "items": {"type": "object", "properties": {
                        "step": {"type": "string"}, "status": {"type": "string", "enum": ["todo", "doing", "done"]}}}}}},
                risk=Risk.write, title="更新计划", modes=("goal",))
    async def plan_update(args: dict[str, Any], ctx: ToolContext) -> Any:
        run = app.runs.get(ctx.run_id)
        steps = [{"step": str(p.get("step", ""))[:200], "status": p.get("status", "todo")}
                 for p in args["steps"] if isinstance(p, dict)][:20]
        if run.get("task_id"):
            await engine._update(run["task_id"], plan=steps)
        await app.bus.emit("run.plan", {"run_id": ctx.run_id, "plan": steps})
        return {"ok": True, "summary": f"计划已更新（{len(steps)} 步）"}

    @capability(reg, "agent.delegate", "把一个独立的子问题交给子 Agent 执行，返回其结果。适合可并行的调研。",
                params=obj(["objective"], objective=s("子任务的完整描述，需自足")),
                risk=Risk.read, title="委派子 Agent", modes=("goal",), timeout_s=320)
    async def delegate(args: dict[str, Any], ctx: ToolContext) -> Any:
        step_title = args["objective"][:60]
        await ctx.progress(f"子 Agent：{step_title}")
        return await engine.delegate(args["objective"], parent_run_id=ctx.run_id)

    @capability(reg, "task.create", "创建一个后台任务或定时提醒。schedule 可选："
                "{\"at\": \"2026-11-24 08:00\"} 一次性，{\"cron\": \"0 8 * * *\"} 周期，{\"every_min\": 60}。",
                params=obj(["goal"], goal=s("任务目标，执行时会作为指令交给 Agent；提醒类写成“提醒用户……”"),
                           title=s("简短标题"), schedule={"type": "object", "description": "可选的调度"}),
                risk=Risk.write, title="创建任务", modes=("chat", "goal"), owner_only=True)
    async def task_create(args: dict[str, Any], ctx: ToolContext) -> Any:
        schedule = args.get("schedule") or None
        if schedule and isinstance(schedule.get("at"), str):
            from datetime import datetime
            from zoneinfo import ZoneInfo

            when = datetime.fromisoformat(schedule["at"].replace("T", " "))
            if when.tzinfo is None:
                when = when.replace(tzinfo=ZoneInfo(app.config.value.agent.timezone))
            schedule = {"at": int(when.timestamp() * 1000)}
        task = await engine.create(args["goal"], title=args.get("title", ""), schedule=schedule, origin="agent",
                                   conversation_id=ctx.conversation_id or None)
        return {"task_id": task["id"], "status": task["status"], "summary": f"已创建任务：{task['title']}"}

    @capability(reg, "task.list", "列出最近的任务与状态。", params=obj(limit=i("数量", default=10)),
                title="查看任务", owner_only=True)
    async def task_list(args: dict[str, Any], ctx: ToolContext) -> Any:
        return {"tasks": [{"id": t["id"], "title": t["title"], "status": t["status"], "result": t["result"][:200]}
                          for t in engine.list(limit=int(args.get("limit") or 10))]}
