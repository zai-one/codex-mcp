---
name: codex-mcp
description: >
  Router for unofficial codex-mcp. ALWAYS use for install/update/wire/debug/codex MCP,
  goals, verify, brainstorm, or MCP Issues. First tool: codex_app_session_begin.
  Triggers: codex-mcp, session_begin, codex login, economy, goal, verify, brainstorm.
  Prefer session_* + MCP tools over long docs; never multi-step pip or OAuth in config.
version: 0.8.0
metadata:
  short-description: "Session Protocol v1 router for codex-mcp"
---

# codex-mcp

> Unofficial — not OpenAI/Codex. Auth = **`codex login`** only. No OAuth in MCP/HTTP bearer/Issues.

## Token budget

| Load | When |
|---|---|
| This file | most turns |
| **1** `references/*` | only if begin.skill_ref |
| templates/scripts | fill / **run** |

## Protocol (always)

1. **`codex_app_session_begin`** (intent auto|…)
2. Only **`recommended_tools`**
3. **`codex_app_session_tick`** while running
4. **`codex_app_session_end`** receipt (± issue draft)

## Gate

`codex --version` · `codex login` · EASY install · roots. Fail → install/triage.

## Economy

session_begin sets compact economy. tokenBudget 16k–40k · no event dumps · one goal.

## Never

OAuth in bearer · multi-step pip · huge budgets · OK without session_begin
