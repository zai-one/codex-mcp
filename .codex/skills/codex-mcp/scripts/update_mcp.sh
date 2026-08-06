#!/usr/bin/env bash
set -euo pipefail
HOME_DIR="${CODEX_MCP_HOME:-$HOME/.local/share/codex-mcp}"
[[ -d "$HOME_DIR/.git" ]] || { echo "No install at $HOME_DIR"; exit 1; }
cd "$HOME_DIR"
git pull --ff-only
if command -v uv >/dev/null 2>&1; then
  uv pip install -e ".[test]"
else
  # shellcheck disable=SC1091
  source .venv/bin/activate
  pip install -e ".[test]"
fi
# shellcheck disable=SC1090
[[ -f "$HOME/.config/codex-mcp/env" ]] && source "$HOME/.config/codex-mcp/env" || true
.venv/bin/python scripts/probe_stdio.py || true
echo "Update attempted. Fix auth with: codex login"
