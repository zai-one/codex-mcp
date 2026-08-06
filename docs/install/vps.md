# VPS guide (English)

> ## ⚠️ Unofficial product disclaimer
>
> **Not** an official product of OpenAI, Codex, Anthropic, xAI, or Grok.
> Community software only. HTTP uses **operator bearer**, not OAuth.

## Goal

Codex authenticated on the VPS; `codex-app-mcp` serves **native HTTP MCP**
behind TLS. Remote Claude / FastMCP / Cursor connect with bearer only.

## Steps

### 1. CLI auth on VPS

```bash
codex login
codex --version
```

### 2. Install gateway

```bash
cd <REPO_PATH>
python -m pip install -e .
export CODEX_APP_MCP_ALLOWED_ROOTS="<PROJECT_ROOT>"
export CODEX_APP_MCP_ECONOMY=1
```

### 3. Bearer (not OpenAI OAuth)

```bash
openssl rand -hex 32 > <TOKEN_FILE>
chmod 600 <TOKEN_FILE>
export CODEX_APP_MCP_HTTP_TOKEN_FILE="<TOKEN_FILE>"
```

### 4. Run HTTP

```bash
codex-app-mcp --transport http --host 127.0.0.1 --port 8765
```

| Method | Path | Auth |
|---|---|---|
| `GET` | `/healthz` | no |
| `GET` | `/readyz` | bearer |
| `POST` | `/mcp` | bearer |

Systemd: [examples/vps.systemd.service](../../examples/vps.systemd.service)  
Env: [examples/http.env.example](../../examples/http.env.example)

### 5. TLS reverse proxy

Proxy `https://mcp.example.invalid` → `http://127.0.0.1:8765`.  
Do not open `:8765` on the public interface.

### 6. Connect

```bash
curl -sS \
  -H "Authorization: Bearer <OPERATOR_BEARER>" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}' \
  https://mcp.example.invalid/mcp
```

## Checklist

- [ ] `codex login` as the service user
- [ ] Roots + economy configured
- [ ] CSPRNG bearer in a file outside git
- [ ] Loopback bind + TLS proxy only
- [ ] Bearer ≠ OpenAI/Codex OAuth
- [ ] Per-tenant token / `CODEX_HOME` / SQLite when multi-tenant

## Related

- [en.md](en.md) · [economy.md](../economy.md) · [SECURITY.md](../../SECURITY.md)
