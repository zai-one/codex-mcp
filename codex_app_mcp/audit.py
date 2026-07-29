"""Secret-safe operational audit for MCP tool calls.

Only a small scalar allowlist is ever emitted. Prompts, tool arguments,
responses, patches, command lines, environment variables, and credentials are
intentionally excluded.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, TextIO

_ALLOWED_FIELDS = frozenset(
    {
        "ts",
        "principal",
        "tool",
        "action",
        "outcome",
        "error",
        "method",
        "thread_id",
        "turn_id",
        "job_id",
        "lane",
        "sandbox",
        "cwd",
        "goal_chars",
        "goal_sha256_8",
        "elapsed_ms",
    }
)
_SECRET_RE = re.compile(
    r"(authorization|bearer\s+\S+|api[_-]?key|access[_-]?token|"
    r"begin\s+(?:rsa\s+)?private\s+key|auth\.json|[\\/]\.codex[\\/])",
    re.IGNORECASE,
)


def _enabled() -> bool:
    return os.environ.get("CODEX_APP_MCP_AUDIT", "1").strip().lower() not in {
        "0",
        "false",
        "off",
        "no",
    }


def goal_fingerprint(value: Any) -> dict[str, Any]:
    text = value if isinstance(value, str) else ""
    return {
        "goal_chars": len(text),
        "goal_sha256_8": hashlib.sha256(text.encode("utf-8")).hexdigest()[:8],
    }


def sanitize_event(event: Mapping[str, Any]) -> dict[str, Any]:
    """Return a scalar allowlist or a single redaction marker."""
    clean: dict[str, Any] = {}
    try:
        for key, value in event.items():
            if key not in _ALLOWED_FIELDS or value is None:
                continue
            if not isinstance(value, (str, int, float, bool)):
                continue
            text = str(value)
            if _SECRET_RE.search(text):
                return {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "outcome": "audit_suppressed",
                    "error": "AUDIT_REDACTED",
                }
            clean[key] = value if not isinstance(value, str) else value[:500]
    except Exception:
        return {
            "ts": datetime.now(timezone.utc).isoformat(),
            "outcome": "audit_suppressed",
            "error": "AUDIT_REDACTED",
        }
    return clean


def emit_audit(event: Mapping[str, Any], *, stream: Optional[TextIO] = None) -> None:
    """Best-effort JSONL emission; audit can never fail an MCP operation."""
    if not _enabled():
        return
    payload = dict(event)
    payload.setdefault("ts", datetime.now(timezone.utc).isoformat())
    payload.setdefault("principal", os.environ.get("CODEX_APP_MCP_PRINCIPAL", "local"))
    line = json.dumps(
        sanitize_event(payload),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    try:
        if stream is not None:
            stream.write(line + "\n")
            stream.flush()
            return
        raw_path = os.environ.get("CODEX_APP_MCP_AUDIT_PATH")
        if raw_path:
            path = Path(raw_path).expanduser().resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
            return
        sys.stderr.write(line + "\n")
        sys.stderr.flush()
    except Exception:
        pass
