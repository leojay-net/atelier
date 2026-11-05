"""The cast of an animated series: characters and places, each with a design picture.

The design pictures are the references every animated scene is made from, which is what keeps
a character looking the same from scene to scene and from one part of the series to the next.
The agent sees the cast through this protocol; the application layer stores it (per user).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

Kind = Literal["character", "place"]


@dataclass(frozen=True)
class CastMember:
    name: str
    kind: Kind
    look: str  # what it looks like (the design prompt)
    voice: str  # characters: how they sound, as a voice description; "" for places
    handle: str  # the design picture


class Cast(Protocol):
    def members(self) -> list[CastMember]: ...

    def save(self, member: CastMember, filename: str) -> None:
        """Add a cast member, or replace the one with the same name (a redesign)."""
        ...


def design_prompt(kind: str, look: str) -> str:
    """What the picture service is asked for: a clean character sheet, or an empty place."""
    if kind == "place":
        return f"{look.strip()} Wide establishing shot of the place, no people, no characters."
    return (
        f"Character design: {look.strip()} Full body, standing, front view, friendly pose, "
        "plain light grey background, nothing else in the picture."
    )


def describe(members: list[CastMember]) -> str:
    """The cast as text for the request."""
    if not members:
        return ""
    lines = ["Your cast (animate uses these by name; their design pictures keep them consistent):"]
    for m in members:
        voice = f"; voice: {m.voice}" if m.voice else ""
        lines.append(f"- {m.name} ({m.kind}): {m.look[:160]}{voice}")
    return "\n".join(lines)
