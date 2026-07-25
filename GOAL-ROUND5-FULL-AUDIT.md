# GOAL-ROUND5 — full-surface hunt, verify, fix

Four rounds have hardened the paths we happened to exercise. This round attacks the rest: every
tool, every option, every error path, every resource, **including the ones nobody has ever run**.

The rule for the whole round: **find → prove → fix → cover**. A finding without a concrete failing
scenario is noise. A fix without a test that would fail before it is theatre. If you cannot prove
something is broken, say what you checked and why it holds — do not pad the list.

You cannot run python or pytest in this lane (git-only shell). The integrator runs every gate, so
write code and tests that pass on his first run, and never claim you executed anything.

## Known and out of scope

`-s workspace-write` currently grants no writes on this host (vendor-side, re-confirmed
2026-07-25 — see `CODEX-CLI-FACTS.md`). Do **not** try to work around it, and do **not** propose
`--dangerously-bypass-approvals-and-sandbox`. Assume the execute path will be exercised again once
the platform allows it: your job is to make sure that when it works, everything around it is
already correct.

## A. Options and paths never exercised even once

Each of these ships in the tool schema but has never been run end to end. For each: trace it,
decide what breaks, fix it, cover it.

1. **`output_schema`** — validated, written to a temp file, passed as `--output-schema`. Is that
   temp file removed on **every** exit path, including argv-build failure, `CODEX_MISSING`,
   timeout, and an exception between mkstemp and the try block? Is it created outside the worktree
   so it never pollutes the diff? What happens if the schema is valid JSON but not an object, or
   16 001 chars, or contains a surrogate pair?
2. **`ephemeral`** — emits `--ephemeral`. Is it in the `exec` contract set? Does it interact with
   anything that assumes a persisted session?
3. **`codex_delegate_review`** — never run against a real diff. Walk the whole handler: what does
   it do when the lane exists but has no changes, when `--base` names a ref that does not exist,
   when both `uncommitted` and `base_ref` are supplied, when `instructions` is empty/whitespace,
   when the worktree was deleted from disk while the branch still exists?
4. **`codex_delegate_models`** — the reduction step. What if `codex debug models` returns an
   object without `models`, a model entry missing `slug`, or a 5 MB payload? Bound it.
5. **`codex_delegate_lanes`** — `git worktree list --porcelain` parsing. Detached HEAD entries,
   `prunable` entries, a worktree whose directory was deleted, paths containing spaces, a branch
   named `codex/x` in the main repo that is *not* a worktree. Does it ever raise instead of
   returning a structured result?

## B. This repository lives at a path containing a space

`C:\Users\codex\Documents\Projects\MCP\Codex CLI`. Every path we hand to git, to `--cd`, to
`-o`, to `--output-schema`, and every path we compare in the allowlist, carries a space. Audit
every place a path becomes a string: argv construction (we never shell out through a shell, so
quoting must **not** be added by hand — verify nobody did), `worktree_path_for_lane`,
`resolve_lanes_parent`, the porcelain parsers in `collect_diff` and `list_lanes` (git quotes paths
containing spaces or non-ASCII with `"` and octal escapes — do we decode that?), and the audit
redaction. Add tests with a space and a non-ASCII character in the path.

## C. Resource and state leaks

1. Temp files: prove by construction that `run_delegation` cannot leak the last-message file or
   the schema file on any path. Same for anything the review handler creates.
2. Process handles: `run_bounded` abandons pipes when descendants keep them after a tree kill.
   Does it leave a `Popen` object whose reader threads never join? Is that acceptable for a
   long-lived stdio server, or does it accumulate per timed-out call? Bound it or document why it
   cannot leak.
3. Worktrees: a failed delegation can leave a created worktree behind. Is that intended (evidence
   for the operator) or garbage? Decide, document, and make `prepare_worktree`'s contract explicit.

## D. Concurrency — this is a long-lived server

Two `tools/call` for the same lane, or two different lanes in the same repo, can arrive close
together. Analyse: `git worktree add` racing itself, the same lane reused while a run is in
flight, `BASE_DIRTY` flapping because another lane's run touched the main tree, temp-file name
collisions. Decide the policy (serialise per repo? reject a lane that is already running?),
implement it, and cover it. A wrong answer here corrupts a user's repository, so err toward
refusing.

## E. Protocol robustness (`server.py`)

Drive `handle_jsonrpc` with hostile input and make sure it never raises and never returns a
malformed frame: missing `jsonrpc`, `id` of every JSON type including `null` and a float, `params`
as a list or a string, `tools/call` with `arguments` as a list, a `Content-Length` header that
lies (too large / too small / negative / non-numeric), a body that is valid JSON but not an
object, an unknown method as a notification, and a 5 MB argument blob. Batch requests (a JSON
array) — we do not support them; make sure the refusal is a proper JSON-RPC error, not a crash.

## F. Guard bypasses — try to defeat your own checks

1. Can any flag reach argv without passing `assert_flags_in_contract`? Trace the review path
   specifically — it builds argv by hand.
2. `-c` overrides: `assert_argv_safe` rejects `-c sandbox_mode=…`. What about `--config
   sandbox_mode=…`, `-csandbox_mode=…`, `-c` with the key quoted, `-c` with leading whitespace, or
   two `-c` where the first is benign? Close what is closable and say plainly what is not.
3. `normalize_lane`: `CODEX/x`, `codex/CODEX/x`, `codex//x`, a slug of 300 chars, a slug that is a
   Windows reserved device name (`con`, `nul`, `prn`, `aux`, `com1`) — the last one creates an
   unopenable directory on Windows. Reject them.
4. `validate_codex_bin`: a path whose basename is `codex.cmd` but which sits in a world-writable
   directory; a UNC path; a path with a trailing dot or space (Windows strips them).
5. Audit: can any tool result carry a value that `sanitize_event` turns into a leak — a repo path
   under `C:\Users\<name>\.codex\`, or a goal whose first 8 sha bytes are logged alongside a
   `changed_files` list that names a secret file?

## G. Honesty invariants — the class of bug this project keeps producing

Four rounds produced four instances of the same class: something reported success while it had not
succeeded (`ok:true` with a refusal; `SMOKE PASS` proving nothing; `PASS` on a failed doctor;
`RESULT: PASS` on a fully skipped table). Hunt for the remaining instances **systematically**:
enumerate every place the package decides `ok` / `status` / a printed verdict, and for each ask
"what is the input that makes this claim while the thing did not happen?". Fix what you find and
add the regression. This section matters more than any other; treat a finding here as critical.

## Deliverables

- All fixes applied, each with a regression test that fails without the fix.
- `EVIDENCE-ROUND5.md` (Russian): per section — what you attacked, what broke (with the concrete
  scenario), what you changed, what you verified by reasoning only, and what remains unknown
  because it needs a live run.
- `CODEX-CLI-FACTS.md` extended if you establish any new CLI fact (with its reproduction).
- One commit on this lane branch: `fix(codex-delegate): full-surface audit round`.

## DONE conditions

1. `py -3 -m pytest tests -q` → all green, strictly more than 169 tests.
2. Every section A–G answered: either a fix + regression, or an explicit "checked X by tracing Y,
   holds because Z" in the evidence file.
3. No new flag/subcommand/config key outside `cli_contract.py` / `CODEX-CLI-FACTS.md`.
4. No claim anywhere that is not traceable to code or a real measurement.

## Boundaries

git-only shell; never claim you ran tests. No push, no merge, no other branch. Code and
identifiers English; Russian only in README/evidence. Never invent a Codex flag.
