"""In-process registry for background delegations.

A real lane runs for minutes. The synchronous tool is honest for short work, but
it holds the MCP request open for the whole run: past the stdio idle window
(30 minutes of silence) the client aborts it, and a call made from a subagent is
never moved to a background task at all. `codex_delegate_start` hands the same
guarded delegation to a thread and returns a job id; `codex_delegate_poll` reads
the result back.

The registry lives in this process. A server restart loses records that were
never collected — the work itself survives on the `codex/<lane>` branch, which
is why the branch, not this dict, is the durable artefact.
"""

from __future__ import annotations

import threading
import uuid
from typing import Any, Callable, Optional

# Job states. A caller that only knows these three can drive the tool.
STATE_RUNNING = "running"
STATE_DONE = "done"
STATE_ERROR = "error"

TERMINAL_STATES = frozenset({STATE_DONE, STATE_ERROR})

# Bounded so a long-lived server cannot grow without limit. Only finished jobs
# are ever evicted, and oldest first: a running job has nowhere else to report.
MAX_JOBS = 64

_LOCK = threading.Lock()
_JOBS: "dict[str, dict[str, Any]]" = {}
# Insertion order of job ids, so eviction does not depend on dict internals.
_ORDER: "list[str]" = []

__all__ = [
    "MAX_JOBS",
    "STATE_DONE",
    "STATE_ERROR",
    "STATE_RUNNING",
    "TERMINAL_STATES",
    "list_jobs",
    "new_job_id",
    "reset_jobs_for_tests",
    "snapshot",
    "start_job",
]


def new_job_id() -> str:
    return f"job-{uuid.uuid4().hex[:12]}"


def _evict_locked() -> None:
    """Drop the oldest finished jobs until the registry fits. Caller holds the lock."""
    if len(_ORDER) <= MAX_JOBS:
        return
    keep: list[str] = []
    for job_id in _ORDER:
        record = _JOBS.get(job_id)
        finished = record is not None and record.get("state") in TERMINAL_STATES
        if finished and len(_ORDER) - len(keep) > MAX_JOBS:
            _JOBS.pop(job_id, None)
            continue
        keep.append(job_id)
    _ORDER[:] = keep


def start_job(
    fn: Callable[[], dict[str, Any]],
    *,
    lane: str,
    tool: str,
) -> dict[str, Any]:
    """Run *fn* on a daemon thread; return the job handle immediately.

    The handle is what the caller polls with. `fn` is expected to return the
    same envelope the synchronous path returns; if it raises, the job lands in
    ``error`` carrying the exception type and message — never a traceback,
    which would leak host paths into an MCP response.
    """
    job_id = new_job_id()
    record: dict[str, Any] = {
        "job_id": job_id,
        "lane": lane,
        "tool": tool,
        "state": STATE_RUNNING,
        "result": None,
        "error": None,
    }
    with _LOCK:
        _JOBS[job_id] = record
        _ORDER.append(job_id)
        _evict_locked()

    def _finish(state: str, *, result: Optional[dict[str, Any]], error: Optional[str]) -> None:
        with _LOCK:
            live = _JOBS.get(job_id)
            if live is None or live.get("state") in TERMINAL_STATES:
                # Evicted, or already terminal: a terminal record is written
                # once so two polls of a finished job cannot disagree.
                return
            live["state"] = state
            live["result"] = result
            live["error"] = error

    def _run() -> None:
        try:
            result = fn()
        except BaseException as exc:  # noqa: BLE001 - a dead thread must still report
            _finish(STATE_ERROR, result=None, error=f"{type(exc).__name__}: {exc}")
            return
        if not isinstance(result, dict):
            _finish(
                STATE_ERROR,
                result=None,
                error=f"delegation returned {type(result).__name__}, expected dict",
            )
            return
        _finish(
            STATE_DONE if result.get("ok") else STATE_ERROR,
            result=result,
            error=None if result.get("ok") else str(result.get("error") or "DELEGATION_FAILED"),
        )

    threading.Thread(target=_run, name=f"codex-delegate-{job_id}", daemon=True).start()

    return {
        "job_id": job_id,
        "lane": lane,
        "tool": tool,
        "state": STATE_RUNNING,
    }


def snapshot(job_id: str) -> Optional[dict[str, Any]]:
    """Full record for *job_id*, or None when it is unknown or evicted.

    Never raises: an unknown id is an answer, not an exception, so a poll can
    always be reported as a structured result.
    """
    with _LOCK:
        record = _JOBS.get(str(job_id))
        return dict(record) if record is not None else None


def list_jobs(limit: int = 20) -> list[dict[str, Any]]:
    """Compact records, newest first, without the result payload."""
    try:
        n = int(limit)
    except (TypeError, ValueError):
        n = 20
    n = max(1, min(n, MAX_JOBS))
    with _LOCK:
        ids = list(reversed(_ORDER))[:n]
        return [
            {
                "job_id": jid,
                "lane": _JOBS[jid].get("lane"),
                "tool": _JOBS[jid].get("tool"),
                "state": _JOBS[jid].get("state"),
                "error": _JOBS[jid].get("error"),
            }
            for jid in ids
            if jid in _JOBS
        ]


def reset_jobs_for_tests() -> None:
    with _LOCK:
        _JOBS.clear()
        _ORDER.clear()
