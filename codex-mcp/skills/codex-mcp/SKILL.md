---
name: codex-mcp
description: >
  Router for unofficial codex-mcp (codex-app-mcp). ALWAYS use for install/update/wire/debug/codex MCP, delegate coding, verify, brainstorm-without-heavy-work, or file MCP Issues. Triggers: codex-mcp, codex-app-mcp, codex login, MCP broken, economy, goal/execute/poll, verify, brainstorm, improve MCP. Prefer MCP tools over long docs; never invent multi-step pip or paste secrets.
version: 0.7.0
metadata:
  short-description: "Token-cheap router for codex-mcp: modes + MCP tools"
---

# codex-mcp

> Unofficial — not OpenAI/Codex/Anthropic/xAI. Auth = **`codex login`** only. No OAuth/API keys in MCP config/Issues.

## Token budget (host)

| Load | When |
|---|---|
| This file only | most turns |
| **1** `references/*.md` | mode needs detail |
| templates/ | fill output, don't narrate |
| scripts/ | **run**, don't cat |
| `docs/*` | only if references insufficient |

**Prefer MCP** `codex_app_economy` over reading `docs/economy.md`.

## Gate

`codex --version` · `codex login` · install (`docs/EASY.md`) · probe_stdio.py from install home

Fail → mode **install**. Script: `scripts/check_ready.sh`

## Route (one mode)

| Signal | Mode | Open |
|---|---|---|
| install/setup/wire | install | `references/install.md` |
| update/pull | update | `references/update.md` |
| broken/auth/tools | triage | install + `references/security.md` → feedback if 2+ fails |
| implement/fix/build | execute | `references/execute.md` |
| check/tests/review done | verify | `references/verify.md` |
| design/how/options | brainstorm | `references/brainstorm.md` |
| MCP product gap | feedback | `references/feedback.md` |
| default | operate | `references/operate.md` |

Tool map: `references/tools.md` · Hosts: `references/hosts.md`

## Economy

Env: `CODEX_APP_MCP_ECONOMY=1` · once/session: `codex_app_status` → `codex_app_economy`  
consult/light ≪ execute/goal · tight briefs · compact polls · no paste dumps · one job

## Never

pip multi-step guides · secrets in JSON/Issues · execute for Q&A · claim OK without gate
