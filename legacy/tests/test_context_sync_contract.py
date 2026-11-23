"""/v1/context/sync 契约校验测试（与应用开发联签 2026-11-17）。

错误码枚举：invalid_json | unauthorized | invalid_date | too_many_entries(>12/天)
| title_too_long(>120) | payload_too_large(>64KB)。
成功响应：200 {"ok", "stored", "receivedAt", "dates", "idempotent"}。
窗口 [today, today+7]（Asia/Shanghai），写入即清理窗口外日期键；
同快照哈希幂等重放不更新 receivedAt。

全部离线：纯函数层传显式 now= 固定时钟；HTTP 层本地 ThreadingHTTPServer +
真实 sqlite 临时库，日期用服务端相对值避免真实时钟滚窗。
"""
from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from whale_core.api.glasses import make_handler
from whale_core.api.glasses_context import (
    ContextSyncError, SHANGHAI, SNAPSHOT_KEY, save_context_snapshot)
from whale_core.memory.sqlite_store import SqliteMemoryStore

NOW = datetime(2026, 11, 17, 10, 0, tzinfo=SHANGHAI)
TODAY = "2026-11-17"


def _day(offset: int) -> str:
    return (NOW + timedelta(days=offset)).date().isoformat()


def _event(title="组会", **kw):
    return {"title": title, "start": "09:00", "end": "10:00",
            "allDay": False, **kw}


@pytest.fixture()
def store(tmp_path):
    store = SqliteMemoryStore(str(tmp_path / "ctx.sqlite3"))
    yield store
    store.close()


def _code(excinfo) -> str:
    assert isinstance(excinfo.value, ContextSyncError)
    return excinfo.value.code


# ---------- 错误码：invalid_json ----------

def test_calendar_missing_or_not_dict(store):
    with pytest.raises(ValueError) as e1:
        save_context_snapshot(store, {}, now=NOW)
    with pytest.raises(ValueError) as e2:
        save_context_snapshot(store, {"calendar": [{"x": 1}]}, now=NOW)
    with pytest.raises(ValueError) as e3:
        save_context_snapshot(store, {"calendar": "2026-11-17"}, now=NOW)
    assert _code(e1) == _code(e2) == _code(e3) == "invalid_json"


def test_events_must_be_array_or_null(store):
    with pytest.raises(ValueError) as exc:
        save_context_snapshot(store, {"calendar": {TODAY: "组会"}}, now=NOW)
    assert _code(exc) == "invalid_json"


def test_event_must_be_dict(store):
    with pytest.raises(ValueError) as exc:
        save_context_snapshot(store, {"calendar": {TODAY: ["组会"]}}, now=NOW)
    assert _code(exc) == "invalid_json"


@pytest.mark.parametrize("event", [
    {"start": "09:00"},                 # title 缺失
    {"title": ""},                      # title 空
    {"title": "   "},                   # title 空白
    {"title": 42},                      # title 类型错
    {"title": None},                    # title null
])
def test_title_required_non_empty_string(store, event):
    with pytest.raises(ValueError) as exc:
        save_context_snapshot(store, {"calendar": {TODAY: [event]}}, now=NOW)
    assert _code(exc) == "invalid_json"


def test_allday_must_be_bool(store):
    with pytest.raises(ValueError) as exc:
        save_context_snapshot(
            store, {"calendar": {TODAY: [_event(allDay="true")]}}, now=NOW)
    assert _code(exc) == "invalid_json"


# ---------- 错误码：title_too_long（边界 120/121） ----------

def test_title_120_chars_ok(store):
    snap = save_context_snapshot(
        store, {"calendar": {TODAY: [_event("长" * 120)]}}, now=NOW)
    assert snap["stored"] == 1
    assert snap["calendar"][TODAY][0]["title"] == "长" * 120


