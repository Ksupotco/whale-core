"""Evidence-gated governance variant of the TimlMemBench DeepSeek adapter.

This adapter reads only the chronological raw sessions. Benchmark gold labels,
operation turn IDs, and expected states are never supplied to the model.
Official raw-native queries have no authorization context, so their audience
boundaries remain a limitation; tests can supply allowed_spaces explicitly.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from evals.timlmembench_deepseek import (
    DeepSeekAnswerModel, DeepSeekMemoryAdapter, parse_object)
from whale_core.memory.curation import _terms
from whale_core.memory.governance import forget_gone_everywhere, supersede
from whale_core.memory.facts import FactService
from whale_core.memory.extraction import contains_sensitive
from whale_core.memory.store import MemoryKind


class GovernedAnswerModel(DeepSeekAnswerModel):
    source_context: str = ""
    case_type: str = ""

    def task_instruction(self) -> str:
        instruction = ("\n\n评测任务：根据上面可见的当前记忆回答行动和治理问题。"
                "若记忆说明旧许可已收回、安排已更正或信息不得转述，"
                "应明确说出现在应该与不应该做什么，以及依据。"
                "撤销一条旧许可只影响指向的旧版本；若另有当前有效的新约束，须分别判断。"
                "写入分类题逐项区分新增、保留、更正、撤销与未提升；"
                "未提升不能直接等同跳过，应根据原话判断临时性。"
                "只在缺少关键证据时回答‘证据不足’，不要用它替代已有证据的结论。"
                "涉及向第三方转述时遵守记忆中的分享边界，"
                "不要披露被禁止的私人细节。不要编造未记录的事实。")
        if self.case_type == "write_op_accuracy":
            instruction += (
                "只处理问题点名的时段及直接相关的事实族，不逐条复述整个样本。"
                "对相关信息分别说明实际 add、update、revoke、preserve、skip"
                "与临时状态。已撤销事实的首次写入仍是历史 add，"
                "不能因为现在失效就把当时的 add 改判为 skip；"
                "先写该事实族的首次 add，再写后续撤销或更正。"
                "旧许可停止使用须明确写为 revoke；若同时形成新的禁止边界，"
                "分别说明两者。不要把无关物品和临时建议列为 add。"
                "无结构化写入记录的原话按证据判断，不直接视为 skip。")
        return instruction

    def output_budget(self) -> int:
        return 704 if self.case_type == "write_op_accuracy" else 448

    def reply(self, text: str, *, persona: str = "", memories=(), history=()) -> str:
        if self.source_context:
            persona = (persona + "\n\n可核验的当前记忆来源与状态变更：\n"
                       + self.source_context)
        if self.case_type == "write_op_accuracy":
            memories = ()  # The requested operation window has its own evidence view.
        return super().reply(text, persona=persona, memories=memories,
                             history=history)


def _source_anchor(turn_id: str) -> str:
    """Use only the source ID, never benchmark gold or a deleted quote."""
    match = re.search(r"(?:^|__)s(\d+)_t\d+", turn_id)
    return (f"Session {int(match.group(1))}, turn {turn_id}" if match
            else f"turn {turn_id}")


class GovernedDeepSeekMemoryAdapter(DeepSeekMemoryAdapter):
    def __init__(self, *, max_queries: int, max_calls: int,
                 model: str = "deepseek-v4.1-flash-1017",
                 no_memory: bool = False, apply_governance: bool = True,
                 fact_cassette: dict[str, list[dict[str, Any]]] | None = None,
                 source_context_enabled: bool = True,
                 max_facts_per_session: int = 6) -> None:
        self.apply_governance = apply_governance
        self.source_context_enabled = source_context_enabled
        super().__init__(max_queries=max_queries, max_calls=max_calls,
                         model=model, no_memory=no_memory,
                         fact_cassette=fact_cassette,
                         max_facts_per_session=max_facts_per_session)

    def reset(self, sample_id: str) -> None:
        super().reset(sample_id)
        self.model = GovernedAnswerModel(self.client)
        self.companion.model = self.model
        self.space_ids: set[str] = set()
        self.operations_applied = {"revoke": 0, "supersede": 0, "preserve": 0}
        self.operations_rejected = 0
        self.operation_rejections: list[dict[str, str]] = []
        self.operation_trace: list[dict[str, str]] = []
        self.decision_trace: list[dict[str, str]] = []
        self.governance_calls = 0
        self.governance_parse_failures = 0
        self.source_turns: dict[str, tuple[str, str, str]] = {}
        self._staged_candidates: list[dict[str, str]] = []

    def _write_candidate(self, *, fact: str, turn_id: str,
                         speaker_id: str, space_id: str,
                         operation: str = "add", slot_key: str = "") -> None:
        if not self.apply_governance:
            return super()._write_candidate(
                fact=fact, turn_id=turn_id, speaker_id=speaker_id,
                space_id=space_id, operation=operation, slot_key=slot_key)
        self._staged_candidates.append({
            "fact": fact, "turn_id": turn_id, "speaker_id": speaker_id,
            "space_id": space_id, "operation": operation, "slot_key": slot_key,
        })

    @staticmethod
    def space_for(session: dict[str, Any]) -> str:
        surface = str(session.get("surface") or "session").strip()[:80]
        return f"timlmembench:{surface}"

    def memory_space_for_session(self, session: dict[str, Any]) -> str:
        return self.space_for(session)

    def _target(self, event: dict[str, Any], *, space_id: str, speaker_id: str):
        """Resolve only an existing curated fact owned by the same speaker/space."""
        key = str(event.get("target_key") or "")
        evidence_id = str(event.get("target_turn_id") or "")
        if key:
            candidates = [self.store.get_record(key)]
        elif evidence_id:
            candidates = [
                rec for kind in (MemoryKind.EPISODE, MemoryKind.PREFERENCE)
                for rec in self.store.list_by_kind(kind)
                if evidence_id in (rec.content.get("evidence_ids")
                                   or [rec.content.get("evidence_id")])
            ]
        else:
            return None, "missing_target_reference"
        candidates = [rec for rec in candidates if rec is not None]
        if not candidates:
            return None, "target_not_found"
        candidates = [rec for rec in candidates if rec.space_id == space_id]
        if not candidates:
            return None, "cross_space_target"
        candidates = [rec for rec in candidates if "curated" in rec.tags]
        if not candidates:
            return None, "target_not_curated"
        candidates = [rec for rec in candidates if "superseded" not in rec.tags]
        candidates = [rec for rec in candidates
                      if rec.content.get("status", "active") == "active"]
        if not candidates:
            return None, "target_already_superseded"
        candidates = [rec for rec in candidates
                      if str(rec.content.get("summary") or "").startswith(f"[{speaker_id}] ")]
        if not candidates:
            return None, "wrong_speaker_target"
        if len(candidates) != 1:
            return None, "ambiguous_target"
        return candidates[0], ""

    def _reject(self, event: Any, reason: str) -> None:
        self.operations_rejected += 1
        self.operation_rejections.append({
            "op": str(event.get("op") or "") if isinstance(event, dict) else "",
            "source_turn_id": (str(event.get("turn_id") or "")
                               if isinstance(event, dict) else ""),
            "reason": reason,
        })

    def ingest_session(self, session: dict[str, Any]) -> None:
        space_id = self.space_for(session)
        self._staged_candidates = []
        # Operation candidates must refer to the state *before* this session's
        # extraction. Otherwise a correction can target its own new assertion.
        prior = [
            {"key": rec.key, "summary": rec.content.get("summary"),
             "evidence_id": rec.content.get("evidence_id")}
            for kind in (MemoryKind.EPISODE, MemoryKind.PREFERENCE)
            for rec in self.store.list_by_kind(kind, space_ids=(space_id,))
            if "curated" in rec.tags and "superseded" not in rec.tags
        ]
        prior_keys = {item["key"] for item in prior}
        turns = [t for t in session.get("turns") or ()
                 if isinstance(t, dict) and str(t.get("text") or "").strip()]
        for turn in turns:
            turn_id = str(turn.get("turn_id") or "")
            if turn_id:
                self.source_turns[turn_id] = (
                    space_id, str(turn["text"]),
                    str(turn.get("speaker_id") or turn.get("speaker_role") or "unknown"))
        super().ingest_session(session)
        if not self.no_memory:
            self.space_ids.add(space_id)
        if self.no_memory or not self.apply_governance:
            return
        if not turns:
            return
        source = [{"turn_id": str(t.get("turn_id") or ""),
                   "speaker_id": str(t.get("speaker_id") or t.get("speaker_role") or "unknown"),
                   "text": str(t["text"])} for t in turns]
        unwritten = {turn["turn_id"] for turn in source} - {
            item["turn_id"] for item in self._staged_candidates
        }
        candidates = [
            {"candidate_id": index, "turn_id": item["turn_id"],
             "speaker_id": item["speaker_id"], "fact": item["fact"]}
            for index, item in enumerate(self._staged_candidates)
        ]
        prompt = (
            "根据按时间排列的对话与当前记忆，找出说话人明确作出的状态变更。"
            "重点检查旧计划被取消、许可被收回、日期/范围/偏好被更改；"
            "不要求出现‘忘记’或‘更正’字样，但必须有本人发言直接支持。"
            "本次只找治理操作，不提取普通新事实。"
            "只返回 JSON 对象：{\"events\":[{\"op\":\"revoke|supersede|preserve\","
            "\"turn_id\":字符串,\"speaker_id\":字符串,\"quote\":当前发言中的原文片段,"
            "\"fact\":更正后的完整当前事实或空字符串,\"target_key\":已有记忆的key或空字符串,"
            "\"target_turn_id\":旧事实的发言ID或空字符串,"
            "\"related_target_keys\":同一次撤销或更正还需失效的旧记忆key数组,"
            "\"support_turn_ids\":同一次变更的原因或替代安排的本人发言ID数组}],"
            "\"decisions\":[{\"turn_id\":发言ID,\"speaker_id\":说话人,"
            "\"action\":\"skip|ephemeral|add_candidate|preserve_candidate\","
            "\"quote\":原文短片段}],"
            "\"candidate_decisions\":[{\"candidate_id\":整数,"
            "\"action\":\"skip|ephemeral|add|pending\",\"slot_key\":字符串}]}。"
            "所有事件必须有当前发言的原文 quote，并指向下面确切的旧记忆；"
            "已有记忆仅含本段对话之前的事实。若一个旧许可拆成多条记忆，"
            "把所有受同一更正影响的旧key列入 related_target_keys；"
            "不得把仍有效的独立安排列入。"
            "revoke 用于‘停用、别再、不再、作废’等收回旧许可或取消旧安排，"
            "即使同时新增禁止边界也先撤销旧许可；"
            "supersede 只用于明确给出替代日期、地点、数值或方式；"
            "preserve 用于明确保持不变的旧事实。"
            "仅因新消息提到同一主题，不能自动撤销；不确定目标就返回空数组。"
            "不把他人的提问或建议当作本人授权。最多 8 个事件，按发言顺序。\n"
            "support_turn_ids 最多 3 个，只选本段对话中同一说话人对这次变更的"
            "直接解释或替代安排；不选无关的新话题，不选他人的确认性提问。"
            "目标旧记忆必须属于发言者本人，不能把他人答应或复述当成本人旧事实。"
            "对于下列尚无结构化写入的发言，再给最多 8 个 decisions："
            "skip 是无需记的内容，ephemeral 是只在短期生效，"
            "add_candidate 是可能漏写的新事实，preserve_candidate 是可能漏记的旧边界确认。"
            "这些只是判定候选，不代表已执行写入；不确定时不输出。"
            "不要把撤销的旧内容作为新事实。候选发言ID："
            + json.dumps(sorted(unwritten), ensure_ascii=False) + "\n"
            "以下提取候选尚未入库。每个候选都必须输出一条 candidate_decision："
            "明确、可长期保存的事实用 add；不应记的用 skip；"
            "短期事项用 ephemeral；无法判断时用 pending。"
            "有明确同一主体、同一适用情境的可变事实槽位时才填写 slot_key，"
            "例如提醒偏好；独立事实留空。遗漏的候选将保持待定，不会自动 add。提取候选："
            + json.dumps(candidates, ensure_ascii=False) + "\n"
            "当前空间：" + space_id + "\n已有记忆："
            + json.dumps(prior, ensure_ascii=False) + "\n本段对话："
            + json.dumps(source, ensure_ascii=False)
        )
        raw, _ = self.client.complete(
            [{"role": "user", "content": prompt}], max_tokens=1280)
        self.governance_calls += 1
        try:
            parsed = parse_object(raw)
            events = parsed.get("events")
            decisions = parsed.get("decisions")
            candidate_decisions = parsed.get("candidate_decisions")
        except (ValueError, json.JSONDecodeError):
            events = None
            decisions = None
            candidate_decisions = None
        selected = {}
        if isinstance(candidate_decisions, list):
            for decision in candidate_decisions[:len(candidates)]:
                if not isinstance(decision, dict):
                    continue
                index = decision.get("candidate_id")
                action = decision.get("action")
                if (type(index) is int and 0 <= index < len(candidates)
                        and action in ("add", "skip", "ephemeral", "pending")):
                    selected[index] = decision
        for index, item in enumerate(self._staged_candidates):
            decision = selected.get(index, {})
            action = str(decision.get("action") or "pending")
            slot_key = str(decision.get("slot_key") or item["slot_key"]).strip()[:80]
            super()._write_candidate(
                fact=item["fact"], turn_id=item["turn_id"],
                speaker_id=item["speaker_id"], space_id=item["space_id"],
                operation=action, slot_key=slot_key)
        if not isinstance(events, list):
            self.governance_parse_failures += 1
            return
        by_turn = {t["turn_id"]: (index, t) for index, t in enumerate(source)}
        if isinstance(decisions, list):
            seen_decisions = set()
            for decision in decisions[:8]:
                if not isinstance(decision, dict):
                    continue
                turn_id = str(decision.get("turn_id") or "")
                speaker_id = str(decision.get("speaker_id") or "")
                action = str(decision.get("action") or "")
                quote = str(decision.get("quote") or "").strip()
                found = by_turn.get(turn_id)
                if (turn_id not in unwritten or turn_id in seen_decisions
                        or action not in ("skip", "ephemeral", "add_candidate", "preserve_candidate")
                        or found is None or found[1]["speaker_id"] != speaker_id
                        or not quote or quote not in found[1]["text"]):
                    continue
                seen_decisions.add(turn_id)
                self.decision_trace.append({
                    "action": action, "source_turn_id": turn_id,
                    "space_id": space_id,
                    "quote": "" if contains_sensitive(quote) else quote[:140],
                })
        last_index = -1
        for event in events[:8]:
            if not isinstance(event, dict):
                self._reject(event, "invalid_event")
                continue
            op = str(event.get("op") or "")
            turn_id = str(event.get("turn_id") or "")
            speaker_id = str(event.get("speaker_id") or "")
            quote = str(event.get("quote") or "").strip()
            found = by_turn.get(turn_id)
            if (found is None or found[0] < last_index
                    or found[1]["speaker_id"] != speaker_id
                    or not quote or quote not in found[1]["text"]):
                self._reject(event, "source_evidence_mismatch")
                continue
            last_index = found[0]
            fact = str(event.get("fact") or "").strip()
            if op not in self.operations_applied:
                self._reject(event, "unsupported_operation")
                continue
            target, target_error = self._target(
                event, space_id=space_id, speaker_id=speaker_id)
            if target is None:
                self._reject(event, target_error)
                continue
            if target.key not in prior_keys:
                self._reject(event, "target_not_prior_state")
                continue
            related = event.get("related_target_keys") or []
            if not isinstance(related, list) or len(related) > 8:
                self._reject(event, "invalid_related_targets")
                continue
            targets = [target]
            for related_key in related:
                if not isinstance(related_key, str):
                    self._reject(event, "invalid_related_target")
                    continue
                other, error = self._target(
                    {"target_key": related_key}, space_id=space_id,
                    speaker_id=speaker_id)
                if other is None or other.key not in prior_keys:
                    self._reject(event, error or "related_target_not_prior_state")
                    continue
                if other.key not in {item.key for item in targets}:
                    targets.append(other)
            support_ids = []
            proposed_support = event.get("support_turn_ids") or []
            if isinstance(proposed_support, list):
                for item in proposed_support[:3]:
                    support_id = str(item)
                    support = by_turn.get(support_id)
                    if (support_id != turn_id and support is not None
                            and support[1]["speaker_id"] == speaker_id
                            and support_id not in support_ids):
                        support_ids.append(support_id)
            new_key = ""
            if op == "revoke":
                revoked_any = False
                for old in targets:
                    if old.kind is MemoryKind.PREFERENCE:
                        current = self.store.get_record(old.key)
                        if (current is None or "superseded" in current.tags
                                or current.content.get("status", "active") not in
                                ("active", "needs_revalidation")):
                            continue
                        try:
                            FactService(self.store).apply_candidate(
                                "", operation="revoke", target_key=old.key,
                                source="timlmembench_session", evidence_id=turn_id,
                                space_id=space_id, subject_id=speaker_id,
                                support_evidence_ids=tuple(support_ids))
                        except KeyError:
                            continue
                        revoked_any = True
                    else:
                        forget_gone_everywhere(self.store, old.key)
                        revoked_any = True
                if not revoked_any:
                    self._reject(event, "target_not_active")
                    continue
            elif op == "supersede":
                if not fact or len(fact) > 300:
                    self._reject(event, "invalid_replacement")
                    continue
                if target.kind is MemoryKind.PREFERENCE:
                    try:
                        outcome = FactService(self.store).apply_candidate(
                            f"[{speaker_id}] {fact}", operation="update",
                            target_key=target.key, space_id=space_id,
                            subject_id=speaker_id,
                            source="timlmembench_session", evidence_id=turn_id,
                            related_keys=tuple(old.key for old in targets[1:]),
                            support_evidence_ids=tuple(support_ids))
                    except KeyError:
                        self._reject(event, "target_not_active")
                        continue
                    if outcome.action != "update":
                        self._reject(event, "no_change")
                        continue
                    new_key = outcome.record.key
                else:
                    new_key = supersede(
                        self.store, target.key, f"[{speaker_id}] {fact}",
                        source="timlmembench_session", evidence_id=turn_id)
                    for old in targets[1:]:
                        forget_gone_everywhere(self.store, old.key)
            self.operations_applied[op] += 1
            self.operation_trace.append({
                "op": op, "source_turn_id": turn_id,
                "target_key": target.key, "new_key": new_key,
                "target_evidence_ids": list(target.content.get("evidence_ids")
                                            or [target.content.get("evidence_id")])[:8],
                "support_turn_ids": support_ids,
                "space_id": space_id,
                "affected_count": str(len(targets)),
            })

    def _source_context(self, query: str, allowed_spaces: list[str],
                        case_type: str) -> str:
        """Build source anchors from relevant current and historical versions."""
        visible = set(allowed_spaces)
        provenance_records = []
        if case_type == "provenance_recoverability":
            query_terms = {term for term in _terms(query) if len(term) > 1}
            records = [
                record for kind in (MemoryKind.PREFERENCE, MemoryKind.EPISODE)
                for record in self.store.list_by_kind(kind, space_ids=tuple(visible))
                if "curated" in record.tags
                and record.content.get("status", "active") != "pending"
            ]
            ranked = sorted(
                records,
                key=lambda record: len(query_terms & _terms(
                    str(record.content.get("summary") or ""))),
                reverse=True)
            seeds = [record for record in ranked[:8]
                     if query_terms & _terms(str(record.content.get("summary") or ""))]
            families = {record.content.get("family_id") for record in seeds
                        if record.content.get("family_id")}
            keys = {record.key for record in seeds}
            provenance_records = [record for record in records
                                  if record.key in keys
                                  or record.content.get("family_id") in families][:24]
            provenance_records.sort(key=lambda record: record.created_at)
        session_match = (re.search(r"Session\s*(\d+)", query, re.I)
                         if case_type == "write_op_accuracy" else None)
        focus = f"__s{int(session_match.group(1)):03d}_" if session_match else ""
        lines: list[str] = []
        for record in provenance_records:
            evidence_id = str(record.content.get("evidence_id") or "")
            if evidence_id:
                status = str(record.content.get("status") or (
                    "superseded" if "superseded" in record.tags else "active"))
                line = f"事实版本 {status}; 来源 {_source_anchor(evidence_id)}"
                summary = str(record.content.get("summary") or "")
                if status == "active" and not contains_sensitive(summary):
                    line += f"; 内容：{summary[:220]}"
                source = self.source_turns.get(evidence_id)
                if source and source[0] in visible:
                    line += f"; 发言者 {source[2]}"
                    if status == "active" and not contains_sensitive(source[1]):
                        line += f"; 原话：{source[1][:280]}"
                extra_evidence = [str(item) for item in
                                  (record.content.get("evidence_ids") or ())
                                  if item and str(item) != evidence_id]
                if extra_evidence:
                    line += "; 追加证据 " + "、".join(
                        _source_anchor(item) for item in extra_evidence[:8])
                lines.append(line)
        selected_keys = {record.key for record in provenance_records}
        selected_evidence = {str(item) for record in provenance_records
                             for item in (record.content.get("evidence_ids")
                                          or [record.content.get("evidence_id")]) if item}
        relevant_operations = set()
        if case_type == "provenance_recoverability":
            query_terms = {term for term in _terms(query) if len(term) > 1}
            scored = []
            for index, event in enumerate(self.operation_trace):
                if event.get("space_id") not in visible:
                    continue
                source_ids = [event.get("source_turn_id"),
                              *(event.get("target_evidence_ids") or ()),
                              *(event.get("support_turn_ids") or ())]
                source_text = " ".join(
                    self.source_turns[item][1] for item in source_ids
                    if item in self.source_turns
                    and self.source_turns[item][0] in visible)
                overlap = len(query_terms & _terms(source_text))
                if overlap:
                    scored.append((overlap, index))
            relevant_operations = {
                index for _, index in sorted(scored, reverse=True)[:4]}
        events = (self.operation_trace if case_type == "provenance_recoverability"
                  else self.operation_trace[-24:])
        for index, event in enumerate(events):
            if event.get("space_id") not in visible:
                continue
            if (case_type == "provenance_recoverability"
                    and event.get("target_key") not in selected_keys
                    and event.get("new_key") not in selected_keys
                    and event.get("source_turn_id") not in selected_evidence
                    and not selected_evidence.intersection(
                        event.get("target_evidence_ids") or ())
                    and index not in relevant_operations):
                continue
            if focus and focus not in event.get("source_turn_id", ""):
                continue
            # Source questions may ask why an old rule stopped applying, even
            # when the revoked fact has already left retrieval. Keep operation
            # anchors in the authorized space, without restoring old text.
            line = f"已执行 {event['op']}: {_source_anchor(event['source_turn_id'])}"
            anchors = [str(item) for item in event.get("target_evidence_ids", ()) if item]
            if anchors:
                line += "; 目标旧版本来源 " + "、".join(
                    _source_anchor(item) +
                    (f"（发言者 {self.source_turns[item][2]}）"
                     if item in self.source_turns else "")
                    for item in anchors)
            source = self.source_turns.get(event["source_turn_id"])
            if source and source[0] in visible:
                line += f"; 发言者 {source[2]}"
                if (case_type == "write_op_accuracy"
                        or not contains_sensitive(source[1])):
                    line += f"; 更正发言：{source[1][:280]}"
            lines.append(line)
            if case_type in {"provenance_recoverability", "write_op_accuracy"}:
                for support_id in event.get("support_turn_ids") or ():
                    support = self.source_turns.get(support_id)
                    if not support or support[0] not in visible:
                        continue
                    detail = (f"同一变更的补充证据 {_source_anchor(support_id)}; "
                              f"发言者 {support[2]}")
                    if not contains_sensitive(support[1]):
                        detail += f"; 原话：{support[1][:280]}"
                    lines.append(detail)
        if case_type == "write_op_accuracy":
            written_turns = set()
            source_ids = [evidence_id for evidence_id, source in self.source_turns.items()
                          if source[0] in visible
                          and (not focus or focus in evidence_id)]
            for item in FactService(self.store).decisions_for_evidence(
                    source_ids, allowed_spaces=visible):
                turn_id = item["evidence_id"]
                durable = item["action"] in ("add", "preserve", "update")
                if durable:
                    written_turns.add(turn_id)
                line = (f"{'事实写入' if durable else '候选判定'} {item['action']}: "
                        f"{_source_anchor(turn_id)}")
                source = self.source_turns.get(turn_id)
                if source and source[0] == item["space_id"]:
                    line += f"; 发言者 {source[2]}"
                    if not contains_sensitive(source[1]):
                        line += f"; 原话：{source[1][:280]}"
                lines.append(line)
            for item in self.decision_trace:
                if item["space_id"] not in visible:
                    continue
                if focus and focus not in item["source_turn_id"]:
                    continue
                line = (f"未写入判定候选 {item['action']}: "
                        f"{_source_anchor(item['source_turn_id'])}")
                if item["quote"]:
                    line += f"; 原话：{item['quote']}"
                lines.append(line)
            # Ordinary top-5 recall can omit a newly written rule. Keep the
            # operation-turn current facts visible without reviving old targets.
            operation_turns = {event["source_turn_id"]
                               for event in self.operation_trace[-24:]
                               if event.get("space_id") in visible
                               and (not focus or focus in event["source_turn_id"])}
            operation_turns.update(written_turns)
            records = [
                rec for kind in (MemoryKind.EPISODE, MemoryKind.PREFERENCE)
                for rec in self.store.list_by_kind(kind, space_ids=tuple(visible))
                if ("curated" in rec.tags and "superseded" not in rec.tags
                    and rec.content.get("status", "active") == "active"
                    and (str(rec.content.get("evidence_id") or "") in operation_turns
                         or (focus and focus in str(rec.content.get("evidence_id") or ""))))
            ]
            for rec in records[:40]:
                evidence_id = str(rec.content.get("evidence_id") or "")
                lines.append(f"操作后仍有效：{rec.content.get('summary')}; "
                             f"来源 {_source_anchor(evidence_id)}")
        return "\n".join(lines)

    def query(self, request: dict[str, Any]) -> dict[str, Any]:
        # Official raw-native queries carry no audience/ACL. See module note.
        scoped = dict(request)
        requested = scoped.get("allowed_spaces")
        if requested is None:
            scoped["allowed_spaces"] = sorted(self.space_ids)
        else:
            if not isinstance(requested, (list, tuple)):
                raise ValueError("allowed_spaces must be a list")
            scoped["allowed_spaces"] = [s for s in requested if s in self.space_ids]
        # Case type is official request metadata, not a gold answer. Only the
        # explanation/operation tasks need extra provenance in the model prompt.
        if (self.source_context_enabled and scoped.get("case_type") in
                {"write_op_accuracy", "provenance_recoverability"}):
            self.model.source_context = self._source_context(
                str(scoped.get("query") or ""), scoped["allowed_spaces"],
                str(scoped.get("case_type")))
        else:
            self.model.source_context = ""
        self.model.case_type = str(scoped.get("case_type") or "")
        try:
            result = super().query(scoped)
        finally:
            self.model.source_context = ""
            self.model.case_type = ""
        result["metadata"]["curated_count"] = sum(
            1 for kind in (MemoryKind.EPISODE, MemoryKind.PREFERENCE)
            for rec in self.store.list_by_kind(kind)
            if ("curated" in rec.tags and "superseded" not in rec.tags
                and rec.content.get("status", "active") == "active"))
        result["metadata"].update({
            "adapter": "whale_core_deepseek_governed_candidate_gate_v9",
            "scope_mode": ("explicit_query_acl" if requested is not None
                           else "all_surfaces_no_audience_acl"),
            "operations_applied": dict(self.operations_applied),
            "operations_rejected": self.operations_rejected,
            "operation_rejections": list(self.operation_rejections),
            "operation_trace": list(self.operation_trace),
            "write_trace": list(self.write_trace),
            "decision_trace": list(self.decision_trace),
            "governance_calls_cumulative": self.governance_calls,
            "governance_parse_failures_cumulative": self.governance_parse_failures,
            "governance_enabled": self.apply_governance,
            "source_context_enabled": self.source_context_enabled,
            "audience_acl_available": requested is not None,
        })
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-queries", type=int, required=True)
    parser.add_argument("--max-calls", type=int, required=True)
    parser.add_argument("--model", default="deepseek-v4.1-flash-1017")
    parser.add_argument("--max-facts-per-session", type=int, default=6)
    parser.add_argument("--no-memory", action="store_true")
    parser.add_argument("--disable-governance", action="store_true")
    parser.add_argument("--disable-source-context", action="store_true")
    parser.add_argument("--facts-in", type=Path,
                        help="Replay frozen extraction candidates from JSON")
    parser.add_argument("--facts-out", type=Path,
                        help="Write extracted candidates to JSON after this run")
    args = parser.parse_args()
    cassette = json.loads(args.facts_in.read_text(encoding="utf-8")) if args.facts_in else None
    if cassette is not None and not isinstance(cassette, dict):
        raise ValueError("frozen fact file must contain an object")
    adapter = GovernedDeepSeekMemoryAdapter(
        max_queries=args.max_queries, max_calls=args.max_calls,
        model=args.model, no_memory=args.no_memory,
        apply_governance=not args.disable_governance,
        fact_cassette=cassette,
        source_context_enabled=not args.disable_source_context,
        max_facts_per_session=args.max_facts_per_session)
    try:
        for line in sys.stdin:
            if not line.strip():
                continue
            request: dict[str, Any] = {}
            try:
                request = json.loads(line)
                response = adapter.handle(request)
            except Exception as exc:
                response = {"error": f"{type(exc).__name__}: {exc}"}
            print(json.dumps(response, ensure_ascii=False), flush=True)
            if request.get("request_type") == "close":
                break
    finally:
        if args.facts_out:
            args.facts_out.parent.mkdir(parents=True, exist_ok=True)
            temporary = args.facts_out.with_name(args.facts_out.name + ".tmp")
            temporary.write_text(json.dumps(adapter.recorded_facts, ensure_ascii=False),
                                 encoding="utf-8")
            temporary.replace(args.facts_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
