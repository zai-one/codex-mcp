"""Durable recurring job scheduler layered over Codex app-server jobs."""

from __future__ import annotations

import os
import threading
from calendar import monthrange
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from typing import TYPE_CHECKING, Any, Mapping, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .errors import GatewayError
from .policy import validate_limit, validate_text, validate_thread_id
from .state import StateStore

if TYPE_CHECKING:
    from .gateway import CodexAppGateway

UTC = timezone.utc
TERMINAL_JOB_STATES = frozenset(
    {"succeeded", "attention", "failed", "cancelled", "orphaned"}
)
SUPPORTED_RULE_KEYS = frozenset(
    {"FREQ", "INTERVAL", "BYDAY", "BYMONTHDAY", "BYHOUR", "BYMINUTE", "UNTIL"}
)
WEEKDAYS = {
    "MO": 0,
    "TU": 1,
    "WE": 2,
    "TH": 3,
    "FR": 4,
    "SA": 5,
    "SU": 6,
}


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _parse_iso(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise GatewayError(f"{field.upper()}_INVALID", f"{field} must be ISO-8601")
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise GatewayError(
            f"{field.upper()}_INVALID", f"{field} must be valid ISO-8601"
        ) from exc
    if parsed.tzinfo is None:
        raise GatewayError(
            f"{field.upper()}_INVALID", f"{field} must include a UTC offset"
        )
    return parsed.astimezone(UTC)


def _parse_rrule(value: Any) -> dict[str, str]:
    text = validate_text(value, "rrule")
    assert text is not None
    if text.upper().startswith("RRULE:"):
        text = text[6:]
    rule: dict[str, str] = {}
    for item in text.split(";"):
        if "=" not in item:
            raise GatewayError("RRULE_INVALID", f"invalid RRULE component: {item}")
        key, raw = item.split("=", 1)
        key = key.strip().upper()
        raw = raw.strip().upper()
        if key not in SUPPORTED_RULE_KEYS:
            raise GatewayError("RRULE_UNSUPPORTED", f"unsupported RRULE key: {key}")
        if not raw:
            raise GatewayError("RRULE_INVALID", f"empty RRULE value: {key}")
        rule[key] = raw
    freq = rule.get("FREQ")
    if freq not in {"ONCE", "MINUTELY", "HOURLY", "DAILY", "WEEKLY", "MONTHLY"}:
        raise GatewayError(
            "RRULE_FREQ_INVALID",
            "FREQ must be ONCE, MINUTELY, HOURLY, DAILY, WEEKLY, or MONTHLY",
        )
    try:
        interval = int(rule.get("INTERVAL", "1"))
    except ValueError as exc:
        raise GatewayError(
            "RRULE_INTERVAL_INVALID", "INTERVAL must be an integer"
        ) from exc
    if interval < 1 or interval > 10_000:
        raise GatewayError(
            "RRULE_INTERVAL_INVALID", "INTERVAL must be between 1 and 10000"
        )
    rule["INTERVAL"] = str(interval)
    for key, low, high in (("BYHOUR", 0, 23), ("BYMINUTE", 0, 59)):
        if key in rule:
            _parse_numbers(rule[key], key, low, high)
    if "BYMONTHDAY" in rule:
        _parse_numbers(rule["BYMONTHDAY"], "BYMONTHDAY", 1, 31)
    if "BYDAY" in rule:
        invalid = set(rule["BYDAY"].split(",")) - set(WEEKDAYS)
        if invalid:
            raise GatewayError(
                "RRULE_BYDAY_INVALID", f"invalid BYDAY values: {sorted(invalid)}"
            )
    if "UNTIL" in rule:
        _parse_until(rule["UNTIL"])
    return rule


def _parse_numbers(value: str, field: str, low: int, high: int) -> list[int]:
    try:
        values = sorted({int(item) for item in value.split(",")})
    except ValueError as exc:
        raise GatewayError(
            f"RRULE_{field}_INVALID", f"{field} must contain integers"
        ) from exc
    if not values or values[0] < low or values[-1] > high:
        raise GatewayError(
            f"RRULE_{field}_INVALID", f"{field} must be between {low} and {high}"
        )
    return values


def _parse_until(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError as exc:
        raise GatewayError(
            "RRULE_UNTIL_INVALID", "UNTIL must use UTC form YYYYMMDDTHHMMSSZ"
        ) from exc


def next_occurrence(
    rule_text: str,
    *,
    start: datetime,
    after: datetime,
    timezone_name: str,
) -> Optional[datetime]:
    """Return the first occurrence strictly after ``after``."""
    rule = _parse_rrule(rule_text)
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise GatewayError(
            "TIMEZONE_INVALID", f"unknown IANA timezone: {timezone_name}"
        ) from exc
    start_local = start.astimezone(zone)
    after_local = after.astimezone(zone)
    freq = rule["FREQ"]
    interval = int(rule["INTERVAL"])
    if start > after:
        return _bounded(start, rule)
    if freq == "ONCE":
        return None
    if freq == "MINUTELY":
        candidate = after_local.replace(second=0, microsecond=0) + timedelta(
            minutes=interval
        )
        return _bounded(candidate.astimezone(UTC), rule)
    if freq == "HOURLY":
        minutes = _parse_numbers(
            rule.get("BYMINUTE", str(start_local.minute)), "BYMINUTE", 0, 59
        )
        base = after_local.replace(minute=0, second=0, microsecond=0)
        for offset in range(0, interval + 2):
            hour = base + timedelta(hours=offset)
            elapsed = int(
                (
                    hour - start_local.replace(minute=0, second=0, microsecond=0)
                ).total_seconds()
                // 3600
            )
            if elapsed < 0 or elapsed % interval:
                continue
            for minute in minutes:
                candidate = hour.replace(minute=minute)
                if candidate > after_local:
                    return _bounded(candidate.astimezone(UTC), rule)
        return None

    hours = _parse_numbers(rule.get("BYHOUR", str(start_local.hour)), "BYHOUR", 0, 23)
    minutes = _parse_numbers(
        rule.get("BYMINUTE", str(start_local.minute)), "BYMINUTE", 0, 59
    )
    weekdays = (
        {WEEKDAYS[item] for item in rule["BYDAY"].split(",")}
        if "BYDAY" in rule
        else None
    )
    monthdays = (
        set(_parse_numbers(rule["BYMONTHDAY"], "BYMONTHDAY", 1, 31))
        if "BYMONTHDAY" in rule
        else None
    )
    start_date = start_local.date()
    current = after_local.date()
    for day_offset in range(0, 366 * 20):
        candidate_date = current + timedelta(days=day_offset)
        if candidate_date < start_date:
            continue
        days = (candidate_date - start_date).days
        months = (candidate_date.year - start_date.year) * 12 + (
            candidate_date.month - start_date.month
        )
        if freq == "DAILY" and days % interval:
            continue
        if freq == "WEEKLY":
            if (days // 7) % interval:
                continue
            expected = weekdays or {start_date.weekday()}
            if candidate_date.weekday() not in expected:
                continue
        if freq == "MONTHLY":
            if months % interval:
                continue
            expected_days = monthdays or {start_date.day}
            if candidate_date.day not in expected_days:
                continue
            if (
                candidate_date.day
                > monthrange(candidate_date.year, candidate_date.month)[1]
            ):
                continue
        if freq != "WEEKLY" and weekdays is not None:
            if candidate_date.weekday() not in weekdays:
                continue
        if freq != "MONTHLY" and monthdays is not None:
            if candidate_date.day not in monthdays:
                continue
        for hour in hours:
            for minute in minutes:
                candidate = datetime.combine(
                    candidate_date,
                    datetime_time(hour=hour, minute=minute),
                    tzinfo=zone,
                )
                if candidate >= start_local and candidate > after_local:
                    return _bounded(candidate.astimezone(UTC), rule)
    raise GatewayError(
        "RRULE_NO_OCCURRENCE", "no occurrence found within the 20-year safety horizon"
    )


def _bounded(candidate: datetime, rule: Mapping[str, str]) -> Optional[datetime]:
    until = _parse_until(rule["UNTIL"]) if "UNTIL" in rule else None
    return None if until is not None and candidate > until else candidate


class SchedulerManager:
    def __init__(
        self,
        gateway: "CodexAppGateway",
        store: StateStore,
        *,
        poll_seconds: Optional[float] = None,
    ) -> None:
        self.gateway = gateway
        self.store = store
        self.poll_seconds = max(
            0.2,
            float(
                poll_seconds
                if poll_seconds is not None
                else os.environ.get("CODEX_APP_MCP_SCHEDULER_POLL_SECONDS", "1")
            ),
        )
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._metrics_lock = threading.Lock()
        self._tick_count = 0
        self._launch_count = 0
        self._error_count = 0
        self._last_error: Optional[str] = None
        self._thread = threading.Thread(
            target=self._loop, name="codex-app-mcp-scheduler", daemon=True
        )
        self._thread.start()

    def metrics(self) -> dict[str, Any]:
        with self._metrics_lock:
            return {
                "running": self._thread.is_alive() and not self._stop.is_set(),
                "tickCount": self._tick_count,
                "launchCount": self._launch_count,
                "errorCount": self._error_count,
                "lastError": self._last_error,
            }

    def begin_close(self) -> None:
        self._stop.set()
        self._wake.set()

    def end_close(self) -> None:
        self._thread.join(timeout=3.0)
        if not self._thread.is_alive():
            self.store.close()

    def handle(self, args: Mapping[str, Any]) -> dict[str, Any]:
        action = str(args.get("action") or "")
        if action == "create":
            return self._create(args)
        if action == "get":
            record = self._get(args.get("scheduleId"))
            return {"ok": True, "schedule": record}
        if action == "list":
            return {
                "ok": True,
                "schedules": self.store.list_schedules(
                    limit=validate_limit(args.get("limit"), default=50)
                ),
            }
        if action in {"pause", "resume"}:
            schedule_id = validate_text(args.get("scheduleId"), "scheduleId")
            state = "paused" if action == "pause" else "enabled"
            record = self.store.set_schedule_state(schedule_id, state)
            self._wake.set()
            return {"ok": True, "schedule": record}
        if action == "delete":
            schedule_id = validate_text(args.get("scheduleId"), "scheduleId")
            if not self.store.delete_schedule(schedule_id):
                raise GatewayError(
                    "SCHEDULE_NOT_FOUND", f"schedule not found: {schedule_id}"
                )
            return {"ok": True, "deleted": True, "scheduleId": schedule_id}
        if action == "trigger":
            schedule = self._get(args.get("scheduleId"))
            run = self._claim_and_launch(schedule, _utc_now())
            return {"ok": True, "run": run}
        if action == "runs":
            schedule_id = args.get("scheduleId")
            if schedule_id is not None:
                schedule_id = validate_text(schedule_id, "scheduleId")
            return {
                "ok": True,
                "runs": self.store.list_schedule_runs(
                    schedule_id=schedule_id,
                    limit=validate_limit(args.get("limit"), default=50),
                ),
            }
        raise GatewayError(
            "SCHEDULE_ACTION_INVALID", f"unknown schedule action: {action}"
        )

    def _create(self, args: Mapping[str, Any]) -> dict[str, Any]:
        name = validate_text(args.get("name"), "name")
        idempotency_key = validate_text(args.get("idempotencyKey"), "idempotencyKey")
        timezone_name = validate_text(args.get("timezone") or "UTC", "timezone")
        try:
            ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise GatewayError(
                "TIMEZONE_INVALID", f"unknown IANA timezone: {timezone_name}"
            ) from exc
        raw_rule = validate_text(args.get("rrule"), "rrule")
        rule = _parse_rrule(raw_rule)
        canonical_rule = "RRULE:" + ";".join(
            f"{key}={value}" for key, value in rule.items()
        )
        start = (
            _parse_iso(args.get("startAt"), "startAt")
            if args.get("startAt") is not None
            else _utc_now()
        )
        request = args.get("request")
        if not isinstance(request, Mapping):
            raise GatewayError("SCHEDULE_REQUEST_INVALID", "request must be an object")
        payload = dict(request)
        kind = payload.get("kind")
        nested = payload.get("request")
        if kind not in {"goal", "turn"} or not isinstance(nested, Mapping):
            raise GatewayError(
                "SCHEDULE_REQUEST_INVALID",
                "request must contain kind=goal|turn and a request object",
            )
        self._validate_job_request(kind, nested)
        retry_count = args.get("retryCount", 0)
        if isinstance(retry_count, bool) or not isinstance(retry_count, int):
            raise GatewayError("RETRY_COUNT_INVALID", "retryCount must be an integer")
        if retry_count < 0 or retry_count > 10:
            raise GatewayError(
                "RETRY_COUNT_INVALID", "retryCount must be between 0 and 10"
            )
        try:
            retry_backoff = float(args.get("retryBackoffSeconds", 30.0))
        except (TypeError, ValueError) as exc:
            raise GatewayError(
                "RETRY_BACKOFF_INVALID", "retryBackoffSeconds must be numeric"
            ) from exc
        if retry_backoff < 1 or retry_backoff > 86_400:
            raise GatewayError(
                "RETRY_BACKOFF_INVALID",
                "retryBackoffSeconds must be between 1 and 86400",
            )
        misfire = str(args.get("misfirePolicy") or "run_once")
        if misfire not in {"run_once", "skip"}:
            raise GatewayError(
                "MISFIRE_POLICY_INVALID", "misfirePolicy must be run_once or skip"
            )
        after = start - timedelta(microseconds=1)
        first = next_occurrence(
            canonical_rule, start=start, after=after, timezone_name=timezone_name
        )
        if first is None:
            raise GatewayError(
                "SCHEDULE_EXPIRED", "RRULE has no occurrence at or after startAt"
            )
        record, created = self.store.create_schedule(
            idempotency_key=idempotency_key,
            name=name,
            timezone_name=timezone_name,
            rrule=canonical_rule,
            start_at=_iso(start),
            next_run_at=_iso(first),
            request=payload,
            retry_count=retry_count,
            retry_backoff_seconds=retry_backoff,
            misfire_policy=misfire,
        )
        if not created:
            expected = {
                "name": name,
                "timezone": timezone_name,
                "rrule": canonical_rule,
                "startAt": _iso(start),
                "request": payload,
                "retryCount": retry_count,
                "retryBackoffSeconds": retry_backoff,
                "misfirePolicy": misfire,
            }
            actual = {key: record.get(key) for key in expected}
            if actual != expected:
                raise GatewayError(
                    "IDEMPOTENCY_CONFLICT",
                    "idempotencyKey already exists with a different schedule",
                    scheduleId=record["scheduleId"],
                )
        self._wake.set()
        return {"ok": True, "created": created, "schedule": record}

    def _validate_job_request(self, kind: str, request: Mapping[str, Any]) -> None:
        if kind == "goal":
            validate_text(request.get("objective"), "objective")
            if request.get("threadId") is None:
                self.gateway.policy.validate_cwd(request.get("cwd"))
            else:
                validate_thread_id(request.get("threadId"))
        else:
            validate_thread_id(request.get("threadId"))
            validate_text(request.get("prompt"), "prompt")
        sandbox = request.get("sandbox")
        if sandbox is not None:
            self.gateway.policy.validate_sandbox(sandbox)

    def _get(self, value: Any) -> dict[str, Any]:
        schedule_id = validate_text(value, "scheduleId")
        record = self.store.get_schedule(schedule_id)
        if record is None:
            raise GatewayError(
                "SCHEDULE_NOT_FOUND", f"schedule not found: {schedule_id}"
            )
        return record

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
                with self._metrics_lock:
                    self._tick_count += 1
            except BaseException as exc:
                # Keep the service alive, but make every daemon failure observable.
                with self._metrics_lock:
                    self._error_count += 1
                    self._last_error = f"{type(exc).__name__}: {exc}"[:2_000]
            self._wake.wait(self.poll_seconds)
            self._wake.clear()

    def _tick(self) -> None:
        now = _utc_now()
        active = self.store.active_schedule_runs()
        active_schedule_ids = {
            run["scheduleId"]
            for run in active
            if run["state"] in {"claimed", "running", "retry_wait"}
        }
        for run in active:
            self._monitor_run(run, now)
        for schedule in self.store.due_schedules(_iso(now)):
            if schedule["scheduleId"] in active_schedule_ids:
                continue
            scheduled_for = _parse_iso(schedule["nextRunAt"], "nextRunAt")
            following = next_occurrence(
                schedule["rrule"],
                start=_parse_iso(schedule["startAt"], "startAt"),
                after=scheduled_for,
                timezone_name=schedule["timezone"],
            )
            self.store.advance_schedule(
                schedule["scheduleId"],
                expected=schedule["nextRunAt"],
                next_run_at=_iso(following) if following is not None else None,
            )
            if schedule["misfirePolicy"] == "skip" and scheduled_for < (
                now - timedelta(seconds=self.poll_seconds * 2)
            ):
                continue
            self._claim_and_launch(schedule, scheduled_for)

    def _claim_and_launch(
        self, schedule: Mapping[str, Any], scheduled_for: datetime
    ) -> dict[str, Any]:
        run = self.store.create_schedule_run(
            str(schedule["scheduleId"]), _iso(scheduled_for)
        )
        if run is None:
            active = self.store.active_schedule_run(str(schedule["scheduleId"]))
            if active is not None:
                raise GatewayError(
                    "SCHEDULE_BUSY",
                    "schedule already has an active run",
                    runId=active["runId"],
                    jobId=active.get("jobId"),
                )
            raise GatewayError(
                "SCHEDULE_RUN_DUPLICATE",
                "this scheduled occurrence was already claimed",
            )
        return self._launch_run(schedule, run)

    def _launch_run(
        self, schedule: Mapping[str, Any], run: Mapping[str, Any]
    ) -> dict[str, Any]:
        request = schedule.get("request")
        if not isinstance(request, Mapping):
            return self.store.update_schedule_run(
                str(run["runId"]),
                state="failed",
                error="schedule request is missing",
                completed=True,
            )
        try:
            started = self.gateway.job(
                {
                    "action": "start",
                    "kind": request.get("kind"),
                    "request": request.get("request"),
                }
            )
            job_id = started["job"]["jobId"]
            with self._metrics_lock:
                self._launch_count += 1
            return self.store.update_schedule_run(
                str(run["runId"]), state="running", job_id=job_id
            )
        except BaseException as exc:
            return self._retry_or_fail(schedule, run, f"{type(exc).__name__}: {exc}")

    def _monitor_run(self, run: Mapping[str, Any], now: datetime) -> None:
        schedule = self.store.get_schedule(str(run["scheduleId"]))
        if schedule is None:
            return
        if run["state"] == "retry_wait":
            next_attempt = run.get("nextAttemptAt")
            if next_attempt and _parse_iso(next_attempt, "nextAttemptAt") <= now:
                self._launch_run(schedule, run)
            return
        job_id = run.get("jobId")
        if not job_id:
            return
        job = self.gateway.job({"action": "get", "jobId": job_id})["job"]
        if job["state"] not in TERMINAL_JOB_STATES:
            return
        if job["state"] == "succeeded":
            self.store.update_schedule_run(
                str(run["runId"]), state="succeeded", completed=True
            )
        else:
            self._retry_or_fail(
                schedule,
                run,
                job.get("error") or f"job ended in {job['state']}",
            )

    def _retry_or_fail(
        self,
        schedule: Mapping[str, Any],
        run: Mapping[str, Any],
        error: str,
    ) -> dict[str, Any]:
        attempt = int(run.get("attempt") or 1)
        if attempt <= int(schedule.get("retryCount") or 0):
            backoff = float(schedule.get("retryBackoffSeconds") or 30.0) * (
                2 ** (attempt - 1)
            )
            return self.store.update_schedule_run(
                str(run["runId"]),
                state="retry_wait",
                error=error[:2_000],
                attempt=attempt + 1,
                next_attempt_at=_iso(_utc_now() + timedelta(seconds=backoff)),
            )
        return self.store.update_schedule_run(
            str(run["runId"]),
            state="failed",
            error=error[:2_000],
            completed=True,
        )
