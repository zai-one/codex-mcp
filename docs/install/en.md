# Install and connect (English)

Complete guide to install **codex-app-mcp**, run it over stdio or HTTP, and
connect popular MCP hosts. Version **0.4.0**.

Other languages: [Русский](ru.md) · [简体中文](zh-CN.md) · [Español](es.md)

## What this package is

`codex-app-mcp` is a **governed MCP gateway** for other agents and IDEs. It is
**not** a replacement for Codex itself.

| Layer | Role |
|---|---|
| **Codex CLI + app-server** | Backend: models, threads, turns, goals, sandbox |
| **codex-app-mcp** | MCP surface (stdio / bearer HTTP) with policy, jobs, lanes |
| **MCP host** | Claude Desktop, Claude Code, Cursor, VS Code, Continue, remote agents |

Auth to models is always the **local Codex CLI session** (`codex login` →
`CODEX_HOME`). This gateway never wants your OpenAI API key or ChatGPT OAuth
pasted into MCP JSON.

## Prerequisites

1. **Python 3.10+** (`python3 --version` or `py -3 --version`)
2. **Codex CLI** installed and on `PATH` (or set `CODEX_APP_MCP_BIN`)
3. **Authenticated Codex session**: run `codex login` on the same machine
   (and same user account) that will run the gateway
4. A clone of this repository (or an installed wheel of the package)

Verify Codex:

```bash
codex --version
# complete interactive login if needed
codex login
```

## Install

From the repository root:

```bash
cd <path-to-repository>
python -m pip install -e ".[test]"
```

Windows (PowerShell):

```powershell
Set-Location "<path-to-repository>"
py -3 -m pip install -e ".[test]"
```

This installs the console entrypoint `codex-app-mcp` and the module
`codex_app_mcp`.

## Configure project roots (required for project work)

Roots **fail closed**. Until you set allowlisted project paths, cwd / repo
operations are rejected.

```bash
export CODEX_APP_MCP_ALLOWED_ROOTS="/path/to/allowed/project;/path/to/other/project"
```

PowerShell:

```powershell
$env:CODEX_APP_MCP_ALLOWED_ROOTS = "D:\Projects\example;D:\Work\example"
```

Optional full-access profile (trusted local machine only):

```bash
export CODEX_APP_MCP_ALLOW_FULL_ACCESS=1
export CODEX_APP_MCP_DEFAULT_SANDBOX=danger-full-access
export CODEX_APP_MCP_DEFAULT_APPROVAL_POLICY=never
```

## Run: stdio MCP

Default transport is stdio (what most desktop MCP clients spawn):

```bash
codex-app-mcp
# equivalent:
python -m codex_app_mcp
```

The process speaks MCP JSON-RPC on stdin/stdout. Keep stderr free for logs;
do not wrap the command in shells that mix protocol bytes with prompts.

## Run: HTTP MCP (bearer)

Generate an **operator-local** secret (never an OpenAI/Codex OAuth token):

```bash
export CODEX_APP_MCP_HTTP_TOKEN="$(openssl rand -hex 32)"
codex-app-mcp --transport http --host 127.0.0.1 --port 8765
```

Prefer a token file for services (mutually exclusive with the env value):

```bash
# write one line: the random secret (file outside git)
export CODEX_APP_MCP_HTTP_TOKEN_FILE="/path/to/codex-app-mcp.token"
codex-app-mcp --transport http --host 127.0.0.1 --port 8765
```

Endpoints:

| Method | Path | Auth | Meaning |
|---|---|---|---|
| `GET` | `/healthz` | no | process liveness |
| `GET` | `/readyz` | bearer | starts/probes app-server |
| `POST` | `/mcp` | bearer | MCP JSON-RPC |
| `GET` | `/mcp` | — | not supported (no unsolicited SSE) |

Bind **127.0.0.1** by default. Non-loopback binds require a token; put
remote access behind TLS reverse proxy or SSH/VPN tunnel.

Call example:

```bash
curl -sS \
  -H "Authorization: Bearer <long-random-secret>" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}' \
  http://127.0.0.1:8765/mcp
```

## Claude Desktop

