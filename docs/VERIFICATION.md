# Verification ledger

Snapshot: 29 July 2026. Gateway `0.4.0`.

## Automated gates

```text
pytest: 55 passed
compileall: passed
ruff format: passed
ruff E9/F63/F7/F82: passed
git diff --check: passed
wheel build and contents: passed
legacy codex_delegate import: absent
```

## Protocol coverage

The catalog is generated from the configured binary with
`generate-json-schema --experimental`.

| Client methods | Callable | Typed | Server requests | Notifications | Rejected |
|---:|---:|---:|---:|---:|---:|
| 126 | 124 | 124 | 11 | 70 | 0 |

`initialize` and `mock/*` are not proxied. Every other callable method has a
typed route. Read-only and governed raw RPC remain forward-compatible
fallbacks.

## Live full-access matrix

The probe uses temporary repositories and cleans up only threads it creates.

| Check | Result |
|---|---|
| app-server initialize/status/models | PASS |
| exact protocol catalog | PASS |
| typed administrative account read | PASS |
| schedule create/list/pause/resume/delete | PASS |
| unsandboxed `process/spawn` file write | PASS |
| `command/exec` file write | PASS |
| filesystem write/read/metadata/list/copy/remove | PASS |
| thread start/name/metadata/read/loaded | PASS |
| model turn exact response | PASS |
| unsandboxed `thread/shellCommand` file write | PASS |
| fork/read/unsubscribe | PASS |
| native review completion | PASS |
| archive/unarchive with verified recovery | PASS |
| raw rate-limit read | PASS |
| worktree lane and changed-file readback | PASS |
| created-thread cleanup | PASS |
| app-server child shutdown | PASS |

Final summaries:

```json
{"mode":"no-model","ok":true,"failures":[]}
{"mode":"full","ok":true,"failures":[]}
```

## External transports

Stdio:

```text
initialize: PASS
tools/list: 20
protocol callable methods: 124
status and collaboration modes: PASS
outer process exit after stdin close: 0
```

HTTP:

```text
health: PASS
missing bearer: 401
initialize/tools/status/doctor: PASS
tools/list: 20
protocol callable methods: 124
```

## Durable and security behavior

- interrupted jobs recover as explicit `orphaned` records;
- partial unique indexes reject concurrent lane and schedule ownership;
- schedules run after service startup without an inbound request;
- schedule recurrence, timezone, idempotency, retry and misfire state persist;
- full access and stateful/raw mutations fail closed without separate gates;
- project and filesystem paths cannot leave configured roots;
- audit excludes prompts, responses, patches, commands, environment and tokens;
- app-server overload uses bounded exponential backoff with jitter;
- ambiguous transport failures never auto-repeat a mutation;
- archive partial-success is accepted only after a successful thread readback;
- non-loopback HTTP requires a bearer token.

## Intentionally non-destructive coverage

Login/logout, billing credits, plugin installation, marketplace mutation,
configuration writes and sandbox setup are schema- and policy-tested but are
not executed by the default live probe because they change external state.
