# codex_delegate — package contract

Governed stdio MCP server that hands a coding goal to the **local Codex CLI**
(`codex exec`) inside an isolated `codex/*` git worktree and returns branch +
diffstat. Dev-only. No push, no merge, no approval/sandbox bypass.

## Entry points

```text
python -m codex_delegate                 # serve MCP over stdio
python -m codex_delegate --self-test     # PASS/FAIL probes, no delegation
python -m codex_delegate --smoke-delegate
python codex_delegate/server.py          # flat launch (dual-import supported)
```

## Modules

| Module | Role |
|---|---|
| `guard.py` | Pure policy, validators, argv construction (zero I/O) |
| `cli_contract.py` | Authoritative flag sets for `exec` / `exec resume` / `exec review` |
| `runner.py` | Worktree prep, spawn, event-stream parse, diffstat |
| `status.py` | Read-only probes (version, auth presence, doctor, models, lanes) |
| `audit.py` | Redacted JSON-line audit on stderr |
| `server.py` | JSON-RPC 2.0 MCP adapter |

## Environment variables

| Var | Meaning | Default |
|---|---|---|
| `CODEX_DELEGATE_BIN` | binary name/path | `codex` |
| `CODEX_DELEGATE_ALLOWED_ROOTS` | `;`-separated absolute roots or JSON array | (empty → fail closed) |
| `CODEX_DELEGATE_REPO_ROOT` | single-root fallback | — |
| `CODEX_DELEGATE_LANES_PARENT` | pin for worktree parent | `<repo>.parent/codex-lanes` |
| `CODEX_DELEGATE_BASE_REF` | default base ref | `HEAD` |
| `CODEX_DELEGATE_MODEL` | default model | — |
| `CODEX_DELEGATE_REASONING_EFFORT` | default effort | — |
| `CODEX_DELEGATE_TIMEOUT_SECONDS` | default timeout | `900` |
| `CODEX_DELEGATE_IGNORE_USER_CONFIG` | emit `--ignore-user-config` | **on** (`0/false/off/no` disables) |

`--ignore-user-config` defaults **on** so a delegated lane does not inherit the
operator's `$CODEX_HOME/config.toml` MCP servers and skills. Auth still resolves
from `CODEX_HOME` (the server never reads `auth.json`).

## Tools

| Tool | Purpose |
|---|---|
| `codex_delegate` | execute goal, `-s workspace-write` |
| `codex_delegate_plan` | plan-only, forced `-s read-only` |
| `codex_delegate_review` | `codex exec review` in existing lane (cwd = worktree; default sandbox) |
| `codex_delegate_status` | health JSON |
| `codex_delegate_doctor` | `codex doctor --json` |
| `codex_delegate_models` | reduced `codex debug models` |
| `codex_delegate_lanes` | list `codex/*` lanes |

Client schemas never accept `codex_bin`, `add_dir`, or raw config passthrough.
If `codex_bin` appears in arguments anyway, the call fails closed with
`CODEX_BIN_CLIENT_FORBIDDEN`.

## argv shape (`codex exec`)

```text
<codex_bin> exec
  --cd <worktree>
  -s <sandbox>
  --json
  --color never
  -o <last_message_path>
  [--ignore-user-config]
  [--ephemeral]
  [-m <model>]
  [-c model_reasoning_effort="<effort>"]   # best-effort; CLI does not enforce values
  [--output-schema <path>]
  -
```

The prompt is always the stdin sentinel `-`. Goal text never appears in argv.
Process cwd is also pinned to the worktree (defence in depth alongside `--cd`).
`-c sandbox_mode=...` is never emitted (probe B proved it is ignored for `exec`).

**`resume` is fail-closed** (`RESUME_UNSUPPORTED`): `codex exec resume` rejects `--cd` and
`-s`, so a resumed session would not run under a caller-selected sandbox. The field is
absent from both delegate schemas; smuggling it still fails closed.

**`codex exec review`** emits no `--cd`, no `-s`, and no `--color` (the real binary rejects
all three). Sandbox in the result is the scalar `"read-only"` plus
`sandbox_is_codex_default: true`.

Every emitted flag is checked against `cli_contract.py` (mirrored from
`CODEX-CLI-FACTS.md`) both at runtime (`assert_argv_safe`) and in conformance tests.

`-c model_reasoning_effort=...` is **best-effort only**: the CLI does not reject bogus
values (see CODEX-CLI-FACTS.md). We validate against our allowlist ourselves and never
claim CLI-level enforcement.

## Error codes (structured)

Every tool error is `{"ok": false, "error": "<CODE>", "message": "..."}`.

| Code | When |
|---|---|
| `LANE_EMPTY` / `LANE_INVALID` / `LANE_RESERVED` | lane name policy |
| `GOAL_EMPTY` / `GOAL_TOO_LONG` / `GOAL_INVALID` | goal policy |
| `MODEL_INVALID` / `REASONING_EFFORT_INVALID` | model/effort |
| `SANDBOX_FORBIDDEN` / `SANDBOX_INVALID` / `SANDBOX_PLAN_CONFLICT` | sandbox |
| `RESUME_UNSUPPORTED` | resume requested (fail-closed; no --cd/-s on resume) |
| `SESSION_ID_INVALID` / `TIMEOUT_INVALID` | session id / timeout |
| `DOCTOR_TIMEOUT` | `codex doctor --json` exceeded `DOCTOR_TIMEOUT_SECONDS` |
| `ARGV_FLAG_NOT_IN_CONTRACT` | emitted flag not in real sub-command option set |
| `OUTPUT_SCHEMA_INVALID` / `OUTPUT_SCHEMA_TOO_LONG` | schema |
| `CODEX_BIN_CLIENT_FORBIDDEN` / `CODEX_BIN_INVALID` / `CODEX_MISSING` | binary |
| `ALLOWED_ROOTS_EMPTY` / `REPO_ROOT_UNTRUSTED` | root allowlist |
| `LANES_PARENT_UNTRUSTED` / `LANES_PARENT_INSIDE_REPO` | lanes parent |
| `PATH_ESCAPE` | path left its root |
| `GIT_MISSING` / `GIT_VERB_FORBIDDEN` | git |
| `BASE_UNREACHABLE` / `BASE_DIRTY` | base tree |
| `WORKTREE_INSIDE_REPO` / `WORKTREE_EXISTS_CONFLICT` / `WORKTREE_CREATE_FAILED` / `WORKTREE_MISSING_AFTER_ADD` / `WORKTREE_MISSING` | worktree |
| `ARGV_*` | argv defence-in-depth |
| `CLI_VERB_FORBIDDEN` | non-readonly CLI verb |
| `TIMEOUT` / `EXEC_FAILED` | run outcome |
| `TOOL_UNKNOWN` | unknown MCP tool |

## Success rule (not exit code alone)

Probe A returned `returncode == 0` while the agent refused under read-only.
Status is derived from the JSONL event stream:

```text
timeout  if timedOut
error    if returncode != 0 or turn_failed or (errors and not turn_completed)
ok       otherwise
```

## Non-goals

No auto-merge, push, PR creation, `codex cloud`, `codex apply`, MCP-server
management, plugin management, login/logout, `codex update`, or session deletion.

## Codex CLI facts source

All flags, subcommands, config keys and event-stream shapes come from
[`CODEX-CLI-FACTS.md`](../CODEX-CLI-FACTS.md) (live probes of codex-cli 0.144.1).
Nothing outside that file is claimed.
