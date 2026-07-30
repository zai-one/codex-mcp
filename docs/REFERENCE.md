# Codex app-server MCP reference

Production-oriented MCP gateway over the experimental
`codex app-server` JSON-RPC protocol. It uses one long-lived app-server child,
not `codex exec`.

Verified on Windows with `codex-cli 0.145.0` and `0.146.0` on 29 July 2026.
Run the schema audit after every Codex update.

## Architecture

```text
MCP client / SaaS / agent service
        │ stdio or authenticated HTTP
        ▼
codex-app-mcp
  ├─ roots / sandbox / RPC / downstream-MCP policy
  ├─ typed app-server adapter
  ├─ exact binary schema registry
  ├─ event + server-request router
  ├─ SQLite durable jobs, schedules, retries and per-lane exclusion
  └─ secret-safe JSONL audit
        │ JSONL JSON-RPC
        ▼
codex app-server
  ├─ threads / turns / goals / review
  ├─ command / fs / process
  └─ configured MCP / apps / plugins / skills
```

## Install

```powershell
Set-Location "<path-to-repository>"
py -3 -m pip install -e ".[test]"
```

## Sandbox and approvals

Full host access is supported and was live-tested:

```powershell
$env:CODEX_APP_MCP_ALLOWED_ROOTS = "D:\Projects;D:\Work"
$env:CODEX_APP_MCP_ALLOW_FULL_ACCESS = "1"
$env:CODEX_APP_MCP_DEFAULT_SANDBOX = "danger-full-access"
$env:CODEX_APP_MCP_DEFAULT_APPROVAL_POLICY = "never"
```

The caller can override the default per `thread`, `turn`, `lane`, `review`, or
`command` request:

```json
{
  "sandbox": "danger-full-access",
  "approvalPolicy": "never"
}
```

`danger-full-access` becomes app-server
`sandboxPolicy.type=dangerFullAccess`. Experimental `process/*` is explicitly
unsandboxed and requires the same full-access opt-in.

Filesystem mutations, thread deletion, and arbitrary stateful RPC require a
second, independent gate:

```powershell
$env:CODEX_APP_MCP_ALLOW_UNSAFE_RPC = "1"
$env:CODEX_APP_MCP_ALLOWED_RPC_METHODS = "thread/delete;config/value/write"
```

`CODEX_APP_MCP_ALLOWED_RPC_METHODS=*` exposes every non-initialize app-server
method. Use `*` only inside a trusted local control plane.

## Stdio MCP

```json
{
  "mcpServers": {
    "codex-app": {
      "command": "C:\\Python\\python.exe",
      "args": ["-m", "codex_app_mcp"],
      "cwd": "C:\\path\\to\\codex-app-mcp",
      "env": {
        "CODEX_APP_MCP_ALLOWED_ROOTS": "D:\\Projects;D:\\Work",
        "CODEX_APP_MCP_ALLOW_FULL_ACCESS": "1",
        "CODEX_APP_MCP_DEFAULT_SANDBOX": "danger-full-access",
        "CODEX_APP_MCP_DEFAULT_APPROVAL_POLICY": "never"
      }
    }
  }
}
```

## HTTP service

```powershell
$env:CODEX_APP_MCP_HTTP_TOKEN = "<long-random-secret>"
py -3 -m codex_app_mcp --transport http --host 127.0.0.1 --port 8765
```

- `GET /healthz` is process liveness.
- `GET /readyz` requires bearer auth and starts/probes app-server.
- `POST /mcp` accepts JSON-RPC MCP requests.
- non-loopback bind is refused without a token;
- put non-loopback deployments behind TLS;
- exact browser Origins require `CODEX_APP_MCP_HTTP_ALLOWED_ORIGINS`.

Legacy MCP versions `2024-11-05` through `2025-11-25` negotiate through
initialize. The current stateless contract supports `2026-07-28` request
headers and `_meta`. Events are retrieved with `codex_app_events`; this
transport intentionally has no unsolicited SSE stream.

## MCP tools

There are 20 tools:

| Tool | Operations |
|---|---|
| `codex_app_status` | connection/PID/version/effective policy/audit status |
| `codex_app_doctor` | app-server, account, config requirements, Windows readiness |
| `codex_app_discover` | models, efforts, modes, features, permission profiles, MCP, apps, plugins, skills, hooks, account |
| `codex_app_thread` | start/list/read/resume/fork/name/compact/archive/unarchive/unsubscribe/loaded/turns/items/delete/shell/metadata/rollback/inject/background terminals |
| `codex_app_goal` | start/set/get/clear/pause/resume/complete and token budget |
| `codex_app_turn` | start/steer/interrupt, images, output schema, Plan/Default |
| `codex_app_review` | uncommitted/base branch/commit/custom, inline or detached |
| `codex_app_command` | exec/write/resize/terminate controllable sessions |
| `codex_app_process` | source-main experimental unsandboxed spawn/write/resize/kill |
| `codex_app_fs` | read/write/mkdir/metadata/list/remove/copy/watch/unwatch |
| `codex_app_events` | cursor polling, pending actions, approvals/input responses |
| `codex_app_job` | durable goal/turn start/get/list/cancel/resume |
| `codex_app_lane` | prepare/list/diff/start/run/poll/review git worktree lanes |
| `codex_app_mcp_call` | allowlisted downstream MCP/SaaS tool call |
| `codex_app_protocol` | exact generated schema summary/method lookup/refresh |
| `codex_app_admin` | typed account/config/extensions/environment/search/memory/realtime/remote operations |
| `codex_app_schedule` | durable RRULE create/get/list/pause/resume/delete/trigger/runs |
| `codex_app_runtime` | counters and explicit governed app-server restart |
| `codex_app_rpc_read` | built-in read-only protocol escape hatch |
| `codex_app_rpc` | operator-allowlisted forward-compatible raw RPC |

