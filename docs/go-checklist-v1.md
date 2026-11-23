# ③ 切换 go 检查单（whale-core v1 上生产）

> 2026-11-23 · 云端开发 · 配套 docs/production-cutover-v1.md（步骤语义以该文为准，本文是 go/no-go 核验表）。
> 每步列：就绪态 / 核验点 / 回退动作。S4c（控制台内网人工验收）封盘 = 定稿信号。

## 前置（切换日前必须全绿）

| # | 项 | 就绪态 | 核验点 |
| --- | --- | --- | --- |
| P1 | ④ 新 APK（信封 1.0 + `/api/devices/ws`）已装 magic8/nova | ☐ 待应用开发线 | 两机 hello_ack + 心跳正常 |
| P2 | ⑤ 真机验证通过（计划 §④ 五组） | ☐ 待 P1 | 报告归档 |
| P3 | QQ weq backend 配置就绪（weq_web_url + token，群模式/白名单） | ☑ 代码就绪（135 绿含 12 例 WeQ） | staging 不连 QQ（防双发）✔ 照守 |
| P4 | 慢档配置项定稿（§3.1：chat=kimi-k3 high + reply_gear=slow + temperature=null） | ☑ staging 8189 实证（见下） | — |
| P5 | 主人窗口裁决（切换日 + 48h 观察期） | ☐ | — |

P4 staging 实证（2026-11-23，whale-v1-staging:8189，tailnet）：
- slow 档 chat 实测：`status=completed`，整段一次性回复 61 字，`model=kimi-k3`，think 步 `reasoning` 192 字符（high 思考真在跑），全程 5.3s；
- slow 档 SSE 实测：`run.delta` 帧数 **0**（缓冲语义成立）；对照 flip fast 后同法测得 **10** 帧，切回 slow 读回 config 确认定稿；
- kimi-k3 只收默认温度：temperature=0.7 被 HTTP 400 拒（实测）→ 代码已支持 `temperature=null` 不下发（测试锁定），方案 §3.1 配置块已按此写。
- staging 数据目录与 QQ 零连接（channels 未配置）。

## S0–S7 核验表

| 步 | 就绪态 | 核验点 | 回退动作 |
| --- | --- | --- | --- |
| S0 备份 | ☑ 已做（2026-11-23）+复核通过（见 §复核） | snap/4 库一致快照、state-files.tar.gz 177M、bundle verify okay、units×8、secrets.tar.gz(600)+清单、东京 nginx 副本 | — |
| S1 clone 代码 | ☐（staging 已跑同码，S1 只是落到生产目录） | `whale --version`=1.0.0、whale doctor 全绿 | 删目录 |
| S2 迁移对账 | ☑ 演练复现（627/3/3/151/60，0 跳过） | 对账报告 == 演练基线；抽检 10 条 candidate 记忆 | 删 v1-data，v2 未动 |
| S3 配置 | ☑ 配置块定稿（§3.1） | models.test 全角色 ok；channels.qq=weq 不上线先 disable | — |
| S4 内网验收 | ☐（S4c 封盘待定稿信号） | 控制台 9 页人工点检、真实对话一轮、记忆页 candidate 可见 | stop whale-v1 |
| S5 QQ 切换 | ☐ | 主人私聊/群 @ 双向实测；WeQ 推送指 v1 webhook | WeQ 指回 v2 监听口 + 启 v2 QQ |
| S6 公网切换 | ☐ | nginx 切流后 health/RPC 鉴权矩阵；眼镜路径按裁决（暂停=v2 glasses 停，届时 /turbo/v1/* 404 属预期） | nginx 切回 |
| S7 v2 封存 | ☐ | 五服务 disable 不删；48h 观察 | enable 即回 |

## §复核（S0 回滚就绪逐项证据，2026-11-23 复核）

- 备份在位：`/opt/whale/backups/pre-v1-20261123/`（权限 700）——snap/ 4 库、state-files.tar.gz 170M、whale-core.bundle 5.0M、units/ 8 件、secrets.tar.gz 600 root-only + secrets-manifest.txt（只列名）
- bundle 可回：`git bundle verify` = okay（完整历史）
- v2 代码可回：origin 侧 archive tag（Tob push 时立）+ 本备份 bundle 双锚
- v2 服务可回：units 副本与现网 unit 一致（逐项 diff 无差异）；v2 服务当前仍 active
- 东京反代可回：`/root/backups/rayneo.wangfanlab.conf.pre-v1-20261123`
- 数据隔离：v1-data 独立目录，v2 state 全程不被 v1 写

## go / no-go 判据

全绿 = P1–P5 全勾 + S0–S3 就绪 + 主人口头 go。任一项不过 → 不进入 S4。
