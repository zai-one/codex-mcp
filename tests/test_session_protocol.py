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
    assert "codex_app_session_tick" in names
    assert "codex_app_session_end" in names


def test_session_begin_compact() -> None:
    r = session_begin("auto")
    assert r["ok"] is True
    assert r["protocol"] == "session/v1"
    assert "skill_ref" in r
    assert len(json.dumps(r)) < 2048


def test_call_tool_session_flow() -> None:
    gw = _gw()
    b = call_tool(gw, "codex_app_session_begin", {"intent": "brainstorm"})
    assert b["ok"] is True
    sid = b["session_id"]
    t = call_tool(gw, "codex_app_session_tick", {"session_id": sid})
    assert t["ok"] is True
    assert len(t.get("host_message", "")) <= 500
    e = call_tool(
        gw,
        "codex_app_session_end",
        {"session_id": sid, "suggest_issue": True, "note": "oauth=secret123456789012"},
    )
    assert e["ok"] is True
    assert e.get("suggest_issue") is True
    assert "secret123456789012" not in (e.get("issue_draft") or "")


def test_invalid_intent() -> None:
    r = session_begin("nope")
    assert r["ok"] is False


def test_scrub() -> None:
    assert "REDACTED" in scrub_secrets("api_key=abc")
