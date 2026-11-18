"""S5 关系图：_graph 端点（画布节点/边/解释信息的单一事实源）。"""

from __future__ import annotations

from whale_core.api.studio.conversations import (
    _conversation_entry,
    _conversation_keys,
)
from whale_core.api.studio.memory import _space_view
from whale_core.api.studio.personas import _find_persona, _persona_view
from whale_core.api.studio.services import StudioServices, management_context
from whale_core.api.studio.shared import _now_iso, _ok
from whale_core.life.context_resolver import GROUP_CEILING
from whale_core.life.conversation_context import ConversationKind, MemorySpaceType
from whale_core.ui.persona_cards import CARD_KEY_PREFIX


def _graph(handler, ctx, svc: StudioServices) -> None:
    nodes: list[dict] = []
    edges: list[dict] = []
    explanations: dict[str, dict] = {}

    # 管理通道自身的解释信息（owner ResolvedContext，见包 docstring）。
    mgmt = management_context()
    explanations["management_channel"] = {
        "code": "owner_channel",
        "message": "本 API 以 Bearer token 作为主人管理通道；"
                   f"管理主体能力：{', '.join(mgmt.capability_scopes)}。",
    }

    conv_keys = _conversation_keys(ctx)
    for key in conv_keys:
        entry = _conversation_entry(ctx, svc, key)
        node_id = f"conv:{key}"
        if entry.get("malformed"):
            continue
        nodes.append({
            "id": node_id, "kind": "conversation", "key": key,
            "label": f"{entry['channel']}:{entry['conversation_id']}",
            "channel": entry["channel"],
            "conversation_kind": entry["kind"],
            "capabilities": entry["capabilities"],
            "ceiling": entry["ceiling"],
        })
        if entry["kind"] == ConversationKind.QQ_GROUP.value:
            explanations[node_id] = {
                "code": "group_ceiling",
                "message": "群会话能力上限 "
                           f"{', '.join(GROUP_CEILING)}；主人在群内不提权（INV-03）。",
            }
        if entry.get("persona_id"):
            pid = entry["persona_id"]
            edges.append({
                "id": f"edge:persona:{key}", "kind": "persona",
                "from": node_id, "to": f"persona:{pid}",
                "version": entry["persona_version"],
                "dangling": _find_persona(ctx, svc, pid) is None,
            })
        for mb in entry["memory_spaces"]:
            sid = mb["space_id"]
            if mb.get("restricted"):
                continue  # vault 污染数据不进图（INV-01：图上无边）
            if mb.get("missing"):
                edges.append({
                    "id": f"edge:mem:{key}:{sid}", "kind": "memory",
                    "from": node_id, "to": f"space:{sid}", "missing": True,
                })
                continue
            edges.append({
                "id": f"edge:mem:{key}:{sid}", "kind": "memory",
                "from": node_id, "to": f"space:{sid}",
                "can_read": mb["can_read"], "can_write": mb["can_write"],
                "granted_by": mb["granted_by"], "created_at": mb["created_at"],
            })

    for view in (_persona_view(svc.personas.require(cid), builtin=True)
                 for cid in svc.personas.ids()):
        nodes.append({"id": f"persona:{view['id']}", "kind": "persona",
                      "label": view["title"] or view["name"], "builtin": True})
    for key in sorted(ctx.store.keys()):
        if not key.startswith(CARD_KEY_PREFIX):
            continue
        cid = key[len(CARD_KEY_PREFIX):]
        card = _find_persona(ctx, svc, cid)
        if card is not None and svc.personas.get(cid) is None:
            nodes.append({"id": f"persona:{cid}", "kind": "persona",
                          "label": card.title or card.name, "builtin": False})

    # issue #11：设备节点——事实源 = DeviceRegistry（非客户端自报）；
    # 只展示 标签/角色/状态/在线/能力/排队水位，绝不回显 token/公钥/配对码。
    # #60：在线状态取自已装配网关（未装配时 online=None=未知，不假装离线）。
    from whale_core.devices.registry import DeviceRegistry
    _registry = DeviceRegistry(ctx.store)
    _gateway = getattr(ctx, "device_gateway", None)
    for meta in _registry.list_devices():
        did = str(meta.get("device_id") or "")
        if not did:
            continue
        queued = 0
        for k in ctx.store.keys():
            if not k.startswith(f"device:event:{did}:"):
                continue
            evid = k.rsplit(":", 1)[-1]
            if not ctx.store.get(f"device:processed:{did}:{evid}"):
                queued += 1
        node = {
            "id": f"device:{did}", "kind": "device",
            "label": str(meta.get("label") or did),
            "status": str(meta.get("status") or "unknown"),
            "capabilities": list(meta.get("scopes") or []),
            "queued_events": queued,
            "created_at": meta.get("created_at"),
        }
        if meta.get("role"):
            node["role"] = str(meta["role"])
        if _gateway is not None:
            node["online"] = bool(_gateway.is_online(did))
        if meta.get("status") == "paused":
            explanations[f"device:{did}"] = {
                "code": "device_paused",
                "message": "设备已暂停：注册与连接保留，全部动作/查询被门控拒绝；"
                           "恢复请用 resume（吊销才需重新配对）。",
            }
        elif meta.get("status") == "revoked":
            explanations[f"device:{did}"] = {
                "code": "device_revoked",
                "message": "设备已吊销：即时生效，在线连接已踢；"
                           "恢复须重新走配对码审批仪式。",
            }
        nodes.append(node)

    for ref in svc.spaces.list_spaces(include_protected=True):
        view = _space_view(ctx, svc, ref.space_id)
        if view is None:
            continue
        node_id = f"space:{ref.space_id}"
        node = {"id": node_id, "kind": "memory_space",
                "label": view["name"], "space_type": view["space_type"],
                "version": view["version"], "bindable": view["bindable"]}
        if view.get("restricted"):
            node["restricted"] = True  # 无记录数/摘要/owner 元数据（INV-08）
            explanations[node_id] = {
                "code": "vault_not_bindable",
                "message": "个人原始库永不允许连接任何会话（MEM-HARD-01）；"
                           "请通过 Fork 向导创建受控副本后再绑定。",
            }
        else:
            node["record_count"] = view["record_count"]
            node["writable"] = view["writable"]
            if view["space_type"] == MemorySpaceType.SHAREABLE_FORK.value:
                explanations[node_id] = {
                    "code": "read_only_fork",
                    "message": "共享副本默认只读；绑定时不允许 can_write。",
                }
        nodes.append(node)

    handler._send(200, _ok({
        "nodes": nodes, "edges": edges, "explanations": explanations,
        "generated_at": _now_iso(),
    }))
