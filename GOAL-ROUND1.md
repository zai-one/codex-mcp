# GOAL-ROUND1 — `codex_delegate`: governed MCP delegation server for Codex CLI

Build a dev-only stdio MCP server, in this repository, that lets an orchestrator (Claude) hand a
coding goal to the **local Codex CLI** running headless inside an **isolated git worktree**, and
get back a branch + diffstat. It is the Codex-side twin of an existing Grok-side server, but it
must be anchored on the *real* Codex CLI surface — see [`CODEX-CLI-FACTS.md`](CODEX-CLI-FACTS.md),
which is the only source of truth for flags. **Do not invent CLI flags or config keys.** If a
capability is not in that file, either probe it and record the probe, or do not claim it.

Read `CODEX-CLI-FACTS.md` in full before writing code.

## 0. Non-negotiable boundaries

1. **No push, no merge.** No code path may assemble `git push`, `git merge`, `git pull`,
   `git rebase`, `git cherry-pick`, `git reset`, or `git clean`. A helper that could do it must
   not exist, not merely be unused.
2. **No approval/sandbox bypass.** `--dangerously-bypass-approvals-and-sandbox`,
   `--dangerously-bypass-hook-trust`, `--ignore-rules`, `--add-dir`, and
   `-s danger-full-access` are forbidden everywhere, including if a caller smuggles them.
3. **Never read `auth.json`** or any file under `CODEX_HOME`. Auth presence is probed only via
   `codex login status` exit/stdout, and the stdout must never be echoed verbatim into results.
4. **The worktree is never inside the main repo working tree.** Fail closed if it resolves inside.
5. **Client cannot choose the binary.** `codex_bin` is not in any client schema; if it appears in
   arguments anyway, fail closed. The binary comes from env only.
6. **Fail closed, always structured.** Every error returns
   `{"ok": false, "error": "<CODE>", "message": "..."}` — never a raised exception across the tool
   boundary, never a bare string.

## 1. Repository layout to produce

```
codex_delegate/
  __init__.py
  __main__.py        # python -m codex_delegate [--self-test | --smoke-delegate]
  guard.py           # pure policy + argv construction, zero I/O
  runner.py          # git worktree prep, headless spawn, diff collection
  status.py          # read-only probes
  audit.py           # redacted JSON-line audit to stderr
  server.py          # stdio JSON-RPC 2.0 MCP adapter
  README.md          # package contract, env vars, error codes
tests/
  test_codex_delegate.py   # pytest, fully mocked — no real codex spawn, no real git mutation
README.md            # operator-facing, Russian
EVIDENCE-ROUND1.md   # what was verified and how (Russian)
```

Both package-relative (`from .guard import ...`) and flat (`from guard import ...`) imports must
work, because the server may be launched as `python codex_delegate/server.py`. Use the same
`try/except ImportError` dual-import shape in every module that imports a sibling.

## 2. `guard.py` — pure, no I/O, unit-testable

Constants (exact names):

```python
DEFAULT_CODEX_BIN = "codex"
ALLOWED_BIN_BASENAMES = frozenset({"codex", "codex.exe", "codex.cmd", "codex.bat"})
LANE_PREFIX = "codex"
RESERVED_LANE_NAMES = frozenset({"dev", "master", "main", "release", "prod", "production", "rc"})

SANDBOX_READ_ONLY = "read-only"
SANDBOX_WORKSPACE_WRITE = "workspace-write"
SANDBOX_DANGER_FULL_ACCESS = "danger-full-access"
ALLOWED_SANDBOX_MODES = frozenset({SANDBOX_READ_ONLY, SANDBOX_WORKSPACE_WRITE})

FORBIDDEN_CLI_FLAGS = frozenset({
    "--dangerously-bypass-approvals-and-sandbox",
    "--dangerously-bypass-hook-trust",
    "--ignore-rules",
    "--add-dir",
    "--oss",
    "--local-provider",
    "--remote",
    "--remote-auth-token-env",
})

ALLOWED_REASONING_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max", "ultra"})

DEFAULT_TIMEOUT_SECONDS = 900.0
MIN_TIMEOUT_SECONDS = 30.0
HARD_CAP_TIMEOUT_SECONDS = 3600.0
MAX_GOAL_CHARS = 60_000
MAX_MODEL_CHARS = 128
MAX_OUTPUT_SCHEMA_CHARS = 16_000
STDIN_PROMPT_SENTINEL = "-"
```

