# Interaction modes (general)

Use MCP as a **specialist worker**, not a second copy of the host brain.

| Mode | Host does | MCP / Codex does | Token bias |
|---|---|---|---|
| **brainstorm** | Clarify goals, options | `codex consult/status` / `review` | Cheap |
| **executor** | One tight brief | `codex_app_goal` + poll | Expensive — intentional |
| **verifier** | Acceptance criteria | status/poll/tests/diff only | Cheap |
| **operate** | Orchestrate | mix per task size | Balanced |
| **feedback** | Confirm file Issue | none (GitHub) | Free |

## Rules of thumb

1. If the user is deciding **what** to build → brainstorm.
2. If the path is clear and code must change → executor (one job).
3. If code already changed → verifier before more execute.
4. If MCP/tooling is the problem → triage, then feedback after repeated pain.
5. Never re-implement a long coding loop in the host chat when MCP is healthy.

## Communication

- Host replies to humans: short receipts ([`../templates/receipt-short.md`](../templates/receipt-short.md)).
- Prefer paths and summaries over paste dumps.
- One active job; cancel stale jobs before starting another.
