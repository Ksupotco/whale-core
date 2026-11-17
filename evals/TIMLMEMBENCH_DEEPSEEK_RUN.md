# TimlMemBench 中文集：DeepSeek 提取、回答与裁判

更新日期：2026-11-10。whale-core 基线提交 `6ba846d0b8382f31c75bdb9606a76089c58d4be3`；
TimlMemBench 官方仓库提交 `447b195344a2feaf995176446379e60632831f94`。

## 结果

| 官方划分 | 样本 | 题目 | DeepSeek 裁判判对 | GPT-4o 裁判判对 |
| --- | ---: | ---: | ---: | ---: |
| train | 81 | 637 | 199/637（31.2%） | 未评分：原账户余额耗尽 |
| dev | 17 | 144 | 59/144（41.0%） | 56/144（38.9%） |
| holdout | 19 | 181 | 62/181（34.3%） | 66/181（36.5%） |
| 三划分合计 | 117 | 962 | 320/962（33.3%） | 不完整 |
| holdout 无记忆对照 | 19 | 181 | 13/181（7.2%） | 31/181（17.1%） |

相同 DeepSeek 裁判下，holdout 记忆方案比无记忆对照高 49/181 题，即 27.1 个百分点。
逐题比较为 53 题由错转对、4 题由对转错、9 题都对、115 题都错。
这说明该评测协议中的记忆步骤对 holdout 有明显帮助，但绝对正确率仍较低。

两种裁判对 dev 主方案有 131/144 题结论相同，对 holdout 主方案有 155/181 题相同，
对 holdout 无记忆对照只有 151/181 题相同。尤其无记忆对照的 13 与 31 题差异较大；
不同裁判的分数不可混用来计算记忆增益。DeepSeek 同时负责回答和裁判，
可能存在同模型评审偏差；GPT-4o 结果保留为独立参照。

## 协议

- 提取、回答和本轮裁判均使用 AgentScope API 的 `deepseek-v4.1-flash-1017`，
  关闭思考模式，流式调用。裁判沿用官方 `score_baseline_predictions.py` 的提示词、
  JSON 解析规则和 `pass` 判定；只替换了裁判 API 与模型。
- 每段官方原始会话调用一次提取；事实需指向源发言 ID 和说话人，最多 6 条，
  再通过 `MemoryCurator.remember()` 写入 whale-core 的精选记忆。
- 每题在新会话中经 `WhaleCompanion.chat()` 和现有 `MemoryCurator.recall()` 回答；
  无记忆对照使用同一回答模型和提示，但跳过提取。
- 此适配器假定基准数据可用于实验性提取，**不代表生产环境同意或权限状态**。
  原始 benchmark 的治理操作没有完整映射到 whale-core 生产写入、撤销和空间授权流程。
  结果是此评测协议的端到端表现，不是原生产品或论文中其他系统的严格可比分数。
- train 是官方训练划分，不能用其分数代替 dev 或 holdout 的泛化表现。

## 复核与用量

- 三划分 962 条预测和评分与官方题目顺序一致，题目 ID 无重复，回答均非空。
  holdout 无记忆对照的 181 条预测和评分也逐题对齐。四组共 1143 条裁判结果，
  JSON 解析失败 0 条。
- train 预测由中断点续跑，第二个样本曾整体重跑以保持该样本的一致提取状态。
  train 裁判在 243 题后按剩余题目分四段并行，按原题序合并且核对全部 ID。
- 本轮 DeepSeek 裁判共 1143 次调用，API 记录的输入 491,412 tokens、输出
  99,485 tokens，包含 holdout 无记忆对照。未核对本轮账户 Credits 差额，
  不将 tokens 或 Credits 换算成人民币账单金额。
- 相关适配器测试 6 项通过。此前 AgentScope 账户在预测阶段结束时显示
  179.62 Credits；这不是本轮裁判结束后的余额。

## 产物

- 适配器：`evals/timlmembench_deepseek.py`
- 预测续跑：`evals/timlmembench_resume.py`
- 裁判续跑：`evals/timlmembench_judge_resume.py`
- DeepSeek 裁判分数：`results/tables/timlmembench_deepseek_{train,dev,holdout}_scores_deepseek.jsonl`
- 同裁判无记忆对照：`results/tables/timlmembench_deepseek_no_memory_holdout_scores_deepseek.jsonl`
- 此前 GPT-4o 分数与汇总仍在 `results/tables/` 中。

