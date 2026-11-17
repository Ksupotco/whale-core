# TimlMemBench 中文 dev 激活旧失败题：Qwen Embedding 对照（2026-11-16）

## 方法

- 官方 raw-native dev 的 14 道激活旧失败题，与 2026-11-15 BM25 复测使用相同题目 ID。
- 固定 `timlmembench_product_20261114_dev_rerun_facts.json` 中的提取候选；治理、回答和裁判仍使用 `deepseek-v4.1-flash-1017`。
- 本轮启用 `qwen3.7-text-embedding-flash`（1024 维），Base URL 为 `https://maas.qianwenaiapi.com/compatible-mode/v1`。密钥只保存在仓库外的本机文件，不写入预测和评分。
- 使用产品 `OpenAICompatibleEmbedding` 和 `HybridCurator`。为了适配模型单请求至多 20 条文本，将 provider 默认批次设为 20；评测 adapter 的嵌入总截止时间设为 30 秒。

## 完整性与结果

| 配置 | 预测/评分 | 正确 | 裁判解析失败 |
| --- | ---: | ---: | ---: |
| BM25（2026-11-15） | 14/14 | 7/14 | 0 |
| BM25 + Qwen Embedding | 14/14 | 7/14 | 0 |

本轮 14 条预测均标记 `embedding_mode=qwen3.7-text-embedding-flash`，每条 `embedding_vectors_cached` 均大于零（13–28），证明向量调用并非静默退化。相较 BM25，3 题转对、3 题转错；这组题没有净提升。

## 失败线索

- `T0084Z__act003` 转对：本轮召回了“不打算在桌游局汇报栗子病情”，替换掉猫粮位置；群聊披露边界更贴题。
- `T0093Z__act002` 转错：本轮漏掉“不希望在群里展开晕/过载，也不替 A 解释”，却加入“那天胃不舒服”；属于边界事实被相关健康细节挤出 Top-K。
- `T0090Z__act002`、`T0095Z__act001` 从对转错，但召回文本及顺序与 BM25 完全相同。两题变化不能归因于向量召回，需考虑回答/裁判模型波动。
- `T0096Z__act001` 仍错。仅加语义相似度不足以解决复合事实粒度及当下激活范围问题。

## 判断

当前证据不足以支持对 144 题做全量付费重跑。下一步应优先让权限/分享边界、事实属性与当前问题在候选选择和激活阶段发挥明确作用，再用同一批题复测；同时尽量冻结回答或进行重复评分，以区分检索变化与模型波动。写入旧失败题另行处理，Embedding 不解决写入操作判定。

## 产物与验证

- `results/tables/timlmembench_qwen_embedding_failed_activation_predictions.jsonl`
- `results/tables/timlmembench_qwen_embedding_failed_activation_scores.jsonl`
- 产品 provider 单句实测成功，返回 1024 维；首题 adapter 冒烟确认缓存 22 条向量。
- 相关测试 47 项通过；3 项使用 pytest 临时目录的测试因当前 Windows 目录访问拒绝在 setup/清理阶段报错，未发现断言失败。
