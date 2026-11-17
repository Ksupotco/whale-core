"""Small background path from an owner's message to curated facts."""
from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import datetime, timedelta, timezone

from whale_core.memory.extraction import contains_sensitive
from whale_core.memory.write_decision import apply_decision, decide_candidate
from whale_core.memory.pause import is_recording_paused

JOB_PREFIX = "memory:formation:"
DONE_PREFIX = "memory:formed:"
_RUN_LOCK = threading.Lock()
_QUOTED = re.compile(r"“[^”]*”|「[^」]*」|\"[^\"]*\"")
_PRIVATE_TOPICS = (
    "分手", "恋爱", "亲密", "心理", "抑郁", "咨询", "男朋友", "女朋友",
)
_TRANSIENT = ("今天", "今晚", "最近", "这几天", "这周", "目前", "暂时")
_SELF_REPORT = (
    "喜欢", "不喜欢", "讨厌", "偏好", "习惯",
    "我是", "我养", "我在读", "我正在学", "我正在准备", "我从事",
    "我有一只", "我每天", "我每周", "我通常", "我正在做", "我计划",
)
_OWNER_CLAIM = re.compile(
    r"^我(?:(?:以后|现在)?(?:一直|很|最|还挺|比较|真的|其实|不)?"
    r"(?:喜欢|讨厌|偏好|习惯)|是|养|有一只|每天|每周|通常|在读|正在学|正在准备|正在做|计划|从事)")
_RECENT_PLAN = re.compile(r"^我最近(?:在|正在)?准备")
_ROLE_WORDS = ("学生", "研究生", "博士", "教师", "程序员", "设计师", "工程师")
_PROMPT = (
    "从用户这条原话中找最多 3 条明确、低风险的本人事实。"
    "可以是兴趣偏好、职业或学业身份、宠物、明确的日常习惯、当前计划或学习事项。"
    "只复制原话里以“我”开头的完整短片段；不要推测、改写或提取别人说的话。"
    "没有合适内容就返回空列表。仅输出 JSON："
    '{"quotes":["我喜欢科幻"]}'
)


def eligible_message(text: str) -> bool:
    """Cheap local gate before sending an automatic extraction request."""
    message = str(text or "").strip()
    if not message or len(message) > 500 or contains_sensitive(message):
        return False
    if any(word in message for word in _PRIVATE_TOPICS):
        return False
    unquoted = _QUOTED.sub("", message)
    return "我" in unquoted and (any(
        word in unquoted for word in _SELF_REPORT)
        or _RECENT_PLAN.match(unquoted) is not None)


def _quotes_from_model(reply: str, source_text: str) -> tuple[str, ...]:
    try:
        data = json.loads(str(reply).strip())
    except (TypeError, ValueError):
        return ()
    quotes = data.get("quotes") if isinstance(data, dict) else None
    if not isinstance(quotes, list):
        return ()
    unquoted_source = _QUOTED.sub("", source_text)
    accepted: list[str] = []
    for item in quotes[:3]:
        quote = str(item).strip() if isinstance(item, str) else ""
        recent_plan = _RECENT_PLAN.match(quote) is not None
        if not (5 <= len(quote) <= 80 and (_OWNER_CLAIM.match(quote) or recent_plan)
                and quote in unquoted_source
                and (any(word in quote for word in _SELF_REPORT) or recent_plan)
                and (recent_plan or not any(word in quote for word in _TRANSIENT))
                and not contains_sensitive(quote)
                and not any(word in quote for word in _PRIVATE_TOPICS)
                and not any(word in quote for word in ("你", "他", "她", "？", "?"))):
            continue
        if quote.startswith("我是") and not any(
                word in quote for word in _ROLE_WORDS):
            continue
        if quote not in accepted:
            accepted.append(quote)
    return tuple(accepted)


