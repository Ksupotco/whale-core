"""Capability bridge: V2 device capabilities → core Capabilities.

A paired phone that is online contributes one Capability per accepted V2
capability, named ``device.<short>.<capability>`` (e.g. ``device.pixel.device.tap``)
with provider ``device:<device_id>``. Invoking it sends ``action.request`` (or
``sensor.query`` for read operations) and waits for the truthful outcome.

Outcome mapping (contracts V2 §7.2 → ToolResult):
    succeeded                → ok
    failed / cancelled       → error
    timeout / unknown        → unknown   (may have happened; never auto-retried)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..capabilities.spec import Risk, ToolResult
from ..kernel.errors import WhaleError


def _int(desc: str, **kw: Any) -> dict[str, Any]:
    return {"type": "integer", "description": desc, **kw}


def _str(desc: str, **kw: Any) -> dict[str, Any]:
    return {"type": "string", "description": desc, **kw}


def _obj(required: list[str] | None = None, **props: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"type": "object", "properties": props}
    if required:
        out["required"] = required
    return out


_TARGET = {
    "x": _int("X coordinate in screen pixels"),
    "y": _int("Y coordinate in screen pixels"),
    "element_id": _int("element_id from the most recent device.observe (needs observation_id)"),
    "observation_id": _str("observation_id returned by the device.observe that produced element_id"),
}


@dataclass(frozen=True)
class CapDef:
    kind: str                      # action | query
    risk: Risk
    title: str
    description: str
    params: dict[str, Any] = field(default_factory=lambda: _obj())
    timeout_s: float = 15.0        # default device-side execution budget
    max_timeout_s: float = 60.0


CATALOG: dict[str, CapDef] = {
    # -- read-only queries (sensor.query) -------------------------------------------------------
    "device.status": CapDef("query", Risk.read, "Phone status",
                            "Battery, network, foreground app and root availability of the phone. No side effects."),
    "device.app.list": CapDef("query", Risk.read, "List apps", "List installed apps (package names and labels).",
                              _obj(query=_str("optional substring filter"))),
    "screen.context.read": CapDef("query", Risk.read, "Read screen context",
                                  "What is currently on the phone screen (app, visible text summary).", _obj()),
    "screen.history.search": CapDef("query", Risk.read, "Search screen history",
                                    "Search what the owner saw on the phone earlier (screen memory).",
                                    _obj(["query"], query=_str("what to look for"),
                                         since=_int("only after this unix-ms time"),
                                         until=_int("only before this unix-ms time"))),
    "screen.evidence.read": CapDef("query", Risk.read, "Read screen evidence",
                                   "Fetch the evidence behind a capture:… reference from an event or search hit.",
                                   _obj(["ref"], ref=_str("evidence reference, e.g. capture:segment_…"))),
    "device.output.read": CapDef("query", Risk.read, "Read action output",
                                 "Read a large action output by its output_ref (agent:out:… / agent:ui:…).",
                                 _obj(["output_ref"], output_ref=_str("output_ref from an action result"),
                                      offset=_int("byte offset", default=0))),
    # -- observation & waiting -----------------------------------------------------------------
    "device.observe": CapDef("action", Risk.read, "Observe screen",
                             "Capture the current UI: foreground app and the list of on-screen elements with "
                             "element_id, text and bounds. Call before tapping elements.",
                             _obj(ui_tree={"type": "boolean", "description": "include the element tree",
                                           "default": True},
                                  screenshot={"type": "boolean", "description": "also capture a screenshot ref",
                                              "default": False},
                                  max_elements=_int("element budget", default=200)),
                             timeout_s=60, max_timeout_s=120),
    "device.wait": CapDef("action", Risk.read, "Wait", "Wait on the phone for the UI to settle.",
                          _obj(["duration_ms"], duration_ms=_int("how long to wait (ms)", default=1500)),
                          timeout_s=10, max_timeout_s=60),
    "phone.notification.read": CapDef("action", Risk.read, "Read notifications",
                                      "Read the phone's current notifications.", _obj()),
    # -- UI actions (external) ----------------------------------------------------------------------
    "device.tap": CapDef("action", Risk.external, "Tap", "Tap on the phone screen: either {x, y} or "
                         "{element_id, observation_id} from the latest observe.", _obj(**_TARGET)),
    "device.long_press": CapDef("action", Risk.external, "Long press", "Long-press a point or an element.",
                                _obj(duration_ms=_int("press duration (ms)", default=800), **_TARGET)),
    "device.swipe": CapDef("action", Risk.external, "Swipe",
                           "Swipe from (x, y) to (x2, y2), e.g. to scroll a list.",
                           _obj(["x", "y", "x2", "y2"], x=_int("start x"), y=_int("start y"), x2=_int("end x"),
                                y2=_int("end y"), duration_ms=_int("swipe duration (ms)", default=300))),
    "device.input_text": CapDef("action", Risk.external, "Type text",
                                "Type text into the focused field (optionally focus element_id first).",
                                _obj(["text"], text=_str("text to type"), element_id=_TARGET["element_id"],
                                     observation_id=_TARGET["observation_id"])),
    "device.key": CapDef("action", Risk.external, "Press key", "Press a system key on the phone.",
                         _obj(["key"], key=_str("key name", enum=["back", "home", "enter", "delete", "tab",
                                                                  "volume_up", "volume_down", "media_play_pause",
                                                                  "media_next", "media_previous", "recents"]))),
    "device.app.launch": CapDef("action", Risk.external, "Launch app", "Open an app by package name.",
                                _obj(["package"], package=_str("Android package, e.g. com.android.settings"))),
    "device.media.seek_to": CapDef("action", Risk.external, "Seek media",
                                   "Seek the active media session to position_ms or ratio (0..1).",
                                   _obj(position_ms=_int("target position (ms)"),
                                        ratio={"type": "number", "description": "target position 0..1"})),
    "device.screen.record": CapDef("action", Risk.external, "Record screen", "Record the screen for a while.",
                                   _obj(duration_ms=_int("recording length (ms)", default=10000)),
                                   timeout_s=60, max_timeout_s=600),
    "device.stop": CapDef("action", Risk.write, "Emergency stop",
                          "Stop whatever the phone is executing for the agent (always allowed).", _obj()),
    "phone.notification.post": CapDef("action", Risk.external, "Notify on phone",
                                      "Show a native notification on the owner's phone.",
                                      _obj(["title"], title=_str("notification title"), body=_str("body text"))),
    "phone.call.ring": CapDef("action", Risk.external, "Ring phone", "Ring the phone to get the owner's attention.",
                              _obj(reason=_str("why"))),
    "phone.device.safe_action": CapDef("action", Risk.external, "Safe phone action",
                                       "Run a whitelisted phone action (e.g. toggle flashlight, do-not-disturb).",
                                       _obj(["action"], action=_str("action name"))),
    "glasses.card.show": CapDef("action", Risk.external, "Show glasses card", "Show a card on paired glasses.",
                                _obj(["title"], card_id=_str("card id"), title=_str("title"), body=_str("body"))),
    "glasses.card.update": CapDef("action", Risk.external, "Update glasses card", "Update a glasses card.",
                                  _obj(["card_id"], card_id=_str("card id"), title=_str("title"), body=_str("body"))),
    "glasses.card.dismiss": CapDef("action", Risk.external, "Dismiss glasses card", "Dismiss a glasses card.",
                                   _obj(["card_id"], card_id=_str("card id"))),
    # -- root / dangerous ---------------------------------------------------------------------------
    "device.shell": CapDef("action", Risk.dangerous, "Root shell", "Run a shell command on the (root) phone.",
                           _obj(["command"], command=_str("shell command"),
                                timeout_ms=_int("execution limit (ms)", default=30000)),
                           timeout_s=30, max_timeout_s=600),
    "device.file.read": CapDef("action", Risk.dangerous, "Read file", "Read a file on the phone.",
                               _obj(["path"], path=_str("absolute path on the phone")), timeout_s=30),
    "device.file.write": CapDef("action", Risk.dangerous, "Write file", "Write a text file on the phone.",
                                _obj(["path", "content"], path=_str("absolute path"), content=_str("content")),
                                timeout_s=30),
    "device.file.list": CapDef("action", Risk.dangerous, "List files", "List a directory on the phone.",
                               _obj(["path"], path=_str("directory path")), timeout_s=30),
    "device.logcat": CapDef("action", Risk.dangerous, "Logcat", "Read recent logcat lines.",
                            _obj(lines=_int("how many lines", default=200), filter=_str("tag or text filter")),
                            timeout_s=30),
    "device.app.install": CapDef("action", Risk.dangerous, "Install app", "Install an APK on the phone.",
                                 _obj(["path"], path=_str("APK path on the phone")), timeout_s=120,
                                 max_timeout_s=600),
    "device.app.uninstall": CapDef("action", Risk.dangerous, "Uninstall app", "Uninstall an app by package.",
                                   _obj(["package"], package=_str("Android package")), timeout_s=60),
}
# Not bridged (no request/response semantics for an agent tool): screen.live.subscribe, phone.call.audio.


def check_arguments(capability: str, args: dict[str, Any]) -> dict[str, Any]:
    """Cross-field rules the flat JSON schema cannot express (§8 element binding)."""
    out = {k: v for k, v in args.items() if v is not None}
    if capability in ("device.tap", "device.long_press"):
        if "element_id" in out:
            if not out.get("observation_id"):
                raise WhaleError("invalid_params", "element_id requires the observation_id that produced it")
            out.pop("x", None)
            out.pop("y", None)
        elif "x" not in out or "y" not in out:
            raise WhaleError("invalid_params", "give either {x, y} or {element_id, observation_id}")
    if capability == "device.input_text" and "element_id" in out and not out.get("observation_id"):
        raise WhaleError("invalid_params", "element_id requires the observation_id that produced it")
    if capability == "device.media.seek_to" and "position_ms" not in out and "ratio" not in out:
        raise WhaleError("invalid_params", "give position_ms or ratio")
    if len(out) > 32:
        raise WhaleError("invalid_params", "too many arguments")
    return out


def action_result(view: dict[str, Any]) -> ToolResult:
    status = view.get("status")
    summary = str(view.get("summary") or "")
    if status == "succeeded":
        return ToolResult("ok", data=view, summary=summary[:200] or "done")
    detail = ": ".join(x for x in (str(status), str(view.get("error_code") or ""), summary) if x)
    if status in ("failed", "cancelled"):
        return ToolResult("error", data=view, error=detail)
    # timeout / unknown / anything unexpected: the effect may have happened.
    return ToolResult("unknown", data=view, error=detail + " (the action may or may not have happened; "
                                                          "observe before retrying)")


def query_result(result: dict[str, Any]) -> ToolResult:
    status = result.get("status")
    data = {"items": result.get("data") or [], "truncated": bool(result.get("truncated")),
            "source": result.get("source")}
    if status == "ok":
        return ToolResult("ok", data=data, summary=str(len(data["items"])) + " item(s)")
    return ToolResult("error", data=data, error="device answered " + str(status))
