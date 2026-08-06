# Easy install (plain English)

> Unofficial community project — **not** OpenAI / Codex official.

## Can a non-developer do this?

**Mostly yes**, if you can paste one command and finish `codex login` in a browser.

| Step | Who does it | Hard? |
|---|---|---|
| Install Python + this MCP + config files | **1 command** below | Easy |
| Install **Codex CLI** | You (product installer) | Medium — once |
| `codex login` | You (browser/device) | Easy but **required** |
| Paste JSON into Claude / Cursor | You (or agent skill) | Easy |
| Day-to-day use | Host agent calls tools | Easy |

There is **no** fully automatic login: Codex account auth is interactive on purpose.

## One command (macOS / Linux)

```bash
curl -fsSL https://raw.githubusercontent.com/zai-one/codex-mcp/main/scripts/install.sh \
  | bash -s -- --project "$HOME/code/my-project"
```

Replace the project path with a real folder.

What the script does:

1. Finds or installs Python 3.10+ (via [uv](https://github.com/astral-sh/uv) if needed)
2. Clones this repo to `~/.local/share/codex-mcp`
3. Creates a venv and installs the package
4. Writes env + a `codex-mcp` launcher in `~/.local/bin`
5. Writes Claude/Cursor MCP JSON snippets
6. Runs `scripts/probe_stdio.py` and tells you if `codex login` is still needed

## One command (Windows PowerShell)

```powershell
irm https://raw.githubusercontent.com/zai-one/codex-mcp/main/scripts/install.ps1 | iex
```

## After the script

```bash
codex login
source ~/.config/codex-mcp/env
~/.local/share/codex-mcp/.venv/bin/python ~/.local/share/codex-mcp/scripts/probe_stdio.py
```

Wire Claude Desktop: merge `~/.config/codex-mcp/mcp/claude_desktop.snippet.json`, restart Claude.

In chat: **codex_app_status** → **codex_app_economy** → small goal with low `tokenBudget`.

## Honesty meter

| Claim | Reality |
|---|---|
| “Truly one command forever” | **Almost** — CLI login is still a second human step |
| “Works without Codex CLI” | **No** — this is only a gateway |
| “Official OpenAI” | **No** — community |

More: [START_HERE.md](START_HERE.md) · [install/en.md](install/en.md)
