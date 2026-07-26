"""stdio JSON-RPC 2.0 MCP adapter for codex_delegate."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, TextIO

try:
    from .guard import MAX_RPC_FRAME_BYTES, structured_error
    from .handlers import handle_tool_call
    from .process import GitRunner, SubprocessRunner, WhichFn
    from .roots import (
        load_allowed_roots,
        resolve_server_codex_bin,
        resolve_trusted_lanes_parent,
        resolve_trusted_repo_root,
    )
except ImportError:  # pragma: no cover - flat launch via python codex_delegate/server.py
    from guard import MAX_RPC_FRAME_BYTES, structured_error
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
    "codex_delegate_start",
    "codex_delegate_poll",
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
            "name": "codex_delegate_start",
            "description": (
                "Detached delegation: same arguments and same guarantees as codex_delegate, "
                "but returns a job_id immediately instead of holding the request open for "
                "the whole lane. Poll it with codex_delegate_poll."
            ),
            "inputSchema": {
                "type": "object",
                "properties": delegate_props,
                "required": ["goal", "lane"],
                "additionalProperties": False,
            },
        },
        {
            "name": "codex_delegate_poll",
            "description": (
                "Read a detached delegation back by job_id. A finished job returns the "
                "same envelope the synchronous path returns, and stays readable: polling "
                "twice gives the same answer. Without job_id, lists the jobs."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "description": "Job id from codex_delegate_start"},
                    "limit": {"type": "integer", "description": "Max records when listing"},
                },
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
    message: Any,
    *,
    repo_root: Optional[Path | str] = None,
    allowed_roots: Optional[Sequence[Path | str]] = None,
    git_runner: Optional[GitRunner] = None,
    subprocess_runner: Optional[SubprocessRunner] = None,
    which: Optional[WhichFn] = None,
    audit_stream: Optional[TextIO] = None,
    principal: str = "local-dev",
) -> Optional[dict[str, Any]]:
    """Handle one JSON-RPC message. Notifications (no id) return None.

    Never raises. Batch requests (JSON arrays) are refused with -32600 —
    this server is single-request only. Hostile ``params`` / ``arguments``
    shapes degrade to structured tool errors rather than crashes.
    """
    # Batch requests (JSON-RPC arrays) and non-object frames.
    if isinstance(message, list):
        return {
            "jsonrpc": "2.0",
            "id": None,
            "error": {
                "code": -32600,
                "message": "Batch requests are not supported",
            },
        }
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
        params = message.get("params")
        if params is None:
            params = {}
        name = params.get("name") if isinstance(params, Mapping) else None
        raw_args = params.get("arguments") if isinstance(params, Mapping) else None
        if isinstance(raw_args, Mapping):
            arguments: Any = raw_args
        elif raw_args is None:
            arguments = {}
        else:
            # arguments as list/string/number — not a tool-args object.
            result = structured_error(
                "TOOL_ARGUMENTS_INVALID",
                "tools/call arguments must be a JSON object",
            )
            return {"jsonrpc": "2.0", "id": msg_id, "result": _tool_result_payload(result)}
        # Bound enormous argument blobs (already parsed; size is a honesty/DoS gate).
        try:
            encoded = json.dumps(arguments, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            result = structured_error(
                "TOOL_ARGUMENTS_INVALID",
                "tools/call arguments are not JSON-serialisable",
            )
            return {"jsonrpc": "2.0", "id": msg_id, "result": _tool_result_payload(result)}
        if len(encoded.encode("utf-8", errors="replace")) > MAX_RPC_FRAME_BYTES:
            result = structured_error(
                "TOOL_ARGUMENTS_TOO_LARGE",
                f"tools/call arguments exceed {MAX_RPC_FRAME_BYTES} bytes",
            )
            return {"jsonrpc": "2.0", "id": msg_id, "result": _tool_result_payload(result)}
        if not name:
            result = structured_error("TOOL_UNKNOWN", "missing tool name")
        else:
            result = handle_tool_call(
                str(name),
                arguments,
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
            raw_len = first.split(":", 1)[1].strip() if ":" in first else ""
            try:
                length = int(raw_len)
            except ValueError:
                # Non-numeric Content-Length: drop the frame, keep serving.
                _drain_headers(inn)
                continue
            # Negative would make read(-n) mean "read all" on TextIO — refuse.
            # Oversize frames are refused so a hostile client cannot force a
            # multi-megabyte allocation; the connection is abandoned because
            # skipping the body would desynchronise the stream.
            if length < 0 or length > MAX_RPC_FRAME_BYTES:
                _drain_headers(inn)
                _write_message(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {
                            "code": -32600,
                            "message": (
                                f"Content-Length out of bounds (0..{MAX_RPC_FRAME_BYTES})"
                            ),
                        },
                    },
                    out,
                    framed=True,
                )
                return
            while True:
                line = inn.readline()
                if line in ("", "\n", "\r\n"):
                    break
                if not line:
                    return
            body = inn.read(length) if length > 0 else ""
            if length > 0 and not body:
                break
            try:
                message = json.loads(body) if body else None
            except json.JSONDecodeError:
                continue
            if message is None:
                continue
        else:
            framed_mode = False if framed_mode is None else framed_mode
            line = first.strip()
            if not line:
                continue
            if len(line.encode("utf-8", errors="replace")) > MAX_RPC_FRAME_BYTES:
                _write_message(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {
                            "code": -32600,
                            "message": f"Request exceeds {MAX_RPC_FRAME_BYTES} bytes",
                        },
                    },
                    out,
                    framed=False,
                )
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


def _drain_headers(inn: TextIO) -> None:
    """Consume header lines until the blank separator (or EOF)."""
    while True:
        line = inn.readline()
        if line in ("", "\n", "\r\n") or not line:
            return


def main() -> None:
    serve_stdio()


if __name__ == "__main__":
    main()
