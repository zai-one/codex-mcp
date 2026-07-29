"""High-level governed operations built on the raw app-server client."""

from __future__ import annotations

import base64
import os
import threading
import time
from pathlib import Path
from typing import Any, Mapping, Optional

from .client import AppServerClient, RpcError
from .errors import GatewayError
from .events import EventHub
from .policy import (
    APPROVAL_POLICIES,
    APPROVAL_REVIEWERS,
    GOAL_STATUSES,
    READONLY_RPC_METHODS,
    GatewayPolicy,
    validate_effort,
    validate_limit,
    validate_model,
    validate_params,
    validate_text,
    validate_thread_id,
)
from .protocol import ProtocolRegistry


ADMIN_OPERATIONS: dict[str, tuple[str, bool]] = {
    # Account and quota.
    "account.read": ("account/read", False),
    "account.login_start": ("account/login/start", True),
    "account.login_cancel": ("account/login/cancel", True),
    "account.logout": ("account/logout", True),
    "account.rate_limits": ("account/rateLimits/read", False),
    "account.usage": ("account/usage/read", False),
    "account.workspace_messages": ("account/workspaceMessages/read", False),
    "account.consume_reset_credit": ("account/rateLimitResetCredit/consume", True),
    "account.send_credits_nudge": ("account/sendAddCreditsNudgeEmail", True),
    # Configuration and capability control.
    "config.read": ("config/read", False),
    "config.requirements": ("configRequirements/read", False),
    "config.write_value": ("config/value/write", True),
    "config.batch_write": ("config/batchWrite", True),
    "config.reload_mcp": ("config/mcpServer/reload", True),
    "feature.list": ("experimentalFeature/list", False),
    "feature.set": ("experimentalFeature/enablement/set", True),
    "external_config.detect": ("externalAgentConfig/detect", False),
    "external_config.histories": ("externalAgentConfig/import/readHistories", False),
    "external_config.import": ("externalAgentConfig/import", True),
    # Apps, plugins, skills and marketplaces.
    "app.list": ("app/list", False),
    "app.installed": ("app/installed", False),
    "app.read": ("app/read", False),
    "plugin.list": ("plugin/list", False),
    "plugin.installed": ("plugin/installed", False),
    "plugin.read": ("plugin/read", False),
    "plugin.install": ("plugin/install", True),
    "plugin.uninstall": ("plugin/uninstall", True),
    "plugin.skill_read": ("plugin/skill/read", False),
    "plugin.share_list": ("plugin/share/list", False),
    "plugin.share_checkout": ("plugin/share/checkout", True),
    "plugin.share_save": ("plugin/share/save", True),
    "plugin.share_update_targets": ("plugin/share/updateTargets", True),
    "plugin.share_delete": ("plugin/share/delete", True),
    "skill.list": ("skills/list", False),
    "skill.write_config": ("skills/config/write", True),
    "skill.set_extra_roots": ("skills/extraRoots/set", True),
    "hook.list": ("hooks/list", False),
    "marketplace.add": ("marketplace/add", True),
    "marketplace.remove": ("marketplace/remove", True),
    "marketplace.upgrade": ("marketplace/upgrade", True),
    "mcp.status": ("mcpServerStatus/list", False),
    "mcp.oauth_login": ("mcpServer/oauth/login", True),
    "mcp.resource_read": ("mcpServer/resource/read", False),
    # Runtime environment, files, search and memory.
    "environment.info": ("environment/info", False),
    "environment.status": ("environment/status", False),
    "environment.add": ("environment/add", True),
    "model.providers": ("modelProvider/capabilities/read", False),
    "permission_profiles.list": ("permissionProfile/list", False),
    "windows.readiness": ("windowsSandbox/readiness", False),
    "windows.setup": ("windowsSandbox/setupStart", True),
    "search.files": ("fuzzyFileSearch", False),
    "search.session_start": ("fuzzyFileSearch/sessionStart", False),
    "search.session_update": ("fuzzyFileSearch/sessionUpdate", False),
    "search.session_stop": ("fuzzyFileSearch/sessionStop", False),
    "thread.search": ("thread/search", False),
    "thread.search_occurrences": ("thread/searchOccurrences", False),
    "thread.settings_update": ("thread/settings/update", True),
    "thread.memory_mode": ("thread/memoryMode/set", True),
    "thread.guardian_approve_denied": ("thread/approveGuardianDeniedAction", True),
    "thread.elicitation_increment": ("thread/increment_elicitation", True),
    "thread.elicitation_decrement": ("thread/decrement_elicitation", True),
    "memory.reset": ("memory/reset", True),
    # Realtime and remote control.
    "realtime.start": ("thread/realtime/start", True),
    "realtime.append_audio": ("thread/realtime/appendAudio", True),
    "realtime.append_speech": ("thread/realtime/appendSpeech", True),
    "realtime.append_text": ("thread/realtime/appendText", True),
    "realtime.list_voices": ("thread/realtime/listVoices", False),
    "realtime.stop": ("thread/realtime/stop", True),
    "remote.status": ("remoteControl/status/read", False),
    "remote.enable": ("remoteControl/enable", True),
    "remote.disable": ("remoteControl/disable", True),
    "remote.pair_start": ("remoteControl/pairing/start", True),
    "remote.pair_status": ("remoteControl/pairing/status", False),
    "remote.clients": ("remoteControl/client/list", False),
    "remote.revoke_client": ("remoteControl/client/revoke", True),
    "feedback.upload": ("feedback/upload", True),
}


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return max(1.0, value)


def _env_enabled(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "off", "no"}


def _extract_thread_id(result: Any) -> str:
    if isinstance(result, Mapping):
        direct = result.get("threadId")
        if isinstance(direct, str) and direct:
            return direct
        thread = result.get("thread")
        if isinstance(thread, Mapping):
            value = thread.get("id")
            if isinstance(value, str) and value:
                return value
    raise GatewayError(
        "PROTOCOL_INVALID", "app-server response did not contain a thread id"
    )


def _extract_turn_id(result: Any) -> str:
    if isinstance(result, Mapping):
        direct = result.get("turnId")
        if isinstance(direct, str) and direct:
            return direct
        turn = result.get("turn")
        if isinstance(turn, Mapping):
            value = turn.get("id")
            if isinstance(value, str) and value:
                return value
    raise GatewayError(
        "PROTOCOL_INVALID", "app-server response did not contain a turn id"
    )


