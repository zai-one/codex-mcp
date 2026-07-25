# GOAL-ROUND4 — skeptic pass on the integrator delta

The previous skeptic round (R1–R11) reviewed **your** round-1 code. Everything below landed
**after** that round, written by the integrator, and has therefore never been attacked by anyone.
That is exactly the code most likely to be wrong: it deals with process signals, process trees,
abandoned pipes, and a verdict rule that must not lie in either direction.

Your job: **try to break the delta**, then fix what you break. Assume it is wrong until you have a
concrete reason to believe otherwise. A round that concludes "looks fine" without a reproduction
attempt is a failed round.

Current gate state on the integrator's host (do not re-litigate, and you cannot re-run these —
your shell is git-only):

- `py -3 -m pytest tests -q` → **148 passed**
- `py -3 -m codex_delegate --self-test` → **RESULT: PASS (1 skipped)** in 48s
- `py -3 -m codex_delegate --smoke-delegate` → **SMOKE PASS** with sentinel
- doctor bound measured: **45.2s** wall clock against a declared 45s (was 240s before the fix)

## The delta under review

### D1 — `codex_delegate/process.py`: `run_bounded` + `kill_process_tree`

Replaced `subprocess.run(timeout=...)` with `Popen` + `communicate(timeout=…)`, and on
`TimeoutExpired`: kill the whole process tree, then one bounded re-drain
(`TREE_KILL_GRACE_SECONDS`), then abandon the pipes rather than block.

Reason it exists: `subprocess.run(timeout=)` kills only the direct child, then keeps blocking in
`communicate()` while grandchildren hold the inherited stdout/stderr handles. `codex doctor`
spawns such grandchildren — measured 240s of wall clock at a declared 45s bound.

Attack it. Concrete questions to answer with code, not opinion:

1. **Deadlock on large output.** `Popen(stdout=PIPE, stderr=PIPE)` + `communicate()` is safe, but
   is every path actually going through `communicate()`? Is there any path that writes stdin and
   reads a pipe without it? A delegation goal can be 60 000 chars and Codex can emit a large JSONL
   stream — check that neither direction can fill an OS pipe buffer and stall.
2. **The second `communicate()` after the kill.** Is it reachable in a state where the first one
   already consumed part of the output? Does the caller get partial `stdout` that the JSONL parser
   then treats as a valid truncated stream? What does `parse_event_stream` do with half a line?
3. **`kill_process_tree` on a process that already exited.** `taskkill /F /T /PID` against a dead
   pid returns non-zero and prints to stderr. Is that swallowed? Can a recycled PID be killed —
   i.e. is there a window where we `taskkill` a PID that the OS has reassigned to something else?
   If that window exists, say so explicitly in a comment even if it cannot be closed.
4. **POSIX branch.** `start_new_session=True` is only set when `os.name != "nt"`, and
   `os.killpg(os.getpgid(pid), SIGKILL)` runs on POSIX. If `start_new_session` were ever dropped,
   `killpg` would signal **our own** process group — including the MCP server itself. Is there any
   configuration in which the group is not new? Add a guard or an assertion so this can never
   become a self-kill.
5. **`FileNotFoundError` semantics.** It is caught around `Popen` only. Can a missing binary now
   surface differently from before (e.g. `NotADirectoryError`, `PermissionError`, or a `.cmd` shim
   that exists but fails to launch)? The `missing: True` contract is relied on by
   `CODEX_MISSING` — verify every caller still gets it.
6. **`default_git_runner` regression.** It now post-patches `stderr` to `"git not found"` when
   `missing` is set. Is the original stderr lost in a case where it carried something useful?
7. **Windows `taskkill` availability.** It is assumed present on PATH. What happens on a stripped
   host where it is not? The fallback is `proc.kill()` — confirm that path returns rather than
   raises.

### D2 — `codex_delegate/__main__.py`: `SKIP` verdict

`self_test_tool_row` gained a third state. `VENDOR_SKIP_ERROR_CODES = {"DOCTOR_TIMEOUT"}` yields a
row with `ok: False, skipped: True`, printed as `SKIP`, and `RESULT` counts only PASS/FAIL.

