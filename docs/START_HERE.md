# START HERE (plain English)

> **Unofficial community project** — not made by OpenAI / Codex / Anthropic / xAI.

## This MCP does nothing useful until ALL of these are true

| # | Requirement | How you know it worked |
|---|---|---|
| 1 | **Codex CLI installed** on the same machine (or VPS) that runs this MCP | `codex --version` (or `codex -V`) works |
| 2 | **You logged in** with that CLI | `codex login` completed on that OS user |
| 3 | **This package installed** | `pip install -e ".[test]"` then `codex-app-mcp --help` |
| 4 | **Allowlisted project roots** set | `CODEX_APP_MCP_ALLOWED_ROOTS` is a real folder |
| 5 | **Host wired** (Claude / Cursor / …) **or** you use a **skill** | Tools like `codex_app_status` appear |

Installing only this GitHub repo is **not** enough without steps 1–2.

## Two ways to set up

### A) Commands yourself

```bash
# 1) Codex CLI + login (upstream — not this repo)
codex --version
codex login

# 2) This gateway
cd <REPO_PATH>
python -m pip install -e ".[test]"
export CODEX_APP_MCP_ALLOWED_ROOTS="<PROJECT_ROOT>"
export CODEX_APP_MCP_ECONOMY=1
python scripts/probe_stdio.py
```

### B) Agent via skill

| Host | Skills path |
|---|---|
| Claude Code | `.claude/skills/` |
| Codex CLI | `.codex/skills/` or `~/.agents/skills/` |
| Portable | `.agents/skills/` |

Say: *Use skill **install-codex-mcp**. Only after Codex CLI is installed and `codex login` succeeded.*

Runtime: **codex-app-mcp**. Authoring: **create-agent-skill**.

## Success

1. `codex login` session exists for the MCP process user  
2. `python scripts/probe_stdio.py` ok  
3. Host lists `codex_app_status`, `codex_app_economy`, …  
4. Optional small `codex_app_goal` with low `tokenBudget`

## Stuck?

| Symptom | Fix |
|---|---|
| codex not found | Install Codex CLI first |
| app-server unavailable | same user as `codex login`; check `CODEX_HOME` |
| roots fail-closed | set `CODEX_APP_MCP_ALLOWED_ROOTS` |
| HTTP 401 | operator bearer token, **not** OpenAI OAuth |

More: [docs/install/en.md](docs/install/en.md) · [economy](docs/economy.md) · [SECURITY](SECURITY.md)
