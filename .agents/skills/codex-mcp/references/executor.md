# Executor mode

1. HARD GATE / `scripts/check_ready.sh`
2. Fill `templates/goal-brief.md`
3. `codex_app_goal` with tight objective + **tokenBudget** (16k–40k typical)
4. Poll job/goal compactly until done
5. Short receipt to user (`templates/receipt-short.md`)

No parallel unbounded goals. Cancel/supersede stale work first.
