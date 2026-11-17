# TimlMemBench dev：五阶段激活改动与旧错题复测

## 改动

产品 memory 链路按顺序执行：解析当前任务的独立需求；在授权空间中过滤失效事实并单独检查适用的边界和进入权限；BM25 与原有 Qwen Embedding 各自排序后以 RRF 融合；按需求覆盖重排；Activation Gate 选择最少的相关证据。前台沟通现在也视为对外场景，禁止主动代办的边界会进入上下文。未修改 benchmark 适配器、冻结提取结果或 Embedding 模型。

## 复测口径

- 数据：官方中文版 dev，仅选 2026-11-16 目标复测中判错的 9 道 activation 和 4 道 write 题；冻结事实文件 `results/tables/timlmembench_product_20261114_dev_rerun_facts.json`。
- 回答、治理和裁判：`deepseek-v4.1-flash-1017`；Embedding：`qwen3.7-text-embedding-flash`，预测元数据确认有向量缓存。零空答案、零裁判解析错误。
- 首轮：激活 **8/9**，写入 **1/4**。前台题的禁止主动安排边界未被召回，补齐对外场景后单题再测 **1/1**。因此原 9 道激活旧错题都在各自最新测试中通过，但这不是一次统一代码版本的 9 题整轮分数。
- 本地相关测试：**68 passed，2 deselected**。两个 SQLite 测试因本机 pytest 临时目录访问被拒绝而未运行。

## 剩余错误

3 道 write 题仍把 add、update、revoke、preserve 和临时 skip 混淆。它们属于写入决策或写入审计回答的判定问题；本轮五阶段检索没有修复。激活题的改进也尚不能外推为整个 dev 分数提升，需要后续全量同版本测试。

## 文件

- 激活预测与评分：`results/tables/timlmembench_fivestage_activation9_20261117_{predictions,scores}.jsonl`
- 写入预测与评分：`results/tables/timlmembench_fivestage_write4_20261117_{predictions,scores}.jsonl`
- 前台单题修正后预测与评分：`results/tables/timlmembench_fivestage_frontdesk1_20261117_{predictions,scores}.jsonl`