Config location (typical; adjust for your OS):

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`
- Linux: `~/.config/Claude/claude_desktop_config.json`

Example (placeholders only) — also in
[`examples/claude_desktop.mcp.json`](../../examples/claude_desktop.mcp.json):

```json
{
  "mcpServers": {
    "codex-app": {
      "command": "codex-app-mcp",
      "args": [],
      "cwd": "<path-to-repository>",
      "env": {
        "CODEX_APP_MCP_ALLOWED_ROOTS": "/path/to/allowed/project",
        "CODEX_APP_MCP_ALLOW_FULL_ACCESS": "0"
      }
    }
  }
}
```

If `codex-app-mcp` is not on `PATH`, use the Python module form:

```json
{
  "mcpServers": {
    "codex-app": {
      "command": "<path-to-python>",
      "args": ["-m", "codex_app_mcp"],
      "cwd": "<path-to-repository>",
      "env": {
        "CODEX_APP_MCP_ALLOWED_ROOTS": "/path/to/allowed/project"
      }
    }
  }
}
```

Restart Claude Desktop after editing config. Confirm tools such as
`codex_app_status` appear in the MCP tool list.

## Claude Code (`.mcp.json`)

Project or user MCP config for Claude Code — see
[`examples/claude-code.mcp.json`](../../examples/claude-code.mcp.json):

```json
{
  "mcpServers": {
    "codex-app": {
      "type": "stdio",
      "command": "codex-app-mcp",
      "args": [],
      "env": {
        "CODEX_APP_MCP_ALLOWED_ROOTS": "/path/to/allowed/project"
      }
    }
  }
}
```

Place as `.mcp.json` in the project root or merge into your Claude Code MCP
settings. Ensure the process runs as the user that owns the `codex login`
session.

## Cursor (`mcp.json`)

Cursor MCP configuration (user or project). Example —
[`examples/cursor.mcp.json`](../../examples/cursor.mcp.json):

```json
{
  "mcpServers": {
    "codex-app": {
      "command": "codex-app-mcp",
      "args": [],
      "env": {
        "CODEX_APP_MCP_ALLOWED_ROOTS": "/path/to/allowed/project"
      }
    }
  }
}
```

For HTTP from Cursor (if your Cursor build supports URL MCP servers):

```json
{
  "mcpServers": {
    "codex-app-http": {
      "url": "http://127.0.0.1:8765/mcp",
      "headers": {
        "Authorization": "Bearer <long-random-secret>"
      }
    }
  }
}
```

Never put OpenAI or Codex OAuth tokens in `headers`; only the local HTTP
bearer you generated for this gateway.

## VS Code / Continue

### VS Code (MCP-capable builds)

Use the editor's MCP server settings (shape varies by VS Code MCP extension
or built-in support). Stdio form:

```json
{
  "servers": {
    "codex-app": {
      "type": "stdio",
      "command": "codex-app-mcp",
      "args": [],
      "env": {
        "CODEX_APP_MCP_ALLOWED_ROOTS": "/path/to/allowed/project"
      }
    }
  }
}
```

### Continue

In Continue config (for example `config.yaml` / MCP section, depending on
Continue version), register a stdio MCP server with the same `command` /
`args` / `env` as above. Point working directories at allowlisted roots only.

## ChatGPT (web) and remote agents

**ChatGPT web does not natively host local stdio MCP** the way Claude Desktop
or Cursor do. Local `codex-app-mcp` on your laptop is not automatically
available inside chatgpt.com.

If a product path supports **remote MCP connectors**:

1. Run the gateway on a host with Codex authenticated (`codex login`).
2. Expose **only** the HTTP MCP endpoint through a **TLS reverse proxy** or
   private tunnel (SSH, VPN, mesh).
3. Protect with the operator bearer
   (`CODEX_APP_MCP_HTTP_TOKEN` or `CODEX_APP_MCP_HTTP_TOKEN_FILE`).
4. **Never** put OpenAI API keys or Codex/ChatGPT OAuth into the HTTP token
   configuration. Those remain on the server as the local CLI session.
5. Keep `CODEX_APP_MCP_ALLOWED_ROOTS` and unsafe/full-access gates tight on
   the remote host.

Any ChatGPT connector (if offered by the product) must point at a
**remote HTTPS** endpoint you control, not a raw unauthenticated LAN port.

## First verification

From the repository root, with install and `codex login` complete:

```bash
# Automated tests
python -m pytest -q

# Stdio transport + app-server smoke
python scripts/probe_stdio.py

