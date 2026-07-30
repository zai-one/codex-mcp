# Codex app-server MCP

Production-oriented MCP gateway for managing Codex through one persistent
`codex app-server` process. The former `codex exec`/`codex_delegate` runtime is
not part of this repository.

The installed `codex` binary remains a runtime dependency because it provides
`codex app-server`.

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

```powershell
Set-Location "<path-to-repository>"
py -3 -m pip install -e ".[test]"
```

Project paths fail closed until roots are configured:

```powershell
$env:CODEX_APP_MCP_ALLOWED_ROOTS = "D:\Projects;D:\Work"
```

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

## Run

```powershell
# MCP stdio
codex-app-mcp

# MCP over HTTP
$env:CODEX_APP_MCP_HTTP_TOKEN = "<long-random-secret>"
codex-app-mcp --transport http --host 127.0.0.1 --port 8765
```

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
