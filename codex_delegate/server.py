"""stdio JSON-RPC 2.0 MCP adapter for codex_delegate."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, TextIO

try:
    from .guard import structured_error
    from .handlers import handle_tool_call
    from .process import GitRunner, SubprocessRunner, WhichFn
    from .roots import (
        load_allowed_roots,
        resolve_server_codex_bin,
        resolve_trusted_lanes_parent,
        resolve_trusted_repo_root,
    )
except ImportError:  # pragma: no cover - flat launch via python codex_delegate/server.py
    from guard import structured_error
    from handlers import handle_tool_call
    from process import GitRunner, SubprocessRunner, WhichFn
    from roots import (
        load_allowed_roots,
        resolve_server_codex_bin,
        resolve_trusted_lanes_parent,
        resolve_trusted_repo_root,
    )

SERVER_NAME = "codex-delegate"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2024-11-05"

TOOL_NAMES = [
    "codex_delegate",
    "codex_delegate_plan",
    "codex_delegate_review",
    "codex_delegate_status",
    "codex_delegate_doctor",
    "codex_delegate_models",
    "codex_delegate_lanes",
]


class _Missing:
    pass


_MISSING = _Missing()


def _tool_result_payload(result: dict[str, Any]) -> dict[str, Any]:
    ok = bool(result.get("ok"))
    text = json.dumps(result, ensure_ascii=False, default=str)
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": result,
        "isError": not ok,
    }


def _tool_schemas() -> list[dict[str, Any]]:
    delegate_props = {
        "goal": {"type": "string", "description": "Coding goal for the Codex agent"},
        "lane": {"type": "string", "description": "Lane slug or codex/<slug>"},
        "base_ref": {"type": "string", "description": "Git base ref (default HEAD)"},
        "model": {"type": "string", "description": "Optional model slug"},
        "reasoning_effort": {
            "type": "string",
            "enum": ["low", "medium", "high", "xhigh", "max", "ultra"],
        },
        "sandbox": {
            "type": "string",
            "enum": ["read-only", "workspace-write"],
            "description": "Sandbox mode; danger-full-access is never accepted",
        },
        "plan_only": {"type": "boolean"},
        "timeout_seconds": {"type": "number"},
        "repo_root": {"type": "string"},
        "lanes_parent": {"type": "string"},
        "output_schema": {
            "description": "JSON Schema object or JSON string for final response shape",
        },
        # resume is intentionally absent: codex exec resume rejects --cd/-s
        # (RESUME_UNSUPPORTED if smuggled via additionalProperties bypass).
        "ephemeral": {"type": "boolean"},
    }
    return [
        {
            "name": "codex_delegate",
            "description": (
                "Execute a coding goal in an isolated codex/* git worktree via headless "
                "codex exec -s workspace-write. Never pushes or merges."
            ),
            "inputSchema": {
                "type": "object",
                "properties": delegate_props,
                "required": ["goal", "lane"],
                "additionalProperties": False,
            },
        },
        {
            "name": "codex_delegate_plan",
            "description": (
                "Plan-only delegation: forced plan_only=true and -s read-only. "
                "No writes under the worktree."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {k: v for k, v in delegate_props.items() if k != "plan_only"},
                "required": ["goal", "lane"],
                "additionalProperties": False,
            },
        },
        {
            "name": "codex_delegate_review",
            "description": (
                "Run codex exec review inside an existing lane worktree. The tool has no "
                "--cd, no -s, and no --color flag (codex exec review rejects them). The "
                "process cwd is set to the lane worktree; sandbox is the Codex default "
                "(probe A: read-only), returned as sandbox='read-only' with "
                "sandbox_is_codex_default=true."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "lane": {"type": "string"},
                    "repo_root": {"type": "string"},
                    "base_ref": {"type": "string", "description": "Passed as --base"},
                    "uncommitted": {"type": "boolean"},
                    "instructions": {"type": "string"},
                    "model": {"type": "string"},
                    "timeout_seconds": {"type": "number"},
                },
                "required": ["lane"],
                "additionalProperties": False,
            },
        },
        {
            "name": "codex_delegate_status",
            "description": "Health JSON: binary, auth presence, git, roots, sandbox policy.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "codex_delegate_doctor",
            "description": "Run codex doctor --json (redacted by Codex). Never runs fix paths.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "codex_delegate_models",
            "description": "Reduced model catalog from codex debug models.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "codex_delegate_lanes",
            "description": "List codex/* lanes for an allowlisted repo root.",
            "inputSchema": {
                "type": "object",
                "properties": {"repo_root": {"type": "string"}},
                "additionalProperties": False,
            },
        },
    ]


def handle_jsonrpc(
    message: Mapping[str, Any],
    *,
    repo_root: Optional[Path | str] = None,
    allowed_roots: Optional[Sequence[Path | str]] = None,
    git_runner: Optional[GitRunner] = None,
    subprocess_runner: Optional[SubprocessRunner] = None,
    which: Optional[WhichFn] = None,
    audit_stream: Optional[TextIO] = None,
    principal: str = "local-dev",
) -> Optional[dict[str, Any]]:
    """Handle one JSON-RPC message. Notifications (no id) return None."""
    if not isinstance(message, Mapping):
        return {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32600, "message": "Invalid Request"},
        }

    method = message.get("method")
    msg_id = message.get("id", _MISSING)
    is_notification = msg_id is _MISSING

    if method == "notifications/initialized" or method == "initialized":
        return None
    if is_notification:
        return None

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        }

    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": _tool_schemas()}}

    if method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name") if isinstance(params, Mapping) else None
        arguments = params.get("arguments") if isinstance(params, Mapping) else {}
        if not name:
            result = structured_error("TOOL_UNKNOWN", "missing tool name")
        else:
            result = handle_tool_call(
                str(name),
                arguments if isinstance(arguments, Mapping) else {},
                repo_root=repo_root,
                allowed_roots=allowed_roots,
                git_runner=git_runner,
                subprocess_runner=subprocess_runner,
                which=which,
                audit_stream=audit_stream,
                principal=principal,
            )
        return {"jsonrpc": "2.0", "id": msg_id, "result": _tool_result_payload(result)}

    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def _write_message(msg: Mapping[str, Any], stdout: TextIO, *, framed: bool = False) -> None:
    body = json.dumps(msg, ensure_ascii=False, default=str)
    if framed:
        stdout.write(f"Content-Length: {len(body.encode('utf-8'))}\r\n\r\n{body}")
    else:
        stdout.write(body + "\n")
    stdout.flush()


def serve_stdio(
    *,
    stdin: Optional[TextIO] = None,
    stdout: Optional[TextIO] = None,
    repo_root: Optional[Path | str] = None,
    allowed_roots: Optional[Sequence[Path | str]] = None,
    git_runner: Optional[GitRunner] = None,
    subprocess_runner: Optional[SubprocessRunner] = None,
    which: Optional[WhichFn] = None,
    audit_stream: Optional[TextIO] = None,
    principal: str = "local-dev",
) -> None:
    """Serve MCP over stdio. Supports line-delimited JSON and Content-Length framing."""
    inn = stdin if stdin is not None else sys.stdin
    out = stdout if stdout is not None else sys.stdout
    framed_mode: Optional[bool] = None

    while True:
        first = inn.readline()
        if not first:
            break
        if first.lower().startswith("content-length:"):
            framed_mode = True
            try:
                length = int(first.split(":", 1)[1].strip())
            except ValueError:
                continue
            while True:
                line = inn.readline()
                if line in ("", "\n", "\r\n"):
                    break
                if not line:
                    return
            body = inn.read(length)
            if not body:
                break
            try:
                message = json.loads(body)
            except json.JSONDecodeError:
                continue
        else:
            framed_mode = False if framed_mode is None else framed_mode
            line = first.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue

        response = handle_jsonrpc(
            message,
            repo_root=repo_root,
            allowed_roots=allowed_roots,
            git_runner=git_runner,
            subprocess_runner=subprocess_runner,
            which=which,
            audit_stream=audit_stream,
            principal=principal,
        )
        if response is not None:
            _write_message(response, out, framed=bool(framed_mode))


def main() -> None:
    serve_stdio()


if __name__ == "__main__":
    main()
