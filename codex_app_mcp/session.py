"""Adaptive Session Protocol v1 for codex-app-mcp."""

from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from typing import Any, Mapping

from .economy import ECONOMY_DEFAULT_TOKEN_BUDGET, economy_enabled
from . import __version__ as SERVER_VERSION

INTENTS = frozenset(
    {"brainstorm", "execute", "verify", "install", "update", "triage", "feedback", "auto"}
)
_HOST_MSG_MAX = 500
_PAYLOAD_SOFT_MAX = 1800
_SECRETISH = re.compile(
    r"(?i)("
    r"api[_-]?key\s*[:=]\s*\S+"
    r"|oauth\s*[:=]\s*\S+"
    r"|authorization\s*[:=]\s*\S+"
    r"|bearer\s+[a-z0-9._\-]{12,}"
    r"|sk-[a-z0-9]{10,}"
    r"|" + "gh" + "p_" + r"[a-z0-9]+"
    r"|xai-[a-z0-9]+"
    r")"
)

_ROUTES: dict[str, dict[str, Any]] = {
    "install": {
        "mode": "install",
        "skill_ref": "references/install.md",
        "tools": [],
        "next": "One-command install + codex login; re-call session_begin.",
    },
    "update": {
        "mode": "update",
        "skill_ref": "references/update.md",
        "tools": [],
        "next": "Run update_mcp.sh then session_begin.",
    },
    "triage": {
        "mode": "triage",
        "skill_ref": "references/security.md",
        "tools": ["codex_app_status", "codex_app_doctor"],
        "next": "Fix CLI/login/roots; feedback after 2+ product bugs.",
    },
    "brainstorm": {
        "mode": "brainstorm",
        "skill_ref": "references/brainstorm.md",
        "tools": ["codex_app_review", "codex_app_status"],
        "next": "Light review/status only — no large goals.",
    },
    "execute": {
        "mode": "execute",
        "skill_ref": "references/execute.md",
        "tools": ["codex_app_goal", "codex_app_job"],
        "next": "Tight goal + tokenBudget → compact job poll → session_end.",
    },
    "verify": {
        "mode": "verify",
        "skill_ref": "references/verify.md",
        "tools": ["codex_app_job", "codex_app_status"],
        "next": "Compact status only; no event dumps.",
    },
    "feedback": {
        "mode": "feedback",
        "skill_ref": "references/feedback.md",
        "tools": [],
        "next": "Scrubbed issue draft via templates/issue.md.",
    },
    "operate": {
        "mode": "operate",
        "skill_ref": "references/operate.md",
        "tools": ["codex_app_status", "codex_app_economy"],
        "next": "Pick brainstorm/execute/verify by task size.",
    },
}

_sessions: dict[str, dict[str, Any]] = {}
_compact_session = False


def session_compact_active() -> bool:
    return _compact_session or economy_enabled()


def enable_session_economy() -> None:
    global _compact_session
    _compact_session = True
    os.environ.setdefault("CODEX_APP_MCP_ECONOMY", "1")


def scrub_secrets(text: str) -> str:
    return _SECRETISH.sub("[REDACTED]", str(text or ""))


def _clip(s: str, n: int) -> str:
    s = str(s or "")
    return s if len(s) <= n else s[: n - 1] + "…"


def _json_size(obj: Any) -> int:
    return len(json.dumps(obj, ensure_ascii=False, default=str))


def _shrink(obj: dict[str, Any]) -> dict[str, Any]:
    out = dict(obj)
    for k, v in list(out.items()):
        if isinstance(v, str) and len(v) > 400:
            out[k] = _clip(v, 400)
    if _json_size(out) > _PAYLOAD_SOFT_MAX:
        out = {
            "ok": out.get("ok", True),
            "protocol": "session/v1",
            "session_id": out.get("session_id"),
            "mode": out.get("mode"),
            "next_step": out.get("next_step"),
            "truncated": True,
        }
    return out


def resolve_gate() -> dict[str, Any]:
    binary_ok = bool(shutil.which("codex"))
    roots_raw = os.environ.get("CODEX_APP_MCP_ALLOWED_ROOTS", "").strip()
    roots = [p.strip() for p in roots_raw.split(os.pathsep) if p.strip()][:8]
    roots_ok = bool(roots)
    # Auth cannot be proven without app-server; report binary + roots only.
    auth_ok = binary_ok  # login checked by doctor/status when CLI present
    ready = binary_ok and roots_ok
    return {
        "ready": ready,
        "cli": "codex",
        "binary_ok": binary_ok,
        "auth_ok": auth_ok,
        "roots_ok": roots_ok,
        "roots": roots,
        "server": SERVER_VERSION,
    }


def _auto_mode(gate: Mapping[str, Any], intent: str) -> str:
    if not gate.get("binary_ok"):
        return "install"
    if not gate.get("roots_ok"):
        return "triage"
    if intent in INTENTS and intent != "auto":
        return intent
    return "operate"