`class GuardError(Exception)` with `.code` and `.message`, plus `structured_error(code, message, **extra)`.

Validators — each returns the normalized value or raises `GuardError`:

- `normalize_lane(name) -> str` — accepts `slug` or `codex/slug`; slug must match
  `^[a-z0-9][a-z0-9-]*$`; reserved names rejected (`LANE_RESERVED`); empty → `LANE_EMPTY`;
  anything else → `LANE_INVALID`. Returns `codex/<slug>`.
- `validate_goal(goal) -> str` — non-empty after strip, `<= MAX_GOAL_CHARS`, no `\x00`.
  Codes: `GOAL_EMPTY`, `GOAL_TOO_LONG`, `GOAL_INVALID`.
- `validate_model(value) -> str | None` — bounded length, no control chars, must not start with
  `-` (would be parsed as a flag). Code: `MODEL_INVALID`.
- `validate_reasoning_effort(value) -> str | None` — lowercased, must be in
  `ALLOWED_REASONING_EFFORTS`. Code: `REASONING_EFFORT_INVALID`.
- `validate_sandbox_mode(value, *, plan_only=False) -> str` — `danger-full-access` →
  `SANDBOX_FORBIDDEN`; unknown → `SANDBOX_INVALID`; when `plan_only` is true the only legal
  result is `read-only` (an explicit `workspace-write` with `plan_only=True` →
  `SANDBOX_PLAN_CONFLICT`); `None` → `read-only` if `plan_only` else `workspace-write`.
- `validate_session_id(value) -> str | None` — UUID form. Code: `SESSION_ID_INVALID`.
- `validate_timeout(value) -> float` — `None` → `DEFAULT_TIMEOUT_SECONDS`; below
  `MIN_TIMEOUT_SECONDS` or above `HARD_CAP_TIMEOUT_SECONDS` → `TIMEOUT_INVALID` (do **not**
  silently clamp a caller-supplied over-cap value).
- `validate_output_schema(value) -> str | None` — accepts a dict or a JSON string, returns compact
  JSON text; must be a JSON **object**; bounded by `MAX_OUTPUT_SCHEMA_CHARS`. Codes:
  `OUTPUT_SCHEMA_INVALID`, `OUTPUT_SCHEMA_TOO_LONG`.
- `validate_codex_bin(value, *, from_client=False) -> str` — `from_client=True` always raises
  `CODEX_BIN_CLIENT_FORBIDDEN`. Otherwise accept a bare name or an absolute path whose basename
  (case-insensitive) is in `ALLOWED_BIN_BASENAMES`; anything else → `CODEX_BIN_INVALID`.

Path helpers (pure): `parse_allowed_roots_env(raw)` (`;`/newline separated, or a JSON array),
`paths_equal(a, b)` (case-insensitive on `os.name == "nt"`), `path_in_allowlist(candidate, allowlist)`
(post-`resolve()` equality — this is what makes `..` and symlink escapes impossible),
`confine_path_to_root(path, root, *, field)` → `PATH_ESCAPE` when it leaves root.

### Execution profile

```python
def build_execution_profile(*, plan_only: bool = False) -> dict[str, Any]
```
Returns at least: `sandbox` (`read-only` for plan, `workspace-write` for execute),
`writable_roots` = `["<cd>"]` conceptually, `network_access: False`,
`git_metadata_writable: False`, `mode`: `"plan"` | `"execute"`.

Document in the docstring, honestly: the confinement is Codex's own OS sandbox selected via `-s`,
proven on Windows by probes A/C in `CODEX-CLI-FACTS.md`. We add no pattern-based deny list,
because Codex has no per-command allow/deny flags — inventing one would be theatre.

### argv construction

```python
def build_exec_argv(
    *,
    codex_bin: str,
    worktree: str,
    last_message_path: str,
    sandbox: str,
    plan_only: bool = False,
    model: str | None = None,
    reasoning_effort: str | None = None,
    output_schema_path: str | None = None,
    ignore_user_config: bool = True,
    ephemeral: bool = False,
    resume: str | bool | None = None,
) -> list[str]
```

