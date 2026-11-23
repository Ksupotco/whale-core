"""Memory audiences: who may see which memories, and where new memories go.

The owner (the person who deployed Whale) is the centre; everyone else is a
side channel.

    audience        reads                                    writes
    ─────────────   ──────────────────────────────────────   ─────────────────────────────
    owner           owner + shared + every group:*           owner (agent learning → shared)
    group:<id>      group:<id> + shared                      group:<id>
    <person>        <person> + shared                        <person>
    (style)         —                                        always shared

* Group memories flow one way: the owner's conversations can use what was
  learned in groups, the group AI never sees the owner's private memory.
* Private chats with other people are sealed: nothing learned there leaves
  that person's scope, except the agent's own self-summarised style
  (personality, catchphrases, tone) which joins the shared persona.
"""

from __future__ import annotations

OWNER = "owner"
SHARED = "shared"
GROUP_PREFIX = "group:"

# The agent's own learning, shared from owner-facing contexts only.
AGENT_KINDS = ("knowledge", "diary", "skill")
# The agent's self-summarised persona: shared from every context.
STYLE_KIND = "style"


def audience_of(peer: str = OWNER, scope: str | None = None) -> str:
    """The audience of a run: explicit scope (e.g. a group) or derived from the speaker."""
    if scope:
        return scope
    return OWNER if peer in ("", OWNER) else peer


def is_group(audience: str) -> bool:
    return audience.startswith(GROUP_PREFIX)


def can_read(audience: str | None, item_scope: str) -> bool:
    if audience is None:  # management views see everything
        return True
    if item_scope in (audience, SHARED):
        return True
    return audience == OWNER and item_scope.startswith(GROUP_PREFIX)


def write_scope(audience: str, kind: str) -> str:
    if kind == STYLE_KIND:
        return SHARED
    if audience == OWNER and kind in AGENT_KINDS:
        return SHARED
    return audience


def sql_filter(audience: str | None, column: str = "scope") -> tuple[str, list[str]]:
    """SQL predicate + args implementing ``can_read`` (pushed into queries so other scopes never crowd results)."""
    if audience is None:
        return "1=1", []
    if audience == OWNER:
        return f"({column} IN (?, ?) OR {column} LIKE ?)", [OWNER, SHARED, GROUP_PREFIX + "%"]
    return f"{column} IN (?, ?)", [audience, SHARED]


def label(scope: str) -> str:
    if scope == OWNER:
        return "主人"
    if scope == SHARED:
        return "共享"
    if scope.startswith(GROUP_PREFIX):
        return "群聊 " + scope[len(GROUP_PREFIX):]
    return "私聊 " + scope
