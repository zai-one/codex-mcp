"""Redacted JSON-line audit to stderr."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, TextIO

ALLOWED_AUDIT_FIELDS = frozenset({
    "ts",
    "principal",
    "tool",
    "lane",
    "branch",
    "base_ref",
    "cwd",
    "worktree_path",
    "outcome",
    "status",
    "error",
    "plan_only",
    "sandbox",
    "goal_chars",
    "goal_sha256_8",
    "changed_file_count",
    "elapsed_seconds",
    "thread_id",
    "usage_input_tokens",
    "usage_output_tokens",
})

_DROP_KEYS = frozenset({
    "goal", "prompt", "diff", "patch", "stdout", "stderr", "argv", "token", "key",
})

_SECRET_PATTERNS = [
    re.compile(r"api[_-]?key\s*[:=]", re.IGNORECASE),
    re.compile(r"authorization\s*[:=]", re.IGNORECASE),
    re.compile(r"bearer\s+\S+", re.IGNORECASE),
    re.compile(r"OPENAI_API_KEY", re.IGNORECASE),
    re.compile(r"CODEX_ACCESS_TOKEN", re.IGNORECASE),
    re.compile(r"BEGIN\s+(RSA\s+)?PRIVATE\s+KEY", re.IGNORECASE),
]

_AUTH_PATH_RE = re.compile(r"auth\.json", re.IGNORECASE)
_CODEX_CRED_RE = re.compile(r"[\\/]\.codex[\\/]", re.IGNORECASE)

_SCALAR_TYPES = (str, int, float, bool)


class AuditError(Exception):
    """Raised when an audit event would leak secrets or credential paths."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def goal_fingerprint(goal: str) -> dict[str, Any]:
    """Return goal length and short sha256 — never the raw goal text."""
    text = goal if isinstance(goal, str) else str(goal or "")
    digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:8]
    return {"goal_chars": len(text), "goal_sha256_8": digest}


def _check_raw_value(value: Any) -> None:
    """Fail closed if a raw value looks like a secret or credential path."""
    if value is None:
        return
    if isinstance(value, (dict, list, tuple)):
        text = json.dumps(value, ensure_ascii=False, default=str)
    else:
        text = str(value)
    if _AUTH_PATH_RE.search(text):
        raise AuditError("audit event references auth.json")
    if _CODEX_CRED_RE.search(text):
        raise AuditError("audit event references .codex credential path")
    for pat in _SECRET_PATTERNS:
        if pat.search(text):
            raise AuditError(f"audit event matches secret pattern: {pat.pattern}")


def _coerce_scalar(value: Any) -> Any:
    """Return a JSON scalar, or None to omit the field.

    Audit fields are defined as scalars. Dicts/lists (e.g. a full sandbox report
    object) are dropped rather than serialised into the line. Path-like values
    reduce to their string form.
    """
    if value is None:
        return None
    if isinstance(value, _SCALAR_TYPES):
        return value
    if isinstance(value, (dict, list, tuple, set)):
        return None
    # Path / Enum / other simple objects → short string scalar when cheap.
    try:
        text = str(value)
    except Exception:
        return None
    if len(text) > 500:
        text = text[:500]
    # Avoid dumping reprs of complex objects that look like containers.
    if text.startswith("{") or text.startswith("["):
        return None
    return text


def sanitize_event(event: Mapping[str, Any]) -> dict[str, Any]:
    """Allowlist fields and fail closed on secret-like raw values.

    Checks the **raw** values before redaction so a leak cannot be silently
    emitted. Drops smuggled ``goal``/``prompt``/``diff``/etc. keys. Coerces
    values to scalars; omits ``None`` keys. Raises ``AuditError`` on
    credential/secret content.
    """
    if not isinstance(event, Mapping):
        raise AuditError("audit event must be a mapping")

    # Inspect every raw value first (including keys that will be dropped)
    for key, value in event.items():
        _check_raw_value(key)
        _check_raw_value(value)

    cleaned: dict[str, Any] = {}
    for key, value in event.items():
        if key in _DROP_KEYS:
            continue
        if key not in ALLOWED_AUDIT_FIELDS:
            continue
        scalar = _coerce_scalar(value)
        if scalar is None:
            continue
        cleaned[key] = scalar
    return cleaned


def emit_audit(
    event: Mapping[str, Any],
    *,
    stream: Optional[TextIO] = None,
    principal: str = "local-dev",
) -> None:
    """Best-effort emit one JSON audit line to stderr. Never raises to callers."""
    out = stream if stream is not None else sys.stderr
    try:
        payload = dict(event)
        payload.setdefault("ts", datetime.now(timezone.utc).isoformat())
        payload.setdefault("principal", principal)
        sanitized = sanitize_event(payload)
        line = json.dumps(sanitized, ensure_ascii=False, separators=(",", ":"))
        out.write(line + "\n")
        out.flush()
    except AuditError:
        # Re-raise AuditError only when called via sanitize_event directly;
        # emit is best-effort and must not crash tool calls.
        try:
            out.write(json.dumps({
                "ts": datetime.now(timezone.utc).isoformat(),
                "principal": principal,
                "outcome": "audit_suppressed",
                "error": "AUDIT_REDACTED",
            }, ensure_ascii=False) + "\n")
            out.flush()
        except Exception:
            pass
    except Exception:
        pass