def test_title_121_chars_rejected(store):
    with pytest.raises(ValueError) as exc:
        save_context_snapshot(
            store, {"calendar": {TODAY: [_event("长" * 121)]}}, now=NOW)
    assert _code(exc) == "title_too_long"


# ---------- 错误码：invalid_date（格式 + 窗口） ----------

@pytest.mark.parametrize("day", ["11-17", "hello", "2026-13-01", "2026-11-40",
                                 "2026-11-17 ", "20261117"])
def test_malformed_date_keys(store, day):
    with pytest.raises(ValueError) as exc:
        save_context_snapshot(store, {"calendar": {day: []}}, now=NOW)
    assert _code(exc) == "invalid_date"


@pytest.mark.parametrize("offset", [-1, 8, 30])
def test_date_outside_window(store, offset):
    with pytest.raises(ValueError) as exc:
        save_context_snapshot(store, {"calendar": {_day(offset): []}}, now=NOW)
    assert _code(exc) == "invalid_date"
    assert store.get(SNAPSHOT_KEY) is None  # 整体拒绝，不落半截


@pytest.mark.parametrize("offset", [0, 1, 7])
def test_date_window_boundaries_ok(store, offset):
    snap = save_context_snapshot(store, {"calendar": {_day(offset): []}}, now=NOW)
    assert snap["calendar"] == {_day(offset): []}


# ---------- 错误码：too_many_entries（边界 12/13） ----------

def test_twelve_events_ok(store):
    snap = save_context_snapshot(
        store, {"calendar": {TODAY: [_event(f"会{i}") for i in range(12)]}},
        now=NOW)
    assert snap["stored"] == 12


def test_thirteen_events_rejected(store):
    with pytest.raises(ValueError) as exc:
        save_context_snapshot(
            store, {"calendar": {TODAY: [_event(f"会{i}") for i in range(13)]}},
            now=NOW)
    assert _code(exc) == "too_many_entries"


# ---------- [] / null 语义 ----------

def test_empty_list_and_null_semantics(store):
    snap = save_context_snapshot(
        store, {"calendar": {TODAY: [], _day(1): None}}, now=NOW)
    assert snap["calendar"][TODAY] == []
    assert snap["calendar"][_day(1)] is None
    assert snap["stored"] == 0
    assert snap["idempotent"] is False


# ---------- 幂等 ----------

def test_idempotent_replay_keeps_received_at(store):
    body = {"calendar": {TODAY: [_event("体检", allDay=True), _event("周会")],
                         _day(1): []}}
    first = save_context_snapshot(store, body, now=NOW)
    assert first["idempotent"] is False and first["stored"] == 2
    replay = save_context_snapshot(store, body, now=NOW + timedelta(seconds=90))
    assert replay["idempotent"] is True
    assert replay["stored"] == 2
    assert replay["receivedAt"] == first["receivedAt"]  # 不更新
    assert store.get(SNAPSHOT_KEY)["receivedAt"] == first["receivedAt"]


def test_key_order_insensitive_idempotency(store):
    a = save_context_snapshot(store, {"calendar": {
        TODAY: [_event("周会")], _day(1): []}}, now=NOW)
    b = save_context_snapshot(store, {"calendar": {
        _day(1): [], TODAY: [_event("周会")]}}, now=NOW + timedelta(seconds=5))
    assert b["idempotent"] is True and b["receivedAt"] == a["receivedAt"]


def test_changed_payload_replaces_and_updates_received_at(store):
    save_context_snapshot(store, {"calendar": {TODAY: [_event("周会")]}}, now=NOW)
    later = save_context_snapshot(
        store, {"calendar": {TODAY: [_event("周会"), _event("加班")]}},
        now=NOW + timedelta(seconds=5))
    assert later["idempotent"] is False and later["stored"] == 2
    assert later["receivedAt"] != NOW.isoformat()


# ---------- 窗口清理：写入即替换，旧窗口日期不留 ----------

