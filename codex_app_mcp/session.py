"""Adaptive Session Protocol v1.2 for codex-app-mcp — plan + budget + navigator."""

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
        "codex_app_session_next",
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
    "install": {"mode": "install", "skill_ref": "references/install.md", "tools": [], "prefer": "install", "next": "Install + login; session_next."},
    "update": {"mode": "update", "skill_ref": "references/update.md", "tools": [], "prefer": "update", "next": "Update then end."},
    "triage": {"mode": "triage", "skill_ref": "references/security.md", "tools": ["codex_app_status", "codex_app_doctor"], "prefer": "consult", "next": "Diagnose gate."},
    "brainstorm": {"mode": "brainstorm", "skill_ref": "references/brainstorm.md", "tools": ["codex_app_review"], "prefer": "consult", "next": "Light review."},
    "execute": {"mode": "execute", "skill_ref": "references/execute.md", "tools": ["codex_app_goal", "codex_app_job"], "prefer": "goal", "next": "Goal → job → end."},
    "verify": {"mode": "verify", "skill_ref": "references/verify.md", "tools": ["codex_app_job", "codex_app_status"], "prefer": "consult", "next": "Status only."},
    "feedback": {"mode": "feedback", "skill_ref": "references/feedback.md", "tools": [], "prefer": "consult", "next": "Issue draft."},
    "operate": {"mode": "operate", "skill_ref": "references/operate.md", "tools": ["codex_app_status"], "prefer": "consult", "next": "Pick mode."},
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
            "protocol": "session/v1.2",
            "session_id": out.get("session_id"),
            "done": out.get("done"),
            "card": out.get("card"),
            "host_message": out.get("host_message"),
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
    if mode == "install" or (not ready and mode not in {"triage", "update", "feedback"}):
        return [
            _step(1, "codex_app_session_next", "Install card"),
            _step(2, "codex_app_session_next", "Login card"),
            _step(3, "codex_app_session_end", "Receipt"),
        ]
    if mode == "update":
        return [
            _step(1, "codex_app_session_next", "Update card"),
            _step(2, "codex_app_session_end", "Receipt"),
        ]
    if mode == "feedback":
        return [
            _step(1, "codex_app_session_next", "Issue card"),
            _step(2, "codex_app_session_end", "Done"),
        ]
    if mode == "triage":
        return [
            _step(1, "codex_app_status", "Probe"),
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
            _step(2, "codex_app_job", "Compact job"),
            _step(3, "codex_app_session_end", "Receipt"),
        ]
    if mode == "verify":
        return [
            _step(1, "codex_app_job", "Job status"),
            _step(2, "codex_app_status", "Runtime"),
            _step(3, "codex_app_session_end", "Receipt"),
        ]
    return [
        _step(1, "codex_app_status", "Once"),
        _step(2, "codex_app_session_end", "Or re-begin with intent"),
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
        return {"ok": False, "error_code": "INTENT_INVALID", "protocol": "session/v1.2"}
    hb = (host_budget or "small").strip().lower()
    if hb not in HOST_BUDGETS:
        return {"ok": False, "error_code": "BUDGET_INVALID", "protocol": "session/v1.2"}
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
    for tname in ["codex_app_session_next"] + [s["tool"] for s in plan] + tools + ["codex_app_session_end"]:
        if tname in _ALLOW and tname not in rec:
            rec.append(tname)
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
        "stop_when": "session_next done=true or budget exhausted",
    }
    deny = [t for t in _DENY.get(mode, []) if t in _ALLOW]
    sid = uuid.uuid4().hex[:12]
    _sessions[sid] = {
        "session_id": sid,
        "mode": mode,
        "goal": goal_s,
        "plan": plan,
        "plan_step": 0,
        "budget": budget,
        "deny_tools": deny,
        "tool_calls_used": 0,
        "polls_used": 0,
        "job_id": None,
    }
    out: dict[str, Any] = {
        "ok": True,
        "protocol": "session/v1.2",
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
        "host_script": _clip(
            f"Call codex_app_session_next until done=true (tools≤{budget['max_tool_calls']}). Do not invent tools.",
            _SCRIPT_MAX,
        ),
        "defaults": {"tokenBudget": ECONOMY_DEFAULT_TOKEN_BUDGET},
        "economy_flags": {"economy": True, "compact": True, "verbose_default": False},
        "skill_ref": route["skill_ref"],
        "next_step": "session_next",
        "disclaimer": "Unofficial — not OpenAI/Codex.",
    }
    if goal_s:
        out["goal_echo"] = goal_s
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
            elif tu and tu not in _ALLOW:
                warns.append(f"tool_unknown:{tu}")
            elif tu:
                sess["tool_calls_used"] = int(sess.get("tool_calls_used") or 0) + 1
        if step_done:
            sess["plan_step"] = min(int(sess.get("plan_step") or 0) + 1, len(sess.get("plan") or []))
    plan = list((sess or {}).get("plan") or [])
    step_i = int((sess or {}).get("plan_step") or 0)
    budget = dict((sess or {}).get("budget") or _BUDGET_PRESETS["small"])
    used_t = int((sess or {}).get("tool_calls_used") or 0)
    used_p = int((sess or {}).get("polls_used") or 0)
    max_t = int(budget.get("max_tool_calls") or 6)
    max_p = int(budget.get("max_polls") or 4)
    force_end = bool(used_t >= max_t or used_p >= max_p or (bool(plan) and step_i >= len(plan)))
    return _shrink(
        {
            "ok": True,
            "protocol": "session/v1.2",
            "session_id": sid,
            "job_id": jid,
            "state": "idle",
            "step": step_i,
            "steps_left": max(0, len(plan) - step_i),
            "budget_remaining": {"tool_calls": max(0, max_t - used_t), "polls": max(0, max_p - used_p)},
            "force_end": force_end,
            "blockers": warns[:8],
            "suggested_host_action": "session_end" if force_end else "session_next",
            "host_message": "Prefer session_next over ad-hoc tools.",
            "economy_compact": not verbose,
        }
    )


