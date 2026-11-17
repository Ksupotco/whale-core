# TimlMemBench owner + contact fact pilot (2026-11-17)

This experiment uses the product `owner_fact_v1` writer for benchmark user B
and the new `contact_fact_v1` writer for authenticated contact A in private
sessions. Contact statements remain attributed to A. The companion reads both
through the current owner authorization, then applies its existing reranker and
activation gate. Other surfaces are skipped and counted. Gold labels are used
only by the official scorer after predictions are saved.

Both extraction/answering and judging used `deepseek-v4.1-flash-1017`. The
first Windows run failed to decode Chinese output and produced no predictions;
it still incurred model calls. After setting subprocess UTF-8, the fixed first
five dev activation questions (sample 0082) progressed from 0/5, to 0/5, to
1/5 during development. The final scored pilot for that sample was **5/5**.
The final writer saved the old entry code as `revoked` and supplied the current
package, cat care, shellfish, phone and emergency access rules to answers.

A distinct first-five holdout activation pilot (samples 0099 and 0100) scored
**0/5**. Sample 0099 retained the former code/key/inside-dropoff instruction
as `accepted` alongside a newer no-entry rule; the activation gate sometimes
selected both. Sample 0100 revoked its old composite instruction, but current
dropoff and silence rules were often not activated. The package question also
missed a subject-specific disclosure boundary. Sample 0099 contained a group
session; the adapter skipped its five turns. These results expose product
governance and activation gaps, not merely a benchmark mapping error.

The samples are too small and differently composed to represent full dev or
holdout accuracy. No full benchmark score or PR improvement claim follows from
them. Local predictions and judge files are under
`E:\2026-1\tmp\timlmembench_owner_contact_{dev5_v4,holdout5}_{predictions,scores}.jsonl`.
They contain synthetic benchmark conversations and are not committed.

## Selective new-version relation update

The old improved `FactService` was used as a reference for one narrow
transition: when an explicit new entry/delivery rule replaces an older
composite instruction from the same contact, the old instruction becomes
`needs_revalidation` and leaves current recall. Independent facts remain
accepted. The new logic is in `memory/fact_relations.py` and
`memory/contact_facts.py`; it does not replace `owner_fact_v1`.

Re-running only the first two holdout activation cases (sample 0099) with the
same DeepSeek model changed the result from **0/2 to 1/2**. The cold-medication
parcel question now receives the current L-12 placement and no-entry rule;
the old 8614/key/inside-dropoff records are marked `needs_revalidation`.
The follow-up silence question still fails because the situational boundary
does not enter answer context. This is a targeted regression result, not a new
five-question or full holdout score. Local files:
`E:\2026-1\tmp\timlmembench_owner_contact_holdout2_relation_{predictions,scores}.jsonl`.

## Explicit-change writer and nonresponse activation follow-up

The next run showed that the extractor could return the right quoted sentence
with invalid metadata. The contact writer then rejected the candidate, leaving
the revoked L-12 placement active and omitting the new R2 placement. It now
independently validates exact original sentences for explicit rule withdrawal
or delivery relocation. An old fact sharing the withdrawn rule identifier is
revoked; a changed delivery placement quarantines the former instruction.
The earlier no-entry rule remains active. A separate activation cue selects
the contact's signal and no-escalation boundary when the query says the contact
has not responded.

The final fixed two-question holdout rerun (sample 0099) scored **2/2** with
`deepseek-v4.1-flash-1017` for extraction, answering, and judging. The parcel
answer uses R2, rejects L-12 and the old code/key. The silence answer applies
the contact's "红水壶" signal and does not escalate merely because of no reply.
This is a local regression on two previously failing questions, not a full
holdout result. Predictions and judge output are saved at
`E:\2026-1\tmp\timlmembench_owner_contact_holdout2_explicit_changes_v2_{predictions,scores}.jsonl`.

The related owner, worker, host, contact, retrieval, and activation regression
suite passed **175 tests**. The new product write fallback is limited to the
contact import path; `owner_fact_v1` remains the live owner writer.
