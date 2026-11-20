# TimlMemBench targeted full paired audit

Frozen session extracts: 61; SHA-256 `3f6549fef5acab7ff511d5db618b88af361249e55f00ba49116eb1e30ae8d6d7`.

| Condition | Overall | Write ops | Provenance | Extract calls | Replay hits | Model calls |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| baseline | 5/18 | 2/10 | 3/8 | 61 | 0 | 79 |
| source | 7/18 | 3/10 | 4/8 | 0 | 61 | 18 |
| governed | 6/18 | 3/10 | 3/8 | 0 | 61 | 79 |

## Paired transitions

- baseline_to_source: 3 wrong→right, 1 right→wrong.
- source_to_governed: 1 wrong→right, 2 right→wrong.

These are same-model judge decisions on a targeted dev subset, not a full benchmark score or an ACL/security proof.
