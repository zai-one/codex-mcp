# codex-app-mcp

[English](README.md) · [Русский](README.ru.md)

**Run and follow Codex coding tasks from your MCP client.**

Start work, steer a task, review changes and return to persisted goals through one
connection. The bridge connects your assistant to an installed Codex app-server;
your Codex login and usage remain with Codex.

## ⚡ Host loop (Session v1.2)

Unofficial bridge. **Save host tokens:**

1. `codex_app_session_begin({"goal":"…","host_budget":"small"})`  
2. Loop `codex_app_session_next` → do only `card` (`host_cmd` | `mcp_tool` | `end`)  
3. Stop polling when `done=true`, then finish with `codex_app_session_end`.

Skill **`codex-mcp` v1.0** enforces this. No OAuth in MCP config — CLI login only.


[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-stdio%20%7C%20HTTP-purple.svg)](https://modelcontextprotocol.io/)
[![Version](https://img.shields.io/badge/version-0.8.0-informational.svg)](pyproject.toml)

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

Then, on macOS/Linux:

```bash
codex login
source ~/.config/codex-mcp/env
~/.local/share/codex-mcp/.venv/bin/python ~/.local/share/codex-mcp/scripts/probe_stdio.py
```

Merge `~/.config/codex-mcp/mcp/claude_desktop.snippet.json` → restart → `codex_app_status`.

On Windows, the installer writes `%USERPROFILE%/.config/codex-mcp/env.ps1` and
installs the command into `%LOCALAPPDATA%/codex-mcp/.venv/Scripts/`.
Merge this entry into your MCP client, replacing both example paths:

```json
{
  "mcpServers": {
    "codex": {
      "command": "C:/Users/you/AppData/Local/codex-mcp/.venv/Scripts/codex-app-mcp.exe",
      "env": {"CODEX_APP_MCP_ALLOWED_ROOTS": "C:/Users/you/code/my-project"}
    }
  }
}
```

Restart the connection, then ask:

> Show the Codex connection status, allowed project directories and available tasks. Do not start work yet.

**Skill (router):** `codex-mcp` — see [docs/SKILLS.md](docs/SKILLS.md)

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

## Built by ZAI.ONE

[ZAI.ONE](https://zai.one) is a digital agency working on websites, SEO, advertising and analytics. We also build tools that connect AI assistants to everyday work. [Talk to us on Telegram](https://t.me/zai_one) about setup, automation or an integration for your team.

Found a bug or need a feature? [Open an issue](https://github.com/zai-one/codex-mcp/issues) with a redacted reproduction. If the tool helps, give it a ⭐.
