---
name: codex-app-mcp
description: Operate the codex-app-mcp gateway (stdio or bearer HTTP over codex app-server, token economy, VPS). Use when wiring Claude/Cursor MCP, HTTP control plane, policy roots, FastMCP proxy, or protocol probes.
version: 0.5.0
---

# codex-app-mcp skill

> **Unofficial.** Not an official OpenAI/Codex product. Not affiliated with
> Anthropic, xAI, or Grok. Auth = local `codex login` (`CODEX_HOME`) only.

## Install (token-cheap)

```bash
cd <REPO_PATH>
python -m pip install -e ".[test]"
export CODEX_APP_MCP_ALLOWED_ROOTS="<PROJECT_ROOT>"
export CODEX_APP_MCP_ECONOMY=1
codex login
python -m pytest -q
python scripts/probe_stdio.py
```

Install-only help: skill `install-codex-mcp`.  
Docs: `docs/install/en.md` · VPS: `docs/install/vps.md` · Economy: `docs/economy.md`

## Economy call sequence

| Order | Action | Notes |
|---|---|---|
| 1 | `codex_app_status` or `codex_app_economy` | Once per session |
| 2 | `codex_app_goal` | Tight objective + `tokenBudget` (16k–40k) |
| 3 | Poll job/goal | No full objective replay |
| 4 | Compact / archive threads | When context grows |

Env: `CODEX_APP_MCP_ECONOMY=1`. Playbook tool: `codex_app_economy`.

## Anti-waste rules

- No huge tokenBudget for trivial edits
- No full event dumps into host chat
- Narrow `ALLOWED_ROOTS`
- **Never** put OpenAI/Codex OAuth into `CODEX_APP_MCP_HTTP_TOKEN`
- One active goal; interrupt instead of stacking

## VPS (HTTP native)

```bash
openssl rand -hex 32 > <TOKEN_FILE>
export CODEX_APP_MCP_HTTP_TOKEN_FILE=<TOKEN_FILE>
codex-app-mcp --transport http --host 127.0.0.1 --port 8765
```

TLS reverse proxy → remote Claude / FastMCP with Bearer only.  
`examples/vps.systemd.service` · `examples/http.env.example` · `examples/fastmcp_proxy.py`

## Safe defaults

- Fail-closed roots without `CODEX_APP_MCP_ALLOWED_ROOTS`
- HTTP: loopback + token file; non-loopback requires token
- No app-server WebSocket as public MCP surface
- `ALLOW_FULL_ACCESS` / `ALLOW_UNSAFE_RPC` off unless required

## Operator checklist

1. Install + `codex login`
2. `probe_stdio.py` / optional `probe_http.py`
3. Host: `codex_app_status` → `codex_app_economy` → focused goal
4. Audit paths ACL-protected

## Client pointers

- Install i18n: `docs/install/{en,ru,zh-CN,es}.md`
- Reference: `docs/REFERENCE.md`
- Security: `SECURITY.md`
