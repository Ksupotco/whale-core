"""Facts a contact stated in a conversation available to the owner.

This is a separate namespace from owner_fact_v1: a contact's first-person
statement must never become an owner self-report. The trusted host supplies
the speaker identity and the owner's current authorization.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
import re

from whale_core.memory.curation import _terms
from whale_core.memory.owner_fact_extraction import (
    OwnerFactExtractor, has_correction_context, validate_candidate,
)
from whale_core.memory.owner_facts import (
    FactKind, FactRecord, FactStatus, MemoryAuthorization, SourceRef,
    UserEvidence, _opposite_claim, contains_credential,
)
from whale_core.memory.store import MemoryKind, MemoryRecord, MemoryStore


TAG = "contact_fact_v1"

CONTACT_PROMPT_TEMPLATE = """从这位已识别联系人的原话中，选择以后可能影响回答的事实、偏好、安排、禁止事项和明确更正。
说话人可以省略“我”，例如“别再用旧门码”也是本人给出的规则。不要选问句、玩笑、猜测或转述他人的说法。
quote 必须是原文连续完整句子，包含前面的条件和后面的否定/例外，不要只截取半句；不补写说话人的名字。只选最多 {max_candidates} 条。
kind 只能是 fact、preference、episode 三个英文值；禁令和边界也填 fact，绝不填“禁止事项”。
predicate 只能是小写英文字母/数字/下划线，例如 door_code；做不到填 null，绝不填中文。value 必须是 quote 原文子串。
明确更正或撤销下方同一事项时才填写 correction_target，值必须是既有事实里的 id；既有事实为空时必须填 null。
valid_from / valid_to 只在原文明写日期时填写 ISO8601 带时区值；相对时间留 null。
观测时间：{observed_at}；时区：{tz}
既有事实：{existing}
只输出 JSON：{{"candidates":[{{"kind":"fact","quote":"原文片段","predicate":null,"value":null,"valid_from":null,"valid_to":null,"correction_target":null}}]}}
联系人消息原文：
【
{text}
】
"""


@dataclass(frozen=True)
class ContactEvidence:
    owner_id: str
    space_id: str
    principal_id: str
    contact_id: str
    evidence_id: str
    conversation_id: str
    text: str
    received_at: datetime


@dataclass(frozen=True)
class ContactFactHit:
    record: MemoryRecord
    score: float


class ContactFactService:
    """Extract, version and retrieve contact self-reports in the owner's space."""

    def __init__(self, store: MemoryStore, authority, extractor: OwnerFactExtractor):
        self.store = store
        self.authority = authority
        self.extractor = extractor

    def _authorized(self, auth: MemoryAuthorization) -> bool:
        check = getattr(self.authority, "authorize_principal_now", None)
        current = (check(auth.owner_id, auth.space_id, auth.principal_id)
                   if callable(check) else self.authority.authorize_now(
                       auth.owner_id, auth.space_id))
        space = self.store.get(f"space:{auth.space_id}")
        return bool(isinstance(space, dict)
                    and space.get("type") == "owner_working_copy"
                    and space.get("owner") == auth.owner_id
                    and current and current.owner_id == auth.owner_id
                    and current.space_id == auth.space_id
                    and current.principal_id == auth.principal_id)

    def _records(self, owner_id: str, space_id: str, contact_id: str = ""):
        for kind in (MemoryKind.EPISODE, MemoryKind.PREFERENCE):
            for record in self.store.list_by_kind(kind, space_ids=(space_id,)):
                content = record.content
                if (TAG in record.tags and content.get("owner_id") == owner_id
                        and (not contact_id or content.get("contact_id") == contact_id)):
                    yield record

    @staticmethod
    def _as_fact(record: MemoryRecord) -> FactRecord:
        content = record.content
        return FactRecord(
            fact_id=record.record_id, owner_id=str(content["owner_id"]),
            space_id=record.space_id, kind=FactKind(str(content["fact_kind"])),
            summary=str(content["summary"]),
            source_refs=(SourceRef(str(content["evidence_id"]),
                                   int(content["span_start"]), int(content["span_end"])),),
            status=FactStatus(str(content["status"])),
            revision=int(content["revision"]), created_at=record.created_at,
            observed_at=record.created_at, predicate=content.get("predicate"),
            value=content.get("value"),
        )

    @staticmethod
    def _complete_sentence(candidate: FactCandidate, text: str) -> FactCandidate | None:
        """Keep a model-selected span together with its condition and exception."""
        separators = "。！？!?；;\n"
        start = candidate.span_start
        end = candidate.span_end
        while start > 0 and text[start - 1] not in separators:
            start -= 1
        while end < len(text) and text[end] not in separators:
            end += 1
        if end < len(text):
            end += 1
        quote = text[start:end].strip()
        if not quote or len(quote) > 300:
            return None
        start = text.index(quote, start, end)
        return replace(candidate, summary=quote, quote=quote,
                       span_start=start, span_end=start + len(quote))

    def observe(self, evidence: ContactEvidence, auth: MemoryAuthorization) -> int:
        """Process one authenticated contact turn; return accepted fact count."""
        if (not self._authorized(auth) or evidence.owner_id != auth.owner_id
                or evidence.space_id != auth.space_id
                or evidence.principal_id != auth.principal_id
                or not evidence.contact_id or not evidence.evidence_id
                or evidence.received_at.tzinfo is None):
            return 0
        if not evidence.text.strip() or contains_credential(evidence.text):
            return 0
        existing = [r for r in self._records(evidence.owner_id, evidence.space_id,
                                              evidence.contact_id)
                    if r.content.get("status") == "accepted"]
        existing.sort(key=lambda r: r.created_at, reverse=True)
        snapshot = {r.record_id: self._as_fact(r) for r in existing[:8]}
        user_evidence = UserEvidence(
            evidence_id=evidence.evidence_id, owner_id=evidence.owner_id,
            principal_id=evidence.principal_id,
            conversation_id=evidence.conversation_id, channel="import",
            space_id=evidence.space_id, text=evidence.text,
            received_at=evidence.received_at,
        )
        candidates = self.extractor.extract(user_evidence, tuple(snapshot.values()))
        accepted = 0
        retained_quotes: list[str] = []
        for candidate in sorted(candidates, key=lambda item: len(item.quote), reverse=True):
            candidate = self._complete_sentence(candidate, evidence.text)
            if candidate is None:
                continue
            if any(candidate.quote in quote for quote in retained_quotes):
                continue
            if candidate.correction_target not in snapshot:
                candidate = replace(candidate, correction_target=None)
            if candidate.predicate and not re.fullmatch(
                    r"[a-z][a-z0-9_]{0,31}", candidate.predicate):
                candidate = replace(candidate, predicate=None)
            decision, _ = validate_candidate(
                candidate, user_evidence,
                {fact_id: fact.revision for fact_id, fact in snapshot.items()},
                allow_implicit_subject=True,
                allow_conditional_instruction=True,
                allow_reported_subject=True)
            if decision != "accepted":
                continue
            target = snapshot.get(candidate.correction_target or "")
            revocation = any(word in candidate.quote for word in
                             ("别用", "不要", "不再", "停用", "作废", "无效", "删了", "撤销"))
            same_value_target = False
            if revocation and target is None and candidate.value:
                matches = [fact for fact in snapshot.values()
                           if fact.value == candidate.value
                           and len(_terms(fact.summary) & _terms(candidate.quote)) >= 2]
                if len(matches) == 1:
                    target = matches[0]
                    same_value_target = True
            replace_old = bool(
                target and ((candidate.predicate and target.predicate
                             and candidate.predicate == target.predicate)
                            or same_value_target)
                and (candidate.value != target.value
                     or _opposite_claim(target.summary, candidate.summary)
                     or revocation)
                and (has_correction_context(user_evidence, candidate) or revocation))
            if (candidate.correction_target and not replace_old
                    and target and candidate.predicate
                    and candidate.predicate == target.predicate):
                continue
            fingerprint = sha256((evidence.owner_id + "\0" + evidence.space_id
                                  + "\0" + evidence.contact_id + "\0"
                                  + evidence.evidence_id + "\0"
                                  + str(candidate.span_start)).encode()).hexdigest()
            key = f"contact_fact:{fingerprint}"
            stamp = evidence.received_at.astimezone(timezone.utc)
            content = {
                "owner_id": evidence.owner_id,
                "contact_id": evidence.contact_id,
                "summary": candidate.quote,
                "evidence_id": evidence.evidence_id,
                "span_start": candidate.span_start,
                "span_end": candidate.span_end,
                "status": "accepted",
                "fact_kind": candidate.kind.value,
                "predicate": candidate.predicate,
                "value": candidate.value,
                "revision": target.revision + 1 if replace_old else 1,
                "supersedes": target.fact_id if replace_old else None,
                "valid_from": (candidate.valid_from.isoformat()
                               if candidate.valid_from else None),
                "valid_to": (candidate.valid_to.isoformat()
                             if candidate.valid_to else None),
            }
            new = MemoryRecord(
                kind=MemoryKind.PREFERENCE if candidate.kind == FactKind.PREFERENCE
                else MemoryKind.EPISODE,
                key=key, content=content, created_at=stamp, tags=(TAG,),
                space_id=evidence.space_id,
            )

            def commit(tx):
                if tx.get_record(key) is not None:
                    return False
                if replace_old:
                    old = tx.get_record(next(
                        (r.key for r in existing if r.record_id == target.fact_id), ""))
                    if old is None or old.content.get("status") != "accepted":
                        return False
                    tx.put_record(replace(old, content={**old.content,
                                                        "status": ("revoked" if revocation
                                                                   else "superseded")}))
                tx.put_record(new)
                return True

            if self.store.transact(commit):
                accepted += 1
                retained_quotes.append(candidate.quote)
        return accepted

    def retrieve_hits(self, auth: MemoryAuthorization, query: str, *,
                      limit: int = 24) -> tuple[ContactFactHit, ...]:
        if not self._authorized(auth) or limit <= 0:
            return ()
        now = datetime.now(timezone.utc)
        terms = _terms(query)
        records = list(self._records(auth.owner_id, auth.space_id))
        contacts = {str(r.content.get("contact_id") or "") for r in records}
        named = {contact for contact in contacts if contact and re.search(
            rf"(?<![A-Za-z0-9]){re.escape(contact)}(?![A-Za-z0-9])", query)}
        hits = []
        for record in records:
            content = record.content
            if named and content.get("contact_id") not in named:
                continue
            if content.get("status") != "accepted":
                continue
            lower = content.get("valid_from")
            upper = content.get("valid_to")
            if (lower and datetime.fromisoformat(lower) > now or
                    upper and datetime.fromisoformat(upper) <= now):
                continue
            summary = str(content.get("summary") or "")
            overlap = terms & _terms(summary)
            if terms and not overlap:
                continue
            score = len(overlap) / len(terms) if terms else 0.0
            hits.append(ContactFactHit(record, score))
        hits.sort(key=lambda hit: (hit.score, hit.record.created_at), reverse=True)
        return tuple(hits[:limit])

    def list_states(self, auth: MemoryAuthorization) -> tuple[MemoryRecord, ...]:
        """Owner-authorized inspection of current and historical contact states."""
        if not self._authorized(auth):
            return ()
        return tuple(self._records(auth.owner_id, auth.space_id))
