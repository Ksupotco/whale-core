"""Whale device protocol WebSocket edge: ``/api/devices/ws`` (whale-mobile).

Connection lifecycle (PROTOCOL.md §4):

    core  → auth.challenge {challenge: 64-hex, expires_in}
    phone → device.hello   {…, signature = Ed25519(challenge hex string as UTF-8), challenge}
    core  → device.hello_ack {session_id, server_time, heartbeat_interval, accepted_capabilities}
    … device.heartbeat every 30 s (offline after 90 s silence: marked, not kicked)
    … experience.event → event.ack (batched, also for duplicates)
    … action.request / sensor.query / notification.event / device.confirm.request ↓
    … action.receipt / sensor.result / notification.receipt|action|sync / device.confirm.response ↑

Violations: unparseable or schema-invalid frame → ``error.invalid_message`` and
the connection stays; unknown type → ignored (forward compatible); unknown
major version → ``error.protocol_version`` then close; revoked → ``error.device_revoked``
then close. Waiters are bound to the authenticated device *and* the connection
that sent the request, so one device can never complete another's request.

This module only speaks the wire; ``DeviceService`` owns policy, records and
the capability bridge.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ..kernel.errors import WhaleError
from ..kernel.ids import now_ms, ulid
from .contract import (DEVICE_ID_RE, FEATURE_OF, MAX_FRAME_BYTES, MESSAGE_TYPES, REF_FALLBACK, SUPPORTED_MAJOR,
                       ULID_RE, ContractValidator, frame, parse_major, strict_loads)

log = logging.getLogger("whale.devices.gateway")

GUI_CAPS = ("device.observe", "device.tap", "device.long_press", "device.swipe", "device.input_text",
            "device.key", "screen.context.read")


def verify_ed25519(pubkey_hex: str, signature_hex: str, message: str) -> bool:
    """Signature input is the UTF-8 (= ASCII) bytes of the hex string itself (§4)."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(pubkey_hex)).verify(
            bytes.fromhex(signature_hex), message.encode("utf-8"))
        return True
    except (InvalidSignature, ValueError):
        return False


def accept_capabilities(declared: list[str], scopes: list[str], features: dict[str, bool]) -> list[str]:
    """hello ∩ granted scopes, minus capabilities whose feature flag is off (§5.2)."""
    out = []
    for cap in declared:
        if cap not in scopes:
            continue
        need = FEATURE_OF.get(cap)
        if need and not features.get(need):
            continue
        if cap in GUI_CAPS and not (features.get("accessibility") or features.get("root_shell")):
            continue
        out.append(cap)
    return out


@dataclass(eq=False)
class Session:
    ws: Any
    device_id: str = ""
    session_id: str = ""
    name: str = ""
    slug: str = ""
    down_seq: int = 0
    up_seq: int | None = None
    seq_gaps: int = 0
    closed: bool = False
    offline: bool = False
    status: str = "connecting"
    connected_at: int = field(default_factory=now_ms)
    last_heartbeat: float = field(default_factory=time.monotonic)
    declared: list[str] = field(default_factory=list)
    accepted: list[str] = field(default_factory=list)
    features: dict[str, bool] = field(default_factory=dict)
    device: dict[str, Any] = field(default_factory=dict)
    app_version: str = ""
    flavor: str = ""
    battery: int | None = None
    network: str | None = None
    foreground: str | None = None
    modules: dict[str, str] = field(default_factory=dict)
    acks: list[str] = field(default_factory=list)
    ack_task: Any = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def state(self) -> str:
        if self.closed:
            return "offline"
        if self.offline:
            return "offline"
        return self.status if self.status in ("online", "connected_only", "paused") else "online"


@dataclass(eq=False)
class Pending:
    future: asyncio.Future
    device_id: str
    kind: str          # sensor.result | action.receipt | device.confirm.response | notification.receipt
    session: Session
    extra: dict[str, Any] = field(default_factory=dict)


