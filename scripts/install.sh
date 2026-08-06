#!/usr/bin/env bash
# codex-mcp one-command installer (unofficial community project)
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/zai-one/codex-mcp/main/scripts/install.sh | bash
#   curl -fsSL ... | bash -s -- --project "$HOME/code/myapp"
#   bash scripts/install.sh --project /path/to/project
#
# Installs Python (via uv if needed), this package, env, MCP snippets, probe.
# CANNOT fully automate: Codex CLI install + `codex login` (interactive).

set -euo pipefail

REPO_URL="${CODEX_MCP_REPO_URL:-https://github.com/zai-one/codex-mcp.git}"
INSTALL_DIR="${CODEX_MCP_HOME:-$HOME/.local/share/codex-mcp}"
PROJECT_ROOT=""
SKIP_CLONE=0
NONINTERACTIVE=0

RED=$'\033[31m'; GRN=$'\033[32m'; YLW=$'\033[33m'; BLD=$'\033[1m'; RST=$'\033[0m'
log()  { printf '%s\n' "$*"; }
ok()   { printf '%s✓%s %s\n' "$GRN" "$RST" "$*"; }
warn() { printf '%s!%s %s\n' "$YLW" "$RST" "$*"; }
err()  { printf '%s✗%s %s\n' "$RED" "$RST" "$*" >&2; }
die()  { err "$*"; exit 1; }
header() {
  printf '\n%s%s%s\n' "$BLD" "$*" "$RST"
  printf '%s\n' "────────────────────────────────────────"
}

usage() {
  cat <<'USAGE'
codex-mcp installer (unofficial — not OpenAI/Codex)

  bash scripts/install.sh [options]
  curl -fsSL https://raw.githubusercontent.com/zai-one/codex-mcp/main/scripts/install.sh | bash -s -- [options]

Options:
  --project PATH     Project folder the MCP may touch
  --home PATH        Install location (default: ~/.local/share/codex-mcp)
  --repo URL         Git clone URL
  --yes              Non-interactive defaults where safe
  --help             This help

After install you STILL must:
  1) have Codex CLI on PATH
  2) run: codex login
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --project) PROJECT_ROOT="${2:-}"; shift 2 ;;
    --home) INSTALL_DIR="${2:-}"; shift 2 ;;
    --repo) REPO_URL="${2:-}"; shift 2 ;;
    --yes|-y) NONINTERACTIVE=1; shift ;;
    --help|-h) usage; exit 0 ;;
    *) die "Unknown option: $1 (try --help)" ;;
  esac
done

if [ -f "pyproject.toml" ] && grep -q 'name = "codex-app-mcp"' pyproject.toml 2>/dev/null; then
  INSTALL_DIR="$(pwd -P)"
  SKIP_CLONE=1
fi

header "1/7  Unofficial codex-mcp installer"
log "Install dir: $INSTALL_DIR"
log "NOT an official OpenAI/Codex product. No OAuth written into config."

have_cmd() { command -v "$1" >/dev/null 2>&1; }

header "2/7  Python 3.10+"
PY=""
pick_python() {
  local c
  for c in python3.13 python3.12 python3.11 python3.10 python3 python; do
    if have_cmd "$c"; then
      if "$c" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
        PY="$c"
        return 0
      fi
    fi
  done
  return 1
}

ensure_uv() {
  if have_cmd uv; then
    ok "uv found: $(command -v uv)"
    return 0
  fi
  warn "uv not found — installing uv (managed Python if needed)"
  if have_cmd curl; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
  elif have_cmd wget; then
    wget -qO- https://astral.sh/uv/install.sh | sh
  else
    die "Need curl/wget to install uv, or install Python 3.10+ yourself"
  fi
  export PATH="$HOME/.local/bin:$PATH"
  have_cmd uv || die "uv installed but not on PATH — open a new terminal and re-run"
  ok "uv ready"
}

USE_UV=0
if pick_python; then
  ok "Python OK: $PY ($($PY -c 'import sys; print(".".join(map(str, sys.version_info[:3])))'))"
else
  warn "No Python 3.10+ on PATH"
  ensure_uv
  uv python install 3.12 >/dev/null
  PY="$(uv python find 3.12)"
  ok "Using uv-managed Python: $PY"
fi
if have_cmd uv; then USE_UV=1; fi

header "3/7  Fetch codex-mcp"
if [ "$SKIP_CLONE" -eq 1 ]; then
  ok "Already inside repo checkout: $INSTALL_DIR"
else
  have_cmd git || die "git is required"
  if [ -d "$INSTALL_DIR/.git" ]; then
    ok "Existing install — pulling latest"
    git -C "$INSTALL_DIR" pull --ff-only || warn "git pull failed — using existing tree"
  else
    mkdir -p "$(dirname "$INSTALL_DIR")"
    git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
    ok "Cloned to $INSTALL_DIR"
  fi
fi
cd "$INSTALL_DIR"

header "4/7  Virtualenv + package"
if [ "$USE_UV" -eq 1 ]; then
  uv venv --clear .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate
  uv pip install -e ".[test]"
else
  "$PY" -m venv --clear .venv 2>/dev/null || { rm -rf .venv; "$PY" -m venv .venv; }
  # shellcheck disable=SC1091
  source .venv/bin/activate
  python -m pip install -U pip setuptools wheel
  python -m pip install -e ".[test]"