Emitted shape (order matters for the tests you write):

```
<codex_bin> exec [resume [<SESSION_ID>|--last]]
  --cd <worktree>
  -s <sandbox>
  --json
  --color never
  -o <last_message_path>
  [--ignore-user-config]
  [--ephemeral]
  [-m <model>]
  [-c model_reasoning_effort="<effort>"]
  [--output-schema <output_schema_path>]
  -
```

Rules:
- The prompt is **always** the stdin sentinel `-` as the final token. The goal text is never
  placed in argv (Windows argv length limits, and the goal must not appear in the process list).
- `resume is True` → `exec resume --last`; a UUID string → `exec resume <uuid>`; `False`/`None` →
  plain `exec`. Any other type → `SESSION_ID_INVALID`.
- `-c model_reasoning_effort=...` is only emitted for a validated effort, and the value is
  TOML-quoted (`model_reasoning_effort="high"`).
- Never emit any `-c sandbox_mode=...` — probe B proved it is ignored for `exec`; emitting it
  would imply a guarantee that does not exist.

```python
def assert_argv_safe(argv: Sequence[str]) -> None
```
Defence in depth, called again by the runner immediately before spawn. Must verify:
`exec` present as the first non-binary token; `--cd` present with a value; `--json` present;
`-s` present with a value in `ALLOWED_SANDBOX_MODES`; final token is `-`; no token in
`FORBIDDEN_CLI_FLAGS` and no token starting with `--dangerously`; no `-c` override whose key is
`sandbox_mode`. Codes: `ARGV_MISSING_EXEC`, `ARGV_MISSING_CD`, `ARGV_MISSING_JSON`,
`ARGV_SANDBOX_INVALID`, `ARGV_PROMPT_NOT_STDIN`, `ARGV_FORBIDDEN_FLAG`.

Also provide, for tests and status: `profile_is_read_only(profile)`,
`argv_uses_stdin_prompt(argv)`, `argv_sandbox_mode(argv)`.

## 3. `runner.py` — the only module allowed to touch the OS

Injectable callables so tests never spawn Codex and never mutate a real repo:

```python
GitRunner       = Callable[[Sequence[str], Path | None, float], dict]
SubprocessRunner = Callable[..., dict]   # (args, cwd, timeout, *, input_text=None)
WhichFn         = Callable[[str], str | None]
```

Every runner result dict: `{"args", "returncode", "stdout", "stderr", "timedOut"}` plus optional
`"missing": True`.

- `default_git_runner(args, cwd, timeout)` — real `git`. Must call `_reject_forbidden_git_args`
  first (skipping global options such as `-C <path>` / `-c <kv>` before identifying the verb) and
  raise `GIT_VERB_FORBIDDEN` for the forbidden verbs in §0.1.
  **Decode with `encoding="utf-8", errors="replace"`** — never the Windows locale codepage;
  otherwise a non-cp1252 byte in a branch or file name kills the reader thread and loses the run.
- `default_subprocess_runner(args, cwd, timeout, *, input_text=None)` — real Codex spawn, same
  UTF-8 decoding rule, passes `input=input_text` so the goal arrives on stdin. Must call
  `_reject_forbidden_flags(args)` before spawning. `FileNotFoundError` → `missing: True`,
  `TimeoutExpired` → `returncode 124, timedOut: True`.

- `resolve_lanes_parent(repo_root, lanes_parent=None) -> Path` — default sibling directory
  `<repo_root>.parent / "codex-lanes"`.
