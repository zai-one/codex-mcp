---
name: install-codex-mcp
description: >
  Install codex-app-mcp with the one-command script only. Require Codex CLI +
  codex login. Do not invent multi-step manual pip guides.
version: 0.5.3
---

# install-codex-mcp

> Unofficial. Not OpenAI/Codex. No OAuth in MCP config.

## Only supported install

```bash
curl -fsSL https://raw.githubusercontent.com/zai-one/codex-mcp/main/scripts/install.sh \
  | bash -s -- --project "<PROJECT_ROOT>"
codex login
source ~/.config/codex-mcp/env
~/.local/share/codex-mcp/.venv/bin/python ~/.local/share/codex-mcp/scripts/probe_stdio.py
```

Windows: `scripts/install.ps1`. Docs: `docs/EASY.md`.

## HARD GATE

Without **Codex CLI** + **`codex login`** stop and tell the user.

## Wire host

Merge `~/.config/codex-mcp/mcp/*.snippet.json`. Restart app.

## Do not

- Write long manual pip/venv tutorials
- Put OAuth into HTTP bearer
- Claim success without probe
