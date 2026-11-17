# TimlMemBench dev 写入与激活旧失败题定向复测（2026-11-15）

## 配置

- 官方中文 dev raw-native 题；只选上一轮 144 题重跑中失败的写入 5 题和激活 14 题。
- 使用上一轮保存的提取候选 `timlmembench_product_20261114_dev_rerun_facts.json`；治理、回答和裁判重新调用 `deepseek-v4.1-flash-1017`。
- 检索使用 BM25、结构化情境重排与激活判断。19 条预测元数据均为 `embedding_mode=bm25`；没有实际使用向量模型。
- SenseNova 新密钥的 `/v1/models` 返回 8 个模型，无 Embedding；该账号的 `qwen3.7-text-embedding` 单句 `/v1/embeddings` 请求返回 404。旧版 SenseNova 向量端点返回 403，故未配置云端 Embedding。

## 结果

| 旧失败题类型 | 数量 | 本轮转对 | 仍错 | 裁判解析错误 |
| --- | ---: | ---: | ---: | ---: |
| 写入操作 | 5 | 3 | 2 | 0 |
| 激活适当性 | 14 | 7 | 7 | 0 |

这只说明旧失败题中有 10 题在本轮转对。未复测上一轮通过的题，不能据此推断 dev 全集正确率提升。治理、回答和裁判仍有模型波动。

## 失败线索

- `T0084Z__act001`：一次性码规则已经提取并写入，但最终回答上下文未见该规则，仍混入栗子的零食和喂食事实。首要问题在候选召回／排序。
- `T0096Z__act001`：保温袋、拍照、签收前电话确认和旧进门方式失效均已进入回答上下文；回答又激活了柜机码和旧钥匙投信箱等无关细节。首要问题在激活范围和复合事实粒度。
- `T0093Z__gb001`、`T0095Z__gb001` 写入题仍错，标签分别包含 `wrong_write_operation`、`missing_core_write_classification` 和 `sensitive_ephemeral_overstored`；应继续核对治理判定与事实状态，而不是只调检索分数。

## 文件

- `results/tables/timlmembench_v2_failed_write_predictions.jsonl`
- `results/tables/timlmembench_v2_failed_write_scores.jsonl`
- `results/tables/timlmembench_v2_failed_activation_predictions.jsonl`
- `results/tables/timlmembench_v2_failed_activation_scores.jsonl`
