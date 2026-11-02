"""The eval baseline: a general coding agent doing the same tasks with plain tools.

The thesis gate (docs/08) asks for "5x fewer tokens per completed job than a general coding
agent, at equal or better pass rate". This is that agent, built to be a fair opponent:

* one tool, `bash`, and nothing else. A leaner tool list means fewer baseline tokens, so this is
  the conservative choice: it makes Atelier's advantage harder to show, not easier;
* the same model, effort, adaptive thinking and automatic prompt caching as the Atelier agent,
  and the same "ask instead of guessing" instruction;
* judged on the delivered files only (`graders.PORTABLE`): it has no spec and no engine checks,
  so only dimensions, format, size, transparency and asking-vs-guessing apply to both sides.

Everything the model writes is untrusted, so commands run only inside a throwaway container
(`sandbox.py`): no network, read-only root filesystem, no capabilities, memory/CPU/process
limits, no host mounts at all, a timeout per command. Each command runs in a fresh shell in
/work (files persist, shell state does not); the system prompt says so.
"""

from __future__ import annotations

import shutil
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from atelier.agent.budget import Budget, BudgetLimits
from atelier.agent.client import MessagesClient, MissingCredentials
from atelier.agent.config import AgentConfig
from atelier.agent.loop import AgentResult
from atelier.agent.providers.anthropic import echo_content, live_tool_uses, request_record
from atelier.agent.telemetry import JobRecord
from atelier.engine.engine import Engine
from atelier.evals.graders import PORTABLE, Context, grade
from atelier.evals.runner import (
    DEFAULT_BUDGET_UNITS,
    EvalReport,
    TaskResult,
    grade_rubric,
    units,
)
from atelier.evals.sandbox import (
    Sandbox,
    SandboxFactory,
    docker_factory,
)
from atelier.evals.task import Task, TaskError
from atelier.media import HandleError

BASH_TOOL = {"type": "bash_20250124", "name": "bash"}
BASELINE_LIMITS = BudgetLimits(max_turns=25, max_output_tokens=40_000, max_images=0)

BASELINE_SYSTEM = (
    "You are a coding agent with a bash tool in a Linux container (no network). Python 3 with "
    "Pillow and NumPy is installed, plus the DejaVu fonts, ffmpeg and ffprobe, python-docx, "
    "openpyxl, python-pptx, pypdf, pikepdf and LibreOffice (soffice). Each command runs "
    "in a fresh shell in /work: files persist between commands, shell state (cd, variables) "
    "does not.\n\n"
    "Do the user's task. Input files are in /work/in. Put the deliverable file(s) in /work/out. "
    "When you are done, reply with one or two sentences saying what you produced. If the request "
    "is too ambiguous to do without guessing, ask one short question instead and produce nothing."
)


# -- the conversation ------------------------------------------------------------------------


@dataclass
class Conversation:
    status: str  # ok | budget | refused | error
    final_text: str
    detail: str
    record: JobRecord


def _bash(sandbox: Sandbox, tool_input: Any) -> tuple[str, bool]:
    if not isinstance(tool_input, dict):
        return "bash: input must be an object", True
    if tool_input.get("restart"):
        return "Each command already runs in a fresh shell; there is nothing to restart.", False
    command = tool_input.get("command")
    if not isinstance(command, str) or not command.strip():
        return "bash: missing command", True
    result = sandbox.run(command)
    text = result.output
    if result.timed_out:
        text += "\n[timed out]"
    elif result.exit_code != 0:
        text += f"\n[exit code {result.exit_code}]"
    return text, result.timed_out


