# Activation integration review (2026-11-17)

This branch prepares the product reply path to select from current, authorized
owner facts and curated memories after those sources perform their own access
and validity checks. It adds task coverage, scoped boundaries, and a small
context-aware reranker. It does not add contact transcript import, a cloud
embedding provider, or a Cross-Encoder.

Local owner-fact, worker, companion, and activation tests: **155 passed**.
The tests caught a regression where an explicit question such as “香菜的事还记得吗”
lost the relevant curated fact; the branch now preserves a single distinctive
term match for explicit recall requests.

An offline TimlMemBench diagnostic supplies *gold current-state candidates*
directly to the activation gate, without testing writing, retrieval, or the
answer model. With task/attribute reranking, exact selection was **17/63 dev**
and **18/82 holdout**. Forbidden candidates were selected in **26 dev** and
**50 holdout** cases. These numbers are diagnostic, not product benchmark
accuracy. They show that the rule classifier and activation threshold still
over-select. This branch is therefore preparation for review, not a PR-ready
claim of improved activation.

Before proposing integration, inspect the forbidden selections, add cases for
ordinary conversational recall and audience-specific rules, and compare the
end-to-end product path on a fixed benchmark batch. The upstream `owner_fact_v1`
write, forget, and authorization services are unchanged here.