- `worktree_path_for_lane(lanes_parent, lane) -> Path` — `codex/<slug>` → `<lanes_parent>/<slug>`.
- `is_path_inside(child, parent) -> bool`.
- `prepare_worktree(*, repo_root, lane, base_ref, lanes_parent=None, git_runner=None, timeout=60.0,
  require_clean_base=True) -> dict` — probe `git --version` (`GIT_MISSING`), verify base ref
  (`BASE_UNREACHABLE`), refuse a dirty main tree (`BASE_DIRTY`), refuse a target inside the repo
  (`WORKTREE_INSIDE_REPO`), reuse an existing worktree only when its `HEAD` is already the
  expected branch (otherwise `WORKTREE_EXISTS_CONFLICT`), else
  `git worktree add -b <branch> <path> <base_ref>` with a fallback to
  `git worktree add <path> <branch>` when the branch already exists
  (`WORKTREE_CREATE_FAILED`, `WORKTREE_MISSING_AFTER_ADD`). Returns
  `{"ok", "lane", "branch", "worktree_path", "base_ref", "reused"}`.
- `collect_diff(worktree_path, *, git_runner=None, timeout=60.0) -> dict` — `changed_files` from
  `diff --name-only HEAD` **plus** `status --porcelain` (so new untracked files are counted), and
  a `diffstat` from `diff --stat HEAD` truncated at 8000 chars. Never return a full patch.

### `parse_event_stream(stdout: str) -> dict`

Parse the JSONL described in `CODEX-CLI-FACTS.md`. Must never raise. Returns:

```python
{
  "thread_id": str | None,        # from thread.started
  "last_message": str | None,     # text of the last agent_message item
  "agent_messages": int,
  "commands": [ {"command": str_truncated, "exit_code": int|None, "status": str|None} ],  # cap 50
  "errors": [str],                # item.type == "error" -> message ; also turn.failed
  "usage": dict | None,           # from turn.completed
  "turn_completed": bool,
  "turn_failed": bool,
  "unparsed_lines": int,
}
```
Lines that are not JSON are counted, not echoed. Truncate every captured string to 500 chars.

### `run_delegation(...) -> dict`

Sequence: validate + build argv (`build_exec_argv`) → `assert_argv_safe` →
`confine_path_to_root(cd_value, worktree)` → resolve the binary with `which` (fail
`CODEX_MISSING` when absent) → create the `-o` last-message temp file **outside the worktree**
(system temp; delete in a `finally`) → spawn with `input_text=goal` → parse events.

Success is **not** `returncode == 0` alone (probe A returned 0 while the agent refused). Define:

```
status = "timeout"  if timedOut
       = "error"    if returncode != 0 or turn_failed or (errors and not turn_completed)
       = "ok"       otherwise
```
Return `{"ok", "status", "returncode", "elapsed_seconds", "summary", "thread_id", "usage",
"errors", "commands", "plan_only", "sandbox", "cwd", "goal_chars",
"stdout_truncated", "stderr_truncated"}`. `summary` = `-o` file content if present, else
`last_message`, else first 500 chars of stdout. Truncate stdout/stderr previews (2000/1000).

### `delegate(...) -> dict`

`prepare_worktree` → `run_delegation` → `collect_diff` **always** (even when the executor failed,
so a partial lane is still visible). Returns `{"ok", "lane", "branch", "worktree_path", "status",
"summary", "thread_id", "usage", "changed_files", "diffstat"}` plus `error`/`message` on failure.

### `run_readonly_cli(args, ...) -> dict`

Bounded read-only Codex invocations for the status tools. Allowlist by first non-flag token:
`{"--version", "-V", "doctor", "features", "debug", "login"}`. Explicitly reject
`{"exec", "e", "logout", "update", "mcp", "mcp-server", "plugin", "apply", "cloud", "app",
"app-server", "remote-control", "archive", "delete", "unarchive", "fork", "resume", "sandbox",
"exec-server", "completion"}` → `CLI_VERB_FORBIDDEN`. Also reject the mutating second tokens
`enable`, `disable`, `fix`, `install`, `uninstall`, `add`, `remove`, `set` (so
`codex features enable X` cannot slip through), and reject `login` with anything other than
`status` (never `--with-api-key` / `--with-access-token`).

## 4. `status.py` — read-only probes

- `probe_codex_version(...)` → `codex --version`, parse `codex-cli <semver>`.
- `probe_auth_presence(...)` → `codex login status`. `auth_present` is true only on
  `returncode == 0` **and** a positive phrase (`logged in`); negative phrases
  (`not logged in`, `please login`, `unauthorized`) force false. Return a `method` field
  (e.g. `"ChatGPT"` / `"API key"` / `None`) derived from the phrase, and **never** the raw stdout
  and never `auth.json` contents. Include `"auth_file_read": False`.
