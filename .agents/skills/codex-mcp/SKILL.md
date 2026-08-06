---
name: codex-mcp
description: >
  Router for unofficial codex-mcp. ALWAYS: codex_app_session_begin then loop codex_app_session_next until done.
  Triggers: codex-mcp, session_next, plan, budget, codex login, install, execute.
  Never multi-step pip, secrets, or invent tools outside the card.
version: 1.0.0
metadata:
  short-description: "Session v1.2 navigator — one card at a time"
---

# codex-mcp

> Unofficial — not OpenAI/Codex. Auth = **`codex login`** only.

## Token budget protocol

1. **`codex_app_session_begin`** once (`goal`, `host_budget=small`)
2. Loop **`codex_app_session_next`** only — execute the returned `card` (host_cmd | mcp_tool | end)
3. When `done=true` → **`codex_app_session_end`** if card says so, then stop

Do **not** open `references/*` unless card/skill_ref and you are blocked.
Do **not** re-plan in prose. Do **not** call tools not on the card.

## Never

OAuth in config · dumps · parallel jobs · claim OK without begin/next
