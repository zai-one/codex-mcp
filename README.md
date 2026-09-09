# Codex app-server MCP

[English](README.md) · [Русский](README.ru.md)

**Run and follow Codex coding tasks from your MCP client.**

Start work, steer a task, review changes and return to persisted goals through one
connection. This gateway runs a persistent `codex app-server` process and exposes
its task, review, command and scheduling controls to your assistant.

Install and sign in to Codex first: its `codex` binary provides the runtime.
Available methods follow your installed version's protocol. Start with explicit
project directories, then choose the extra permissions your workflow needs.

## Capabilities

- threads, turns, steering, interrupt, fork, archive and rollback;
- autonomous persisted goals with model, reasoning effort and token budget;
- native review, command sessions, filesystem v2 and unsandboxed `process/*`;
- approvals, user input, dynamic tools and server-initiated requests;
- durable SQLite jobs, worktree lanes and timezone-aware RRULE schedules;
- typed access to all methods in the configured experimental schema;
- downstream MCP/SaaS calls with server and tool allowlists;
- stdio MCP and bearer-protected HTTP;
- exact-schema introspection, audit, metrics, retry and recovery.

## Repository

```text
codex_app_mcp/   runtime package
docs/            reference, migration, research and verification
scripts/         repeatable protocol and transport probes
tests/           automated test suite
pyproject.toml   package and entrypoint metadata
```

## Install

Install and sign in to Codex, then clone this repository. Windows PowerShell:

```powershell
git clone https://github.com/zai-one/codex-mcp.git
cd codex-mcp
py -3 -m pip install -e ".[test]"
```

macOS / Linux:

```sh
git clone https://github.com/zai-one/codex-mcp.git
cd codex-mcp
python3 -m pip install -e ".[test]"
```

Project paths fail closed until roots are configured:

```powershell
$env:CODEX_APP_MCP_ALLOWED_ROOTS = "D:\Projects;D:\Work"
```

## Run

```powershell
# MCP stdio
codex-app-mcp

# MCP over HTTP
$env:CODEX_APP_MCP_HTTP_TOKEN = "<long-random-secret>"
codex-app-mcp --transport http --host 127.0.0.1 --port 8765
```

For a service deployment, use `CODEX_APP_MCP_HTTP_TOKEN_FILE` instead of
putting the bearer token directly in the process environment. The two token
settings are mutually exclusive.

## Connect your assistant

Merge this entry into your MCP client's configuration. Replace the project path;
use an absolute path to `codex-app-mcp` if it is not on the client's PATH.
This example uses Windows paths; on macOS/Linux use your project's absolute path.

```json
{
  "mcpServers": {
    "codex": {
      "command": "codex-app-mcp",
      "env": {"CODEX_APP_MCP_ALLOWED_ROOTS": "D:\\Projects\\my-app"}
    }
  }
}
```

Restart the connection, then ask:

> Show the available Codex capabilities and current tasks. Do not start any work yet.

`codex_app_status` reports connection and effective policy. Configure additional
permissions only when a workflow needs them; the [reference](docs/REFERENCE.md)
explains the controls and the installed-binary protocol audit.

<details>
<summary>Advanced: trusted host access and administrative RPC (Windows PowerShell)</summary>

Full host access is explicit:

```powershell
$env:CODEX_APP_MCP_ALLOW_FULL_ACCESS = "1"
$env:CODEX_APP_MCP_DEFAULT_SANDBOX = "danger-full-access"
$env:CODEX_APP_MCP_DEFAULT_APPROVAL_POLICY = "never"
```

State-changing administrative and raw RPC calls use a separate gate:

```powershell
$env:CODEX_APP_MCP_ALLOW_UNSAFE_RPC = "1"
$env:CODEX_APP_MCP_ALLOWED_RPC_METHODS = "*"
```

Use a narrow method allowlist instead of `*` outside a trusted local control
plane.

</details>

## Verify

```powershell
py -3 -m pytest -q
py -3 -m compileall -q codex_app_mcp scripts tests
py -3 scripts\check_protocol.py
py -3 scripts\audit_protocol.py
py -3 scripts\probe_stdio.py
py -3 scripts\probe_http.py
```

The full probe mutates only its own temporary repositories:

```powershell
py -3 scripts\probe_full.py
```

The optional persisted-goal probe may consume model usage:

```powershell
py -3 scripts\probe_goal.py --goal
```

## Documentation

- [Reference](docs/REFERENCE.md)
- [Migration](docs/MIGRATION.md)
- [Research](docs/RESEARCH.md)
- [Verification](docs/VERIFICATION.md)

The protocol catalog is generated from the configured binary, so an older
Codex build cannot be mistaken for one that supports a future method.

## Built by ZAI.ONE

[ZAI.ONE](https://zai.one) is a digital agency working on websites, SEO, advertising and analytics. We also build tools that connect AI assistants to everyday work. [Talk to us on Telegram](https://t.me/zai_one) about setup, automation or an integration for your team.

For bugs and feature requests, [open an issue](https://github.com/zai-one/codex-mcp/issues). If the project helps, give it a ⭐.
