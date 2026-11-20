"""Small, typed view of memories selected for one reply."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

from whale_core.memory.transient import (
    is_transient_time_only, scrub_episode_summary, scrub_transient_time,
)
from whale_core.memory.types import MemorySpaceType
from whale_core.memory.activation import activate
from whale_core.memory.rerank import rerank
from whale_core.memory.facts import _active_fact
from whale_core.memory.roles import (
    access_relevance, boundary_relevance, constraint_relevance, memory_role,
    task_topics, _topics,
)
from whale_core.memory.store import MemoryKind
from whale_core.memory.task_plan import plan_query


def _recall(curator, query: str, allowed_spaces, **kwargs):
    """Preserve an explicitly resolved space filter for all callers."""
    if not kwargs.get("include_protected") or allowed_spaces is None:
        kwargs.pop("include_protected", None)
    if allowed_spaces is not None:
        kwargs["allowed_spaces"] = allowed_spaces
    return curator.recall(query, **kwargs)


def _clean_summary(raw: str) -> str:
    text = str(raw or "").strip()
    if not text or is_transient_time_only(text):
        return ""
    return scrub_episode_summary(text) or scrub_transient_time(text)


def _group_safe_summary(summary: str) -> str:
    """Keep an ordinary preference without its separate health explanation."""
    import re
    clauses = re.split(r"([，,；;。])", summary)
    kept: list[str] = []
    for index in range(0, len(clauses), 2):
        clause = clauses[index]
        if any(word in clause for word in
               ("胃不舒服", "身体不舒服", "病情", "心理咨询", "过敏原因")):
            continue
        kept.append(clause)
    return "，".join(part.strip(" ，,；;。") for part in kept if part.strip(" ，,；;。"))


def _dedupe_key(text: str) -> str:
    import re

    match = re.match(r"^用户：(.+?)(?:\n|$)", text, re.S)
    head = match.group(1) if match else text[:120]
    return re.sub(r"[\s\W]+", "", head)[:40]


def _asks_history(query: str) -> bool:
    return any(word in query for word in
               ("以前", "过去", "当时", "为什么认为", "为什么被撤销", "为何撤销",
                "更正链", "历史", "来源", "原话"))


def _state_anchor(summary: str, query: str) -> str:
    for word in ("芒果", "燕麦奶", "门码", "钥匙", "合唱", "过敏", "提醒"):
        if word in summary and word in query:
            return f"“{word}”"
    import re
    generic = {"不要", "不能", "可以", "已经", "现在", "以前", "为什么", "什么",
               "应该", "需要", "用户", "记录", "记忆", "事实", "关于", "撤销",
               "更正", "群里", "回复", "回答", "时候", "之后"}
    shared = {summary[i:i + 2] for i in range(len(summary) - 1)
              if re.fullmatch(r"[\u4e00-\u9fff]{2}", summary[i:i + 2])
              and summary[i:i + 2] in query and summary[i:i + 2] not in generic}
    return f"“{sorted(shared)[0]}”" if shared else "此事"


def _state_relevance(summary: str, query: str) -> bool:
    return _state_anchor(summary, query) != "此事"


@dataclass(frozen=True)
class MemoryContext:
    """Curated facts, other memories, and same-conversation snippets."""

    boundaries: tuple[str, ...] = ()
    access: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    statuses: tuple[str, ...] = ()
    facts: tuple[str, ...] = ()
    related: tuple[str, ...] = ()
    recalled_chat: tuple[str, ...] = ()
    group_archive_quotes: tuple[str, ...] = ()
    recent: tuple[str, ...] = ()
    patterns: tuple[str, ...] = ()
    other: tuple[str, ...] = ()
    history: tuple[str, ...] = ()
    scenario_time: str = ""

    def as_memories(self) -> tuple[str, ...]:
        """Compatibility view for existing reply models."""
        return (self.boundaries + self.access + self.constraints + self.statuses
                + self.history + self.recalled_chat + self.group_archive_quotes
                + self.facts + self.related + self.recent
                + self.patterns + self.other)

    def with_other(self, text: str) -> MemoryContext:
        return replace(self, other=self.other + (text,)) if text else self

    def with_patterns(self, patterns: tuple[str, ...]) -> MemoryContext:
        return replace(self, patterns=patterns) if patterns else self

    def with_recalled_chat(self, quotes: tuple[str, ...]) -> MemoryContext:
        return replace(self, recalled_chat=quotes) if quotes else self

    def with_group_archive_quotes(self, quotes: tuple[str, ...]) -> MemoryContext:
        return replace(self, group_archive_quotes=quotes) if quotes else self


def build_memory_context(*, curator, query: str, recent_episodes,
                         conversation_id: str, allowed_spaces,
                         now=None) -> MemoryContext:
    """Select existing curated facts and recent snippets without creating new facts."""
    facts: list[str] = []
    related: list[str] = []
    boundaries: list[str] = []
    access: list[str] = []
    constraints: list[str] = []
    statuses: list[str] = []
    history: list[str] = []
    retired_evidence_ids: set[str] = set()
    plan = plan_query(query, group=str(conversation_id).startswith("group:"))
    if curator is not None:
        include_protected = any(
            getattr(space, "type", None) is MemorySpaceType.PROTECTED_ORIGINAL
            for space in (allowed_spaces or ()))
        recall_options = {"limit": 12, "include_protected": include_protected}
        if now is not None:
            recall_options["now"] = now
        # An explicit authorization scope is required before this separate read.
        # Boundary records do not compete with ordinary facts for recall Top-K.
        rule_keys: set[str] = set()
        if allowed_spaces is not None:
            space_ids = tuple(str(getattr(space, "space_id", space))
                              for space in allowed_spaces)
            if space_ids:
                scored = {"boundary": [], "access_permission": [], "constraint": []}
                for record in curator.store.list_by_kind(
                        MemoryKind.PREFERENCE, space_ids=space_ids,
                        include_protected=include_protected):
                    if "curated" not in record.tags:
                        continue
                    summary = str(record.content.get("summary") or "")
                    role = memory_role(summary)
                    if not _active_fact(record, at=now):
                        if record.content.get("status") in {"revoked", "superseded", "needs_revalidation"}:
                            retired_evidence_ids.update(str(item) for item in
                                (record.content.get("evidence_ids") or
                                 [record.content.get("evidence_id")]) if item)
                        if (record.content.get("status") in
                                {"revoked", "needs_revalidation"}
                                and _state_relevance(summary, query)):
                            subject = str(record.content.get("subject_id") or "owner")
                            anchor = _state_anchor(summary, query)
                            status = str(record.content["status"])
                            state = ("已撤销，不可作为当前依据" if status == "revoked"
                                     else "待重新核实，核实前不可使用")
                            statuses.append(f"[状态证据] {subject}关于{anchor}的旧事实{state}"
                                            f"（记录 {record.key}；来源 {record.content.get('evidence_id', '')}）")
                            if _asks_history(query):
                                history.append(f"[历史版本：{status}] {summary}"
                                               f"（来源 {record.content.get('evidence_id', '')}）")
                        continue
                    if role == "boundary":
                        score = boundary_relevance(
                            summary, query,
                            subject_id=str(record.content.get("subject_id") or "owner"),
                            is_group=str(conversation_id).startswith("group:"))
                    elif role == "access_permission":
                        score = access_relevance(summary, query)
                    elif role == "constraint":
                        score = constraint_relevance(summary, query)
                    else:
                        continue
                    if score:
                        scored[role].append((score, record))
                for role, target, label in (
                    ("boundary", boundaries, "[当前披露边界] "),
                    ("access_permission", access, "[当前访问权限] "),
                    ("constraint", constraints, "[当前行为约束] "),
                ):
                    scored[role].sort(key=lambda row: (row[0], row[1].created_at),
                                      reverse=True)
                    ordered = scored[role]
                    if role == "constraint":
                        # Reserve evidence for each requested action before
                        # allowing several rules about one subject to fill Top-K.
                        covered = set()
                        chosen = []
                        for topic in sorted(task_topics(query)):
                            match = next((row for row in ordered
                                          if topic in _topics(str(row[1].content.get("summary") or ""))
                                          and row[1].key not in covered), None)
                            if match is not None:
                                chosen.append(match)
                                covered.add(match[1].key)
                            if len(chosen) == 2:
                                break
                        ordered = chosen + [row for row in ordered
                                            if row[1].key not in covered]
                    for _, record in ordered[:2]:
                        summary = _clean_summary(record.content.get("summary", ""))
                        if summary:
                            target.append(label + summary)
                            rule_keys.add(record.key)
        candidates = list(_recall(curator, query, allowed_spaces, **recall_options))
        # A multi-part task needs evidence for each part. A generic drop-off
        # request may refer to a stored "放东西" rule without naming its location.
        followups = []
        if any(word in query for word in ("放过去", "送过去", "送汤", "放东西")):
            followups.append("放东西")
        for subquery in followups:
            for hit in _recall(curator, subquery, allowed_spaces,
                               **{**recall_options, "limit": 6}):
                if all(hit.record.key != row.record.key for row in candidates):
                    candidates.append(hit)
        for activated in activate(query, rerank(query, candidates), limit=5,
                                  now=now):
            hit = activated.hit
            if hit.record.key in rule_keys:
                continue
            summary = _clean_summary(hit.record.content.get("summary", ""))
            if plan.group and summary:
                summary = _group_safe_summary(summary)
            if summary:
                if activated.role == "constraint":
                    summary = "[当前约束] " + summary
                elif activated.role == "conditional":
                    summary = "[仅在所述条件满足时] " + summary
                (facts if hit.record.content.get("stable_fact") else related).append(summary)

    if curator is None:
        recent = tuple(filter(None, (
            _clean_summary(record.content.get("summary", ""))
            for record in recent_episodes)))
        return MemoryContext(recent=recent, scenario_time=_scenario_time(query))

    seen = {_dedupe_key(text) for text in
            boundaries + access + constraints + statuses + facts + related}
    recent: list[str] = []
    recent_cutoff = (now or datetime.now(timezone.utc)) - timedelta(hours=72)
    for record in recent_episodes:
        if "chat" not in record.tags:
            continue
        if record.created_at < recent_cutoff:
            continue
        if str(record.content.get("source_turn_id") or "") in retired_evidence_ids:
            continue
        if str(record.content.get("conversation_id") or "") != conversation_id:
            continue
        summary = _clean_summary(record.content.get("summary", ""))
        key = _dedupe_key(summary)
        if not summary or not key or key in seen:
            continue
        seen.add(key)
        recent.append(summary)
        if len(recent) == 3:
            break
    # Keep the current conversation visible even when recall returns many facts.
    recent_slots = min(2, len(recent))
    protected_count = len(boundaries) + len(access) + len(constraints) + min(2, len(statuses))
    recall_slots = max(0, 8 - recent_slots - protected_count)
    selected_facts = tuple(facts[:recall_slots])
    selected_related = tuple(related[:recall_slots - len(selected_facts)])
    slots = max(0, 8 - protected_count - len(selected_facts) - len(selected_related))
    return MemoryContext(boundaries=tuple(boundaries), access=tuple(access),
                         constraints=tuple(constraints), statuses=tuple(statuses[:2]),
                         history=tuple(history[:3]), facts=selected_facts,
                         related=selected_related,
                         recent=tuple(recent[:slots]),
                         scenario_time=_scenario_time(query))


def _scenario_time(query: str) -> str:
    """Use an explicitly described scenario time before the wall clock."""
    import re

    match = re.search(r"(?:凌晨|清晨|早上|上午|中午|下午|傍晚|晚上|深夜)"
                      r"(?:\s*\d{1,2}(?::\d{2}|点(?:半)?)?)?", query)
    return match.group(0) if match else ""
