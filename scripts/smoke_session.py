#!/usr/bin/env python3
from __future__ import annotations
import json, sys
from pathlib import Path
from types import SimpleNamespace
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from codex_app_mcp.server import call_tool
from codex_app_mcp.session import reset_sessions_for_tests

def main() -> int:
    reset_sessions_for_tests()
    gw = SimpleNamespace(record_tool_call=lambda *a, **k: None)
    b = call_tool(gw, "codex_app_session_begin", {"intent": "auto", "goal": "review module", "host_budget": "small"})
    assert b.get("ok") and b.get("protocol") == "session/v1.1"
    assert 0 <= len(b.get("plan") or []) <= 5
    assert len(json.dumps(b)) < 1536
    t = call_tool(gw, "codex_app_session_tick", {"session_id": b["session_id"]})
    assert "force_end" in t
    e = call_tool(gw, "codex_app_session_end", {"session_id": b["session_id"]})
    assert e.get("budget_report")
    print("SMOKE SESSION v1.1 PASS", b.get("mode"), len(b.get("plan") or []))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
