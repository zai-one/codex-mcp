# GOAL-ROUND2 — skeptic pass on your own round-1 delegate server

You built `codex_delegate` in round 1. The integrator then ran the gates you could not run inside
the lane, and ran the **real Codex CLI** against the argv your code emits. Several of your
guarantees do not survive contact with the actual binary.

Your job this round: **attack your own work, then fix it.** Every finding below was reproduced on
this host — none of it is hypothetical. Fix all of them, then keep hunting for the same *class* of
mistake elsewhere in the package.

Gate results already obtained by the integrator (do not re-litigate them):

- `py -3 -m pytest tests -q` → **104 passed**.
- `py -3 -m codex_delegate --self-test` → `RESULT: PASS`, but it took **5m17s**, and one row is a
  false PASS (see R5).

## The root cause you must internalise

Your tests assert what your own `build_exec_argv` produces. Nothing in the suite ever checked those
flags against the **flag set the real sub-command accepts**. So the suite is green while two of the
seven tools cannot run at all. Green tests that only agree with the code under test are worthless
for a CLI-integration boundary. Fixing R1 and R2 individually is not enough — R7 is what stops the
class from recurring.

---

## R1 — CRITICAL — `codex exec resume` rejects `--cd` and `-s`; the whole resume path is dead

Reproduced:

```
$ codex exec resume 00000000-0000-0000-0000-000000000000 --cd . -s read-only --json -o z.txt -
error: unexpected argument '--cd' found
  tip: to pass '--cd' as a value, use '-- --cd'
Usage: codex exec resume <SESSION_ID> [PROMPT]
```

The real option set of `codex exec resume` is exactly:
`-c/--config`, `--last`, `--all`, `--enable`, `--disable`, `-i/--image`, `--strict-config`,
`-m/--model`, `--dangerously-bypass-approvals-and-sandbox`, `--dangerously-bypass-hook-trust`,
`--skip-git-repo-check`, `--ephemeral`, `--ignore-user-config`, `--ignore-rules`,
`--output-schema`, `--json`, `-o/--output-last-message`, `-h/--help`.
**There is no `--cd` and no `-s/--sandbox`.**

`build_exec_argv(resume=...)` emits `exec resume <id> --cd <wt> -s <mode> …`, so every resume call
fails at argument parsing. It is reachable from the `codex_delegate` and `codex_delegate_plan`
schemas, so a client can only ever get a failure out of it.

Note the security consequence too: because `resume` cannot take `-s`, a resumed session runs under
the Codex **default** sandbox, not one we selected. We must never hand a caller a write-capable
path we do not control.

**Required fix (fail closed):** remove `resume` from the delegation argv builder and from both
delegate tool schemas, and reject it explicitly with a dedicated code (`RESUME_UNSUPPORTED`) if it
still arrives in arguments — including via `additionalProperties` bypass. Add the real
`codex exec resume` option list to `CODEX-CLI-FACTS.md` under a new "sub-command flag sets"
section, with the reproduction above.

Do **not** "fix" this by dropping `--cd`/`-s` and pinning process cwd instead: that silently
downgrades a caller-selected sandbox to whatever Codex defaults to, which is exactly the kind of
unearned guarantee this project forbids.

## R2 — CRITICAL — `codex_delegate_review` emits `--color`, which `codex exec review` rejects

Reproduced:

```
$ codex exec review --color never --json -
error: unexpected argument '--color' found
```

`_handle_review` in `codex_delegate/handlers.py` builds
`[bin, "exec", "review", "--json", "--color", "never", …]`. `codex exec review` accepts only:
`-c/--config`, `--uncommitted`, `--base <BRANCH>`, `--enable`, `--commit <SHA>`, `--disable`,
`--strict-config`, `--title <TITLE>`, `-m/--model`, `--dangerously-bypass-approvals-and-sandbox`,
`--dangerously-bypass-hook-trust`, `--skip-git-repo-check`, `--ephemeral`,
`--ignore-user-config`, `--ignore-rules`, `--output-schema`, `--json`,
`-o/--output-last-message`, `-h/--help`.

So `codex_delegate_review` has never worked. Remove `--color never` from that argv, add the review
option list to `CODEX-CLI-FACTS.md`, and cover it by R7.

While you are there: `_handle_review` returns
`"sandbox": "read-only (codex default; no -s flag available for review)"` — a prose sentence in a
field that everywhere else holds an enum value, and that then lands in the audit stream. Return a
structured shape instead, e.g. `"sandbox": "read-only"` plus a separate boolean/short note field,
and keep the explanation in the tool description and README.

