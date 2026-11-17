"""Offline TimlMemBench raw-session adapter for whale-core's existing memory path.

This is a structural baseline, not a paper-comparable LLM answer run. The
benchmark's human-human sessions cannot be replayed through chat() without
inventing model replies, so each observed turn becomes an ordinary, non-curated
chat episode. Queries use WhaleCompanion's normal recall/context assembly and
an abstaining probe model. No gold labels are read by this adapter.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from whale_core.core.companion import WhaleCompanion
from whale_core.memory.continuity import remember_episode
from whale_core.memory.curation import MemoryCurator
from whale_core.memory.history import append_user_message
from whale_core.memory.store import InMemoryStore, MemoryKind
from whale_core.core.prompt_composer import compose_companion_system


class ProbeModel:
    """Capture the exact evidence supplied to the answer stage; use no API."""

    def __init__(self) -> None:
        self.memories: tuple[str, ...] = ()
        self.history: tuple[Any, ...] = ()

    def reply(self, text: str, *, persona: str = "", memories=(), history=()) -> str:
        self.memories = tuple(str(item) for item in memories)
        self.history = tuple(history)
        return "<ABSTAIN>"


class GPT4oModel(ProbeModel):
    """Use the product's system-prompt composer and record API usage."""

    def __init__(self, *, max_output_tokens: int = 256) -> None:
        super().__init__()
        self.api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY is required for --answer-mode gpt4o")
        if max_output_tokens < 1 or max_output_tokens > 1024:
            raise ValueError("max_output_tokens must be between 1 and 1024")
        self.max_output_tokens = max_output_tokens
        self.last_usage: dict[str, Any] = {}

    def reply(self, text: str, *, persona: str = "", memories=(), history=()) -> str:
        self.memories = tuple(str(item) for item in memories)
        self.history = tuple(history)
        system = compose_companion_system(persona=persona, memories=self.memories)
        messages = [{"role": "system", "content": system}]
        messages.extend(
            {"role": item["role"], "content": item["content"][:2000]}
            for item in self.history[-12:]
            if isinstance(item, dict) and item.get("role") in {"user", "assistant"}
            and isinstance(item.get("content"), str)
        )
        messages.append({"role": "user", "content": text[:4000]})
        body = json.dumps({
            "model": "gpt-4o", "messages": messages,
            "temperature": 0, "max_tokens": self.max_output_tokens,
        }, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=body,
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as exc:
            # Never include response bodies or authorization headers in logs.
            raise RuntimeError(f"GPT-4o HTTP {exc.code}") from exc
        self.last_usage = dict(payload.get("usage") or {})
        return str(payload["choices"][0]["message"]["content"] or "<ABSTAIN>")


class NativeMemoryAdapter:
    def __init__(self, *, answer_mode: str = "probe", max_queries: int = 0,
                 max_output_tokens: int = 256) -> None:
        if answer_mode not in {"probe", "gpt4o"}:
            raise ValueError("unsupported answer_mode")
        if answer_mode == "gpt4o" and max_queries < 1:
            raise ValueError("--max-queries is required for GPT-4o runs")
        self.answer_mode = answer_mode
        self.max_queries = max_queries
        self.max_output_tokens = max_output_tokens
        self.query_count = 0
        self.reset("")

    def reset(self, sample_id: str) -> None:
        self.sample_id = sample_id
        self.store = InMemoryStore()
        self.curator = MemoryCurator(self.store)
        self.model = (GPT4oModel(max_output_tokens=self.max_output_tokens)
                      if self.answer_mode == "gpt4o" else ProbeModel())
        self.companion = WhaleCompanion(self.store, self.model, curator=self.curator)
        self.sessions = 0
        self.turns = 0

    def ingest_session(self, session: dict[str, Any]) -> None:
        session_id = str(session.get("session_id") or "")
        if not session_id:
            raise ValueError("session_id is required")
        for index, turn in enumerate(session.get("turns") or (), start=1):
            if not isinstance(turn, dict):
                continue
            body = str(turn.get("text") or "").strip()
            if not body:
                continue
            speaker = str(turn.get("speaker_id") or turn.get("speaker_role") or "unknown")
            turn_id = str(turn.get("turn_id") or f"{session_id}_t{index:03d}")
            observed = f"[{speaker}] {body}"
            # Historical messages are observed, not generated by this agent.
            # Keep their role in text; history itself supports only user/assistant.
            append_user_message(self.store, session_id, observed)
            remember_episode(
                self.store,
                summary=observed,
                key=f"timlmembench:chat:{turn_id}",
                tags=("chat",),
                extra={"conversation_id": session_id, "source_turn_id": turn_id},
            )
            self.turns += 1
        self.sessions += 1

    def query(self, request: dict[str, Any]) -> dict[str, Any]:
        if request.get("mode") != "raw_native_cases":
            raise ValueError("this adapter supports raw_native_cases only")
        if self.answer_mode == "gpt4o" and self.query_count >= self.max_queries:
            raise ValueError("GPT-4o query cap reached")
        self.query_count += 1
        query = str(request.get("query") or "")
        # A fresh query conversation gives no privileged access to source-session
        # history. This matches the cross-session memory question.
        # An evaluation harness may supply the authorized read scope. Official
        # raw-native queries omit it, retaining the legacy unpartitioned path.
        allowed_spaces = request.get("allowed_spaces")
        query_now = getattr(self, "memory_now", None)
        answer = self.companion.chat(
            query, episodic=False, allowed_spaces=allowed_spaces,
            memory_now=query_now)
        recall_options = {"allowed_spaces": allowed_spaces}
        if query_now is not None:
            recall_options["now"] = query_now
        hits = self.curator.recall(query, **recall_options)
        episodes = self.store.list_by_kind(MemoryKind.EPISODE)
        curated = [record for record in
                   (*episodes, *self.store.list_by_kind(MemoryKind.PREFERENCE))
                   if "curated" in record.tags and "superseded" not in record.tags]
        return {
            "answer": answer,
            "retrieved_memory_ids": [hit.record.key for hit in hits],
            "retrieved_texts": list(self.model.memories),
            "metadata": {
                "adapter": "whale_core_structural_native_v1",
                "answer_mode": ("gpt-4o" if self.answer_mode == "gpt4o"
                                else "offline_abstain_probe"),
                "ingested_sessions": self.sessions,
                "ingested_turns": self.turns,
                "episode_count": len(episodes),
                "curated_count": len(curated),
                "recall_hit_count": len(hits),
                "history_messages_visible": len(self.model.history),
                "scope_mode": "unpartitioned_legacy",
            },
            "usage": (self.model.last_usage if self.answer_mode == "gpt4o"
                      else {"model_calls": 0, "input_tokens": 0, "output_tokens": 0}),
        }

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        kind = request.get("request_type")
        if kind == "reset":
            self.reset(str(request.get("sample_id") or ""))
            return {"ok": True}
        if kind == "ingest_session":
            if str(request.get("sample_id") or "") != self.sample_id:
                raise ValueError("sample_id changed without reset")
            self.ingest_session(request.get("session") or {})
            return {"ok": True}
        if kind == "query":
            if str(request.get("sample_id") or "") != self.sample_id:
                raise ValueError("sample_id changed without reset")
            return self.query(request)
        if kind == "close":
            return {"ok": True}
        raise ValueError(f"unsupported request_type: {kind}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, help="Optional JSONL diagnostics path")
    parser.add_argument("--answer-mode", choices=("probe", "gpt4o"), default="probe")
    parser.add_argument("--max-queries", type=int, default=0,
                        help="Required spending cap for GPT-4o runs; match runner --limit")
    parser.add_argument("--max-output-tokens", type=int, default=256)
    args = parser.parse_args()
    adapter = NativeMemoryAdapter(answer_mode=args.answer_mode,
                                  max_queries=args.max_queries,
                                  max_output_tokens=args.max_output_tokens)
    log = args.log.open("w", encoding="utf-8") if args.log else None
    try:
        for line in sys.stdin:
            if not line.strip():
                continue
            request: dict[str, Any] = {}
            try:
                request = json.loads(line)
                response = adapter.handle(request)
                if log is not None:
                    log.write(json.dumps({
                        "request_type": request.get("request_type"),
                        "sample_id": request.get("sample_id"),
                        "task_id": request.get("task_id"),
                        "metadata": response.get("metadata", {}),
                    }, ensure_ascii=False) + "\n")
                    log.flush()
            except Exception as exc:
                response = {"error": f"{type(exc).__name__}: {exc}"}
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
            if request.get("request_type") == "close":
                break
    finally:
        if log is not None:
            log.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
