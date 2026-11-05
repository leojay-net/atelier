"""The agent loop (docs/04 §4.5): request in, executed and checked spec out.

Invariants the tests pin down:
* History is append-only. Earlier turns are never edited, reordered or removed, which keeps the
  prompt cache valid; a truncated (`max_tokens`) or refused turn is never appended at all.
* Tools, system, model, thinking and effort are identical on every request of a job.
* All tool results of one assistant turn go back in a single user message.
* Every stop reason is handled explicitly. Nothing is silently dropped.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from atelier.agent.budget import Budget
from atelier.agent.client import MessagesClient, MissingCredentials
from atelier.agent.config import AgentConfig
from atelier.agent.music import MusicService
from atelier.agent.pictures import PictureService
from atelier.agent.plan_cache import PlanCache, plan_key
from atelier.agent.prompt import FINAL_SCHEMA, catalogue_version, system_blocks
from atelier.agent.providers.anthropic import AnthropicAdapter
from atelier.agent.providers.base import ModelAdapter, Turn
from atelier.agent.telemetry import JobRecord, RequestRecord, Telemetry
from atelier.agent.tools import TOOLS, Toolbox
from atelier.agent.voice import VoiceService
from atelier.engine.engine import BatchItem, Engine
from atelier.engine.runner import JobResult, StepFailed
from atelier.media import AssetInfo, HandleError
from atelier.spec.errors import SpecInvalid

# ok · every check passed          checks_failed · finished, but a check still fails
# needs_input · the model asked    no_output · finished without producing anything
# refused · safety classifier      budget · hard limit hit     error · anything else
STATUSES = ("ok", "checks_failed", "needs_input", "no_output", "refused", "budget", "error")


@dataclass(frozen=True)
class Job:
    prompt: str
    assets: tuple[str, ...] = ()
    route: str = "ask"
    batch_size: int = 1  # >1: the first asset is a sample, the spec will run on all of them


@dataclass
class AgentResult:
    status: str
    summary: str = ""
    title: str = ""  # 2-6 words naming the result, for chat lists and the library
    warnings: tuple[str, ...] = ()
    question: str = ""
    detail: str = ""  # refusal category, error text…
    outputs: dict[str, str] = field(default_factory=dict)  # output id → asset handle
    spec: Any = None  # the spec that produced `outputs`
    engine_result: JobResult | None = None
    record: JobRecord | None = None
    generated: tuple[tuple[str, str], ...] = ()  # (handle, filename) the tools made or fetched
    credits: tuple[str, ...] = ()  # attribution lines for photos found online

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def run_job(
    job: Job,
    *,
    engine: Engine,
    client: MessagesClient | None = None,
    adapter: ModelAdapter | None = None,
    config: AgentConfig,
    telemetry: Telemetry | None = None,
    plan_cache: PlanCache | None = None,
    voice: VoiceService | None = None,
    pictures: PictureService | None = None,
    music: MusicService | None = None,
) -> AgentResult:
    """`client` (today's Anthropic-shaped `MessagesClient`) or `adapter` (any `ModelAdapter`,
    `providers/`), not both. `client` stays for every existing caller: it is wrapped in
    `AnthropicAdapter` internally, so Claude's behavior is unchanged byte for byte."""
    if adapter is None:
        if client is None:
            raise ValueError("run_job needs either client (Anthropic) or adapter (any provider)")
        adapter = AnthropicAdapter(client)
    system = system_blocks(engine.registry)
    version = catalogue_version(system)
    infos = [engine.store.resolve(h) for h in job.assets]  # fails before any tokens are spent
    record = JobRecord(
        job_id=uuid.uuid4().hex[:12],
        route=job.route,
        model=config.model,
        effort=config.effort_for(job.route),
        catalogue=version,
    )
    started = time.perf_counter()
    counted = True
    try:
        result = _run(
            job, infos, system, version, record, engine, adapter, config, plan_cache,
            voice, pictures, music,
        )  # fmt: skip
        record.status = result.status
        result.record = record
        return result
    except MissingCredentials:
        counted = False  # a setup problem, not a job: it must not skew the metrics
        raise
    finally:
        record.seconds = time.perf_counter() - started
        if telemetry is not None and counted:
            telemetry.record(record)


