---
name: codex-app-mcp
description: Operate the codex-app-mcp gateway (stdio or bearer HTTP over codex app-server). Use when wiring Claude/Cursor MCP, HTTP control plane, policy roots, or protocol probes.
version: 0.4.0
---

# codex-app-mcp skill

## Safe defaults

- No OpenAI/Codex OAuth in repo or MCP env blocks.
- Auth is local `codex login` (`CODEX_HOME`).
- Project work fails closed without `CODEX_APP_MCP_ALLOWED_ROOTS`.
- HTTP: loopback without token forbidden for non-loopback binds; use `CODEX_APP_MCP_HTTP_TOKEN` or `TOKEN_FILE` (mutually exclusive).
- Do not expose experimental app-server WebSocket; use stdio child + optional HTTP MCP.

## Operator checklist

1. `python -m pip install -e ".[test]"`
2. `python -m pytest -q`
3. `python scripts/probe_stdio.py`
4. Optional HTTP: set token, `python scripts/probe_http.py`
5. Host: `codex_app_status` then `codex_app_doctor`

## Client pointers

- Install: `docs/install/en.md` (+ ru / zh-CN / es)
- Examples: `examples/`
- Reference: `docs/REFERENCE.md`
- Security: `SECURITY.md`
