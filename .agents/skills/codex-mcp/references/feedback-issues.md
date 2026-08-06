# Feedback → GitHub Issue

Repo: `zai-one/codex-mcp`.

Use `templates/issue-bug.md` / `issue-improvement.md` and:

```bash
python scripts/draft_issue.py --repo zai-one/codex-mcp --type bug --title "..." --body-file body.md
# add --create if gh is authenticated and user wants it filed
```

Redact OAuth, tokens, secrets. Free-form allowed.
