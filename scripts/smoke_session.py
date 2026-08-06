#!/usr/bin/env python3
from __future__ import annotations
import json, sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from codex_app_mcp.server import call_tool
from codex_app_mcp.session import reset_sessions_for_tests

def main() -> int:
    reset_sessions_for_tests()
    gw = SimpleNamespace(record_tool_call=lambda *a, **k: None)
    b = call_tool(gw, "codex_app_session_begin", {"intent": "auto", "goal": "review", "host_budget": "small"})
    assert b["ok"] and b["protocol"] == "session/v1.2"
    n = call_tool(gw, "codex_app_session_next", {"session_id": b["session_id"]})
    assert n.get("card")
    call_tool(gw, "codex_app_session_end", {"session_id": b["session_id"]})
    print("SMOKE v1.2 PASS", b["mode"], n["card"]["kind"])
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
