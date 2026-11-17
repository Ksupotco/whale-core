# Whale memory 代码对照与贡献入口

核对时间：2026-11-17。这里区分三个版本，避免把不同分支的功能算作同一份代码：

- **本地开发基线**：`eval/timlmembench-native`，HEAD `6ba846d`；它从旧 `main` 的 `bbefca8` 分出。本地 memory 开发多数仍是未提交的工作区改动。
- **本地当前工作区**：在上述基线上，`src/whale_core/memory/` 有 6 个已跟踪文件修改、12 个新文件；还有 core、Studio、web、测试和 evals 的改动。2026-11-17 已创建个人 fork `Ksupotco/whale-core`；此文记录的是对齐前的开发快照，尚未向原项目提交 PR。
- **GitHub 当前 `main`**：`fdc895c`，相对旧基线 `bbefca8` 已前进 31 个提交。上游新增了 `owner_fact_v1` 完整的 owner 事实链路；本地检出尚不包含它。因此下列“本地新增”只相对本地基线成立，不能说是相对 GitHub 最新 `main` 的净新增。

## 代码位置

| 职责 | 本地旧基线 | 本地工作区的改动位置 | 当前情况 |
| --- | --- | --- | --- |
| 存储与普通精选记忆 | `memory/store.py`、`sqlite_store.py`、`curation.py`、`governance.py` | 原文件修改；新增 `facts.py`、`relations.py`、`temporal.py` | `FactService` 提供版本、更正、撤销、待定和决策记录；与上游 `OwnerFactService` 功能重叠 |
| 自动写入 | 原有 `memory/extraction.py` 与群聊路径 | 新增 `formation.py`、`write_decision.py`；修改 `core/companion.py`、`core/memory_commands.py` | 本地 owner 自述提取、候选决策和同步命令已接入部分聊天路径；目前覆盖范围仍窄 |
| 检索与激活 | `memory/curation.py`、`hybrid.py` | 修改原文件；新增 `context.py`、`roles.py`、`task_plan.py`、`rerank.py`、`activation.py`；修改 `core/prompt_composer.py` | 加了事实状态、边界/访问约束、BM25 与可选 Embedding 融合、任务需求和激活选择 |
| 情绪与画像 | 旧有连续性/人格相关模块 | 新增 `emotion.py`、`person_model.py`；修改 `core/companion.py` | 有情绪事件和后台画像原型；不等于功能规格中所有体验已经验收 |
| 记忆管理页 | 旧 Studio memory 路由与前端 | 修改 `api/studio/memory_browse.py`、`router.py`、`web/src/components/Memory.tsx`、`web/src/api.ts` | 新增事实历史和管理视图；与上游同名文件的改动可能冲突 |
| Benchmark | 原有结构化 TimlMemBench 起点 | 新增 `evals/timlmembench_deepseek.py`、`timlmembench_governed.py`、续跑/评分脚本及报告 | 中文 holdout 最新 128/181；评测提取/治理与产品自动写入是两条路径，不能把分数当作 `formation.py` 的验证 |

本地完整需求在 `docs/memory-functional-spec-2026-11-13.md`，架构草案在 `docs/memory-architecture-v1-2026-11-12.md`，近期写入判断在 `docs/memory-write-decision-2026-11-17.md`。这些是工作区文档，尚未进入上游。

## 与 GitHub 最新 main 的关键差异

上游现在已有 [`owner_facts.py`](https://github.com/amwangfan/whale-core/blob/main/src/whale_core/memory/owner_facts.py)、[`owner_fact_extraction.py`](https://github.com/amwangfan/whale-core/blob/main/src/whale_core/memory/owner_fact_extraction.py)、[`owner_fact_worker.py`](https://github.com/amwangfan/whale-core/blob/main/src/whale_core/memory/owner_fact_worker.py) 和 [`owner_fact_host.py`](https://github.com/amwangfan/whale-core/blob/main/src/whale_core/core/owner_fact_host.py)。上游[使用说明](https://github.com/amwangfan/whale-core/blob/main/docs/owner-fact-memory.md)写明：可信 owner QQ 私聊与眼镜共用事实空间、原话 span 校验、持久队列、自然更正、删除墓碑及统一回复预算，已接入宿主；群聊与其他成员不在这条自动采集路径中。

本地 `facts.py` / `formation.py` / `write_decision.py` 则是另一套精选事实与后台写入路径，状态、记录键和任务队列均不同。把本地工作区整体合入最新 `main` 会出现**双写、双召回、两种更正/删除语义**的风险；`companion.py`、`memory_browse.py`、`memory_commands.py` 等同名文件也已有上游变动。合并前应选定一个事实写入权威入口。建议以上游已经联通的 `owner_fact_v1` 为主，把本地经测试证明有价值的关系/时间判断与检索策略逐项移植，不保留第二套 owner 写入服务。

## Co-worker 的第一批贡献

1. **先做产品行为对照**：在最新 `main` 上，用固定的中文聊天序列测试重复自述、兼容补充、明确更正、撤销、不同主体/空间与跨设备召回。先指出 `owner_fact_v1` 实际失败在哪里；TimlMemBench 适配器错误不能直接当作产品写入错误。
2. **每个问题一个小 PR**：例如“明确更正不误伤同消息另一事实”或“撤销后旧版本不参与当前回答”。改上游 `owner_fact_v1` 对应领域模块，附离线正反例；不要整包复制本地 `facts.py`。
3. **检索单独推进**：只有写入状态与权限接口确定后，再移植本地 `context.py`、`roles.py`、`hybrid.py` 中有效的部分。用 dev 回归与真实聊天场景验收，holdout 不再当调参集。
4. **陪伴功能后续拆分**：情绪事件、每周画像和记忆页各有独立数据与验收规则；先确认与上游 owner 事实的来源、删除和空间契约，再分别实现。

实际协作流程：从最新 `main` 建新分支 → 写一个能复现问题的离线测试 → 做最小产品修改 → 跑相关测试和全量离线测试 → 在 PR 说明行为变化、证据、限制。若无直接写权限，先 fork 再向 `main` 提 PR。遵循上游 [`CONTRIBUTING.md`](https://github.com/amwangfan/whale-core/blob/main/CONTRIBUTING.md)：核心库保持 stdlib、测试离线可跑、密钥不入仓、一个 PR 一个可验收目标。
