# ADR-0008: Agent layer: implementation decisions that differ from the design doc

- **Status:** Accepted
- **Date:** 2026-09-25 (decided under delegated authority)

## Context
Building Phase 2 against the real SDK types, the current API reference and the eval design
turned up places where docs/04 either could not be followed literally or where a small change
serves the Phase 2 exit criteria (median turns ≤ 3, first-pass ≥ 85%, ≥ 90% cache reads) better.
None of these change the architecture (ADR-0002, ADR-0003); they are choices inside it. They are
listed here so they can be confirmed or overturned in one place. Nothing has been run against the
live API yet (no credentials were available), so items 1 and 6 in particular are unverified there.

## Decisions

1. **`run_pipeline` is the one tool that is not `strict`.** docs/04 §4.2 says all four tools use
   `strict: true`. Strict schemas need `additionalProperties: false` on every object, but a spec's
   `args` are op-specific and free-form. The alternatives were to pass the spec as a JSON *string*
   inside a strict schema (escaped quotes cost output tokens and the schema guarantees nothing about
   the string's content), or to generate a per-op schema (large, and it would sit in the cached
   prefix). Instead `describe_op`, `inspect_media` and `preview` are strict and `run_pipeline`
   takes a JSON object whose contract is the engine's validator, which reports every error with its
   path (docs/03 §3.2). A string-valued `spec` is also tolerated.
2. **The final structured answer is `{summary, warnings, question}`; outputs come from the engine.**
   docs/04 §4.5 has the model list `outputs`. Asking it to restate handles costs tokens and invites
   an invented handle. `AgentResult.outputs` is read from the engine result of the best run. The
   `question` field is how the model asks instead of guessing when a request is ambiguous.
3. **`preview` takes `assets: [handle]`** (one for `thumb`/`crop`, two for `compare`, several for
   `contact_sheet`) instead of a single `asset`, matching the engine's preview ops. The `page` and
   `time` inputs arrive with the document and video domains. Adding them changes the tool list once,
   which re-writes the cached prefix once per release.
4. **The opening user message carries one brief inspect line per asset.** The engine computes it at
   zero tokens, so the model can go straight to `run_pipeline` (two requests for a simple job)
   instead of spending a round trip on `inspect_media`. The tool stays for `detail: "full"`.
5. **The plan cache stores the spec the model submitted, not its canonical expansion.** A hit is
   re-validated and re-run; if it no longer validates (an op version changed) or a check now fails,
   the entry is evicted and the model takes over. Only jobs whose output passed every check are
   stored.
6. **Refusal fallbacks use `fallbacks: "default"` on `client.beta.messages`.** In the installed SDK
   (`anthropic` 1.8) `fallbacks` and `betas` exist only on the beta surface, so the client wrapper
   switches endpoint when fallbacks are enabled (beta header `server-side-fallback-2026-07-01`).
   Echoing a fallback turn back into history follows the API rules: thinking, redacted thinking and
   `tool_use` blocks before the last `fallback` marker are not echoed, and only tool calls after it are
   answered. The SDK's `BetaFallbackBlock` also requires a `trigger` field that the reference's
   `{type, from, to}` example omits; the test fixtures use the SDK's real shape.
7. **Budgets are checked between requests.** A hard limit never interrupts tool execution: engine
   work costs no tokens, so a `run_pipeline` that was already requested still runs and can still
   become the result. The soft limit adds a wrap-up notice to the last permitted turn, as a text
   block after the tool results (append-only, so the cache stays valid).
8. **Evals compare cost in billed input-token equivalents.** Uncached input 1x, cache write 1.25x,
   cache read 0.1x, output 5x (the list-price ratio for the current models). It makes agent runs and a
   baseline run comparable without picking a currency. Reports are JSON, so a baseline produced by any
   harness can be compared with `atelier eval report --baseline`.
9. **Requests are not streamed.** `max_tokens` is 16k with one retry at 20k after a `max_tokens` stop
   (the SDK refuses larger non-streaming requests). Jobs are short (a spec is ~100-300 tokens). Move
   to streaming if long outputs ever appear.

## Consequences
- The strict-tool guarantee covers three of four tools; malformed specs are caught by the validator
  and cost one short repair turn, which the eval's first-pass metric measures.
- Everything that the scripted-model tests cannot prove is isolated in one opt-in live test
  (`ATELIER_LIVE=1 pytest tests/live`): that the API accepts the request shape (strict schemas beside
  `output_config.format`, top-level `cache_control` beside a system breakpoint, `fallbacks`) and that
  a second identical job reads the prefix from cache.
- The price table in `agent/telemetry.py` is a cached copy of list prices and must be re-checked
  before a dollar figure is quoted.
- The baseline harness (the same tasks done by a general coding agent) is not built. The thesis gate's
  "5x fewer tokens at equal or better pass rate" cannot be evaluated until a baseline report exists.
- The `min` cacheable prefix is model-dependent (512 tokens on Claude Opus 5, 4096 on Opus 4.6/4.5 and
  Haiku 4.5). Below it caching fails silently, so `atelier stats` warns when requests were made but
  nothing was ever cached.
