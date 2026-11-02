# 4. Agent Layer

The agent layer turns a plain-language request into an executed, checked spec, spending as few tokens as possible. It is a thin client of the engine: all media knowledge lives in ops, presets and recipes.

> **Implementation note (2026-09-25).** Phase 2 is implemented in `src/atelier/agent/`. It follows
> this document except for the choices recorded in
> [ADR-0008](adr/0008-agent-layer-implementation-decisions.md): `run_pipeline` is not `strict`,
> the final answer carries `summary`/`warnings`/`question` (outputs come from the engine), `preview`
> takes `assets: [...]`, and the opening message includes one brief line per asset.

## 4.1 Integration choice

- **SDK:** the official Anthropic Python SDK (`anthropic`), plus `openai` and `google-genai` for the other providers (§4.1.1).
- **Surface:** the Claude **Messages API** with **custom (client-side) tools** and an Atelier-owned agent loop.
- **Why not a batteries-included agent harness** (the Claude Agent SDK, i.e. Claude Code as a library): its main value is built-in shell, file and web tools. Atelier deliberately removes those (principle P1), and needs byte-level control of prompt layout for caching and hard per-job budgets. See [ADR-0003](adr/0003-claude-api-custom-loop.md).
- **Model:** configurable per route. The default is `claude-opus-5` with adaptive thinking. Model and effort are settings, never hard-coded.

### 4.1.1 Provider choice: a `ModelAdapter` seam

The loop itself (`agent/loop.py`) does not read any provider's raw response directly — it reads
a normalized `Turn` (`agent/providers/base.py`) through a `ModelAdapter` Protocol
(`initial_history`, `send`, `append_assistant`, `append_tool_results`). A deployment picks a
provider by pointing `ATELIER_MODEL_CLIENT` (the existing factory-swap setting) at one of four
factories in `api/core/clients.py` — `anthropic_client`, `openai_client`, `gemini_client`,
`ollama_client` — or, on the CLI, with `atelier ask --provider {anthropic,openai,gemini,ollama}`.
Ollama needs no API key at all: it is reached through the `openai` client pointed at
`OLLAMA_BASE_URL` (default `http://localhost:11434/v1`).

Claude keeps everything in §4.4–4.6 (explicit cache control, adaptive thinking, server-side
safety fallbacks) — none of the other providers have a real equivalent, so those `Turn`/config
fields are simply inert for their adapters. See [ADR-0019](adr/0019-multi-provider-model-adapter.md) for the full design and the Chat-Completions-not-Responses-API and Gemini-SDK-verification
decisions behind it. `JobRecord.units()`'s cost accounting stays calibrated to Claude's pricing
and is not yet accurate for the other providers.

## 4.2 Tool surface: 4 tools

