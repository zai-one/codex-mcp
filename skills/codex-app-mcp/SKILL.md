---
name: codex-app-mcp
description: >
  Operate codex-app-mcp after Codex CLI install+login. Economy goals, HTTP VPS,
  security. Refuse if CLI/auth/roots missing.
version: 0.5.1
---

# codex-app-mcp (runtime)

> **Unofficial.** Auth = `codex login` → `CODEX_HOME` only.

## Preflight

```bash
codex --version
# process user must own the login session
export CODEX_APP_MCP_ALLOWED_ROOTS="<PROJECT_ROOT>"
python scripts/probe_stdio.py
```

On failure → skill **install-codex-mcp**.

## Economy sequence

| # | Action |
|---|---|
| 1 | `codex_app_status` or `codex_app_economy` once |
| 2 | `codex_app_goal` with tight objective + `tokenBudget` 16k–40k |
| 3 | Poll job/goal — no full objective replay |
| 4 | Compact/archive threads when large |

Env: `CODEX_APP_MCP_ECONOMY=1`

## Never

- OpenAI OAuth inside `CODEX_APP_MCP_HTTP_TOKEN`  
- Huge budgets for typos  
- Full event dumps into host chat  

Docs: `docs/START_HERE.md` · `docs/economy.md` · `docs/REFERENCE.md`
