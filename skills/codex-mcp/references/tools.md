# Tools

**Plan compiler = source of truth** (`session_begin.plan`).

| Tool | Role |
|---|---|
| `codex_app_session_begin` | mode + plan + budget + deny + host_script |
| `codex_app_session_tick` | step / budget_remaining / force_end |
| `codex_app_session_end` | receipt + budget_report + lesson |

Other tools: only if listed in plan/recommended for this session.
