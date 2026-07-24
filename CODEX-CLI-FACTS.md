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