### Lanes

`codex_app_lane` replaces the removed `codex_delegate` runtime. A full-access
background coding task:

```json
{
  "action": "start",
  "repoRoot": "D:\\Projects\\service",
  "lane": "feature-x",
  "goal": "Реализуй задачу, запусти тесты и сообщи точный результат",
  "model": "gpt-5.6-sol",
  "effort": "high",
  "sandbox": "danger-full-access",
  "approvalPolicy": "never"
}
```

The result immediately contains `threadId`, `jobId`, branch, and worktree.
Poll with `action=poll`. Terminal poll results include changed files and
diffstat. `planOnly=true` forces Plan mode and read-only sandbox.

Only one active job can own a lane. SQLite enforces this with a partial unique
index, including concurrent callers.

### Persisted goals

A goal is an app-server runtime loop, not a single turn:

```json
{
  "action": "start",
  "kind": "goal",
  "request": {
    "cwd": "D:\\Projects\\service",
    "objective": "Finish the task, verify it, then call update_goal complete",
    "tokenBudget": 80000,
    "model": "gpt-5.6-sol",
    "effort": "high",
    "sandbox": "danger-full-access",
    "approvalPolicy": "never"
  }
}
```

Terminal goal statuses are `complete`, `paused`, `blocked`, `usageLimited`,
and `budgetLimited`. A prose answer saying “done” is not completion; the
runtime must set goal status to `complete`.

Jobs persist request/result/thread/turn/history in SQLite. The ledger can
contain prompts and should be protected by service-account ACLs. Active jobs
found after a restart become explicit `orphaned` records; goal resume is an
operator action.

### Schedules

`codex_app_schedule` provides recurrence because app-server does not expose
the Desktop Scheduled management UI:

```json
{
  "action": "create",
  "idempotencyKey": "service-daily-check-v1",
  "name": "Daily service check",
  "timezone": "Europe/Moscow",
  "rrule": "RRULE:FREQ=DAILY;BYHOUR=9;BYMINUTE=0",
  "request": {
    "kind": "goal",
    "request": {
      "cwd": "D:\\Projects\\service",
      "objective": "Проверь проект, исправь подтверждённые дефекты и заверши цель",
      "model": "gpt-5.6-sol",
      "effort": "high",
      "sandbox": "danger-full-access",
      "approvalPolicy": "never"
    }
  },
  "retryCount": 3,
  "retryBackoffSeconds": 30,
  "misfirePolicy": "run_once"
}
```

Supported RRULE frequencies are `ONCE`, `MINUTELY`, `HOURLY`, `DAILY`,
`WEEKLY`, and `MONTHLY`, with `INTERVAL`, `BYDAY`, `BYMONTHDAY`, `BYHOUR`,
`BYMINUTE`, and UTC `UNTIL`. Timezones are IANA names; Windows installs the
package `tzdata`. The SQLite ledger guarantees one claim per
`scheduleId + scheduledFor`, one active run per schedule, idempotent creation,
exponential retry backoff, and explicit `run_once`/`skip` misfire behavior.

### Server requests and dynamic tools

All official server requests are surfaced as pending actions, including:

- command/file/permission approvals;
- user input;
- dynamic `item/tool/call`;
- MCP elicitation;
- auth-token refresh, attestation, and legacy approvals.

Use `codex_app_events action=pending` and `action=respond`. Nothing is silently
approved. Timed-out actions are declined. The safe source-main
`currentTime/read` request is answered automatically with the current Unix
timestamp so an external-clock turn does not stall.

### SaaS

Downstream tools require both allowlists:

```powershell
$env:CODEX_APP_MCP_ALLOWED_SERVERS = "github;crm"
$env:CODEX_APP_MCP_ALLOWED_TOOLS = "github/get_issue;crm/read_account"
```

The configured app-server `CODEX_HOME` determines which MCP servers/apps/
plugins actually exist. A service deployment should use a dedicated
`CODEX_HOME` containing only its needed integrations.

## Audit

Audit is JSONL to stderr by default:

```powershell
$env:CODEX_APP_MCP_AUDIT_PATH = "D:\ServiceState\codex-app-mcp.audit.jsonl"
$env:CODEX_APP_MCP_PRINCIPAL = "agent-control-plane"
```

