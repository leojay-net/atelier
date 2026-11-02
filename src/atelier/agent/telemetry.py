"""Per-request and per-job telemetry, and the headline metrics (docs/04 §4.9, docs/07 §7.4).

One JSON line per job (its requests nested inside), appended to `telemetry/jobs.jsonl`.
`summarize` is the only place the metrics are defined, so the CLI report and the eval
harness cannot drift apart.
"""

from __future__ import annotations

import json
import statistics
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# USD per million tokens (input, output). Cache reads bill at 0.1x input and 5-minute cache
# writes at 1.25x input. First-party API list prices as cached 2026-06-24: verify before
# quoting a dollar figure, and override per deployment if they have moved.
PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-fable-5-1": (10.0, 50.0),
}
CACHE_READ_MULTIPLIER = 0.1
CACHE_WRITE_MULTIPLIER = 1.25


@dataclass
class RequestRecord:
    turn: int
    model: str
    stop_reason: str
    input_tokens: int = 0  # uncached input
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    tool_calls: list[str] = field(default_factory=list)
    fallback: bool = False
    seconds: float = 0.0


@dataclass
class JobRecord:
    job_id: str
    route: str
    model: str
    effort: str
    catalogue: str
    status: str = "error"
    plan_cache_hit: bool = False
    first_pass: bool | None = None  # first run_pipeline validated, ran and passed every check
    images: int = 0
    seconds: float = 0.0
    requests: list[RequestRecord] = field(default_factory=list)

    @property
    def turns(self) -> int:
        return len(self.requests)

    def total(self, name: str) -> int:
        return sum(getattr(r, name) for r in self.requests)

    def units(self) -> float:
        """Billed-token equivalents: uncached input 1x, cache write 1.25x, cache read 0.1x, and
        output 5x (its price ratio). The measure quotas and evals count in."""
        return (
            self.total("input_tokens")
            + 1.25 * self.total("cache_write_tokens")
            + 0.1 * self.total("cache_read_tokens")
            + 5.0 * self.total("output_tokens")
        )

    def cost(self) -> float | None:
        price = PRICES.get(self.model)
        if price is None:
            return None
        inp, out = price
        return (
            self.total("input_tokens") * inp
            + self.total("cache_read_tokens") * inp * CACHE_READ_MULTIPLIER
            + self.total("cache_write_tokens") * inp * CACHE_WRITE_MULTIPLIER
            + self.total("output_tokens") * out
        ) / 1_000_000

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> JobRecord:
        data = dict(data)
        requests = [RequestRecord(**r) for r in data.pop("requests", [])]
        return cls(**data, requests=requests)


class Telemetry:
    def __init__(self, root: Path) -> None:
        self.path = root / "jobs.jsonl"

    def record(self, job: JobRecord) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(job.to_json(), sort_keys=True) + "\n")

    def load(self) -> list[JobRecord]:
        if not self.path.is_file():
            return []
        return [
            JobRecord.from_json(json.loads(line))
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def summarize(jobs: Iterable[JobRecord]) -> dict[str, Any]:
    """Headline metrics. Tokens are per *completed* job (status ok), the number that matters."""
    all_jobs = list(jobs)
    agent_jobs = [j for j in all_jobs if not j.plan_cache_hit]
    done = [j for j in agent_jobs if j.status == "ok"]
    first_pass_known = [j for j in agent_jobs if j.first_pass is not None]

    # Prefix cache-read ratio in steady state: the very first request of the data set has
    # nothing to read yet, so it is left out.
    requests = [r for j in agent_jobs for r in j.requests][1:]
    prompt_tokens = sum(
        r.input_tokens + r.cache_read_tokens + r.cache_write_tokens for r in requests
    )

    def per_done(name: str) -> float | None:
        return statistics.fmean([j.total(name) for j in done]) if done else None

    costs = [c for j in done if (c := j.cost()) is not None]
    all_requests = [r for j in agent_jobs for r in j.requests]
    return {
        "jobs": len(all_jobs),
        # False when requests were made yet nothing was ever cached or read from cache: the
        # prefix is probably below the model's minimum cacheable length. It fails silently.
        "caching_active": (
            any(r.cache_read_tokens or r.cache_write_tokens for r in all_requests)
            if all_requests
            else None
        ),
        "plan_cache_hit_rate": (
            sum(j.plan_cache_hit for j in all_jobs) / len(all_jobs) if all_jobs else None
        ),
        "completed": len(done),
        "completed_rate": len(done) / len(agent_jobs) if agent_jobs else None,
        "first_pass_rate": (
            sum(bool(j.first_pass) for j in first_pass_known) / len(first_pass_known)
            if first_pass_known
            else None
        ),
        "median_turns": _median([j.turns for j in agent_jobs]),
        "median_images": _median([j.images for j in agent_jobs]),
        "cache_read_ratio": (
            sum(r.cache_read_tokens for r in requests) / prompt_tokens if prompt_tokens else None
        ),
        "tokens_per_completed_job": {
            "input": per_done("input_tokens"),
            "cache_read": per_done("cache_read_tokens"),
            "cache_write": per_done("cache_write_tokens"),
            "output": per_done("output_tokens"),
        },
        "cost_per_completed_job": statistics.fmean(costs) if costs else None,
    }


def format_summary(s: dict[str, Any]) -> str:
    def pct(v: float | None) -> str:
        return "n/a" if v is None else f"{v:.0%}"

    def num(v: float | None, spec: str = ".1f") -> str:
        return "n/a" if v is None else format(v, spec)

    t = s["tokens_per_completed_job"]
    cost = s["cost_per_completed_job"]
    warning = (
        [
            "warning: no prompt caching is happening. The tools + system prefix is likely "
            "below this model's minimum cacheable length (docs/04 §4.4), so every request "
            "pays full price."
        ]
        if s["caching_active"] is False
        else []
    )
    return "\n".join(
        [
            *warning,
            f"jobs {s['jobs']} · completed {s['completed']} ({pct(s['completed_rate'])}) "
            f"· plan-cache hits {pct(s['plan_cache_hit_rate'])}",
            f"turns median {num(s['median_turns'])} · images median {num(s['median_images'])} "
            f"· first-pass checks {pct(s['first_pass_rate'])} "
            f"· prefix cache-read {pct(s['cache_read_ratio'])}",
            f"tokens per completed job: uncached in {num(t['input'], '.0f')} "
            f"· cache read {num(t['cache_read'], '.0f')} "
            f"· cache write {num(t['cache_write'], '.0f')} "
            f"· out {num(t['output'], '.0f')}" + ("" if cost is None else f" · ${cost:.4f}"),
        ]
    )
