#!/usr/bin/env bash
set -euo pipefail
D="${CODEX_MCP_HOME:-$HOME/.local/share/codex-mcp}"
[[ -d "$D/.git" ]] || { echo "no install $D"; exit 1; }
cd "$D" && git pull --ff-only
command -v uv >/dev/null && uv pip install -e ".[test]" || { source .venv/bin/activate && pip install -e ".[test]"; }
[[ -f "$HOME/.config/codex-mcp/env" ]] && source "$HOME/.config/codex-mcp/env" || true
.venv/bin/python scripts/probe_stdio.py || true