def session_begin(intent: str = "auto") -> dict[str, Any]:
    intent_n = (intent or "auto").strip().lower()
    if intent_n not in INTENTS:
        return {
            "ok": False,
            "error_code": "INTENT_INVALID",
            "error": f"intent must be one of: {', '.join(sorted(INTENTS))}",
            "protocol": "session/v1",
        }
    enable_session_economy()
    gate = resolve_gate()
    mode = _auto_mode(gate, intent_n)
    if mode in {"execute", "brainstorm", "verify", "operate", "feedback"} and not gate.get("ready"):
        mode = "install" if not gate.get("binary_ok") else "triage"
    route = _ROUTES.get(mode, _ROUTES["operate"])
    tools = list(route["tools"])
    if gate.get("ready") and mode not in {"install", "update"} and "codex_app_status" not in tools:
        tools = ["codex_app_status", *tools]
    sid = uuid.uuid4().hex[:12]
    _sessions[sid] = {"session_id": sid, "intent": intent_n, "mode": mode, "job_id": None}
    out = {
        "ok": True,
        "protocol": "session/v1",
        "session_id": sid,
        "mode": mode,
        "gate_status": {
            "ready": gate["ready"],
            "binary_ok": gate["binary_ok"],
            "auth_ok": gate["auth_ok"],
            "roots_ok": gate["roots_ok"],
            "cli": "codex",
            "server": gate["server"],
        },
        "recommended_tools": tools,
        "defaults": {"tokenBudget": ECONOMY_DEFAULT_TOKEN_BUDGET},
        "economy_flags": {"economy": True, "compact": True, "verbose_default": False},
        "skill_ref": route["skill_ref"],
        "next_step": route["next"],
        "disclaimer": "Unofficial community project — not OpenAI/Codex.",
    }
    if not gate.get("ready"):
        out["next_step"] = "Gate failed: need codex on PATH, codex login, CODEX_APP_MCP_ALLOWED_ROOTS."
    return _shrink(out)


def session_tick(
    *,
    session_id: str | None = None,
    job_id: str | None = None,
    verbose: bool = False,
    job_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    enable_session_economy()
    jid = (job_id or "").strip() or None
    sid = (session_id or "").strip() or None
    sess = _sessions.get(sid) if sid else None
    if sess and not jid:
        jid = sess.get("job_id")
    state = "idle"
    phase = "waiting"
    pct = 0
    blockers: list[str] = []
    suggested = "session_begin or start a goal."
    host_message = "No active job."
    snap = dict(job_snapshot or {})
    if jid and not snap:
        # Without gateway injection, report unknown lightly
        state = "unknown_job" if jid else "idle"
        if jid:
            blockers.append("JOB_LOOKUP_LOCAL_ONLY")
            suggested = "Pass job via codex_app_job then tick, or use gateway-backed poll."
            host_message = f"No local job store for {jid}; use codex_app_job."
    elif snap:
        if sid and sess is not None:
            sess["job_id"] = jid or snap.get("job_id")
        state = str(snap.get("state") or snap.get("status") or "running")
        st = state.lower()
        if st in {"completed", "ok", "success", "done"}:
            pct, phase, suggested = 100, "done", "session_end"
        elif st in {"failed", "error", "cancelled", "canceled"}:
            pct, phase = 100, "failed"
            blockers.append(str(snap.get("error") or st))
            suggested = "session_end or one tight goal"
        else:
            pct, phase, suggested = 50, "running", "poll compact; no event dumps"
        host_message = _clip(f"{state} job={jid or snap.get('job_id')}", _HOST_MSG_MAX)
    out: dict[str, Any] = {
        "ok": True,
        "protocol": "session/v1",
        "session_id": sid,
        "job_id": jid,
        "state": state,
        "phase": phase,
        "percent": pct,
        "changed_files_count": int(snap.get("changed_files_count") or 0),
        "tests_summary": snap.get("tests_summary"),
        "blockers": blockers,
        "suggested_host_action": suggested,
        "host_message": scrub_secrets(host_message)[:_HOST_MSG_MAX],
        "economy_compact": not verbose,
    }
    if verbose and snap:
        out["job"] = dict(snap)
    return _shrink(out)


def session_end(
    *,
    session_id: str | None = None,
    job_id: str | None = None,
    suggest_issue: bool = False,
    note: str | None = None,
    status_hint: str | None = None,
) -> dict[str, Any]:
    enable_session_economy()
    sid = (session_id or "").strip() or None
    jid = (job_id or "").strip() or None
    sess = _sessions.get(sid) if sid else None
    if sess and not jid:
        jid = sess.get("job_id")
    status = status_hint or "ok"
    receipt = {
        "status": status,
        "job": jid or "none",
        "changed": "n/a",
        "tests": "n/a",
        "next": _clip(note or "Done.", 200),
        "blockers": [],
    }
    out: dict[str, Any] = {
        "ok": True,
        "protocol": "session/v1",
        "session_id": sid,
        "receipt": receipt,
        "host_message": _clip(f"{status}: job={receipt['job']}", _HOST_MSG_MAX),
        "disclaimer": "Unofficial — not OpenAI/Codex.",
    }
    if suggest_issue:
        draft = (
            f"## Summary\nSession {sid or '-'} status={status}\n"
            f"## Job\n{jid or 'none'}\n"
            f"## Env\nserver={SERVER_VERSION}\n"
            f"## Free-form\n{scrub_secrets(note or '')}\n"
        )
        out["suggest_issue"] = True
        out["issue_draft"] = scrub_secrets(_clip(draft, 1200))
        out["issue_repo"] = "zai-one/codex-mcp"
    if sid and sid in _sessions:
        _sessions[sid]["ended"] = True
    return _shrink(out)


def reset_sessions_for_tests() -> None:
    global _compact_session
    _sessions.clear()
    _compact_session = False
