from datetime import datetime, timezone

from whale_core.memory.contact_facts import ContactEvidence, ContactFactService
from whale_core.memory.owner_fact_extraction import FactCandidate, OwnerFactExtractor
from whale_core.memory.owner_fact_extraction import validate_candidate
from whale_core.memory.owner_facts import FactKind, MemoryAuthorization, UserEvidence
from whale_core.memory.spaces import ensure_owner_working_copy
from whale_core.memory.sqlite_store import SqliteMemoryStore
from whale_core.memory.store import InMemoryStore


class Authority:
    def authorize_principal_now(self, owner_id, space_id, principal_id):
        if (owner_id, space_id, principal_id) == ("B", "owner-working", "B:qq"):
            return MemoryAuthorization(owner_id, space_id, principal_id,
                                       "test", "test", (space_id,))
        return None


class Extractor:
    def extract(self, evidence, existing):
        if "团子" in evidence.text:
            quote = "其实我养的猫叫团子"
            target = existing[0].fact_id if existing else None
            value = "团子"
        else:
            quote = "猫叫年糕" if "猫叫年糕" in evidence.text else "我养的猫叫年糕"
            target = None
            value = "年糕"
        if quote not in evidence.text:
            return ()
        start = evidence.text.index(quote)
        return (FactCandidate(FactKind.FACT, quote, evidence.evidence_id,
                              quote, start, start + len(quote),
                              predicate="pet_name", value=value,
                              correction_target=target),)


def test_contact_fact_correction_and_subject_scope():
    store = InMemoryStore()
    ensure_owner_working_copy(store, owner="B")
    service = ContactFactService(store, Authority(), Extractor())
    auth = Authority().authorize_principal_now("B", "owner-working", "B:qq")
    stamp = datetime.now(timezone.utc)

    def evidence(contact, eid, text):
        return ContactEvidence("B", "owner-working", "B:qq", contact,
                               eid, "private:A-B", text, stamp)

    old = evidence("A", "e1", "猫叫年糕")
    assert service.observe(old, auth) == 1
    assert service.observe(old, auth) == 0
    assert service.observe(evidence("C", "e2", "猫叫年糕"), auth) == 1
    assert service.observe(evidence("A", "e3", "其实我养的猫叫团子"), auth) == 1
    hits = service.retrieve_hits(auth, "A养的猫叫什么")
    by_subject = {(h.record.content["contact_id"], h.record.content["summary"])
                  for h in hits}
    assert ("A", "其实我养的猫叫团子") in by_subject
    assert ("A", "猫叫年糕") not in by_subject
    assert ("C", "猫叫年糕") not in by_subject
    assert service.retrieve_hits(auth, "C养的猫叫什么")[0].record.content[
        "contact_id"] == "C"
    assert service.retrieve_hits(MemoryAuthorization("B", "owner-working",
                                                      "someone-else", "test", "test"),
                                 "猫") == ()
    assert service.list_states(MemoryAuthorization("B", "owner-working",
                                                   "someone-else", "test", "test")) == ()


def test_implicit_subject_is_only_allowed_for_contact_validation():
    stamp = datetime.now(timezone.utc)
    evidence = UserEvidence("e1", "B", "B:qq", "private:A-B", "qq",
                            "owner-working", "别再用旧门码", stamp)
    candidate = FactCandidate(FactKind.FACT, evidence.text, evidence.evidence_id,
                              evidence.text, 0, len(evidence.text))
    assert validate_candidate(candidate, evidence, {}) == ("reject", "no_first_person")
    assert validate_candidate(candidate, evidence, {},
                              allow_implicit_subject=True) == ("accepted", "")


def test_contact_revocation_same_value_disables_old_permission():
    class CodeExtractor:
        def extract(self, evidence, existing):
            quote = evidence.text
            revoked = "别用" in quote
            return (FactCandidate(FactKind.FACT, quote, evidence.evidence_id,
                                  quote, 0, len(quote),
                                  predicate="禁用门码" if revoked else "door_code",
                                  value="0419",
                                  correction_target="旧门码作废" if revoked else None),)

    store = InMemoryStore()
    ensure_owner_working_copy(store, owner="B")
    service = ContactFactService(store, Authority(), CodeExtractor())
    auth = Authority().authorize_principal_now("B", "owner-working", "B:qq")
    stamp = datetime.now(timezone.utc)
    old = ContactEvidence("B", "owner-working", "B:qq", "A", "e1",
                          "private:A-B", "门口临时代码 0419", stamp)
    new = ContactEvidence("B", "owner-working", "B:qq", "A", "e2",
                          "private:A-B", "别用 0419 了，旧码已经无效", stamp)
    assert service.observe(old, auth) == 1
    assert service.observe(new, auth) == 1
    hits = service.retrieve_hits(auth, "A 的旧门码 0419")
    assert [hit.record.content["summary"] for hit in hits] == [new.text]


def test_contact_quote_keeps_condition_and_exception():
    store = InMemoryStore()
    ensure_owner_working_copy(store, owner="B")
    auth = Authority().authorize_principal_now("B", "owner-working", "B:qq")

    class PartialExtractor:
        def extract(self, evidence, existing):
            quote = "药片在蓝色小盒子里"
            start = evidence.text.index(quote)
            return (FactCandidate(FactKind.FACT, quote, evidence.evidence_id,
                                  quote, start, start + len(quote)),)

    service = ContactFactService(store, Authority(), PartialExtractor())
    text = "如果我让你帮忙喂，药片在蓝色小盒子里，但没有我说就不用碰。"
    evidence = ContactEvidence("B", "owner-working", "B:qq", "A", "e1",
                               "private:A-B", text, datetime.now(timezone.utc))
    assert service.observe(evidence, auth) == 1
    assert service.retrieve_hits(auth, "A的药片")[0].record.content["summary"] == text


def test_rule_kind_alias_keeps_valid_quote():
    class Model:
        def complete(self, prompt, *, timeout):
            return ('{"candidates":[{"kind":"禁止事项",'
                    '"quote":"别用旧码","predicate":null,"value":null}]}')

    evidence = UserEvidence("e1", "B", "B:qq", "private:A-B", "qq",
                            "owner-working", "别用旧码", datetime.now(timezone.utc))
    assert OwnerFactExtractor(Model()).extract(evidence, ()) == ()
    candidates = OwnerFactExtractor(Model(), allow_rule_kind_aliases=True).extract(
        evidence, ())
    assert len(candidates) == 1 and candidates[0].kind is FactKind.FACT


def test_contact_fact_sqlite_transaction_and_recall():
    class Extractor:
        def extract(self, evidence, existing):
            quote = evidence.text
            return (FactCandidate(FactKind.FACT, quote, evidence.evidence_id,
                                  quote, 0, len(quote)),)

    store = SqliteMemoryStore(":memory:")
    try:
        ensure_owner_working_copy(store, owner="B")
        service = ContactFactService(store, Authority(), Extractor())
        auth = Authority().authorize_principal_now("B", "owner-working", "B:qq")
        evidence = ContactEvidence("B", "owner-working", "B:qq", "A", "e1",
                                   "private:A-B", "别再用旧门码",
                                   datetime.now(timezone.utc))
        assert service.observe(evidence, auth) == 1
        assert service.retrieve_hits(auth, "A的旧门码")[0].record.content[
            "summary"] == evidence.text
    finally:
        store.db.close()
