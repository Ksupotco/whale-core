# LoCoMo 记忆证据召回基线

中文版 150 题的旧版／当前版问答对照见
[LOCOMO_ZH150_COMPARISON_2026-11-16.md](LOCOMO_ZH150_COMPARISON_2026-11-16.md)。
下文保留最初英文数据集检索基线的说明。

本阶段只测 **证据召回**，不测问答准确率或自动记忆提炼。脚本把官方
`locomo10.json` 的每条文本对话显式写成一条 curated 记忆，再通过
`MemoryCurator` 或 `HybridCurator` 召回。数据集的标准答案和证据 ID
仅在召回结束后用于计算指标，绝不作为查询输入。

## 数据与运行

从 [LoCoMo 官方仓库](https://github.com/snap-research/locomo) 获取
`data/locomo10.json`，保持原文件不变。下例使用 Python 3.11+，无需额外依赖：

```bash
PYTHONPATH=src python3 -m evals.locomo_retrieval \
  --data /path/to/locomo10.json \
  --out results/tables/locomo_lexical_100.json \
  --limit 100 --seed 42 --top-k 5 --method lexical
```

`--full` 跑全部题目。`--method hash` 使用项目的确定性散列向量，
它**没有语义理解能力**；只有配置真实多语言 embedding 后的实验才能
称作语义检索。`--limit` 按五类题目轮流取样，固定 `seed` 后题目集合稳定。

报告包含逐题检索到的对话 ID、标准证据 ID、召回耗时，以及：

- `any_hit_rate`：Top-K 中至少有一条标准证据的题目比例；
- `all_hit_rate`：Top-K 覆盖全部标准证据的题目比例；
- `mean_evidence_recall`：逐题已召回标准证据占比的平均值。

第五类不可回答题不计入证据召回分母；它需要在后续端到端问答实验中
评估拒答。报告的 `protocol` 字段须随结果一同保存。每段对话使用独立
记忆库，避免跨样本串题。

## 解释边界

这是 **text-only curated-turn retrieval** 协议：图像原文件未随官方
数据发布，脚本不借用图片描述替代图像；它也绕开了产品的自动提炼和
授权流程。因此结果不能写成“whale-core 长期记忆端到端准确率”，
更不能与论文的 LoCoMo QA 分数直接比较。下一阶段须单独接入真实写入
流程、固定回答模型和裁判，并报告中文规则对英文原始对话的影响。
