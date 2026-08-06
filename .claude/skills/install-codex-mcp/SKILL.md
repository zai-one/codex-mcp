---
name: install-codex-mcp
description: Token-cheap install and setup only for codex-app-mcp (local stdio, native HTTP, FastMCP, VPS). Use when the user needs wiring or env — not deep goal execution.
version: 0.5.0
---

# install-codex-mcp (setup only)

> **Unofficial.** Not an official OpenAI/Codex product. No OAuth in MCP config.

Keep replies short. Prefer links to docs over long pastes.

## Minimum local path

```bash
cd <REPO_PATH>
python -m pip install -e ".[test]"
export CODEX_APP_MCP_ALLOWED_ROOTS="<PROJECT_ROOT>"
export CODEX_APP_MCP_ECONOMY=1
codex login
python scripts/probe_stdio.py
```

## Client config

Templates: `examples/claude_desktop.mcp.json`, `examples/claude-code.mcp.json`,
`examples/cursor.mcp.json`. Non-secret env only.

## HTTP (native)

```bash
export CODEX_APP_MCP_HTTP_TOKEN_FILE=<TOKEN_FILE>
codex-app-mcp --transport http --host 127.0.0.1 --port 8765
python scripts/probe_http.py
```

Bearer = `openssl rand -hex 32` — **not** OpenAI OAuth.

## FastMCP / VPS

- `docs/install/fastmcp.md` + `examples/fastmcp_proxy.py`
- `docs/install/vps.md` + `examples/vps.systemd.service`
- `examples/http.env.example`

## Doc map

| Need | File |
|---|---|
| Full install EN | `docs/install/en.md` |
| FastMCP | `docs/install/fastmcp.md` |
| VPS | `docs/install/vps.md` |
| Economy | `docs/economy.md` |
| Runtime skill | `codex-app-mcp` skill |

## Do not

- Put OAuth/API keys in MCP JSON or HTTP bearer
- Expand into full coding sessions — hand off to `codex-app-mcp` skill
