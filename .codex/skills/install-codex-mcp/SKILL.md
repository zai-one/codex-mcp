---
name: install-codex-mcp
description: >
  Install codex-app-mcp only after Codex CLI is installed and `codex login`
  succeeded. Claude/Cursor/VPS/FastMCP wiring. Abort if CLI/login missing.
version: 0.5.1
---

# install-codex-mcp

> **Unofficial.** Not an official OpenAI/Codex product. Never put OAuth in MCP config or HTTP bearer.

## HARD GATE

1. `codex --version` (or your install’s version flag) works  
2. `codex login` completed as the **same OS user** that runs the MCP  
3. Then install this package

If 1–2 fail → **stop**. Do not invent API keys.

```bash
codex --version
codex login
```

## Package + roots

```bash
cd <REPO_PATH>
python -m pip install -e ".[test]"
export CODEX_APP_MCP_ALLOWED_ROOTS="<PROJECT_ROOT>"
export CODEX_APP_MCP_ECONOMY=1
python scripts/probe_stdio.py
```

## Hosts

| Host | Template |
|---|---|
| Claude Desktop | `examples/claude_desktop.mcp.json` |
| Claude Code | `examples/claude-code.mcp.json` |
| Cursor | `examples/cursor.mcp.json` |
| HTTP / VPS | `docs/install/vps.md` + `examples/vps.systemd.service` |
| FastMCP | `docs/install/fastmcp.md` |

## Skill mirrors

`.claude/skills/` · `.codex/skills/` · `.agents/skills/` · `skills/`

## Hand off

Runtime skill **codex-app-mcp**. Call `codex_app_economy` once.

## Never

- OAuth/API keys in JSON or bearer file  
- Claim success without probe  
- Full coding sessions in install skill  
