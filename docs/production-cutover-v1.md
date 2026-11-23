# 阶段③：whale-core v1 生产上线方案（N100）

> 2026-11-23 · 云端开发 · **方案待主人裁决后执行**。备份方案在 §2（先行件）。
> 依赖链：④端侧 APK 发版（信封 1.0+无版本路径）→ ⑤真机验证（两台真机）→ ③本方案切换。
> ③不可先于④——设备面 clean break 无回退档，APK 未全量前切生产 = 设备断连。

## 1. 现状盘点（N100 实测 + 记忆校准）

| 面 | 现网 v2 | v1 接替 |
| --- | --- | --- |
| 代码 | `/opt/whale/whale-core`（git main=2dd4b9a 压缩后） | 新目录 `/opt/whale/whale-v1`（clone 自 GitHub main，push 完成后） |
| 服务 | whale-glasses(:8191) · whale-api(:8180) · whale-core-dev8190 · qq-bot · qq · whale-qq-watchdog.timer | 单服务 `whale-v1.service`（whale serve，监听 127.0.0.1:8180 或分配端口） |
| 数据 | `/opt/whale/state/`（qq_workflow 等 4 库 + artifacts 等） | `/opt/whale/v1-data/`（迁移器产出 whale.db + config.json + secrets.json） |
| QQ | qq.service + qq-bot（WeQ 外置进程 + 工作流） | channels.qq **backend=weq**（主人裁决 2026-11-23：OneBot 与 WeQ 双支持、config 驱动切换；现网 WeQ 栈不动——v3 经 webhook 收 WeQ 推送 + tRPC 出站）；backend=onebot 保留给 NapCat 类协议端 |
| 眼镜 | whale-glasses :8191 + 东京反代 `/turbo/v1/*` | v1 无眼镜轻量面；眼镜桥应迁移为 Link 客户端（role=glasses）——**眼镜侧改造是独立工作项（应用开发/眼镜桥），③期间眼镜功能会缺口**，需主人确认是否接受或保 v2 glasses 服务并存一段 |
| 公网 | CF→东京 nginx→N100:8191 | 切到 v1 端口；`/api/*` 鉴权 Bearer；控制台路径 `/` |

## 2. 备份方案（先行件，切换前完成）

备份根：`/opt/whale/backups/pre-v1-2026112X/`，权限 700，内容：

1. `state.tar.zst`——`/opt/whale/state/` 全目录（先对 4 个 sqlite 做 `.backup` 在线一致快照到临时目录再打包，WAL 不丢；**不 checkpoint 现网库本体**）
2. `whale-core.bundle`——`git -C /opt/whale/whale-core bundle create ... --all`（含 2dd4b9a 全部历史与 tag）
3. `units/`——五个 service + watchdog timer 的 unit 文件副本
4. `nginx/`——东京反代 rayneo.wangfanlab.conf 副本（在东京机另行备份）
5. `secrets-manifest.txt`—— secrets 文件名+权限清单（**只列名不含值**）；secrets 本体随 state.tar 同目录权限带走（目录本来 700）
6. `RESTORE.md`——回滚步骤（见 §5）

回滚成立的依据：v1 数据目录独立于 v2 state，v2 服务 unit 只 disable 不删，nginx 改动有副本。

## 3. 切换步骤（每步可退）

| 步 | 动作 | 验证 | 回退 |
| --- | --- | --- | --- |
| S0 | 备份（§2）+ 校验（tar 可解、bundle verify） | 备份完整性 | — |
| S1 | N100 clone v1 代码到 `/opt/whale/whale-v1`；`pip install -e .`（系统 python3，无 venv，与现网一致） | `whale --version` = 1.0.0 | 删目录 |
| S2 | 迁移数据：state 快照 → `migrate_v2_to_v1.py --data /opt/whale/v1-data` → 对账报告（计数与 dry-run 基线一致：627/3/3/151/60） | 对账报告 + 抽检 candidate 记忆 10 条 | 删 v1-data |
| S3 | 配置 v1：config.json（QQ weq 渠道、模型 provider、agent.name、autonomy 关、**回复档位与思考强度——见 §3.1**）；secrets 只写 key 引用，值从现网 secrets 复制（不落日志） | `whale doctor` + `models.test` 全角色 | — |

### 3.1 回复档位与模型配置（主人令 f850274d：切换后默认慢档高思考）

