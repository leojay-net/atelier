"""Request bodies. Responses are plain dictionaries built in `services`, so the wire format is
in one place and does not depend on the ORM."""

from __future__ import annotations

from typing import Any

from ninja import Schema
from pydantic import Field


class SpecIn(Schema):
    spec: dict[str, Any]


class JobIn(Schema):
    """Exactly one of `spec` (engine only, zero tokens), `prompt` (the agent) or `recipe_id`."""

    spec: dict[str, Any] | None = None
    prompt: str | None = Field(None, max_length=4000)
    assets: list[str] = Field(default_factory=list, max_length=20)  # what a prompt is about
    recipe_id: int | None = None
    inputs: dict[str, str] = Field(default_factory=dict)  # recipe: input name → asset handle


class RecipeIn(Schema):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,58}$")
    description: str = Field("", max_length=200)
    spec: dict[str, Any]


class SaveRecipeIn(Schema):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,58}$")
    description: str = Field("", max_length=200)


class BrandKitIn(Schema):
    name: str = Field("", max_length=80)
    tagline: str = Field("", max_length=200)
    colors: dict[str, str] = Field(default_factory=dict, max_length=12)
    fonts: dict[str, str] = Field(default_factory=dict, max_length=6)
    logo: str | None = None
    logo_dark: str | None = None
