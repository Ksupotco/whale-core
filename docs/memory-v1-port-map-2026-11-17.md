# Memory v1 → current upstream: selective port map

The earlier improved branch is a reference implementation, not a drop-in
replacement for upstream `owner_fact_v1`. This branch extends the newer product
paths and keeps the upstream authority and evidence gates.

| Capability in improved v1 | Current upstream-based branch | Remaining gap |
| --- | --- | --- |
| Quote extraction and write decisions | Owner uses `OwnerFactExtractor` and `OwnerFactService`; contact transcript import has a separate attributed writer | No common, stable entity/attribute contract across both writers |
| Corrections and revocation | Owner revisions, forget fence and tombstones retained; contact has explicit revoke plus narrow `needs_revalidation` for replaced composite access/delivery instructions | Composite dependency propagation for owner facts and general contact facts remains incomplete |
| Storage and provenance | Owner records, source spans and KV jobs retained; contact records keep speaker, source span and status | Contact history cleanup and live import host wiring are not complete |
| Retrieval | Scoped Curator BM25 with optional embedding RRF; owner and contact candidates join at reply time | Owner/contact readers are lexical; no Cross-Encoder or shared state-aware query plan |
| Activation | Pure reranker and activation gate run after authorization | Situation and audience matching still miss holdout cases |
| Product integration | Owner fact path is wired into live hosts; contact writer is used by the benchmark import path | Live QQ cannot observe A/B private conversations by default |
| Evaluation | Fixed DeepSeek dev five: 5/5; distinct holdout five: 0/5 before this relation update; first two holdout questions: 2/2 after explicit-change writer and activation fixes | No full new-version dev/holdout score; a small pilot cannot support a PR performance claim |

The first new-version adaptation from the former `FactService` is a pure
`revalidation_reason` decision. An explicit change to a contact's entry or
delivery plan quarantines an older composite instruction in the same owner
space and contact identity. It preserves unrelated facts and keeps the old
source for historical explanation. This narrow behavior is tested before any
broader state migration.

This branch does **not** port the former write pipeline wholesale. The live
owner writer still follows upstream `owner_fact_v1`; contact extraction is
available for authenticated private transcript import and the benchmark, but
is not wired as a general QQ group writer. The retrieval path uses the current
Curator and optional embedding RRF; a Cross-Encoder has not been added. The
current progress therefore establishes an interface and two targeted holdout
fixes, not feature parity or a benchmark-level gain over improved v1.
