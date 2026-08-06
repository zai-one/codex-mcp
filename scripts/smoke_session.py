#!/usr/bin/env python3
"""Smoke Session Protocol v1 for codex-app-mcp."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from codex_app_mcp.server import call_tool  # noqa: E402
from codex_app_mcp.session import reset_sessions_for_tests  # noqa: E402


def main() -> int:
    reset_sessions_for_tests()
    gw = SimpleNamespace(record_tool_call=lambda *a, **k: None)
    b = call_tool(gw, "codex_app_session_begin", {"intent": "auto"})
    assert b.get("ok"), b
    assert len(json.dumps(b)) < 2048
    t = call_tool(gw, "codex_app_session_tick", {"session_id": b.get("session_id")})
    assert t.get("ok"), t
    e = call_tool(gw, "codex_app_session_end", {"session_id": b.get("session_id")})
    assert e.get("ok") and e.get("receipt"), e
    print("SMOKE SESSION PASS", "mode=", b.get("mode"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
