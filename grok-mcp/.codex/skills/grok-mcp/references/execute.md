# Execute

1. Gate OK.
2. One-line objective + paths + 1–3 tests (see templates/goal-brief.md).
3. `grok_agent_execute` max_turns 8–16.
4. `grok_agent_poll` job_id only; use summary/changed_files/tests.
5. Receipt → human merges `grok/*` (never push/merge).

Anti: whole-chat as objective · parallel jobs · host rewriting files mid-job.
Template: `templates/goal-brief.md`.
