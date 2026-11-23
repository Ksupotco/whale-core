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
| QQ | qq.service + qq-bot（WeQ 外置进程 + 工作流） | channels.onebot（OneBot v11，config.json `channels.qq` 段；**WeQ/协议端衔接方式待 Tob 确认**：现网 QQ 走 WeQ 外置进程，v3 onebot 渠道接 OneBot 协议端（NapCat 类）——若现网协议端就是 WeQ，需要 OneBot 兼容层或换 NapCat，这是③的前置确认项） |
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
| S3 | 配置 v1：config.json（QQ onebot 渠道、模型 provider、agent.name、autonomy 关）；secrets 只写 key 引用，值从现网 secrets 复制（不落日志） | `whale doctor` | — |
| S4 | 起 v1 服务（先仅 127.0.0.1）→ 控制台自验（令牌登录/对话一轮 ScriptedModel 外的真实模型/记忆命中迁移条目 candidate 可见） | 控制台人工验收 | systemctl stop whale-v1 |
| S5 | QQ 切换：停 qq.service/qq-bot，onebot 渠道指向协议端 → 主人私聊一条 → 群 @ 一条 | QQ 双向实测 | 启回 v2 QQ |
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

## 6. 待主人/Tob 确认项

1. **QQ 协议端**：现网 WeQ 外置进程 vs v3 onebot（NapCat 类）——是换协议端还是加兼容层？（影响 S5）
2. **眼镜缺口**：③期间眼镜功能暂停（等 Link 化改造）还是 v2 glasses 服务并存？（影响 S6）
3. 切换日窗口（建议工作日晚间低峰，48h 观察期跨工作日）
4. v1 监听端口分配（建议沿用 127.0.0.1 + tailscale 绑定纪律）
