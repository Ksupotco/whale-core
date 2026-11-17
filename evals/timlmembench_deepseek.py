"""TimlMemBench raw-session evaluation with AgentScope DeepSeek extraction/answers.

The benchmark supplies observed human conversations, not agent chat turns. An
experimental extraction step writes evidence-linked facts through FactService.
This is an evaluation protocol, not whale-core's production
consent or member-extraction workflow.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Mapping

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from evals.timlmembench_native import NativeMemoryAdapter, ProbeModel
from whale_core.core.prompt_composer import compose_companion_system
from whale_core.memory.context import _scenario_time
from whale_core.memory.facts import FactService
from whale_core.memory.hybrid import HybridCurator
from whale_core.plugins.embedding import OpenAICompatibleEmbedding
from datetime import datetime, timezone


def _benchmark_time(value: str) -> datetime:
    """Treat zone-free benchmark timestamps as UTC for consistent comparisons."""
    stamp = datetime.fromisoformat(value)
    return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp


def parse_object(text: str) -> dict[str, Any]:
    raw = text.strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match is None:
            raise ValueError("model returned no JSON object") from None
        value = json.loads(match.group())
    if not isinstance(value, dict):
        raise ValueError("model JSON must be an object")
    return value


class AgentScopeClient:
    def __init__(self, *, model: str, max_calls: int,
                 api_key_env: str = "AGENTSCOPE_API_KEY"):
        self.key = os.environ.get(api_key_env, "").strip()
        if not self.key:
            raise ValueError(f"{api_key_env} is required")
        if max_calls < 1:
            raise ValueError("max_calls must be positive")
        self.model = model
        self.max_calls = max_calls
        self.calls = 0
        self.usage: dict[str, int] = {}
        self.http = requests.Session()
        retry = Retry(total=2, connect=2, read=0, status=2, backoff_factor=1,
                      status_forcelist=(429, 500, 502, 503, 504),
                      allowed_methods={"POST"})
        self.http.mount("https://", HTTPAdapter(max_retries=retry))

    def complete(self, messages: list[dict[str, str]], *, max_tokens: int) -> tuple[str, dict[str, Any]]:
        started = time.perf_counter()
        for attempt in range(3):
            if self.calls >= self.max_calls:
                raise ValueError("AgentScope call cap reached")
            self.calls += 1
            try:
                with self.http.post(
                    "https://platform.agentscope.io/v1/chat/completions",
                    headers={"Authorization": f"Bearer {self.key}", "Accept": "text/event-stream"},
                    json={"model": self.model, "stream": True,
                          "stream_options": {"include_usage": True},
                          "thinking": {"type": "disabled"},
                          "temperature": 0, "max_tokens": max_tokens,
                          "messages": messages},
                    stream=True, timeout=(15, 120),
                ) as response:
                    if response.status_code != 200:
                        raise RuntimeError(f"AgentScope HTTP {response.status_code}")
                    chunks: list[str] = []
                    usage: dict[str, Any] = {}
                    finished = False
                    for line in response.iter_lines(decode_unicode=True):
                        if not line or not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            finished = True
                            break
                        event = json.loads(data)
                        if isinstance(event.get("usage"), dict):
                            usage = event["usage"]
                        for choice in event.get("choices") or []:
                            delta = choice.get("delta") or {}
                            if delta.get("content"):
                                chunks.append(str(delta["content"]))
                    if not finished:
                        raise requests.ConnectionError("stream ended before DONE")
            except requests.RequestException as exc:
                if attempt == 2:
                    raise RuntimeError(
                        f"AgentScope transport failure: {type(exc).__name__}") from exc
                time.sleep(attempt + 1)
                continue
            for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
                self.usage[field] = self.usage.get(field, 0) + int(usage.get(field) or 0)
            return "".join(chunks), {"model": self.model, "calls": attempt + 1,
                                     "usage": usage,
                                     "latency_ms": round((time.perf_counter() - started) * 1000, 1)}
        raise AssertionError("unreachable")


class DeepSeekAnswerModel(ProbeModel):
    def __init__(self, client: AgentScopeClient):
        super().__init__()
        self.client = client
        self.last_usage: dict[str, Any] = {}

    def task_instruction(self) -> str:
        return ("\n\n评测任务：只根据上面可见的记忆回答问题。"
                "若没有足够证据，请只回答‘证据不足’。不要编造信息。")

    def output_budget(self) -> int:
        return 384

    def reply(self, text: str, *, persona: str = "", memories=(), history=()) -> str:
        self.memories = tuple(str(item) for item in memories)
        self.history = tuple(history)
        system = compose_companion_system(
            persona=persona, memories=self.memories,
            scenario_time=_scenario_time(text))
        system += self.task_instruction()
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": text[:4000]}]
        answer, self.last_usage = self.client.complete(
            messages, max_tokens=self.output_budget())
        return answer.strip() or "证据不足"


class DeepSeekMemoryAdapter(NativeMemoryAdapter):
    def memory_space_for_session(self, session: dict[str, Any]) -> str:
        """Legacy run has one unpartitioned space; variants may override."""
        return ""

    def __init__(self, *, max_queries: int, max_calls: int,
                 model: str = "deepseek-v4.1-flash-1017",
                 no_memory: bool = False,
                 fact_cassette: Mapping[str, list[dict[str, Any]]] | None = None,
                 max_facts_per_session: int = 6) -> None:
        if max_queries < 1:
            raise ValueError("max_queries must be positive")
        if not 1 <= max_facts_per_session <= 12:
            raise ValueError("max_facts_per_session must be between 1 and 12")
        self.client = AgentScopeClient(model=model, max_calls=max_calls)
        self.max_facts_per_session = max_facts_per_session
        self.no_memory = no_memory
        self.fact_cassette = fact_cassette
        self.recorded_facts: dict[str, list[dict[str, Any]]] = {}
        self.fact_replay_hits = 0
        self.extract_calls = 0
        self.extracted_facts = 0
        self.extraction_parse_failures = 0
        super().__init__(answer_mode="probe", max_queries=max_queries)

    def reset(self, sample_id: str) -> None:
        super().reset(sample_id)
        self.turn_timestamps: dict[str, datetime] = {}
        self.memory_now: datetime | None = None
        embedding_base = os.environ.get("WHALE_EMBEDDING_BASE_URL", "").strip()
        embedding_key = os.environ.get("WHALE_EMBEDDING_API_KEY", "").strip()
        embedding_model = os.environ.get("WHALE_EMBEDDING_MODEL", "").strip()
        self.embedding_mode = "bm25"
        if embedding_base and embedding_key and embedding_model:
            provider = OpenAICompatibleEmbedding(
                base_url=embedding_base, api_key=embedding_key,
                model=embedding_model, timeout=30)
            self.curator = HybridCurator(self.store, embedder=provider)
            self.companion.curator = self.curator
            self.embedding_mode = embedding_model
        self.model = DeepSeekAnswerModel(self.client)
        self.companion.model = self.model
        self.write_trace: list[dict[str, str]] = []

    def _write_candidate(self, *, fact: str, turn_id: str,
                         speaker_id: str, space_id: str,
                         operation: str = "add", slot_key: str = "") -> None:
        result = FactService(self.store).apply_candidate(
            f"[{speaker_id}] {fact}", source="timlmembench_session",
            evidence_id=turn_id, subject_id=speaker_id,
            space_id=space_id, operation=operation, slot_key=slot_key,
            observed_at=self.turn_timestamps.get(turn_id))
        self.write_trace.append({
            "action": result.action, "source_turn_id": turn_id,
            "space_id": space_id,
            "fact_key": result.record.key if result.record else "",
        })

    def ingest_session(self, session: dict[str, Any]) -> None:
        super().ingest_session(session)
        for turn in session.get("turns") or ():
            if not isinstance(turn, dict) or not turn.get("timestamp"):
                continue
            try:
                self.turn_timestamps[str(turn.get("turn_id") or "")] = _benchmark_time(
                    str(turn["timestamp"]))
            except ValueError:
                continue
        ended = session.get("ended_at")
        if ended:
            try:
                stamp = _benchmark_time(str(ended))
                if self.memory_now is None or stamp > self.memory_now:
                    self.memory_now = stamp
            except ValueError:
                pass
        if self.no_memory:
            return
        turns = [t for t in session.get("turns") or ()
                 if isinstance(t, dict) and str(t.get("text") or "").strip()]
        if not turns:
            return
        source = [{"turn_id": str(t.get("turn_id") or ""),
                   "speaker_id": str(t.get("speaker_id") or t.get("speaker_role") or "unknown"),
                   "text": str(t["text"])} for t in turns]
        prompt = ("从以下既有对话提取以后回答问题可能需要的明确事实。只输出 JSON 对象，"
                  "格式为 {\"facts\":[{\"turn_id\":字符串,\"speaker_id\":字符串,"
                  "\"fact\":字符串}]}。每条事实必须由对应发言直接支持，并写明主体；"
                  "不要推测，不提取寒暄、无长期价值的临时对话、密码、联系方式、住址等敏感信息，"
                  "也不要提取明确要求忘记的内容。合并重复表述。"
                  f"若没有合适事实，返回空数组；最多 {self.max_facts_per_session} 条。\n"
                  + json.dumps(source, ensure_ascii=False))
        cassette_key = f"{self.sample_id}/{session['session_id']}"
        if self.fact_cassette is None:
            raw, _ = self.client.complete(
                [{"role": "user", "content": prompt}],
                max_tokens=512 if self.max_facts_per_session <= 6 else 1024)
            self.extract_calls += 1
            try:
                facts = parse_object(raw).get("facts")
            except (ValueError, json.JSONDecodeError):
                facts = None
        else:
            if cassette_key not in self.fact_cassette:
                raise ValueError(f"missing frozen facts for {cassette_key}")
            facts = self.fact_cassette[cassette_key]
            self.fact_replay_hits += 1
        if not isinstance(facts, list):
            self.extraction_parse_failures += 1
            self.recorded_facts[cassette_key] = []
            return
        # Persist only extracted candidates, not the raw conversation or gold.
        self.recorded_facts[cassette_key] = facts[:self.max_facts_per_session]
        by_turn = {t["turn_id"]: t for t in source}
        for item in facts[:self.max_facts_per_session]:
            if not isinstance(item, dict):
                continue
            turn_id = str(item.get("turn_id") or "")
            speaker_id = str(item.get("speaker_id") or "")
            fact = str(item.get("fact") or "").strip()
            evidence = by_turn.get(turn_id)
            if evidence is None or evidence["speaker_id"] != speaker_id or not fact:
                continue
            if len(fact) > 300:
                continue
            operation = str(item.get("operation") or "add")
            if operation not in ("add", "skip", "ephemeral"):
                operation = "add"
            self._write_candidate(
                fact=fact, turn_id=turn_id, speaker_id=speaker_id,
                space_id=self.memory_space_for_session(session),
                operation=operation,
                slot_key=str(item.get("slot_key") or "").strip()[:80])
            self.extracted_facts += 1

    def query(self, request: dict[str, Any]) -> dict[str, Any]:
        if self.query_count >= self.max_queries:
            raise ValueError("DeepSeek query cap reached")
        result = super().query(request)
        result["metadata"].update({
            "adapter": "whale_core_deepseek_factservice_v2",
            "answer_mode": self.client.model,
            "extraction_calls_cumulative": self.extract_calls,
            "extracted_facts_cumulative": self.extracted_facts,
            "extraction_parse_failures_cumulative": self.extraction_parse_failures,
            "fact_replay_hits_cumulative": self.fact_replay_hits,
            "model_calls_cumulative": self.client.calls,
            "model_usage_cumulative": dict(self.client.usage),
            "protocol_consent": "benchmark_only_not_production_consent",
            "no_memory_control": self.no_memory,
            "embedding_mode": self.embedding_mode,
            "embedding_vectors_cached": len(getattr(self.curator.embedder, "_cache", ()))
            if isinstance(self.curator, HybridCurator) and self.curator.embedder else 0,
        })
        result["usage"] = self.model.last_usage
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-queries", type=int, required=True)
    parser.add_argument("--max-calls", type=int, required=True)
    parser.add_argument("--model", default="deepseek-v4.1-flash-1017")
    parser.add_argument("--max-facts-per-session", type=int, default=6)
    parser.add_argument("--no-memory", action="store_true")
    parser.add_argument("--facts-in", type=Path,
                        help="Replay frozen extraction candidates from JSON")
    parser.add_argument("--facts-out", type=Path,
                        help="Write extracted candidates to JSON after this run")
    args = parser.parse_args()
    cassette = json.loads(args.facts_in.read_text(encoding="utf-8")) if args.facts_in else None
    if cassette is not None and not isinstance(cassette, dict):
        raise ValueError("frozen fact file must contain an object")
    adapter = DeepSeekMemoryAdapter(max_queries=args.max_queries,
                                    max_calls=args.max_calls, model=args.model,
                                    no_memory=args.no_memory, fact_cassette=cassette,
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
