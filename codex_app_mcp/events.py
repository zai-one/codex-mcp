"""Thread-safe event buffers and deferred app-server request arbitration."""

from __future__ import annotations

import threading
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional

from .errors import GatewayError

Responder = Callable[[Any, Optional[dict[str, Any]], Optional[dict[str, Any]]], None]

CRITICAL_METHODS = frozenset(
    {
        "error",
        "thread/goal/cleared",
        "thread/goal/updated",
        "thread/status/changed",
        "turn/completed",
        "turn/started",
    }
)
APPROVAL_METHODS = frozenset(
    {
        "applyPatchApproval",
        "execCommandApproval",
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
    }
)
PERMISSION_METHODS = frozenset({"item/permissions/requestApproval"})
USER_INPUT_METHODS = frozenset(
    {
        "item/tool/requestUserInput",
        "tool/requestUserInput",
    }
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _thread_id(params: Any) -> str:
    if isinstance(params, Mapping):
        direct = params.get("threadId")
        if isinstance(direct, str) and direct:
            return direct
        for key in ("thread", "turn"):
            nested = params.get(key)
            if isinstance(nested, Mapping):
                candidate = nested.get("threadId") or (
                    nested.get("id") if key == "thread" else None
                )
                if isinstance(candidate, str) and candidate:
                    return candidate
    return "_global"


@dataclass
class EventBuffer:
    max_size: int = 1_000
    hard_max_size: int = 2_000
    next_id: int = 1
    events: list[dict[str, Any]] = field(default_factory=list)

    def append(
        self, method: str, params: Any, *, pinned: bool = False
    ) -> dict[str, Any]:
        entry = {
            "id": self.next_id,
            "method": method,
            "params": params,
            "timestamp": _now(),
            "pinned": bool(pinned),
        }
        self.next_id += 1
        self.events.append(entry)
        self._evict()
        return entry

    def _evict(self) -> None:
        while len(self.events) > self.max_size:
            index = next(
                (
                    idx
                    for idx, event in enumerate(self.events)
                    if not event.get("pinned")
                ),
                None,
            )
            if index is None:
                break
            self.events.pop(index)
        while len(self.events) > self.hard_max_size:
            self.events.pop(0)

    def poll(self, cursor: int, limit: int) -> dict[str, Any]:
        earliest = self.events[0]["id"] if self.events else self.next_id
        reset = earliest if cursor < earliest else None
        effective = max(cursor, earliest)
        selected = [event.copy() for event in self.events if event["id"] >= effective][
            :limit
        ]
        next_cursor = selected[-1]["id"] + 1 if selected else effective
        result: dict[str, Any] = {
            "events": selected,
            "nextCursor": next_cursor,
        }
        if reset is not None:
            result["cursorResetTo"] = reset
        return result


@dataclass
class PendingAction:
    action_id: str
    rpc_id: Any
    method: str
    params: dict[str, Any]
    thread_id: str
    created_at: str
    expires_at_monotonic: float
    timer: Optional[threading.Timer] = None

    def public(self) -> dict[str, Any]:
        return {
            "actionId": self.action_id,
            "method": self.method,
            "params": self.params,
            "threadId": self.thread_id,
            "createdAt": self.created_at,
            "expiresInSeconds": max(
                0, round(self.expires_at_monotonic - time.monotonic(), 3)
            ),
        }


class EventHub:
    """Buffers notifications and turns server-initiated requests into MCP actions."""

    def __init__(self, *, action_timeout_seconds: float = 60.0) -> None:
        self._lock = threading.RLock()
        self._buffers: dict[str, EventBuffer] = defaultdict(EventBuffer)
        self._actions: dict[str, PendingAction] = {}
        self._responder: Optional[Responder] = None
        self.action_timeout_seconds = max(1.0, float(action_timeout_seconds))

    def bind_responder(self, responder: Responder) -> None:
        self._responder = responder

    def notification(self, method: str, params: Any) -> None:
        tid = _thread_id(params)
        with self._lock:
            self._buffers[tid].append(method, params, pinned=method in CRITICAL_METHODS)

    def server_request(self, rpc_id: Any, method: str, params: Any) -> None:
        payload = dict(params) if isinstance(params, Mapping) else {}
        tid = _thread_id(payload)
        if method == "currentTime/read" and self._responder is not None:
            self._responder(rpc_id, {"currentTimeAt": int(time.time())}, None)
            with self._lock:
                self._buffers[tid].append(
                    "gateway/action/auto-resolved",
                    {"method": method},
                    pinned=False,
                )
            return

        action_id = f"action-{uuid.uuid4().hex[:16]}"
        action = PendingAction(
            action_id=action_id,
            rpc_id=rpc_id,
            method=method,
            params=payload,
            thread_id=tid,
            created_at=_now(),
            expires_at_monotonic=time.monotonic() + self.action_timeout_seconds,
        )
        timer = threading.Timer(
            self.action_timeout_seconds, self._expire, args=(action_id,)
        )
        timer.daemon = True
        action.timer = timer
        with self._lock:
            self._actions[action_id] = action
            self._buffers[tid].append(
                "gateway/action/required",
                action.public(),
                pinned=True,
            )
        timer.start()

    def pending_actions(self, thread_id: Optional[str] = None) -> list[dict[str, Any]]:
        with self._lock:
            values = list(self._actions.values())
            if thread_id is not None:
                values = [action for action in values if action.thread_id == thread_id]
            return [action.public() for action in values]

    def respond(
        self,
        action_id: str,
        *,
        decision: Optional[str] = None,
        answers: Optional[dict[str, Any]] = None,
        response: Optional[dict[str, Any]] = None,
        execpolicy_amendment: Optional[list[str]] = None,
        network_policy_amendment: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        with self._lock:
            action = self._actions.get(action_id)
        if action is None:
            raise GatewayError(
                "ACTION_NOT_FOUND", f"pending action not found: {action_id}"
            )

        if action.method in APPROVAL_METHODS:
            allowed = {
                "accept",
                "acceptForSession",
                "acceptWithExecpolicyAmendment",
                "applyNetworkPolicyAmendment",
                "decline",
                "cancel",
            }
            if decision not in allowed:
                raise GatewayError(
                    "DECISION_INVALID", f"decision must be one of {sorted(allowed)}"
                )
            wire_decision: Any = decision
            if decision == "acceptWithExecpolicyAmendment":
                if not execpolicy_amendment or not all(
                    isinstance(item, str) and item for item in execpolicy_amendment
                ):
                    raise GatewayError(
                        "AMENDMENT_INVALID",
                        "execpolicy_amendment must be a non-empty string array",
                    )
                wire_decision = {
                    "acceptWithExecpolicyAmendment": {
                        "execpolicy_amendment": execpolicy_amendment
                    }
                }
            elif decision == "applyNetworkPolicyAmendment":
                if not isinstance(network_policy_amendment, dict):
                    raise GatewayError(
                        "AMENDMENT_INVALID",
                        "network_policy_amendment must be an object",
                    )
                wire_decision = {
                    "applyNetworkPolicyAmendment": {
                        "network_policy_amendment": network_policy_amendment
                    }
                }
            result = {"decision": wire_decision}
        elif action.method in USER_INPUT_METHODS:
            if not isinstance(answers, dict):
                raise GatewayError("ANSWERS_INVALID", "answers must be an object")
            result = {"answers": answers}
        elif action.method in PERMISSION_METHODS:
            if not isinstance(response, dict) or not isinstance(
                response.get("permissions"), dict
            ):
                raise GatewayError(
                    "PERMISSIONS_REQUIRED",
                    "response.permissions must contain the granted permission subset",
                )
            scope = response.get("scope")
            if scope not in (None, "turn", "session"):
                raise GatewayError(
                    "PERMISSION_SCOPE_INVALID",
                    "permission scope must be turn or session",
                )
            result = dict(response)
        else:
            if not isinstance(response, dict):
                raise GatewayError(
                    "RESPONSE_REQUIRED",
                    "response object is required for this action type",
                )
            result = response

        # Remove only after the response has been validated. A malformed MCP
        # reply must not destroy the pending action that the caller can repair.
        with self._lock:
            live = self._actions.pop(action_id, None)
        if live is None:
            raise GatewayError(
                "ACTION_NOT_FOUND", f"pending action not found: {action_id}"
            )
        if action.timer is not None:
            action.timer.cancel()
        self._respond(action.rpc_id, result, None)
        self.notification(
            "gateway/action/resolved",
            {
                "threadId": action.thread_id,
                "actionId": action.action_id,
                "method": action.method,
                # Responses can contain OAuth tokens or dynamic-tool payloads.
                # Confirm resolution without echoing secrets into event history.
                "resolved": True,
            },
        )
        return {
            "actionId": action.action_id,
            "threadId": action.thread_id,
            "resolved": True,
        }

    def poll(
        self,
        thread_id: str,
        *,
        cursor: int = 1,
        limit: int = 50,
        include_actions: bool = True,
    ) -> dict[str, Any]:
        with self._lock:
            result = self._buffers[thread_id].poll(
                max(1, int(cursor)), max(1, int(limit))
            )
            if include_actions:
                result["actions"] = [
                    action.public()
                    for action in self._actions.values()
                    if action.thread_id == thread_id
                ]
            return result

    def close(self) -> None:
        with self._lock:
            actions = list(self._actions.values())
            self._actions.clear()
        for action in actions:
            if action.timer is not None:
                action.timer.cancel()
            self._respond(
                action.rpc_id,
                None,
                {"code": -32000, "message": "codex-app-mcp gateway is shutting down"},
            )

    def _expire(self, action_id: str) -> None:
        with self._lock:
            action = self._actions.pop(action_id, None)
        if action is None:
            return
        if action.method in APPROVAL_METHODS:
            self._respond(action.rpc_id, {"decision": "decline"}, None)
        else:
            self._respond(
                action.rpc_id,
                None,
                {"code": -32000, "message": "client action timed out"},
            )
        self.notification(
            "gateway/action/expired",
            {
                "threadId": action.thread_id,
                "actionId": action.action_id,
                "method": action.method,
            },
        )

    def _respond(
        self,
        rpc_id: Any,
        result: Optional[dict[str, Any]],
        error: Optional[dict[str, Any]],
    ) -> None:
        responder = self._responder
        if responder is not None:
            try:
                responder(rpc_id, result, error)
            except Exception:
                # Transport closure during timeout/shutdown is expected.
                pass
