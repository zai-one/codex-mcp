# Operate

`codex_app_session_begin` → loop `codex_app_session_next` → `codex_app_session_end`.

- `card.kind=host_cmd`: run `card.cmd` (user shell)
- `card.kind=mcp_tool`: call `card.tool` with `card.args`
- `card.kind=end`: `codex_app_session_end`
- `done=true` or `force_end`: stop after end