def _run(
    job: Job,
    infos: Sequence[AssetInfo],
    system: list[dict[str, Any]],
    version: str,
    record: JobRecord,
    engine: Engine,
    adapter: ModelAdapter,
    config: AgentConfig,
    plan_cache: PlanCache | None,
    voice: VoiceService | None = None,
    pictures: PictureService | None = None,
    music: MusicService | None = None,
) -> AgentResult:
    key = plan_key(job.prompt, (i.hash for i in infos), version, config.model)
    if plan_cache is not None and (hit := _replay(plan_cache, key, engine)) is not None:
        record.plan_cache_hit = True
        return hit

    budget = Budget(config.limits)
    toolbox = Toolbox(engine, budget, voice=voice, pictures=pictures, music=music)
    history = adapter.initial_history(_user_text(job, infos, engine))
    max_tokens, retried = config.max_tokens, False

    def finish(status: str, detail: str = "", final: dict[str, Any] | None = None) -> AgentResult:
        record.images = budget.images
        record.first_pass = toolbox.first_pass
        best = toolbox.best()
        final = final or {}
        if status == "budget" and (done := toolbox.finished_edit()) is not None:
            best, status = done, "ok"  # the edit is done; only the reply did not fit
            final = {
                "summary": "Here it is. I ran out of steps before describing it, so have a look "
                "and tell me anything you would like changed.",
            }  # no title: the request's first words name it
        return AgentResult(
            status=status,
            summary=str(final.get("summary", "")),
            title=" ".join(str(final.get("title", "")).split())[:80],
            warnings=tuple(str(w) for w in final.get("warnings", ())),
            question=str(final.get("question", "")),
            detail=detail,
            outputs=_outputs(best.result) if best and best.result else {},
            spec=best.spec if best else None,
            engine_result=best.result if best else None,
            generated=tuple(toolbox.generated),
            credits=tuple(dict.fromkeys(toolbox.credits)),
        )

    while True:
        sent = time.perf_counter()
        model_turn: Turn = adapter.send(
            history,
            system=system,  # frozen; Claude's explicit cache breakpoint is on its last block
            tools=TOOLS,  # frozen and sorted
            output_schema=FINAL_SCHEMA,
            model=config.model,
            max_tokens=max_tokens,
            effort=record.effort,
        )
        turn = RequestRecord(
            turn=budget.turns + 1,
            model=model_turn.model or config.model,
            stop_reason=model_turn.stop_reason,
            input_tokens=model_turn.usage.input_tokens,
            output_tokens=model_turn.usage.output_tokens,
            cache_read_tokens=model_turn.usage.cache_read_tokens,
            cache_write_tokens=model_turn.usage.cache_write_tokens,
            fallback=model_turn.fallback,
            seconds=time.perf_counter() - sent,
        )
        record.requests.append(turn)
        budget.charge(turn.output_tokens)
        if engine.observer is not None:
            engine.observer("turn", {"turn": budget.turns, "stop": model_turn.stop_reason})

        stop = model_turn.stop_reason
        if stop == "refused":
            return finish("refused", model_turn.detail)
        if stop == "max_tokens":
            if retried or config.max_tokens_retry <= max_tokens:
                return finish("error", "the response hit max_tokens")
            if budget.hard_exhausted:
                return finish("budget")
            retried, max_tokens = True, config.max_tokens_retry
            continue  # the truncated turn is dropped, not appended
        if stop == "pause_turn":
            history = adapter.append_assistant(history, model_turn)
            if budget.hard_exhausted:
                return finish("budget")
            continue
        if stop == "end_turn":
            return _conclude(model_turn, finish, toolbox, plan_cache, key)
        if stop != "tool_use":
            return finish("error", f"unexpected stop_reason {stop!r}")

        history = adapter.append_assistant(history, model_turn)
        uses = model_turn.tool_calls
        if not uses:
            return finish("error", "stop_reason tool_use without a tool_use block")
        results = [(call, toolbox.dispatch(call.name, call.input)) for call in uses]
        turn.tool_calls = [call.name for call in uses]
        if engine.observer is not None:
            engine.observer(
                "tools",
                {"names": turn.tool_calls, "details": [_call_detail(call) for call in uses]},
            )
            for call, outcome in results:  # what went wrong, for the activity feed and debugging
                if outcome.is_error:
                    text = outcome.content if isinstance(outcome.content, str) else ""
                    engine.observer("tool_error", {"name": call.name, "text": text[:600]})
        if budget.hard_exhausted:
            return finish("budget")
        notice = budget.notice() if budget.soft_exhausted else ""
        history = adapter.append_tool_results(history, results, notice)


def _conclude(
    model_turn: Turn,
    finish: Callable[..., AgentResult],
    toolbox: Toolbox,
    plan_cache: PlanCache | None,
    key: str,
) -> AgentResult:
    text = model_turn.text
    try:
        final = json.loads(text)
        if not isinstance(final, dict):
            raise ValueError("not an object")
    except ValueError:
        final = {"summary": text.strip(), "warnings": ["the final reply was not valid JSON"]}
    best = toolbox.best()
    if final.get("question") and best is None:
        return finish("needs_input", final=final)
    if best is None:
        return finish("no_output", final=final)
    if not best.passed:
        return finish("checks_failed", final=final)
    if plan_cache is not None:
        plan_cache.store(key, best.spec, str(final.get("summary", "")))
    return finish("ok", final=final)


