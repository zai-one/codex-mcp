# Codex CLI — verified facts (live probes, 2026-07-24)

Binary under test: `codex-cli 0.144.1`, resolved by `shutil.which("codex")` to
`C:\Users\codex\AppData\Roaming\npm\codex.CMD` (npm shim). `subprocess.run([that_path, "--version"])`
works without `shell=True`.

Auth: `codex login status` → `Logged in using ChatGPT`. Credentials live in `CODEX_HOME`
(`C:\Users\codex\.codex\auth.json`). **Never read that file.**

## Subcommands that exist

`exec` (alias `e`), `review`, `login`, `logout`, `mcp`, `plugin`, `mcp-server`, `app-server`,
`remote-control`, `app`, `completion`, `update`, `doctor`, `sandbox`, `debug`, `apply`, `resume`,
`archive`, `delete`, `unarchive`, `fork`, `cloud`, `exec-server`, `features`, `help`.

There is **no** `codex models` and **no** `codex inspect`. The model catalog is
`codex debug models` (JSON on stdout). Feature flags are `codex features list`.

## `codex exec` options (verified from `codex exec --help`)

```
codex exec [OPTIONS] [PROMPT]
codex exec resume [OPTIONS] [SESSION_ID] [PROMPT]
codex exec review [OPTIONS] [PROMPT]
```

| Flag | Meaning |
|---|---|
| `-c, --config <key=value>` | TOML config override |
| `--enable / --disable <FEATURE>` | feature toggle |
| `--strict-config` | error on unknown fields in config.toml |
| `-i, --image <FILE>...` | attach images |
| `-m, --model <MODEL>` | model slug |
| `--oss`, `--local-provider` | open-source / local provider |
| `-p, --profile <NAME>` | layer `$CODEX_HOME/<name>.config.toml` |
| `-s, --sandbox <MODE>` | `read-only` \| `workspace-write` \| `danger-full-access` |
| `--dangerously-bypass-approvals-and-sandbox` | **forbidden** |
| `--dangerously-bypass-hook-trust` | **forbidden** |
| `-C, --cd <DIR>` | agent working root |
| `--add-dir <DIR>` | extra writable dirs |
| `--skip-git-repo-check` | allow running outside a git repo |
| `--ephemeral` | do not persist session files |
| `--ignore-user-config` | do not load `$CODEX_HOME/config.toml`; auth still uses `CODEX_HOME` |
| `--ignore-rules` | do not load execpolicy `.rules` files |
| `--output-schema <FILE>` | JSON Schema **file** for final response shape |
| `--color <always\|never\|auto>` | color |
| `--json` | JSONL events on stdout |
| `-o, --output-last-message <FILE>` | write agent's last message to file |

`codex exec` has **no** `-a/--ask-for-approval` and **no** `--max-turns`. There is no turn cap
in this CLI — the only bound available to a caller is wall-clock timeout.

`codex exec review [OPTIONS] [PROMPT]` extra flags: `--uncommitted`, `--base <BRANCH>`,
`--commit <SHA>`, `--title <TITLE>`. It has **no `--cd` and no `-s/--sandbox`** — its working
directory is the process cwd and its sandbox is the Codex default.

`codex exec resume [SESSION_ID] [PROMPT]` extra flags: `--last`, `--all`.

## Sub-command flag sets (authoritative for codex_delegate)

These sets were transcribed from live rejections / help of **codex-cli 0.144.1** and are
mirrored in `codex_delegate/cli_contract.py`. **Do not invent flags outside these sets.**

### `codex exec` (plain)

Accepted (from `codex exec --help`):
`-c/--config`, `--enable`, `--disable`, `--strict-config`, `-i/--image`, `-m/--model`,
`--oss`, `--local-provider`, `-p/--profile`, `-s/--sandbox`,
`--dangerously-bypass-approvals-and-sandbox`, `--dangerously-bypass-hook-trust`,
`-C/--cd`, `--add-dir`, `--skip-git-repo-check`, `--ephemeral`, `--ignore-user-config`,
`--ignore-rules`, `--output-schema`, `--color`, `--json`, `-o/--output-last-message`,
`-h/--help`.

### `codex exec resume` — NO `--cd`, NO `-s/--sandbox`

Reproduction (2026-07-25, integrator):

```
$ codex exec resume 00000000-0000-0000-0000-000000000000 --cd . -s read-only --json -o z.txt -
error: unexpected argument '--cd' found
  tip: to pass '--cd' as a value, use '-- --cd'
Usage: codex exec resume <SESSION_ID> [PROMPT]
```

