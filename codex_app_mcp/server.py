"""MCP stdio server exposing the governed Codex app-server gateway."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Mapping, Optional, TextIO

from . import __version__
from .session import session_begin, session_end, session_tick
from .economy import economy_playbook
from .audit import emit_audit, goal_fingerprint
from .client import RpcError, TransportClosed
from .errors import GatewayError, error_envelope
from .gateway import ADMIN_OPERATIONS, CodexAppGateway

SERVER_NAME = "codex-app-mcp"
SERVER_INSTRUCTIONS = (
    "Use this server to obtain an independent Codex analysis or to run governed "
    "Codex work. For a focused consultation, start a thread, start a turn, then "
    "poll codex_app_events. Use codex_app_lane for isolated repository work and "
    "codex_app_job with kind=goal for durable long-running objectives. Use "
    "codex_app_protocol only to inspect a specific low-level method. Always pass "
    "the intended cwd or repoRoot, model, reasoning effort, sandbox, and approval "
    "policy; the calling agent remains responsible for comparing results and "
    "presenting the final decision."
)
CURRENT_PROTOCOL_VERSION = "2026-07-28"
LEGACY_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2024-11-05")
SUPPORTED_PROTOCOL_VERSIONS = (CURRENT_PROTOCOL_VERSION, *LEGACY_PROTOCOL_VERSIONS)
PROTOCOL_VERSION = LEGACY_PROTOCOL_VERSIONS[0]
MAX_MCP_FRAME_BYTES = 4 * 1024 * 1024


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


COMMON_THREAD_PROPERTIES: dict[str, Any] = {
    "action": {"type": "string"},
    "threadId": {"type": "string"},
    "cwd": {"type": "string"},
    "name": {"type": "string"},
    "model": {"type": "string"},
    "effort": {"type": "string"},
    "sandbox": {
        "type": "string",
        "enum": ["read-only", "workspace-write", "danger-full-access"],
    },
    "approvalPolicy": {
        "type": "string",
        "enum": ["untrusted", "on-request", "never"],
    },
    "approvalReviewer": {
        "type": "string",
        "enum": ["user", "auto_review", "guardian_subagent"],
    },
    "config": {"type": "object"},
    "ephemeral": {"type": "boolean"},
    "includeTurns": {"type": "boolean"},
    "archived": {"type": "boolean"},
    "limit": {"type": "integer"},
    "cursor": {},
    "searchTerm": {"type": "string"},
    "sortKey": {"type": "string"},
    "sortDirection": {"type": "string"},
    "baseInstructions": {"type": "string"},
    "developerInstructions": {"type": "string"},
    "personality": {"type": "string"},
    "serviceName": {"type": "string"},
    "serviceTier": {"type": "string"},
    "turnId": {"type": "string"},
    "items": {"type": "array"},
    "itemsView": {"type": "string"},
    "gitInfo": {"type": "object"},
    "numTurns": {"type": "integer"},
    "command": {},
    "processId": {"type": "string"},
    "dynamicTools": {"type": "array", "items": {"type": "object"}},
}


def tool_schemas() -> list[dict[str, Any]]:
    return [
        {
            "name": "codex_app_status",
            "description": "Start/probe the version-pinned Codex app-server connection.",
            "inputSchema": _schema(
                {"includeStderr": {"type": "boolean"}},
                [],
            ),
        },
        {
            "name": "codex_app_economy",
            "description": (
                "Token-economy playbook for host agents: offload coding to Codex, "
                "set token budgets, compact threads, VPS HTTP tips. Call once."
            ),
            "inputSchema": {"type": "object", "additionalProperties": False, "properties": {}},
        },
        {
            "name": "codex_app_session_begin",
            "description": "Session Protocol v1: begin compact host session (intent enum). Returns mode, gate, tools, skill_ref. Call first.",
            "inputSchema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "intent": {
                        "type": "string",
                        "enum": ["brainstorm", "execute", "verify", "install", "update", "triage", "feedback", "auto"],
                        "default": "auto",
                    }
                },
            },
        },
        {
            "name": "codex_app_session_tick",
            "description": "Session Protocol v1: compact progress (state, blockers, host_message). verbose default false.",
            "inputSchema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "session_id": {"type": "string"},
                    "job_id": {"type": "string"},
                    "verbose": {"type": "boolean", "default": False},
                },
            },
        },
        {
            "name": "codex_app_session_end",
            "description": "Session Protocol v1: short receipt; optional scrubbed issue draft (no auto-create).",
            "inputSchema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "session_id": {"type": "string"},
                    "job_id": {"type": "string"},
                    "suggest_issue": {"type": "boolean", "default": False},
                    "note": {"type": "string"},
                },
            },
        },
        {
            "name": "codex_app_doctor",
            "description": (
                "Run read-only app-server, account, config-requirements, and Windows "
                "sandbox-readiness diagnostics without invoking codex doctor."
            ),
            "inputSchema": _schema({}, []),
        },
        {
            "name": "codex_app_discover",
            "description": (
                "Discover live models/efforts, collaboration modes, features, permission "
                "profiles, configured MCP servers, apps, plugins, skills, hooks, or account."
            ),
            "inputSchema": _schema(
                {
                    "action": {
                        "type": "string",
                        "enum": [
                            "models",
                            "collaboration_modes",
                            "features",
                            "permission_profiles",
                            "mcp_servers",
                            "apps",
                            "plugins",
                            "skills",
                            "hooks",
                            "account",
                            "config_requirements",
                        ],
                    },
                    "limit": {"type": "integer"},
                    "cursor": {},
                    "force": {"type": "boolean"},
                    "cwds": {"type": "array", "items": {"type": "string"}},
                    "detail": {"type": "string"},
                },
                ["action"],
            ),
        },
        {
            "name": "codex_app_thread",
            "description": (
                "Create, list, read, resume, fork, name, compact, archive, unarchive, "
                "or unsubscribe persistent Codex threads."
            ),
            "inputSchema": _schema(
                {
                    **COMMON_THREAD_PROPERTIES,
                    "action": {
                        "type": "string",
                        "enum": [
                            "start",
                            "list",
                            "read",
                            "resume",
                            "fork",
                            "name",
                            "compact",
                            "archive",
                            "unarchive",
                            "unsubscribe",
                            "loaded",
                            "turns",
                            "items",
                            "delete",
                            "shell",
                            "metadata",
                            "rollback",
                            "inject",
                            "terminals_list",
                            "terminals_clean",
                            "terminal_terminate",
                        ],
                    },
                },
                ["action"],
            ),
        },
        {
            "name": "codex_app_goal",
            "description": (
                "Start or manage a persisted autonomous goal. Activating a goal lets the "
                "Codex runtime generate continuation turns until terminal goal status."
            ),
            "inputSchema": _schema(
                {
                    **COMMON_THREAD_PROPERTIES,
                    "action": {
                        "type": "string",
                        "enum": [
                            "start",
                            "set",
                            "get",
                            "clear",
                            "pause",
                            "resume",
                            "complete",
                        ],
                    },
                    "objective": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": [
                            "active",
                            "paused",
                            "blocked",
                            "usageLimited",
                            "budgetLimited",
                            "complete",
                        ],
                    },
                    "tokenBudget": {"type": "integer"},
                },
                ["action"],
            ),
        },
        {
            "name": "codex_app_turn",
            "description": "Start, steer, or interrupt a turn on a loaded Codex thread.",
            "inputSchema": _schema(
                {
                    "action": {
                        "type": "string",
                        "enum": ["start", "steer", "interrupt"],
                    },
                    "threadId": {"type": "string"},
                    "turnId": {"type": "string"},
                    "prompt": {"type": "string"},
                    "model": {"type": "string"},
                    "effort": {"type": "string"},
                    "mode": {"type": "string", "enum": ["default", "plan"]},
                    "cwd": {"type": "string"},
                    "sandbox": {
                        "type": "string",
                        "enum": ["read-only", "workspace-write", "danger-full-access"],
                    },
                    "approvalPolicy": {
                        "type": "string",
                        "enum": ["untrusted", "on-request", "never"],
                    },
                    "approvalReviewer": {"type": "string"},
                    "images": {"type": "array", "items": {"type": "string"}},
                    "outputSchema": {"type": "object"},
                },
                ["action", "threadId"],
            ),
        },
        {
            "name": "codex_app_review",
            "description": (
                "Run the native app-server reviewer inline or detached against "
                "uncommitted changes, a base branch, a commit, or custom instructions."
            ),
            "inputSchema": _schema(
                {
                    **COMMON_THREAD_PROPERTIES,
                    "target": {"type": "object"},
                    "instructions": {"type": "string"},
                    "commit": {"type": "string"},
                    "title": {"type": "string"},
                    "baseBranch": {"type": "string"},
                    "delivery": {
                        "type": "string",
                        "enum": ["inline", "detached"],
                    },
                    "wait": {"type": "boolean"},
                    "timeoutSeconds": {"type": "number"},
                },
                [],
            ),
        },
        {
            "name": "codex_app_command",
            "description": (
                "Run and control app-server command/exec sessions. Commands are argv "
                "arrays; danger-full-access remains an explicit governed option."
            ),
            "inputSchema": _schema(
                {
                    "action": {
                        "type": "string",
                        "enum": ["exec", "write", "resize", "terminate"],
                    },
                    "command": {"type": "array", "items": {"type": "string"}},
                    "cwd": {"type": "string"},
                    "sandbox": {
                        "type": "string",
                        "enum": ["read-only", "workspace-write", "danger-full-access"],
                    },
                    "env": {"type": "object"},
                    "timeoutMs": {"type": "integer"},
                    "timeoutSeconds": {"type": "number"},
                    "disableTimeout": {"type": "boolean"},
                    "outputBytesCap": {"type": "integer"},
                    "disableOutputCap": {"type": "boolean"},
                    "tty": {"type": "boolean"},
                    "processId": {"type": "string"},
                    "streamStdin": {"type": "boolean"},
                    "streamStdoutStderr": {"type": "boolean"},
                    "size": {"type": "object"},
                    "deltaBase64": {"type": "string"},
                    "closeStdin": {"type": "boolean"},
                },
                ["action"],
            ),
        },
        {
            "name": "codex_app_process",
            "description": (
                "Control experimental unsandboxed app-server process/* sessions. "
                "Requires CODEX_APP_MCP_ALLOW_FULL_ACCESS=1."
            ),
            "inputSchema": _schema(
                {
                    "action": {
                        "type": "string",
                        "enum": ["spawn", "write", "resize", "kill"],
                    },
                    "processHandle": {"type": "string"},
                    "command": {"type": "array", "items": {"type": "string"}},
                    "cwd": {"type": "string"},
                    "env": {"type": "object"},
                    "tty": {"type": "boolean"},
                    "size": {"type": "object"},
                    "streamStdin": {"type": "boolean"},
                    "streamStdoutStderr": {"type": "boolean"},
                    "outputBytesCap": {"type": "integer"},
                    "timeoutMs": {"type": "integer"},
                    "deltaBase64": {"type": "string"},
                    "closeStdin": {"type": "boolean"},
                },
                ["action", "processHandle"],
            ),
        },
        {
            "name": "codex_app_fs",
            "description": (
                "Use app-server filesystem v2 for governed read/write/list/copy/remove/"
                "watch operations under allowlisted roots."
            ),
            "inputSchema": _schema(
                {
                    "action": {
                        "type": "string",
                        "enum": [
                            "read",
                            "write",
                            "mkdir",
                            "metadata",
                            "list",
                            "remove",
                            "copy",
                            "watch",
                            "unwatch",
                        ],
                    },
                    "path": {"type": "string"},
                    "sourcePath": {"type": "string"},
                    "destinationPath": {"type": "string"},
                    "dataBase64": {"type": "string"},
                    "text": {"type": "string"},
                    "decodeText": {"type": "boolean"},
                    "recursive": {"type": "boolean"},
                    "force": {"type": "boolean"},
                    "watchId": {"type": "string"},
                },
                ["action"],
            ),
        },
        {
            "name": "codex_app_events",
            "description": (
                "Poll cursor-paginated thread events, list pending approvals/input, or "
                "respond to a deferred app-server request."
            ),
            "inputSchema": _schema(
                {
                    "action": {
                        "type": "string",
                        "enum": ["poll", "pending", "respond"],
                    },
                    "threadId": {"type": "string"},
                    "cursor": {"type": "integer"},
                    "limit": {"type": "integer"},
                    "includeActions": {"type": "boolean"},
                    "actionId": {"type": "string"},
                    "decision": {"type": "string"},
                    "answers": {"type": "object"},
                    "response": {"type": "object"},
                    "execpolicyAmendment": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "networkPolicyAmendment": {"type": "object"},
                },
                ["action"],
            ),
        },
        {
            "name": "codex_app_job",
            "description": (
                "Start, inspect, resume, or cancel durable background goal/turn jobs. "
                "Jobs use a SQLite ledger and expose persisted thread/turn identifiers."
            ),
            "inputSchema": _schema(
                {
                    "action": {
                        "type": "string",
                        "enum": ["start", "get", "list", "cancel", "resume"],
                    },
                    "kind": {"type": "string", "enum": ["goal", "turn"]},
                    "request": {"type": "object"},
                    "jobId": {"type": "string"},
                    "state": {
                        "type": "string",
                        "enum": [
                            "queued",
                            "running",
                            "waiting_action",
                            "cancelling",
                            "succeeded",
                            "attention",
                            "failed",
                            "cancelled",
                            "orphaned",
                        ],
                    },
                    "includeHistory": {"type": "boolean"},
                    "limit": {"type": "integer"},
                },
                ["action"],
            ),
        },
        {
            "name": "codex_app_lane",
            "description": (
                "Prepare/list/diff isolated codex/* git worktrees, run or background "
                "a turn, poll it, and review it through app-server."
            ),
            "inputSchema": _schema(
                {
                    "action": {
                        "type": "string",
                        "enum": [
                            "prepare",
                            "list",
                            "diff",
                            "start",
                            "run",
                            "poll",
                            "review",
                        ],
                    },
                    "goal": {"type": "string"},
                    "lane": {"type": "string"},
                    "repoRoot": {"type": "string"},
                    "lanesParent": {"type": "string"},
                    "baseRef": {"type": "string"},
                    "baseBranch": {"type": "string"},
                    "requireCleanBase": {"type": "boolean"},
                    "model": {"type": "string"},
                    "effort": {"type": "string"},
                    "sandbox": {
                        "type": "string",
                        "enum": ["read-only", "workspace-write", "danger-full-access"],
                    },
                    "approvalPolicy": {
                        "type": "string",
                        "enum": ["untrusted", "on-request", "never"],
                    },
                    "approvalReviewer": {"type": "string"},
                    "planOnly": {"type": "boolean"},
                    "outputSchema": {"type": "object"},
                    "ephemeral": {"type": "boolean"},
                    "name": {"type": "string"},
                    "timeoutSeconds": {"type": "number"},
                    "jobId": {"type": "string"},
                    "includeHistory": {"type": "boolean"},
                    "threadId": {"type": "string"},
                    "target": {"type": "object"},
                    "instructions": {"type": "string"},
                    "commit": {"type": "string"},
                    "title": {"type": "string"},
                    "delivery": {
                        "type": "string",
                        "enum": ["inline", "detached"],
                    },
                },
                ["action"],
            ),
        },
        {
            "name": "codex_app_mcp_call",
            "description": (
                "Call an explicitly allowlisted downstream MCP/SaaS tool through app-server. "
                "Disabled until server and tool allowlists are configured."
            ),
            "inputSchema": _schema(
                {
                    "threadId": {"type": "string"},
                    "server": {"type": "string"},
                    "tool": {"type": "string"},
                    "arguments": {"type": "object"},
                    "_meta": {"type": "object"},
                },
                ["server", "tool"],
            ),
        },
        {
            "name": "codex_app_protocol",
            "description": (
                "Generate and inspect the exact versioned JSON schema exposed by the "
                "configured Codex app-server binary."
            ),
            "inputSchema": _schema(
                {
                    "action": {
                        "type": "string",
                        "enum": ["summary", "methods", "method", "refresh"],
                    },
                    "kind": {
                        "type": "string",
                        "enum": ["client", "serverRequest", "notification"],
                    },
                    "method": {"type": "string"},
                    "query": {"type": "string"},
                    "limit": {"type": "integer"},
                    "force": {"type": "boolean"},
                },
                ["action"],
            ),
        },
        {
            "name": "codex_app_admin",
            "description": (
                "Typed access to account, config, apps/plugins/skills, environment, "
                "search, memory, realtime, and remote-control app-server operations. "
                "Mutations require the unsafe RPC operator gate."
            ),
            "inputSchema": _schema(
                {
                    "operation": {
                        "type": "string",
                        "enum": sorted(ADMIN_OPERATIONS),
                    },
                    "params": {"type": "object"},
                    "timeoutSeconds": {"type": "number"},
                    "requireSupported": {"type": "boolean"},
                },
                ["operation"],
            ),
        },
        {
            "name": "codex_app_schedule",
            "description": (
                "Create and operate durable timezone-aware RRULE schedules that launch "
                "Codex goal/turn jobs with idempotency, misfire policy, and retries."
            ),
            "inputSchema": _schema(
                {
                    "action": {
                        "type": "string",
                        "enum": [
                            "create",
                            "get",
                            "list",
                            "pause",
                            "resume",
                            "delete",
                            "trigger",
                            "runs",
                        ],
                    },
                    "scheduleId": {"type": "string"},
                    "idempotencyKey": {"type": "string"},
                    "name": {"type": "string"},
                    "timezone": {"type": "string"},
                    "rrule": {"type": "string"},
                    "startAt": {"type": "string"},
                    "request": {"type": "object"},
                    "retryCount": {"type": "integer"},
                    "retryBackoffSeconds": {"type": "number"},
                    "misfirePolicy": {
                        "type": "string",
                        "enum": ["run_once", "skip"],
                    },
                    "limit": {"type": "integer"},
                },
                ["action"],
            ),
        },
        {
            "name": "codex_app_runtime",
            "description": (
                "Read gateway counters or explicitly restart the owned app-server "
                "connection. Restart requires the unsafe RPC operator gate."
            ),
            "inputSchema": _schema(
                {
                    "action": {
                        "type": "string",
                        "enum": ["metrics", "restart"],
                    }
                },
                ["action"],
            ),
        },
        {
            "name": "codex_app_rpc_read",
            "description": (
                "Version-forward-compatible escape hatch for a strict read-only RPC allowlist."
            ),
            "inputSchema": _schema(
                {
                    "method": {"type": "string"},
                    "params": {"type": "object"},
                },
                ["method"],
            ),
        },
        {
            "name": "codex_app_rpc",
            "description": (
                "Forward-compatible raw app-server RPC. Read methods use the built-in "
                "allowlist; stateful methods require an operator method allowlist plus "
                "CODEX_APP_MCP_ALLOW_UNSAFE_RPC=1."
            ),
            "inputSchema": _schema(
                {
                    "method": {"type": "string"},
                    "params": {"type": "object"},
                    "timeoutSeconds": {"type": "number"},
                },
                ["method"],
            ),
        },
    ]


def _tool_payload(result: dict[str, Any]) -> dict[str, Any]:
    ok = bool(result.get("ok"))
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(result, ensure_ascii=False, default=str),
            }
        ],
        "structuredContent": result,
        "isError": not ok,
    }


def call_tool(
    gateway: CodexAppGateway, name: str, args: Mapping[str, Any]
) -> dict[str, Any]:
    started_at = time.monotonic()
    try:
        if name == "codex_app_economy":
            result = economy_playbook()
        elif name == "codex_app_session_begin":
            result = session_begin(str(args.get("intent") or "auto"))
        elif name == "codex_app_session_tick":
            result = session_tick(
                session_id=str(args.get("session_id") or "") or None,
                job_id=str(args.get("job_id") or "") or None,
                verbose=bool(args.get("verbose")),
            )
        elif name == "codex_app_session_end":
            result = session_end(
                session_id=str(args.get("session_id") or "") or None,
                job_id=str(args.get("job_id") or "") or None,
                suggest_issue=bool(args.get("suggest_issue")),
                note=str(args.get("note") or "") or None,
            )
        elif name == "codex_app_status":
            result = gateway.status(include_stderr=bool(args.get("includeStderr")))
        elif name == "codex_app_doctor":
            result = gateway.doctor()
        elif name == "codex_app_discover":
            result = gateway.discover(args)
        elif name == "codex_app_thread":
            result = gateway.thread(args)
        elif name == "codex_app_goal":
            result = gateway.goal(args)
        elif name == "codex_app_turn":
            result = gateway.turn(args)
        elif name == "codex_app_review":
            result = gateway.review(args)
        elif name == "codex_app_command":
            result = gateway.command(args)
        elif name == "codex_app_process":
            result = gateway.process(args)
        elif name == "codex_app_fs":
            result = gateway.filesystem(args)
        elif name == "codex_app_events":
            result = gateway.event(args)
        elif name == "codex_app_job":
            result = gateway.job(args)
        elif name == "codex_app_lane":
            result = gateway.lane(args)
        elif name == "codex_app_mcp_call":
            result = gateway.mcp_call(args)
        elif name == "codex_app_protocol":
            result = gateway.protocol(args)
        elif name == "codex_app_admin":
            result = gateway.admin(args)
        elif name == "codex_app_schedule":
            result = gateway.schedule(args)
        elif name == "codex_app_runtime":
            result = gateway.runtime(args)
        elif name == "codex_app_rpc_read":
            result = gateway.readonly_rpc(args)
        elif name == "codex_app_rpc":
            result = gateway.rpc(args)
        else:
            result = error_envelope("TOOL_UNKNOWN", f"unknown tool: {name}")
    except GatewayError as exc:
        result = error_envelope(exc.code, exc.message, **exc.details)
    except RpcError as exc:
        result = error_envelope(
            "APP_SERVER_RPC_ERROR",
            exc.message,
            rpcCode=exc.code,
            rpcData=exc.data,
        )
    except TimeoutError as exc:
        result = error_envelope("APP_SERVER_TIMEOUT", str(exc))
    except (TransportClosed, FileNotFoundError) as exc:
        result = error_envelope("APP_SERVER_UNAVAILABLE", str(exc))
    except Exception as exc:  # noqa: BLE001 - MCP boundary must not crash
        result = error_envelope("INTERNAL", f"{type(exc).__name__}: {exc}")
    gateway.record_tool_call(
        name,
        ok=bool(result.get("ok")),
        error_code=str(result.get("error")) if result.get("error") else None,
    )
    _emit_tool_audit(name, args, result, started_at)
    return result


def _emit_tool_audit(
    name: str,
    args: Mapping[str, Any],
    result: Mapping[str, Any],
    started_at: float,
) -> None:
    event: dict[str, Any] = {
        "tool": name,
        "action": args.get("action"),
        "method": args.get("method"),
        "lane": result.get("lane") or args.get("lane"),
        "sandbox": args.get("sandbox"),
        "cwd": args.get("cwd") or args.get("repoRoot"),
        "outcome": "ok" if result.get("ok") else "error",
        "error": result.get("error"),
        "thread_id": result.get("threadId") or args.get("threadId"),
        "turn_id": result.get("turnId") or args.get("turnId"),
        "job_id": args.get("jobId"),
        "elapsed_ms": round((time.monotonic() - started_at) * 1000, 3),
    }
    job = result.get("job")
    if isinstance(job, Mapping):
        event["job_id"] = job.get("jobId")
    if "goal" in args:
        event.update(goal_fingerprint(args.get("goal")))
    elif "objective" in args:
        event.update(goal_fingerprint(args.get("objective")))
    emit_audit(event)


def handle_jsonrpc(
    message: Any,
    gateway: CodexAppGateway,
) -> Optional[dict[str, Any]]:
    if not isinstance(message, Mapping):
        return {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32600, "message": "Invalid Request"},
        }
    msg_id = message.get("id")
    method = message.get("method")
    if "id" not in message:
        return None
    if method == "server/discover":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "supportedVersions": list(SUPPORTED_PROTOCOL_VERSIONS),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": __version__},
                "instructions": SERVER_INSTRUCTIONS,
            },
        }
    if method == "initialize":
        params = message.get("params")
        requested = (
            params.get("protocolVersion") if isinstance(params, Mapping) else None
        )
        negotiated = (
            requested if requested in LEGACY_PROTOCOL_VERSIONS else PROTOCOL_VERSION
        )
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": negotiated,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": __version__},
                "instructions": SERVER_INSTRUCTIONS,
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"tools": tool_schemas()},
        }
    if method == "tools/call":
        params = message.get("params")
        if not isinstance(params, Mapping):
            params = {}
        name = params.get("name")
        arguments = params.get("arguments")
        if not isinstance(name, str):
            result = error_envelope("TOOL_UNKNOWN", "missing tool name")
        elif arguments is None:
            result = call_tool(gateway, name, {})
        elif not isinstance(arguments, Mapping):
            result = error_envelope(
                "TOOL_ARGUMENTS_INVALID", "arguments must be an object"
            )
        else:
            result = call_tool(gateway, name, arguments)
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": _tool_payload(result),
        }
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def dispatch_jsonrpc(
    message: Any,
    gateway: CodexAppGateway,
) -> Optional[dict[str, Any] | list[dict[str, Any]]]:
    if not isinstance(message, list):
        return handle_jsonrpc(message, gateway)
    if not message:
        return {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32600, "message": "Invalid Request"},
        }
    responses = [
        response
        for item in message
        if (response := handle_jsonrpc(item, gateway)) is not None
    ]
    return responses or None


def serve_stdio(
    *,
    stdin: Optional[TextIO] = None,
    stdout: Optional[TextIO] = None,
    gateway: Optional[CodexAppGateway] = None,
) -> None:
    source = stdin or sys.stdin
    sink = stdout or sys.stdout
    owned_gateway = gateway is None
    app = gateway or CodexAppGateway()
    if owned_gateway:
        app.start_background()
    try:
        for raw_line in source:
            if len(raw_line.encode("utf-8", errors="replace")) > MAX_MCP_FRAME_BYTES:
                response = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32600, "message": "request frame too large"},
                }
            else:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                response = dispatch_jsonrpc(message, app)
                if response is None:
                    continue
            sink.write(json.dumps(response, ensure_ascii=False, default=str) + "\n")
            sink.flush()
    finally:
        if owned_gateway:
            app.close()


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="Governed MCP gateway for Codex app-server"
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default=os.environ.get("CODEX_APP_MCP_TRANSPORT", "stdio"),
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("CODEX_APP_MCP_HTTP_HOST", "127.0.0.1"),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("CODEX_APP_MCP_HTTP_PORT", "8765")),
    )
    args = parser.parse_args(argv)
    if args.transport == "stdio":
        serve_stdio()
    else:
        from .http_server import serve_http

        serve_http(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