class CodexAppGateway:
    """MCP-facing application service with policy and protocol adaptation."""

    def __init__(
        self,
        *,
        policy: Optional[GatewayPolicy] = None,
        client: Optional[AppServerClient] = None,
        action_timeout_seconds: Optional[float] = None,
        state_path: Optional[str | Path] = None,
    ) -> None:
        self.policy = policy or GatewayPolicy.from_env()
        self.operation_timeout = _env_float(
            "CODEX_APP_MCP_OPERATION_TIMEOUT_SECONDS", 180.0
        )
        self.events = EventHub(
            action_timeout_seconds=action_timeout_seconds
            or _env_float("CODEX_APP_MCP_ACTION_TIMEOUT_SECONDS", 60.0)
        )
        if client is None:
            config_overrides = tuple(
                item
                for item in os.environ.get("CODEX_APP_MCP_CONFIG_OVERRIDES", "").split(
                    ";"
                )
                if item.strip()
            )
            client = AppServerClient(
                codex_bin=os.environ.get("CODEX_APP_MCP_BIN", "codex"),
                config_overrides=config_overrides,
                experimental_api=True,
                request_timeout=_env_float(
                    "CODEX_APP_MCP_REQUEST_TIMEOUT_SECONDS", 30.0
                ),
                notification_handler=self.events.notification,
                server_request_handler=self.events.server_request,
            )
        else:
            client.notification_handler = self.events.notification
            client.server_request_handler = self.events.server_request
        self.client = client
        self.events.bind_responder(self.client.respond)
        self._models_lock = threading.Lock()
        self._models_at = 0.0
        self._models: dict[str, dict[str, Any]] = {}
        self.default_sandbox = os.environ.get("CODEX_APP_MCP_DEFAULT_SANDBOX")
        if self.default_sandbox:
            self.default_sandbox = self.policy.validate_sandbox(self.default_sandbox)
        self.default_approval_policy = os.environ.get(
            "CODEX_APP_MCP_DEFAULT_APPROVAL_POLICY"
        )
        if self.default_approval_policy:
            self.default_approval_policy = self._approval_policy(
                self.default_approval_policy
            )
        self._state_path = state_path
        self._jobs_lock = threading.Lock()
        self._jobs: Any = None
        self._scheduler_lock = threading.Lock()
        self._scheduler: Any = None
        self._protocol_lock = threading.Lock()
        self._protocol: Optional[ProtocolRegistry] = None
        self._metrics_lock = threading.Lock()
        self._metrics: dict[str, Any] = {
            "startedAt": time.time(),
            "toolCalls": 0,
            "toolErrors": 0,
            "rpcRestarts": 0,
            "lastTool": None,
            "lastError": None,
        }
        self._close_lock = threading.Lock()
        self._closed = False

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
            if self._scheduler is not None:
                self._scheduler.begin_close()
            if self._jobs is not None:
                self._jobs.begin_close()
            self.events.close()
            self.client.close()
            if self._scheduler is not None:
                self._scheduler.end_close()
            if self._jobs is not None:
                self._jobs.end_close()

    def job(self, args: Mapping[str, Any]) -> dict[str, Any]:
        return self._job_manager().handle(args)

    def start_background(self) -> None:
        """Start durable service loops without starting the app-server child."""
        if _env_enabled("CODEX_APP_MCP_SCHEDULER_ENABLED"):
            self._scheduler_manager()

    def schedule(self, args: Mapping[str, Any]) -> dict[str, Any]:
        return self._scheduler_manager().handle(args)

    def protocol(self, args: Mapping[str, Any]) -> dict[str, Any]:
        return self._protocol_registry().handle(args)

    def admin(self, args: Mapping[str, Any]) -> dict[str, Any]:
        operation = validate_text(args.get("operation"), "operation")
        entry = ADMIN_OPERATIONS.get(operation)
        if entry is None:
            raise GatewayError(
                "ADMIN_OPERATION_INVALID", f"unknown admin operation: {operation}"
            )
        method, mutating = entry
        if mutating:
            self._require_unsafe_rpc(method)
        if bool(
            args.get("requireSupported", True)
        ) and not self._protocol_registry().supports(method):
            raise GatewayError(
                "APP_SERVER_METHOD_UNSUPPORTED",
                f"configured app-server does not expose {method}",
                method=method,
            )
        params = validate_params(args.get("params"))
        params = self._govern_admin_params(params)
        timeout = self._timeout_arg(args, default=self.operation_timeout)
        return {
            "ok": True,
            "operation": operation,
            "method": method,
            "result": self.client.request(method, params, timeout=timeout),
        }

    def runtime(self, args: Mapping[str, Any]) -> dict[str, Any]:
        action = str(args.get("action") or "metrics")
        if action == "metrics":
            with self._metrics_lock:
                metrics = dict(self._metrics)
            metrics["uptimeSeconds"] = max(0.0, time.time() - metrics["startedAt"])
            metrics["running"] = self.client.running
            metrics["pid"] = self.client.pid
            metrics["scheduler"] = (
                self._scheduler.metrics()
                if self._scheduler is not None
                else {"running": False}
            )
            return {"ok": True, "metrics": metrics}
        if action == "restart":
            self._require_unsafe_rpc("runtime/restart")
            self.client.close()
            metadata = self.client.start()
            with self._metrics_lock:
                self._metrics["rpcRestarts"] += 1
            return {
                "ok": True,
                "running": self.client.running,
                "pid": self.client.pid,
                "metadata": metadata,
            }
        raise GatewayError(
            "RUNTIME_ACTION_INVALID", f"unknown runtime action: {action}"
        )

    def record_tool_call(
        self, name: str, *, ok: bool, error_code: Optional[str] = None
    ) -> None:
        with self._metrics_lock:
            self._metrics["toolCalls"] += 1
            self._metrics["lastTool"] = name
            if not ok:
                self._metrics["toolErrors"] += 1
                self._metrics["lastError"] = error_code

    def _job_manager(self) -> Any:
        with self._jobs_lock:
            if self._jobs is None:
                from .jobs import JobManager
                from .state import StateStore, default_state_path

                path = (
                    self._state_path
                    if self._state_path is not None
                    else default_state_path()
                )
                self._jobs = JobManager(self, StateStore(path))
            return self._jobs

    def _scheduler_manager(self) -> Any:
        with self._scheduler_lock:
            if self._scheduler is None:
                from .scheduler import SchedulerManager
                from .state import StateStore, default_state_path

                path = (
                    self._state_path
                    if self._state_path is not None
                    else default_state_path()
                )
                self._scheduler = SchedulerManager(self, StateStore(path))
            return self._scheduler

    def _protocol_registry(self) -> ProtocolRegistry:
        with self._protocol_lock:
            if self._protocol is None:
                self._protocol = ProtocolRegistry(
                    os.environ.get("CODEX_APP_MCP_BIN", "codex"),
                    ttl_seconds=_env_float(
                        "CODEX_APP_MCP_PROTOCOL_CACHE_SECONDS", 300.0
                    ),
                )
            return self._protocol

    def status(self, *, include_stderr: bool = False) -> dict[str, Any]:
        metadata = self.client.start()
        result: dict[str, Any] = {
            "ok": True,
            "running": self.client.running,
            "pid": self.client.pid,
            "metadata": metadata,
            "policy": {
                "allowedRootCount": len(self.policy.allowed_roots),
                "fullAccessEnabled": self.policy.allow_full_access,
                "allowedMcpServerCount": len(self.policy.allowed_mcp_servers),
                "allowedMcpToolCount": len(self.policy.allowed_mcp_tools),
                "allowedThreadConfigKeyCount": len(
                    self.policy.allowed_thread_config_keys
                ),
                "readonlyRpcEnabled": self.policy.allow_readonly_rpc,
                "unsafeRpcEnabled": self.policy.allow_unsafe_rpc,
                "allowedRpcMethodCount": len(self.policy.allowed_rpc_methods),
                "defaultSandbox": self.default_sandbox,
                "defaultApprovalPolicy": self.default_approval_policy,
            },
            "audit": {
                "enabled": os.environ.get("CODEX_APP_MCP_AUDIT", "1").strip().lower()
                not in {"0", "false", "off", "no"},
                "destination": (
                    "file" if os.environ.get("CODEX_APP_MCP_AUDIT_PATH") else "stderr"
                ),
            },
            "protocolCatalog": {
                "dynamic": True,
                "experimental": True,
            },
            "scheduler": {
                "enabled": _env_enabled("CODEX_APP_MCP_SCHEDULER_ENABLED"),
                "running": self._scheduler is not None,
            },
        }
        if include_stderr:
            result["stderrTail"] = self.client.stderr_tail[-50:]
        return result

    def doctor(self) -> dict[str, Any]:
        """Run non-mutating app-server readiness checks.

        This replaces the old ``codex doctor --json`` subprocess path. Each
        check is independently reported so an older app-server can expose an
        unsupported diagnostic without hiding the healthy connection.
        """
        status = self.status(include_stderr=True)
        checks: dict[str, Any] = {
            "appServer": {
                "ok": bool(status.get("running")),
                "pid": status.get("pid"),
                "metadata": status.get("metadata"),
            }
        }
        for name, method in (
            ("account", "account/read"),
            ("configRequirements", "configRequirements/read"),
            ("windowsSandboxReadiness", "windowsSandbox/readiness"),
        ):
            try:
                checks[name] = {
                    "ok": True,
                    "result": self.client.request(method, {}),
                }
            except Exception as exc:
                checks[name] = {
                    "ok": False,
                    "error": type(exc).__name__,
                    "message": str(exc)[:1_000],
                }
        required_ok = checks["appServer"]["ok"] and checks["account"]["ok"]
        return {
            "ok": bool(required_ok),
            "checks": checks,
            "policy": status["policy"],
            "stderrTail": status.get("stderrTail", []),
        }

    def discover(self, args: Mapping[str, Any]) -> dict[str, Any]:
        action = str(args.get("action") or "")
        limit = validate_limit(args.get("limit"), default=100)
        if action == "models":
            return {
                "ok": True,
                "result": self._model_list(force=bool(args.get("force"))),
            }
        method_map = {
            "collaboration_modes": "collaborationMode/list",
            "features": "experimentalFeature/list",
            "permission_profiles": "permissionProfile/list",
            "mcp_servers": "mcpServerStatus/list",
            "apps": "app/list",
            "plugins": "plugin/list",
            "skills": "skills/list",
            "hooks": "hooks/list",
            "account": "account/read",
            "config_requirements": "configRequirements/read",
        }
        method = method_map.get(action)
        if method is None:
            raise GatewayError(
                "DISCOVER_ACTION_INVALID",
                f"unknown discover action: {action}",
            )
        params: dict[str, Any] = {}
        if action not in {"account", "config_requirements"}:
            params["limit"] = limit
        if action in {"skills", "hooks"}:
            raw_cwds = args.get("cwds")
            if not isinstance(raw_cwds, list) or not raw_cwds:
                raise GatewayError("CWDS_REQUIRED", "cwds must be a non-empty array")
            params["cwds"] = [self.policy.validate_cwd(value) for value in raw_cwds]
            params.pop("limit", None)
        if action == "mcp_servers":
            params["detail"] = str(args.get("detail") or "toolsAndAuthOnly")
        if args.get("cursor") is not None:
            params["cursor"] = args.get("cursor")
        return {"ok": True, "result": self.client.request(method, params)}

    def thread(self, args: Mapping[str, Any]) -> dict[str, Any]:
        action = str(args.get("action") or "")
        if action == "start":
            result = self._thread_start(args)
            return {
                "ok": True,
                "threadId": _extract_thread_id(result),
                "result": result,
            }
        if action == "list":
            params = {
                "limit": validate_limit(args.get("limit"), default=50),
                "archived": bool(args.get("archived", False)),
            }
            for key in ("cursor", "searchTerm", "sortKey", "sortDirection"):
                if args.get(key) is not None:
                    params[key] = args.get(key)
            if args.get("cwd") is not None:
                params["cwd"] = self.policy.validate_cwd(args.get("cwd"))
            return {"ok": True, "result": self.client.request("thread/list", params)}
        if action == "loaded":
            return {
                "ok": True,
                "result": self.client.request("thread/loaded/list", {}),
            }

        thread_id = validate_thread_id(args.get("threadId"))
        if action == "read":
            return {
                "ok": True,
                "threadId": thread_id,
                "result": self.client.request(
                    "thread/read",
                    {
                        "threadId": thread_id,
                        "includeTurns": bool(args.get("includeTurns")),
                    },
                ),
            }
        if action in {"turns", "items"}:
            params = {
                "threadId": thread_id,
                "limit": validate_limit(args.get("limit"), default=50),
            }
            for key in ("cursor", "sortDirection", "itemsView", "turnId"):
                if args.get(key) is not None:
                    params[key] = args.get(key)
            return {
                "ok": True,
                "threadId": thread_id,
                "result": self.client.request(f"thread/{action}/list", params),
            }
        if action in {"resume", "fork"}:
            params = self._thread_overrides(args, require_cwd=False)
            params["threadId"] = thread_id
            method = f"thread/{action}"
            result = self.client.request(method, params, timeout=self.operation_timeout)
            return {
                "ok": True,
                "threadId": _extract_thread_id(result),
                "result": result,
            }
        if action == "archive":
            result = self.client.request("thread/archive", {"threadId": thread_id})
        elif action == "unarchive":
            last_error: Optional[BaseException] = None
            result = None
            deadline = time.monotonic() + min(20.0, self.operation_timeout)
            attempt = 0
            while True:
                try:
                    result = self.client.request(
                        "thread/unarchive", {"threadId": thread_id}
                    )
                    last_error = None
                    break
                except RpcError as exc:
                    last_error = exc
                    moved_but_unread = (
                        exc.code == -32603
                        and "failed to read unarchived thread" in exc.message.casefold()
                    )
                    if moved_but_unread:
                        try:
                            readback = self.client.request(
                                "thread/read",
                                {"threadId": thread_id, "includeTurns": False},
                            )
                        except Exception:
                            pass
                        else:
                            result = {
                                "recovered": True,
                                "readback": readback,
                            }
                            last_error = None
                            break
                    archive_pending = (
                        exc.code == -32600
                        and "no archived rollout found" in exc.message.casefold()
                    )
                    if not archive_pending or time.monotonic() >= deadline:
                        break
                    delay = min(2.0, 0.25 * (2**attempt))
                    attempt += 1
                    time.sleep(delay)
            if last_error is not None:
                raise last_error
        elif action == "unsubscribe":
            result = self.client.request("thread/unsubscribe", {"threadId": thread_id})
        elif action == "name":
            name = validate_text(args.get("name"), "name")
            result = self.client.request(
                "thread/name/set",
                {"threadId": thread_id, "name": name},
            )
        elif action == "compact":
            result = self.client.request(
                "thread/compact/start", {"threadId": thread_id}
            )
        elif action == "delete":
            self._require_unsafe_rpc("thread/delete")
            result = self.client.request("thread/delete", {"threadId": thread_id})
        elif action == "shell":
            self._require_full_access("thread/shellCommand")
            command = validate_text(args.get("command"), "command")
            result = self.client.request(
                "thread/shellCommand",
                {"threadId": thread_id, "command": command},
                timeout=self.operation_timeout,
            )
        elif action == "metadata":
            git_info = args.get("gitInfo")
            if not isinstance(git_info, Mapping):
                raise GatewayError("GIT_INFO_INVALID", "gitInfo must be an object")
            result = self.client.request(
                "thread/metadata/update",
                {"threadId": thread_id, "gitInfo": dict(git_info)},
            )
        elif action == "rollback":
            self._require_unsafe_rpc("thread/rollback")
            num_turns = args.get("numTurns")
            if (
                isinstance(num_turns, bool)
                or not isinstance(num_turns, int)
                or num_turns < 1
            ):
                raise GatewayError(
                    "NUM_TURNS_INVALID", "numTurns must be a positive integer"
                )
            result = self.client.request(
                "thread/rollback",
                {"threadId": thread_id, "numTurns": num_turns},
            )
        elif action == "inject":
            self._require_unsafe_rpc("thread/inject_items")
            items = args.get("items")
            if not isinstance(items, list) or not items:
                raise GatewayError("ITEMS_INVALID", "items must be a non-empty array")
            result = self.client.request(
                "thread/inject_items",
                {"threadId": thread_id, "items": items},
            )
        elif action in {"terminals_list", "terminals_clean", "terminal_terminate"}:
            suffix = {
                "terminals_list": "list",
                "terminals_clean": "clean",
                "terminal_terminate": "terminate",
            }[action]
            params = {"threadId": thread_id}
            if action == "terminals_list":
                params["limit"] = validate_limit(args.get("limit"), default=50)
                if args.get("cursor") is not None:
                    params["cursor"] = args.get("cursor")
            if action == "terminal_terminate":
                params["processId"] = validate_text(args.get("processId"), "processId")
            result = self.client.request(
                f"thread/backgroundTerminals/{suffix}",
                params,
            )
        else:
            raise GatewayError(
                "THREAD_ACTION_INVALID", f"unknown thread action: {action}"
            )
        return {"ok": True, "threadId": thread_id, "result": result}

    def goal(self, args: Mapping[str, Any]) -> dict[str, Any]:
        action = str(args.get("action") or "")
        thread_id_value = args.get("threadId")
        if action == "start" and thread_id_value is None:
            started = self._thread_start(args)
            thread_id = _extract_thread_id(started)
        else:
            thread_id = validate_thread_id(thread_id_value)
            if action == "start":
                read = self.client.request(
                    "thread/read",
                    {"threadId": thread_id, "includeTurns": False},
                    timeout=self.operation_timeout,
                )
                thread = read.get("thread") if isinstance(read, Mapping) else None
                status = thread.get("status") if isinstance(thread, Mapping) else None
                status_type = (
                    status.get("type") if isinstance(status, Mapping) else None
                )
                if status_type == "notLoaded":
                    self.client.request(
                        "thread/resume",
                        {"threadId": thread_id},
                        timeout=self.operation_timeout,
                    )

        if action == "get":
            result = self.client.request("thread/goal/get", {"threadId": thread_id})
        elif action == "clear":
            result = self.client.request("thread/goal/clear", {"threadId": thread_id})
        elif action in {"start", "set", "pause", "resume", "complete"}:
            params: dict[str, Any] = {"threadId": thread_id}
            if action == "start":
                objective = validate_text(args.get("objective"), "objective")
                self.client.request("thread/goal/clear", {"threadId": thread_id})
                params.update({"objective": objective, "status": "active"})
            elif action == "set":
                objective = validate_text(
                    args.get("objective"), "objective", required=False
                )
                status = args.get("status")
                if (
                    objective is None
                    and status is None
                    and args.get("tokenBudget") is None
                ):
                    raise GatewayError(
                        "GOAL_UPDATE_EMPTY",
                        "set requires objective, status, or tokenBudget",
                    )
                if objective is not None:
                    params["objective"] = objective
                if status is not None:
                    if status not in GOAL_STATUSES:
                        raise GatewayError("GOAL_STATUS_INVALID", "invalid goal status")
                    params["status"] = status
            else:
                params["status"] = {
                    "pause": "paused",
                    "resume": "active",
                    "complete": "complete",
                }[action]
            if args.get("tokenBudget") is not None:
                budget = args.get("tokenBudget")
                if isinstance(budget, bool):
                    raise GatewayError(
                        "TOKEN_BUDGET_INVALID", "tokenBudget must be an integer"
                    )
                try:
                    budget_i = int(budget)
                except (TypeError, ValueError) as exc:
                    raise GatewayError(
                        "TOKEN_BUDGET_INVALID", "tokenBudget must be an integer"
                    ) from exc
                if budget_i <= 0:
                    raise GatewayError(
                        "TOKEN_BUDGET_INVALID", "tokenBudget must be greater than zero"
                    )
                params["tokenBudget"] = budget_i
            result = self.client.request(
                "thread/goal/set", params, timeout=self.operation_timeout
            )
        else:
            raise GatewayError("GOAL_ACTION_INVALID", f"unknown goal action: {action}")
        return {"ok": True, "threadId": thread_id, "result": result}

    def turn(self, args: Mapping[str, Any]) -> dict[str, Any]:
        action = str(args.get("action") or "")
        thread_id = validate_thread_id(args.get("threadId"))
        if action == "start":
            prompt = validate_text(args.get("prompt"), "prompt")
            model = validate_model(args.get("model"))
            effort = validate_effort(args.get("effort"))
            self._validate_model_effort(model, effort)
            inputs: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
            raw_images = args.get("images")
            if raw_images is not None:
                if not isinstance(raw_images, list):
                    raise GatewayError("IMAGES_INVALID", "images must be an array")
                for value in raw_images:
                    image_path = Path(
                        self.policy.validate_cwd(str(Path(str(value)).parent))
                    )
                    full_path = (image_path / Path(str(value)).name).resolve()
                    if not full_path.exists() or not full_path.is_file():
                        raise GatewayError(
                            "IMAGE_NOT_FOUND", f"image not found: {value}"
                        )
                    inputs.append({"type": "localImage", "path": str(full_path)})
            params: dict[str, Any] = {"threadId": thread_id, "input": inputs}
            if model is not None:
                params["model"] = model
            if effort is not None:
                params["effort"] = effort
            if args.get("approvalPolicy") is not None:
                params["approvalPolicy"] = self._approval_policy(
                    args.get("approvalPolicy")
                )
            elif self.default_approval_policy is not None:
                params["approvalPolicy"] = self.default_approval_policy
            if args.get("approvalReviewer") is not None:
                params["approvalsReviewer"] = self._approval_reviewer(
                    args.get("approvalReviewer")
                )
            if args.get("sandbox") is not None:
                sandbox = self.policy.validate_sandbox(args.get("sandbox"))
                params["sandboxPolicy"] = {
                    "type": {
                        "read-only": "readOnly",
                        "workspace-write": "workspaceWrite",
                        "danger-full-access": "dangerFullAccess",
                    }[sandbox]
                }
            elif self.default_sandbox is not None:
                params["sandboxPolicy"] = {
                    "type": {
                        "read-only": "readOnly",
                        "workspace-write": "workspaceWrite",
                        "danger-full-access": "dangerFullAccess",
                    }[self.default_sandbox]
                }
            if args.get("cwd") is not None:
                params["cwd"] = self.policy.validate_cwd(args.get("cwd"))
            if args.get("outputSchema") is not None:
                params["outputSchema"] = validate_params(
                    args.get("outputSchema"), "outputSchema"
                )
            mode = args.get("mode")
            if mode == "plan":
                params["collaborationMode"] = {
                    "mode": "plan",
                    "settings": {
                        "model": model,
                        "reasoning_effort": effort or "medium",
                        "developer_instructions": None,
                    },
                }
            elif mode not in (None, "default"):
                raise GatewayError("MODE_INVALID", "mode must be default or plan")
            result = self.client.request(
                "turn/start", params, timeout=self.operation_timeout
            )
            return {
                "ok": True,
                "threadId": thread_id,
                "turnId": _extract_turn_id(result),
                "result": result,
            }
        if action == "steer":
            prompt = validate_text(args.get("prompt"), "prompt")
            turn_id = validate_thread_id(args.get("turnId"))
            params = {
                "threadId": thread_id,
                "expectedTurnId": turn_id,
                "input": [{"type": "text", "text": prompt}],
            }
            result = self.client.request("turn/steer", params)
        elif action == "interrupt":
            turn_id = validate_thread_id(args.get("turnId"))
            result = self.client.request(
                "turn/interrupt",
                {"threadId": thread_id, "turnId": turn_id},
            )
        else:
            raise GatewayError("TURN_ACTION_INVALID", f"unknown turn action: {action}")
        return {"ok": True, "threadId": thread_id, "result": result}

    def event(self, args: Mapping[str, Any]) -> dict[str, Any]:
        action = str(args.get("action") or "")
        if action == "pending":
            thread_id = args.get("threadId")
            if thread_id is not None:
                thread_id = validate_thread_id(thread_id)
            return {"ok": True, "actions": self.events.pending_actions(thread_id)}
        if action == "poll":
            raw_thread_id = args.get("threadId")
            thread_id = (
                validate_thread_id(raw_thread_id)
                if raw_thread_id is not None
                else "_global"
            )
            cursor = int(args.get("cursor") or 1)
            limit = validate_limit(args.get("limit"), default=50)
            return {
                "ok": True,
                "threadId": thread_id,
                **self.events.poll(
                    thread_id,
                    cursor=cursor,
                    limit=limit,
                    include_actions=bool(args.get("includeActions", True)),
                ),
            }
        if action == "respond":
            action_id = validate_text(args.get("actionId"), "actionId")
            return {
                "ok": True,
                **self.events.respond(
                    action_id,
                    decision=args.get("decision"),
                    answers=args.get("answers"),
                    response=args.get("response"),
                    execpolicy_amendment=args.get("execpolicyAmendment"),
                    network_policy_amendment=args.get("networkPolicyAmendment"),
                ),
            }
        raise GatewayError("EVENT_ACTION_INVALID", f"unknown event action: {action}")

    def mcp_call(self, args: Mapping[str, Any]) -> dict[str, Any]:
        server, tool = self.policy.validate_nested_mcp(
            args.get("server"), args.get("tool")
        )
        thread_id = args.get("threadId")
        params: dict[str, Any] = {
            "server": server,
            "tool": tool,
            "arguments": validate_params(args.get("arguments"), "arguments"),
        }
        if thread_id is not None:
            params["threadId"] = validate_thread_id(thread_id)
        if args.get("_meta") is not None:
            params["_meta"] = validate_params(args.get("_meta"), "_meta")
        return {
            "ok": True,
            "result": self.client.request("mcpServer/tool/call", params),
        }

    def review(self, args: Mapping[str, Any]) -> dict[str, Any]:
        thread_id_value = args.get("threadId")
        if thread_id_value is None:
            started = self._thread_start(args)
            thread_id = _extract_thread_id(started)
        else:
            thread_id = validate_thread_id(thread_id_value)
        raw_target = args.get("target")
        if raw_target is not None:
            if not isinstance(raw_target, Mapping):
                raise GatewayError("REVIEW_TARGET_INVALID", "target must be an object")
            target = dict(raw_target)
        elif args.get("instructions") is not None:
            target = {
                "type": "custom",
                "instructions": validate_text(args.get("instructions"), "instructions"),
            }
        elif args.get("commit") is not None:
            target = {
                "type": "commit",
                "sha": validate_text(args.get("commit"), "commit"),
            }
            if args.get("title") is not None:
                target["title"] = validate_text(args.get("title"), "title")
        elif args.get("baseBranch") is not None:
            target = {
                "type": "baseBranch",
                "branch": validate_text(args.get("baseBranch"), "baseBranch"),
            }
        else:
            target = {"type": "uncommittedChanges"}
        if target.get("type") not in {
            "uncommittedChanges",
            "baseBranch",
            "commit",
            "custom",
        }:
            raise GatewayError("REVIEW_TARGET_INVALID", "unknown review target type")
        delivery = str(args.get("delivery") or "inline")
        if delivery not in {"inline", "detached"}:
            raise GatewayError(
                "REVIEW_DELIVERY_INVALID", "delivery must be inline or detached"
            )
        result = self.client.request(
            "review/start",
            {"threadId": thread_id, "delivery": delivery, "target": target},
            timeout=self.operation_timeout,
        )
        turn_id = _extract_turn_id(result)
        review_thread_id = (
            result.get("reviewThreadId") if isinstance(result, Mapping) else None
        ) or thread_id
        response: dict[str, Any] = {
            "ok": True,
            "threadId": thread_id,
            "reviewThreadId": review_thread_id,
            "turnId": turn_id,
            "result": result,
        }
        if bool(args.get("wait")):
            response["completion"] = self._wait_for_turn(
                str(review_thread_id),
                turn_id,
                timeout_seconds=self._timeout_arg(args, default=900.0),
            )
        return response

    def command(self, args: Mapping[str, Any]) -> dict[str, Any]:
        action = str(args.get("action") or "")
        if action == "exec":
            command = args.get("command")
            if (
                not isinstance(command, list)
                or not command
                or not all(isinstance(item, str) and item for item in command)
            ):
                raise GatewayError(
                    "COMMAND_INVALID", "command must be a non-empty argv array"
                )
            params: dict[str, Any] = {"command": command}
            if args.get("cwd") is not None:
                params["cwd"] = self.policy.validate_cwd(args.get("cwd"))
            sandbox = args.get("sandbox") or self.default_sandbox
            if sandbox is not None:
                value = self.policy.validate_sandbox(sandbox)
                params["sandboxPolicy"] = {
                    "type": {
                        "read-only": "readOnly",
                        "workspace-write": "workspaceWrite",
                        "danger-full-access": "dangerFullAccess",
                    }[value]
                }
            for key in (
                "env",
                "timeoutMs",
                "disableTimeout",
                "outputBytesCap",
                "disableOutputCap",
                "tty",
                "processId",
                "streamStdin",
                "streamStdoutStderr",
                "size",
            ):
                if args.get(key) is not None:
                    params[key] = args.get(key)
            return {
                "ok": True,
                "result": self.client.request(
                    "command/exec",
                    params,
                    timeout=self._timeout_arg(args, default=900.0),
                ),
            }
        method_map = {
            "write": "command/exec/write",
            "resize": "command/exec/resize",
            "terminate": "command/exec/terminate",
        }
        method = method_map.get(action)
        if method is None:
            raise GatewayError(
                "COMMAND_ACTION_INVALID", f"unknown command action: {action}"
            )
        process_id = validate_text(args.get("processId"), "processId")
        params = {"processId": process_id}
        if action == "write":
            if args.get("deltaBase64") is not None:
                self._validate_base64(args.get("deltaBase64"), "deltaBase64")
                params["deltaBase64"] = args.get("deltaBase64")
            if args.get("closeStdin") is not None:
                params["closeStdin"] = bool(args.get("closeStdin"))
            if len(params) == 1:
                raise GatewayError(
                    "COMMAND_INPUT_EMPTY", "write requires deltaBase64 or closeStdin"
                )
        elif action == "resize":
            params["size"] = self._terminal_size(args.get("size"))
        return {"ok": True, "result": self.client.request(method, params)}

    def process(self, args: Mapping[str, Any]) -> dict[str, Any]:
        self._require_full_access("process/*")
        action = str(args.get("action") or "")
        method_map = {
            "spawn": "process/spawn",
            "write": "process/writeStdin",
            "resize": "process/resizePty",
            "kill": "process/kill",
        }
        method = method_map.get(action)
        if method is None:
            raise GatewayError(
                "PROCESS_ACTION_INVALID", f"unknown process action: {action}"
            )
        process_handle = validate_text(args.get("processHandle"), "processHandle")
        params: dict[str, Any] = {"processHandle": process_handle}
        if action == "spawn":
            command = args.get("command")
            if (
                not isinstance(command, list)
                or not command
                or not all(isinstance(item, str) and item for item in command)
            ):
                raise GatewayError(
                    "COMMAND_INVALID", "command must be a non-empty argv array"
                )
            params.update(
                {
                    "command": command,
                    "cwd": self.policy.validate_cwd(args.get("cwd")),
                }
            )
            for key in (
                "env",
                "tty",
                "size",
                "streamStdin",
                "streamStdoutStderr",
                "outputBytesCap",
                "timeoutMs",
            ):
                if args.get(key) is not None:
                    params[key] = args.get(key)
        elif action == "write":
            if args.get("deltaBase64") is not None:
                self._validate_base64(args.get("deltaBase64"), "deltaBase64")
                params["deltaBase64"] = args.get("deltaBase64")
            if args.get("closeStdin") is not None:
                params["closeStdin"] = bool(args.get("closeStdin"))
            if len(params) == 1:
                raise GatewayError(
                    "PROCESS_INPUT_EMPTY", "write requires deltaBase64 or closeStdin"
                )
        elif action == "resize":
            params["size"] = self._terminal_size(args.get("size"))
        return {
            "ok": True,
            "processHandle": process_handle,
            "result": self.client.request(method, params),
        }

    def filesystem(self, args: Mapping[str, Any]) -> dict[str, Any]:
        action = str(args.get("action") or "")
        method_map = {
            "read": "fs/readFile",
            "write": "fs/writeFile",
            "mkdir": "fs/createDirectory",
            "metadata": "fs/getMetadata",
            "list": "fs/readDirectory",
            "remove": "fs/remove",
            "copy": "fs/copy",
            "watch": "fs/watch",
            "unwatch": "fs/unwatch",
        }
        method = method_map.get(action)
        if method is None:
            raise GatewayError(
                "FS_ACTION_INVALID", f"unknown filesystem action: {action}"
            )
        if action == "unwatch":
            params = {"watchId": validate_text(args.get("watchId"), "watchId")}
        elif action == "copy":
            if not self.policy.allow_unsafe_rpc:
                self._require_unsafe_rpc(method)
            params = {
                "sourcePath": self.policy.validate_path(
                    args.get("sourcePath"), field="sourcePath"
                ),
                "destinationPath": self.policy.validate_path(
                    args.get("destinationPath"),
                    field="destinationPath",
                    must_exist=False,
                ),
                "recursive": bool(args.get("recursive", False)),
            }
        else:
            mutating = action in {"write", "mkdir", "remove"}
            if mutating:
                self._require_unsafe_rpc(method)
            must_exist = action not in {"write", "mkdir"}
            path = self.policy.validate_path(
                args.get("path"),
                must_exist=must_exist,
                field="path",
            )
            params = {"path": path}
            if action == "write":
                if args.get("text") is not None:
                    text = validate_text(args.get("text"), "text", required=False)
                    params["dataBase64"] = base64.b64encode(
                        (text or "").encode("utf-8")
                    ).decode("ascii")
                else:
                    params["dataBase64"] = self._validate_base64(
                        args.get("dataBase64"), "dataBase64"
                    )
            elif action in {"mkdir", "remove"}:
                params["recursive"] = bool(args.get("recursive", True))
                if action == "remove":
                    params["force"] = bool(args.get("force", True))
            elif action == "watch":
                params["watchId"] = validate_text(args.get("watchId"), "watchId")
        result = self.client.request(method, params)
        response: dict[str, Any] = {"ok": True, "result": result}
        if (
            action == "read"
            and bool(args.get("decodeText"))
            and isinstance(result, Mapping)
        ):
            encoded = result.get("dataBase64")
            if isinstance(encoded, str):
                try:
                    response["text"] = base64.b64decode(encoded, validate=True).decode(
                        "utf-8"
                    )
                except (ValueError, UnicodeDecodeError) as exc:
                    raise GatewayError(
                        "FILE_TEXT_INVALID", "file is not valid UTF-8"
                    ) from exc
        return response

    def readonly_rpc(self, args: Mapping[str, Any]) -> dict[str, Any]:
        if not self.policy.allow_readonly_rpc:
            raise GatewayError(
                "READONLY_RPC_DISABLED", "generic read-only RPC is disabled"
            )
        method = args.get("method")
        if not isinstance(method, str) or method not in READONLY_RPC_METHODS:
            raise GatewayError(
                "RPC_METHOD_NOT_ALLOWED", "method is not on the read-only allowlist"
            )
        params = validate_params(args.get("params"))
        # Path-bearing discovery calls still pass through root policy.
        if method in {"skills/list", "hooks/list"} and "cwds" in params:
            raw_cwds = params["cwds"]
            if not isinstance(raw_cwds, list):
                raise GatewayError("CWDS_INVALID", "cwds must be an array")
            params["cwds"] = [self.policy.validate_cwd(value) for value in raw_cwds]
        return {
            "ok": True,
            "method": method,
            "result": self.client.request(method, params),
        }

    def rpc(self, args: Mapping[str, Any]) -> dict[str, Any]:
        method = self.policy.validate_rpc_method(args.get("method"))
        params = self._govern_rpc_params(method, validate_params(args.get("params")))
        timeout = args.get("timeoutSeconds")
        if timeout is None:
            timeout_value = self.operation_timeout
        else:
            try:
                timeout_value = float(timeout)
            except (TypeError, ValueError) as exc:
                raise GatewayError(
                    "TIMEOUT_INVALID", "timeoutSeconds must be numeric"
                ) from exc
            if not 0.1 <= timeout_value <= 3600:
                raise GatewayError(
                    "TIMEOUT_INVALID", "timeoutSeconds must be between 0.1 and 3600"
                )
        return {
            "ok": True,
            "method": method,
            "result": self.client.request(method, params, timeout=timeout_value),
        }

    def lane(self, args: Mapping[str, Any]) -> dict[str, Any]:
        from .lane import (
            collect_diff,
            lane_path,
            list_lanes,
            normalize_lane,
            prepare_worktree,
            resolve_lanes_parent,
        )

        action = str(args.get("action") or "")
        if action == "poll":
            job_id = validate_text(args.get("jobId"), "jobId")
            job = self.job(
                {
                    "action": "get",
                    "jobId": job_id,
                    "includeHistory": bool(args.get("includeHistory")),
                }
            )["job"]
            request = job.get("request")
            request = request if isinstance(request, Mapping) else {}
            lane = str(request.get("_lane") or args.get("lane") or "")
            path = request.get("_laneWorktreePath")
            response: dict[str, Any] = {"ok": True, "lane": lane, "job": job}
            if path and job.get("state") in {
                "succeeded",
                "attention",
                "failed",
                "cancelled",
            }:
                try:
                    response.update(collect_diff(Path(str(path))))
                except GatewayError as exc:
                    response["diffError"] = {"code": exc.code, "message": exc.message}
            return response
        repo_root = Path(self.policy.validate_cwd(args.get("repoRoot")))
        if action == "list":
            return {"ok": True, "lanes": list_lanes(repo_root)}
        lane = normalize_lane(args.get("lane"))
        parent = resolve_lanes_parent(repo_root, args.get("lanesParent"))
        self.policy.validate_path(
            str(parent),
            field="lanesParent",
            must_exist=False,
        )
        worktree = lane_path(parent, lane)
        if action == "diff":
            self.policy.validate_path(
                str(worktree), field="worktreePath", must_exist=True, directory=True
            )
            return {
                "ok": True,
                "lane": lane,
                "worktreePath": str(worktree),
                **collect_diff(worktree),
            }
        if action == "prepare":
            prepared = prepare_worktree(
                repo_root=repo_root,
                lane=lane,
                base_ref=str(args.get("baseRef") or "HEAD"),
                lanes_parent=str(parent),
                require_clean_base=bool(args.get("requireCleanBase", True)),
                timeout=self._timeout_arg(args, default=60.0),
            )
            self.policy.validate_cwd(prepared["worktreePath"])
            return {"ok": True, **prepared}
        if action == "review":
            if not worktree.is_dir():
                raise GatewayError(
                    "WORKTREE_MISSING", f"lane worktree is absent: {worktree}"
                )
            review_args = dict(args)
            review_args["cwd"] = str(worktree)
            review_args.setdefault("name", f"{lane} review")
            review_args.setdefault("wait", True)
            if review_args.get("baseRef") and review_args.get("baseBranch") is None:
                review_args["baseBranch"] = review_args["baseRef"]
            result = self.review(review_args)
            return {"ok": True, "lane": lane, "worktreePath": str(worktree), **result}
        if action not in {"start", "run"}:
            raise GatewayError("LANE_ACTION_INVALID", f"unknown lane action: {action}")

        prepared = prepare_worktree(
            repo_root=repo_root,
            lane=lane,
            base_ref=str(args.get("baseRef") or "HEAD"),
            lanes_parent=str(parent),
            require_clean_base=bool(args.get("requireCleanBase", True)),
            timeout=min(self._timeout_arg(args, default=900.0), 120.0),
        )
        worktree_path = self.policy.validate_cwd(prepared["worktreePath"])
        plan_only = bool(args.get("planOnly", False))
        sandbox = args.get("sandbox")
        if plan_only:
            sandbox = "read-only"
        elif sandbox is None:
            sandbox = self.default_sandbox
        thread_args: dict[str, Any] = {
            "action": "start",
            "cwd": worktree_path,
            "name": str(args.get("name") or lane),
            "model": args.get("model"),
            "effort": args.get("effort"),
            "sandbox": sandbox,
            "approvalPolicy": args.get("approvalPolicy"),
            "approvalReviewer": args.get("approvalReviewer"),
            "ephemeral": bool(args.get("ephemeral", False)),
        }
        thread_args = {
            key: value for key, value in thread_args.items() if value is not None
        }
        started = self.thread(thread_args)
        thread_id = started["threadId"]
        try:
            self.thread(
                {
                    "action": "metadata",
                    "threadId": thread_id,
                    "gitInfo": {"branch": lane},
                }
            )
        except Exception:
            # Metadata is useful but not required by older app-server builds.
            pass
        goal = validate_text(args.get("goal"), "goal")
        turn_request: dict[str, Any] = {
            "threadId": thread_id,
            "prompt": goal,
            "model": args.get("model"),
            "effort": args.get("effort"),
            "mode": "plan" if plan_only else "default",
            "cwd": worktree_path,
            "sandbox": sandbox,
            "approvalPolicy": args.get("approvalPolicy"),
            "approvalReviewer": args.get("approvalReviewer"),
            "outputSchema": args.get("outputSchema"),
            "_lane": lane,
            "_laneWorktreePath": worktree_path,
            "_laneRepoRoot": str(repo_root),
        }
        turn_request = {
            key: value for key, value in turn_request.items() if value is not None
        }
        created = self.job({"action": "start", "kind": "turn", "request": turn_request})
        response = {
            "ok": True,
            **prepared,
            "threadId": thread_id,
            "job": created["job"],
            "planOnly": plan_only,
        }
        if action == "start":
            return response

        job_id = created["job"]["jobId"]
        deadline = time.monotonic() + self._timeout_arg(args, default=900.0)
        job = created["job"]
        while time.monotonic() < deadline:
            job = self.job({"action": "get", "jobId": job_id})["job"]
            if job["state"] not in {
                "queued",
                "running",
                "waiting_action",
                "cancelling",
            }:
                break
            time.sleep(0.2)
        else:
            return {
                **response,
                "ok": False,
                "status": "timeout",
                "error": "LANE_TIMEOUT",
                "job": job,
            }
        diff = collect_diff(Path(worktree_path))
        status = "ok" if job["state"] == "succeeded" else job["state"]
        if not plan_only and job["state"] == "succeeded" and not diff["changedFiles"]:
            status = "no_changes"
        return {
            **response,
            **diff,
            "ok": status == "ok",
            "status": status,
            "error": "EXECUTE_NO_CHANGES"
            if status == "no_changes"
            else job.get("error"),
            "job": job,
        }

    def _wait_for_turn(
        self,
        thread_id: str,
        turn_id: str,
        *,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        cursor = 1
        seen: list[dict[str, Any]] = []
        while time.monotonic() < deadline:
            polled = self.events.poll(
                thread_id,
                cursor=cursor,
                limit=200,
                include_actions=True,
            )
            cursor = int(polled["nextCursor"])
            seen.extend(polled["events"])
            if polled.get("actions"):
                return {
                    "status": "waiting_action",
                    "actions": polled["actions"],
                    "nextCursor": cursor,
                }
            for event in polled["events"]:
                if event.get("method") != "turn/completed":
                    continue
                params = event.get("params")
                turn = params.get("turn") if isinstance(params, Mapping) else None
                event_turn_id = (
                    turn.get("id") if isinstance(turn, Mapping) else None
                ) or (params.get("turnId") if isinstance(params, Mapping) else None)
                if event_turn_id == turn_id:
                    return {
                        "status": (
                            turn.get("status")
                            if isinstance(turn, Mapping)
                            else "completed"
                        ),
                        "event": event,
                        "summary": self._last_agent_message(seen),
                        "nextCursor": cursor,
                    }
            time.sleep(0.1)
        return {"status": "timeout", "nextCursor": cursor}

    @staticmethod
    def _last_agent_message(events: list[dict[str, Any]]) -> Optional[str]:
        for event in reversed(events):
            params = event.get("params")
            item = params.get("item") if isinstance(params, Mapping) else None
            if isinstance(item, Mapping):
                if item.get("type") == "agentMessage" and isinstance(
                    item.get("text"), str
                ):
                    return str(item["text"])
                if item.get("type") == "exitedReviewMode" and isinstance(
                    item.get("review"), str
                ):
                    return str(item["review"])
        return None

    def _govern_rpc_params(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        governed = dict(params)
        if "cwd" in governed and governed["cwd"] is not None:
            governed["cwd"] = self.policy.validate_cwd(governed["cwd"])
        if "cwds" in governed and governed["cwds"] is not None:
            if not isinstance(governed["cwds"], list):
                raise GatewayError("CWDS_INVALID", "cwds must be an array")
            governed["cwds"] = [
                self.policy.validate_cwd(value) for value in governed["cwds"]
            ]
        if method.startswith("fs/"):
            for key in ("path", "sourcePath", "destinationPath"):
                if key in governed:
                    governed[key] = self.policy.validate_path(
                        governed[key],
                        field=key,
                        must_exist=(
                            key != "destinationPath"
                            and method not in {"fs/writeFile", "fs/createDirectory"}
                        ),
                    )
        if method in {"thread/start", "thread/resume", "thread/fork", "turn/start"}:
            sandbox = governed.get("sandbox")
            if sandbox is not None:
                governed["sandbox"] = self.policy.validate_sandbox(sandbox)
            policy = governed.get("sandboxPolicy")
            if isinstance(policy, Mapping) and policy.get("type") == "dangerFullAccess":
                self._require_full_access(method)
        return governed

    def _govern_admin_params(self, params: dict[str, Any]) -> dict[str, Any]:
        """Apply path/thread boundaries to the fixed administrative surface."""
        governed = dict(params)
        for key in ("cwd", "root", "rootPath", "workspaceRoot"):
            if key in governed and governed[key] is not None:
                governed[key] = self.policy.validate_cwd(governed[key])
        for key in ("cwds", "roots", "extraRoots"):
            if key not in governed or governed[key] is None:
                continue
            values = governed[key]
            if not isinstance(values, list):
                raise GatewayError(
                    "PATHS_INVALID", f"{key} must be an array of directories"
                )
            governed[key] = [self.policy.validate_cwd(value) for value in values]
        for key in ("path", "filePath", "sourcePath", "destinationPath"):
            if key in governed and governed[key] is not None:
                governed[key] = self.policy.validate_path(
                    governed[key], field=key, must_exist=False
                )
        if governed.get("threadId") is not None:
            governed["threadId"] = validate_thread_id(governed["threadId"])
        return governed

    def _require_unsafe_rpc(self, method: str) -> None:
        if not self.policy.allow_unsafe_rpc:
            raise GatewayError(
                "UNSAFE_RPC_DISABLED",
                f"{method} requires CODEX_APP_MCP_ALLOW_UNSAFE_RPC=1",
            )

    def _require_full_access(self, method: str) -> None:
        if not self.policy.allow_full_access:
            raise GatewayError(
                "FULL_ACCESS_DISABLED",
                f"{method} requires CODEX_APP_MCP_ALLOW_FULL_ACCESS=1",
            )

    @staticmethod
    def _validate_base64(value: Any, field: str) -> str:
        if not isinstance(value, str) or not value:
            raise GatewayError(
                "BASE64_INVALID", f"{field} must be a non-empty base64 string"
            )
        try:
            base64.b64decode(value, validate=True)
        except ValueError as exc:
            raise GatewayError(
                "BASE64_INVALID", f"{field} is not valid base64"
            ) from exc
        return value

    @staticmethod
    def _terminal_size(value: Any) -> dict[str, int]:
        if not isinstance(value, Mapping):
            raise GatewayError("TERMINAL_SIZE_INVALID", "size must be an object")
        rows = value.get("rows")
        cols = value.get("cols")
        if (
            isinstance(rows, bool)
            or isinstance(cols, bool)
            or not isinstance(rows, int)
            or not isinstance(cols, int)
            or rows < 1
            or cols < 1
        ):
            raise GatewayError(
                "TERMINAL_SIZE_INVALID",
                "size.rows and size.cols must be positive integers",
            )
        return {"rows": rows, "cols": cols}

    @staticmethod
    def _timeout_arg(args: Mapping[str, Any], *, default: float) -> float:
        raw = args.get("timeoutSeconds")
        if raw is None:
            raw = args.get("timeout_seconds")
        if raw is None:
            return default
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise GatewayError("TIMEOUT_INVALID", "timeout must be numeric") from exc
        if not 0.1 <= value <= 3600:
            raise GatewayError(
                "TIMEOUT_INVALID", "timeout must be between 0.1 and 3600"
            )
        return value

    def _thread_start(self, args: Mapping[str, Any]) -> Any:
        params = self._thread_overrides(args, require_cwd=True)
        params["ephemeral"] = bool(args.get("ephemeral", False))
        result = self.client.request(
            "thread/start", params, timeout=self.operation_timeout
        )
        thread_id = _extract_thread_id(result)
        name = validate_text(args.get("name"), "name", required=False)
        if name:
            self.client.request(
                "thread/name/set", {"threadId": thread_id, "name": name}
            )
        return result

    def _thread_overrides(
        self, args: Mapping[str, Any], *, require_cwd: bool
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        cwd = args.get("cwd")
        if require_cwd or cwd is not None:
            params["cwd"] = self.policy.validate_cwd(cwd)
        model = validate_model(args.get("model"))
        effort = validate_effort(args.get("effort"))
        self._validate_model_effort(model, effort)
        if model is not None:
            params["model"] = model
        config = self.policy.validate_thread_config(args.get("config"))
        if effort is not None:
            config["model_reasoning_effort"] = effort
        if config:
            params["config"] = config
        sandbox = args.get("sandbox")
        if sandbox is not None:
            params["sandbox"] = self.policy.validate_sandbox(sandbox)
        elif self.default_sandbox is not None:
            params["sandbox"] = self.default_sandbox
        approval = args.get("approvalPolicy")
        if approval is not None:
            params["approvalPolicy"] = self._approval_policy(approval)
        elif self.default_approval_policy is not None:
            params["approvalPolicy"] = self.default_approval_policy
        reviewer = args.get("approvalReviewer")
        if reviewer is not None:
            params["approvalsReviewer"] = self._approval_reviewer(reviewer)
        for key in (
            "baseInstructions",
            "developerInstructions",
            "personality",
            "serviceName",
            "serviceTier",
        ):
            if args.get(key) is not None:
                params[key] = args.get(key)
        if args.get("dynamicTools") is not None:
            dynamic_tools = args.get("dynamicTools")
            if not isinstance(dynamic_tools, list):
                raise GatewayError(
                    "DYNAMIC_TOOLS_INVALID", "dynamicTools must be an array"
                )
            params["dynamicTools"] = dynamic_tools
        return params

    @staticmethod
    def _approval_policy(value: Any) -> str:
        if not isinstance(value, str) or value not in APPROVAL_POLICIES:
            raise GatewayError(
                "APPROVAL_POLICY_INVALID",
                f"approvalPolicy must be one of {sorted(APPROVAL_POLICIES)}",
            )
        return value

    @staticmethod
    def _approval_reviewer(value: Any) -> str:
        if not isinstance(value, str) or value not in APPROVAL_REVIEWERS:
            raise GatewayError(
                "APPROVAL_REVIEWER_INVALID",
                f"approvalReviewer must be one of {sorted(APPROVAL_REVIEWERS)}",
            )
        return value

    def _model_list(self, *, force: bool = False) -> Any:
        with self._models_lock:
            if force or not self._models or time.monotonic() - self._models_at > 60:
                result = self.client.request(
                    "model/list", {"limit": 200, "includeHidden": True}
                )
                rows = result.get("data") if isinstance(result, Mapping) else None
                if not isinstance(rows, list):
                    raise GatewayError(
                        "PROTOCOL_INVALID", "model/list returned invalid data"
                    )
                self._models = {
                    str(row.get("id") or row.get("model")): dict(row)
                    for row in rows
                    if isinstance(row, Mapping) and (row.get("id") or row.get("model"))
                }
                self._models_at = time.monotonic()
                return result
            return {"data": list(self._models.values()), "nextCursor": None}

    def _validate_model_effort(
        self, model: Optional[str], effort: Optional[str]
    ) -> None:
        if model is None and effort is None:
            return
        self._model_list()
        if model is None:
            defaults = [row for row in self._models.values() if row.get("isDefault")]
            row = defaults[0] if defaults else None
        else:
            row = self._models.get(model)
            if row is None:
                raise GatewayError(
                    "MODEL_NOT_AVAILABLE", f"model is not available: {model}"
                )
        if effort is None or row is None:
            return
        raw_efforts = row.get("supportedReasoningEfforts")
        allowed = {
            str(item.get("reasoningEffort"))
            for item in raw_efforts or []
            if isinstance(item, Mapping) and item.get("reasoningEffort")
        }
        if allowed and effort not in allowed:
            raise GatewayError(
                "EFFORT_NOT_SUPPORTED",
                f"{row.get('id') or row.get('model')} does not support effort={effort}",
                supportedEfforts=sorted(allowed),
            )
