# Owner fact retrieval alignment (2026-11-17)

## Product path

`OwnerFactService.retrieve_hits` returns the same authorized, accepted, current
facts as `retrieve`, with their lexical relevance scores. The companion merges
these with already scoped Curator hits, runs context-aware reranking, then runs
activation. Neither reranking nor activation reads storage or grants access.

Curated retrieval uses BM25 without an embedding provider. With a configured
provider, `HybridCurator` fuses BM25 and embedding ranks by weighted RRF. The
QQ live example now uses `DynamicHybridCurator`, which falls back to BM25 when
no provider is available. This reranker uses task and attribute hints; it is
not a Cross-Encoder.

The legacy writer was not copied. One narrow decision was adapted into
`owner_fact_v1`: an explicit same-object polarity change such as "I like coffee"
to "I do not like coffee" can supersede the earlier fact even when the
structured `value` is still "coffee". The existing evidence, authorization,
revision, and correction-context checks still govern the transition.

## Checks

- Relevant owner-fact, retrieval, embedding, correction, forgetting, and group
  permission regressions: **250 passed, 1 deselected**. The deselected episode
  deduplication test also fails on clean upstream.
- TimlMemBench oracle-candidate activation diagnostic: sample 0082 **5/5**;
  dev **19/63**; holdout **18/82** exact selections. Four dev and two holdout
  cases expect memories excluded by the current-state input filter, so this
  diagnostic is not an official benchmark score.
- Fixed five-question raw-session owner-fact pilot, DeepSeek extraction, answer
  and judge: **1/5**. The sample's important rules were spoken by contact A,
  while `owner_fact_v1` legitimately ingests only user B's self statements.
  This pilot cannot measure the full A/B relationship memory task.

## Remaining gap

Activation still overselects and misses candidates on other dev and holdout
cases. The oracle-candidate diagnostic isolates this gap, while the owner-fact
pilot isolates the benchmark scope mismatch. Neither number justifies a PR
claim of improved end-to-end benchmark accuracy.
