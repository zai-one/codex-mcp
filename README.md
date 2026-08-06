# codex-app-mcp

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-stdio%20%7C%20HTTP-purple.svg)](https://modelcontextprotocol.io/)
[![Version](https://img.shields.io/badge/version-0.5.3-informational.svg)](pyproject.toml)

**Claude / Cursor orchestrate → Codex app-server works** (goals, budgets, policy roots).

> **Unofficial community project** — not OpenAI, Codex, Anthropic, or xAI.  
> Auth = local `codex login` (`CODEX_HOME`). Never put OAuth in MCP config or HTTP bearer.

---

## Install (one command)

```bash
curl -fsSL https://raw.githubusercontent.com/zai-one/codex-mcp/main/scripts/install.sh \
  | bash -s -- --project "$HOME/code/my-project"
```

Windows: `irm https://raw.githubusercontent.com/zai-one/codex-mcp/main/scripts/install.ps1 | iex`

Then:

```bash
codex login
source ~/.config/codex-mcp/env
~/.local/share/codex-mcp/.venv/bin/python ~/.local/share/codex-mcp/scripts/probe_stdio.py
```

Merge `~/.config/codex-mcp/mcp/claude_desktop.snippet.json` → restart → `codex_app_status`.

**Full easy guide:** [docs/EASY.md](docs/EASY.md)

| Language | Page |
|---|---|
| Easy (canonical) | [docs/EASY.md](docs/EASY.md) |
| EN / RU / 中文 / ES | [docs/install/](docs/install/) (short pointers) |

---

## What it is

| | |
|---|---|
| Host | Claude, Cursor, … (stdio or bearer HTTP) |
| Worker | **Codex CLI / app-server** on same machine or VPS |
| Why | Save host tokens under `tokenBudget` |
| Economy | `CODEX_APP_MCP_ECONOMY=1` · tool `codex_app_economy` |

## Optional

- [VPS](docs/install/vps.md) · [FastMCP](docs/install/fastmcp.md) · [Economy](docs/economy.md)
- [Skills](docs/SKILLS.md) · [Security](SECURITY.md) · [Reference](docs/REFERENCE.md)
