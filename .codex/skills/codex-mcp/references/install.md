# Install

```bash
curl -fsSL https://raw.githubusercontent.com/zai-one/codex-mcp/main/scripts/install.sh \
  | bash -s -- --project "<PROJECT_ROOT>"
codex login
source ~/.config/codex-mcp/env
~/.local/share/codex-mcp/.venv/bin/python ~/.local/share/codex-mcp/scripts/probe_stdio.py
```

Windows: `scripts/install.ps1` then `codex login`.

Wire: merge `~/.config/codex-mcp/mcp/*.snippet.json`, restart host, call `codex_app_status`.

See `docs/EASY.md`.
