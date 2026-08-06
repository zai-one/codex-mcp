---
name: codex-mcp
description: >
  Router for unofficial codex-mcp. ALWAYS use for MCP install/update/debug/delegate.
  First: codex_app_session_begin(goal, host_budget). Follow plan tools only. Triggers: codex-mcp,
  session_begin, plan, budget, codex login, economy. Never multi-step pip or secrets.
version: 0.9.0
metadata:
  short-description: "Session v1.1 plan compiler + budget guard for codex-mcp"
---

# codex-mcp

> Unofficial — not OpenAI/Codex. Auth = **`codex login`** only.

## Protocol

1. **`codex_app_session_begin`** `intent` + optional `goal` + `host_budget` (tiny|small|normal)
2. Execute **only** `plan[]` / `recommended_tools` (never invent tools; honor `deny_tools`)
3. **`codex_app_session_tick`** with `tool_used`/`step_done`; stop if `force_end`
4. **`codex_app_session_end`** → receipt + `budget_report` + `lesson`

Open **one** `references/*` only if `skill_ref` and plan insufficient.

## Budget (token budget)

Host executes plan; does **not** re-plan in prose. Caps from `budget`. Economy on begin.

## Never

Secrets · tools outside plan · long docs when plan exists · OK without begin
