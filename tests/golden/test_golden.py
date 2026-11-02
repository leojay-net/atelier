"""Golden (bit-exact) tests (docs/09-testing.md §9.2).

Each case in cases.json runs a spec on fixtures and compares output SHA-256s. The case
also records the version of every op it uses, which enforces the rule that output bytes
may only change together with an op version bump:

* versions unchanged, hash changed → fail: bump the op version (and the changelog).
* versions changed → fail until regenerated with `pytest --update-golden`.

Image outputs are hashed by decoded content (mode, size, pixels), not file bytes: PNG
deflate streams from zlib-ng can differ between CPU architectures even when pixels are
identical. Byte-level determinism on one platform is covered by the repeat-run tests.

Cases marked `reference_only` (lossy encoders) only run inside the reference Docker
image, where ATELIER_REFERENCE_PLATFORM=1; generate their hashes there.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from atelier.canonical import sha256_file, sha256_hex
from atelier.engine.engine import Engine
from atelier.media import MediaType

HERE = Path(__file__).parent
CASES_FILE = HERE / "cases.json"
FIXTURES = HERE.parent / "fixtures"
ON_REFERENCE = os.environ.get("ATELIER_REFERENCE_PLATFORM") == "1"

CASES: list[dict[str, Any]] = json.loads(CASES_FILE.read_text())


def _bind(value: Any, handles: dict[str, str]) -> Any:
    """Replace "$name" strings with fixture handles."""
    if isinstance(value, str) and value.startswith("$"):
        return handles[value[1:]]
    if isinstance(value, dict):
        return {k: _bind(v, handles) for k, v in value.items()}
    if isinstance(value, list):
        return [_bind(v, handles) for v in value]
    return value


def _content_hash(engine: Engine, digest: str, media: MediaType) -> str:
    path = engine.store.path(digest)
    if media is not MediaType.IMAGE:
        return sha256_file(path)
    with Image.open(path) as im:
        im.load()
        header = f"{im.mode}:{im.width}x{im.height}:".encode()
        return "px:" + sha256_hex(header + im.tobytes())


def _run_case(engine: Engine, case: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    handles = {
        name: engine.store.put_file(FIXTURES / rel).handle for name, rel in case["fixtures"].items()
    }
    spec = engine.validate(_bind(case["spec"], handles))
    result = engine.run(spec)
    hashes = {k: _content_hash(engine, v.hash, v.media) for k, v in result.outputs.items()}
    versions = {s.op.name: s.op.version for s in spec.steps}
    return hashes, dict(sorted(versions.items()))


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_golden(case: dict[str, Any], engine: Engine, request: pytest.FixtureRequest) -> None:
    if case.get("reference_only") and not ON_REFERENCE:
        pytest.skip("lossy encoder output is only pinned on the reference platform")
    hashes, versions = _run_case(engine, case)
    versions_changed = versions != case["ops"]

    if request.config.getoption("--update-golden"):
        if hashes != case["outputs"] and not versions_changed:
            pytest.fail(f"{case['name']}: output changed but no op version was bumped")
        case["outputs"], case["ops"] = hashes, versions
        CASES_FILE.write_text(json.dumps(CASES, indent=2, ensure_ascii=False) + "\n")
        return

    if versions_changed:
        pytest.fail(
            f"{case['name']}: op versions changed {case['ops']} → {versions}; "
            "regenerate with `pytest --update-golden` and add a changelog entry"
        )
    assert hashes == case["outputs"], (
        f"{case['name']}: output bytes changed without an op version bump"
    )
