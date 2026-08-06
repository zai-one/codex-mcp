---
name: codex-mcp
description: >
  Router for unofficial codex-mcp. ALWAYS use for install/update/wire/debug/codex MCP, delegate coding, verify, brainstorm-without-heavy-work, or MCP Issues. Triggers: codex-mcp, codex login, MCP broken, economy, execute/goal/poll, verify, brainstorm. Prefer MCP tools over long docs; never multi-step pip or secrets.
version: 0.7.0
metadata:
  short-description: "Token-cheap router for codex-mcp"
---

# codex-mcp

> Unofficial — not OpenAI/Codex/Anthropic/xAI. Auth = **`codex login`** only. No OAuth/keys in MCP config/Issues.

## Token budget (host)

| Load | When |
|---|---|
| This file only | most turns |
| **1** `references/*` | need mode detail |
| templates/ | fill, don't narrate |
| scripts/ | **run**, don't cat |
| `docs/*` | last resort |

Prefer MCP `codex_app_economy` over `docs/economy.md`.

## Gate

`codex --version` · `codex login` · install (`docs/EASY.md`) · probe_stdio.py from install home

Fail → **install**. `scripts/check_ready.sh`

## Route

| Signal | Mode | Open |
|---|---|---|
| install/setup/wire | install | `references/install.md` |
| update/pull | update | `references/update.md` |
| broken/auth/tools | triage | install + `references/security.md` → feedback if 2+ |
| implement/fix/build | execute | `references/execute.md` |
| check/tests/review done | verify | `references/verify.md` |
| design/options | brainstorm | `references/brainstorm.md` |
| MCP product gap | feedback | `references/feedback.md` |
| default | operate | `references/operate.md` |

Also: `references/tools.md` · `references/hosts.md`

## Economy

Env: `CODEX_APP_MCP_ECONOMY=1`  
Once/session: `codex_app_status` → `codex_app_economy`  
light ≪ execute · tight briefs · compact polls · one job · no dumps

## Never

multi-step pip · secrets in JSON/Issues · execute for Q&A · OK without gate
