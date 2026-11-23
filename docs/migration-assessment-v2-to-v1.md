# 数据迁移面评估：v2 现网（N100）→ v3/v1（whale.db）

> 2026-11-23 · 云端开发 · **只评估，未实现**。现网数据为只读勘察（sqlite3 -readonly，未做任何写操作），secrets 未触碰。
> 结论：**能迁，且值得迁；分「结构化直迁 / 提炼重放 / 不迁留档」三档。核心纪律：无证据数据绝不直接污染 v3 记忆面。**

## 0. 现网数据面实测（N100 /opt/whale/state，2026-11-23 只读）

| 库/目录 | 大小（含 WAL） | 表与行数 | 内容 |
| --- | --- | --- | --- |
| `qq_workflow.sqlite3` | 4.2M + 4.1M WAL（+11 个历史 .bak） | `kv` 6482 行；`records` 627 行 | 主状态库：KV 命名空间 + 记忆记录 |
| `qq_inbox.sqlite3` | 840K + 4.0M WAL | `inbox` 2312 行 | 出站投递队列（message_id/conversation/text/status/attempts/lease/attachments） |
| `rayneo_context.sqlite3` | 4K + 1.7M WAL | `rayneo_segment` 230；`rayneo_source` 2 | 眼镜上下文片段 |
| `trace.sqlite3` | 3.5M + 4.9M WAL | `traces` 7639；`spans` 15606 | 可观测轨迹 |
| `persona-cards/` | 1 卡 | — | 人格卡文本 |
| `console-config/` | settings.json（177B，余皆空目录） | — | 早期控制台配置残余 |
| `artifacts/` `gui_traces/` `demo/` `phone-gallery-staging/` | 目录 | — | 文件产物与演示残余 |

### kv 命名空间分布（6482 行，冒号分隔）

`device` 2417（设备记录 + `device:action-id:*` 回执账本为绝对大头）· `msgimg` 897 · `msgimg-md5` 557 · `life` 498（`life:draft:qq-followup:*` 草稿审批）· `audit` 440 · `artifact` 401 · `uidmap` 232 · `reaction` 178 · `task` 151 · `imgcap` 132 · `member` 107（群友匿名回填）· `namecache` 106 · `rl` 76（频控桶）· `chat` 62（`chat:history:glasses:*`）· `agentlog` 35 · `notify` 33 · `space` 26 · `runtime` 18 · `bind` 15（`bind:persona/memver/rhythm:*`）· `plugin` 12 · `notifyquota` 12 · `epoch` 12 · `budget` 9 · owner_facts 系（ofjob/ofev/ofhost/ofq）~15 · `identity` 5 · `ep` 5 · `timeline` 4 · `schedule` 4 · `persona` 4（卡×3 + active）· `gui_task` 3 · `video` 2 · `recentmsg` 2 · `provider` 2（health）· `glasses` 2 · 其他 3。

### records 表（627 行，v2 记忆主体）

`(key, kind, content, record_id, created_at, tags, space_id)`；kind 分布：**episode 601 / preference 26**。

## 1. 逐面判定与 v3 承接

| v2 面 | 量级 | v3 承接点 | 判定 |
| --- | --- | --- | --- |
| records episode/preference | 627 | `MemoryItem`（kind 同名；tags 直迁；space_id→scope 映射） | **可迁**。v2 无强制 quote 证据 → 迁入一律 `status=candidate` + tag `legacy-import`，主人确认后转 active |
| persona 卡×3 + active 指针 | 4 | `personas` 表（PersonaCard，兼容 CCv2） | **可迁**（字段映射 + 校验；active → config.agent.persona_id） |
| 设备身份（`device:<32hex>` 记录） | 个位数台 | `devices` 表（pubkey/scopes/角色/隐私） | **可迁**；pubkey→device_id 重算一致才落库，**手机免重新配对** |
| `device:action-id:*` 回执账本 | ~2400 | v3 ledger 语义不同 | **不迁**（历史回执留档旧库） |
| `namecache:*` | 106 | channels NameDirectory（同构 db.kv） | **不迁**（24h TTL 内自然重建） |
| `uidmap:*` / `member:*` 群友匿名 | 339 | v3 无此模型（记忆边界按 scope） | **不迁** |
| `bind:persona/memver/rhythm` | 15 | 会话 persona_id / — / — | **有损**：群→人格绑定可迁为会话 persona_id；memver/rhythm 无对应语义，不迁 |
| `life:draft:*` 草稿审批 | 498 | v3 Approval 对象 | **不迁**（在途草稿切换日清零；语义由 approvals 承接） |
| `task:*` 任务定义 + `schedule:*` | 155 | `tasks` 表（goal/title/schedule→cron/interval） | **可迁定义**；运行态（次数/在途）不迁 |
| `chat:history:glasses:*` + recentmsg | 64 | `conversations`/`messages`（channel="glasses"） | **可迁** |
| QQ 群/私聊会话绑定（space/bind:mem） | ~50 | conversations（`qq:private:<号>`/`qq:group:<群号>`，与 channels.onebot 逐字同口径） | **可迁**（标题/绑定关系；历史消息 v2 本无持久化对话表，无从迁） |
| `qq_inbox` 投递队列 | 2312 | — | **不迁**（运行态；切换前排空或放弃，失败项已在 v2 侧如实记录） |
| `rayneo_segment` | 230 | v3 无 rayneo 面 | **留档不迁**（眼镜上下文如需，v3 另立新面） |
| `trace.sqlite3` traces/spans | 7639/15606 | v3 runs/steps/events 模型不同 | **留档不迁**（只读可查） |
| `audit`/`agentlog`/`runtime`/`rl`/`notifyquota`/`budget`/`epoch` | ~600 | v3 事件日志/预算另有实现 | **不迁**（审计留档；频控/预算是易失运行态） |
| `msgimg`/`msgimg-md5`/`imgcap`/`reaction`/`video`/`artifact` | ~2000 | 缓存/产物 | **不迁**（缓存自然失效；`artifacts/` 目录文件可整体搬移） |

