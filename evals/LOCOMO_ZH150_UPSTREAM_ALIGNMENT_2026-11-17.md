# 中文 LoCoMo 150 题：上游对齐后的检索诊断

基线是 `amwangfan/whale-core` 的 `fdc895c`；候选仅修改
`MemoryCurator.recall` 的词项排序，使用 BM25。事实写入、权限和
`owner_fact_v1` 均沿用上游实现，未接 Embedding。

固定种子 42，从中文译本 10 段对话抽取 150 题，Top-5；数据 SHA-256 为
`3e3d9aa70c59c35b0254553314e15a0df4a340a2aea7d3781b95c7d1944c7f5d`。
每条对话发言直接存为 curated memory，再做证据检索；118 题有可评分的标准
证据。脚本为 `evals/locomo_retrieval.py`。未调用答案模型或裁判。

| 指标 | 上游基线 | BM25 候选 |
| --- | ---: | ---: |
| Top-5 至少命中一条标准证据 | 55/118 | 68/118 |
| Top-5 命中全部标准证据 | 36/118 | 49/118 |
| 平均证据覆盖率 | 38.2% | 48.3% |
| 平均检索耗时（本机） | 19.3 ms | 50.3 ms |

同题配对：18 题从未命中变命中，5 题从命中变未命中。退步发生在
`conv-44:56`、`conv-44:46`、`conv-48:84`、`conv-48:87`、`conv-48:0`；
其中 3 题是多跳、2 题是时间题。多跳至少命中一条反而从 13/30 降为
12/30，仍需针对多证据召回另行设计。此实验不衡量自动提取、更正、
实际回答正确率，也不是官方完整 LoCoMo 分数。

运行命令（需自行准备上述数据文件；结果文件不要提交入仓）：

```powershell
$env:PYTHONPATH='src'
D:\anaconda3\envs\kaggle_nli\python.exe evals\locomo_retrieval.py --data <中文LoCoMo JSON路径> --out <本地结果路径> --limit 150 --seed 42 --top-k 5 --method lexical
```

上游 `owner_fact_v1` 相关 153 项基线测试、改动后扩大到 176 项相关测试均
通过。全套测试尚不能宣称通过：当前环境缺 `cryptography`、`websockets`，
另有未改动的情节去重测试与 CLI doctor 测试失败；两者都在干净的
`fdc895c` 工作区复现。先厘清上游基线再提交 PR。
