# Boundary-aware Retrieval 与 Activation Gate 定向验证（2026-11-16）

## 产品改动

- `FactService` 写入 `memory_role`：`fact`、`preference`、`constraint`、`boundary`。旧记录在读取时按摘要推断，暂不迁移数据库。
- `build_memory_context` 先用调用方的 `allowed_spaces` 从存储层读取当前有效的 Boundary，再按主体、受众和话题筛选；最多补充 2 条，不与普通事实争夺检索 Top-K。没有显式授权空间时，不执行这条补充读取。
- 普通候选仍经 BM25/Embedding、情境重排和 `activate`；激活层抑制过期、已撤销、错话题及未匹配的 Boundary。适用的 Boundary 作为 `[当前边界]` 放在回答上下文前部。
- 群聊里“有没有人……”一类未点名提问，在话题匹配时也检查该群成员相关 Boundary；普通私聊不继承群聊边界。

## 同批题评测

中文 dev 中上一轮的 14 道激活旧失败题，冻结同一份事实候选，回答/治理/裁判均为 `deepseek-v4.1-flash-1017`，向量为 `qwen3.7-text-embedding-flash`。三次运行的预测和评分各 14 条，题目 ID 唯一，裁判解析失败均为 0。

| 版本 | 正确 |
| --- | ---: |
| BM25 | 7/14 |
| BM25 + Embedding | 7/14 |
| 初版 Boundary Gate | 8/14 |
| 收紧匹配后的 Boundary Gate | 9/14 |

收紧版的 `T0093Z__act002` 群聊健康题转对：`[当前边界] A不希望B在群里展开晕或过载的事` 进入上下文。`T0095Z__act001` 的群聊称呼边界也进入上下文并转对。`T0082Z__act005` 门锁题不再收到无关的社交边界。仍有同一组召回文本下答对/答错变化，例如 `T0087Z__act002` 和 `T0094Z__act004`，因此 9/14 不能全部归因于代码。

收紧版之后补了“有没有人不能吃芒果”的未点名群聊匹配，并单题复测 `T0094Z__act001`。身体细节 Boundary 已正确进入上下文，但该题仍错：回答把已撤销的芒果过敏当成当前事实。最新这项小改动尚未重跑全部 14 题，不能把 9/14 当作当前代码的完整新分数。

## 下一处产品短板

当前最明确的问题是已撤销事实在回答阶段仍被当作当前信息。应检查事实状态、召回文本和回答器提示中的撤销解释链，而不是继续放宽 Boundary 匹配或盲目增加 Top-K。大规模 144 题重跑留待这处问题修正后进行。

## 文件与测试

- `results/tables/timlmembench_boundary_gate_refined_activation_predictions.jsonl`
- `results/tables/timlmembench_boundary_gate_refined_activation_scores.jsonl`
- `results/tables/timlmembench_boundary_gate_mango_probe_predictions.jsonl`
- `results/tables/timlmembench_boundary_gate_mango_probe_scores.jsonl`
- 相关本地测试：55 通过、2 个使用 SQLite 临时目录的测试因当前 Windows 临时目录权限未运行。
