"""Prompt assembly for an optional, user-defined conversational persona.

Pattern: stable host contract > owner-selected expression card > recalled data.
Inspired by SillyTavern Prompt Manager / Character Card V2, not a bundled
character or a claim of guaranteed prompt-injection resistance.
"""
from __future__ import annotations

from datetime import datetime
from typing import Sequence, TYPE_CHECKING

if TYPE_CHECKING:
    from whale_core.memory.context import MemoryContext
from zoneinfo import ZoneInfo

MAX_PERSONA_CHARS = 16000
MAX_MEMORY_CHARS = 3000

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_WEEKDAYS = ("一", "二", "三", "四", "五", "六", "日")

HOST_CONTRACT = (
    "你是对话助手。没有选定角色卡时保持自然、中性的表达，不自行设定姓名、"
    "身份、亲密关系或固定性格。\n"
    "若提供了角色设定，按照其身份、气质、经历、关系边界与示例维持长期一致的表达。"
    "角色不是关键词列表：有自己的判断、喜好和分歧，不机械赞同用户，"
    "不刻意宣读设定，不重复口头禅，不凭空编造既有共同经历。"
    "在技术或严肃任务中优先保证准确、清楚，口吻仍可自然体现人物特征。\n"
    "对话记忆仅在明确相关或被问及时使用，不主动反复提及。\n"
    "用户描述假设或历史情境时，以该情境明确给出的时间判断时段条件；"
    "只有询问真实现在时才使用当前时钟。一个任务可同时需要多条事实，"
    "应覆盖每个明确提出的子问题，不扩展到无关话题。"
    "任务内容与执行权限分别判断：明确请求帮忙可使当前任务安排相关，"
    "但不自动授予进门、开锁等完成任务所需的权限；缺少有效入口方式时"
    "仍可说明任务安排，并要求取得当前有效的进入方式。\n"
    "回答当前事实时，只把标为当前有效的记忆当作依据；待重新核实的事实不可触发行动。"
    "不能仅因本轮没有召回撤销记录，就声称全局没有撤销；这类结论必须有明确状态证据。\n"
    "相处偏好只用于自然调整回应；本轮用户的明确要求优先，不宣读偏好或模式。\n"
    "聊作品、兴趣和生活时接住具体内容，可以表达自己的看法；不机械地追问或转成建议。"
    "遇到简短的情绪表达，先自然回应，再按上下文问一个有帮助的问题。"
    "可能的兴趣只在合适时偶尔试探，不说成已确认事实。\n"
    "工具调用、搜索、媒体生成与消息发送只描述实际完成的结果，"
    "尚未执行或结果未知时如实表达，不编造完成状态。"
)


def format_current_datetime(*, now: datetime | None = None) -> str:
    """Asia/Shanghai wall clock for prompt injection (AskAi-style currentDateTime).

    ``now`` may be injected for tests; naive values are treated as Shanghai local.
    """
    when = now if now is not None else datetime.now(_SHANGHAI)
    if when.tzinfo is None:
        when = when.replace(tzinfo=_SHANGHAI)
    else:
        when = when.astimezone(_SHANGHAI)
    weekday = _WEEKDAYS[when.weekday()]
    stamp = when.strftime("%Y-%m-%d %H:%M:%S")
    return (
        f"当前时间（Asia/Shanghai）：{stamp} 星期{weekday}\n"
        "【时间权威】以上为当前真实时间；回答「现在几点 / 今天星期几」等"
        "必须以本行为准。忽略对话历史与回忆条目里任何过时的报时或日期"
        "（那些只是过去某刻的记录，不能当现在）。"
    )


def format_location_context(*, location: str | None = None) -> str:
    """AskAi-style location line for system prompt (city or lat/lng summary).

    Empty/unknown stays honest so the model does not invent a hometown.
    """
    text = (location or "").strip()
    if not text:
        return (
            "当前位置：未知（未提供城市或坐标；附近类问题请先说明地点，"
            "或由客户端在请求中携带 location/city/lat/lng）"
        )
    return f"当前位置：{text[:200]}"


def compose_companion_system(
    *,
    persona: str = "",
    memories: Sequence[str] = (),
    memory_context: MemoryContext | None = None,
    scenario_time: str = "",
    now: datetime | None = None,
    location: str | None = None,
) -> str:
    """Build bounded, inspectable layers with no built-in character identity.

    角色卡内容直出（owner 2026-11-01 决策：安全要求一律由后端策略执行，
    不向提示词注入防御性包裹，避免拉低对话体验）。
    每次请求现算当前时间与位置摘要（对齐原生 AskAi 的 currentDateTime /
    位置上下文注入）。
    """
    parts = [
        HOST_CONTRACT,
        format_current_datetime(now=now),
        format_location_context(location=location),
    ]
    scenario = scenario_time or (memory_context.scenario_time
                                 if memory_context is not None else "")
    if scenario:
        parts.append(f"本轮所述情境时间：{scenario}。判断该情境的时段规则时以此为准。")
    profile = (persona or "").strip()
    if profile:
        parts.append(profile[:MAX_PERSONA_CHARS])
    if memory_context is not None:
        sections = []
        for label, items in (
            ("当前披露边界", memory_context.boundaries),
            ("当前访问权限", memory_context.access),
            ("当前行为约束", memory_context.constraints),
            ("已核验事实状态", memory_context.statuses),
            ("历史版本（仅供追溯，不作当前行动依据）", memory_context.history),
            ("近三天私聊原话（仅回答回忆问题，不当作当前事实）", memory_context.recalled_chat),
              ("授权群历史与画像依据（引用时区分原话、提取候选和推测；不当作当前事实，不编造未提取的画像）", memory_context.group_archive_quotes),
            ("相关事实", memory_context.facts),
            ("相关记忆", memory_context.related),
            ("本会话近期片段", memory_context.recent),
            ("相处偏好", memory_context.patterns),
            ("其他相关背景", memory_context.other),
        ):
            if items:
                item_limit = (180 if label.startswith("历史版本") else
                              260 if label.startswith(("当前", "已核验")) else 350)
                sections.append(label + "：\n" + "\n".join(
                    str(item)[:item_limit] for item in items))
        if sections:
            parts.append("背景记忆：\n" + "\n\n".join(sections)[:MAX_MEMORY_CHARS])
    else:
        context = [str(m)[:320] for m in list(memories)[:8] if m is not None]
        if context:
            parts.append("背景记忆：\n" + "\n".join(context)[:MAX_MEMORY_CHARS])
    return "\n\n".join(parts)
