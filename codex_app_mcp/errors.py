"""Structured errors shared by the app-server gateway layers."""

from __future__ import annotations

from typing import Any


class GatewayError(ValueError):
    """A policy, validation, or lifecycle failure safe to return through MCP."""

    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


def error_envelope(code: str, message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": code, "message": message, **details}
