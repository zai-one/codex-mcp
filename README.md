# codex-app-mcp

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-see%20repo-lightgrey.svg)](SECURITY.md)
[![MCP](https://img.shields.io/badge/MCP-stdio%20%7C%20HTTP-purple.svg)](https://modelcontextprotocol.io/)
[![Version](https://img.shields.io/badge/version-0.5.0-informational.svg)](pyproject.toml)

**One-line pitch:** Governed MCP gateway for **OpenAI Codex app-server** — Claude / Cursor orchestrate with short prompts; Codex on your machine or VPS runs the long coding loop under policy and token budgets.

> ## ⚠️ Unofficial product disclaimer
>
> **This is a community project.** It is **not** an official product of **OpenAI**, **Codex**, Anthropic, xAI, or Grok. It is not affiliated with, endorsed by, or supported by those companies. Use at your own risk. Auth stays on your machine via the local Codex CLI session (`codex login` → `CODEX_HOME`). **Never** put OpenAI/Codex OAuth or API keys in MCP config or in the HTTP bearer field.

---

## Why this exists (token economy)

| Role | Who pays tokens | What they do |
|---|---|---|
| **Host agent** (Claude, Cursor, …) | Short tool calls | Plan, start goals, poll compact status |
| **Codex app-server** (local / VPS) | Long coding loop | Threads, turns, goals, tools under budget |
| **Operator** | Policy review | Roots, sandbox, merge decisions |

```bash
export CODEX_APP_MCP_ECONOMY=1
```

Call **`codex_app_economy`** once per session for the host playbook. Prefer `codex_app_goal` with a tight objective + `tokenBudget` (e.g. 16k–40k).

Deep dive → [docs/economy.md](docs/economy.md)

---

## Quickstart

```bash
# 1) Install
cd <REPO_PATH>
python -m pip install -e ".[test]"

# 2) Auth (local Codex CLI — not MCP OAuth)
codex login

# 3) Fail-closed roots
export CODEX_APP_MCP_ALLOWED_ROOTS="<PROJECT_ROOT>"

# 4) Stdio (desktop hosts)
codex-app-mcp
# or: python -m codex_app_mcp
```

HTTP is **native** (no extra bridge):

```bash
export CODEX_APP_MCP_HTTP_TOKEN_FILE="<TOKEN_FILE>"
codex-app-mcp --transport http --host 127.0.0.1 --port 8765
```

---

## Install guides (languages)

| Language | Guide |
|---|---|
| English | [docs/install/en.md](docs/install/en.md) |
| Русский | [docs/install/ru.md](docs/install/ru.md) |
| 简体中文 | [docs/install/zh-CN.md](docs/install/zh-CN.md) |
| Español | [docs/install/es.md](docs/install/es.md) |
| FastMCP | [docs/install/fastmcp.md](docs/install/fastmcp.md) |
| VPS | [docs/install/vps.md](docs/install/vps.md) |
| Economy | [docs/economy.md](docs/economy.md) |

Also: [REFERENCE](docs/REFERENCE.md) · [VERIFICATION](docs/VERIFICATION.md) · [SECURITY](SECURITY.md) · [MIGRATION](docs/MIGRATION.md)

---

## Client matrix

| Client | Mode | Notes |
|---|---|---|
| **Claude Desktop** | stdio | [examples/claude_desktop.mcp.json](examples/claude_desktop.mcp.json) |
| **Claude Code** | stdio | [examples/claude-code.mcp.json](examples/claude-code.mcp.json) |
| **Cursor** | stdio or HTTP URL | [examples/cursor.mcp.json](examples/cursor.mcp.json) |
| **VS Code / Continue** | stdio | Same command / env pattern |
| **Remote agents** | HTTPS + bearer | TLS reverse proxy; operator bearer only |
| **FastMCP** | stdio or `create_proxy` | [docs/install/fastmcp.md](docs/install/fastmcp.md) |

### Minimal Claude Desktop fragment

```json
{
  "mcpServers": {
    "codex-app": {
      "command": "codex-app-mcp",
      "args": [],
      "cwd": "<REPO_PATH>",
      "env": {
        "CODEX_APP_MCP_ALLOWED_ROOTS": "<PROJECT_ROOT>",
        "CODEX_APP_MCP_ECONOMY": "1"
      }
    }
  }
}
```

---

## VPS one-liner (HTTP already native)

```bash
openssl rand -hex 32 > <TOKEN_FILE>
export CODEX_APP_MCP_HTTP_TOKEN_FILE="<TOKEN_FILE>"
export CODEX_APP_MCP_ALLOWED_ROOTS="<PROJECT_ROOT>"
codex-app-mcp --transport http --host 127.0.0.1 --port 8765
# TLS reverse proxy → https://mcp.example.invalid/mcp
# Authorization: Bearer <operator-secret>  — NOT OpenAI OAuth
```

Full guide → [docs/install/vps.md](docs/install/vps.md) · unit → [examples/vps.systemd.service](examples/vps.systemd.service) · env → [examples/http.env.example](examples/http.env.example)

---

## FastMCP

Local: spawn `codex-app-mcp` as stdio. Remote: FastMCP `create_proxy` against your HTTPS `/mcp` with bearer headers — [examples/fastmcp_proxy.py](examples/fastmcp_proxy.py).

---

## Capabilities

- Threads, turns, steering, interrupt, fork, archive, rollback
- Autonomous goals with model, reasoning effort, and **token budget**
- Native review, command sessions, filesystem v2, `process/*` (gated)
- Durable SQLite jobs, worktree lanes, RRULE schedules
- Downstream MCP/SaaS allowlists
- **stdio MCP** and **bearer HTTP** (no unsupported WebSocket MCP surface)
- Economy tool: **`codex_app_economy`**

~20 MCP tools — see [docs/REFERENCE.md](docs/REFERENCE.md).

---

## Architecture

```text
MCP client / host agent (Claude, Cursor, …)
        │  stdio  or  HTTPS + Bearer
        ▼
codex-app-mcp  (policy, jobs, lanes, audit, economy)
        │  JSON-RPC child stdio
        ▼
codex app-server
        │
        ▼
local CODEX_HOME session from `codex login`
```

---

## Security (no OAuth in MCP config)

| Do | Don't |
|---|---|
| `codex login` on the machine | Paste OpenAI API keys into MCP JSON |
| Operator CSPRNG bearer for HTTP | Use OAuth token as `HTTP_TOKEN` |
| Tight `ALLOWED_ROOTS` | Commit token files or personal paths |
| TLS reverse proxy for remote | Expose raw unauthenticated LAN ports |

See [SECURITY.md](SECURITY.md).

---

## First verification

```bash
python -m pytest -q
python scripts/probe_stdio.py
python scripts/probe_http.py   # with token set
```

Host tool: **`codex_app_status`**, then **`codex_app_economy`**.

## License

See the repository license file if present; otherwise follow the terms of the project host.
