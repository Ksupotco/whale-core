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
