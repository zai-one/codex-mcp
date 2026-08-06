from __future__ import annotations
import json
from types import SimpleNamespace
from codex_app_mcp.server import call_tool, tool_schemas
from codex_app_mcp.session import reset_sessions_for_tests, scrub_secrets

def setup_function():
    reset_sessions_for_tests()

def _gw():
    return SimpleNamespace(record_tool_call=lambda *a, **k: None)

def test_next_listed():
    assert "codex_app_session_next" in {t["name"] for t in tool_schemas()}

def test_navigator_loop():
    gw = _gw()
    b = call_tool(gw, "codex_app_session_begin", {"intent": "auto", "goal": "fix y", "host_budget": "small"})
    assert b["protocol"] == "session/v1.2"
    sid = b["session_id"]
    n = call_tool(gw, "codex_app_session_next", {"session_id": sid})
    assert n.get("card", {}).get("kind") in {"host_cmd", "mcp_tool", "end"}
    assert len(json.dumps(n)) < 1536
    e = call_tool(gw, "codex_app_session_end", {"session_id": sid})
    assert e.get("budget_report")

def test_scrub():
    assert "REDACTED" in scrub_secrets("api_key=xyz")
