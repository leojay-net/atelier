"""The frozen system prompt: deterministic, cache-friendly, and its examples really work."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from atelier.agent.prompt import (
    DOC,
    FINAL_SCHEMA,
    LOGO,
    PHOTO,
    RULES,
    VIDEO,
    VIDEO2,
    catalogue_version,
    example_specs,
    system_blocks,
)
from atelier.agent.tools import TOOLS
from atelier.engine.engine import Engine
from atelier.ops.registry import load_builtin_ops
from atelier.presets import all_presets
from atelier.recipes import all_recipes
from conftest import FIXTURES

# Keywords strict mode does not accept; a schema using one would be rejected by the API.
UNSUPPORTED_IN_STRICT = {
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
    "minLength", "maxLength", "minItems", "maxItems", "pattern",
}  # fmt: skip


def walk(schema: Any) -> Any:
    if isinstance(schema, dict):
        yield schema
        for v in schema.values():
            yield from walk(v)
    elif isinstance(schema, list):
        for v in schema:
            yield from walk(v)


def assert_strict_compatible(schema: Any) -> None:
    for node in walk(schema):
        assert not (UNSUPPORTED_IN_STRICT & set(node)), node
        if node.get("type") == "object":
            assert node.get("additionalProperties") is False, node


def test_system_blocks_are_byte_stable_across_engines(tmp_path: Path) -> None:
    """No paths, handles, dates or per-store data may leak into the cached prefix."""
    a = system_blocks(Engine.open(tmp_path / "a").registry)
    b = system_blocks(Engine.open(tmp_path / "b").registry)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert catalogue_version(a) == catalogue_version(b)
    assert str(tmp_path) not in json.dumps(a)


def test_exactly_one_cache_breakpoint_on_the_last_block() -> None:
    blocks = system_blocks(load_builtin_ops())
    marked = [i for i, b in enumerate(blocks) if "cache_control" in b]
    assert marked == [len(blocks) - 1]
    assert blocks[-1]["cache_control"] == {"type": "ephemeral"}
    assert [b["type"] for b in blocks] == ["text"] * len(blocks)


def test_catalogue_version_tracks_the_text() -> None:
    blocks = system_blocks(load_builtin_ops())
    edited = [{**b} for b in blocks]
    edited[0]["text"] += " "
    assert catalogue_version(blocks) != catalogue_version(edited)
    assert len(catalogue_version(blocks)) == 12


def test_catalogue_block_names_every_op_preset_and_recipe() -> None:
    registry = load_builtin_ops()
    catalogue = system_blocks(registry)[1]["text"]
    for name in [*(d.name for d in registry.ops()), *all_presets(), *all_recipes()]:
        assert name in catalogue


def test_rules_mention_every_tool() -> None:
    """Rules and tool list drift apart silently otherwise."""
    for tool in TOOLS:
        assert tool["name"] in RULES


def test_prompt_stays_within_the_token_budget() -> None:
    """docs/04 §4.3: rules ~400 + catalogue 1.5-3k + examples ~800 tokens (≈ 4 chars/token).
    Raised 4500 → 4600 for the Sound and Follow-up rules (no silent video by surprise,
    make_voiceover, refining a result), trimmed to their minimum first; 4600 → 4800 for the
    Pictures rules (find_images / make_image, tutorials)."""
    text = "\n".join(b["text"] for b in system_blocks(load_builtin_ops()))
    assert len(text) / 4 < 4800


def test_final_schema_is_strict_compatible() -> None:
    assert_strict_compatible(FINAL_SCHEMA)
    assert set(FINAL_SCHEMA["required"]) == set(FINAL_SCHEMA["properties"])


def test_strict_tools_are_strict_compatible_and_run_pipeline_is_the_exception() -> None:
    assert [t["name"] for t in TOOLS] == sorted(t["name"] for t in TOOLS)
    for tool in TOOLS:
        if tool["name"] == "run_pipeline":
            assert "strict" not in tool  # free-form spec args cannot be expressed strictly
            assert tool["input_schema"]["additionalProperties"] is False
        else:
            assert tool["strict"] is True
            assert_strict_compatible(tool["input_schema"])
        for name in tool["input_schema"]["required"]:
            assert name in tool["input_schema"]["properties"]


def test_tool_list_serializes_identically_every_time() -> None:
    once = json.dumps(TOOLS, sort_keys=False)
    import importlib

    from atelier.agent import tools  # rebuild from scratch: definitions use constants only

    importlib.reload(tools)
    assert json.dumps(tools.TOOLS, sort_keys=False) == once


def test_every_example_spec_runs_and_passes_its_checks(
    engine: Engine, photo: str, logo: str
) -> None:
    """The examples are frozen text the model imitates: none may be invalid or fail a check."""
    clip = engine.store.put_file(FIXTURES / "av" / "clip_landscape.mp4").handle
    specs = example_specs()
    assert len(specs) >= 7
    template = engine.store.put_file(
        FIXTURES.parent / "evals" / "inputs" / "invoice_template.docx"
    ).handle
    ran_video = False
    for spec in specs:
        text = json.dumps(spec)
        if VIDEO in text and shutil.which("ffmpeg") is None:
            continue  # the video example needs ffmpeg to run; CI's image has it
        if DOC in text and shutil.which("soffice") is None:
            continue  # printing a Word file needs LibreOffice; the reference image has it
        ran_video = ran_video or VIDEO in text
        text = text.replace(PHOTO, photo).replace(LOGO, logo)
        text = text.replace(VIDEO2, clip).replace(VIDEO, clip).replace(DOC, template)
        result = engine.run(json.loads(text))
        failures = [
            r.line() for rs in result.checks.values() for r in rs if r.status.value != "pass"
        ]
        assert result.passed and not failures, (spec, failures)
    assert ran_video or shutil.which("ffmpeg") is None


def test_the_prompt_teaches_a_video_request_the_short_way() -> None:
    """A video job should be one recipe call, like a photo job: that is what keeps it cheap."""
    text = system_blocks(load_builtin_ops())[-1]["text"]
    assert "social_clip" in text and "Make a Reel" in text
