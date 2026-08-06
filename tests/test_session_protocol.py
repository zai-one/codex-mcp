from __future__ import annotations

import json
from types import SimpleNamespace

from codex_app_mcp.server import call_tool, tool_schemas
from codex_app_mcp.session import reset_sessions_for_tests, session_begin, scrub_secrets


def setup_function() -> None:
    reset_sessions_for_tests()


def _gw():
    return SimpleNamespace(record_tool_call=lambda *a, **k: None)


def test_session_tools_listed() -> None:
    names = {t["name"] for t in tool_schemas()}
    assert "codex_app_session_begin" in names


def test_session_begin_plan_budget() -> None:
    r = session_begin("auto", goal="implement fix oauth=leaktestvalue", host_budget="small")
    assert r["ok"] and r["protocol"] == "session/v1.1"
    assert "budget" in r and isinstance(r["plan"], list) and len(r["plan"]) <= 5
    assert len(json.dumps(r)) < 1536
    assert "leaktestvalue" not in json.dumps(r)


def test_call_tool_budget_force_end() -> None:
    gw = _gw()
    b = call_tool(gw, "codex_app_session_begin", {"intent": "install", "host_budget": "tiny"})
    sid = b["session_id"]
    call_tool(gw, "codex_app_session_tick", {"session_id": sid})
    t2 = call_tool(gw, "codex_app_session_tick", {"session_id": sid})
    assert t2["force_end"] is True
    e = call_tool(gw, "codex_app_session_end", {"session_id": sid})
    assert e["budget_report"]["was_capped"] is True


def test_scrub() -> None:
    assert "REDACTED" in scrub_secrets("api_key=abc123xyz")