# HTTP bearer smoke
python scripts/probe_http.py
```

Inside an MCP client, call **`codex_app_status`** (and optionally
`codex_app_doctor`). Expect connection/PID/version and effective policy.
If status fails, fix Codex CLI auth and `CODEX_APP_MCP_BIN` first.

Optional deeper checks:

```bash
python scripts/check_protocol.py
python scripts/audit_protocol.py
python scripts/probe_full.py
```

## Environment variables

All values below are **defaults or placeholders**. Do not commit real
secrets. See also [`.env.example`](../../.env.example) and
[`examples/http.env.example`](../../examples/http.env.example).

| Variable | Default | Description |
|---|---|---|
| `CODEX_APP_MCP_BIN` | `codex` | Codex binary providing `app-server` |
| `CODEX_HOME` | user Codex home | Auth, config, threads for app-server |
| `CODEX_APP_MCP_ALLOWED_ROOTS` | empty | `;` list or JSON array of allowed project roots; empty = fail closed |
| `CODEX_APP_MCP_ALLOW_FULL_ACCESS` | `0` | Allow `danger-full-access` and experimental `process/*` |
| `CODEX_APP_MCP_DEFAULT_SANDBOX` | unset | `read-only` / `workspace-write` / `danger-full-access` |
| `CODEX_APP_MCP_DEFAULT_APPROVAL_POLICY` | unset | `untrusted` / `on-request` / `never` |
| `CODEX_APP_MCP_ALLOW_UNSAFE_RPC` | `0` | Gate for stateful admin / raw mutations |
| `CODEX_APP_MCP_ALLOWED_RPC_METHODS` | empty | `;` allowlist or `*` (trusted local only) |
| `CODEX_APP_MCP_ALLOWED_SERVERS` | empty | Downstream MCP server allowlist |
| `CODEX_APP_MCP_ALLOWED_TOOLS` | empty | Downstream tool or `server/tool` allowlist |
| `CODEX_APP_MCP_ALLOWED_CONFIG_KEYS` | empty | Non-security thread config keys |
| `CODEX_APP_MCP_CONFIG_OVERRIDES` | empty | App-server launch config overrides |
| `CODEX_APP_MCP_REQUEST_TIMEOUT_SECONDS` | `30` | Ordinary RPC timeout |
| `CODEX_APP_MCP_OPERATION_TIMEOUT_SECONDS` | `180` | Long operation timeout |
| `CODEX_APP_MCP_ACTION_TIMEOUT_SECONDS` | `60` | Pending server-request timeout |
| `CODEX_APP_MCP_STATE_PATH` | under `CODEX_HOME` | Durable jobs SQLite path |
| `CODEX_APP_MCP_SCHEDULER_ENABLED` | `1` | Autostart durable schedules |
| `CODEX_APP_MCP_SCHEDULER_POLL_SECONDS` | `1` | Scheduler poll interval |
| `CODEX_APP_MCP_PROTOCOL_CACHE_SECONDS` | `300` | Exact schema cache TTL |
| `CODEX_APP_MCP_AUDIT` | `1` | Secret-safe audit on/off |
| `CODEX_APP_MCP_AUDIT_PATH` | stderr | Optional JSONL audit file |
| `CODEX_APP_MCP_PRINCIPAL` | `local` | Audit principal label |
| `CODEX_APP_MCP_TRANSPORT` | `stdio` | `stdio` or `http` |
| `CODEX_APP_MCP_HTTP_HOST` | `127.0.0.1` | HTTP bind address |
| `CODEX_APP_MCP_HTTP_PORT` | `8765` | HTTP port |
| `CODEX_APP_MCP_HTTP_TOKEN` | empty | Bearer secret (env); **not** OpenAI OAuth |
| `CODEX_APP_MCP_HTTP_TOKEN_FILE` | empty | Bearer secret file path; exclusive with token env |
| `CODEX_APP_MCP_HTTP_MAX_INFLIGHT` | `16` | Concurrent HTTP MCP requests (1–256) |
| `CODEX_APP_MCP_HTTP_ALLOWED_ORIGINS` | empty | `;` list of exact browser Origins |
| `CODEX_APP_MCP_LANES_PARENT` | unset | Parent directory for worktree lanes |

## Why no WebSocket

The official Codex app-server **WebSocket** listener is experimental /
unsupported as a public service endpoint. This package deliberately exposes:

- **stdio** for local MCP hosts, and
- **HTTP** (`POST /mcp` with bearer) for remote control planes.

There is **no** supported WebSocket MCP transport here, and no unsolicited
SSE stream on `GET /mcp`. Poll events with the `codex_app_events` tool.

## Security checklist

- [ ] Repository and examples contain **no** API keys, OAuth tokens, or
      personal paths
- [ ] Host has `codex login`; gateway uses local `CODEX_HOME` only
- [ ] `CODEX_APP_MCP_ALLOWED_ROOTS` set; leave empty only if you intend
      fail-closed project denial
- [ ] `ALLOW_FULL_ACCESS` / `ALLOW_UNSAFE_RPC` off unless required
- [ ] HTTP on `127.0.0.1` with a CSPRNG bearer, or TLS proxy + bearer
- [ ] `CODEX_APP_MCP_HTTP_TOKEN` / `TOKEN_FILE` never committed
- [ ] HTTP bearer is **not** an OpenAI or Codex OAuth credential
- [ ] Separate token, `CODEX_HOME`, and SQLite state per tenant/service
- [ ] Audit and state files ACL-protected

Full policy: [SECURITY.md](../../SECURITY.md).

## Next reading

- [Reference](../REFERENCE.md) — tools, architecture, env details
- [Verification](../VERIFICATION.md) — test and probe ledger
- [Migration](../MIGRATION.md) — from removed `codex_delegate`
- [Contributing](../../CONTRIBUTING.md)
- [Security](../../SECURITY.md)
