# Install

```bash
curl -fsSL https://raw.githubusercontent.com/zai-one/grok-mcp/main/scripts/install.sh | bash -s -- --project "<ROOT>"
grok login
```

Windows: `scripts/install.ps1` then `grok login`.

Wire host JSON under `~/.config/grok-mcp/mcp/` · restart host · `grok_agent_status`.

Success: gate green, tools visible, no secrets in config. Details: `docs/EASY.md`.
