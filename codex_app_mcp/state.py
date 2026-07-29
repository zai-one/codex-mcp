"""Durable SQLite ledger for background app-server operations."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

from .errors import GatewayError

SCHEMA_VERSION = 3
ACTIVE_STATES = frozenset({"queued", "running", "waiting_action", "cancelling"})
TERMINAL_STATES = frozenset(
    {"succeeded", "attention", "failed", "cancelled", "orphaned"}
)
ALL_STATES = ACTIVE_STATES | TERMINAL_STATES
MAX_STORED_JSON_CHARS = 1_000_000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_state_path() -> Path:
    raw = os.environ.get("CODEX_APP_MCP_STATE_PATH")
    if raw:
        return Path(raw).expanduser().resolve()
    codex_home = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
    return (codex_home / "app-mcp" / "state.sqlite3").resolve()


def _encode(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    if len(text) > MAX_STORED_JSON_CHARS:
        raise GatewayError(
            "JOB_PAYLOAD_TOO_LARGE",
            f"job payload exceeds {MAX_STORED_JSON_CHARS} characters",
        )
    return text


def _decode(value: Optional[str]) -> Any:
    if value is None:
        return None
    return json.loads(value)


class StateStore:
    """Small thread-safe ledger; every state change is transactional."""

    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self._closed = False
        if self.path != ":memory:":
            resolved = Path(self.path).expanduser().resolve()
            resolved.parent.mkdir(parents=True, exist_ok=True)
            self.path = str(resolved)
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock, self._db:
            self._db.execute("PRAGMA foreign_keys=ON")
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL CHECK (kind IN ('goal', 'turn')),
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    lane TEXT,
                    thread_id TEXT,
                    turn_id TEXT,
                    result_json TEXT,
                    error TEXT
                );
                CREATE TABLE IF NOT EXISTS job_transitions (
                    transition_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
                    at TEXT NOT NULL,
                    state TEXT NOT NULL,
                    details_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_jobs_updated ON jobs(updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_transitions_job
                    ON job_transitions(job_id, transition_id);
                CREATE TABLE IF NOT EXISTS schedules (
                    schedule_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (state IN ('enabled','paused')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    timezone TEXT NOT NULL,
                    rrule TEXT NOT NULL,
                    start_at TEXT NOT NULL,
                    next_run_at TEXT,
                    request_json TEXT NOT NULL,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    retry_backoff_seconds REAL NOT NULL DEFAULT 30,
                    misfire_policy TEXT NOT NULL DEFAULT 'run_once'
                        CHECK (misfire_policy IN ('run_once','skip'))
                );
                CREATE TABLE IF NOT EXISTS schedule_runs (
                    run_id TEXT PRIMARY KEY,
                    schedule_id TEXT NOT NULL
                        REFERENCES schedules(schedule_id) ON DELETE CASCADE,
                    scheduled_for TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    state TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 1,
                    next_attempt_at TEXT,
                    job_id TEXT,
                    error TEXT,
                    UNIQUE(schedule_id, scheduled_for)
                );
                CREATE INDEX IF NOT EXISTS idx_schedules_due
                    ON schedules(state, next_run_at);
                CREATE INDEX IF NOT EXISTS idx_schedule_runs_active
                    ON schedule_runs(state, next_attempt_at);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_schedule_runs_one_active
                    ON schedule_runs(schedule_id)
                    WHERE state IN ('claimed','running','retry_wait');
                """
            )
            columns = {
                row["name"]
                for row in self._db.execute("PRAGMA table_info(jobs)").fetchall()
            }
            if "lane" not in columns:
                self._db.execute("ALTER TABLE jobs ADD COLUMN lane TEXT")
            self._db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_one_active_lane "
                "ON jobs(lane) WHERE lane IS NOT NULL AND "
                "state IN ('queued','running','waiting_action','cancelling')"
            )
            self._db.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    def create_schedule(
        self,
        *,
        idempotency_key: str,
        name: str,
        timezone_name: str,
        rrule: str,
        start_at: str,
        next_run_at: str,
        request: Mapping[str, Any],
        retry_count: int,
        retry_backoff_seconds: float,
        misfire_policy: str,
    ) -> tuple[dict[str, Any], bool]:
        """Create once by idempotency key and return ``(record, created)``."""
        now = _now()
        schedule_id = f"sch-{uuid.uuid4().hex[:16]}"
        request_json = _encode(dict(request))
        assert request_json is not None
        with self._lock, self._db:
            existing = self._db.execute(
                "SELECT * FROM schedules WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                return self._schedule_row(existing), False
            self._db.execute(
                "INSERT INTO schedules("
                "schedule_id,idempotency_key,name,state,created_at,updated_at,"
                "timezone,rrule,start_at,next_run_at,request_json,retry_count,"
                "retry_backoff_seconds,misfire_policy"
                ") VALUES(?,?,?,'enabled',?,?,?,?,?,?,?,?,?,?)",
                (
                    schedule_id,
                    idempotency_key,
                    name,
                    now,
                    now,
                    timezone_name,
                    rrule,
                    start_at,
                    next_run_at,
                    request_json,
                    retry_count,
                    retry_backoff_seconds,
                    misfire_policy,
                ),
            )
        record = self.get_schedule(schedule_id)
        assert record is not None
        return record, True

    def get_schedule(self, schedule_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM schedules WHERE schedule_id=?", (schedule_id,)
            ).fetchone()
            return self._schedule_row(row) if row is not None else None

    def list_schedules(self, *, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM schedules ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [self._schedule_row(row, include_request=False) for row in rows]

    def set_schedule_state(self, schedule_id: str, state: str) -> dict[str, Any]:
        if state not in {"enabled", "paused"}:
            raise GatewayError(
                "SCHEDULE_STATE_INVALID", "schedule state must be enabled or paused"
            )
        with self._lock, self._db:
            changed = self._db.execute(
                "UPDATE schedules SET state=?,updated_at=? WHERE schedule_id=?",
                (state, _now(), schedule_id),
            ).rowcount
        if not changed:
            raise GatewayError(
                "SCHEDULE_NOT_FOUND", f"schedule not found: {schedule_id}"
            )
        record = self.get_schedule(schedule_id)
        assert record is not None
        return record

    def delete_schedule(self, schedule_id: str) -> bool:
        with self._lock, self._db:
            return (
                self._db.execute(
                    "DELETE FROM schedules WHERE schedule_id=?", (schedule_id,)
                ).rowcount
                > 0
            )

    def due_schedules(self, now: str, *, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM schedules WHERE state='enabled' "
                "AND next_run_at IS NOT NULL AND next_run_at<=? "
                "ORDER BY next_run_at LIMIT ?",
                (now, limit),
            ).fetchall()
            return [self._schedule_row(row) for row in rows]

    def advance_schedule(
        self, schedule_id: str, *, expected: str, next_run_at: Optional[str]
    ) -> bool:
        with self._lock, self._db:
            return (
                self._db.execute(
                    "UPDATE schedules SET next_run_at=?,updated_at=? "
                    "WHERE schedule_id=? AND next_run_at=?",
                    (next_run_at, _now(), schedule_id, expected),
                ).rowcount
                > 0
            )

    def create_schedule_run(
        self, schedule_id: str, scheduled_for: str
    ) -> Optional[dict[str, Any]]:
        run_id = f"run-{uuid.uuid4().hex[:16]}"
        now = _now()
        with self._lock, self._db:
            try:
                self._db.execute(
                    "INSERT INTO schedule_runs("
                    "run_id,schedule_id,scheduled_for,started_at,state"
                    ") VALUES(?,?,?,?,'claimed')",
                    (run_id, schedule_id, scheduled_for, now),
                )
            except sqlite3.IntegrityError:
                return None
        return self.get_schedule_run(run_id)

    def update_schedule_run(
        self,
        run_id: str,
        *,
        state: str,
        job_id: Optional[str] = None,
        error: Optional[str] = None,
        attempt: Optional[int] = None,
        next_attempt_at: Optional[str] = None,
        completed: bool = False,
    ) -> dict[str, Any]:
        with self._lock, self._db:
            changed = self._db.execute(
                "UPDATE schedule_runs SET state=?,job_id=COALESCE(?,job_id),"
                "error=?,attempt=COALESCE(?,attempt),next_attempt_at=?,"
                "completed_at=CASE WHEN ? THEN ? ELSE completed_at END "
                "WHERE run_id=?",
                (
                    state,
                    job_id,
                    error,
                    attempt,
                    next_attempt_at,
                    1 if completed else 0,
                    _now(),
                    run_id,
                ),
            ).rowcount
        if not changed:
            raise GatewayError("SCHEDULE_RUN_NOT_FOUND", f"run not found: {run_id}")
        record = self.get_schedule_run(run_id)
        assert record is not None
        return record

    def get_schedule_run(self, run_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM schedule_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            return self._schedule_run_row(row) if row is not None else None

    def active_schedule_runs(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM schedule_runs WHERE state IN "
                "('claimed','running','retry_wait') ORDER BY started_at"
            ).fetchall()
            return [self._schedule_run_row(row) for row in rows]

    def active_schedule_run(self, schedule_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM schedule_runs WHERE schedule_id=? AND state IN "
                "('claimed','running','retry_wait') ORDER BY started_at LIMIT 1",
                (schedule_id,),
            ).fetchone()
            return self._schedule_run_row(row) if row is not None else None

    def list_schedule_runs(
        self, *, schedule_id: Optional[str] = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        with self._lock:
            if schedule_id is None:
                rows = self._db.execute(
                    "SELECT * FROM schedule_runs ORDER BY started_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                rows = self._db.execute(
                    "SELECT * FROM schedule_runs WHERE schedule_id=? "
                    "ORDER BY started_at DESC LIMIT ?",
                    (schedule_id, limit),
                ).fetchall()
            return [self._schedule_run_row(row) for row in rows]

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._db.close()

    def recover_interrupted(self) -> int:
        """Mark nonterminal jobs orphaned; resumption always requires an explicit call."""
        now = _now()
        with self._lock, self._db:
            rows = self._db.execute(
                "SELECT job_id FROM jobs WHERE state IN ('queued','running','waiting_action','cancelling')"
            ).fetchall()
            for row in rows:
                self._db.execute(
                    "UPDATE jobs SET state='orphaned', updated_at=?, "
                    "error='gateway restarted while job was active' WHERE job_id=?",
                    (now, row["job_id"]),
                )
                self._db.execute(
                    "INSERT INTO job_transitions(job_id, at, state, details_json) "
                    "VALUES(?, ?, 'orphaned', ?)",
                    (
                        row["job_id"],
                        now,
                        _encode({"reason": "gateway_restart"}),
                    ),
                )
            return len(rows)

    def create(self, kind: str, request: Mapping[str, Any]) -> dict[str, Any]:
        if kind not in {"goal", "turn"}:
            raise GatewayError("JOB_KIND_INVALID", "job kind must be goal or turn")
        job_id = f"job-{uuid.uuid4().hex[:16]}"
        now = _now()
        request_json = _encode(dict(request))
        assert request_json is not None
        raw_lane = request.get("_lane")
        lane = raw_lane if isinstance(raw_lane, str) and raw_lane else None
        with self._lock, self._db:
            try:
                self._db.execute(
                    "INSERT INTO jobs("
                    "job_id,kind,state,created_at,updated_at,request_json,lane"
                    ") VALUES(?,?,'queued',?,?,?,?)",
                    (job_id, kind, now, now, request_json, lane),
                )
            except sqlite3.IntegrityError as exc:
                if lane is not None:
                    active = self.find_active_lane(lane)
                    raise GatewayError(
                        "LANE_BUSY",
                        f"lane already has an active job: {lane}",
                        lane=lane,
                        jobId=active["jobId"] if active else None,
                    ) from exc
                raise
            self._db.execute(
                "INSERT INTO job_transitions(job_id,at,state,details_json) "
                "VALUES(?,?,'queued',NULL)",
                (job_id, now),
            )
        record = self.get(job_id)
        assert record is not None
        return record

    def transition(
        self,
        job_id: str,
        state: str,
        *,
        thread_id: Optional[str] = None,
        turn_id: Optional[str] = None,
        result: Any = None,
        error: Optional[str] = None,
        details: Any = None,
    ) -> dict[str, Any]:
        if state not in ALL_STATES:
            raise GatewayError("JOB_STATE_INVALID", f"invalid job state: {state}")
        now = _now()
        with self._lock, self._db:
            current = self._db.execute(
                "SELECT job_id,lane FROM jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            if current is None:
                raise GatewayError("JOB_NOT_FOUND", f"job not found: {job_id}")
            try:
                self._db.execute(
                    "UPDATE jobs SET state=?,updated_at=?,"
                    "thread_id=COALESCE(?,thread_id),turn_id=COALESCE(?,turn_id),"
                    "result_json=COALESCE(?,result_json),error=? WHERE job_id=?",
                    (
                        state,
                        now,
                        thread_id,
                        turn_id,
                        _encode(result),
                        error,
                        job_id,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                lane = current["lane"]
                if lane is not None and state in ACTIVE_STATES:
                    active = self.find_active_lane(lane)
                    raise GatewayError(
                        "LANE_BUSY",
                        f"lane already has an active job: {lane}",
                        lane=lane,
                        jobId=active["jobId"] if active else None,
                    ) from exc
                raise
            self._db.execute(
                "INSERT INTO job_transitions(job_id,at,state,details_json) VALUES(?,?,?,?)",
                (job_id, now, state, _encode(details)),
            )
        record = self.get(job_id)
        assert record is not None
        return record

    def get(
        self, job_id: str, *, include_history: bool = False
    ) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            if row is None:
                return None
            record = self._row(row)
            if include_history:
                transitions = self._db.execute(
                    "SELECT at,state,details_json FROM job_transitions "
                    "WHERE job_id=? ORDER BY transition_id",
                    (job_id,),
                ).fetchall()
                record["history"] = [
                    {
                        "at": item["at"],
                        "state": item["state"],
                        "details": _decode(item["details_json"]),
                    }
                    for item in transitions
                ]
            return record

    def list(
        self, *, limit: int = 50, state: Optional[str] = None
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        with self._lock:
            if state is None:
                rows = self._db.execute(
                    "SELECT * FROM jobs ORDER BY updated_at DESC LIMIT ?", (limit,)
                ).fetchall()
            else:
                if state not in ALL_STATES:
                    raise GatewayError(
                        "JOB_STATE_INVALID", f"invalid job state: {state}"
                    )
                rows = self._db.execute(
                    "SELECT * FROM jobs WHERE state=? ORDER BY updated_at DESC LIMIT ?",
                    (state, limit),
                ).fetchall()
            return [
                self._row(row, include_request=False, include_result=False)
                for row in rows
            ]

    def find_active_lane(self, lane: str) -> Optional[dict[str, Any]]:
        """Return the newest active job that owns ``lane``.

        The lane marker lives inside request_json so schema version 1 remains
        compatible with already-created state databases.
        """
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM jobs WHERE lane=? AND state IN "
                "('queued','running','waiting_action','cancelling') "
                "ORDER BY updated_at DESC LIMIT 1",
                (lane,),
            ).fetchone()
            return self._row(row) if row is not None else None

    @staticmethod
    def _row(
        row: sqlite3.Row,
        *,
        include_request: bool = True,
        include_result: bool = True,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "jobId": row["job_id"],
            "kind": row["kind"],
            "state": row["state"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "threadId": row["thread_id"],
            "turnId": row["turn_id"],
            "lane": row["lane"],
            "error": row["error"],
        }
        if include_request:
            result["request"] = _decode(row["request_json"])
        if include_result:
            result["result"] = _decode(row["result_json"])
        return result

    @staticmethod
    def _schedule_row(
        row: sqlite3.Row, *, include_request: bool = True
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "scheduleId": row["schedule_id"],
            "idempotencyKey": row["idempotency_key"],
            "name": row["name"],
            "state": row["state"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "timezone": row["timezone"],
            "rrule": row["rrule"],
            "startAt": row["start_at"],
            "nextRunAt": row["next_run_at"],
            "retryCount": row["retry_count"],
            "retryBackoffSeconds": row["retry_backoff_seconds"],
            "misfirePolicy": row["misfire_policy"],
        }
        if include_request:
            result["request"] = _decode(row["request_json"])
        return result

    @staticmethod
    def _schedule_run_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "runId": row["run_id"],
            "scheduleId": row["schedule_id"],
            "scheduledFor": row["scheduled_for"],
            "startedAt": row["started_at"],
            "completedAt": row["completed_at"],
            "state": row["state"],
            "attempt": row["attempt"],
            "nextAttemptAt": row["next_attempt_at"],
            "jobId": row["job_id"],
            "error": row["error"],
        }
