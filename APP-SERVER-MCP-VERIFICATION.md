# Codex app-server MCP — verification ledger

Дата: 29 июля 2026. Gateway `0.4.0`.

## Финальные автоматические гейты

До удаления старого runtime:

```text
old codex_delegate + new codex_app_mcp: 283 passed
```

После удаления старого runtime:

```text
pytest tests -q: 55 passed
compileall codex_app_mcp scripts: exit 0
git diff --check: exit 0
ruff format --check: 24 files formatted
ruff E9/F63/F7/F82: all checks passed
editable install/import/entrypoint: codex-app-mcp 0.4.0
legacy import: no module found
```

## Protocol coverage

| Schema | Client methods | Callable | Typed | Server requests | Notifications | Rejected |
|---|---:|---:|---:|---:|---:|---:|
| configured binary, default schema | 89 | 88 | 88 | 10 | 70 | 0 |
| configured binary, `--experimental` | 126 | 124 | 124 | 11 | 70 | 0 |
| isolated `0.146.0`, default schema | 90 | 89 | 89 | 10 | 70 | 0 |

`initialize` не проксируется повторно, `mock/*` исключён. Все остальные
124 experimental callable methods имеют typed route; read-only/raw routes
остаются дополнительным forward-compatible fallback. Каталог получен
непосредственно от `CODEX_APP_MCP_BIN`, а не из зафиксированного списка.

## Live configured `0.145.0` full-access matrix

Изолированный аутентифицированный `CODEX_HOME`, временный git repo,
`danger-full-access`, approvals `never`:

| Проверка | Результат |
|---|---|
| initialize/status/models | PASS |
| exact protocol schema: 126/124/11/70 | PASS |
| typed administrative account read | PASS |
| durable schedule create/list/pause/resume/delete | PASS |
| explicitly unsandboxed `process/spawn` real file write | PASS |
| `command/exec` real file write | PASS |
| fs write/read/metadata/list/copy/remove | PASS |
| thread start/name/metadata/read/loaded | PASS |
| model turn exact response | PASS |
| unsandboxed `thread/shellCommand` real file write | PASS |
| fork/read/unsubscribe | PASS |
| native review completed | PASS |
| archive/unarchive | PASS |
| raw `account/rateLimits/read` | PASS |
| app-server-backed worktree lane + changed file | PASS |
| created thread cleanup | PASS |
| app-server child close | PASS |

Probe summary:

```json
{"ok":true,"failures":[]}
```

Финальный gateway `0.4.0` full probe прошёл за 180 секунд. Thread и fork
readback показали `dangerFullAccess`; native review завершился `completed`;
lane job завершился `succeeded` и вернул `lane_probe.txt`. Все три созданных
probe threads удалены, child закрыт.

Отдельный 86-секундный no-model probe также прошёл. Два предыдущих прогона
обнаружили race vendor app-server: archive отвечал раньше физического
перемещения, а иногда перемещение успевало завершиться до внутреннего
readback. Gateway теперь ограниченно повторяет только `archive not found` и
при partial-success подтверждает результат отдельным `thread/read`. Unit и
live recovery после исправления прошли.

## External MCP transports

Stdio, отдельный gateway process:

```text
initialize 2025-11-25: PASS
tools/list: 20
codex_app_status: PASS
collaboration modes: PASS
protocol callable methods: 124
app-server: 0.145.0 / gateway 0.4.0
outer exit after stdin close: 0
```

HTTP, отдельный gateway process:

```text
GET /healthz: PASS
POST /mcp without bearer: 401
initialize: PASS
tools/list: 20
status/app-server: PASS
doctor appServer/account/configRequirements/windowsSandboxReadiness: PASS
exact protocol callable methods: 124
process terminated by probe after success
```

## Durable and security behavior

- SQLite persists job request, threadId, turnId, result and transitions.
- Startup recovery marks interrupted work `orphaned`.
- SQLite partial unique index rejects a second active job on the same lane.
- schedules persist RRULE/timezone/idempotency/retry/misfire state and runs.
- service startup launches the scheduler; due work runs without an inbound tick.
- SQLite rejects concurrent active runs of the same schedule.
- Windows package installs IANA `tzdata` for non-UTC schedules.
- exact protocol lookup is generated from the configured binary.
- app-server overload uses exponential backoff with jitter.
- full access fails closed without `CODEX_APP_MCP_ALLOW_FULL_ACCESS=1`.
- raw mutations fail closed without both unsafe opt-in and method allowlist.
- project cwd and typed filesystem paths cannot leave allowed roots.
- audit does not serialize prompt, response, patch, command, env or tokens.
- bearer is mandatory for non-loopback HTTP.
- unknown/invalid MCP calls return structured errors without crashing.

## Honest boundaries

- experimental API availability is read from the concrete binary schema;
  unsupported typed admin operations fail before RPC.
- account login/logout, credits, config writes, plugin/marketplace mutation and
  sandbox setup are intentionally not live-run because they change external
  state. They remain raw-RPC reachable only after explicit operator policy.
- Desktop Scheduled is not an app-server RPC; recurrence is implemented by the
  gateway's durable scheduler over goal/turn jobs.
