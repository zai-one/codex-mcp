# Token economy (English)

> ## ⚠️ Unofficial product disclaimer
>
> **Not** an official product of OpenAI, Codex, Anthropic, xAI, or Grok.
> Community software only. Auth remains the local Codex CLI session.

## Idea in one paragraph

The **host agent** (Claude, Cursor, …) orchestrates with short prompts and
tool calls. **Codex app-server** on the same host or a VPS runs the long
coding loop under policy and a **token budget**. Host tokens buy control-plane
work; Codex does the expensive turns.

```text
Host agent  ──short MCP calls──►  codex-app-mcp  ──►  codex app-server
     ▲                                  │
     └──── status / goal / job poll ────┘
```

## Enable economy

| Variable | Effect |
|---|---|
| `CODEX_APP_MCP_ECONOMY=1` | Signals economy-aware defaults and host playbook usage |

Recommended goal budget (playbook default): **`tokenBudget` ≈ 24_000**
(adjust 16k–40k for focused work).

## Playbook tool

```text
codex_app_economy
```

Returns `do` / `dont`, recommended budget, HTTP/VPS hints. No arguments.

## Recommended call sequence

| Step | Tool | Host cost tip |
|---|---|---|
| 1 | `codex_app_status` or `codex_app_economy` | Once per session |
| 2 | `codex_app_goal` | Tight objective + `tokenBudget` |
| 3 | Poll job/goal status | Do not re-send the full objective |
| 4 | Thread compact / archive | When context grows |
| 5 | Keep roots narrow | Only the active project |

## Anti-waste rules

1. **Don't** set huge `tokenBudget` for trivial edits.
2. **Don't** dump full event buffers into the host chat.
3. **Don't** load unrelated roots or MCP servers without allowlists.
4. **Don't** put OpenAI/Codex OAuth into `CODEX_APP_MCP_HTTP_TOKEN`.
5. Prefer one active goal; interrupt/cancel instead of stacking.

## VPS angle

HTTP is **native** on this package. Authenticate Codex on the VPS, run
bearer HTTP on loopback, TLS reverse proxy, connect remote Claude/FastMCP.
See [install/vps.md](install/vps.md).

## Related

- [install/en.md](install/en.md) · [REFERENCE.md](REFERENCE.md) · [README.md](../README.md)
