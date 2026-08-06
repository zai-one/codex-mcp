# Tools

| Mode | Tools (prefer these only) |
|---|---|
| ready? | `grok_agent_status` |
| playbook | `grok_agent_economy` **once**/session |
| brainstorm | `grok_agent_consult`, `grok_agent_review` |
| execute | `grok_agent_execute` → `grok_agent_poll` (+ cancel/fix if needed) |
| verify | poll/status; optional review — **no** new execute unless fail |
| ops | `grok_delegate_doctor` / models/inspect if broken |

Legacy `grok_delegate_*` = advanced; default to `grok_agent_*`.
