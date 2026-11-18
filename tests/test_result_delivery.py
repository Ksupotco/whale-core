"""ResultDelivery 路由：QQ 在线走 WeQ；离线走手机 stub。"""
from whale_core.life.result_delivery import (
    DeliveryItem, ResultDelivery, summarize_receipts)


class FakePort:
    def __init__(self):
        self.sent = []
        self.last_receipt = {"ok": True, "privateSequence": 11}

    def send_c2c(self, uid, text):
        self.sent.append(("c2c", uid, text))
        return True

    def send_image_to(self, peer_type, target, data, *, filename="result.png"):
        self.sent.append(("image", peer_type, target, len(data), filename))
        return True

    def send_group_text(self, group, text):
        self.sent.append(("group", group, text))
        return True


def test_online_prefers_owner_dm():
    port = FakePort()
    d = ResultDelivery(port, owner_uid="u_owner", online_probe=lambda: True)
    receipts = d.deliver([
        DeliveryItem(kind="text", text="摘要"),
        DeliveryItem(kind="image", image_bytes=b"PNG", filename="a.png"),
    ])
    assert all(r.ok for r in receipts)
    assert receipts[0].channel == "qq_c2c"
    assert port.sent[0] == ("c2c", "u_owner", "摘要")
    assert port.sent[1][0] == "image"
    assert summarize_receipts(receipts)["ok"] is True


def test_offline_uses_phone_stub(tmp_path, monkeypatch):
    port = FakePort()
    monkeypatch.setenv("WHALE_PHONE_GALLERY_STAGING", str(tmp_path))
    d = ResultDelivery(port, owner_uid="u_owner", online_probe=lambda: False)
    receipts = d.deliver([
        DeliveryItem(kind="image", image_bytes=b"\x89PNG", filename="x.png"),
    ], summary="offline-demo")
    assert len(receipts) == 1
    assert receipts[0].ok is True
    assert receipts[0].qq_online is False
    assert "stub:" in receipts[0].detail or receipts[0].channel in (
        "stub_log", "phone_gallery")
    assert list(tmp_path.iterdir()), "staging file expected"
    assert port.sent == []


def test_offline_text_only_logged():
    port = FakePort()
    d = ResultDelivery(port, owner_uid="u_owner", online_probe=lambda: False)
    receipts = d.deliver([DeliveryItem(kind="text", text="仅文字")])
    assert receipts[0].ok is True
    assert receipts[0].channel == "stub_log"
    assert port.sent == []



def test_offline_text_prefers_whale_notification():
    port = FakePort()
    from whale_core.life.result_delivery import DeliveryReceipt

    pushed = []
    def whale_push(items, conversation, summary):
        pushed.append((items, conversation, summary))
        return [DeliveryReceipt(
            channel="whale_notification", ok=True,
            detail="queued_for_device_sync", qq_online=False)]

    d = ResultDelivery(port, owner_uid="u_owner",
                       online_probe=lambda: False, whale_push=whale_push)
    receipts = d.deliver(
        [DeliveryItem(kind="text", text="B站视频《FPGA 入门》")],
        conversation="glasses:s1", summary="exploration:BV1")
    assert len(receipts) == 1
    assert receipts[0].channel == "whale_notification"
    assert receipts[0].ok is True
    assert pushed and pushed[0][1] == "glasses:s1"
    assert port.sent == []


def test_offline_whale_failure_falls_back_to_stub_log():
    port = FakePort()
    from whale_core.life.result_delivery import DeliveryReceipt

    d = ResultDelivery(
        port, owner_uid="u_owner", online_probe=lambda: False,
        whale_push=lambda items, conversation, summary: [
            DeliveryReceipt(channel="whale_notification", ok=False,
                            detail="gateway_unavailable", qq_online=False)])
    receipts = d.deliver([DeliveryItem(kind="text", text="内容")])
    assert any(r.channel == "whale_notification" and not r.ok for r in receipts)
    assert any(r.channel == "stub_log" for r in receipts)


# ---------- 探测口径（2026-11-18 对齐 weq-web 实际可发能力） ----------

from whale_core.life.result_delivery import probe_weq_qq_online


class CapabilityPort(FakePort):
    """模拟 weq-web 两态的桩：_get 回 getGroupAlbumAccessState 形状。"""

    def __init__(self, payload, status=200):
        super().__init__()
        self._payload = payload
        self._status = status
        self._cookie = "s"  # 跳过 login

    def _get(self, path, *, auth=True):
        assert path == "/trpc/account.getGroupAlbumAccessState"
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._status, {"result": {"data": {"json": self._payload}}}


def test_probe_online_requires_qq_online_and_attach_enabled():
    assert probe_weq_qq_online(
        CapabilityPort({"qqOnline": True, "attachEnabled": True})) is True


def test_probe_offline_mode_means_not_online():
    """完全离线模式：QQ 进程在线但 attach 关 → 判离线（走回落不误判）。"""
    assert probe_weq_qq_online(
        CapabilityPort({"qqOnline": True, "attachEnabled": False})) is False
    assert probe_weq_qq_online(
        CapabilityPort({"qqOnline": False, "attachEnabled": True})) is False


def test_probe_fail_closed_on_error_or_unknown_shape():
    assert probe_weq_qq_online(
        CapabilityPort(ConnectionError("down"))) is False          # 探测失败
    assert probe_weq_qq_online(
        CapabilityPort({"online": True})) is False                 # 旧形状不再认
    assert probe_weq_qq_online(
        CapabilityPort({}, status=404)) is False                   # 端点缺失
    assert probe_weq_qq_online(
        CapabilityPort({"qqOnline": True})) is False               # 缺 attachEnabled


def test_deliver_online_prefers_qq_via_real_probe():
    port = CapabilityPort({"qqOnline": True, "attachEnabled": True})
    d = ResultDelivery(port, owner_uid="u_owner")  # 默认走真实探针
    receipts = d.deliver([DeliveryItem(kind="text", text="在线")])
    assert receipts[0].channel == "qq_c2c" and receipts[0].ok
    assert port.sent == [("c2c", "u_owner", "在线")]


def test_deliver_offline_mode_does_not_pick_qq():
    port = CapabilityPort({"qqOnline": True, "attachEnabled": False})
    d = ResultDelivery(port, owner_uid="u_owner")
    receipts = d.deliver([DeliveryItem(kind="text", text="离线")])
    assert all(r.channel != "qq_c2c" for r in receipts)
    assert port.sent == []  # 不选 QQ、走回落（stub_log/通知）
