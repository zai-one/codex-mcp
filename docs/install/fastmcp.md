# FastMCP guide (English)

> ## ⚠️ Unofficial product disclaimer
>
> **Not** an official product of OpenAI, Codex, Anthropic, xAI, or Grok.
> Community software only. **No OAuth in MCP config.**

## Paths

| Path | Command / target |
|---|---|
| **Local stdio** | `codex-app-mcp` or `python -m codex_app_mcp` |
| **Remote proxy** | HTTPS `…/mcp` with `Authorization: Bearer <OPERATOR_BEARER>` |

## Local stdio

```bash
export CODEX_APP_MCP_ALLOWED_ROOTS="<PROJECT_ROOT>"
export CODEX_APP_MCP_ECONOMY=1
codex-app-mcp
```

Register that command in FastMCP / your host with non-secret env only.

## Remote proxy

1. VPS: `codex login`, HTTP MCP + bearer ([vps.md](vps.md)).
2. TLS reverse proxy to `127.0.0.1:8765`.
3. Local FastMCP proxy — [examples/fastmcp_proxy.py](../../examples/fastmcp_proxy.py).

```python
# Conceptual placeholders — adapt to your FastMCP version
# create_proxy(
#   url="https://mcp.example.invalid/mcp",
#   headers={"Authorization": "Bearer <OPERATOR_BEARER>"},
# )
# NEVER use OpenAI/Codex OAuth as the bearer.
```

## Security

- Bearer from `openssl rand -hex 32` → `CODEX_APP_MCP_HTTP_TOKEN_FILE`
- Mutually exclusive with `CODEX_APP_MCP_HTTP_TOKEN`
- No API keys or OAuth in MCP JSON

## Related

- [vps.md](vps.md) · [economy.md](../economy.md) · [en.md](en.md)