## R3 — HIGH — `codex doctor --json` does not return; the doctor tool blocks the client for minutes

Measured directly, with stdin already at `DEVNULL` (so this is not a stdin-inheritance artefact):

```
['--version']      rc 0 in 0.3s
['login','status'] rc 0 in 0.3s
['doctor','--json'] TIMEOUT after 277.7s
```

In `--self-test` the `codex_delegate_doctor` row alone spanned `06:57:23Z → 07:02:40Z` — 5m17s.
An MCP tool call that blocks its client for five minutes is a defect regardless of what the
underlying binary is doing.

**Required fix:** give the doctor probe its own tight bound (≤ 60s, its own constant, not the
delegation timeout), return a structured `{"ok": false, "error": "DOCTOR_TIMEOUT", …}` on expiry
instead of hanging, make sure `build_status_report` never blocks on doctor, and record the
measurement in `CODEX-CLI-FACTS.md` so nobody "optimises" the bound away later.

## R4 — HIGH — read-only probes let the child inherit the MCP server's stdin

`process.default_subprocess_runner` calls `subprocess.run(..., input=input_text)`. When
`input_text is None` — which is every call from `run_readonly_cli`, i.e. version, auth, doctor,
models — Python does not redirect stdin, so the child **inherits the parent's stdin**.

For this server the parent's stdin is the **JSON-RPC channel from the MCP client**. Any child that
reads stdin can consume protocol bytes and corrupt or deadlock the session. This is not theoretical
for a stdio server; it is the standard failure mode.

**Required fix:** pass `stdin=subprocess.DEVNULL` whenever `input_text is None`, and add a test
that asserts the runner never leaves stdin inherited (assert on the kwargs passed to a patched
`subprocess.run`, for both the `input_text=None` and `input_text="…"` branches).

## R5 — MEDIUM — `--self-test` reports a failed tool as PASS

From the same run, the audit line and the table disagree about the same call:

```
{"tool":"codex_delegate_doctor","outcome":"error", …}
…
codex_delegate_doctor  PASS  ok
```

The self-test check accepts "a structured dict came back" as success. A tool that returned
`ok: false` must produce a `FAIL` row with the error code as its detail. Audit `outcome` and the
self-test verdict for the same call must never disagree. Fix the check, and add a unit test that
feeds a failing tool result through the self-test row logic and asserts `FAIL`.

## R6 — MEDIUM — audit lines carry non-scalar payloads and misleading zero fields

Real emitted line (trimmed):

```json
{"tool":"codex_delegate_status","outcome":"ok","sandbox":{"plan_default":"read-only",
 "execute_default":"workspace-write","allowed_modes":["read-only","workspace-write"],
 "danger_full_access_allowed":false,"enforcement_note":"Probe A (CODEX-CLI-FACTS.md): …"},
 "changed_file_count":0,"lane":null,"branch":null,"base_ref":null,"cwd":null, …}
```

Two problems: the whole `sandbox` report object (including the multi-sentence enforcement note) is
dumped into an audit field that the spec defines as a scalar, and read-only tools emit
`changed_file_count: 0` plus a wall of `null`s, which reads as "0 files changed" rather than
"not applicable".

**Required fix:** coerce audit values to scalars in `sanitize_event` — if a value is not
`str/int/float/bool/None`, drop it (or reduce it to a short scalar) rather than serialising an
object; omit keys that are `None` instead of emitting them; only emit `changed_file_count` when the
tool actually produced a change set. Add tests: a dict-valued field is not serialised into the
line; `None` keys are absent; a delegate call still carries `lane`, `goal_sha256_8`,
`changed_file_count`.

## R7 — MEDIUM — the suite cannot catch R1/R2; add a real flag-set conformance test

Introduce a single authoritative table in the package (e.g. `codex_delegate/cli_contract.py`)
holding the accepted option set for each sub-command we actually invoke —
`exec`, `exec resume`, `exec review` — transcribed from `CODEX-CLI-FACTS.md`, with a comment
pointing at the reproduction commands.

Then add tests that take the argv produced by `build_exec_argv` and by the review handler, walk
every token that starts with `-`, and assert it is in that sub-command's set. These tests must fail
if someone re-adds `--color` to review or `--cd` to resume. Wire the same table into
`assert_argv_safe` so a bad flag is rejected before spawn, not only in tests.