- `probe_git_available(...)` → `git --version`.
- `run_doctor_json(...)` → `codex doctor --json` (already redacted by Codex), parsed; on parse
  failure return a bounded `text_preview`. Never `codex doctor` without `--json` and never any
  fix path.
- `run_models(...)` → `codex debug models`, and **reduce** it: return only
  `[{"slug", "display_name", "default_reasoning_level", "supported_reasoning_levels": [effort...]}]`
  — the raw catalog is ~30 keys per model and must not be dumped into a tool result.
- `list_lanes(repo_root, *, git_runner=None)` → `git worktree list --porcelain` for the root,
  keep only worktrees whose branch is `codex/*`, and add per-lane `changed_files` count +
  `diffstat` via `collect_diff`. Read-only.
- `build_status_report(...)` → aggregate: `server` (name/version), `codex`
  (binary, binary_found, version), `auth`, `git`, `roots` (allowed + `lanes_parent_by_root` +
  `configured`), `sandbox` (`plan_default`, `execute_default`, `allowed_modes`,
  `danger_full_access_allowed: False`, plus an `enforcement_note` that states what probes A/B/C
  actually proved), and `config` (`ignore_user_config`, `default_base_ref`, `timeout_seconds`,
  `hard_cap_timeout_seconds`).

## 5. `audit.py` — redacted stderr JSON lines

One JSON line per tool call on stderr. Allowlisted fields only: `ts`, `principal`, `tool`, `lane`,
`branch`, `base_ref`, `cwd`, `worktree_path`, `outcome`, `status`, `error`, `plan_only`,
`sandbox`, `goal_chars`, `goal_sha256_8`, `changed_file_count`, `elapsed_seconds`, `thread_id`,
`usage_input_tokens`, `usage_output_tokens`.

`goal_fingerprint(goal)` → `{"goal_chars": len, "goal_sha256_8": sha256[:8]}` — the raw goal text
must never be logged. `sanitize_event` must **raise `AuditError`** (fail closed) if a value
references `auth.json`, a `.codex/` credential path, or matches a secret-like pattern
(`api[_-]?key\s*[:=]`, `authorization\s*[:=]`, `bearer \S+`, `OPENAI_API_KEY`,
`CODEX_ACCESS_TOKEN`, `BEGIN (RSA )?PRIVATE KEY`) — check the **raw** values before redaction, so
a leak cannot be silently emitted. Drop `goal`, `prompt`, `diff`, `patch`, `stdout`, `stderr`,
`argv`, `token`, `key` keys even if smuggled. Audit failures must never crash a tool call
(best-effort emit inside `try/except`), but a `sanitize_event` violation must be a real raise that
the tests assert.

## 6. `server.py` — stdio JSON-RPC 2.0

`SERVER_NAME = "codex-delegate"`, `SERVER_VERSION = "0.1.0"`, `PROTOCOL_VERSION = "2024-11-05"`.

Handle `initialize`, `notifications/initialized` (no response), `tools/list`, `tools/call`,
`ping`; unknown method → JSON-RPC `-32601`. Requests without an `id` are notifications and get no
response. `tools/call` returns
`{"content": [{"type": "text", "text": <json>}], "structuredContent": <result>, "isError": not result["ok"]}`.
`serve_stdio` must support both line-delimited JSON and `Content-Length:`-framed messages.

### Tools

| Tool | Purpose |
|---|---|
| `codex_delegate` | execute a goal in a `codex/*` worktree, `-s workspace-write` |
| `codex_delegate_plan` | same, forced `plan_only=true` / `-s read-only` |
| `codex_delegate_review` | `codex exec review` inside an existing lane worktree (read-only) |
| `codex_delegate_status` | health JSON |
| `codex_delegate_doctor` | `codex doctor --json` |
| `codex_delegate_models` | reduced `codex debug models` |
| `codex_delegate_lanes` | list `codex/*` lanes for an allowlisted root |

