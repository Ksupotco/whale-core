"""Connect a local OneBot 11 HTTP webhook to the existing Whale runtime.

Enable the OneBot 11 HTTP API and POST events to http://127.0.0.1:18798/onebot.
For OpenQQ, set both its access token and HTTP POST secret to the generated
onebot_token. Owner private messages are enabled; groups require --group-id.
"""
from __future__ import annotations

import argparse
from copy import copy
import os
import queue
import re
import secrets
import sqlite3
import threading
import time
from collections import deque
from collections.abc import Mapping
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from whale_core.configuration import read_config
from whale_core.life.channels import OwnerChannelRegistry
from whale_core.life.initiative import ProactiveDraft, ProactivePrefs
from whale_core.life.group_turn import GroupTurnPolicy
from whale_core.life.qq_onebot import (
    HttpOneBotTransport, OneBotHttpListener, OneBotInboundBridge, OneBotOutboundPort,
    parse_onebot_message,
)
from whale_core.life.qq_workflow import QqInboundWorkflow
from whale_core.life.trpg import TableReply, TableTools
from whale_core.memory import group_refresh
from whale_core.memory.consent import MemberConsentStore
from whale_core.memory.group_directory import load_directory
from whale_core.memory.pause import is_recording_paused
from whale_core.memory.timeline import GroupTimelineStore
from whale_core.profiles import assemble_runtime


class _QueuedBridge(OneBotInboundBridge):
    def __init__(self, registry: OwnerChannelRegistry, pending: queue.Queue,
                 bot_qq: str | None = None, group_ids: set[str] | None = None, archive_event=None):
        super().__init__(registry)
        self.archive_event = archive_event
        self.pending = pending
        self.bot_qq = bot_qq
        self.group_ids = frozenset(group_ids or ())

    def handle_event(self, event: object, *, authenticated: bool):
        if self.bot_qq is not None and (
            not isinstance(event, Mapping) or str(event.get("self_id")) != self.bot_qq
        ):
            return None
        if isinstance(event, Mapping) and event.get("message_type") == "group":
            if not authenticated or str(event.get("group_id")) not in self.group_ids:
                return None
            incoming = parse_onebot_message(event, authenticated=True,
                                            accept_group=True)
            if incoming is not None and incoming.text.strip():
                if self.archive_event:
                    self.archive_event(event, incoming)
                segments = event.get("message")
                mentioned = isinstance(segments, list) and any(
                    isinstance(part, Mapping) and part.get("type") == "at"
                    and isinstance(part.get("data"), Mapping)
                    and str(part["data"].get("qq")) == self.bot_qq
                    for part in segments)
                self.pending.put((incoming, mentioned))
            return None
        owner_event = super().handle_event(event, authenticated=authenticated)
        if owner_event is not None:
            self.pending.put(owner_event)
        return owner_event


def _one_line(text: str) -> str:
    return re.sub(r"\s*[\r\n\u2028\u2029]+\s*", " ", str(text or "")).strip()


class _OneLineOwnerPort(OneBotOutboundPort):
    table_reply: TableReply | None = None

    def for_table(self, reply: TableReply):
        port = copy(self)
        port.table_reply = reply
        return port

    def message_segments(self, text: str) -> list[dict]:
        if self.table_reply:
            cover = self.table_reply.cover if text == self.table_reply.text else ""
            return TableReply(text, cover).segments()
        return super().message_segments(text)

    def send(self, draft) -> bool:
        if not self.table_reply:
            draft = replace(draft, text=_one_line(draft.text))
        return super().send(draft)


def _token(config_dir: Path) -> str:
    path = config_dir / "onebot_token"
    if path.exists():
        token = path.read_text(encoding="utf-8").strip()
        if not token:
            raise ValueError(f"empty OneBot token file: {path}")
        return token
    token = secrets.token_urlsafe(32)
    path.write_text(token + "\n", encoding="utf-8")
    if os.name != "nt":
        os.chmod(path, 0o600)
    return token


def _process_owner(event, workflow: QqInboundWorkflow, port: OneBotOutboundPort,
                   prefs: ProactivePrefs, *, table=None, group="") -> str:
    tool_reply = table.command(event.text, group) if table else None
    if tool_reply:
        if isinstance(port, _OneLineOwnerPort):
            port = port.for_table(tool_reply)
        draft_id = f"qq-reply:{event.message_id}"
        workflow.outbox.propose(ProactiveDraft(
            draft_id, tool_reply.text, "主人私聊跑团指令",
            (f"qq:{event.conversation}:{event.message_id}",), channel="direct_message",
            target_conversation=event.conversation))
    else:
        result = workflow.handle_owner_event(event)
        draft_id = result.get("draft_id")
        if not draft_id:
            return str(result.get("skipped") or "no_draft")
    verdict = workflow.outbox.dispatch(
        draft_id, prefs, now=datetime.now(timezone.utc), port=port,
        passive_reply=True)
    return verdict.status


