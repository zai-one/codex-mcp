"""Parse Codex ``--json`` JSONL event streams. Never raises."""

from __future__ import annotations

import json
from typing import Any, Optional

try:
    from .guard import TRUNCATION_MARKER
except ImportError:  # pragma: no cover
    from guard import TRUNCATION_MARKER

_TRUNC = 500
_MAX_COMMANDS = 50


def _truncate(text: Any, limit: int = _TRUNC) -> str:
    s = "" if text is None else str(text)
    if len(s) <= limit:
        return s
    marker = TRUNCATION_MARKER
    if limit <= len(marker):
        return marker[:limit]
    return s[: limit - len(marker)] + marker


def parse_event_stream(stdout: str) -> dict[str, Any]:
    """Parse Codex ``--json`` JSONL. Never raises.

    Shape matches CODEX-CLI-FACTS.md (thread.started, item.completed,
    turn.completed / turn.failed). Non-JSON lines increment unparsed_lines.
    """
    thread_id: Optional[str] = None
    last_message: Optional[str] = None
    agent_messages = 0
    commands: list[dict[str, Any]] = []
    errors: list[str] = []
    usage: Optional[dict[str, Any]] = None
    turn_completed = False
    turn_failed = False
    unparsed_lines = 0

    for raw_line in (stdout or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, TypeError, ValueError):
            unparsed_lines += 1
            continue
        if not isinstance(event, dict):
            unparsed_lines += 1
            continue

        etype = event.get("type")
        if etype == "thread.started":
            tid = event.get("thread_id")
            if tid is not None:
                thread_id = _truncate(tid)
        elif etype == "turn.completed":
            turn_completed = True
            u = event.get("usage")
            if isinstance(u, dict):
                usage = u
        elif etype == "turn.failed":
            turn_failed = True
            msg = event.get("message") or event.get("error") or "turn failed"
            errors.append(_truncate(msg))
        elif etype == "item.completed":
            item = event.get("item")
            if not isinstance(item, dict):
                continue
            itype = item.get("type")
            if itype == "agent_message":
                agent_messages += 1
                msg = item.get("text")
                if msg is not None:
                    last_message = _truncate(msg)
            elif itype == "command_execution":
                if len(commands) < _MAX_COMMANDS:
                    commands.append({
                        "command": _truncate(item.get("command")),
                        "exit_code": item.get("exit_code"),
                        "status": item.get("status"),
                    })
            elif itype == "error":
                errors.append(_truncate(item.get("message") or "error"))

    return {
        "thread_id": thread_id,
        "last_message": last_message,
        "agent_messages": agent_messages,
        "commands": commands,
        "errors": errors,
        "usage": usage,
        "turn_completed": turn_completed,
        "turn_failed": turn_failed,
        "unparsed_lines": unparsed_lines,
    }
