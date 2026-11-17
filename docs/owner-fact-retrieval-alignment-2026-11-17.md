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

The benchmark adapter now keeps the benchmark speaker ID stable and records
ingested turn IDs plus skipped turn IDs by reason. It does not convert contact
speech into owner self statements. The offline contact coverage audit joins
canonical state provenance only after prediction: among activation cases
associated with dev samples, **59/63** require at least one state attributed
to the contact speaker; the holdout figure is **81/82**. These are coverage
flags, not predicted errors or official benchmark scores.

The next narrow product addition is `contact_fact_v1` in `memory/contact_facts.py`.
It accepts an authenticated contact turn in an owner-owned working space,
reuses the existing quote extractor and validation, and stores the speaker ID,
source span, current status and correction link separately from owner facts.
The companion retrieves it only in an authorized owner context and labels it
as a contact utterance before activation. The benchmark bridge maps private
contact turns into this service; it still skips other surfaces. This is a
transcript-import capability, not a claim that the live QQ integration can
observe private chats between two other people.

The contact pilot now has a [separate report](../evals/TIMLMEMBENCH_OWNER_CONTACT_PILOT_2026-11-17.md):
the fixed first five dev activation questions scored **5/5**, while five
different holdout questions scored **0/5**. The holdout failures show stale
composite permissions and missing activation for current situational rules.

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