def _replay(plan_cache: PlanCache, key: str, engine: Engine) -> AgentResult | None:
    """A plan-cache hit: run the stored spec through the engine, zero tokens."""
    hit = plan_cache.lookup(key)
    if hit is None:
        return None
    try:
        result = engine.run(hit.spec)
    except (SpecInvalid, StepFailed):
        plan_cache.discard(key)  # written against ops that have since changed
        return None
    if not result.passed:
        plan_cache.discard(key)
        return None
    return AgentResult(
        status="ok",
        summary=hit.summary,
        outputs=_outputs(result),
        spec=hit.spec,
        engine_result=result,
    )


def _outputs(result: JobResult) -> dict[str, str]:
    return {out_id: rep.handle for out_id, rep in result.outputs.items()}


def _call_detail(call: Any) -> str:
    """A short, human-readable line about one tool call, for the live activity feed."""
    args = call.input if isinstance(call.input, dict) else {}
    if call.name == "make_voiceover":
        text = " ".join(str(args.get("text", "")).split())
        return f"voice {args.get('voice', '')}: “{text[:160]}{'…' if len(text) > 160 else ''}”"
    if call.name == "run_pipeline":
        spec = args.get("spec")
        if isinstance(spec, dict):
            if "recipe" in spec:
                return f"recipe {spec['recipe']}"
            steps = spec.get("steps") or spec.get("ops") or []
            ops = [s.get("op") if isinstance(s, dict) else s[0] for s in steps if s]
            return " → ".join(str(o) for o in ops if o)
        return ""
    if call.name == "find_images":
        return f"“{str(args.get('query', ''))[:80]}” × {args.get('count', '')}"
    if call.name == "make_music":
        style = str(args.get("style", ""))[:100]
        return f"{args.get('bpm', '')} bpm, {args.get('seconds', '')}s: “{style}”"
    if call.name == "make_image":
        text = " ".join(str(args.get("prompt", "")).split())
        return f"“{text[:140]}{'…' if len(text) > 140 else ''}”"
    if call.name in ("inspect_media", "preview"):
        return f"{len(args.get('assets') or [])} file(s)"
    if call.name == "describe_op":
        return ", ".join(str(n) for n in (args.get("names") or [])[:6])
    return ""


def _user_text(job: Job, infos: Sequence[AssetInfo], engine: Engine) -> str:
    """The request plus one brief line per asset, so no `inspect_media` round trip is needed."""
    parts = [job.prompt.strip()]
    if infos:
        parts.append("Assets:\n" + "\n".join(engine.inspect(i) for i in infos))
    if job.batch_size > 1:
        parts.append(
            f"Batch: the spec you write will run on {job.batch_size} files. The first asset is "
            "a sample; write the spec for it."
        )
    return "\n\n".join(parts)


# -- batches (docs/04 §4.7) ------------------------------------------------------------------


@dataclass
class BatchJobResult:
    agent: AgentResult
    input_name: str | None = None
    items: list[BatchItem] = field(default_factory=list)
    error: str = ""  # set when the agent succeeded but the batch could not be started


def find_input_name(engine: Engine, spec: Any, handle: str) -> str | None:
    """Which input of `spec` holds `handle` (in any spec form); None if not exactly one."""
    if not isinstance(spec, dict):
        return None
    digest = engine.store.resolve(handle).hash

    def holds(value: Any) -> bool:
        try:
            return isinstance(value, str) and engine.store.resolve(value).hash == digest
        except (HandleError, ValueError):
            return False

    if "recipe" in spec:
        names = [k for k, v in (spec.get("params") or {}).items() if holds(v)]
    elif "ops" in spec:
        names = ["in"] if holds(spec.get("in")) else []
    else:
        names = [k for k, v in (spec.get("inputs") or {}).items() if holds(v)]
    return names[0] if len(names) == 1 else None


def run_batch_job(
    prompt: str,
    each: Sequence[str],
    *,
    fixed: Sequence[str] = (),
    engine: Engine,
    client: MessagesClient | None = None,
    adapter: ModelAdapter | None = None,
    config: AgentConfig,
    telemetry: Telemetry | None = None,
    plan_cache: PlanCache | None = None,
) -> BatchJobResult:
    """One agent run on a sample, then the engine over all `each`: one model call for N files.

    `fixed` are assets shared by every item (a logo, say); they are shown to the model but
    not varied. `client`/`adapter`: see `run_job`.
    """
    if not each:
        raise ValueError("a batch needs at least one file")
    job = Job(prompt, assets=(each[0], *fixed), batch_size=len(each))
    agent = run_job(
        job,
        engine=engine,
        client=client,
        adapter=adapter,
        config=config,
        telemetry=telemetry,
        plan_cache=plan_cache,
    )
    if not agent.ok:
        return BatchJobResult(agent)
    name = find_input_name(engine, agent.spec, each[0])
    if name is None:
        return BatchJobResult(agent, error="could not tell which input of the spec to vary")
    return BatchJobResult(agent, name, engine.run_batch(agent.spec, name, list(each)))