Tool definitions are re-sent on every request (they're cached, but still part of the prefix), and each extra tool is one more thing the model can choose wrongly. Four is enough:

| Tool | Input | Returns | Purpose |
|---|---|---|---|
| `inspect_media` | `assets: [handle]`, `detail: "brief"\|"full"` | One compact line per asset (full adds streams, fonts, placeholders, page list…) | Understand inputs without seeing them |
| `describe_op` | `names: [op or preset or recipe]` | Full param docs for just those items | On-demand detail; the catalogue in the system prompt stays one line per op |
| `run_pipeline` | `spec` (full, linear or recipe form), `dry_run: bool` | Validation errors **or** per-output summaries + check results | The single "do the work" call |
| `preview` | `asset`, `kind: "thumb"\|"contact_sheet"\|"crop"\|"compare"\|"page"`, region/time/page | A **small image** block + one-line caption | Visual judgment, used sparingly (see [Verification](06-verification.md)) |

All schemas use **`strict: true`** with `additionalProperties: false`, so the model cannot submit malformed tool input. Tool definitions are **sorted by name and serialized deterministically** to keep the cache prefix byte-stable.

> **Note on forced tool choice:** some current models (e.g. Claude Opus 5.5, Claude Fable 5.1) reject `tool_choice` of type `any`/`tool`. Atelier always uses `tool_choice: auto` and steers through the system prompt, so it stays portable across models.

### Tool result format
Results are **plain text, short and consistent**, designed to be read once:

```
ok · out → asset:77ab0e (reels_1080x1920 v3)
  video 15.00s · 1080x1920 · 30fps · h264 high · 4.2MB
  audio aac 48k stereo · -14.0 LUFS · TP -1.2 dB
  checks 11/11 pass
cache: 2/5 steps reused · 6.8s
```

On failure:

```
checks 10/11 · FAIL captions_in_safe_area: 4 lines (00:03.2–00:07.9) exceed bottom safe margin by 38px
hint: set video.captions.args.position="lower_safe" or reduce style size (bold_pop → clean)
```

Errors are reported with `is_error: true` so the model knows to correct itself.

## 4.3 System prompt

The system prompt is **frozen per release** (no dates, user names or per-request data) and has three blocks in stability order:

1. **Role & rules** (~400 tokens): you produce specs; prefer recipes > presets > explicit args; use the linear shorthand; don't request previews unless a check fails or the task is visual; ask the user only when the intent is ambiguous in a way that changes the output.
2. **Catalogue** (~1.5–3k tokens, generated by `catalogue.py`): one line per op, preset and recipe, e.g.
   `video.trim(start,end,precise?) — cut range; stream-copy on keyframes`
   `recipe social_clip(in,start,end,platform=reels|tiktok|shorts,captions=clean|bold_pop|none)`
3. **Examples** (~800 tokens): 4–6 request → spec pairs covering each domain, using the shortest form.

Rendering is deterministic (sorted, no timestamps), so the whole block caches.

## 4.4 Prompt caching layout

Following the prefix-match rule (render order: `tools → system → messages`):

```
[tools: 4 definitions, sorted]                      ─┐
[system: role + catalogue + examples]  ◄─ explicit   │ cached across ALL jobs
                                          breakpoint ─┘
[messages: user request, tool results …] ◄─ top-level automatic caching
                                            (grows with the conversation)
```

- **One explicit `cache_control` breakpoint** on the last system block caches tools + system for every job.
- **Top-level automatic caching** covers the growing conversation within a job.
- **Never change tools, model, thinking or effort settings mid-job.** Each of these invalidates cached tiers.
- Operator context that varies (for example the user's brand kit) goes **into messages**, never into the system prompt.
- **Verify in CI:** an integration test sends two identical jobs and asserts `usage.cache_read_input_tokens > 0` on the second. Monitoring alerts if the cache-read ratio drops.

Cache reads bill at roughly 0.1× the input price, so the ~3–4k-token fixed prefix costs about as much as ~400 uncached tokens on every request after the first.

## 4.5 The loop

```python
# Sketch only; not final code.
def run_job(job: Job) -> JobResult:
    if (spec := plan_cache.lookup(job.prompt_norm, job.input_hashes)):
        return engine.run(spec)                       # zero tokens

    messages = [user_message(job)]
    budget = Budget(max_turns=6, max_output_tokens=12_000, max_images=3)

    while True:
        resp = client.messages.create(
            model=cfg.model,                          # e.g. "claude-opus-5"
            max_tokens=cfg.max_tokens,
            thinking={"type": "adaptive"},
            output_config={"effort": cfg.effort},     # pinned per route
            system=SYSTEM_BLOCKS,                     # frozen, cache_control on last block
            tools=TOOLS,                              # frozen, sorted
            cache_control={"type": "ephemeral"},      # automatic caching for the tail
            messages=messages,
        )
        telemetry.record(job, resp.usage)
        budget.charge(resp)

        if resp.stop_reason == "refusal":  ...        # surface to user, stop
        if resp.stop_reason == "max_tokens":  ...      # retry once with higher cap, else fail
        if resp.stop_reason == "end_turn":
            break

        messages.append({"role": "assistant", "content": resp.content})
        results = [dispatch(b) for b in resp.content if b.type == "tool_use"]
        messages.append({"role": "user", "content": results})   # ALL results in one message

        if budget.exhausted():
            messages.append(budget_notice())          # "finish now with best result"
    plan_cache.store(job, final_spec)
    return result
```

Key behaviors:
- **Append-only history.** Never edit or delete earlier turns. That keeps the cache valid and meets newer models' history-integrity rules.
- **Parallel tool calls** are allowed (for example `inspect_media` on 3 assets). All results go back in **one** user message.
- **Stop reasons** (`refusal`, `max_tokens`, `pause_turn`) are handled explicitly and never silently dropped.
- **Refusal fallback:** for models that support it, the server-side `fallbacks` parameter is enabled so a safety-classifier false positive routes to a fallback model instead of failing the job. This can be turned off in config.
- **Budgets:** soft limit (tell the model to wrap up) and hard limit (stop, return best completed output + report).
- **Final answer** uses structured output (`output_config.format` with a JSON schema): `{outputs:[…], summary, warnings[]}`. The UI never has to parse prose.

## 4.6 Effort and thinking

- Adaptive thinking (`thinking: {type: "adaptive"}`) lets the model think only as much as a request needs.
- **Effort is pinned per route** (changing it mid-conversation invalidates the message cache):
  - `ask` (new free-form request): `high` by default
  - `recipe_fill` (the request clearly maps to a recipe): evaluate `medium`/`low`
  - `repair` (a check failed and the hint is explicit): evaluate `low`
- Lower effort is only adopted **after the eval suite shows no quality drop** ([Testing](09-testing.md#agent-evals)). Judge cost per *completed job*, not per request.

## 4.7 Plan cache (zero-token repeats)

- Key: `sha256(normalized_prompt + sorted input content hashes + catalogue version + model id)`.
- Value: the final canonical spec that passed all checks.
- **Exact match only.** Semantic ("similar request") matching would bring back nondeterminism and is intentionally left out.
- Typical hits: batch jobs ("do the same to these 200 photos" → one agent run, then 199 engine runs), retries, re-renders after input tweaks with the same instruction (input hash changes → miss → normal run).

### Batch pattern
For "apply X to N files," the agent is called **once** on a representative file. The resulting spec is **generalized** (the input becomes a parameter) and run by the engine over all N files: **one model call for N files.**

## 4.8 Context hygiene

- Jobs are short (typically 2–4 requests), so compaction is not needed for single jobs.
- For long interactive sessions in the UI (many successive edits), use **context editing** to clear old tool results. The spec history is kept in the app DB, not in the transcript.
- Document content (text extracted from a DOCX/PDF) is sent **only when the task requires reading it**, and truncated deliberately with an explicit notice. The model can page through it with `inspect_media(detail="full", range=…)`.

## 4.9 Telemetry (per request, per job)

Logged fields: `input_tokens`, `output_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`, turns, images sent, tool calls, checks first-pass result, plan-cache hit, wall time, model, effort.

Headline metrics:
- **Tokens per completed job** (by route and domain)
- **Turns per job** (target median ≤ 3)
- **Cache-read ratio** of the prefix (target ≥ 90% after warm-up)
- **First-pass check rate**: jobs whose first `run_pipeline` passes all checks (target ≥ 85%)
- **Images per job** (target median 0)