class DeviceGateway:
    def __init__(self, service: Any, validator: ContractValidator | None = None) -> None:
        self.service = service
        self.contracts = validator or ContractValidator()
        self.sessions: dict[str, Session] = {}
        self.pending: dict[str, Pending] = {}
        self.by_msg_id: dict[str, str] = {}     # downlink message id → pending key (device error frames)
        self.heartbeat_interval = 30
        self.heartbeat_timeout = 90.0
        self.challenge_ttl = 30
        self.ack_delay = 0.05
        self.handlers: dict[str, Callable[[Session, dict[str, Any]], Any]] = {
            "device.heartbeat": self._on_heartbeat,
            "experience.event": self._on_event,
            "sensor.result": self._on_sensor_result,
            "action.receipt": self._on_receipt,
            "device.confirm.response": self._on_confirm,
            "notification.receipt": self._on_notification_receipt,
            "notification.action": self._on_notification_action,
            "notification.sync": self._on_notification_sync,
            "run.trace.event": self._on_trace,
            "recording.state": self._on_recording,
            "error": self._on_error,
        }

    # -- sending ------------------------------------------------------------------------------------
    async def send(self, sess: Session, msg_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        async with sess.lock:
            if sess.closed:
                raise WhaleError("unavailable", "device connection is closed")
            msg = frame(msg_type, payload, sess.down_seq + 1)
            err = self.contracts.validate(msg_type, msg)
            if err:
                raise WhaleError("invalid_params", "outgoing " + msg_type + " violates contract: " + err)
            text = json.dumps(msg, ensure_ascii=False, allow_nan=False)
            if len(text.encode("utf-8")) > MAX_FRAME_BYTES:
                raise WhaleError("invalid_params", "frame exceeds 256 KiB; use an output reference")
            sess.down_seq += 1
            try:
                await sess.ws.send_text(text)
            except Exception as exc:  # peer gone mid-send
                sess.closed = True
                raise WhaleError("unavailable", "device connection lost") from exc
            return msg

    async def send_error(self, sess: Session, ref_id: Any, code: str, message: str) -> None:
        if not (isinstance(ref_id, str) and ULID_RE.match(ref_id)):
            ref_id = REF_FALLBACK
        try:
            await self.send(sess, "error", {"ref_id": ref_id, "code": code, "message": message[:500] or code})
        except WhaleError:
            pass

    async def request(self, sess: Session, key: str, kind: str, msg_type: str, payload: dict[str, Any],
                      timeout_s: float, extra: dict[str, Any] | None = None) -> Any:
        """Send a downlink request and wait for its response bound to (device, connection).

        Raises ``asyncio.TimeoutError`` when nothing arrives in time.
        """
        if key in self.pending:
            raise WhaleError("conflict", "a request with this id is already waiting")
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self.pending[key] = Pending(fut, sess.device_id, kind, sess, extra or {})
        msg_id = ""
        try:
            msg = await self.send(sess, msg_type, payload)
            msg_id = msg["id"]
            self.by_msg_id[msg_id] = key
            return await asyncio.wait_for(asyncio.shield(fut), timeout_s)
        finally:
            self.pending.pop(key, None)
            self.by_msg_id.pop(msg_id, None)

    def _take(self, key: str, sess: Session, kind: str) -> Pending | None:
        """Pop the waiter only if this very connection of this very device owns it."""
        pend = self.pending.get(key)
        if pend is None:
            return None
        if pend.device_id != sess.device_id or pend.session is not sess or pend.kind != kind:
            log.warning("ignoring %s for %s from device %s (owned by %s)", kind, key, sess.device_id, pend.device_id)
            return None
        self.pending.pop(key, None)
        return pend

    # -- connection -------------------------------------------------------------------------------
    async def endpoint(self, ws: Any) -> None:
        await ws.accept()
        sess = Session(ws)
        challenge = secrets.token_hex(32)
        deadline = time.monotonic() + self.challenge_ttl
        try:
            await self.send(sess, "auth.challenge", {"challenge": challenge, "expires_in": int(self.challenge_ttl)})
            first = await asyncio.wait_for(ws.receive(), self.challenge_ttl + 5)
        except Exception:
            await self._close(sess, 1008)
            return
        try:
            raw = self._text(first)
            if raw is None or not await self._hello(sess, raw, challenge, deadline):
                return
            while not sess.closed:
                msg = await ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                raw = self._text(msg)
                if raw is None:
                    continue
                if not await self._frame(sess, raw):
                    break
        except Exception as exc:  # disconnects surface as RuntimeError / WebSocketDisconnect
            if not sess.closed:
                log.debug("device socket ended: %r", exc)
        finally:
            await self.finish(sess, "disconnected")
            await self._close(sess, 1000)

    @staticmethod
    def _text(msg: dict[str, Any]) -> str | None:
        if msg.get("type") != "websocket.receive":
            return None
        if msg.get("text") is not None:
            return msg["text"]
        if msg.get("bytes") is not None:
            try:
                return msg["bytes"].decode("utf-8")
            except UnicodeDecodeError:
                return ""
        return None

    async def _close(self, sess: Session, code: int) -> None:
        sess.closed = True
        try:
            await sess.ws.close(code)
        except Exception:
            pass

    async def kick(self, device_id: str, code: str, message: str) -> bool:
        sess = self.sessions.get(device_id)
        if sess is None:
            return False
        await self.send_error(sess, REF_FALLBACK, code, message)
        await self.finish(sess, code)
        await self._close(sess, 1008)
        return True

    async def finish(self, sess: Session, reason: str) -> None:
        """Idempotent teardown: release this connection's waiters and unpublish it."""
        current = bool(sess.device_id) and self.sessions.get(sess.device_id) is sess
        sess.closed = True
        if sess.ack_task is not None and not sess.ack_task.done():
            sess.ack_task.cancel()
        for key, pend in list(self.pending.items()):
            if pend.session is not sess:
                continue
            self.pending.pop(key, None)
            if pend.future.done():
                continue
            if pend.kind == "action.receipt":
                # A dropped connection says nothing about effects.
                pend.future.set_result({"status": "unknown", "provider_ref": "core:connection-lost"})
            elif pend.kind == "device.confirm.response":
                pend.future.set_result({"approved": False, "reason": "unavailable"})
            else:
                pend.future.set_exception(WhaleError("unavailable", "device disconnected"))
        if current:
            del self.sessions[sess.device_id]
            await self.service.session_closed(sess, reason)

    async def _hello(self, sess: Session, raw: str, challenge: str, deadline: float) -> bool:
        ref: Any = REF_FALLBACK

        async def fail(code: str, message: str) -> bool:
            await self.send_error(sess, ref, code, message)
            log.warning("device hello rejected (%s): %s", code, message)
            await self._close(sess, 1008)
            return False

        try:
            msg = strict_loads(raw)
        except ValueError:
            return await fail("auth_failed", "first frame is not valid JSON")
        if not isinstance(msg, dict):
            return await fail("auth_failed", "first frame is not an object")
        ref = msg.get("id")
        payload = msg.get("payload") if isinstance(msg.get("payload"), dict) else {}
        for version in (msg.get("v"), payload.get("protocol_version")):
            major = parse_major(version)
            if major is not None and major != SUPPORTED_MAJOR:
                return await fail("protocol_version", "unsupported protocol version " + str(version))
        if msg.get("type") != "device.hello":
            return await fail("auth_failed", "first frame must be device.hello")
        err = self.contracts.validate("device.hello", msg)
        if err:
            return await fail("auth_failed", "device.hello invalid: " + err)
        p = msg["payload"]
        if time.monotonic() > deadline:
            return await fail("auth_failed", "challenge expired")
        if p["challenge"] != challenge:
            return await fail("auth_failed", "challenge mismatch")
        record = self.service.registry.get(p["device_id"])
        if record is None:
            return await fail("auth_failed", "device not paired")
        if record["revoked"]:
            return await fail("device_revoked", "device has been revoked")
        try:
            good = verify_ed25519(record["pubkey"], p["signature"], challenge)
        except ImportError:
            return await fail("unavailable", "core lacks the cryptography package")
        if not good:
            return await fail("auth_failed", "signature verification failed")
        sess.device_id = p["device_id"]
        sess.session_id = ulid()
        sess.name = record["name"]
        sess.status = "online"
        sess.up_seq = int(msg["seq"]) + 1
        sess.declared = list(p["capabilities"])
        sess.features = {k: bool(v) for k, v in p["features"].items()}
        sess.accepted = accept_capabilities(sess.declared, record["scopes"], sess.features)
        sess.device = dict(p["device"])
        sess.app_version = p["app_version"]
        sess.flavor = p["app_flavor"]
        sess.last_heartbeat = time.monotonic()
        try:
            await self.send(sess, "device.hello_ack", {
                "session_id": sess.session_id, "server_time": now_ms(),
                "heartbeat_interval": int(self.heartbeat_interval), "accepted_capabilities": sess.accepted})
        except WhaleError:
            return False
        old = self.sessions.get(sess.device_id)
        if old is not None:  # the same phone reconnected: the new socket replaces the old one
            await self.finish(old, "replaced")
            await self._close(old, 1000)
        self.sessions[sess.device_id] = sess
        await self.service.session_opened(sess)
        return True

    async def _frame(self, sess: Session, raw: str) -> bool:
        """Handle one authenticated frame. False → close the connection."""
        if len(raw.encode("utf-8")) > MAX_FRAME_BYTES:
            await self.send_error(sess, REF_FALLBACK, "invalid_message", "frame exceeds 256 KiB")
            return True
        try:
            msg = strict_loads(raw)
        except ValueError:
            await self.send_error(sess, REF_FALLBACK, "invalid_message", "frame is not valid JSON")
            return True
        if not isinstance(msg, dict):
            await self.send_error(sess, REF_FALLBACK, "invalid_message", "frame is not an object")
            return True
        ref = msg.get("id")
        major = parse_major(msg.get("v"))
        if major is not None and major != SUPPORTED_MAJOR:
            await self.send_error(sess, ref, "protocol_version", "unsupported major version " + str(msg.get("v")))
            return False
        if self.sessions.get(sess.device_id) is not sess:
            return False
        record = self.service.registry.get(sess.device_id)
        if record is None or record["revoked"]:  # revocation is re-checked on every frame
            await self.send_error(sess, ref, "device_revoked", "device has been revoked")
            await self.finish(sess, "revoked")
            return False
        msg_type = msg.get("type")
        if not isinstance(msg_type, str) or msg_type not in MESSAGE_TYPES:
            log.info("ignoring unknown device message type %r", msg_type)
            return True
        err = self.contracts.validate(msg_type, msg)
        if err:
            await self.send_error(sess, ref, "invalid_message", (msg_type + ": " + err)[:500])
            return True
        seq = msg["seq"]
        if sess.up_seq is not None and seq != sess.up_seq:
            sess.seq_gaps += 1
            log.info("device %s uplink seq jump: expected %s got %s", sess.device_id, sess.up_seq, seq)
        sess.up_seq = seq + 1
        handler = self.handlers.get(msg_type)
        if handler is None:
            return True   # valid but not meaningful for the core (call.*, hello again …)
        try:
            await handler(sess, msg)
        except WhaleError as exc:
            if exc.code == "unavailable":
                return False
            log.warning("device %s handling failed: %s", msg_type, exc.message)
        return True

    # -- uplink handlers --------------------------------------------------------------------------
    async def _on_heartbeat(self, sess: Session, msg: dict[str, Any]) -> None:
        p = msg["payload"]
        if p["device_id"] != sess.device_id:
            log.warning("heartbeat device_id mismatch on %s", sess.device_id)
            return
        before = (sess.state(), sess.battery, sess.network)
        recovered = sess.offline
        sess.last_heartbeat = time.monotonic()
        sess.offline = False
        sess.status = p["status"]
        if "battery" in p:
            sess.battery = p["battery"]
        if "network" in p:
            sess.network = p["network"]
        if isinstance(p.get("modules"), dict):
            sess.modules = {str(k): str(v) for k, v in p["modules"].items()}
        await self.service.heartbeat(sess, changed=(sess.state(), sess.battery, sess.network) != before,
                                     recovered=recovered)

    async def _on_event(self, sess: Session, msg: dict[str, Any]) -> None:
        p = msg["payload"]
        if p["device_id"] != sess.device_id:
            await self.send_error(sess, msg["id"], "invalid_message", "event device_id does not match this device")
            return
        await self.service.experience(sess, p)
        sess.acks.append(p["event_id"])  # new and duplicate alike: the phone stops resending
        if len(sess.acks) >= 200:
            await self.flush_acks(sess)
        elif sess.ack_task is None or sess.ack_task.done():
            sess.ack_task = asyncio.get_running_loop().create_task(self._ack_later(sess))

    async def _ack_later(self, sess: Session) -> None:
        await asyncio.sleep(self.ack_delay)
        await self.flush_acks(sess)

    async def flush_acks(self, sess: Session) -> None:
        while sess.acks and not sess.closed:
            batch, sess.acks = list(dict.fromkeys(sess.acks[:200])), sess.acks[200:]
            try:
                await self.send(sess, "event.ack", {"event_ids": batch})
            except WhaleError:
                return

    async def _on_sensor_result(self, sess: Session, msg: dict[str, Any]) -> None:
        p = msg["payload"]
        pend = self._take(p["request_id"], sess, "sensor.result")
        if pend is not None and not pend.future.done():
            pend.future.set_result(p)

    async def _on_receipt(self, sess: Session, msg: dict[str, Any]) -> None:
        p = msg["payload"]
        record = await self.service.receipt(sess, p)
        if record is None or record["status"] == "received" or record["status"] == "pending":
            return
        pend = self._take(p["action_id"], sess, "action.receipt")
        if pend is not None and not pend.future.done():
            pend.future.set_result({**(record.get("receipt") or {}), "status": record["status"]})

    async def _on_confirm(self, sess: Session, msg: dict[str, Any]) -> None:
        p = msg["payload"]
        pend = self._take(p["challenge_id"], sess, "device.confirm.response")
        if pend is None or pend.future.done():
            return
        approved = bool(p["approved"])
        signature = p.get("signature")
        if approved:
            record = self.service.registry.get(sess.device_id) or {}
            approved = bool(signature) and verify_ed25519(record.get("pubkey", ""), signature, p["challenge_id"])
        pend.future.set_result({"approved": approved, "reason": p.get("reason") or (
            "" if approved or not p["approved"] else "bad_signature"), "responded_at": p["responded_at"]})

    async def _on_notification_receipt(self, sess: Session, msg: dict[str, Any]) -> None:
        p = msg["payload"]
        if p["device_id"] != sess.device_id:
            return
        await self.service.notification_receipt(sess, p)
        pend = self._take("notif:" + p["event_id"], sess, "notification.receipt")
        if pend is not None and not pend.future.done():
            pend.future.set_result(p)

    async def _on_notification_action(self, sess: Session, msg: dict[str, Any]) -> None:
        p = msg["payload"]
        if p["device_id"] == sess.device_id:
            await self.service.notification_action(sess, p)

    async def _on_notification_sync(self, sess: Session, msg: dict[str, Any]) -> None:
        await self.service.notification_sync(sess, msg["payload"].get("last_event_id"))

    async def _on_trace(self, sess: Session, msg: dict[str, Any]) -> None:
        await self.service.app.bus.emit("device.trace", {"device_id": sess.device_id, "trace": msg["payload"]})

    async def _on_recording(self, sess: Session, msg: dict[str, Any]) -> None:
        await self.service.app.bus.emit("device.recording", {"device_id": sess.device_id, **msg["payload"]})

    async def _on_error(self, sess: Session, msg: dict[str, Any]) -> None:
        p = msg["payload"]
        log.warning("device %s reported error %s for %s: %s", sess.device_id, p["code"], p["ref_id"], p["message"])
        key = self.by_msg_id.get(p["ref_id"])
        pend = self.pending.get(key or "")
        if pend is None or pend.session is not sess:
            return
        if pend.kind == "action.receipt":
            # The phone refused the request frame itself: it was not executed.
            record = await self.service.receipt(sess, {"action_id": key, "status": "failed",
                                                       "error_code": p["code"],
                                                       "result": {"summary": p["message"]},
                                                       "provider_ref": "device:error-frame"})
            pend = self._take(key or "", sess, "action.receipt")
            if pend is not None and not pend.future.done() and record is not None:
                pend.future.set_result({**(record.get("receipt") or {}), "status": record["status"]})
        else:
            self.pending.pop(key or "", None)
            if not pend.future.done():
                pend.future.set_exception(WhaleError("unavailable", "device error " + p["code"] + ": " + p["message"]))

    # -- watchdog ---------------------------------------------------------------------------------
    async def sweep(self) -> None:
        now = time.monotonic()
        for sess in list(self.sessions.values()):
            if not sess.offline and not sess.closed and now - sess.last_heartbeat > self.heartbeat_timeout:
                sess.offline = True     # marked offline, not kicked (§1)
                await self.service.heartbeat(sess, changed=True, recovered=False)

    async def close_all(self) -> None:
        for sess in list(self.sessions.values()):
            await self.finish(sess, "shutdown")
            await self._close(sess, 1001)


def is_device_id(value: str) -> bool:
    return bool(DEVICE_ID_RE.match(value or ""))
