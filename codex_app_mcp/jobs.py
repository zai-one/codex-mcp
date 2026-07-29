"""Background goal/turn execution backed by the durable state ledger."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any, Mapping

from .errors import GatewayError
from .policy import validate_limit, validate_text, validate_thread_id
from .state import ACTIVE_STATES, StateStore

if TYPE_CHECKING:
    from .gateway import CodexAppGateway

GOAL_TERMINAL = frozenset(
    {"paused", "blocked", "usageLimited", "budgetLimited", "complete"}
)


class JobManager:
    def __init__(
        self,
        gateway: "CodexAppGateway",
        store: StateStore,
        *,
        poll_seconds: float = 1.0,
    ) -> None:
        self.gateway = gateway
        self.store = store
        self.poll_seconds = max(0.1, float(poll_seconds))
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._workers: dict[str, threading.Thread] = {}
        self.store.recover_interrupted()

    def begin_close(self) -> None:
        self._stop.set()
        with self._lock:
            ids = list(self._workers)
        for job_id in ids:
            record = self.store.get(job_id)
            if record is not None and record["state"] in ACTIVE_STATES:
                self.store.transition(
                    job_id,
                    "orphaned",
                    error="gateway shut down while job was active",
                    details={"reason": "gateway_shutdown"},
                )

    def end_close(self) -> None:
        with self._lock:
            workers = list(self._workers.values())
        for worker in workers:
            worker.join(timeout=2.0)
        if not any(worker.is_alive() for worker in workers):
            self.store.close()

    def close(self) -> None:
        """Standalone close; gateway normally brackets transport shutdown itself."""
        self.begin_close()
        self.end_close()

    def handle(self, args: Mapping[str, Any]) -> dict[str, Any]:
        action = str(args.get("action") or "")
        if action == "start":
            return self._start(args)
        if action == "get":
            job_id = validate_text(args.get("jobId"), "jobId")
            record = self.store.get(
                job_id, include_history=bool(args.get("includeHistory"))
            )
            if record is None:
                raise GatewayError("JOB_NOT_FOUND", f"job not found: {job_id}")
            return {"ok": True, "job": record}
        if action == "list":
            state = args.get("state")
            if state is not None and not isinstance(state, str):
                raise GatewayError("JOB_STATE_INVALID", "state must be a string")
            return {
                "ok": True,
                "jobs": self.store.list(
                    limit=validate_limit(args.get("limit"), default=50),
                    state=state,
                ),
            }
        if action == "cancel":
            return self._cancel(validate_text(args.get("jobId"), "jobId"))
        if action == "resume":
            return self._resume(validate_text(args.get("jobId"), "jobId"))
        raise GatewayError("JOB_ACTION_INVALID", f"unknown job action: {action}")

    def _start(self, args: Mapping[str, Any]) -> dict[str, Any]:
        kind = args.get("kind")
        if kind not in {"goal", "turn"}:
            raise GatewayError("JOB_KIND_INVALID", "kind must be goal or turn")
        request = args.get("request")
        if not isinstance(request, Mapping):
            raise GatewayError("JOB_REQUEST_INVALID", "request must be an object")
        payload = dict(request)
        payload["action"] = "start"
        # Validate synchronously before returning a durable handle.
        if kind == "goal":
            if payload.get("threadId") is None:
                self.gateway.policy.validate_cwd(payload.get("cwd"))
            else:
                validate_thread_id(payload.get("threadId"))
            validate_text(payload.get("objective"), "objective")
        else:
            validate_thread_id(payload.get("threadId"))
            validate_text(payload.get("prompt"), "prompt")
        with self._lock:
            lane = payload.get("_lane")
            if isinstance(lane, str) and lane:
                active = self.store.find_active_lane(lane)
                if active is not None:
                    raise GatewayError(
                        "LANE_BUSY",
                        f"lane already has an active job: {lane}",
                        jobId=active["jobId"],
                        lane=lane,
                    )
            record = self.store.create(kind, payload)
            self._launch_locked(record["jobId"], kind, payload, resume=False)
        return {"ok": True, "job": self.store.get(record["jobId"])}

    def _resume(self, job_id: str) -> dict[str, Any]:
        record = self.store.get(job_id)
        if record is None:
            raise GatewayError("JOB_NOT_FOUND", f"job not found: {job_id}")
        if record["state"] in ACTIVE_STATES:
            raise GatewayError(
                "JOB_ALREADY_RUNNING", f"job is already active: {job_id}"
            )
        if record["kind"] != "goal":
            raise GatewayError(
                "JOB_NOT_RESUMABLE",
                "individual turns cannot be resumed; start a new turn job",
            )
        if record["state"] not in {"attention", "orphaned"}:
            raise GatewayError(
                "JOB_NOT_RESUMABLE",
                f"job in state {record['state']} cannot be resumed",
            )
        thread_id = record.get("threadId")
        if not thread_id:
            raise GatewayError(
                "JOB_NOT_RESUMABLE",
                "job has no persisted threadId; start a new job",
            )
        request = dict(record["request"])
        request["threadId"] = thread_id
        self.store.transition(
            job_id,
            "queued",
            result={},
            error=None,
            details={"reason": "explicit_resume"},
        )
        self._launch(job_id, record["kind"], request, resume=True)
        return {"ok": True, "job": self.store.get(job_id)}

    def _cancel(self, job_id: str) -> dict[str, Any]:
        record = self.store.get(job_id)
        if record is None:
            raise GatewayError("JOB_NOT_FOUND", f"job not found: {job_id}")
        if record["state"] in {"succeeded", "failed", "cancelled"}:
            return {"ok": True, "job": record, "alreadyTerminal": True}
        self.store.transition(job_id, "cancelling")
        try:
            if record["kind"] == "goal" and record.get("threadId"):
                self.gateway.goal(
                    {
                        "action": "pause",
                        "threadId": record["threadId"],
                    }
                )
            elif (
                record["kind"] == "turn"
                and record.get("threadId")
                and record.get("turnId")
            ):
                self.gateway.turn(
                    {
                        "action": "interrupt",
                        "threadId": record["threadId"],
                        "turnId": record["turnId"],
                    }
                )
        except Exception as exc:
            failed = self.store.transition(
                job_id,
                "failed",
                error=f"cancel failed: {type(exc).__name__}: {exc}",
            )
            return {"ok": False, "job": failed}
        cancelled = self.store.transition(
            job_id,
            "cancelled",
            details={"reason": "caller_cancelled"},
        )
        return {"ok": True, "job": cancelled}

    def _launch(
        self,
        job_id: str,
        kind: str,
        request: dict[str, Any],
        *,
        resume: bool,
    ) -> None:
        with self._lock:
            self._launch_locked(job_id, kind, request, resume=resume)

    def _launch_locked(
        self,
        job_id: str,
        kind: str,
        request: dict[str, Any],
        *,
        resume: bool,
    ) -> None:
        worker = self._workers.get(job_id)
        if worker is not None and worker.is_alive():
            raise GatewayError(
                "JOB_ALREADY_RUNNING", f"job is already active: {job_id}"
            )
        worker = threading.Thread(
            target=self._run,
            args=(job_id, kind, request, resume),
            name=f"codex-app-mcp-{job_id}",
            daemon=True,
        )
        self._workers[job_id] = worker
        worker.start()

    def _run(
        self,
        job_id: str,
        kind: str,
        request: dict[str, Any],
        resume: bool,
    ) -> None:
        try:
            self.store.transition(job_id, "running")
            if kind == "goal":
                self._run_goal(job_id, request, resume=resume)
            else:
                self._run_turn(job_id, request, resume=resume)
        except BaseException as exc:  # a background exception must remain observable
            current = self.store.get(job_id)
            if current is not None and current["state"] in ACTIVE_STATES:
                self.store.transition(
                    job_id,
                    "failed",
                    error=f"{type(exc).__name__}: {exc}",
                )
        finally:
            with self._lock:
                self._workers.pop(job_id, None)

    def _run_goal(self, job_id: str, request: dict[str, Any], *, resume: bool) -> None:
        if resume:
            started = self.gateway.goal(
                {"action": "resume", "threadId": request["threadId"]}
            )
        else:
            started = self.gateway.goal(request)
        thread_id = started["threadId"]
        self.store.transition(job_id, "running", thread_id=thread_id)
        while not self._stop.wait(self.poll_seconds):
            if not self._still_active(job_id):
                return
            actions = self.gateway.events.pending_actions(thread_id)
            if actions:
                self.store.transition(
                    job_id,
                    "waiting_action",
                    details={"pendingActionCount": len(actions)},
                )
            else:
                current = self.store.get(job_id)
                if current is not None and current["state"] == "waiting_action":
                    self.store.transition(job_id, "running")
            response = self.gateway.goal({"action": "get", "threadId": thread_id})
            result = response.get("result")
            goal = result.get("goal") if isinstance(result, Mapping) else None
            status = goal.get("status") if isinstance(goal, Mapping) else None
            if status in GOAL_TERMINAL:
                state = "succeeded" if status == "complete" else "attention"
                self.store.transition(
                    job_id,
                    state,
                    result={"goal": goal},
                    details={"goalStatus": status},
                )
                return
        self._orphan_if_active(job_id)

    def _run_turn(self, job_id: str, request: dict[str, Any], *, resume: bool) -> None:
        if resume:
            raise GatewayError(
                "JOB_NOT_RESUMABLE",
                "individual turns cannot be resumed; resume the owning goal or start a new turn",
            )
        started = self.gateway.turn(request)
        thread_id = started["threadId"]
        turn_id = started["turnId"]
        self.store.transition(
            job_id,
            "running",
            thread_id=thread_id,
            turn_id=turn_id,
        )
        cursor = 1
        while not self._stop.wait(self.poll_seconds):
            if not self._still_active(job_id):
                return
            polled = self.gateway.event(
                {
                    "action": "poll",
                    "threadId": thread_id,
                    "cursor": cursor,
                    "limit": 200,
                    "includeActions": True,
                }
            )
            cursor = int(polled["nextCursor"])
            actions = polled.get("actions") or []
            if actions:
                self.store.transition(
                    job_id,
                    "waiting_action",
                    details={"pendingActionCount": len(actions)},
                )
            for event in polled["events"]:
                params = event.get("params")
                event_turn = None
                if isinstance(params, Mapping):
                    event_turn = params.get("turnId")
                    nested = params.get("turn")
                    if event_turn is None and isinstance(nested, Mapping):
                        event_turn = nested.get("id")
                if event_turn not in {None, turn_id}:
                    continue
                if event["method"] == "turn/completed":
                    turn = params.get("turn") if isinstance(params, Mapping) else None
                    turn_status = (
                        turn.get("status") if isinstance(turn, Mapping) else "completed"
                    )
                    if turn_status == "failed":
                        self.store.transition(
                            job_id,
                            "failed",
                            result={"event": event},
                            error="turn completed with failed status",
                            details={"turnStatus": turn_status},
                        )
                        return
                    if turn_status == "interrupted":
                        self.store.transition(
                            job_id,
                            "cancelled",
                            result={"event": event},
                            details={"turnStatus": turn_status},
                        )
                        return
                    self.store.transition(
                        job_id,
                        "succeeded",
                        result={"event": event},
                        details={"turnStatus": turn_status},
                    )
                    return
                if event["method"] in {"turn/failed", "error"}:
                    self.store.transition(
                        job_id,
                        "failed",
                        result={"event": event},
                        error="turn failed",
                    )
                    return
        self._orphan_if_active(job_id)

    def _still_active(self, job_id: str) -> bool:
        record = self.store.get(job_id)
        return record is not None and record["state"] in ACTIVE_STATES

    def _orphan_if_active(self, job_id: str) -> None:
        if self._still_active(job_id):
            self.store.transition(
                job_id,
                "orphaned",
                error="gateway shut down while job was active",
            )