Accepted option set of `codex exec resume` is exactly:
`-c/--config`, `--last`, `--all`, `--enable`, `--disable`, `-i/--image`, `--strict-config`,
`-m/--model`, `--dangerously-bypass-approvals-and-sandbox`, `--dangerously-bypass-hook-trust`,
`--skip-git-repo-check`, `--ephemeral`, `--ignore-user-config`, `--ignore-rules`,
`--output-schema`, `--json`, `-o/--output-last-message`, `-h/--help`.

**Security consequence:** because resume cannot take `-s`, a resumed session runs under the
Codex **default** sandbox, not one the caller selected. `codex_delegate` therefore
**fail-closes** resume (`RESUME_UNSUPPORTED`) and does not emit `exec resume` at all.

### `codex exec review` — NO `--cd`, NO `-s/--sandbox`, NO `--color`

Reproduction (2026-07-25, integrator):

```
$ codex exec review --color never --json -
error: unexpected argument '--color' found
```

Accepted option set of `codex exec review` is exactly:
`-c/--config`, `--uncommitted`, `--base <BRANCH>`, `--enable`, `--commit <SHA>`, `--disable`,
`--strict-config`, `--title <TITLE>`, `-m/--model`,
`--dangerously-bypass-approvals-and-sandbox`, `--dangerously-bypass-hook-trust`,
`--skip-git-repo-check`, `--ephemeral`, `--ignore-user-config`, `--ignore-rules`,
`--output-schema`, `--json`, `-o/--output-last-message`, `-h/--help`.

Working directory is the process cwd; sandbox is the Codex default (probe A: read-only).

### `codex doctor --json` hang (bounded by package)

Measured with stdin already at `DEVNULL` (so this is not a stdin-inheritance artefact):

```
['--version']      rc 0 in 0.3s
['login','status'] rc 0 in 0.3s
['doctor','--json'] TIMEOUT after 277.7s
```

`codex doctor --json` can hang for minutes. `codex_delegate` bounds the probe with
`DOCTOR_TIMEOUT_SECONDS` (≤ 60s) and returns structured `DOCTOR_TIMEOUT` on expiry.
Do not raise this bound without a new live measurement.

## `-s workspace-write` needs the user `config.toml` (corrected 2026-07-26, round 6)

**The 2026-07-25 reading below was wrong, and it shaped rounds 1–5.** It is kept verbatim because
the observations are real; only the conclusion was.

Root cause: `--ignore-user-config`, which every failing probe below carries. It discards
`~/.codex/config.toml`, and that file sets:

```toml
[windows]
sandbox = "elevated"
```

Without it Codex cannot establish a write-capable sandbox on Windows and degrades to read-only.
Same repo, same lane, same goal, on `codex-cli 0.144.1`, one flag apart:

```
CODEX_DELEGATE_IGNORE_USER_CONFIG=1 -> ok:false EXECUTE_NO_CHANGES changed_file_count:0  11.2s
CODEX_DELEGATE_IGNORE_USER_CONFIG=0 -> ok:true                     changed_file_count:1  72.9s
```

Repeat run at the same settings: `ok:true`, 27.8s — reproducible, not a fluke. The lane sat at
`D:\ZAI\MCP\codex-lanes\...`, a path absent from every `trust_level = "trusted"` entry, so project
trust is **not** the mechanism; `[windows] sandbox` is.

This also answers the question left open below: probe C ran **without** `--ignore-user-config`.

The feature-flag signal quoted further down (`elevated_windows_sandbox … removed`) is still true
today and is **not** the cause — writes succeed with those same flags retired.

---

### Original 2026-07-25 entry (conclusion superseded, observations intact)

Probe C below did grant writes earlier the same day. Hours later, on the **same** `codex-cli
0.144.1`, **the same command shape, in the same directory**, every write is refused:

```
$ echo "Create a file dprobe.md containing exactly OK, then reply DONE." \
  | codex exec --cd . -s workspace-write -m gpt-5.4-mini --ignore-user-config --json -o od.log -
"text":"I can't create `dprobe.md` in this workspace because the filesystem is read-only."
# dprobe.md: No such file or directory
```

Reproduced in three settings: a plain `git init` repo, a **linked git worktree** (what this
package creates), and with `--cd` equal to / different from the process cwd. All read-only.

The flag does reach the policy handed to the model — this is not an argv bug on our side:

```
$ codex -s workspace-write debug prompt-input
"<permissions instructions>\nFilesystem sandboxing defines which files can be read or written.
 `sandbox_mode` is `workspace-write`: The sandbox permits reading files, and editing files in
 `cwd` and `writable_roots`. …"
```

Correlating signal — the Windows sandbox feature flags are retired in this build:

```
$ codex features list | grep -iE "sandbox|windows"
elevated_windows_sandbox             removed            false
experimental_windows_sandbox         removed            false
use_linux_sandbox_bwrap              removed            false
```