def session_next(
    *,
    session_id: str | None = None,
    advance: bool = True,
    note: str | None = None,
) -> dict[str, Any]:
    enable_session_economy()
    sid = (session_id or "").strip() or None
    sess = _sessions.get(sid) if sid else None
    if sess is None:
        return _shrink(
            {
                "ok": False,
                "error_code": "SESSION_UNKNOWN",
                "protocol": "session/v1.2",
                "done": True,
                "card": {"kind": "end", "why": "call session_begin first"},
            }
        )
    sess["polls_used"] = int(sess.get("polls_used") or 0) + 1
    sess["tool_calls_used"] = int(sess.get("tool_calls_used") or 0) + 1
    budget = dict(sess.get("budget") or _BUDGET_PRESETS["small"])
    max_t = int(budget.get("max_tool_calls") or 6)
    max_p = int(budget.get("max_polls") or 4)
    used_t = int(sess.get("tool_calls_used") or 0)
    used_p = int(sess.get("polls_used") or 0)
    plan = list(sess.get("plan") or [])
    step_i = int(sess.get("plan_step") or 0)
    mode = str(sess.get("mode") or "operate")
    goal = str(sess.get("goal") or "")
    rem = {"tool_calls": max(0, max_t - used_t), "polls": max(0, max_p - used_p)}

    if used_t >= max_t or used_p >= max_p:
        return _shrink(
            {
                "ok": True,
                "protocol": "session/v1.2",
                "session_id": sid,
                "done": True,
                "force_end": True,
                "card": {"kind": "end", "tool": "codex_app_session_end", "args": {"session_id": sid}, "why": "budget"},
                "host_message": "Budget exhausted — session_end.",
                "budget_remaining": rem,
            }
        )

    if mode == "install" and step_i == 0:
        if advance:
            sess["plan_step"] = 1
        return _shrink(
            {
                "ok": True,
                "protocol": "session/v1.2",
                "session_id": sid,
                "done": False,
                "card": {
                    "kind": "host_cmd",
                    "cmd": "curl -fsSL https://raw.githubusercontent.com/zai-one/codex-mcp/main/scripts/install.sh | bash",
                    "why": "EASY install",
                },
                "host_message": "Run install, then codex login, then session_next.",
                "budget_remaining": rem,
            }
        )
    if mode == "install" and step_i == 1:
        if advance:
            sess["plan_step"] = 2
        return _shrink(
            {
                "ok": True,
                "protocol": "session/v1.2",
                "session_id": sid,
                "done": False,
                "card": {"kind": "host_cmd", "cmd": "codex login && codex --version", "why": "Auth gate"},
                "host_message": "Login if needed, then session_next/end.",
                "budget_remaining": rem,
            }
        )
    if mode == "update" and step_i == 0:
        if advance:
            sess["plan_step"] = 1
        return _shrink(
            {
                "ok": True,
                "protocol": "session/v1.2",
                "session_id": sid,
                "done": False,
                "card": {
                    "kind": "host_cmd",
                    "cmd": "bash skills/codex-mcp/scripts/update_mcp.sh 2>/dev/null || curl -fsSL https://raw.githubusercontent.com/zai-one/codex-mcp/main/scripts/install.sh | bash",
                    "why": "Update",
                },
                "host_message": "Run update, then session_end.",
                "budget_remaining": rem,
            }
        )
    if mode == "feedback" and step_i == 0:
        if advance:
            sess["plan_step"] = 1
        return _shrink(
            {
                "ok": True,
                "protocol": "session/v1.2",
                "session_id": sid,
                "done": False,
                "card": {
                    "kind": "mcp_tool",
                    "tool": "codex_app_session_end",
                    "args": {
                        "session_id": sid,
                        "suggest_issue": True,
                        "note": scrub_secrets(_clip(note or goal or "bug", 200)),
                    },
                    "why": "Issue draft",
                },
                "host_message": "session_end with suggest_issue.",
                "budget_remaining": rem,
            }
        )

    while step_i < len(plan) and plan[step_i].get("tool") == "codex_app_session_next":
        step_i += 1
        if advance:
            sess["plan_step"] = step_i

    if step_i >= len(plan):
        return _shrink(
            {
                "ok": True,
                "protocol": "session/v1.2",
                "session_id": sid,
                "done": True,
                "force_end": True,
                "card": {
                    "kind": "end",
                    "tool": "codex_app_session_end",
                    "args": {"session_id": sid},
                    "why": "plan complete",
                },
                "host_message": "session_end now.",
                "budget_remaining": rem,
            }
        )

    step = plan[step_i]
    tool = step.get("tool")
    if tool == "codex_app_session_end":
        if advance:
            sess["plan_step"] = step_i + 1
        return _shrink(
            {
                "ok": True,
                "protocol": "session/v1.2",
                "session_id": sid,
                "done": True,
                "force_end": True,
                "card": {
                    "kind": "end",
                    "tool": "codex_app_session_end",
                    "args": {"session_id": sid},
                    "why": step.get("why") or "end",
                },
                "host_message": "Call session_end.",
                "budget_remaining": rem,
            }
        )

    card = {
        "kind": "mcp_tool",
        "tool": tool,
        "args": dict(step.get("args_hint") or {}),
        "why": step.get("why") or "",
    }
    if advance:
        sess["plan_step"] = step_i + 1
    return _shrink(
        {
            "ok": True,
            "protocol": "session/v1.2",
            "session_id": sid,
            "done": False,
            "step": step_i,
            "steps_left": max(0, len(plan) - step_i - 1),
            "card": card,
            "host_message": _clip(f"Call {tool}, then session_next.", 200),
            "budget_remaining": rem,
            "disclaimer": "Unofficial — not OpenAI/Codex.",
        }
    )


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
    out: dict[str, Any] = {
        "ok": True,
        "protocol": "session/v1.2",
        "session_id": sid,
        "receipt": {
            "status": status,
            "job": jid or "none",
            "changed": "n/a",
            "tests": "n/a",
            "next": _clip(scrub_secrets(note or "Done."), 200),
            "blockers": [],
        },
        "budget_report": {
            "tool_calls_used": used_t,
            "polls_used": used_p,
            "max_tool_calls": max_t,
            "max_polls": max_p,
            "was_capped": was_capped,
        },
        "lesson": _clip(
            scrub_secrets(f"mode={(sess or {}).get('mode')}; use session_next; budget={'capped' if was_capped else 'ok'}"),
            _LESSON_MAX,
        ),
        "host_message": _clip(f"{status}: job={jid or 'none'}", _HOST_MSG_MAX),
        "disclaimer": "Unofficial — not OpenAI/Codex.",
    }
    if suggest_issue:
        draft = f"## Summary\nSession {sid}\n## Free-form\n{scrub_secrets(note or '')}\n"
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
