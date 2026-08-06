#!/usr/bin/env bash
set -euo pipefail
command -v codex >/dev/null || { echo "FAIL: no codex"; exit 1; }
D="${CODEX_MCP_HOME:-$HOME/.local/share/codex-mcp}"
[[ -f "$HOME/.config/codex-mcp/env" ]] && source "$HOME/.config/codex-mcp/env" || true
[[ -x "$D/.venv/bin/python" ]] || { echo "no install $D"; exit 1; }
exec "$D/.venv/bin/python" "$D/scripts/probe_stdio.py"
