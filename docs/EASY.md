# Install (the only path)

> **Unofficial** community project — **not** OpenAI / Codex.

## One command

**macOS / Linux:**

```bash
curl -fsSL https://raw.githubusercontent.com/zai-one/codex-mcp/main/scripts/install.sh \
  | bash -s -- --project "$HOME/code/my-project"
```

**Windows (PowerShell):**

```powershell
irm https://raw.githubusercontent.com/zai-one/codex-mcp/main/scripts/install.ps1 | iex
```

## Then do these 3 things

```bash
# 1) Codex product login (required — interactive)
codex login

# 2) Check
source ~/.config/codex-mcp/env
~/.local/share/codex-mcp/.venv/bin/python ~/.local/share/codex-mcp/scripts/probe_stdio.py

# 3) Wire Claude Desktop / Cursor
# merge: ~/.config/codex-mcp/mcp/claude_desktop.snippet.json
# restart → call codex_app_status
```

## If something fails

| Message | Fix |
|---|---|
| codex not found | Install Codex CLI, then `codex login` |
| app-server unavailable | Same OS user as login; check `CODEX_HOME` |
| Tools missing | Restart host; check launcher path |

## Optional later

- VPS / HTTP: `docs/install/vps.md`
- FastMCP: `docs/install/fastmcp.md`
- Economy: `docs/economy.md`

No multi-step manual pip install in public docs — use the script.

## Agent skill

Use router skill **`codex-mcp`** ([SKILLS.md](SKILLS.md)).

## After install (Session Protocol)

1. Wire host MCP snippet.
2. Call **`codex_app_session_begin`** (`intent: "auto"`).
3. Follow recommended tools → **`codex_app_session_end`**.
4. Skill: `codex-mcp` v0.8.