切换完成后 v1 默认回复 = **慢档 kimi-k3 high 思考**（完整缓冲想完一次性发，非流式快答）。
v2 两档语义 → v1 映射：v2 `kv replytier` 默认档 slow ⇔ v1 `agent.reply_gear="slow"` + chat 角色 `reasoning_effort="high"`。

S3 写入的配置项（config.json 可见面 / secrets.json 只写值）：

```json
{
  "models": {
    "providers": [{"id": "kimi", "kind": "openai", "base_url": "https://api.moonshot.cn/v1", "label": "Kimi"}],
    "chat":  {"provider": "kimi", "model": "kimi-k3", "reasoning_effort": "high", "temperature": 0.7, "max_tokens": 2048},
    "fast":  {"provider": "<现有便宜模型>", "model": "…", "temperature": 0.2, "max_tokens": 800},
    "vision": {"provider": "kimi", "model": "<视觉模型>", "reasoning_effort": ""}
  },
  "agent": {"reply_gear": "slow", "name": "鲸鱼"}
}
```

- secrets：`provider.kimi` = Moonshot API key（从现网 secrets 复制，不进日志/文档）。
- 语义校验点：slow 档下 `run.delta` 零增量事件、回复整段一次性出现；渠道（QQ/通知）本就完成后投递，行为不变。
- 控制台对应 UI：设置→模型→角色行的「思考强度」下拉；设置→智能体→「回复档位」。
- 回退口径：把 `agent.reply_gear` 改回 `fast` 即恢复流式（config.update 即时生效，无需重启）。
| S4 | 起 v1 服务（先仅 127.0.0.1）→ 控制台自验（令牌登录/对话一轮 ScriptedModel 外的真实模型/记忆命中迁移条目 candidate 可见） | 控制台人工验收 | systemctl stop whale-v1 |
| S5 | QQ 切换：停 qq.service/qq-bot；v1 `channels.qq`（backend=weq）配置 weq_web_url+token，WeQ 推送地址改指 v1 webhook（`/api/hooks/channel-qq/inbound`）→ 主人私聊一条 → 群 @ 一条 | QQ 双向实测 | WeQ 推送指回 v2 监听口 + 启回 v2 QQ |
| S6 | 公网切换：东京 nginx `/turbo/*` → v1 端口（或按确认项只切手机/控制台路径，眼镜路径暂留 v2:8191 并存） | 公网 health+RPC 401/200 矩阵 | nginx 切回 |
| S7 | v2 五服务 `disable`（不删）；观察 48h | 无回滚需求即封盘 | enable 即回 |

## 4. 上线后验证清单

- [ ] 控制台 9 页可用；实时事件流不断线
- [ ] QQ：主人私聊/群 mention/smart 三态；通知器 `qq:owner` 可达
- [ ] 设备：两台真机 在线、心跳正常、`device.status` 查询回执 ok（前提是 ④⑤ 已完成）
- [ ] 记忆：迁移条目以 candidate 出现在记忆页，主人确认转 active 后检索命中
- [ ] 任务：迁移的 151 条历史任务在任务页可见（completed/failed/cancelled）
- [ ] 调度器：sys_memory_maintain 正常运行

## 5. 回滚预案（RESTORE.md 摘要）

1. `systemctl stop whale-v1` → `enable --now` v2 五服务 → nginx 切回旧 conf → reload
2. v2 数据零接触（state 目录全程未被 v1 写），回退即原样恢复
3. v1-data 保留封存（回滚后再切换不用重迁）
4. 已知有损点：切换窗口内 v1 侧新产生的对话/记忆不回灌 v2（可接受——回滚窗口按小时计）

## 6. 裁决记录与待确认项

1. ~~**QQ 协议端**~~（2026-11-23 主人裁决）：**OneBot 与 WeQ 双支持、config 驱动切换**；③ 切换走 `backend=weq`（现网 WeQ 栈无缝接入），onebot 保留给 NapCat 类协议端。
2. ~~**眼镜缺口**~~（2026-11-23 主人裁决）：③期间**暂停**；眼镜 Link 化（role=glasses）另线开发，独立项。
3. 切换日窗口（建议工作日晚间低峰，48h 观察期跨工作日）——待定。
4. v1 监听端口分配（建议沿用 127.0.0.1 + tailscale 绑定纪律）——待定。
