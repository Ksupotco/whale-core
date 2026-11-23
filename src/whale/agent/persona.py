"""Personas: who the agent is. Cards are data; nothing is hard-coded in prompts.

Imports SillyTavern / Character Card V2 JSON so existing cards keep working.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from ..kernel.db import Database, dumps, loads, migration
from ..kernel.errors import WhaleError, not_found
from ..kernel.events import EventBus
from ..kernel.ids import now_ms

migration(
    "personas.001",
    """CREATE TABLE IF NOT EXISTS personas (id TEXT PRIMARY KEY, card TEXT NOT NULL,
       created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);""",
)


class PersonaCard(BaseModel):
    id: str
    name: str
    tagline: str = ""
    description: str = ""          # who they are
    personality: str = ""          # traits
    style: str = ""                # how they talk
    scenario: str = ""             # relationship / setting
    greeting: str = ""
    examples: str = ""             # example dialogue
    boundaries: str = ""           # user-defined taboos
    avatar: str = ""               # emoji or data URL
    color: str = "#3b82f6"
    builtin: bool = False
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("avatar")
    @classmethod
    def _avatar_size(cls, v: str) -> str:
        if len(v) > 700_000:  # ≈ 512 KB of image data as a data: URL
            raise ValueError("avatar image is too large (max ~512 KB)")
        return v

    def system_block(self) -> str:
        parts = [f"你是「{self.name}」。"]
        for label, value in (("身份", self.description), ("性格", self.personality), ("说话方式", self.style),
                             ("关系与情境", self.scenario), ("示例对话", self.examples),
                             ("边界", self.boundaries)):
            if value.strip():
                parts.append(f"### {label}\n{value.strip()}")
        return "\n\n".join(parts)


DEFAULT = PersonaCard(
    id="default", name="鲸鱼", tagline="长期陪伴你的个人智能体", avatar="🐋", builtin=True,
    description="一个有长期记忆、会主动关心用户、也能动手做事的个人 AI。",
    personality="温和、好奇、可靠；有自己的观点，但尊重用户的决定。",
    style="自然口语，简洁，不说套话；需要时条理清晰。不知道就说不知道。",
)


def from_card_v2(data: dict[str, Any], persona_id: str) -> PersonaCard:
    d = data.get("data", data)
    name = str(d.get("name") or "").strip()
    if not name:
        raise WhaleError("invalid_params", "persona card has no name")
    return PersonaCard(
        id=persona_id, name=name, description=str(d.get("description") or ""),
        personality=str(d.get("personality") or ""), scenario=str(d.get("scenario") or ""),
        greeting=str(d.get("first_mes") or d.get("greeting") or ""),
        examples=str(d.get("mes_example") or d.get("examples") or ""),
        style=str(d.get("style") or d.get("system_prompt") or ""), tagline=str(d.get("creator_notes") or "")[:80],
        avatar=str(d.get("avatar") or ""),
    )


class Personas:
    def __init__(self, db: Database, bus: EventBus) -> None:
        self.db = db
        self.bus = bus

    def list(self) -> list[PersonaCard]:
        rows = self.db.query("SELECT card FROM personas ORDER BY created_at")
        cards = [PersonaCard.model_validate(loads(r[0])) for r in rows]
        if not any(c.id == DEFAULT.id for c in cards):
            cards.insert(0, DEFAULT)
        return cards

    def get(self, persona_id: str) -> PersonaCard:
        r = self.db.one("SELECT card FROM personas WHERE id=?", (persona_id,))
        if r:
            return PersonaCard.model_validate(loads(r[0]))
        if persona_id == DEFAULT.id:
            return DEFAULT
        raise not_found("persona", persona_id)

    def resolve(self, persona_id: str | None) -> PersonaCard:
        try:
            return self.get(persona_id or DEFAULT.id)
        except WhaleError:
            return DEFAULT

    async def save(self, card: PersonaCard) -> PersonaCard:
        now = now_ms()
        self.db.execute(
            "INSERT INTO personas(id, card, created_at, updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET card=excluded.card, updated_at=excluded.updated_at",
            (card.id, dumps(card.model_dump()), now, now))
        await self.bus.emit("persona.saved", {"id": card.id, "name": card.name})
        return card

    async def delete(self, persona_id: str) -> None:
        if persona_id == DEFAULT.id:
            raise WhaleError("forbidden", "the default persona cannot be deleted")
        self.db.execute("DELETE FROM personas WHERE id=?", (persona_id,))
        await self.bus.emit("persona.deleted", {"id": persona_id})
