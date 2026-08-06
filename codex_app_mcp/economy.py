"""Token-economy helpers for host agents using codex-app-mcp."""

from __future__ import annotations

import os
from typing import Any, Mapping

_TRUE = frozenset({"1", "true", "yes", "on"})

ECONOMY_DEFAULT_TOKEN_BUDGET = 24_000


def economy_enabled(env: Mapping[str, str] | None = None) -> bool:
    source = env if env is not None else os.environ
    return str(source.get("CODEX_APP_MCP_ECONOMY", "")).strip().lower() in _TRUE


def economy_playbook() -> dict[str, Any]:
    return {
        "ok": True,
        "economy": True,
        "prerequisite": (
            "Codex CLI must be installed and `codex login` completed on this host "
            "before the gateway can talk to app-server. This package is only a bridge."
        ),
        "goal": (
            "Orchestrate from Claude/Cursor with short prompts; let Codex on this "
            "host/VPS run the long coding loop under a token budget."
        ),
        "do": [
            "Confirm codex CLI + login before other tools.",
            "Call codex_app_status (or codex_app_economy) once per session.",
            "Prefer codex_app_goal with a tight objective + tokenBudget (e.g. 16k–40k).",
            "Use thread compact / archive when context grows; avoid replaying full histories.",
            "Poll job/goal status instead of re-sending the entire objective.",
            "Keep CODEX_APP_MCP_ALLOWED_ROOTS narrow to the active project.",
            "On VPS: expose bearer HTTP behind TLS; host agents only see tool results.",
        ],
        "dont": [
            "Don't put OpenAI/Codex OAuth into CODEX_APP_MCP_HTTP_TOKEN.",
            "Don't set huge tokenBudget for trivial edits.",
            "Don't load unrelated roots or MCP servers without allowlists.",
            "Don't dump full event buffers into the host chat.",
        ],
        "recommended": {
            "tokenBudget": ECONOMY_DEFAULT_TOKEN_BUDGET,
            "CODEX_APP_MCP_ECONOMY": "1",
            "http": "127.0.0.1 + TOKEN_FILE; reverse-proxy TLS for remote Claude",
        },
        "disclaimer": (
            "Unofficial community project. Not affiliated with, endorsed by, or "
            "supported by OpenAI, Codex, Anthropic, xAI, or Grok."
        ),
    }
