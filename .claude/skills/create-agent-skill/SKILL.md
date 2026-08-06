---
name: create-agent-skill
description: >
  Create portable Agent Skills (SKILL.md) for Claude Code, Codex CLI, Cursor,
  and other hosts for this unofficial codex-app-mcp project.
version: 0.5.1
---

# create-agent-skill

Write portable skills with YAML frontmatter + markdown.

## Mirrors

```text
skills/<name>/SKILL.md
.claude/skills/<name>/SKILL.md
.codex/skills/<name>/SKILL.md
.agents/skills/<name>/SKILL.md
```

## Required for this repo

1. Unofficial disclaimer  
2. HARD GATE: Codex CLI + `codex login`  
3. Placeholders only  
4. Success checks (`probe_stdio` / tool names)  
5. Never list (no OAuth in config)  

## Validate

```bash
python scripts/verify_skills.py
```