Consistent reading: when Codex cannot establish a write-capable sandbox on Windows it degrades to
read-only rather than running unsandboxed. **Why probe C succeeded earlier is not explained** — do
not invent a story for it; treat write capability as something to re-verify per session.

> **Round 6 correction.** The degradation is real, but its trigger is `--ignore-user-config`, not
> the build. Probe C succeeded because it ran without that flag. Every probe in this section carries
> it — that is the variable nobody isolated. See the corrected heading above.

Consequence for `codex_delegate`: the read-only tools (`_plan`, `_review`, `_status`, `_models`,
`_lanes`) work regardless. The **execute** path produces changes when the server runs with
`CODEX_DELEGATE_IGNORE_USER_CONFIG=0`; with the historical default `1` it cannot, and `delegate()`
reports that honestly instead of returning `ok:true` with an empty diff — see `EXECUTE_NO_CHANGES`.
The only flag that would bypass the sandbox is `--dangerously-bypass-approvals-and-sandbox`, which
this package forbids everywhere and which is **not** needed for any of this.

## Sandbox behaviour — measured, not assumed

Probe A — `codex exec --cd <repo> -m gpt-5.4-mini --ignore-user-config --json -o a.txt -`,
prompt `Create a file A.txt containing exactly OK, then reply DONE.`:

```
{"type":"item.completed","item":{"id":"item_0","type":"agent_message",
 "text":"I can't create `A.txt` in this environment because the filesystem is read-only."}}
```
→ **default sandbox for `codex exec` is `read-only`, and it is enforced on Windows.**

Probe B — same, plus `-c sandbox_mode="workspace-write"` (no `-s`):

```
{"type":"item.completed","item":{"id":"item_0","type":"agent_message",
 "text":"I can't create `bprobe.md` in this environment because the workspace is read-only."}}
```
file not created →  **`-c sandbox_mode=...` does NOT change the sandbox for `codex exec`;
the CLI `-s` default wins.** The key itself is real and validated
(`-c sandbox_mode="bogus"` → `Error: unknown variant 'bogus', expected one of 'read-only',
'workspace-write', 'danger-full-access'`), it is simply overridden. Never claim sandbox
control through `-c`.

Probe C — same, with `-s workspace-write`:

```
{"type":"item.completed","item":{"id":"item_3","type":"command_execution",
 "command":"pwsh.exe -Command 'Get-Content -Raw cprobe.md'","aggregated_output":"OK\r\n","exit_code":0}}
{"type":"item.completed","item":{"id":"item_4","type":"agent_message","text":"DONE"}}
```
file created with content `OK` → **`-s workspace-write` grants writes under the `--cd` root.**

`-c model_reasoning_effort="bogus"` does **not** error, so that override is not validated at
parse time. `model_reasoning_effort` is nevertheless a real config.toml key (present in the
operator's own `config.toml`). Treat `-c model_reasoning_effort=<value>` as best-effort:
validate the value ourselves, never claim CLI-level enforcement.

## `--json` event stream shape (observed)

Line-delimited JSON on stdout:

```
{"type":"thread.started","thread_id":"019f95bd-359c-7361-aa60-0c73f39fa60e"}
{"type":"turn.started"}
{"type":"item.completed","item":{"id":"item_1","type":"agent_message","text":"..."}}
{"type":"item.completed","item":{"id":"item_3","type":"command_execution","command":"...",
  "aggregated_output":"...","exit_code":0,"status":"completed"}}
{"type":"item.completed","item":{"id":"item_0","type":"error","message":"..."}}
{"type":"turn.completed","usage":{"input_tokens":42863,"cached_input_tokens":33408,
  "output_tokens":509,"reasoning_output_tokens":231}}
```

Diagnostic `ERROR`/`WARN` lines from the Rust runtime go to **stderr**, not stdout, and are not
JSON. Exit code was `0` even when the agent reported it could not perform the task — **exit code
alone is not a success signal**; the event stream must be inspected.

`-o <FILE>` receives the final agent message as plain text (no trailing newline observed).

## Model catalog (from `codex debug models`, 2026-07-24)

Slugs: `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`, `gpt-5.5`, `gpt-5.4`, `gpt-5.4-mini`,
`gpt-5.3-codex-spark`, `codex-auto-review`.
Union of `supported_reasoning_levels[].effort`: `low`, `medium`, `high`, `xhigh`, `max`, `ultra`.

## Operator environment note

The operator's `$CODEX_HOME/config.toml` loads many skills and external MCP servers; during the
probes those produced `HTTP 500` transport errors and an
`Exceeded skills context budget of 2%` error item. A delegation lane must not inherit that
surface — see `--ignore-user-config` in the goal spec.