fi
ok "Package installed (venv: $INSTALL_DIR/.venv)"

VENV_PY="$INSTALL_DIR/.venv/bin/python"
VENV_BIN="$INSTALL_DIR/.venv/bin/codex-app-mcp"
[ -x "$VENV_BIN" ] || die "codex-app-mcp entrypoint missing after install"

header "5/7  Project paths"
if [ -z "$PROJECT_ROOT" ]; then
  if [ "$NONINTERACTIVE" -eq 1 ]; then
    PROJECT_ROOT="$HOME"
    warn "No --project; defaulting ALLOWED_ROOTS to \$HOME (broad — tighten later)"
  elif [ -t 0 ]; then
    printf 'Project folder this MCP may use [%s]: ' "$HOME"
    read -r ans || true
    PROJECT_ROOT="${ans:-$HOME}"
  else
    PROJECT_ROOT="$HOME"
    warn "Non-tty and no --project; using \$HOME"
  fi
fi
PROJECT_ROOT="$(cd "$PROJECT_ROOT" 2>/dev/null && pwd -P)" || die "Project path does not exist: set --project"

CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/codex-mcp"
mkdir -p "$CONFIG_DIR"
ENV_FILE="$CONFIG_DIR/env"
cat > "$ENV_FILE" <<ENV
# Generated by codex-mcp install.sh — NO secrets / OAuth here
# Unofficial community gateway — not OpenAI/Codex official
export CODEX_APP_MCP_ALLOWED_ROOTS="$PROJECT_ROOT"
export CODEX_APP_MCP_ECONOMY=1
ENV
ok "Wrote $ENV_FILE"

BIN_DIR="$HOME/.local/bin"
mkdir -p "$BIN_DIR"
WRAPPER="$BIN_DIR/codex-mcp"
cat > "$WRAPPER" <<WRAP
#!/usr/bin/env bash
set -euo pipefail
# shellcheck disable=SC1090
source "$ENV_FILE"
exec "$VENV_BIN" "\$@"
WRAP
chmod +x "$WRAPPER"
ok "Launcher: $WRAPPER"

MCP_DIR="$CONFIG_DIR/mcp"
mkdir -p "$MCP_DIR"
cat > "$MCP_DIR/claude_desktop.snippet.json" <<JSON
{
  "mcpServers": {
    "codex-app": {
      "command": "$WRAPPER",
      "args": [],
      "env": {}
    }
  }
}
JSON
cat > "$MCP_DIR/cursor.snippet.json" <<JSON
{
  "mcpServers": {
    "codex-app": {
      "command": "$WRAPPER",
      "args": []
    }
  }
}
JSON
ok "Client snippets: $MCP_DIR/"

header "6/7  Codex CLI (required — not skippable)"
CODEX_OK=0
if have_cmd codex; then
  ok "codex on PATH: $(command -v codex)"
  CODEX_OK=1
else
  err "Codex CLI NOT found on PATH"
  log ""
  log "Install Codex CLI from OpenAI docs for your OS, then:"
  log "  codex login"
  log "  codex --version"
  log ""
  log "This installer cannot log you into Codex (interactive product login)."
fi

header "7/7  Probe / self-check"
# shellcheck disable=SC1090
source "$ENV_FILE"
set +e
if [ -f "$INSTALL_DIR/scripts/probe_stdio.py" ]; then
  "$VENV_PY" "$INSTALL_DIR/scripts/probe_stdio.py"
  ST=$?
else
  "$VENV_PY" -c "import codex_app_mcp; print('import ok', codex_app_mcp.__version__)"
  ST=$?
fi
set -e

if [ $ST -eq 0 ]; then
  ok "Probe/import PASS"
else
  warn "Probe did not fully pass (often missing codex login / app-server)"
  log "After: codex login  →  source $ENV_FILE && $VENV_PY $INSTALL_DIR/scripts/probe_stdio.py"
fi

header "Done — next 60 seconds"
cat <<DONE

${BLD}What you have now${RST}
  Install:   $INSTALL_DIR
  Env:       $ENV_FILE
  Launcher:  $WRAPPER
  Snippets:  $MCP_DIR/

${BLD}If you are not a developer — do only this:${RST}
  1) Install Codex CLI (if step 6 failed)
  2) Run:  ${GRN}codex login${RST}
  3) Run:  ${GRN}source $ENV_FILE && $VENV_PY $INSTALL_DIR/scripts/probe_stdio.py${RST}
  4) Claude Desktop / Cursor: merge $MCP_DIR/claude_desktop.snippet.json (restart app)
  5) In chat: ${GRN}codex_app_status${RST} then ${GRN}codex_app_economy${RST}

${YLW}Unofficial community project — not affiliated with OpenAI/Codex.${RST}
Docs: $INSTALL_DIR/docs/START_HERE.md · $INSTALL_DIR/docs/EASY.md

DONE

if [ "$CODEX_OK" -ne 1 ]; then
  warn "Finished install, but Codex CLI is missing — exit 2"
  exit 2
fi
if [ "${ST:-1}" -ne 0 ]; then
  warn "Finished install, but probe failed — exit 2 (usually: codex login)"
  exit 2
fi
ok "Fully ready"
exit 0
