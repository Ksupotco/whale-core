# TimlMemBench 中文 dev：产品 owner_fact_v1 小样本

这次试跑直接调用上游产品的 `OwnerFactService.observe`、
`OwnerFactExtractor`、`OwnerFactWorker`、`OwnerFactService.retrieve`，
不使用旧 `FactService` 评测治理路径。适配器见
`evals/timlmembench_owner_fact.py`，只负责将官方历史会话映射进产品接口：
`speaker_role=user` 为 owner，`private_chat` 为可信 QQ 私聊；联系人发言
不自动进入 owner 事实。授权句柄是隔离的 benchmark 测试替身，未测试真实 QQ
绑定。未读取 gold 状态给提取器或回答器。

| 固定前 5 道 activation 题 | DeepSeek 裁判通过 |
| --- | ---: |
| 产品 `owner_fact_v1` | 1/5 |
| 旧版独立治理适配器的同题历史记录 | 5/5 |

两边使用 `deepseek-v4.1-flash-1017` 回答与裁判。旧版记录取自
`timlmembench_state_aware_dev_full_scores.jsonl`；并非在本次试跑同步重跑。
这**不是可归因的产品退步幅度**：旧适配器会提取双方历史并执行独立治理；
`owner_fact_v1` 首版只自动保存 owner 私聊自述。首样本共有 38 条 owner
发言和 38 条联系人发言；本次 43 次模型调用，合计约 21,087 token，
5 题回答前各只召回 1–2 条事实。4 题缺少关键证据而判错。

这个结果说明，直接拿 TimlMemBench 总分比较两版会把**覆盖范围**、
模型提取和检索能力混在一起。先把 owner 私有事实、其他人的公开事实、
群聊边界分开验收，再决定是否扩到 dev 全集。当前 BM25 分支只改 curated
词项排序，未改 `owner_fact_v1` 的检索实现，因此本小样本不衡量 BM25 效果。

逐题预测与裁判结果仅在本机
`E:\2026-1\tmp\timlmembench_owner_fact_v1_dev5_{predictions,scores}.jsonl`，
含基准原文，不提交仓库。密钥也未写入仓库。