def test_write_replaces_and_drops_rolled_out_dates(store):
    save_context_snapshot(store, {"calendar": {_day(0): [_event("旧会")],
                                               _day(1): []}}, now=NOW)
    rolled = NOW + timedelta(days=8)
    new_day = (rolled).date().isoformat()
    snap = save_context_snapshot(store, {"calendar": {new_day: []}}, now=rolled)
    assert list(snap["calendar"].keys()) == [new_day]
    stored = store.get(SNAPSHOT_KEY)
    assert _day(0) not in stored["calendar"] and _day(1) not in stored["calendar"]


# ---------- HTTP 层契约 ----------

@contextmanager
def server(tmp_path):
    store = SqliteMemoryStore(str(tmp_path / "http.sqlite3"))
    delegates = {"sync_context": lambda body: save_context_snapshot(store, body)}
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(delegates))
    srv.glasses_token = "test-phone"
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()

    def post(body, token="test-phone", raw=None):
        data = raw if raw is not None else json.dumps(body).encode()
        req = Request(f"http://127.0.0.1:{srv.server_port}/v1/context/sync",
                      data=data, headers={"Authorization": "Bearer " + token,
                                          "Content-Type": "application/json"})
        try:
            with urlopen(req, timeout=5) as response:
                return response.status, json.load(response)
        except HTTPError as response:
            return response.code, json.load(response)
    try:
        yield post, store
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(5)
        store.close()


def _server_day(offset: int) -> str:
    return (datetime.now(SHANGHAI) + timedelta(days=offset)).date().isoformat()


def test_http_unauthorized(tmp_path):
    with server(tmp_path) as (post, _):
        status, body = post({"calendar": {}}, token="wrong")
        assert status == 401 and body["error"] == "unauthorized"


def test_http_error_codes(tmp_path):
    today = _server_day(0)
    with server(tmp_path) as (post, _):
        assert post({})[1] == {"error": "invalid_json"}
        assert post({"calendar": []})[1] == {"error": "invalid_json"}
        assert post({"calendar": {"bad": []}})[1] == {"error": "invalid_date"}
        assert post({"calendar": {_server_day(9): []}})[1] == {
            "error": "invalid_date"}
        assert post({"calendar": {today: [_event(f"会{i}")
                                          for i in range(13)]}})[1] == {
            "error": "too_many_entries"}
        assert post({"calendar": {today: [_event("长" * 121)]}})[1] == {
            "error": "title_too_long"}
        assert post({"calendar": {today: [{"start": "09:00"}]}})[1] == {
            "error": "invalid_json"}
        # 非 JSON 体 → invalid_json
        assert post(None, raw=b"not-json")[1] == {"error": "invalid_json"}


def test_http_payload_too_large(tmp_path):
    with server(tmp_path) as (post, _):
        big = {"calendar": {}, "pad": "x" * (70 * 1024)}
        status, body = post(big)
        assert status == 413 and body == {"error": "payload_too_large"}


def test_http_success_shape_and_idempotent_replay(tmp_path):
    today, tomorrow = _server_day(0), _server_day(1)
    body = {"source": "android.calendar",
            "calendar": {today: [_event("周会")], tomorrow: []}}
    with server(tmp_path) as (post, _):
        status, first = post(body)
        assert status == 200
        assert first["ok"] is True and first["idempotent"] is False
        assert first["stored"] == 1 and first["dates"] == [today, tomorrow]
        assert first["receivedAt"]
        status, replay = post(body)
        assert status == 200
        assert replay["idempotent"] is True
        assert replay["receivedAt"] == first["receivedAt"]
        assert replay["stored"] == 1 and replay["dates"] == [today, tomorrow]


def test_http_empty_and_null_semantics(tmp_path):
    today, tomorrow = _server_day(0), _server_day(1)
    with server(tmp_path) as (post, _):
        status, body = post({"calendar": {today: [], tomorrow: None}})
        assert status == 200 and body["stored"] == 0
        assert body["dates"] == [today, tomorrow]