This is the fix that matters most: R1 and R2 are symptoms, R7 is the immune system.

## R8 — LOW — `_default_timeout()` can raise `ValueError` and surface as `INTERNAL_ERROR`

`handlers._default_timeout` does `validate_timeout(float(raw))`; a non-numeric
`CODEX_DELEGATE_TIMEOUT_SECONDS` raises `ValueError` before `validate_timeout` can produce a clean
code, and the generic handler turns it into `INTERNAL_ERROR`. Pass the raw value to
`validate_timeout` and let it raise `TIMEOUT_INVALID`. Same audit: check every other env reader for
the same pattern.

## R9 — LOW — the delegation spawn does not pin process cwd

`run_delegation` calls `run(argv, None, timeout_v, input_text=goal_text)` — process cwd is left as
whatever the server was started in, and only `--cd` points at the worktree. Pin the process cwd to
the worktree as well (defence in depth, and consistent with the review path, which already does).
Keep `--cd` — do not replace one with the other.

## R10 — LOW — truncation is silent

`runner._truncate` and `events._truncate` cut strings with no marker, so a truncated `summary`,
`stdout_truncated` or command line is indistinguishable from a complete one. Append an explicit
marker (e.g. `…(truncated)`) and assert it in a test.

## R11 — MEDIUM — `--smoke-delegate` passes without proving anything

The smoke ran green on this host:

```json
{"ok": true, "lane": "codex/smoke", "status": "ok", "sandbox": "read-only", "plan_only": true,
 "summary": "I'll create a new `hello.txt` file in the workspace containing a brief greeting,
             then stop without making any other changes.",
 "thread_id": "019f981a-6803-70a0-9005-f1f54c4bd2e5", "changed_files": [], "elapsed_seconds": 8.74}
SMOKE PASS
```

Look at what that actually says: the smoke goal asks the agent to **create a file**, in a
**read-only** plan lane. The agent announced an intent it could not carry out, the write was
blocked, `changed_files` is empty — and the smoke still printed `SMOKE PASS`. The verdict is
`status == "ok"`, which here only means "the process exited and emitted a completed turn".

Two things are wrong: the goal is incompatible with the lane's own sandbox, and the pass condition
never inspects the answer.

**Required fix:** make the smoke goal a genuinely read-only task with a verifiable answer (ask for
an exact sentinel token in the reply), and make `SMOKE PASS` require *both* `ok is True` **and**
that the sentinel appears in the returned summary. A smoke that cannot fail on a wrong answer is
not a smoke. While you are at it, assert `changed_files == []` for the plan lane — that turns the
read-only guarantee into something the smoke actually checks.

---

## Also required this round

- Re-read your own `guard.py` and `handlers.py` hunting for **more instances of the R1/R2 class**:
  any flag, sub-command, or config key we emit that has not been verified against the real binary.
  `-c model_reasoning_effort="…"` is a known soft spot — `CODEX-CLI-FACTS.md` already records that
  a bogus value does **not** error, so we cannot claim it is enforced. Make sure the code comments
  and both READMEs state that honestly rather than implying enforcement.
- Update `CODEX-CLI-FACTS.md` with a new "sub-command flag sets" section (exec / exec resume /
  exec review) and the doctor timing measurement, each with its reproduction command.
- Update `EVIDENCE-ROUND1.md` (or add `EVIDENCE-ROUND2.md`, Russian) recording: the integrator gate
  results above, every R item, what you changed, and what remains unverified.
- Keep every existing test passing. The suite must grow, not shrink.

## DONE conditions for this round

1. `py -3 -m pytest tests -q` → all green, and strictly more tests than the current 104.
2. No argv emitted anywhere in the package contains a flag absent from the real sub-command's
   option set, enforced by the R7 table at runtime and in tests.
3. `resume` is fail-closed with `RESUME_UNSUPPORTED` and absent from both delegate schemas.
4. The doctor path is bounded and returns a structured timeout rather than blocking.
5. No child process inherits the server's stdin.
6. `--self-test` cannot print PASS for a tool whose result has `ok: false`.
7. Audit lines contain scalars only, with no `None`-valued keys.

## Boundaries (unchanged)

Your shell is git-only — you cannot run python or pytest here; do not claim you did. No push, no
merge, no other branch. Code and identifiers in English; Russian only in `README.md` and the
evidence file. Never invent a Codex flag: if it is not in `CODEX-CLI-FACTS.md`, it does not exist.