def converse(
    prompt: str, client: MessagesClient, config: AgentConfig, sandbox: Sandbox, job_id: str
) -> Conversation:
    record = JobRecord(job_id, "baseline", config.model, config.effort, "baseline")
    budget = Budget(BASELINE_LIMITS)
    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
    max_tokens, retried = config.max_tokens, False
    while True:
        request = {
            "model": config.model,
            "max_tokens": max_tokens,
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": config.effort},
            "system": BASELINE_SYSTEM,
            "tools": [BASH_TOOL],
            "cache_control": {"type": "ephemeral"},
            "messages": list(messages),
        }
        sent = time.perf_counter()
        resp = client.create(request)
        turn = request_record(resp, budget.turns + 1, config.model, time.perf_counter() - sent)
        record.requests.append(turn)
        budget.charge(turn.output_tokens)

        stop = resp.stop_reason
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        if stop == "end_turn":
            return Conversation("ok", text, "", record)
        if stop == "refusal":
            return Conversation("refused", "", "the model refused", record)
        if stop == "max_tokens":
            if retried or config.max_tokens_retry <= max_tokens or budget.hard_exhausted:
                return Conversation("error", text, "the response hit max_tokens", record)
            retried, max_tokens = True, config.max_tokens_retry
            continue
        if stop == "pause_turn":
            messages.append({"role": "assistant", "content": echo_content(resp.content)})
            if budget.hard_exhausted:
                return Conversation("budget", text, "turn or token budget used", record)
            continue
        if stop != "tool_use":
            return Conversation("error", text, f"unexpected stop_reason {stop!r}", record)

        messages.append({"role": "assistant", "content": echo_content(resp.content)})
        uses = live_tool_uses(resp.content)
        if not uses:
            return Conversation("error", text, "tool_use without a tool_use block", record)
        results = []
        for block in uses:
            if block.name != "bash":
                out, is_error = f"unknown tool {block.name!r}", True
            else:
                out, is_error = _bash(sandbox, block.input)
            item: dict[str, Any] = {
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": out,
            }
            if is_error:
                item["is_error"] = True
            results.append(item)
        turn.tool_calls = [b.name for b in uses]
        if budget.hard_exhausted:
            return Conversation("budget", text, "turn or token budget used", record)
        messages.append({"role": "user", "content": results})


# -- running tasks ---------------------------------------------------------------------------


def _prompt(task: Task) -> str:
    files = "\n".join(f"- in/{name}" for name in task.inputs.values())
    return f"{task.request}\n\nInput files:\n{files}" if files else task.request


def _grade(
    task: Task,
    convo: Conversation,
    deliverables: list[Path],
    inputs: Sequence[Path],
    engine: Engine,
    config: AgentConfig,
    judge_client: MessagesClient | None,
) -> tuple[str, list[Any]]:
    status, question = convo.status, ""
    if status == "ok" and not deliverables:
        if convo.final_text.endswith("?"):
            status, question = "needs_input", convo.final_text
        else:
            status = "no_output"
    outcome = AgentResult(
        status=status,
        summary=convo.final_text,
        question=question,
        outputs={p.stem: str(p) for p in deliverables},
    )
    graders = grade(
        tuple(g for g in task.graders if g["type"] in PORTABLE),
        Context(outcome, files=deliverables),
    )
    if task.rubric:
        originals = [engine.store.put_file(p).handle for p in inputs[:2]]
        outputs = {p.name: engine.store.put_file(p).handle for p in deliverables[:2]}
        graders.append(grade_rubric(task, outputs, originals, engine, config, judge_client))
    return status, graders


def _run_task(
    task: Task,
    client: MessagesClient,
    config: AgentConfig,
    inputs_dir: Path,
    factory: SandboxFactory,
    judge_client: MessagesClient | None,
    engine: Engine,
) -> TaskResult:
    sources: dict[str, Path] = {}
    for name, filename in task.inputs.items():
        src = inputs_dir / filename
        if not src.is_file():
            raise TaskError(f"{task.id}: input {name!r} → {src} does not exist")
        sources[filename] = src
    scratch = Path(tempfile.mkdtemp(prefix="atelier-baseline-"))
    sandbox = factory()
    try:
        sandbox.upload(sources)
        try:
            convo = converse(_prompt(task), client, config, sandbox, f"baseline-{task.id}")
        except (MissingCredentials, HandleError, TaskError):
            raise
        except Exception as e:  # one flaky request must not lose the whole run
            return TaskResult(
                task.id, task.split, task.kind, "error", f"{type(e).__name__}: {e}",
                [], None, None, False,
            )  # fmt: skip
        deliverables = sandbox.download(scratch)
        status, graders = _grade(
            task, convo, deliverables, list(sources.values()), engine, config, judge_client
        )
    finally:
        sandbox.close()
        shutil.rmtree(scratch, ignore_errors=True)
    used = units(convo.record)
    return TaskResult(
        task.id,
        task.split,
        task.kind,
        status,
        convo.detail,
        graders,
        convo.record,
        used,
        used > (task.budget or DEFAULT_BUDGET_UNITS),
    )


def run_baseline(
    tasks: Sequence[Task],
    *,
    client: MessagesClient,
    config: AgentConfig,
    inputs_dir: Path,
    engine: Engine,
    sandbox_factory: SandboxFactory | None = None,
    judge_client: MessagesClient | None = None,
    on_result: Callable[[TaskResult], None] | None = None,
) -> EvalReport:
    factory = sandbox_factory or docker_factory()
    results = []
    for task in tasks:
        result = _run_task(task, client, config, inputs_dir, factory, judge_client, engine)
        results.append(result)
        if on_result is not None:
            on_result(result)
    return EvalReport(config.model, config.effort, "baseline", results)
