"""Owner-facing emotion and weekly portrait management."""
from __future__ import annotations

from whale_core.api.studio.shared import (
    StudioError, _audit, _ok, _reject_unknown_fields, _require_json_body,
)
from whale_core.memory.emotion import EmotionMemory, EVENT_PREFIX
from whale_core.memory.person_model import ITEM_PREFIX, PersonModel


def _list(handler, ctx, svc) -> None:
    owner_ids = set()
    for key in ctx.store.keys():
        if key.startswith((EVENT_PREFIX, ITEM_PREFIX)):
            item = ctx.store.get(key)
            if isinstance(item, dict) and item.get("owner_id"):
                owner_ids.add(str(item["owner_id"]))
    emotion, portrait = EmotionMemory(ctx.store), PersonModel(ctx.store)
    owners = []
    for owner_id in sorted(owner_ids):
        events = []
        for event in emotion.list_events(owner_id):
            source = ctx.store.get_record(event.get("source_id", ""))
            source_text = str(source.content.get("summary") or "") if source else ""
            events.append({**event, "source_text": source_text[:600]})
        owners.append({
            "owner_id": owner_id,
            "events": events,
            "profiles": tuple(item for item in portrait.list_items(owner_id)
                              if not item.get("deleted")),
        })
    handler._send(200, _ok({"owners": owners}))


def _change(handler, ctx, svc) -> None:
    body = _require_json_body(handler)
    _reject_unknown_fields(body, {
        "owner_id", "item_id", "event_id", "action", "active",
        "effective_response", "expression_preference", "emotion", "trigger", "need",
        "context", "topic",
    })
    owner_id = str(body.get("owner_id") or "").strip()
    action = body.get("action")
    if not owner_id or action not in ("update", "delete_profile", "delete_event", "update_event"):
        raise StudioError(400, "invalid_input", "缺少主体或操作无效")
    with ctx.lock:
        if action == "update":
            item_id = str(body.get("item_id") or "")
            active = body.get("active")
            response = body.get("effective_response")
            expression = body.get("expression_preference")
            context = body.get("context")
            need = body.get("need")
            topic = body.get("topic")
            if (not item_id or (active is not None and not isinstance(active, bool))
                    or (response is not None and not isinstance(response, str))
                    or (expression is not None and not isinstance(expression, str))
                    or any(value is not None and not isinstance(value, str)
                           for value in (context, need, topic))):
                raise StudioError(400, "invalid_input", "画像修改参数无效")
            changed = PersonModel(ctx.store).update_item(
                owner_id, item_id, active=active, context=context, need=need, topic=topic,
                effective_response=response, expression_preference=expression)
        elif action == "delete_profile":
            changed = PersonModel(ctx.store).delete_item(
                owner_id, str(body.get("item_id") or ""))
        elif action == "update_event":
            fields = {name: body.get(name) for name in ("emotion", "trigger", "need")}
            if (not body.get("event_id") or not any(value is not None for value in fields.values())
                    or any(value is not None and not isinstance(value, str)
                           for value in fields.values())):
                raise StudioError(400, "invalid_input", "情绪修改参数无效")
            changed = EmotionMemory(ctx.store).update_event(
                owner_id, str(body["event_id"]), **fields)
        else:
            changed = EmotionMemory(ctx.store).delete_event(
                owner_id, str(body.get("event_id") or ""))
        if not changed:
            raise StudioError(404, "not_found", "记录不存在")
        _audit(ctx, f"portrait.{action}", reason=owner_id)
    handler._send(200, _ok({"changed": True}))