def _expires_at(quote: str) -> str:
    days = (30 if _RECENT_PLAN.match(quote)
            or quote.startswith(("我正在准备", "我正在做", "我计划"))
            else 180 if quote.startswith(("我在读", "我正在学"))
            else 0)
    return ((datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
            if days else "")


def _slot_for_quote(quote: str) -> str:
    """Only assign a slot when the topic can be read without guessing."""
    match = re.fullmatch(
        r"我(?:以后|现在)?(?:一直|很|最|还挺|比较|真的|其实)?(?:喜欢|不喜欢|讨厌)(.+)",
        quote)
    if match:
        topic = match.group(1).strip(" ，。！？!? ")
        return f"interest:{topic}" if topic else ""
    return ""


def enqueue(store, *, owner_id: str, space_id: str, source_id: str,
            text: str) -> str | None:
    if not owner_id or not space_id or not source_id or not eligible_message(text):
        return None
    digest = hashlib.sha256(
        f"{owner_id}\0{source_id}".encode("utf-8")).hexdigest()[:24]
    key = f"{JOB_PREFIX}{digest}"
    if store.get(f"{DONE_PREFIX}{digest}"):
        return None
    if store.get(key) is None:
        store.put(key, {
            "owner_id": owner_id, "space_id": space_id,
            "source_id": source_id, "text": text[:500],
            "attempts": 0, "status": "pending",
        })
    return key


def run_pending(store, model, *, limit: int = 20) -> dict[str, int]:
    """Process durable candidates; failed model calls can retry later."""
    report = {"written": 0, "skipped": 0, "failed": 0}
    with _RUN_LOCK:
        keys = [key for key in store.keys() if key.startswith(JOB_PREFIX)][:limit]
        for key in keys:
            job = store.get(key)
            if not isinstance(job, dict) or job.get("status") != "pending":
                continue
            space_id = str(job.get("space_id") or "")
            source = store.get_record(str(job.get("source_id") or ""))
            if (not source or source.space_id != space_id
                    or is_recording_paused(store, space_id)):
                store.put(f"{DONE_PREFIX}{key[len(JOB_PREFIX):]}", True)
                store.delete(key)
                report["skipped"] += 1
                continue
            try:
                answer = model.reply(
                    str(job["text"]), persona=_PROMPT, memories=(), history=())
                quotes = _quotes_from_model(answer, str(job["text"]))
                for quote in quotes:
                    expires_at = _expires_at(quote)
                    summary = f"用户自述：{quote}"
                    slot_key = _slot_for_quote(quote)
                    decision = decide_candidate(
                        store, model, summary=summary,
                        source_text=quote, space_id=space_id,
                        subject_id="owner", slot_key=slot_key,
                        expires_at=expires_at)
                    result = apply_decision(
                        store, decision, summary=summary,
                        evidence_id=str(job["source_id"]), space_id=space_id,
                        subject_id="owner", slot_key=slot_key,
                        expires_at=expires_at, observed_at=source.created_at)
                    if result.action in ("add", "update"):
                        report["written"] += 1
                    elif result.action in ("pending", "skip"):
                        report["skipped"] += 1
                store.put(f"{DONE_PREFIX}{key[len(JOB_PREFIX):]}", True)
                store.delete(key)
                if not quotes:
                    report["skipped"] += 1
            except Exception:
                job["attempts"] = int(job.get("attempts") or 0) + 1
                if job["attempts"] >= 3:
                    job["status"] = "failed"
                store.put(key, job)
                report["failed"] += 1
    return report


def cancel_pending(store, clue: str, *, allowed_spaces=None) -> int:
    """Remove queued facts matching an explicit forget request."""
    spaces = (None if allowed_spaces is None else
              {str(getattr(space, "space_id", space)) for space in allowed_spaces})
    removed = 0
    with _RUN_LOCK:
        for key in store.keys():
            if not key.startswith(JOB_PREFIX):
                continue
            job = store.get(key)
            if not isinstance(job, dict) or job.get("status") != "pending":
                continue
            if spaces is not None and job.get("space_id") not in spaces:
                continue
            if clue not in str(job.get("text") or ""):
                continue
            store.put(f"{DONE_PREFIX}{key[len(JOB_PREFIX):]}", True)
            store.delete(key)
            removed += 1
    return removed


def schedule(store, model) -> None:
    """Finish generation first; run extraction shortly afterward."""
    def work():
        try:
            run_pending(store, model)
        except Exception:
            pass

    timer = threading.Timer(0.25, work)
    timer.daemon = True
    timer.start()
