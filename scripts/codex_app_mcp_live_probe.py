"""Opt-in live verification for the installed Codex app-server.

The default mode is read-only. ``--goal`` creates and archives one harmless
read-only persisted goal and can consume a small number of model tokens.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from codex_app_mcp.gateway import CodexAppGateway  # noqa: E402
from codex_app_mcp.policy import GatewayPolicy  # noqa: E402


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, default=str), flush=True)


def run_goal(gateway: CodexAppGateway, cwd: Path, timeout: float) -> bool:
    thread_id: str | None = None
    try:
        started_at = time.monotonic()
        started = gateway.goal(
            {
                "action": "start",
                "cwd": str(cwd),
                "objective": (
                    "Live gateway verification: respond with exactly "
                    "GOAL_GATEWAY_PROBE_OK, then invoke the built-in update_goal "
                    "tool exactly once with status complete. "
                    "Do not modify files or call tools."
                ),
                "tokenBudget": 10_000,
                "model": "gpt-5.6-luna",
                "effort": "low",
                "sandbox": "read-only",
                "approvalPolicy": "never",
                "name": "codex-app-mcp live goal probe",
            }
        )
        thread_id = started["threadId"]
        emit(
            {
                "phase": "goal_started",
                "threadId": thread_id,
                "elapsedSeconds": round(time.monotonic() - started_at, 3),
            }
        )
        cursor = 1
        deadline = time.monotonic() + timeout
        terminal = {"paused", "blocked", "usageLimited", "budgetLimited", "complete"}
        evidence: list[dict[str, Any]] = []
        goal: Any = None
        while time.monotonic() < deadline:
            polled = gateway.event(
                {
                    "action": "poll",
                    "threadId": thread_id,
                    "cursor": cursor,
                    "limit": 200,
                }
            )
            cursor = int(polled["nextCursor"])
            for event in polled["events"]:
                if event["method"] in {
                    "turn/started",
                    "turn/completed",
                    "thread/goal/updated",
                    "item/completed",
                    "error",
                }:
                    evidence.append(event)
            response = gateway.goal({"action": "get", "threadId": thread_id})
            result = response.get("result")
            goal = result.get("goal") if isinstance(result, dict) else None
            status = goal.get("status") if isinstance(goal, dict) else None
            if status in terminal:
                break
            time.sleep(1)
        status = goal.get("status") if isinstance(goal, dict) else None
        emit(
            {
                "phase": "goal_terminal",
                "threadId": thread_id,
                "goal": goal,
                "events": evidence[-30:],
                "elapsedSeconds": round(time.monotonic() - started_at, 3),
            }
        )
        if status not in terminal:
            gateway.goal({"action": "pause", "threadId": thread_id})
            return False
        gateway.thread({"action": "archive", "threadId": thread_id})
        emit({"phase": "goal_archived", "threadId": thread_id})
        return status == "complete"
    except Exception as exc:
        if thread_id is not None:
            try:
                gateway.goal({"action": "pause", "threadId": thread_id})
                gateway.thread({"action": "archive", "threadId": thread_id})
            except Exception:
                pass
        emit(
            {
                "phase": "goal_error",
                "threadId": thread_id,
                "error": f"{type(exc).__name__}: {exc}",
                "stderrTail": gateway.client.stderr_tail[-30:],
            }
        )
        return False


def cleanup_thread(gateway: CodexAppGateway, thread_id: str) -> bool:
    try:
        paused = gateway.goal({"action": "pause", "threadId": thread_id})
        archived = gateway.thread({"action": "archive", "threadId": thread_id})
        emit(
            {
                "phase": "cleanup",
                "threadId": thread_id,
                "paused": paused,
                "archived": archived,
            }
        )
        return True
    except Exception as exc:
        emit(
            {
                "phase": "cleanup_error",
                "threadId": thread_id,
                "error": f"{type(exc).__name__}: {exc}",
                "stderrTail": gateway.client.stderr_tail[-30:],
            }
        )
        return False


def run_background_job(gateway: CodexAppGateway, cwd: Path, timeout: float) -> bool:
    job_id: str | None = None
    thread_id: str | None = None
    try:
        started_at = time.monotonic()
        started = gateway.job(
            {
                "action": "start",
                "kind": "goal",
                "request": {
                    "cwd": str(cwd),
                    "objective": (
                        "Background gateway verification: respond with exactly "
                        "JOB_GATEWAY_PROBE_OK, then invoke the built-in update_goal "
                        "tool exactly once with status complete. Do not modify files "
                        "or call other tools."
                    ),
                    "tokenBudget": 10_000,
                    "model": "gpt-5.6-luna",
                    "effort": "low",
                    "sandbox": "read-only",
                    "approvalPolicy": "never",
                    "name": "codex-app-mcp live background job probe",
                },
            }
        )
        job_id = started["job"]["jobId"]
        terminal = {"succeeded", "attention", "failed", "cancelled", "orphaned"}
        deadline = time.monotonic() + timeout
        job: Any = started["job"]
        while time.monotonic() < deadline:
            job = gateway.job(
                {
                    "action": "get",
                    "jobId": job_id,
                    "includeHistory": True,
                }
            )["job"]
            thread_id = job.get("threadId")
            if job["state"] in terminal:
                break
            time.sleep(0.5)
        emit(
            {
                "phase": "job_terminal",
                "job": job,
                "elapsedSeconds": round(time.monotonic() - started_at, 3),
            }
        )
        if job["state"] not in terminal:
            gateway.job({"action": "cancel", "jobId": job_id})
            return False
        if thread_id is not None:
            gateway.thread({"action": "archive", "threadId": thread_id})
            emit(
                {
                    "phase": "job_thread_archived",
                    "jobId": job_id,
                    "threadId": thread_id,
                }
            )
        return job["state"] == "succeeded"
    except Exception as exc:
        if job_id is not None:
            try:
                gateway.job({"action": "cancel", "jobId": job_id})
            except Exception:
                pass
        emit(
            {
                "phase": "job_error",
                "jobId": job_id,
                "threadId": thread_id,
                "error": f"{type(exc).__name__}: {exc}",
                "stderrTail": gateway.client.stderr_tail[-30:],
            }
        )
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cwd", type=Path, default=REPO_ROOT)
    parser.add_argument("--goal", action="store_true")
    parser.add_argument("--job", action="store_true")
    parser.add_argument("--goal-timeout", type=float, default=180.0)
    parser.add_argument("--cleanup-thread")
    args = parser.parse_args()
    cwd = args.cwd.resolve()
    state_path: str | Path = ":memory:"
    if args.job:
        state_path = (
            Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
            / "app-mcp-probe.sqlite3"
        )
    gateway = CodexAppGateway(
        policy=GatewayPolicy(allowed_roots=(cwd,)),
        state_path=state_path,
    )
    ok = True
    try:
        status = gateway.status(include_stderr=True)
        models = gateway.discover({"action": "models"})
        modes = gateway.discover({"action": "collaboration_modes"})
        emit(
            {
                "phase": "handshake",
                "status": status,
                "models": models,
                "collaborationModes": modes,
                "argv": gateway.client._build_argv(),
            }
        )
        if args.cleanup_thread:
            ok = cleanup_thread(gateway, args.cleanup_thread) and ok
        if args.goal:
            ok = run_goal(gateway, cwd, args.goal_timeout) and ok
        if args.job:
            ok = run_background_job(gateway, cwd, args.goal_timeout) and ok
    except Exception as exc:
        emit(
            {
                "phase": "probe_error",
                "error": f"{type(exc).__name__}: {exc}",
                "stderrTail": gateway.client.stderr_tail[-30:],
            }
        )
        ok = False
    finally:
        gateway.close()
        emit({"phase": "closed", "runningAfterClose": gateway.client.running})
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
