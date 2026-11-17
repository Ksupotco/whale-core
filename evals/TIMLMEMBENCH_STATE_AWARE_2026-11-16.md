# State-aware Memory 激活复测（2026-11-16）

## 配置与结果

- 中文 dev 中上一轮 14 道激活旧失败题；冻结提取候选：`results/tables/timlmembench_product_20261114_dev_rerun_facts.json`。
- 提取候选不重新生成；治理、回答、裁判均用 `deepseek-v4.1-flash-1017`；向量模型为 `qwen3.7-text-embedding-flash`。
- 相同题目集合，旧版 `boundary_gate_refined_activation` 为 **9/14**；本次 `state_aware_activation14` 为 **10/14**。14 条评分全部写入，裁判解析失败 0。
- 上轮仍错的 5 题中，本次 **2/5** 转对：`T0084Z__act004_choir_status_activation`、`T0094Z__act001_group_mango_scope`。另有 `T0088Z__act001_food_planning_activation` 从对变错。

## 本次仍错的 4 题

| 题目 | 裁判指出的第一处主要问题 |
| --- | --- |
| `T0084Z__act001_cold_chain_access_activation` | 虽答对旧门码失效和不得进门，仍引入猫零食、分享边界等无关信息。 |
| `T0087Z__act002_social_invite_activation` | 邀请与座位问题混入辣度、点餐规则。 |
| `T0088Z__act001_food_planning_activation` | 漏掉“熟洋葱少量可用”，把香菜籽限制泛化，并无证据地称没有撤销记录。 |
| `T0094Z__act004_oat_milk_group_drink` | 群聊饮料问题仍激活私聊健康话题约束，并推断没有撤销/分享边界。 |

这些失败集中在**约束适用范围和属性匹配**；`T0088Z` 还需检查熟洋葱事实在写入、召回和回答阶段的去向。裁判结果不能单独证明分数变化全部由代码造成，模型回答存在波动。

## 产物

- `results/tables/timlmembench_state_aware_activation14_predictions.jsonl`
- `results/tables/timlmembench_state_aware_activation14_scores.jsonl`
- 对照：`results/tables/timlmembench_boundary_gate_refined_activation_scores.jsonl`