`codex_delegate` input schema (`additionalProperties: false`, required `goal` + `lane`):
`goal`, `lane`, `base_ref`, `model`, `reasoning_effort`, `sandbox`, `plan_only`,
`timeout_seconds`, `repo_root`, `lanes_parent`, `output_schema`, `resume`, `ephemeral`.
**No `codex_bin`, no `add_dir`, no raw `config` passthrough.**

`codex_delegate_review` input: `lane` (required), `repo_root`, `base_ref` (→ `--base`),
`uncommitted` (bool → `--uncommitted`), `instructions` (optional prompt via stdin), `model`,
`timeout_seconds`. Because `codex exec review` has no `--cd` and no `-s`, the runner must set the
**process cwd** to the lane worktree and the tool description must state that its read-only
posture comes from the Codex default sandbox (probe A), not from a flag we pass.

Root resolution (mirror precisely):
- `load_allowed_roots(env=None, injected=None)` — injection wins; then
  `CODEX_DELEGATE_ALLOWED_ROOTS`; then a single `CODEX_DELEGATE_REPO_ROOT` as a one-entry
  allowlist. Empty → `ALLOWED_ROOTS_EMPTY` at resolve time (fail closed, with a setup hint).
- `resolve_trusted_repo_root(args, *, repo_root=None, allowed_roots=None)` — a client
  `repo_root` is accepted only when its `resolve()` equals an allowlist entry
  (`REPO_ROOT_UNTRUSTED`); omitted → first allowlist entry.
- `resolve_trusted_lanes_parent(args, *, repo_root)` — when `CODEX_DELEGATE_LANES_PARENT` is set,
  a client value must resolve inside it (`LANES_PARENT_UNTRUSTED`); the result must never resolve
  inside `repo_root` (`LANES_PARENT_INSIDE_REPO`).
- `resolve_server_codex_bin(args)` — if `codex_bin` is present in args at all, call
  `validate_codex_bin(..., from_client=True)` so it fails closed even when
  `additionalProperties` was bypassed; otherwise read `CODEX_DELEGATE_BIN`.

`handle_tool_call(name, arguments, *, repo_root=None, allowed_roots=None, git_runner=None,
subprocess_runner=None, which=None, audit_stream=None, principal="local-dev")` must be callable
directly, without stdio — the tests and `--self-test` drive it that way.

## 7. Environment variables

| Var | Meaning |
|---|---|
| `CODEX_DELEGATE_BIN` | binary name/path (default `codex`) |
| `CODEX_DELEGATE_ALLOWED_ROOTS` | `;`-separated absolute roots (or JSON array) |
| `CODEX_DELEGATE_REPO_ROOT` | single-root fallback |
| `CODEX_DELEGATE_LANES_PARENT` | pin for worktree parent |
| `CODEX_DELEGATE_BASE_REF` | default base ref, default `HEAD` |
| `CODEX_DELEGATE_MODEL` | default model |
| `CODEX_DELEGATE_REASONING_EFFORT` | default effort |
| `CODEX_DELEGATE_TIMEOUT_SECONDS` | default timeout |
| `CODEX_DELEGATE_IGNORE_USER_CONFIG` | default **on**; `0/false/off/no` disables |

`--ignore-user-config` defaults to **on** deliberately: the operator's `CODEX_HOME/config.toml`
attaches external MCP servers and >130 skills to every session (see the last section of
`CODEX-CLI-FACTS.md`). A delegated lane must be hermetic — it must not inherit tools that can
reach Telegram, infrastructure, or a desktop. Document this in both READMEs as a security
property, and note that auth still resolves from `CODEX_HOME`.

## 8. `__main__.py`

- default → `serve_stdio()`
- `--self-test` → PASS/FAIL table, no delegation: binary, version, auth presence, git,
  in-process `initialize`, `tools/list` (all seven names), and each read-only status tool via
  `handle_tool_call`. Exit 0 only when every row passes.
- `--smoke-delegate` → one real, bounded, **plan-only** run in a throwaway git repo created in the
  system temp dir (init, one commit, delegate with `plan_only=True`, tiny goal, small model,
  short timeout), printing the structured result and `SMOKE PASS`/`SMOKE FAIL`. Clean up the temp
  tree in a `finally`.

