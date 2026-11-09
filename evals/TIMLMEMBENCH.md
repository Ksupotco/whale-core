# TimlMemBench 中文原始会话结构评测

## 范围与结论

本评测对接 [TimlMemBench 官方仓库](https://github.com/lyirs/TimlMemBench)
`raw_native_cases` JSONL 协议，使用 `zh` 全集。它是**离线记忆结构诊断**，
不是论文可比的端到端问答分数：回答模型是固定的 `<ABSTAIN>` 探针，
官方 LLM 语义裁判未运行，也未提供可验证的用户授权上下文。

数据集原始会话是两个人的既有对话；`WhaleCompanion.chat()` 则会生成新的
助手回复。为了不伪造历史，本适配器把每条原始发言保存为 `chat` 情节，
并将带说话人标签的文本加入源会话的有界历史。它**不**调用
`MemoryCurator.remember()`，不使用数据集的 gold state、expected answer、
`derived_memory_scopes` 或其他派生金标字段。跨会话查询调用
`WhaleCompanion.chat(..., episodic=False)`，通过其现有
`MemoryCurator.recall()` 路径注入证据，探针模型只记录收到的上下文。

| TimlMemBench 阶段 | whale-core 映射 | 当前限制 |
| --- | --- | --- |
| `reset` | 新建 `InMemoryStore`、`MemoryCurator`、`WhaleCompanion` | 样本互相隔离；不测 SQLite 持久化 |
| `ingest_session` | 原话进入 `chat` Episode 和本会话 History | 人类对话无法等价重演为用户与机器人问答；不自动提炼事实 |
| `query` | `WhaleCompanion` 的跨会话召回与上下文构建 | 无回答模型；无可解析的读者身份或授权空间 |
| `close` | 结束 JSONL 进程 | 无外部资源 |

关键结构：`History` 只保留每会话最近 24 条消息；普通 `chat` Episode
可以用于本会话短期背景，但 `MemoryCurator.recall()` 只召回有 `curated`
标签的记录。查询在新会话中发起，因此不会把源会话近期情节当作跨会话长期记忆。

## 已执行的离线运行

- 日期：2026-11-09；Windows；`D:\anaconda3\python.exe` 3.13.9；无 API 费用。
- whale-core：`bbefca8aa5d98d048c2fefb028126bbb93bc0511`。
- TimlMemBench：`447b195344a2feaf995176446379e60632831f94`；官方发布校验及 16 项自测通过。
- 中文 `raw_native_cases --split all`：117 个样本、962 个治理/激活案例。
- 962/962 个案例均有 0 条精选记忆和 0 条跨会话召回；回答探针全部返回 `<ABSTAIN>`。
- 官方启发式评分对这 962 条探针回答的九项指标均为 0。**这不是 whale-core 的问答准确率**，因为探针根本不生成实质答案；它只确认写入到长期召回的结构断点。
- 查询适配器平均用时 0.281 ms；不包含任何模型调用或完整系统服务开销。

运行产物在 `results/tables/timlmembench_zh_all_structural_*`。逐题预测文件含
基准查询文本，发布或提交前需检查数据集许可与报告范围；建议只提交
聚合报告和可复现脚本。

## 复现

在独立目录克隆官方基准，使用已存在的 Python 3.11+ 解释器，无需安装包：

```powershell
git -c http.proxy= -c https.proxy= clone https://github.com/lyirs/TimlMemBench.git E:\2026-1\tmp\TimlMemBench
Set-Location E:\2026-1\tmp\TimlMemBench
D:\anaconda3\python.exe code\validate_release.py
D:\anaconda3\python.exe code\run_baseline_predictions.py `
  --mode raw_native_cases --lang zh --split all `
  --baseline whale_core_structural_native_v1 `
  --adapter-command "D:\anaconda3\python.exe E:\2026-1\whale-core\evals\timlmembench_native.py" `
  --output E:\2026-1\whale-core\results\tables\timlmembench_zh_all_structural_predictions.jsonl
```

## 正式评测仍需完成

1. 明确 benchmark 的 `user/contact` 与产品用户、机器人、空间授权的身份映射。官方 `query` 不提供独立的读者身份；不能从问题文字推断授权后暗中扩权。Scope/Boundary 结果必须单独标注这一条件。
2. 接入固定的回答模型、温度与预算，记录写入和问答 token、延迟及成本。小样本校验后才运行全量。
3. 用官方语义裁判评估答案，并与同模型的 No Memory 对照；启发式评分仅用于协议烟测。
4. 在同一协议上逐步测试自动提取、去重和冲突更新，再加隔离、撤销及删除负向测试。

本机现有 memory/companion 测试中，42 项通过、5 项在创建/清理 Windows
临时目录时因 `PermissionError` 中断；这是当前运行环境的限制，不能记作
产品逻辑测试通过或失败。新增的适配器测试 2 项均通过。
