from __future__ import annotations

from codex_app_mcp.economy import economy_playbook
from codex_app_mcp.server import call_tool, tool_schemas


class _Dummy:
    def record_tool_call(self, *args, **kwargs) -> None:
        return None


def test_economy_tool_schema_and_call() -> None:
    names = {t["name"] for t in tool_schemas()}
    assert "codex_app_economy" in names
    result = call_tool(_Dummy(), "codex_app_economy", {})  # type: ignore[arg-type]
    assert result["ok"] is True
    assert "Unofficial" in result["disclaimer"]
    assert economy_playbook()["economy"] is True
