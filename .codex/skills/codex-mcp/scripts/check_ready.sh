#!/usr/bin/env bash
set -euo pipefail
if ! command -v codex >/dev/null 2>&1; then
  echo "FAIL: codex CLI not on PATH"
  exit 1
fi
echo "OK: codex -> $(command -v codex)"
HOME_DIR="${CODEX_MCP_HOME:-$HOME/.local/share/codex-mcp}"
# shellcheck disable=SC1090
[[ -f "$HOME/.config/codex-mcp/env" ]] && source "$HOME/.config/codex-mcp/env" || true
if [[ -x "$HOME_DIR/.venv/bin/python" && -f "$HOME_DIR/scripts/probe_stdio.py" ]]; then
  set +e
  "$HOME_DIR/.venv/bin/python" "$HOME_DIR/scripts/probe_stdio.py"
  exit $?
fi
echo "WARN: install not found at $HOME_DIR — run install.sh"
exit 1