## 2. 映射口径（实施时冻结）

- **身份/作用域**：`owner_qq`→peer=owner；私聊 `qq:<号>`；群 `group:qq:<群号>`（与 channels.onebot 现网口径逐字一致）；glasses 会话 channel="glasses"。
- **ID**：v2 id 一律不沿用；v3 重发前缀 ULID，落 `legacy_id` 映射表（kv ns `migrate`）备查。
- **记忆**：v2 records → MemoryItem；`space_id`→scope；无 quote → candidate + `legacy-import`；created_at 保留为 created_at（strength 按 v3 半衰期从迁移日重算）。
- **关系边**：v2 无 → 迁入后由 v3 整理管道自然补建，不硬造。
- **设备**：pubkey 重算 device_id 一致才迁；scopes/role/privacy 字段逐项校验，缺省落 v3 默认。

## 3. 工作量估

| 件 | 规模 | 估时 |
| --- | --- | --- |
| `scripts/migrate_v2_to_v1.py`（读 4 库→写 whale.db + 对账报告） | 400-600 行 | 1-1.5 天 |
| 假库往返测试（照 DEVELOPMENT §3：临时 v2 库→迁移→v3 断言） | 150-200 行 | 0.5 天 |
| 真数据试跑 + 对账 + 主人抽检 candidate 记忆 | — | 0.5 天 |
| 提炼重放（如需把无证据 episode 过提取管道升格） | 依赖 fast 模型预算 | 另计（建议不做，candidate 闸门已够） |

## 4. 风险与切换窗口

1. **污染风险**：无证据记忆直迁 active 会拉低 v3 记忆质量 → candidate 闸门强制（本评估核心纪律）。
2. **双写窗口**：切换顺序 = v2 停写 → 快照（cp 全目录，含 WAL checkpoint）→ 迁移 → 对账 → v3 上线 → v2 只读封存；任何一步失败退回 v2（旧库全程不动）。
3. **设备配对**：迁移错误=手机重配对；窗口内用两台真机验证 WS 重连与回执。
4. **WAL 一致性**：迁移前 `PRAGMA wal_checkpoint(TRUNCATE)` 落盘再拷（或直接用 sqlite 在线备份 API），避免 WAL 里 4M 未落盘数据被漏读。
5. **语义边界**：v2 consent/governance/pause 等 12 记忆模块的裁决语义在 v3 由「scope + Policy + 隐私过滤」承接，迁移文档需逐条对照说明（不在数据面，不做数据迁移）。

## 5. 建议的最小迁移包（MVP）

只迁：**records 627 + persona 4 + 设备身份 + task 定义 155 + glasses/qq 会话绑定**。其余一律留档。这是「主人连续性体验」所需的全部，也是风险最小的切法。

## 6. 迁移器实测对账（2026-11-23，scripts/migrate_v2_to_v1.py）

实现已落地并经假库往返测试 + 真数据快照 dry-run 验证（快照经 sqlite 在线备份 `.backup` 取得，**现网零写入**）：

| 面 | 源 | 迁入 | 校验 |
| --- | --- | --- | --- |
| 记忆 | records 627 | **627/627**（episode 601 / preference 26） | 全部 candidate + legacy-import；scope 分布 group:qq:<群号>×430、owner×115、member:*×76（9 个匿名空间保持隔离）、group:qq:<群号>×1 |
| 人格卡 | 3 卡 + active | 3/3（人格卡×3），active 卡写入 config | 字段映射校验过 |
| 设备身份 | 3 台 | **3/3，pubkey→device_id 重算全部一致**（三台真机：对账机→agent、主人机→personal、验收机→agent） | paired_via=legacy-migrate，免重配对成立 |
| 历史任务 | 151 | 151（状态映射 completed/failed/cancelled） | — |
| 会话 | 绑定+眼镜历史 | 60 迁（54 glasses + 5 QQ 群 + QQ 私聊主人）；192 条眼镜消息；owner_qq=<主人QQ> 自动识别 | 1 条旧绑定重复键由 INSERT OR IGNORE 吸收（表内 59） |
| 幂等 | — | 第二遍运行零新增（映射表去重 844 条） | 假库往返测试覆盖 |

假库测试（tests/test_migrate.py，2 例）覆盖：candidate 闸门、scope 映射（含未知空间隔离 legacy:*）、时间戳保留、persona 映射与 active 落 config、设备 id 不符跳过、任务状态映射、uid→uin 经 uidmap、无映射跳过留痕、幂等与 dry-run 不写盘。全量 pytest 123 passed。
