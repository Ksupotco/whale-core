"""Whale device protocol (whale-mobile ↔ core): constants, envelope helpers, schema validator.

The canonical contract lives in ``contracts/device`` (PROTOCOL.md + JSON schemas).
The schemas use a deliberately tiny draft 2020-12 subset (no ``$ref``, no
combinators), so a ~60 line validator is enough. Anything outside the subset
fails closed at load time.

When the schema files cannot be found (e.g. a wheel without package data) the
validator degrades to a built-in structural check: envelope shape plus the
required payload keys of every message the core handles.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from ..kernel.ids import now_ms, ulid

log = logging.getLogger("whale.devices.contract")

PROTOCOL_VERSION = "1.0"
SUPPORTED_MAJOR = 1
WS_PATH = "/api/devices/ws"
MAX_FRAME_BYTES = 256 * 1024
TS_MAX = 4102444800000
REF_FALLBACK = "0" * 26          # a well-formed ULID for error.ref_id when the culprit had none

ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
DEVICE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
IDEM_RE = re.compile(r"^[0-9A-Za-z:_-]{8,128}$")

# §5.1 — the single capability namespace (device.hello enum, verbatim).
CAPABILITIES: tuple[str, ...] = (
    "screen.context.read", "screen.history.search", "screen.evidence.read", "screen.live.subscribe",
    "phone.notification.post", "phone.notification.read", "phone.call.ring", "phone.call.audio",
    "phone.device.safe_action", "glasses.card.show", "glasses.card.update", "glasses.card.dismiss",
    "device.observe", "device.tap", "device.long_press", "device.swipe", "device.input_text", "device.key",
    "device.wait", "device.app.launch", "device.app.list", "device.app.install", "device.app.uninstall",
    "device.shell", "device.file.read", "device.file.write", "device.file.list", "device.logcat",
    "device.media.seek_to", "device.stop", "device.output.read", "device.screen.record", "device.status",
)
# §5.3 — sensor.query operations; everything else (but device.output.read) is an action.
QUERY_OPS: tuple[str, ...] = ("screen.context.read", "screen.history.search", "screen.evidence.read",
                              "screen.live.subscribe", "device.output.read", "device.app.list", "device.status")
ACTION_CAPS: tuple[str, ...] = tuple(c for c in CAPABILITIES if c != "device.output.read")
# §5.2 — a capability whose feature flag is false is ignored even if declared.
FEATURE_OF: dict[str, str] = {
    "device.shell": "root_shell", "device.file.read": "root_shell", "device.file.write": "root_shell",
    "device.file.list": "root_shell", "device.logcat": "root_shell", "device.app.install": "root_shell",
    "device.app.uninstall": "root_shell", "device.screen.record": "media_projection",
    "phone.call.ring": "telecom", "phone.call.audio": "telecom",
}
TERMINAL = ("succeeded", "failed", "timeout", "cancelled", "unknown")
ERROR_CODES = ("protocol_version", "auth_failed", "device_revoked", "scope_denied", "capability_unknown",
               "expired", "duplicate", "rate_limited", "unavailable", "permission_missing", "invalid_message",
               "internal", "background_restricted", "timeout", "cancelled", "stale_observation",
               "element_not_found", "approval_denied", "estop_active")
NOTIFY_CATEGORIES = ("chat.message", "task.started", "task.progress", "task.completed", "task.failed",
                     "approval.requested", "device.offline", "device.online", "memory.updated",
                     "reminder.triggered")
MESSAGE_TYPES = ("auth.challenge", "device.hello", "device.hello_ack", "device.heartbeat", "experience.event",
                 "event.ack", "sensor.query", "sensor.result", "action.request", "action.receipt",
                 "device.confirm.request", "device.confirm.response", "run.trace.event", "recording.control",
                 "recording.state", "call.invite", "call.answer", "call.hangup", "call.state", "error",
                 "notification.event", "notification.receipt", "notification.action", "notification.sync",
                 "notification.revoke")


def parse_major(v: Any) -> int | None:
    if not isinstance(v, str):
        return None
    m = re.match(r"^([0-9]+)\.[0-9]+$", v)
    return int(m.group(1)) if m else None


def frame(msg_type: str, payload: dict[str, Any], seq: int, direction: str = "downlink") -> dict[str, Any]:
    return {"v": PROTOCOL_VERSION, "id": ulid(), "type": msg_type, "ts": now_ms(), "seq": seq,
            "dir": direction, "payload": payload}


def strict_loads(raw: str | bytes) -> Any:
    def bad_constant(name: str) -> Any:
        raise ValueError("non-finite number " + name)
    return json.loads(raw, parse_constant=bad_constant)


# -- schema subset validator ---------------------------------------------------------------------
_ANNOTATIONS = {"$schema", "$id", "title", "description", "default", "examples", "$comment"}
_SUPPORTED = {"type", "const", "enum", "pattern", "minLength", "maxLength", "minimum", "maximum",
              "properties", "required", "additionalProperties", "maxProperties", "minProperties",
              "items", "minItems", "maxItems", "uniqueItems"} | _ANNOTATIONS


def _type_ok(kind: str, value: Any) -> bool:
    if kind == "object":
        return isinstance(value, dict)
    if kind == "array":
        return isinstance(value, list)
    if kind == "string":
        return isinstance(value, str)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "null":
        return value is None
    return False


def audit_schema(schema: Any, where: str = "$") -> None:
    """Fail closed on keywords outside the contract subset (§19)."""
    if not isinstance(schema, dict):
        return
    for key, sub in schema.items():
        if key not in _SUPPORTED:
            raise ValueError("unsupported schema keyword " + key + " at " + where)
        if key == "properties":
            for name, child in sub.items():
                audit_schema(child, where + "." + name)
        elif key in ("items", "additionalProperties") and isinstance(sub, dict):
            audit_schema(sub, where + "[]")


def check(schema: dict[str, Any], value: Any, path: str = "$") -> str | None:
    """Returns the first violation as text, or None when ``value`` conforms."""
    kind = schema.get("type")
    if kind is not None:
        kinds = kind if isinstance(kind, list) else [kind]
        if not any(_type_ok(k, value) for k in kinds):
            return path + ": expected " + "|".join(kinds)
    if "const" in schema and value != schema["const"]:
        return path + ": must equal " + json.dumps(schema["const"])
    if "enum" in schema and value not in schema["enum"]:
        return path + ": not an allowed value"
    if isinstance(value, str):
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            return path + ": does not match pattern"
        if len(value) < schema.get("minLength", 0):
            return path + ": too short"
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            return path + ": too long"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            return path + ": below minimum"
        if "maximum" in schema and value > schema["maximum"]:
            return path + ": above maximum"
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                return path + ": missing required " + key
        if "maxProperties" in schema and len(value) > schema["maxProperties"]:
            return path + ": too many properties"
        if len(value) < schema.get("minProperties", 0):
            return path + ": too few properties"
        extra = schema.get("additionalProperties", True)
        for key, item in value.items():
            if key in props:
                err = check(props[key], item, path + "." + key)
            elif extra is False:
                err = path + ": unexpected property " + key
            elif isinstance(extra, dict):
                err = check(extra, item, path + "." + key)
            else:
                err = None
            if err:
                return err
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            return path + ": too few items"
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            return path + ": too many items"
        if schema.get("uniqueItems"):
            seen = [json.dumps(v, sort_keys=True) for v in value]
            if len(set(seen)) != len(seen):
                return path + ": items not unique"
        items = schema.get("items")
        if isinstance(items, dict):
            for idx, item in enumerate(value):
                err = check(items, item, path + "[" + str(idx) + "]")
                if err:
                    return err
    return None


# Built-in fallback: required payload keys for every message the core parses or emits.
_FALLBACK_REQUIRED: dict[str, tuple[str, ...]] = {
    "auth.challenge": ("challenge", "expires_in"),
    "device.hello": ("protocol_version", "device_id", "capabilities", "features", "device", "app_version",
                     "app_flavor", "signature", "challenge"),
    "device.hello_ack": ("session_id", "server_time", "heartbeat_interval", "accepted_capabilities"),
    "device.heartbeat": ("device_id", "observed_at", "status"),
    "experience.event": ("event_id", "kind", "occurred_at", "device_id", "source", "summary", "privacy_class",
                         "schema_version"),
    "event.ack": ("event_ids",),
    "sensor.query": ("request_id", "operation", "filters", "max_items", "deadline"),
    "sensor.result": ("request_id", "status", "data", "truncated", "source"),
    "action.request": ("action_id", "capability", "arguments", "timeout_ms", "expires_at", "idempotency_key",
                       "authorized_scope"),
    "action.receipt": ("action_id", "status"),
    "device.confirm.request": ("challenge_id", "device_id", "principal_id", "channel", "scopes", "expires_at"),
    "device.confirm.response": ("challenge_id", "approved", "responded_at"),
    "notification.event": ("event_id", "notification_id", "device_id", "category", "title", "body", "priority",
                           "created_at", "expires_at"),
    "notification.receipt": ("event_id", "notification_id", "device_id", "status", "received_at"),
    "notification.action": ("event_id", "notification_id", "device_id", "action_id", "submitted_at"),
    "notification.sync": ("device_id", "synced_at"),
    "notification.revoke": ("event_id", "notification_id", "device_id"),
    "run.trace.event": ("run_id", "seq", "timestamp_ms", "kind"),
    "recording.state": ("run_id", "state"),
    "error": ("ref_id", "code", "message"),
}
_ENVELOPE = {
    "type": "object", "additionalProperties": False, "required": ["v", "id", "type", "ts", "seq", "dir", "payload"],
    "properties": {"v": {"type": "string", "pattern": r"^1\.[0-9]+$"},
                   "id": {"type": "string", "pattern": ULID_RE.pattern},
                   "type": {"type": "string", "enum": list(MESSAGE_TYPES)},
                   "ts": {"type": "integer", "minimum": 0, "maximum": TS_MAX},
                   "seq": {"type": "integer", "minimum": 0, "maximum": 9007199254740991},
                   "dir": {"type": "string", "enum": ["uplink", "downlink"]},
                   "payload": {"type": "object"}},
}


def contracts_dir() -> Path | None:
    here = Path(__file__).resolve()
    candidates = []
    env = os.environ.get("WHALE_CONTRACTS_DIR")
    if env:
        candidates.append(Path(env))
    parents = list(here.parents)
    for depth in (3, 1):   # source checkout (<repo>/contracts) or packaged (<whale>/contracts)
        if len(parents) > depth:
            candidates.append(parents[depth] / "contracts" / "device")
    for cand in candidates:
        if (cand / "messages" / "envelope.schema.json").is_file():
            return cand
    return None


class ContractValidator:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root if root is not None else contracts_dir()
        self.envelope: dict[str, Any] = _ENVELOPE
        self.messages: dict[str, dict[str, Any]] = {}
        self.full = False
        if self.root is None:
            log.warning("contracts/device not found; using the built-in structural validator")
            return
        try:
            for path in sorted((self.root / "messages").glob("*.schema.json")):
                schema = json.loads(path.read_text(encoding="utf-8"))
                audit_schema(schema)
                name = path.name[: -len(".schema.json")]
                if name == "envelope":
                    self.envelope = schema
                else:
                    self.messages[name] = schema
            self.full = bool(self.messages) and self.envelope is not _ENVELOPE
            if not self.full:
                self.messages = {}
                self.envelope = _ENVELOPE
        except (OSError, ValueError) as exc:
            log.warning("contracts/device unusable (%r); using the built-in structural validator", exc)
            self.messages = {}
            self.envelope = _ENVELOPE

    def validate(self, msg_type: str, msg: Any) -> str | None:
        err = check(self.envelope, msg)
        if err:
            return "envelope " + err
        if msg.get("type") != msg_type:
            return "type mismatch"
        schema = self.messages.get(msg_type)
        if schema is not None:
            return check(schema, msg)
        payload = msg["payload"]
        for key in _FALLBACK_REQUIRED.get(msg_type, ()):
            if key not in payload:
                return "$.payload: missing required " + key
        return None

    def object_schema(self, name: str) -> dict[str, Any] | None:
        if self.root is None:
            return None
        path = self.root / "objects" / (name + ".schema.json")
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