Attack it:

1. **Does this reintroduce the R5 defect by another door?** R5 was "a failing tool must not read
   as healthy". Is `SKIP` a disguised PASS? Argue both sides and make the code match the honest
   answer. In particular: if *every* tool row skipped, `RESULT: PASS (N skipped)` would be printed
   while nothing was actually verified. Is that acceptable? If not, fix it — e.g. require at least
   one real PASS, or fail when the skip ratio is total.
2. **Scope creep of the skip set.** `DOCTOR_TIMEOUT` is currently the only entry. Is there any way
   for a *our-side* failure to surface as `DOCTOR_TIMEOUT`? If the bound is misconfigured to 0.1s
   by env, our own misconfiguration would be excused as "vendor-side". Check whether the doctor
   timeout is env-tunable, and if so, close that hole.
3. **Audit consistency (the original R5 invariant).** The audit line for the doctor call records
   `outcome: "error"`. The table now prints `SKIP`. Is that a disagreement of the same kind R5
   condemned, or a legitimate distinction? Decide, document the decision in the code, and make the
   evidence file state it plainly.
4. Exit code: `--self-test` returns 0 when only skips occurred. Is that right for CI use? If a CI
   consumer needs "nothing was skipped", is there a way to ask for it?

### D3 — test delta in `tests/test_codex_delegate.py`

New: `TestTimeoutKillsProcessTree` (3 tests), `TestSelfTestRow` extended to 5. Rewritten:
`TestStdinIsolation` (3 tests) now patch `subprocess.Popen` instead of `subprocess.run`.

Attack it:

1. The `_fake_popen` double is hand-rolled. Does it faithfully model `Popen` for the paths under
   test — in particular, does `communicate()` raising `TimeoutExpired` **twice** get exercised, and
   does the double's `poll()` behaviour match what `kill_process_tree` branches on?
2. `test_tree_kill_targets_descendants` constructs the double through a patched `Popen` and then
   calls `kill_process_tree` on it. Is it actually asserting the Windows branch on Windows and the
   POSIX branch elsewhere, or does it silently pass by skipping the branch it cannot reach?
3. Do the rewritten stdin tests still prove the R4 property (**no inheritance**), or do they now
   only prove "some stdin kwarg was passed"? R4 is a security property — the test must fail if
   someone removes the `DEVNULL`.
4. Is there any test that would still pass if `run_bounded` silently returned before killing the
   tree? If yes, that test is theatre — strengthen it.

### D4 — docs delta

`README.md` (wiring snippet rewritten to the verified absolute-path form; doctor section rewritten
with the 240s→45.2s measurement) and `EVIDENCE-ROUND3-INTEGRATOR.md` (new).

Attack it: does any sentence claim more than was measured? Every number must trace to a command
someone actually ran. If a claim cannot be traced, either weaken it or delete it.

## Also required

- Re-run the R7 discipline over the delta: no new flag, subcommand or config key may have crept in
  without being in `cli_contract.py` / `CODEX-CLI-FACTS.md`.
- If you find nothing wrong in a section, say so **with the reasoning that convinced you**, not as
  a verdict. "I checked X by tracing path Y and here is why it holds" is acceptable;
  "looks correct" is not.
- Record findings in `EVIDENCE-ROUND4.md` (Russian): what you attacked, what broke, what you
  changed, what you could not verify without running code.

## DONE conditions

1. `py -3 -m pytest tests -q` → all green, strictly more than 148 tests.
2. Every question in D1 and D2 answered in code or in `EVIDENCE-ROUND4.md`.
3. No claim in README/evidence that is not traceable to a real measurement.
4. One git commit on this lane branch, message
   `fix(codex-delegate): skeptic pass on integrator delta`.

## Boundaries (unchanged)

Shell is git-only — you cannot run python or pytest; never claim you did. No push, no merge, no
other branch. Code and identifiers in English; Russian only in README/evidence. Never invent a
Codex flag: if it is not in `CODEX-CLI-FACTS.md`, it does not exist.
