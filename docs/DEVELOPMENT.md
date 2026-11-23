# 开发指南

面向维护者与编程 Agent。先读 [ARCHITECTURE.md](ARCHITECTURE.md) 与 [PROTOCOL.md](PROTOCOL.md)。

## 1. 不变量

1. **一条主干**：任何会让 Agent 思考或行动的入口都调用 `app.agent.run(RunRequest(...))`。不要为新客户端再写一条"召回 → 拼提示词 → 调模型"的旁路。
2. **一切能做的事都是 Capability**：新增工具、插件动作、设备动作、客户端能力，都通过 `CapabilityRegistry.register` 注册，带 JSON Schema 参数与风险等级。Agent 与策略层不区分来源。
3. **一切可观测的都是事件**：状态变化发 `bus.emit(topic, data)`；需要持久的对象（Run、Step、记忆、任务、审批）另有表。UI 只依赖 RPC 快照 + 事件。
4. **一个闸门**：能力调用只经过 `Policy.decide` → `Approvals`。不要在工具内部再做重复审批，也不要在提示词里做权限判断。
5. **真实状态**：外部动作超时记为 `unknown`，不自动重放；重启后未完成的 Run 标记 `interrupted`，不伪装完成。
6. **记忆要有依据**：自动提取必须带用户原话 quote；Agent 自己的学习（knowledge/diary）进入 `shared` 作用域，关于某个人的记忆进入该人的作用域。
7. **依赖单向**：`kernel ← models ← memory ← capabilities ← agent ← tasks/autonomy ← link/devices/channels/plugins ← server`。channels 不依赖 plugins；插件可复用 channels 的实现（如 QQ 插件托管 `channels.onebot`）。

## 2. 常见扩展

**新内置能力**（`capabilities/builtin.py` 或新模块的 `register(app)`）：

```python
@capability(reg, "calendar.add", "在主人的日历里添加日程", params=obj(["title", "at"], title=s("标题"), at=s("ISO 时间")),
            risk=Risk.external, title="添加日程")
async def calendar_add(args, ctx):
    ...
    return {"id": ..., "summary": "已添加"}
```

**插件**（`plugins/builtin/<id>/plugin.json` + `plugin.py`，或用户目录 `<data>/plugins/<id>/`）：

```python
def setup(p):
    if not p.secret("key"):
        return                      # 未配置 → needs_config
    @p.capability("weather.now", "查询实时天气", params={...})
    async def now(args, ctx): ...
    @p.webhook("inbound")           # → /api/hooks/<id>/inbound（自行校验调用方）
    async def inbound(request): ...
    p.notifier(push_to_channel)     # 主人通知的一个投递出口
```

**新 RPC 方法**（`server/api.py`）：参数模型写在模块顶层（便于导出 Schema），`scope="client"` 表示非 owner 客户端也可调用。

**新事件主题**：直接 `await app.bus.emit("domain.verb", {...})`，并在 PROTOCOL.md §6 登记。

## 3. 测试

```bash
python -m pytest -q
```

- 模型用 `ScriptedModel` 注入（`tests/helpers.py: scripted(app, ...)`），按顺序返回文本或工具调用。
- HTTP/WS 用 Starlette `TestClient`；设备用带 Ed25519 密钥的假设备。
- 新功能至少覆盖：成功路径、失败路径的真实状态、权限边界。

## 4. 数据与迁移

表结构通过 `kernel.db.migration(name, sql)` 在模块导入时登记，按名称只执行一次。修改已有表请新增迁移（`memory.002` …），不要改旧迁移。
