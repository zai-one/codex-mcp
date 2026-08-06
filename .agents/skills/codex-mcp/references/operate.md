# Operate

1. `codex_app_session_begin` with goal + host_budget=small (default).
2. Read `plan`, `budget`, `host_script`, `deny_tools`.
3. Call plan tools in order; tick after each meaningful tool (`tool_used`, `step_done`).
4. If `force_end` or plan done → `codex_app_session_end`.
5. Do not re-plan in chat; do not open extra references if plan is enough.
