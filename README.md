<div align="center">

# Whale-Core

*山有木兮木有枝，心悦君兮君不知*

一个以个人 AI Agent 为核心的智能体系统：持续记忆、独立判断、任务规划、工具使用、主动探索与外部交互。

[架构](docs/ARCHITECTURE.md) · [协议](docs/PROTOCOL.md) · [开发](docs/DEVELOPMENT.md)

</div>

## 它是什么

把 Whale 理解为一个 **AI 的操作系统**：模型是它的思考能力，记忆是它的长期经验，人格是它的行为特征，能力（工具、插件、设备）扩展它能做的事，客户端是它与世界交互的接口。

v1 是一次从零开始的重建。整个系统只有一条主干：**每一个输入都成为一个 Run**——回忆记忆、思考、调用能力（必要时请你批准）、回复——每一步实时可见、持久可查。控制台、手机、QQ、眼镜和定时任务走的是同一条路。

## 现在能做什么

| 能力 | 说明 |
| --- | --- |
| 对话与人格 | 多会话、流式回复、看图；人格卡可编辑，兼容 Character Card V2 导入 |
| 长期记忆 | 每轮对话后台提取明确说过的事实/偏好/经历（必须有原话依据）；重复则强化，矛盾则新条目取代旧条目，相关则建立关系；混合检索（全文 + 向量 + 重要度 + 衰减强度）并解释命中原因；遗忘曲线与墓碑式删除 |
| 记忆边界 | 以主人为中心：群聊记忆单向流入主人会话；其他人的私聊完全隔离；只有鲸鱼自己总结的性格与口癖跨会话共享；非主人会话不提供 Shell、任务、设备等能力（[详情](docs/MEMORY.md)） |
| 手机经历 | 鲸鱼专用手机：全部能力、经历原样记录；主人日常手机：系统隐私界面（锁屏、密码框、输入法、系统界面、安全中心、密码管理器）与你排除的应用/网站不保存，其余默认记录并脱敏验证码与卡号；每 30 分钟按片段整理成记忆 |
| 可观测性 | 每次回复都能展开"思维轨迹"：用了哪些记忆、为什么、调用了什么能力、耗时、是否等待审批 |
| 能力与权限 | 内置时间、记忆、网页搜索/阅读、通知、Shell；插件与设备能力统一注册；按风险默认放行/询问，可逐项设为允许/询问/禁止；审批在任意客户端处理 |
| 任务（Goal 模式） | 给一个目标，Agent 自己写计划、逐步执行、把子问题委派给子 Agent；可一次性、定时（cron）或周期执行；Agent 也能自己创建提醒 |
| 自主探索 | 空闲时按兴趣搜索阅读，写探索日记，产出候选知识等你确认；主动消息统一经过静默时段、日额度和冷却 |
| 客户端与设备 | Link v1 协议：一个 WebSocket，RPC + 事件 + 能力声明；whale-mobile 设备经设备线协议（`/api/devices/ws`）接入，设备能力自动成为 Agent 能力 |
| 离线可用 | 未配置模型时有一个确定性的"离线大脑"，可以先体验记忆、任务与界面 |

## 快速开始

需要 Python 3.11+。控制台无需 Node 构建。

```bash
git clone https://github.com/amwangfan/whale-core.git
cd whale-core
python -m pip install -e ".[dev]"
whale serve            # 打印控制台地址与访问令牌
```

打开 http://127.0.0.1:8080 ，粘贴令牌，在 **设置 → 模型** 添加一个 OpenAI 兼容服务（DeepSeek、通义、Ollama、vLLM…），为 `chat` 角色选择模型并测试连接。`fast` 角色（记忆提取、关系判断）可选配一个更便宜的模型；`embedding` 角色配置后启用语义检索。

```bash
whale doctor           # 检查环境与模型
whale token --rotate   # 轮换访问令牌
whale --data D:/whale serve --port 9000
```

数据默认在 `~/.whale/`：`config.json`（可见配置）、`secrets.json`（密钥，权限 600）、`whale.db`（SQLite：记忆、对话、Run、任务、事件日志）、`artifacts/`。

## 结构

```
src/whale/
  kernel/        ID · 错误模型 · 事件总线与事件日志 · SQLite 与迁移 · 配置 · 产物
  models/        OpenAI 兼容（流式、工具调用、无工具模型自动降级）· 离线大脑 · 角色路由与预算
  memory/        存储（FTS5 + 关系边 + 向量）· 提取 / 整理 / 检索 / 遗忘
  capabilities/  能力规范 · 注册表 · 策略与审批 · 内置能力
  agent/         人格 · 对话 / Run / Step 记录 · 运行时（唯一的 Agent 循环）
  tasks/         目标任务与子 Agent · 唯一调度器（cron / 周期 / 一次性）
  autonomy/      探索 · 主动消息闸门
  plugins/       插件管理 · 官方插件（天气、QQ、媒体）
  devices/       设备注册与配对 · V2 设备链路 · 设备能力桥
  link/          Link v1 协议：信封、RPC 注册表、连接中枢
  channels/      Channel 抽象与 IO 渠道：QQ 语义层（backend 可选 onebot/weq）· 入站规范化 · 出站回执 · 昵称目录
  server/        Starlette：/api/rpc · /api/link · /api/events · 控制台静态文件
web/             控制台（ES Modules + Preact/htm，无构建步骤）
contracts/device/  whale-mobile 设备协议镜像（信封 1.x，路径无版本）
tests/           单元与集成测试（pytest / unittest）
```

## 开发

```bash
python -m pytest -q
```

设计原则、模块边界与扩展方式见 [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)。旧版（v2）代码保存在 `legacy/`，仅供移植参考，不参与运行，将在移植完成后删除。
