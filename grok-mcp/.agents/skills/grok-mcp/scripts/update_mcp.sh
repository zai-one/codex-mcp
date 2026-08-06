#!/usr/bin/env bash
set -euo pipefail
D="${GROK_MCP_HOME:-$HOME/.local/share/grok-mcp}"
[[ -d "$D/.git" ]] || { echo "no install $D"; exit 1; }
cd "$D" && git pull --ff-only
command -v uv >/dev/null && uv pip install -e ".[test]" || { source .venv/bin/activate && pip install -e ".[test]"; }
[[ -f "$HOME/.config/grok-mcp/env" ]] && source "$HOME/.config/grok-mcp/env" || true
.venv/bin/python -m grok_delegate --self-test || true
