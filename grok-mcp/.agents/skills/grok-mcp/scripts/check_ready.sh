#!/usr/bin/env bash
set -euo pipefail
command -v grok >/dev/null || { echo "FAIL: no grok"; exit 1; }
PY="${GROK_MCP_HOME:-$HOME/.local/share/grok-mcp}/.venv/bin/python"
[[ -x "$PY" ]] || PY=python3
[[ -f "$HOME/.config/grok-mcp/env" ]] && source "$HOME/.config/grok-mcp/env" || true
exec "$PY" -m grok_delegate --self-test