def _process_group(event, workflow: QqInboundWorkflow,
                   transport: HttpOneBotTransport) -> tuple[str, str]:
    result = workflow.handle_stranger_event(event)
    reply = _one_line(result.get("reply") or "")
    if not reply:
        return str(result.get("skipped") or "no_reply"), ""
    receipt = transport.call("send_group_msg", {
        "group_id": int(event.conversation.split(":", 1)[1]),
        "message": reply[:4000],
    })
    return ("sent", reply[:4000]) if receipt.get("retcode") == 0 else ("send_failed", "")


def _is_plus_one(text: str) -> bool:
    return str(text or "").strip() in ("+1", "＋1")


def _send_table(transport, group: str, reply: TableReply) -> bool:
    receipt = transport.call("send_group_msg", {"group_id": int(group), "message": reply.segments()})
    return receipt.get("retcode") == 0


def _plus_one_ready(timeline: deque) -> bool:
    if len(timeline) < 2:
        return False
    recent = list(timeline)[-2:]
    return (all(not item["is_self"] and _is_plus_one(item["text"])
                for item in recent)
            and recent[0]["sender_id"] != recent[1]["sender_id"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config-dir", required=True, type=Path)
    parser.add_argument("--owner-qq", required=True, help="QQ account that may chat with Whale")
    parser.add_argument("--bot-qq", required=True, help="QQ account that must be running OpenQQ")
    parser.add_argument("--group-id", action="append", default=[],
                        help="Group allowed to receive Jax replies; repeat for more groups")
    parser.add_argument("--onebot-url", default="http://127.0.0.1:3000")
    parser.add_argument("--port", type=int, default=18798)
    args = parser.parse_args(argv)
    if not args.owner_qq.isdigit():
        parser.error("--owner-qq must be numeric")
    if not args.bot_qq.isdigit() or args.bot_qq == args.owner_qq:
        parser.error("--bot-qq must be a different numeric QQ account")
    if any(not group_id.isdigit() for group_id in args.group_id):
        parser.error("--group-id must be numeric")
    if not args.onebot_url.startswith("http://127.0.0.1:"):
        parser.error("--onebot-url must be a local 127.0.0.1 endpoint")

    config_dir = args.config_dir.expanduser().resolve()
    config_dir.mkdir(parents=True, exist_ok=True)
    token = _token(config_dir)
    profile = str(read_config(config_dir).get("profile") or "local")
    bundle = assemble_runtime(
        profile, config_dir=str(config_dir),
        pairing_bindings={("qq", args.owner_qq): "owner"})
    prefs = ProactivePrefs(
        enabled=True, allowed_channels=frozenset({"direct_message"}),
        auto_send=True, passive_reply_in_quiet=True, passive_daily_limit=200)
    workflow = QqInboundWorkflow(
        bundle.store, bundle.companion.model, prefs=prefs,
        orchestrator=bundle.orchestrator, stranger_daily_limit=0)
    port = _OneLineOwnerPort(
        HttpOneBotTransport(args.onebot_url, token=token),
        user_id=args.owner_qq, enabled=True)
    group_transport = HttpOneBotTransport(args.onebot_url, token=token)
    allowed_groups = set(args.group_id)
    table = TableTools(config_dir)
    preview_group = next(iter(allowed_groups)) if len(allowed_groups) == 1 else ""
    group_policy = GroupTurnPolicy(bundle.store, bot_names=("Jax",),
                                   topic_join_prob=0.12)
    timeline_store = GroupTimelineStore(bundle.store)
    bundle.companion.timeline = timeline_store
    bundle.companion.self_uid = args.bot_qq
    group_timeline: dict[str, deque] = {}
    group_plus_one_followed: set[str] = set()
    pending: queue.Queue = queue.Queue()
    archive = config_dir / "group_archive.sqlite3"
    live_groups = {}
    if archive.is_file():
        with closing(sqlite3.connect(archive)) as db:
            for gid in allowed_groups:
                if group_refresh.status(db, gid) is not None:
                    roster = load_directory(archive, gid)
                    if roster:
                        live_groups[gid] = {a:m["accounts"] for m in roster["members"] for a in m["accounts"]}
    consents = MemberConsentStore(bundle.store)

    def archive_event(raw, incoming):
        gid = str(raw["group_id"])
        if gid not in live_groups or is_recording_paused(bundle.store, "group-" + gid):
            return
        accounts = live_groups[gid].get(incoming.account, [incoming.account])
        if not any(consents.is_opted_out("anon:qq:" + a) for a in accounts):
            group_refresh.capture(archive, gid, raw, incoming)

    bridge = _QueuedBridge(
        OwnerChannelRegistry(owner_id="owner", bindings={("qq", args.owner_qq): "owner"}),
        pending, bot_qq=args.bot_qq, group_ids=allowed_groups, archive_event=archive_event)
    listener = OneBotHttpListener(bridge, expected_token=token, port=args.port,
                                 signature_secret=token)
    stopping = threading.Event()

    def consume() -> None:
        while not stopping.is_set() or not pending.empty():
            try:
                event = pending.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                if isinstance(event, tuple):
                    incoming, mentioned = event
                    if not timeline_store.append(
                            incoming.conversation, message_id=incoming.message_id,
                            sender_id=incoming.account, text=incoming.text):
                        continue
                    timeline = group_timeline.setdefault(incoming.conversation,
                                                         deque(maxlen=12))
                    timeline.append({"sender_id": incoming.account,
                                     "text": incoming.text, "is_self": False})
                    if not _is_plus_one(incoming.text):
                        group_plus_one_followed.discard(incoming.conversation)
                    else:
                        status = "observe"
                        if (incoming.conversation not in group_plus_one_followed
                                and _plus_one_ready(timeline)):
                            receipt = group_transport.call("send_group_msg", {
                                "group_id": int(incoming.conversation.split(":", 1)[1]),
                                "message": "+1"})
                            if receipt.get("retcode") == 0:
                                group_plus_one_followed.add(incoming.conversation)
                                timeline_store.append(
                                    incoming.conversation,
                                    message_id=f"self:{incoming.message_id}",
                                    sender_id=args.bot_qq, text="+1", is_self=True)
                                timeline.append({"sender_id": args.bot_qq,
                                                 "text": "+1", "is_self": True})
                                status = "plus_one"
                            else:
                                status = "send_failed"
                        print(f"[onebot] group_message={incoming.message_id} status={status}",
                              flush=True)
                        continue
                    group = incoming.conversation.split(":", 1)[1]
                    card = table.command(incoming.text, group, manage=incoming.account == args.owner_qq)
                    if card:
                        status = "sent" if _send_table(group_transport, group, card) else "send_failed"
                        reply = card.text
                    else:
                        decision = group_policy.maybe_join(
                            conversation=incoming.conversation, text=incoming.text,
                            activity="balanced", timeline_entries=tuple(timeline))
                        status, reply = (_process_group(incoming, workflow, group_transport)
                                         if mentioned or decision.action == "join" else ("observe", ""))
                    if status == "sent":
                        timeline_store.append(incoming.conversation,
                                              message_id=f"self:{incoming.message_id}",
                                              sender_id=args.bot_qq, text=reply, is_self=True)
                        timeline.append({"sender_id": args.bot_qq, "text": "", "is_self": True})
                    print(f"[onebot] group_message={incoming.message_id} status={status}", flush=True)
                else:
                    status = _process_owner(event, workflow, port, prefs, table=table, group=preview_group)
                    print(f"[onebot] message={event.message_id} status={status}", flush=True)
            except Exception as exc:
                kind = getattr(exc, "kind", None)
                status = getattr(exc, "status", None)
                detail = f" kind={kind} status={status}" if kind else ""
                print(f"[onebot] error={type(exc).__name__}{detail}", flush=True)
            finally:
                pending.task_done()

    worker = threading.Thread(target=consume, name="onebot-whale", daemon=True)
    worker.start()
    try:
        listener.start()
        print(f"[onebot] webhook=http://127.0.0.1:{listener.port}/onebot", flush=True)
        print(f"[onebot] token file={config_dir / 'onebot_token'}", flush=True)
        print(f"[onebot] data={config_dir / 'memory.sqlite3'}", flush=True)
        zone = ZoneInfo("Asia/Shanghai")
        while True:
            if allowed_groups:
                try:
                    table.tick(datetime.now(zone), allowed_groups,
                               lambda group, card: _send_table(group_transport, group, card))
                except sqlite3.Error as exc:
                    print(f"[onebot] weekly={type(exc).__name__}", flush=True)
            time.sleep(30)
    except KeyboardInterrupt:
        return 0
    finally:
        listener.stop()
        stopping.set()
        worker.join(timeout=10)
        bundle.store.close()


if __name__ == "__main__":
    raise SystemExit(main())
