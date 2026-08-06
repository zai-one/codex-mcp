# Install

```bash
curl -fsSL https://raw.githubusercontent.com/zai-one/codex-mcp/main/scripts/install.sh | bash -s -- --project "<ROOT>"
codex login
```

Windows: `scripts/install.ps1` then `codex login`.

Wire host JSON under `~/.config/codex-mcp/mcp/` · restart host · `codex_app_status`.

Success: gate green, tools visible, no secrets in config. Details: `docs/EASY.md`.
