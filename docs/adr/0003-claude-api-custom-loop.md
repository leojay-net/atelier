# ADR-0003: Claude Messages API with an Atelier-owned loop, instead of the Claude Agent SDK

- **Status:** Accepted
- **Date:** 2026-09-24

## Context
The initial brief said "full app (Agent SDK)," meaning a complete application with its own agent loop rather than just a library or an MCP server. There are several ways to build the agent part with Claude:

| Approach | Who supplies the loop | Built-in tools |
|---|---|---|
| Claude Messages API, own loop | Atelier | None, only ours |
| Messages API + SDK Tool Runner (beta helper) | SDK | None, only ours |
| Claude Agent SDK (Claude Code as a library) | SDK | Shell, file read/write/edit, search, web… |
| Managed Agents | Anthropic (hosted) | Hosted sandbox with bash/files |

## Options considered
1. **Claude Agent SDK**
   - Pros: batteries included: loop, context management, built-in file and shell tools, subagents.
   - Cons: its core value is exactly the capabilities Atelier removes on purpose (ADR-0002). We would be disabling most of it while giving up byte-level control of prompt layout (caching) and hard per-job budgets.
2. **Managed Agents**
   - Pros: no loop code; hosted sandbox.
   - Cons: the sandbox model again centers on bash/files; media processing needs our pinned binaries and fonts on our workers; data residency and cost control are harder.
3. **Messages API + Tool Runner**
   - Pros: less loop code.
   - Cons: beta helper; we need custom handling for image-bearing tool results, budgets, structured final output and exact transcript logging for replay.
4. **Messages API + Atelier-owned loop**
   - Pros: full control of the prompt bytes (cache layout), budgets, stop-reason handling, telemetry and replay; no beta dependency for the core loop; the loop is small (~150 lines) because the tool surface is 4 tools.
   - Cons: we maintain the loop ourselves.

## Decision
Option 4: official `anthropic` Python SDK, `client.messages.create` (streaming where outputs may be long), with an Atelier-owned loop in `atelier/agent/loop.py`. The model is configurable per route (default `claude-opus-5`, adaptive thinking, effort pinned per route).

## Consequences
- The app is still a "full app with an agent." Only the harness choice differs from the original wording.
- We own: stop-reason handling (`end_turn`, `tool_use`, `max_tokens`, `refusal`, `pause_turn`), append-only history, parallel tool-result batching and budget enforcement.
- Switching to the Tool Runner later is cheap if it covers our needs, because tools are plain functions behind a dispatch table.
- If an open-ended "expert mode" (code execution) is ever added, the Claude Agent SDK is the first candidate for **that route only**.
