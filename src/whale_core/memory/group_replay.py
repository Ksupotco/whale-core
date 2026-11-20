"""Write group portraits and explicit preferences using the normal memory governance."""
from collections import Counter
from datetime import datetime, timezone, timedelta
from whale_core.memory.group_portraits import extract_profiles
from whale_core.memory.group_preferences import saved_profile
from whale_core.memory.group_directory import atomic_write, portrait_path, read_portrait
from whale_core.memory.bindings import BindingStore
from whale_core.memory.consent import MemberConsentStore
from whale_core.memory.extraction import BACKFILL_KEY_PREFIX, PREFERENCE_TTL_S
from whale_core.memory.facts import FactService
from whale_core.memory.group_preferences import SOURCE, preference_clauses, save_observations
from whale_core.memory.pause import is_recording_paused
from whale_core.memory.spaces import SpaceStore, ensure_owner_working_copy
from whale_core.memory.store import MemoryKind
from whale_core.memory.types import MemorySpaceType
from whale_core.memory.write_decision import apply_decision, decide_candidate


def replay(store, gid, owner_account, inputs, *, now=None, pending_ids=None, portrait_archive=None):
    roster, rows, index, expected = inputs
    current = now or datetime.now(timezone.utc)
    group_space = "group-" + gid
    spaces = SpaceStore(store)
    ref = spaces.ref(group_space)
    if not ref or ref.type != MemorySpaceType.GROUP_SPACE:
        raise ValueError("请先导入该群空间")
    members = {a:m for m in roster["members"] for a in m["accounts"]}
    if owner_account not in members:
        raise ValueError("指定的主人账号不在该群名单中")
    if is_recording_paused(store, group_space):
        return {"group_paused": True}
    bindings = BindingStore(store, spaces)
    consents = MemberConsentStore(store)
    eligible = {}
    for m in roster["members"]:
        principals = ["anon:qq:" + a for a in m["accounts"]]
        if any(consents.is_opted_out(p) for p in principals):
            continue
        bound = [bindings.member_binding_for(p) for p in principals]
        if (owner_account in m["accounts"] and is_recording_paused(store,"owner-working")
                or any(b and is_recording_paused(store,b["space_id"]) for b in bound)):
            continue
        cutoffs = [(store.get(BACKFILL_KEY_PREFIX + p) or {}).get("not_before") for p in principals]
        start = max((datetime.fromisoformat(t) for t in cutoffs if t), default=None)
        eligible.update((a,start) for a in m["accounts"])
    speech = {r["message_id"]:r for r in rows if r["sender_id"] in eligible
              and (eligible[r["sender_id"]] is None or datetime.fromisoformat(r["sent_at"]) >= eligible[r["sender_id"]])}
    counts = Counter(scanned=len(speech))
    profiles = {}
    for member, profile in extract_profiles(gid, roster, list(speech.values()), index, expected):
        if member["accounts"][0] not in eligible or not profile["text_count"]:
            continue
        stamp = max(r["sent_at"] for r in speech.values() if r["sender_id"] in member["accounts"])
        subject = "owner" if owner_account in member["accounts"] else "anon:qq:" + member["accounts"][0]
        action = save_observations(store, gid, member, profile, speech, stamp, subject)
        counts["profile_" + action] += 1
        if portrait_archive and saved_profile(store, gid, member["accounts"][0]):
            path = portrait_path(portrait_archive, gid, member)
            if read_portrait(portrait_archive, gid, member) != profile:
                atomic_write(path, profile)
        for kind in ("topics","scenes","expressions"):
            counts[kind] += len(profile[kind])
        profiles[member["accounts"][0]] = member
    facts = FactService(store)
    for r in speech.values():
        if pending_ids is not None and r["message_id"] not in pending_ids:
            continue
        member = members[r["sender_id"]]
        primary = member["accounts"][0]
        if primary not in profiles:
            continue
        subject = "owner" if owner_account in member["accounts"] else "anon:qq:" + primary
        stamp = datetime.fromisoformat(r["sent_at"])
        expiry = stamp + timedelta(seconds=PREFERENCE_TTL_S)
        for quote, slot in preference_clauses(r["text"]):
            counts["preference_candidates"] += 1
            if expiry <= current:
                counts["expired"] += 1
                continue
            sid = (ensure_owner_working_copy(store) if subject == "owner" else
                   bindings.ensure_member_space(subject, conversation_key=f"qq:qq_group:group:{gid}",
                       granted_by="owner:qce-import",allow_group_evidence=True)).space_id
            eid = f"qq:group:{gid}:{r['message_id']}"
            if facts.decision_for(summary=quote,evidence_id=eid,space_id=sid,subject_id=subject):
                counts["already_decided"] += 1
                continue
            # A historical replay cannot supersede a later version from normal chat.
            related = (v.content for v in store.list_by_kind(MemoryKind.PREFERENCE,space_ids=(sid,))
                       if v.content.get("subject_id")==subject and v.content.get("slot_key")==slot)
            if any(c.get("valid_from") and datetime.fromisoformat(c["valid_from"]) > stamp for c in related):
                counts["later_version"] += 1
                continue
            decision = decide_candidate(store,None,summary=quote,source_text=r["text"],space_id=sid,
                                        subject_id=subject,slot_key=slot,expires_at=expiry.isoformat())
            result = apply_decision(store,decision,summary=quote,evidence_id=eid,space_id=sid,
                                    subject_id=subject,slot_key=slot,expires_at=expiry.isoformat(),
                                    observed_at=stamp,source=SOURCE)
            counts["fact_" + result.action] += 1
    return dict(counts)