逐题预测和评分文件含 benchmark 查询及回答原文，发布前需核查数据集许可。
API 密钥仅通过临时进程环境变量传递，未写入仓库或结果文件。

## 2026-11-11：治理适配器 dev 对照实验

本节是独立于上表 v1 全集结果的追加实验。两组都使用同一个改进后的回答提示词、
同一个 DeepSeek 模型和官方裁判提示词；治理组另外对每段原始会话做第二次操作识别，
通过真实 `MemoryCurator.remember()`、`forget_gone_everywhere()` 和 `supersede()`
处理事实、更正与撤销。两组都按 `surface` 分记忆空间。裁判仍是 DeepSeek，
所以这些数值不等同于独立模型复评。

| dev 中文 144 题 | 判对 | 正确率 |
| --- | ---: | ---: |
| v1 原始提示词与单次提取 | 59 | 41.0% |
| 改进回答提示词，关闭治理操作 | 116 | 80.6% |
| 改进回答提示词，开启治理操作 | 119 | 82.6% |

治理组与同提示词对照组逐题对齐：110 题都对，19 题都错，9 题仅治理组对，
6 题仅对照组对。两次运行各自独立调用提取模型，且裁判有波动；净增 3 题
不能当成治理操作的因果增益。尤其一个撤销题两组答案语义接近却被裁判判成不同结果。
旧版到改进版的较大跃升主要与回答提示词变化相关，不能归因于 memory 存储层变化。

| 题型 | 题数 | 治理组判对 | 关闭治理判对 |
| --- | ---: | ---: | ---: |
| activation_appropriateness | 68 | 61 | 60 |
| revoke_correctness | 16 | 14 | 15 |
| supersede_correctness | 6 | 5 | 4 |
| preserve_correctness | 2 | 2 | 2 |
| boundary_preservation | 19 | 19 | 18 |
| scope_isolation | 8 | 7 | 7 |
| write_op_accuracy | 10 | 3 | 3 |
| provenance_recoverability | 8 | 1 | 0 |
| unsupported_reason_abstention | 4 | 4 | 4 |
| proactive_overreach | 3 | 3 | 3 |

治理组 17 个样本共实际执行 12 次撤销、8 次更正、0 次显式保留；
另有 53 个操作候选被证据门禁拒绝，117 次操作识别调用无 JSON 解析失败。
离线端到端测试证明：合法撤销与更正改变真实存储和召回结果；非本人、虚构引文、
跨空间的操作被拒绝；显式 `allowed_spaces` 限定召回。官方题目未传入当前受众或
ACL，因此 `scope_isolation` 得分只反映回答表现，**不能证明第三方分享权限隔离**。
`preserve_correctness` 2/2 也不能证明显式保留操作识别成功，因为实际没有执行
`preserve` 事件；目前保留是“不要撤销”的状态约束。

失败案例显示两类适配缺口。`write_op_accuracy` 仅 3/10，部分问题问“Session 3
之后如何更新记忆”，但回答模型只看到当前事实摘要，看不到会话序号与操作轨迹，
因而错误地回答“证据不足”。`provenance_recoverability` 仅 1/8：底层精选记忆
保存 `evidence_id`，召回命中的 `reasons` 也包含该 ID，但 `WhaleCompanion`
给回答模型的上下文只注入摘要，没有来源锚点。因此现在没有证据表明存储层
的撤销、更正能力需要修改；下一轮应先在适配器的解释类问题中提供授权范围内
的来源 ID 与操作记录，再做定向对照。产品若要求用户可追溯解释，则应单独设计
来源呈现接口；撤销的原文不得为了追溯而重新保留。

治理适配器位于 `evals/timlmembench_governed.py`；端到端测试位于
`tests/test_timlmembench_governed.py`。两组各 144 条预测与评分顺序、ID 一致，
回答非空，裁判 JSON 解析失败均为 0。相关适配器与 memory 测试 13 项通过。
另一次较广的 memory 测试为 53 项通过、7 项因 Windows 临时目录权限在
fixture 建立阶段报错，没有产品断言失败。实验操作轨迹只保留操作类型、来源 ID
和目标 key，不复制被撤销事实原文；已清理本轮预测日志中旧的摘要字段。

结果文件：`results/tables/timlmembench_{governed,prompt_only}_dev_predictions.jsonl`
以及对应的 `_scores_deepseek.jsonl`。这次只重跑官方 dev；train 和 holdout
仍保留上表 v1 结果，不能将不同版本的分数混为同一全集。
