"""Adaptive Session Protocol v1.1 for codex-app-mcp — plan compiler + budget guard."""

from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from typing import Any, Mapping

from . import __version__ as SERVER_VERSION
from .economy import ECONOMY_DEFAULT_TOKEN_BUDGET, economy_enabled

INTENTS = frozenset(
    {"brainstorm", "execute", "verify", "install", "update", "triage", "feedback", "auto"}
)
HOST_BUDGETS = frozenset({"tiny", "small", "normal"})
_BUDGET_PRESETS = {
    "tiny": {"max_tool_calls": 3, "max_polls": 2},
    "small": {"max_tool_calls": 6, "max_polls": 4},
    "normal": {"max_tool_calls": 12, "max_polls": 8},
}
_ALLOW = frozenset(
    {
        "codex_app_session_begin",
        "codex_app_session_tick",
        "codex_app_session_end",
        "codex_app_status",
        "codex_app_economy",
        "codex_app_doctor",
        "codex_app_goal",
        "codex_app_job",
        "codex_app_review",
        "codex_app_turn",
        "codex_app_events",
    }
)
_HOST_MSG_MAX = 500
_GOAL_MAX = 500
_PAYLOAD_SOFT_MAX = 1536
_WHY_MAX = 60
_SCRIPT_MAX = 240
_LESSON_MAX = 120
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
    "install": {"mode": "install", "skill_ref": "references/install.md", "tools": [], "prefer": "install", "next": "Install + codex login; re-begin."},
    "update": {"mode": "update", "skill_ref": "references/update.md", "tools": [], "prefer": "update", "next": "update_mcp.sh then begin."},
    "triage": {"mode": "triage", "skill_ref": "references/security.md", "tools": ["codex_app_status", "codex_app_doctor"], "prefer": "consult", "next": "Fix gate; feedback if product bug."},
    "brainstorm": {"mode": "brainstorm", "skill_ref": "references/brainstorm.md", "tools": ["codex_app_review", "codex_app_status"], "prefer": "consult", "next": "Light tools only; no large goals."},
    "execute": {"mode": "execute", "skill_ref": "references/execute.md", "tools": ["codex_app_goal", "codex_app_job"], "prefer": "goal", "next": "Tight goal + tokenBudget → job → end."},
    "verify": {"mode": "verify", "skill_ref": "references/verify.md", "tools": ["codex_app_job", "codex_app_status"], "prefer": "consult", "next": "Compact status only."},
    "feedback": {"mode": "feedback", "skill_ref": "references/feedback.md", "tools": [], "prefer": "consult", "next": "Scrubbed issue draft."},
    "operate": {"mode": "operate", "skill_ref": "references/operate.md", "tools": ["codex_app_status", "codex_app_economy"], "prefer": "consult", "next": "Pick mode by task size."},
}
_DENY = {
    "brainstorm": ["codex_app_goal"],
    "verify": ["codex_app_goal"],
    "install": ["codex_app_goal", "codex_app_review"],
    "update": ["codex_app_goal"],
    "feedback": ["codex_app_goal"],
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
        if isinstance(v, str) and len(v) > 320:
            out[k] = _clip(v, 320)
    if _json_size(out) > _PAYLOAD_SOFT_MAX:
        out = {
            "ok": out.get("ok", True),
            "protocol": "session/v1.1",
            "session_id": out.get("session_id"),
            "mode": out.get("mode"),
            "plan": (out.get("plan") or [])[:3],
            "host_script": out.get("host_script"),
            "force_end": out.get("force_end"),
            "truncated": True,
        }
    return out


def resolve_gate() -> dict[str, Any]:
    binary_ok = bool(shutil.which("codex"))
    roots_raw = os.environ.get("CODEX_APP_MCP_ALLOWED_ROOTS", "").strip()
    roots = [p.strip() for p in roots_raw.split(os.pathsep) if p.strip()][:8]
    roots_ok = bool(roots)
    return {
        "ready": binary_ok and roots_ok,
        "cli": "codex",
        "binary_ok": binary_ok,
        "auth_ok": binary_ok,
        "roots_ok": roots_ok,
        "roots": roots,
        "server": SERVER_VERSION,
    }


def _auto_mode(gate: Mapping[str, Any], intent: str, goal: str) -> str:
    if not gate.get("binary_ok"):
        return "install"
    if not gate.get("roots_ok"):
        return "triage"
    if intent in INTENTS and intent != "auto":
        return intent
    g = goal.lower()
    if any(w in g for w in ("fix", "implement", "build", "add ", "refactor")):
        return "execute"
    if any(w in g for w in ("review", "verify", "test", "check")):
        return "verify"
    if any(w in g for w in ("brainstorm", "design", "options")):
        return "brainstorm"
    return "operate"


def _step(i: int, tool: str, why: str, args_hint: dict[str, Any] | None = None) -> dict[str, Any]:
    if tool not in _ALLOW:
        raise ValueError(tool)
    return {"i": i, "tool": tool, "why": _clip(why, _WHY_MAX), "args_hint": args_hint or {}}


def compile_plan(mode: str, goal: str, ready: bool) -> list[dict[str, Any]]:
    g = _clip(scrub_secrets(goal), 200) if goal else ""
    if mode in {"install", "update", "feedback"} or (not ready and mode not in {"triage"}):
        return []
    if mode == "triage":
        return [
            _step(1, "codex_app_status", "Probe connection"),
            _step(2, "codex_app_doctor", "Diagnose"),
            _step(3, "codex_app_session_end", "Receipt"),
        ]
    if mode == "brainstorm":
        return [
            _step(1, "codex_app_review", "Light review", {"instructions": g} if g else {}),
            _step(2, "codex_app_session_end", "Receipt"),
        ]
    if mode == "execute":
        return [
            _step(1, "codex_app_goal", "Tight goal", {"objective": g, "tokenBudget": ECONOMY_DEFAULT_TOKEN_BUDGET} if g else {"tokenBudget": ECONOMY_DEFAULT_TOKEN_BUDGET}),
            _step(2, "codex_app_session_tick", "Budget/progress"),
            _step(3, "codex_app_job", "Compact job status"),
            _step(4, "codex_app_session_end", "Receipt"),
        ]
    if mode == "verify":
        return [
            _step(1, "codex_app_job", "Job status"),
            _step(2, "codex_app_status", "Runtime"),
            _step(3, "codex_app_session_end", "Receipt"),
        ]
    return [
        _step(1, "codex_app_status", "Once"),
        _step(2, "codex_app_economy", "Playbook once"),
        _step(3, "codex_app_session_end", "Or pick mode"),
    ]


def session_begin(
    intent: str = "auto",
    *,
    goal: str | None = None,
    host_budget: str = "small",
    max_tool_calls: int | None = None,
) -> dict[str, Any]:
    intent_n = (intent or "auto").strip().lower()
    if intent_n not in INTENTS:
        return {"ok": False, "error_code": "INTENT_INVALID", "error": "bad intent", "protocol": "session/v1.1"}
    hb = (host_budget or "small").strip().lower()
    if hb not in HOST_BUDGETS:
        return {"ok": False, "error_code": "BUDGET_INVALID", "error": "bad host_budget", "protocol": "session/v1.1"}
    goal_s = scrub_secrets(_clip(goal or "", _GOAL_MAX))
    enable_session_economy()
    gate = resolve_gate()
    mode = _auto_mode(gate, intent_n, goal_s)
    if mode in {"execute", "brainstorm", "verify", "operate", "feedback"} and not gate.get("ready"):
        mode = "install" if not gate.get("binary_ok") else "triage"
    route = _ROUTES[mode]
    plan = compile_plan(mode, goal_s, bool(gate.get("ready")))
    tools = [t for t in route["tools"] if t in _ALLOW]
    rec: list[str] = []
    for t in [s["tool"] for s in plan] + tools + ["codex_app_session_tick", "codex_app_session_end"]:
        if t in _ALLOW and t not in rec:
            rec.append(t)
    preset = dict(_BUDGET_PRESETS[hb])
    if max_tool_calls is not None:
        try:
            preset["max_tool_calls"] = min(max(int(max_tool_calls), 1), 32)
        except (TypeError, ValueError):
            pass
    budget = {
        "host_budget": hb,
        "max_tool_calls": preset["max_tool_calls"],
        "max_polls": preset["max_polls"],
        "prefer": route["prefer"],
        "stop_when": "plan done or budget exhausted or force_end",
    }
    deny = [t for t in _DENY.get(mode, []) if t in _ALLOW]
    sid = uuid.uuid4().hex[:12]
    _sessions[sid] = {
        "session_id": sid,
        "mode": mode,
        "plan": plan,
        "plan_step": 0,
        "budget": budget,
        "deny_tools": deny,
        "tool_calls_used": 0,
        "polls_used": 0,
        "job_id": None,
    }
    if plan:
        tools_s = " → ".join(s["tool"].replace("codex_app_", "") for s in plan[:5])
        script = _clip(f"Follow plan only ({tools_s}). Caps tools={budget['max_tool_calls']} polls={budget['max_polls']}. session_end when done.", _SCRIPT_MAX)
    elif mode == "install":
        script = _clip("Install codex-mcp + codex login + roots; session_begin again.", _SCRIPT_MAX)
    else:
        script = _clip(route["next"], _SCRIPT_MAX)
    out: dict[str, Any] = {
        "ok": True,
        "protocol": "session/v1.1",
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
        "recommended_tools": rec[:12],
        "plan": plan,
        "budget": budget,
        "deny_tools": deny,
        "host_script": script,
        "defaults": {"tokenBudget": ECONOMY_DEFAULT_TOKEN_BUDGET},
        "economy_flags": {"economy": True, "compact": True, "verbose_default": False},
        "skill_ref": route["skill_ref"],
        "next_step": route["next"],
        "disclaimer": "Unofficial — not OpenAI/Codex.",
    }
    if goal_s:
        out["goal_echo"] = goal_s
    if not gate.get("ready"):
        out["next_step"] = "Gate failed: codex CLI + login + roots."
    return _shrink(out)


def session_tick(
    *,
    session_id: str | None = None,
    job_id: str | None = None,
    verbose: bool = False,
    tool_used: str | None = None,
    step_done: bool = False,
    job_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    enable_session_economy()
    sid = (session_id or "").strip() or None
    jid = (job_id or "").strip() or None
    sess = _sessions.get(sid) if sid else None
    if sess and not jid:
        jid = sess.get("job_id")
    warns: list[str] = []
    if sess is not None:
        sess["polls_used"] = int(sess.get("polls_used") or 0) + 1
        if tool_used:
            tu = str(tool_used).strip()
            if tu in set(sess.get("deny_tools") or []):
                warns.append(f"tool_denied:{tu}")
            if tu and tu not in _ALLOW:
                warns.append(f"tool_unknown:{tu}")
            elif tu:
                sess["tool_calls_used"] = int(sess.get("tool_calls_used") or 0) + 1
        if step_done:
            sess["plan_step"] = min(int(sess.get("plan_step") or 0) + 1, len(sess.get("plan") or []))
    state, phase, pct = "idle", "waiting", 0
    blockers = list(warns)
    suggested = "Follow plan; session_end when done."
    host_message = "No active job."
    snap = dict(job_snapshot or {})
    if jid and not snap:
        state = "unknown_job"
        blockers.append("JOB_LOOKUP_LOCAL_ONLY")
        host_message = f"Use codex_app_job for {jid}."
        suggested = "codex_app_job or session_end"
    elif snap:
        if sess is not None:
            sess["job_id"] = jid or snap.get("job_id")
        state = str(snap.get("state") or snap.get("status") or "running")
        st = state.lower()
        if st in {"completed", "ok", "success", "done", "succeeded"}:
            pct, phase, suggested = 100, "done", "session_end"
        elif st in {"failed", "error", "cancelled", "canceled"}:
            pct, phase = 100, "failed"
            blockers.append(str(snap.get("error") or st))
            suggested = "session_end"
        else:
            pct, phase, suggested = 50, "running", "compact poll only"
        host_message = _clip(f"{state} job={jid or snap.get('job_id')}", _HOST_MSG_MAX)
    plan = list((sess or {}).get("plan") or [])
    step_i = int((sess or {}).get("plan_step") or 0)
    steps_left = max(0, len(plan) - step_i)
    budget = dict((sess or {}).get("budget") or _BUDGET_PRESETS["small"])
    used_t = int((sess or {}).get("tool_calls_used") or 0)
    used_p = int((sess or {}).get("polls_used") or 0)
    max_t = int(budget.get("max_tool_calls") or 6)
    max_p = int(budget.get("max_polls") or 4)
    force_end = bool(
        used_t >= max_t
        or used_p >= max_p
        or (bool(plan) and steps_left == 0 and state in {"idle", "done", "completed", "failed", "succeeded"})
    )
    if used_t >= max_t:
        blockers.append("BUDGET_TOOLS")
    if used_p >= max_p:
        blockers.append("BUDGET_POLLS")
    if force_end:
        suggested = "session_end"
    out: dict[str, Any] = {
        "ok": True,
        "protocol": "session/v1.1",
        "session_id": sid,
        "job_id": jid,
        "state": state,
        "phase": phase,
        "percent": pct,
        "step": step_i,
        "steps_left": steps_left,
        "budget_remaining": {"tool_calls": max(0, max_t - used_t), "polls": max(0, max_p - used_p)},
        "force_end": force_end,
        "changed_files_count": int(snap.get("changed_files_count") or 0),
        "tests_summary": snap.get("tests_summary"),
        "blockers": blockers[:8],
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
    used_t = int((sess or {}).get("tool_calls_used") or 0)
    used_p = int((sess or {}).get("polls_used") or 0)
    budget = dict((sess or {}).get("budget") or {})
    max_t = int(budget.get("max_tool_calls") or 6)
    max_p = int(budget.get("max_polls") or 4)
    was_capped = used_t >= max_t or used_p >= max_p
    receipt = {
        "status": status,
        "job": jid or "none",
        "changed": "n/a",
        "tests": "n/a",
        "next": _clip(scrub_secrets(note or "Done."), 200),
        "blockers": [],
    }
    out: dict[str, Any] = {
        "ok": True,
        "protocol": "session/v1.1",
        "session_id": sid,
        "receipt": receipt,
        "budget_report": {
            "tool_calls_used": used_t,
            "polls_used": used_p,
            "max_tool_calls": max_t,
            "max_polls": max_p,
            "was_capped": was_capped,
        },
        "lesson": _clip(scrub_secrets(f"mode={(sess or {}).get('mode')}; budget={'capped' if was_capped else 'ok'}"), _LESSON_MAX),
        "host_message": _clip(f"{status}: job={receipt['job']}", _HOST_MSG_MAX),
        "disclaimer": "Unofficial — not OpenAI/Codex.",
    }
    if suggest_issue:
        draft = f"## Summary\nSession {sid}\n## Job\n{jid}\n## Free-form\n{scrub_secrets(note or '')}\n"
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
