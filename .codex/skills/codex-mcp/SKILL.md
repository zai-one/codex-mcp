---
name: codex-mcp
description: >
  Single router for the unofficial codex-mcp (codex-app-mcp) package. Use whenever
  the user installs, updates, wires, debugs, or runs Codex MCP; runs goals via
  Codex app-server; verifies results; brainstorms without heavy goals; or wants a
  GitHub Issue after repeated MCP friction. Triggers on: codex-mcp, codex-app-mcp,
  install MCP, update MCP, MCP broken, tools missing, codex login, economy,
  tokenBudget, goal, verify, brainstorm, open issue, improve MCP. Prefer this
  skill over multi-step pip guides or putting OAuth in config.
version: 0.6.0
metadata:
  short-description: "Router: install/update/operate/execute/verify/brainstorm/feedback for Codex MCP"
---

# codex-mcp (router)

> **Unofficial community project** — not OpenAI, Codex, Anthropic, or xAI.
> Auth = local **`codex login`** (`CODEX_HOME`) only. Never put OAuth/API keys in MCP config, HTTP bearer, or Issues.

**Read `references/*` only for the active mode.**

## HARD GATE

1. Codex CLI: `codex --version`
2. Login: `codex login` as the same OS user
3. Package + roots via one-command install / `docs/EASY.md`
4. Probe: `python scripts/probe_stdio.py` (from install) succeeds enough to list tools

If gate fails → **install** mode.

Script: [`scripts/check_ready.sh`](scripts/check_ready.sh)

## Mode router

| User signal | Mode | Load |
|---|---|---|
| install, setup, wire host | **install** | [`references/install.md`](references/install.md) |
| update, upgrade, pull | **update** | [`references/update.md`](references/update.md) + [`scripts/update_mcp.sh`](scripts/update_mcp.sh) |
| MCP broken / auth / roots | **triage** | install/update + [`references/security.md`](references/security.md) → **feedback** if repeated |
| implement / fix / build | **executor** | [`references/executor.md`](references/executor.md) + [`templates/goal-brief.md`](templates/goal-brief.md) |
| verify / tests / review | **verifier** | [`references/verifier.md`](references/verifier.md) |
| brainstorm / design | **brainstorm** | [`references/brainstorm.md`](references/brainstorm.md) — light tools only |
| improve MCP / file bug | **feedback** | [`references/feedback-issues.md`](references/feedback-issues.md) |
| normal session | **operate** | [`references/operate.md`](references/operate.md) + [`references/modes.md`](references/modes.md) |

## Economy (always)

1. `codex_app_status` or `codex_app_economy` once
2. Prefer small goals + `tokenBudget` 16k–40k over unbounded runs
3. Poll compact status — no full event dumps into host chat
4. Archive/compact large threads when supported
5. Env: `CODEX_APP_MCP_ECONOMY=1`

## Never

- OAuth inside `CODEX_APP_MCP_HTTP_TOKEN`
- Manual multi-step pip as the public path
- Huge budgets for typos
- Secrets in Issues

## Docs

`docs/EASY.md` · `docs/economy.md` · `docs/REFERENCE.md` · `SECURITY.md` · `docs/SKILLS.md`
