---
name: grok-mcp
description: >
  Router for unofficial grok-mcp (grok-delegate). ALWAYS use for install/update/wire/debug/grok MCP, delegate coding, verify, brainstorm-without-heavy-work, or file MCP Issues. Triggers: grok-mcp, grok-delegate, grok login, MCP broken, economy, goal/execute/poll, verify, brainstorm, improve MCP. Prefer MCP tools over long docs; never invent multi-step pip or paste secrets.
version: 0.7.0
metadata:
  short-description: "Token-cheap router for grok-mcp: modes + MCP tools"
---

# grok-mcp

> Unofficial — not xAI/Grok/Anthropic/OpenAI. Auth = **`grok login`** only. No OAuth/API keys in MCP config/Issues.

## Token budget (host)

| Load | When |
|---|---|
| This file only | most turns |
| **1** `references/*.md` | mode needs detail |
| templates/ | fill output, don't narrate |
| scripts/ | **run**, don't cat |
| `docs/*` | only if references insufficient |

**Prefer MCP** `grok_agent_economy` over reading `docs/economy.md`.

## Gate

`grok --version` · `grok login` · install (`docs/EASY.md`) · `python -m grok_delegate --self-test` → binary+auth PASS

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

Env: `GROK_DELEGATE_ECONOMY=1 GROK_DELEGATE_ECONOMY_COMPACT_POLL=1` · once/session: `grok_agent_status` → `grok_agent_economy`  
consult/light ≪ execute/goal · tight briefs · compact polls · no paste dumps · one job

## Never

pip multi-step guides · secrets in JSON/Issues · execute for Q&A · claim OK without gate