## 9. Tests — `tests/test_codex_delegate.py`

`pytest`/`unittest` style, fully mocked. Required coverage, asserting **behaviour**, not the
presence of substrings in source:

1. `normalize_lane` — good slugs, `codex/` prefix, reserved names, invalid charset, empty.
2. `validate_sandbox_mode` — `danger-full-access` rejected; `plan_only=True` + `workspace-write`
   rejected; defaults correct.
3. `build_exec_argv` — final token is `-`; `--cd` present; `-s` correct per mode; `--json`
   present; no forbidden flag; no `-c sandbox_mode`; goal text absent from argv entirely;
   `resume=True` → `exec resume --last`; `resume="<uuid>"` → `exec resume <uuid>`;
   bad uuid rejected.
4. `assert_argv_safe` — each failure code triggered by a hand-built bad argv.
5. `validate_codex_bin` — client-supplied always rejected; `python.exe` rejected; `codex.cmd`
   accepted.
6. `prepare_worktree` — dirty base rejected; unreachable base rejected; target inside repo
   rejected; branch-exists fallback path; reuse-on-matching-branch path.
7. Forbidden git verbs — `default_git_runner` raises for `push`/`merge` even when disguised
   behind `-C <path>`.
8. `parse_event_stream` — the exact sample stream from `CODEX-CLI-FACTS.md` yields the right
   `thread_id`, `last_message`, `usage`, `commands`, and an `error` item is captured; garbage
   lines only bump `unparsed_lines`.
9. **Probe-A regression**: a run whose stdout contains an `agent_message` refusal and
   `turn.completed`, with `returncode == 0`, must still be reported honestly — assert the
   documented status rule, and assert that an `error` item plus no `turn.completed` yields
   `status == "error"`.
10. Timeout → `status == "timeout"`, `ok is False`.
11. `run_delegation` passes the goal via `input_text` and **not** in argv (assert on the captured
    mock call).
12. `delegate` collects a diffstat even when the executor failed.
13. Root allowlist — untrusted `repo_root` rejected; `..` escape rejected; empty allowlist →
    `ALLOWED_ROOTS_EMPTY`; lanes parent inside repo rejected.
14. `audit.sanitize_event` — raises on an `auth.json` path, on a bearer token, on
    `OPENAI_API_KEY=...`; drops a smuggled `goal` key; emits only allowlisted fields.
15. Server JSON-RPC — `initialize` returns `serverInfo.name == "codex-delegate"`; `tools/list`
    returns exactly the seven tool names; `tools/call` on an unknown tool returns
    `isError: true` with a structured payload; a notification (no `id`) returns `None`.
16. `run_readonly_cli` — `exec`, `logout`, `mcp`, `features enable x`, `login --with-api-key`
    all rejected; `doctor --json` and `debug models` accepted.

All tests must pass with `py -3 -m pytest tests -q` from the repository root.

## 10. DONE conditions

- `py -3 -m pytest tests -q` → all green, ≥ 45 tests.
- `py -3 -m codex_delegate --self-test` → `RESULT: PASS` on this host.
- `py -3 -m codex_delegate --smoke-delegate` → `SMOKE PASS` (a real plan-only Codex run).
- `python codex_delegate/server.py` answers a hand-fed `initialize` + `tools/list` on stdin.
- `README.md` (Russian, operator-facing) documents: what the server is, the two-server picture
  (`codex mcp-server` as the built-in consult/session channel vs `codex_delegate` as the governed
  delegation channel), the wiring snippet for `claude_desktop_config.json`, env vars, the tool
  table, and the honest limits section.
- `EVIDENCE-ROUND1.md` (Russian) records: commands run, their real output, what each DONE item
  proved, and every limitation that remains.
- No file in `codex_delegate/` exceeds 500 lines except `guard.py` and `server.py`, which may
  reach 700. If a module would exceed it, split it.

## 11. Explicit non-goals

No auto-merge, no push, no PR creation, no cloud tasks (`codex cloud`), no `codex apply`,
no MCP-server management (`codex mcp`), no plugin management, no login/logout, no `codex update`,
no session deletion. This server is a **dev-only local delegation surface**, not a product
admin bridge.
