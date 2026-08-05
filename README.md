# codex-app-mcp

**Version 0.4.0** — Governed MCP gateway for [OpenAI Codex](https://github.com/openai/codex) **app-server**.

This package turns a single long-lived `codex app-server` process into an MCP
server that other agents and IDEs can call. **Codex remains the backend**;
`codex-app-mcp` is the policy-aware gateway (stdio or bearer HTTP), not a
replacement for the CLI or for ChatGPT.

> **Security one-liner:** no OAuth tokens or API keys belong in this repo.
> Model auth is the local Codex CLI session from `codex login`
> (`CODEX_HOME`). HTTP bearer is an operator-generated local secret only.

## Install guides

| Language | Guide |
|---|---|
| English | [docs/install/en.md](docs/install/en.md) |
| Русский | [docs/install/ru.md](docs/install/ru.md) |
| 简体中文 | [docs/install/zh-CN.md](docs/install/zh-CN.md) |
| Español | [docs/install/es.md](docs/install/es.md) |

Also: [REFERENCE](docs/REFERENCE.md) · [VERIFICATION](docs/VERIFICATION.md) ·
[SECURITY](SECURITY.md) · [CONTRIBUTING](CONTRIBUTING.md) ·
[MIGRATION](docs/MIGRATION.md)

## Capabilities

- Threads, turns, steering, interrupt, fork, archive, and rollback
- Autonomous persisted goals with model, reasoning effort, and token budget
- Native review, command sessions, filesystem v2, and unsandboxed `process/*`
- Approvals, user input, dynamic tools, and server-initiated requests
- Durable SQLite jobs, worktree lanes, and timezone-aware RRULE schedules
- Typed access to methods in the configured experimental app-server schema
- Downstream MCP/SaaS calls with server and tool allowlists
- Stdio MCP and bearer-protected HTTP (no unsupported WebSocket surface)
- Exact-schema introspection, secret-safe audit, metrics, retry, and recovery

There are **20** MCP tools (including `codex_app_status`, `codex_app_thread`,
`codex_app_goal`, `codex_app_lane`, `codex_app_job`, and more). See
[docs/REFERENCE.md](docs/REFERENCE.md).

## Prerequisites

- Python **3.10+**
- **Codex CLI** installed (`codex` on `PATH`, or `CODEX_APP_MCP_BIN`)
- Local auth: **`codex login`** (session under `CODEX_HOME`)

## Quick install

```bash
cd <path-to-repository>
python -m pip install -e ".[test]"
```

Project paths **fail closed** until roots are configured:

```bash
export CODEX_APP_MCP_ALLOWED_ROOTS="/path/to/allowed/project"
```

## Quick run

**Stdio** (default — what most desktop MCP hosts spawn):

```bash
codex-app-mcp
# or:
python -m codex_app_mcp
```

**HTTP** (operator secret, loopback bind):

```bash
export CODEX_APP_MCP_HTTP_TOKEN="$(openssl rand -hex 32)"
codex-app-mcp --transport http --host 127.0.0.1 --port 8765
```

Prefer `CODEX_APP_MCP_HTTP_TOKEN_FILE` for services. The token env var and
token file are mutually exclusive. Never put OpenAI/Codex OAuth into these
settings.

## Client matrix

| Client | Typical mode | Notes |
|---|---|---|
| **Claude Desktop** | stdio | Config JSON with `command` / `env` — [example](examples/claude_desktop.mcp.json) |
| **Claude Code** | stdio | Project/user `.mcp.json` — [example](examples/claude-code.mcp.json) |
| **Cursor** | stdio (or HTTP URL if supported) | [example](examples/cursor.mcp.json) |
| **VS Code / Continue** | stdio | Register the same command in MCP / Continue settings |
| **Remote agents** | HTTP + TLS | Reverse proxy or tunnel; bearer required; not native ChatGPT web stdio |

**ChatGPT web** does not natively host local stdio MCP. Remote connectors, if
any, need a HTTPS endpoint you control—never put OpenAI credentials into the
HTTP bearer config. Full details: [docs/install/en.md](docs/install/en.md).

## First verification

```bash
python -m pytest -q
python scripts/probe_stdio.py
python scripts/probe_http.py
```

In a connected MCP host, call **`codex_app_status`**. Optional deeper checks:
`scripts/check_protocol.py`, `scripts/audit_protocol.py`,
`scripts/probe_full.py`.

## Architecture (short)

```text
MCP client / agent host
        │ stdio or bearer HTTP
        ▼
codex-app-mcp  (policy, jobs, lanes, audit)
        │ JSON-RPC over child stdio
        ▼
codex app-server  (threads, turns, goals, tools)
        │
        ▼
local CODEX_HOME session from `codex login`
```

## Security

- No API keys or OAuth material in the repository or public examples
- Auth to Codex: local CLI session only
- HTTP: operator-generated bearer via `CODEX_APP_MCP_HTTP_TOKEN` or
  `CODEX_APP_MCP_HTTP_TOKEN_FILE` (never commit)
- Project roots fail closed; full access and unsafe RPC are separate opt-ins
- Audit omits prompts, tokens, and credentials

Details and reporting: **[SECURITY.md](SECURITY.md)**.

## Repository layout

```text
codex_app_mcp/   runtime package
docs/            reference, verification, install (i18n)
examples/        placeholder MCP client configs
scripts/         protocol and transport probes
tests/           automated test suite
pyproject.toml   package metadata (version 0.4.0)
```

## Contributing

See **[CONTRIBUTING.md](CONTRIBUTING.md)** for tests, probes, and PR process.

## License

See the repository license file if present; otherwise follow the terms of
the project host.
