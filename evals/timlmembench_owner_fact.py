"""TimlMemBench JSONL adapter using whale-core's actual owner_fact_v1 pipeline.

The benchmark's ``user`` speaker is treated as the owner of one private QQ
space. Contact turns and non-private sessions are outside owner_fact_v1's
automatic collection scope. Gold labels are never used during ingestion or
answering. This adapter does not change product extraction or governance.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any

import requests

from whale_core.core.prompt_composer import compose_companion_system
from whale_core.life.bounded_call import BoundedCall
from whale_core.memory.owner_fact_extraction import OwnerFactExtractor
from whale_core.memory.owner_fact_worker import OwnerFactWorker
from whale_core.memory.owner_facts import (
    MemoryAuthorization, OwnerFactService, UserEvidence,
)
from whale_core.memory.store import InMemoryStore


MODEL = "deepseek-v4.1-flash-1017"
SPACE = "owner-working"
PRINCIPAL = "benchmark:qq:owner"


class ChatClient:
    def __init__(self, *, max_calls: int):
        self.key = os.environ.get("AGENTSCOPE_API_KEY", "").strip()
        if not self.key:
            raise ValueError("AGENTSCOPE_API_KEY is required")
        self.max_calls = max_calls
        self.calls = 0
        self.usage: dict[str, int] = {}
        self.http = requests.Session()

    def complete(self, messages: list[dict[str, str]], *, max_tokens: int) -> str:
        if self.calls >= self.max_calls:
            raise RuntimeError("model call limit reached")
        self.calls += 1
        with self.http.post(
            "https://platform.agentscope.io/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.key}",
                     "Accept": "text/event-stream"},
            json={"model": MODEL, "stream": True,
                  "stream_options": {"include_usage": True},
                  "thinking": {"type": "disabled"}, "temperature": 0,
                  "max_tokens": max_tokens, "messages": messages},
            stream=True, timeout=(15, 90),
        ) as response:
            if response.status_code != 200:
                raise RuntimeError(f"model HTTP {response.status_code}")
            chunks: list[str] = []
            done = False
            for line in response.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    done = True
                    break
                event = json.loads(payload)
                for key, value in (event.get("usage") or {}).items():
                    if key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                        self.usage[key] = self.usage.get(key, 0) + int(value or 0)
                for choice in event.get("choices") or ():
                    text = (choice.get("delta") or {}).get("content")
                    if text:
                        chunks.append(str(text))
            if not done:
                raise RuntimeError("model stream ended before DONE")
            return "".join(chunks)


class ExtractionModel:
    def __init__(self, client: ChatClient):
        self.client = client

    def complete(self, prompt: str, *, timeout: float) -> str:
        return self.client.complete([{"role": "user", "content": prompt}],
                                    max_tokens=512)


class BenchmarkAuthority:
    def __init__(self, owner_id: str):
        self.owner_id = owner_id

    def authorize_now(self, owner_id: str, space_id: str):
        if owner_id != self.owner_id or space_id != SPACE:
            return None
        return MemoryAuthorization(owner_id, SPACE, PRINCIPAL, "benchmark-v1",
                                   "benchmark-v1", (SPACE,))

    def authorize_principal_now(self, owner_id: str, space_id: str,
                                principal_id: str):
        return (self.authorize_now(owner_id, space_id)
                if principal_id == PRINCIPAL else None)

    def authority_version(self, owner_id: str, space_id: str) -> str:
        return "benchmark-v1"


def _timestamp(value: Any) -> datetime:
    try:
        stamp = datetime.fromisoformat(str(value))
    except ValueError:
        stamp = datetime.now(timezone.utc)
    return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp


class OwnerFactAdapter:
    def __init__(self, client: ChatClient, *, max_queries: int):
        self.client = client
        self.max_queries = max_queries
        self.worker: OwnerFactWorker | None = None
        self.reset("")

    def reset(self, sample_id: str) -> None:
        if self.worker is not None:
            self.worker.stop(timeout=5)
        self.sample_id = sample_id
        self.store = InMemoryStore()
        self.authority = BenchmarkAuthority(sample_id)
        self.service = OwnerFactService(self.store, self.authority)
        self.auth = self.authority.authorize_now(sample_id, SPACE)
        extractor = OwnerFactExtractor(ExtractionModel(self.client), timeout_s=25)
        self.worker = OwnerFactWorker.create(
            self.store, self.service, extractor,
            call_gate=BoundedCall(extractor.extract, default_timeout_s=30),
            lease_seconds=90, model_timeout=40, poll_interval=0.01,
        )
        self.worker.start()
        self.queries = 0
        self.sessions = 0
        self.owner_turns = 0
        self.skipped_turns = 0

    def ingest_session(self, session: dict[str, Any]) -> None:
        if session.get("surface") != "private_chat":
            self.skipped_turns += len(session.get("turns") or ())
            return
        for turn in session.get("turns") or ():
            if turn.get("speaker_role") != "user":
                self.skipped_turns += 1
                continue
            evidence = UserEvidence(
                evidence_id=str(turn.get("turn_id") or ""),
                owner_id=self.sample_id, principal_id=PRINCIPAL,
                conversation_id=str(session.get("session_id") or ""),
                channel="qq", space_id=SPACE,
                text=str(turn.get("text") or ""),
                received_at=_timestamp(turn.get("timestamp")),
                timezone="UTC",
                source_message_id=str(turn.get("turn_id") or ""),
            )
            self.service.observe(evidence, self.auth)
            self.owner_turns += 1
        if not self.worker.drain(timeout=600):
            raise RuntimeError("owner fact extraction did not drain")
        self.sessions += 1

    def query(self, request: dict[str, Any]) -> dict[str, Any]:
        if request.get("mode") != "raw_native_cases":
            raise ValueError("raw_native_cases only")
        if self.queries >= self.max_queries:
            raise RuntimeError("query limit reached")
        self.queries += 1
        query = str(request.get("query") or "")
        facts = self.service.retrieve(self.auth, query, limit=8)
        accepted_total = len(self.service.retrieve(self.auth, "", limit=1000))
        memories = [f"长期记忆（用户自述）：{fact.summary}" for fact in facts]
        prompt = compose_companion_system(persona="", memories=memories)
        prompt += ("\n\n仅根据可见记忆回答问题。证据不足时回答‘证据不足’，"
                   "不要编造，也不要把联系人发言当作 owner 自述。")
        before = dict(self.client.usage)
        answer = self.client.complete(
            [{"role": "system", "content": prompt},
             {"role": "user", "content": query}], max_tokens=384)
        return {
            "answer": answer, "retrieved_memory_ids": [f.fact_id for f in facts],
            "retrieved_texts": memories,
            "metadata": {
                "adapter": "owner_fact_v1_product_pilot",
                "owner_mapping": "benchmark user -> owner; private_chat only",
                "ingested_sessions": self.sessions,
                "owner_turns": self.owner_turns,
                "skipped_turns": self.skipped_turns,
                "accepted_recall_count": len(facts),
                "accepted_total": accepted_total,
                "model_calls_cumulative": self.client.calls,
                "model_usage_cumulative": dict(self.client.usage),
            },
            "usage": {key: value - before.get(key, 0)
                      for key, value in self.client.usage.items()},
        }

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        kind = request.get("request_type")
        if kind == "reset":
            self.reset(str(request.get("sample_id") or ""))
            return {"ok": True}
        if kind == "ingest_session":
            if request.get("sample_id") != self.sample_id:
                raise ValueError("sample changed without reset")
            self.ingest_session(request.get("session") or {})
            return {"ok": True}
        if kind == "query":
            if request.get("sample_id") != self.sample_id:
                raise ValueError("sample changed without reset")
            return self.query(request)
        if kind == "close":
            return {"ok": True}
        raise ValueError("unknown request type")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-queries", type=int, required=True)
    parser.add_argument("--max-calls", type=int, required=True)
    args = parser.parse_args()
    adapter = OwnerFactAdapter(ChatClient(max_calls=args.max_calls),
                               max_queries=args.max_queries)
    try:
        for line in sys.stdin:
            if not line.strip():
                continue
            request = json.loads(line)
            try:
                response = adapter.handle(request)
            except Exception as exc:
                response = {"error": f"{type(exc).__name__}: {exc}"}
            print(json.dumps(response, ensure_ascii=False), flush=True)
            if request.get("request_type") == "close":
                break
    finally:
        if adapter.worker is not None:
            adapter.worker.stop(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
