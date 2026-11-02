# ADR-0019: A `ModelAdapter` seam for Claude, OpenAI, Gemini and Ollama

- **Status:** Accepted
- **Date:** 2026-10-10

## Context

ADR-0003 chose the Claude Messages API with an Atelier-owned loop, and the loop (`agent/loop.py`)
was written directly against that API's response shape: `resp.stop_reason` values `tool_use /
end_turn / max_tokens / refusal / pause_turn`, `resp.content` blocks (`thinking`,
`redacted_thinking`, `tool_use`, `fallback`), `resp.usage.cache_read_input_tokens`, and a request
carrying `thinking: {type: "adaptive"}`, `output_config: {effort, format}` and
`cache_control: {type: "ephemeral"}`.

A deployment without an `ANTHROPIC_API_KEY` could not use the agent at all — including a
developer who only has an OpenAI key, or who wants to run a fully local, no-API-key model
through Ollama. None of OpenAI, Gemini or Ollama have a real equivalent of Claude's extended
thinking, this style of explicit prompt caching, or its server-side safety-fallback re-routing,
so the loop could not simply swap in a different HTTP client — it needed a seam that keeps
those three Claude-only behaviors intact while giving the other providers a shape they can
actually fill in.

## Options considered

1. **A second, parallel agent loop for non-Claude providers.**
   - Pros: the Claude path is untouched, byte for byte, by construction.
   - Cons: two loops to keep in sync on every future change to budgets, plan-cache keys, batch
     handling, telemetry; the whole point of ADR-0003 (one small, well-tested loop) is lost.
2. **Rewrite the loop against a generic, least-common-denominator request/response shape** (no
   thinking, no explicit cache control, no fallback vocabulary).
   - Pros: simplest adapters.
   - Cons: throws away Claude's real capabilities — adaptive thinking, explicit caching and
     safety fallbacks are load-bearing for cost and reliability today, confirmed by the eval
     gate in `evals/baseline.py`. Not acceptable just to make three other providers easier.
3. **A `ModelAdapter` Protocol between the loop's control flow and each provider's wire format**,
   with a canonical `Turn` dataclass that keeps Claude's full stop-reason vocabulary (so its
   branches in `loop.py` are unchanged) while other adapters only ever produce the subset they
   have a real equivalent for.
   - Pros: one loop; Claude's adapter is today's exact request/response handling moved verbatim,
     so the existing `ScriptedModel`-based test suite keeps passing unmodified; new providers
     are additive modules.
   - Cons: the adapter interface has to carry some Claude-shaped vocabulary (`pause_turn`,
     `refused`, `fallback`) that other providers simply never emit — an asymmetry, but a
     contained one (it lives in one dataclass's docstring, not spread through the loop).

## Decision

Option 3. `agent/providers/base.py` defines `Turn`, `ToolCall`, `TurnUsage` and the
`ModelAdapter` Protocol (`initial_history`, `send`, `append_assistant`, `append_tool_results`).
`agent/loop.py`'s `_run` reads only `Turn` and the Protocol; it never reads a provider's raw
response object directly, which stays opaque outside its own adapter as `Turn.raw`.

- **`providers/anthropic.py`**: the exact pre-existing Claude behavior (thinking, explicit
  `cache_control`, fallback echoing via `echo_content`/`live_tool_uses`/`_after_fallback`), now
  as `AnthropicAdapter`'s internals. `client.py`'s `MessagesClient` Protocol is untouched.
- **`providers/openai_compatible.py`**: targets the stable **Chat Completions** shape, not the
  newer Responses API, specifically because Ollama's OpenAI-compatible endpoint mirrors Chat
  Completions — one adapter module this way covers OpenAI, Ollama, and any other
  OpenAI-compatible server (vLLM, LM Studio). No thinking, no explicit caching (OpenAI's is
  automatic server-side, surfaced only in `usage.prompt_tokens_details.cached_tokens`), no
  fallback routing — those fields are simply inert for this adapter. Tool schemas carry over
  `strict: true` from `TOOLS`' existing shape, since Chat Completions supports the same
  strict-JSON-schema tool arguments Claude's tools already require.
- **`providers/gemini.py`**: targets `google-genai`'s classic `generate_content` surface,
  verified against the installed SDK (not assumed from training — see Research note below).
  `output_schema` (`FINAL_SCHEMA`) is deliberately not sent: Gemini's forced-JSON response mode
  is documented as incompatible with function calling in the same request, and every request
  here carries tools. The loop already tolerates a non-JSON final answer
  (`loop._conclude`'s `except ValueError` fallback), so the system prompt's own instruction to
  answer in the final JSON shape is the only enforcement for this adapter.
- **`run_job`/`run_batch_job`** gained an optional `adapter: ModelAdapter | None = None`
  parameter alongside the existing `client: MessagesClient | None = None`; when `adapter` is
  omitted, `AnthropicAdapter(client)` is built internally, so every existing call site and test
  (including `tests/unit/test_agent_loop.py`'s `go()` helper) kept working unchanged.
- **`agent/providers/__init__.py`** holds `build_adapter(provider, *, fallbacks=True)`, the one
  place credential handling and SDK construction happen for all four providers — used by both
  `api/core/clients.py` (the Django worker, selected via the existing `ATELIER_MODEL_CLIENT`
  factory-swap setting from ADR-0018's pattern) and `agent/commands.py`'s new
  `--provider {anthropic,openai,gemini,ollama}` CLI flag, so there is exactly one place to get
  a provider's construction and `MissingCredentials` mapping right.
- **`pyproject.toml`**: new optional extras `openai` (covers OpenAI and Ollama — Ollama is
  reached through the same `openai` client pointed at a different `base_url`) and `gemini`,
  mirroring the existing `ml` extra's pattern; both SDKs are imported lazily inside the adapter
  or registry functions that need them, never at module top level.

### Research note: verify the SDK, don't guess the enum

An initial research pass claimed Google now recommends a new "Interactions API"
(`client.interactions.create()`). Installing `google-genai` (2.29.0) and inspecting it directly
showed this is not what the installed package exposes: `dir(genai)` has no `interactions`
module, and `genai.models.Models` exposes the long-established `generate_content` /
`generate_content_stream` methods. `types.FinishReason` is likewise the classic enum (`STOP`,
`MAX_TOKENS`, `SAFETY`, ...; a few newer additions like `UNEXPECTED_TOOL_CALL` don't change the
overall shape). The adapter was built against this verified shape, not the research claim.

## Consequences

- Claude's behavior is unchanged: same request bytes, same cache layout, same fallback
  handling, same test suite passing unmodified.
- A deployment can now run on whichever provider it actually has access to, including a fully
  local, no-API-key Ollama model, by pointing `ATELIER_MODEL_CLIENT` at `ollama_client` (or
  passing `--provider ollama` on the CLI) — the actual problem that motivated this ADR.
- `JobRecord.units()`'s pricing ratios stay calibrated to Claude's token costs; quota accounting
  is not yet accurate for other providers' real prices. This is a separate pricing-research
  task, tracked but deliberately not done here.
- No per-org or per-request provider picker exists in the web UI yet; provider choice is
  deployment-wide via `ATELIER_MODEL_CLIENT`, consistent with how model/effort selection already
  works. A Settings-page picker is a reasonable follow-up once there's demand for it.
- Gemini's forced-JSON output mode cannot be combined with this adapter's tool calling; its
  final-answer correctness relies on prompt instruction rather than server-side enforcement,
  unlike Claude and OpenAI. If Gemini's API later allows combining the two, revisit.
