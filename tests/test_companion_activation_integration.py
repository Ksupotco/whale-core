"""Reply assembly uses activation after source-specific retrieval checks."""

from whale_core.core.companion import WhaleCompanion
from whale_core.memory.curation import MemoryCurator
from whale_core.memory.store import InMemoryStore


class CaptureBrain:
    def __init__(self):
        self.memories = ()

    def reply(self, text, *, persona="", memories=(), history=(), images=()):
        self.memories = tuple(memories)
        return "ok"


def test_companion_covers_delivery_and_access_without_topic_noise():
    store = InMemoryStore()
    curator = MemoryCurator(store)
    for index in range(8):
        curator.remember(f"今天猫粮在窗边{index}", source="test",
                         evidence_id=f"noise-{index}", consent=True,
                         space_id="owner")
    curator.remember("旧门码不能再开门", source="test", evidence_id="access",
                     consent=True, space_id="owner")
    curator.remember("冷藏件放保温袋；签收先打电话", source="test",
                     evidence_id="delivery", consent=True, space_id="owner")
    brain = CaptureBrain()
    WhaleCompanion(store, brain, curator=curator).chat(
        "冷藏件到了，能进门吗，签收先联系谁？", episodic=False,
        allowed_spaces=("owner",))
    assert any("旧门码" in item for item in brain.memories)
    assert any("保温袋" in item for item in brain.memories)
    assert all("猫粮" not in item for item in brain.memories)


def test_group_reply_cannot_activate_other_groups_curated_memory():
    store = InMemoryStore()
    curator = MemoryCurator(store)
    curator.remember("不要在本群透露我的健康细节", source="test",
                     evidence_id="private-rule", consent=True,
                     stable_fact=True, space_id="group:one")
    brain = CaptureBrain()
    WhaleCompanion(store, brain, curator=curator).chat(
        "群里有人问我的身体怎么样", conversation_id="group:two",
        allowed_spaces=("group:two",), episodic=False)
    assert all("健康细节" not in item for item in brain.memories)
