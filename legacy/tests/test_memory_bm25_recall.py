"""Lexical ranking regression for crowded Chinese memory contexts."""

from whale_core.memory.curation import MemoryCurator
from whale_core.memory.store import InMemoryStore


def test_rare_subject_and_action_outweigh_generic_question_words():
    store = InMemoryStore()
    curator = MemoryCurator(store)
    curator.remember("栗子可以在群里分享近况", source="test",
                     evidence_id="target", consent=True)
    for index in range(8):
        curator.remember(f"我想知道今天怎么安排{index}", source="test",
                         evidence_id=f"distractor-{index}", consent=True)

    hits = curator.recall("我想知道栗子如何分享", limit=5)
    assert hits[0].record.content["evidence_id"] == "target"
    assert len(hits) == 5
