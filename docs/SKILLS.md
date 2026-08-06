# Skills (Claude · Codex · Cursor · others)

> Unofficial project. Skills teach **agents** how to install and operate this MCP
> **after** Codex CLI is installed and `codex login` succeeded.

## Portable layout

| Path | Host |
|---|---|
| `skills/<name>/` | Canonical |
| `.claude/skills/<name>/` | Claude Code |
| `.codex/skills/<name>/` | Codex CLI |
| `.agents/skills/<name>/` | Agent Skills / Cursor-friendly |

## Bundled skills

| Skill | Purpose |
|---|---|
| **install-codex-mcp** | Setup only — hard gate on CLI+login |
| **codex-app-mcp** | Runtime economy + HTTP/VPS |
| **create-agent-skill** | Author portable skills |

## Copy to personal dirs

```bash
# Claude
cp -R .claude/skills/* ~/.claude/skills/
# Codex
cp -R .codex/skills/* ~/.codex/skills/
# Portable
cp -R .agents/skills/* ~/.agents/skills/
```

## Validate

```bash
python scripts/verify_skills.py
```