Only scalar metadata is emitted: tool/action/outcome, IDs, lane, sandbox,
cwd, duration, and goal length/hash. Prompts, responses, patches, commands,
arguments, env, tokens, and credentials are never included. Set
`CODEX_APP_MCP_AUDIT=0` to disable.

## Environment

| Variable | Default | Meaning |
|---|---:|---|
| `CODEX_APP_MCP_BIN` | `codex` | Codex binary that provides app-server |
| `CODEX_HOME` | user profile | Codex auth/config/thread store |
| `CODEX_APP_MCP_ALLOWED_ROOTS` | empty | `;` list or JSON array; project work fails closed when empty |
| `CODEX_APP_MCP_ALLOW_FULL_ACCESS` | `0` | allow `danger-full-access` and `process/*` |
| `CODEX_APP_MCP_DEFAULT_SANDBOX` | unset | read-only/workspace-write/danger-full-access default |
| `CODEX_APP_MCP_DEFAULT_APPROVAL_POLICY` | unset | untrusted/on-request/never default |
| `CODEX_APP_MCP_ALLOW_UNSAFE_RPC` | `0` | mutations/raw stateful calls |
| `CODEX_APP_MCP_ALLOWED_RPC_METHODS` | empty | `;` allowlist or `*` |
| `CODEX_APP_MCP_ALLOWED_SERVERS` | empty | downstream MCP server allowlist |
| `CODEX_APP_MCP_ALLOWED_TOOLS` | empty | tool or `server/tool` allowlist |
| `CODEX_APP_MCP_ALLOWED_CONFIG_KEYS` | empty | non-security thread config keys |
| `CODEX_APP_MCP_CONFIG_OVERRIDES` | empty | app-server launch config overrides |
| `CODEX_APP_MCP_REQUEST_TIMEOUT_SECONDS` | `30` | ordinary RPC timeout |
| `CODEX_APP_MCP_OPERATION_TIMEOUT_SECONDS` | `180` | long operation timeout |
| `CODEX_APP_MCP_ACTION_TIMEOUT_SECONDS` | `60` | pending server-request timeout |
| `CODEX_APP_MCP_STATE_PATH` | `$CODEX_HOME/app-mcp/state.sqlite3` | durable jobs |
| `CODEX_APP_MCP_SCHEDULER_POLL_SECONDS` | `1` | durable scheduler poll interval |
| `CODEX_APP_MCP_SCHEDULER_ENABLED` | `1` | autostart schedules with stdio/HTTP service |
| `CODEX_APP_MCP_PROTOCOL_CACHE_SECONDS` | `300` | exact-schema cache TTL |
| `CODEX_APP_MCP_AUDIT` | `1` | secret-safe audit |
| `CODEX_APP_MCP_AUDIT_PATH` | stderr | optional JSONL file |
| `CODEX_APP_MCP_HTTP_TOKEN` | empty | bearer token |

Security-sensitive thread config keys cannot be enabled through the generic
config allowlist. Sandbox, approval, model effort, plugins, MCP, hooks,
features, permissions, and Windows security settings use typed fields or an
explicit raw RPC operator policy.

## Protocol drift and administration

`codex_app_protocol` runs the official version-specific
`generate-json-schema --experimental` command against `CODEX_APP_MCP_BIN` and
caches the method catalog. `codex_app_admin` checks that catalog before a typed
operation by default. This prevents a gateway from claiming that a future-only
endpoint exists on an older binary.

The administrative surface includes account/quota, config/features, apps,
plugins, skills, marketplaces, MCP OAuth/resources, environments, fuzzy/thread
search, memory, realtime and remote control. Read operations are directly
typed. Every mutation requires `CODEX_APP_MCP_ALLOW_UNSAFE_RPC=1`; external
login, billing, plugin and marketplace mutations are never run implicitly.

Overload error `-32001` is retried with bounded exponential backoff and jitter.
Transport restart is explicit through `codex_app_runtime action=restart`, so a
possibly accepted mutating call is never duplicated after an ambiguous
connection failure. Runtime metrics include tool/error/restart counters and
scheduler ticks, launches, errors, and its last bounded error message.

## Verification

```powershell
py -3 -m pytest tests -q
py -3 -m compileall -q codex_app_mcp scripts
py -3 scripts\check_protocol.py
py -3 scripts\audit_protocol.py
py -3 scripts\probe_stdio.py
py -3 scripts\probe_http.py
py -3 scripts\probe_full.py
```

The full probe creates a temporary repository, performs real
danger-full-access command/fs/turn/shell/review/lane work, verifies the files
and diff, deletes only its own threads, and closes app-server.

## Boundaries

- app-server remains experimental and may drift between Codex versions;
- experimental method presence is determined from the configured binary, not
  inferred from a README or package version;
- mutating account/config/plugin/marketplace calls are typed and raw-RPC
  reachable but are not executed by the default live probe;
- HTTP bearer is a single-tenant boundary; isolate token, process,
  `CODEX_HOME`, and SQLite per tenant;
- no auto-push, merge, PR, login, plugin install, or billing mutation occurs
  without an explicit tool/RPC call and policy authorization.
