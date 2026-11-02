"""Opt-in checks against the real Messages API (docs/04 §4.4: "verify in CI").

These cost a few cents and need credentials, so they never run by default:

    ATELIER_LIVE=1 ANTHROPIC_API_KEY=... uv run pytest tests/live -v -s

What only the real API can tell us, and what the scripted-model tests cannot:
* it accepts the whole request shape (strict tool schemas, `output_config.format` beside
  tools, top-level `cache_control` beside a system breakpoint, `fallbacks`, adaptive thinking);
* the frozen tools + system prefix really is cached across jobs.
A silent cache miss is the costliest failure in this design: every request still succeeds.
"""

from __future__ import annotations

import os

import pytest

from atelier.agent.budget import BudgetLimits
from atelier.agent.client import AnthropicMessages
from atelier.agent.config import AgentConfig
from atelier.agent.loop import Job, run_job
from atelier.engine.engine import Engine

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("ATELIER_LIVE") != "1",
        reason="spends tokens: set ATELIER_LIVE=1 (and credentials) to run",
    ),
]


def test_the_api_accepts_the_request_and_the_second_job_reads_the_cached_prefix(
    engine: Engine, photo: str
) -> None:
    # An ambiguous request: the cheapest job that still exercises tools, thinking and format.
    job = Job("Resize the photo.", assets=(photo,))
    config = AgentConfig(limits=BudgetLimits(max_turns=2))  # fallbacks on, as in production
    client = AnthropicMessages(fallbacks=config.fallbacks)

    first = run_job(job, engine=engine, client=client, config=config)
    second = run_job(job, engine=engine, client=client, config=config)

    assert first.record is not None and second.record is not None
    a, b = first.record.requests[0], second.record.requests[0]
    print(f"\njob 1 first request: {a}\njob 2 first request: {b}")
    assert first.status != "error", first.detail
    assert a.cache_write_tokens + a.cache_read_tokens > 0, (
        "nothing was cached: the tools + system prefix may be below the model's minimum "
        "cacheable length"
    )
    assert b.cache_read_tokens > 0, "the second identical job did not read the prefix from cache"
