from __future__ import annotations

import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import textwrap
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from codex_app_mcp.audit import sanitize_event
from codex_app_mcp.client import AppServerClient, RpcError
from codex_app_mcp.errors import GatewayError
from codex_app_mcp.events import EventBuffer, EventHub
from codex_app_mcp.gateway import CodexAppGateway
from codex_app_mcp.http_server import create_http_server
from codex_app_mcp.lane import collect_diff
from codex_app_mcp.policy import GatewayPolicy
from codex_app_mcp.protocol import _methods
from codex_app_mcp.scheduler import next_occurrence
from codex_app_mcp.server import (
    dispatch_jsonrpc,
    handle_jsonrpc,
    serve_stdio,
    tool_schemas,
)
from codex_app_mcp.state import StateStore


class FakeClient:
    def __init__(self) -> None:
        self.notification_handler = None
        self.server_request_handler = None
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.responses: list[tuple[Any, Any, Any]] = []
        self.running = True
        self.pid = 123
        self.stderr_tail: list[str] = []
        self.metadata = {
            "userAgent": "fake/1",
            "codexHome": "X:/fake",
            "platformFamily": "windows",
            "platformOs": "windows",
        }
        self.models = [
            {
                "id": "gpt-sol",
                "model": "gpt-sol",
                "isDefault": True,
                "supportedReasoningEfforts": [
                    {"reasoningEffort": "low"},
                    {"reasoningEffort": "high"},
                    {"reasoningEffort": "ultra"},
                ],
            },
            {
                "id": "gpt-luna",
                "model": "gpt-luna",
                "isDefault": False,
                "supportedReasoningEfforts": [
                    {"reasoningEffort": "low"},
                    {"reasoningEffort": "high"},
                ],
            },
        ]

    def start(self) -> dict[str, Any]:
        return self.metadata

    def close(self) -> None:
        self.running = False

    def respond(self, rpc_id: Any, result: Any, error: Any) -> None:
        self.responses.append((rpc_id, result, error))

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> Any:
        payload = dict(params or {})
        self.requests.append((method, payload))
        if method == "model/list":
            return {"data": self.models, "nextCursor": None}
        if method == "thread/start":
            return {
                "thread": {
                    "id": "thr-new",
                    "ephemeral": payload.get("ephemeral", False),
                }
            }
        if method in {"thread/resume", "thread/unarchive"}:
            return {"thread": {"id": payload["threadId"]}}
        if method == "thread/fork":
            return {"thread": {"id": "thr-fork"}}
        if method == "turn/start":
            return {"turn": {"id": "turn-new", "status": "inProgress"}}
        if method == "review/start":
            return {
                "turn": {"id": "turn-new", "status": "inProgress"},
                "reviewThreadId": payload["threadId"],
            }
        if method == "thread/list":
            return {"data": [], "nextCursor": None}
        if method == "thread/read":
            return {"thread": {"id": payload["threadId"], "status": {"type": "idle"}}}
        if method == "thread/goal/get":
            return {"goal": None}
        return {}


@pytest.fixture
def allowed_root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    return root


@pytest.fixture
def gateway(allowed_root: Path) -> tuple[CodexAppGateway, FakeClient]:
    client = FakeClient()
    app = CodexAppGateway(
        policy=GatewayPolicy(allowed_roots=(allowed_root.resolve(),)),
        client=client,  # type: ignore[arg-type]
        state_path=":memory:",
    )
    return app, client


def test_policy_fails_closed_without_roots(tmp_path: Path) -> None:
    policy = GatewayPolicy(allowed_roots=())
    with pytest.raises(GatewayError) as raised:
        policy.validate_cwd(str(tmp_path))
    assert raised.value.code == "ALLOWED_ROOTS_EMPTY"


def test_policy_confines_cwd(allowed_root: Path, tmp_path: Path) -> None:
    policy = GatewayPolicy(allowed_roots=(allowed_root.resolve(),))
    assert policy.validate_cwd(str(allowed_root)) == str(allowed_root.resolve())
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(GatewayError) as raised:
        policy.validate_cwd(str(outside))
    assert raised.value.code == "CWD_NOT_ALLOWED"


def test_full_access_is_opt_in(allowed_root: Path) -> None:
    policy = GatewayPolicy(allowed_roots=(allowed_root.resolve(),))
    with pytest.raises(GatewayError) as raised:
        policy.validate_sandbox("danger-full-access")
    assert raised.value.code == "FULL_ACCESS_DISABLED"


def test_thread_config_is_fail_closed_and_security_keys_are_never_allowed(
    allowed_root: Path,
) -> None:
    policy = GatewayPolicy(
        allowed_roots=(allowed_root.resolve(),),
        allowed_thread_config_keys=frozenset({"model_verbosity", "sandbox_mode"}),
    )
    assert policy.validate_thread_config({"model_verbosity": "low"}) == {
        "model_verbosity": "low"
    }
    with pytest.raises(GatewayError) as unknown:
        policy.validate_thread_config({"custom_key": True})
    assert unknown.value.code == "CONFIG_KEY_NOT_ALLOWED"
    with pytest.raises(GatewayError) as forbidden:
        policy.validate_thread_config({"sandbox_mode": "danger-full-access"})
    assert forbidden.value.code == "CONFIG_KEY_FORBIDDEN"


def test_nested_mcp_requires_server_and_tool_allowlists(allowed_root: Path) -> None:
    policy = GatewayPolicy(
        allowed_roots=(allowed_root.resolve(),),
        allowed_mcp_servers=frozenset({"crm"}),
        allowed_mcp_tools=frozenset({"crm/read"}),
    )
    assert policy.validate_nested_mcp("crm", "read") == ("crm", "read")
    with pytest.raises(GatewayError):
        policy.validate_nested_mcp("crm", "write")


def test_event_buffer_cursor_reset() -> None:
    buf = EventBuffer(max_size=2, hard_max_size=3)
    buf.append("a", {}, pinned=False)
    buf.append("b", {}, pinned=False)
    buf.append("c", {}, pinned=False)
    result = buf.poll(1, 10)
    assert result["cursorResetTo"] == 2
    assert [event["method"] for event in result["events"]] == ["b", "c"]


def test_event_hub_routes_notification_by_thread() -> None:
    hub = EventHub()
    hub.notification("turn/started", {"threadId": "thr-a", "turn": {"id": "t1"}})
    result = hub.poll("thr-a", cursor=1, limit=10)
    assert result["events"][0]["method"] == "turn/started"
    assert result["events"][0]["pinned"] is True


def test_event_hub_approval_round_trip() -> None:
    responses: list[tuple[Any, Any, Any]] = []
    hub = EventHub(action_timeout_seconds=10)
    hub.bind_responder(
        lambda rpc_id, result, error: responses.append((rpc_id, result, error))
    )
    hub.server_request(
        91,
        "item/commandExecution/requestApproval",
        {"threadId": "thr-a", "turnId": "turn-a", "command": "git status"},
    )
    action = hub.pending_actions("thr-a")[0]
    result = hub.respond(action["actionId"], decision="accept")
    assert result["resolved"] is True
    assert responses == [(91, {"decision": "accept"}, None)]


def test_current_time_request_is_answered_automatically() -> None:
    responses: list[tuple[Any, Any, Any]] = []
    hub = EventHub()
    hub.bind_responder(
        lambda rpc_id, result, error: responses.append((rpc_id, result, error))
    )
    before = int(time.time())
    hub.server_request(90, "currentTime/read", {"threadId": "thr-time"})
    after = int(time.time())
    assert len(responses) == 1
    assert responses[0][0] == 90
    assert responses[0][2] is None
    assert before <= responses[0][1]["currentTimeAt"] <= after
    assert hub.pending_actions("thr-time") == []


def test_invalid_approval_response_keeps_action_pending() -> None:
    hub = EventHub(action_timeout_seconds=10)
    hub.server_request(
        92,
        "item/fileChange/requestApproval",
        {"threadId": "thr-a", "turnId": "turn-a"},
    )
    action_id = hub.pending_actions("thr-a")[0]["actionId"]
    with pytest.raises(GatewayError):
        hub.respond(action_id, decision="bogus")
    assert hub.pending_actions("thr-a")[0]["actionId"] == action_id
    hub.close()


def test_dynamic_tool_request_is_exposed_for_round_trip() -> None:
    responses: list[tuple[Any, Any, Any]] = []
    hub = EventHub()
    hub.bind_responder(
        lambda rpc_id, result, error: responses.append((rpc_id, result, error))
    )
    hub.server_request(
        8,
        "item/tool/call",
        {"threadId": "thr-a", "tool": "lookup", "arguments": {"id": 1}},
    )
    pending = hub.pending_actions("thr-a")
    assert len(pending) == 1
    hub.respond(
        pending[0]["actionId"],
        response={
            "success": True,
            "contentItems": [{"type": "inputText", "text": "result"}],
        },
    )
    assert responses == [
        (
            8,
            {
                "success": True,
                "contentItems": [{"type": "inputText", "text": "result"}],
            },
            None,
        )
    ]


def test_permission_request_uses_granted_subset_response() -> None:
    responses: list[tuple[Any, Any, Any]] = []
    hub = EventHub()
    hub.bind_responder(
        lambda rpc_id, result, error: responses.append((rpc_id, result, error))
    )
    hub.server_request(
        9,
        "item/permissions/requestApproval",
        {"threadId": "thr-a", "permissions": {"network": ["example.com"]}},
    )
    action = hub.pending_actions("thr-a")[0]
    hub.respond(
        action["actionId"],
        response={"permissions": {"network": ["example.com"]}, "scope": "turn"},
    )
    assert responses[0][1]["scope"] == "turn"


def test_thread_start_validates_live_model_effort(
    gateway: tuple[CodexAppGateway, FakeClient], allowed_root: Path
) -> None:
    app, client = gateway
    result = app.thread(
        {
            "action": "start",
            "cwd": str(allowed_root),
            "model": "gpt-sol",
            "effort": "ultra",
            "sandbox": "workspace-write",
            "approvalPolicy": "never",
        }
    )
    assert result["threadId"] == "thr-new"
    method, params = next(item for item in client.requests if item[0] == "thread/start")
    assert method == "thread/start"
    assert params["config"]["model_reasoning_effort"] == "ultra"


def test_thread_start_rejects_model_effort_mismatch(
    gateway: tuple[CodexAppGateway, FakeClient], allowed_root: Path
) -> None:
    app, _ = gateway
    with pytest.raises(GatewayError) as raised:
        app.thread(
            {
                "action": "start",
                "cwd": str(allowed_root),
                "model": "gpt-luna",
                "effort": "ultra",
            }
        )
    assert raised.value.code == "EFFORT_NOT_SUPPORTED"
    assert raised.value.details["supportedEfforts"] == ["high", "low"]


def test_goal_start_creates_persisted_thread_and_activates_goal(
    gateway: tuple[CodexAppGateway, FakeClient], allowed_root: Path
) -> None:
    app, client = gateway
    result = app.goal(
        {
            "action": "start",
            "cwd": str(allowed_root),
            "objective": "Finish the migration",
            "tokenBudget": 40000,
            "model": "gpt-sol",
            "effort": "high",
        }
    )
    assert result["threadId"] == "thr-new"
    methods = [method for method, _ in client.requests]
    assert methods[-2:] == ["thread/goal/clear", "thread/goal/set"]
    set_params = client.requests[-1][1]
    assert set_params == {
        "threadId": "thr-new",
        "objective": "Finish the migration",
        "status": "active",
        "tokenBudget": 40000,
    }


def test_turn_start_uses_protocol_sandbox_shape(
    gateway: tuple[CodexAppGateway, FakeClient],
) -> None:
    app, client = gateway
    result = app.turn(
        {
            "action": "start",
            "threadId": "thr-a",
            "prompt": "Inspect the repo",
            "sandbox": "workspace-write",
            "model": "gpt-sol",
            "effort": "high",
        }
    )
    assert result["turnId"] == "turn-new"
    params = client.requests[-1][1]
    assert params["sandboxPolicy"] == {"type": "workspaceWrite"}
    assert params["effort"] == "high"


def test_turn_steer_requires_expected_turn_id(
    gateway: tuple[CodexAppGateway, FakeClient],
) -> None:
    app, client = gateway
    app.turn(
        {
            "action": "steer",
            "threadId": "thr-a",
            "turnId": "turn-a",
            "prompt": "Focus on tests",
        }
    )
    assert client.requests[-1] == (
        "turn/steer",
        {
            "threadId": "thr-a",
            "expectedTurnId": "turn-a",
            "input": [{"type": "text", "text": "Focus on tests"}],
        },
    )


def test_thread_loaded_does_not_require_thread_id(
    gateway: tuple[CodexAppGateway, FakeClient],
) -> None:
    app, client = gateway
    result = app.thread({"action": "loaded"})
    assert result["ok"] is True
    assert client.requests[-1] == ("thread/loaded/list", {})


def test_unarchive_retries_only_archive_materialization_race(
    allowed_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class ArchiveClient(FakeClient):
        attempts = 0

        def request(
            self,
            method: str,
            params: dict[str, Any] | None = None,
            *,
            timeout: float | None = None,
        ) -> Any:
            if method == "thread/unarchive":
                self.attempts += 1
                if self.attempts < 3:
                    raise RpcError(-32600, "no archived rollout found for thread id")
            return super().request(method, params, timeout=timeout)

    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    client = ArchiveClient()
    app = CodexAppGateway(
        policy=GatewayPolicy(allowed_roots=(allowed_root.resolve(),)),
        client=client,  # type: ignore[arg-type]
        state_path=":memory:",
    )
    result = app.thread({"action": "unarchive", "threadId": "thr-race"})
    assert result["ok"] is True
    assert client.attempts == 3
    app.close()


def test_unarchive_recovers_when_move_succeeded_but_readback_failed(
    allowed_root: Path,
) -> None:
    class PartialUnarchiveClient(FakeClient):
        def request(
            self,
            method: str,
            params: dict[str, Any] | None = None,
            *,
            timeout: float | None = None,
        ) -> Any:
            if method == "thread/unarchive":
                raise RpcError(
                    -32603,
                    "failed to unarchive session: failed to read unarchived thread",
                )
            return super().request(method, params, timeout=timeout)

    app = CodexAppGateway(
        policy=GatewayPolicy(allowed_roots=(allowed_root.resolve(),)),
        client=PartialUnarchiveClient(),  # type: ignore[arg-type]
        state_path=":memory:",
    )
    result = app.thread({"action": "unarchive", "threadId": "thr-partial"})
    assert result["ok"] is True
    assert result["result"]["recovered"] is True
    assert result["result"]["readback"]["thread"]["id"] == "thr-partial"
    app.close()


def test_readonly_rpc_rejects_mutation(
    gateway: tuple[CodexAppGateway, FakeClient],
) -> None:
    app, _ = gateway
    with pytest.raises(GatewayError) as raised:
        app.readonly_rpc({"method": "config/value/write", "params": {}})
    assert raised.value.code == "RPC_METHOD_NOT_ALLOWED"


def test_raw_rpc_requires_method_allowlist_and_unsafe_opt_in(
    allowed_root: Path,
) -> None:
    client = FakeClient()
    app = CodexAppGateway(
        policy=GatewayPolicy(
            allowed_roots=(allowed_root.resolve(),),
            allowed_rpc_methods=frozenset({"thread/delete"}),
            allow_unsafe_rpc=True,
        ),
        client=client,  # type: ignore[arg-type]
    )
    result = app.rpc({"method": "thread/delete", "params": {"threadId": "thr-a"}})
    assert result["ok"] is True
    assert client.requests[-1][0] == "thread/delete"

    blocked = CodexAppGateway(
        policy=GatewayPolicy(
            allowed_roots=(allowed_root.resolve(),),
            allowed_rpc_methods=frozenset({"thread/delete"}),
        ),
        client=FakeClient(),  # type: ignore[arg-type]
    )
    with pytest.raises(GatewayError) as raised:
        blocked.rpc({"method": "thread/delete", "params": {"threadId": "thr-a"}})
    assert raised.value.code == "UNSAFE_RPC_DISABLED"


def test_review_command_process_and_filesystem_shapes(
    allowed_root: Path,
) -> None:
    client = FakeClient()
    app = CodexAppGateway(
        policy=GatewayPolicy(
            allowed_roots=(allowed_root.resolve(),),
            allow_full_access=True,
            allow_unsafe_rpc=True,
        ),
        client=client,  # type: ignore[arg-type]
    )
    reviewed = app.review(
        {
            "threadId": "thr-a",
            "baseBranch": "main",
            "delivery": "detached",
        }
    )
    assert reviewed["turnId"] == "turn-new"
    assert client.requests[-1] == (
        "review/start",
        {
            "threadId": "thr-a",
            "delivery": "detached",
            "target": {"type": "baseBranch", "branch": "main"},
        },
    )

    app.command(
        {
            "action": "exec",
            "command": ["git", "status", "--short"],
            "cwd": str(allowed_root),
            "sandbox": "danger-full-access",
        }
    )
    assert client.requests[-1][1]["sandboxPolicy"] == {"type": "dangerFullAccess"}

    app.process(
        {
            "action": "spawn",
            "processHandle": "probe-1",
            "command": ["python", "-V"],
            "cwd": str(allowed_root),
        }
    )
    assert client.requests[-1][0] == "process/spawn"

    target = allowed_root / "created.txt"
    app.filesystem({"action": "write", "path": str(target), "text": "hello"})
    assert client.requests[-1][0] == "fs/writeFile"
    assert client.requests[-1][1]["dataBase64"] == "aGVsbG8="


def test_mcp_call_obeys_allowlist(allowed_root: Path) -> None:
    client = FakeClient()
    app = CodexAppGateway(
        policy=GatewayPolicy(
            allowed_roots=(allowed_root.resolve(),),
            allowed_mcp_servers=frozenset({"crm"}),
            allowed_mcp_tools=frozenset({"crm/read"}),
        ),
        client=client,  # type: ignore[arg-type]
    )
    result = app.mcp_call({"server": "crm", "tool": "read", "arguments": {"id": 1}})
    assert result["ok"] is True
    assert client.requests[-1][0] == "mcpServer/tool/call"


def test_tool_schema_is_small_and_strict() -> None:
    tools = tool_schemas()
    assert len(tools) == 24
    assert "codex_app_doctor" in {tool["name"] for tool in tools}
    assert len({tool["name"] for tool in tools}) == len(tools)
    assert all(tool["inputSchema"]["additionalProperties"] is False for tool in tools)


def test_mcp_initialize_and_tools_list(
    gateway: tuple[CodexAppGateway, FakeClient],
) -> None:
    app, _ = gateway
    initialized = handle_jsonrpc({"id": 1, "method": "initialize", "params": {}}, app)
    assert initialized["result"]["serverInfo"]["name"] == "codex-app-mcp"
    listed = handle_jsonrpc({"id": 2, "method": "tools/list", "params": {}}, app)
    assert len(listed["result"]["tools"]) == 24


def test_mcp_jsonrpc_batch(gateway: tuple[CodexAppGateway, FakeClient]) -> None:
    app, _ = gateway
    result = dispatch_jsonrpc(
        [
            {"id": 1, "method": "ping", "params": {}},
            {"method": "notifications/initialized", "params": {}},
            {"id": 2, "method": "tools/list", "params": {}},
        ],
        app,
    )
    assert isinstance(result, list)
    assert [row["id"] for row in result] == [1, 2]


def test_current_mcp_server_discover(
    gateway: tuple[CodexAppGateway, FakeClient],
) -> None:
    app, _ = gateway
    result = handle_jsonrpc(
        {"id": 1, "method": "server/discover", "params": {}},
        app,
    )
    assert result["result"]["supportedVersions"][0] == "2026-07-28"
    assert result["result"]["capabilities"] == {"tools": {}}


def test_mcp_stdio_does_not_start_app_server_for_tools_list(
    gateway: tuple[CodexAppGateway, FakeClient],
) -> None:
    app, client = gateway
    source = io.StringIO(
        json.dumps({"id": 1, "method": "initialize", "params": {}})
        + "\n"
        + json.dumps({"id": 2, "method": "tools/list", "params": {}})
        + "\n"
    )
    sink = io.StringIO()
    serve_stdio(stdin=source, stdout=sink, gateway=app)
    rows = [json.loads(line) for line in sink.getvalue().splitlines()]
    assert rows[0]["result"]["serverInfo"]["name"] == "codex-app-mcp"
    assert len(rows[1]["result"]["tools"]) == 24
    assert client.requests == []


def test_raw_client_handshake_request_notification_and_server_request(
    tmp_path: Path,
) -> None:
    script = tmp_path / "fake_app_server.py"
    script.write_text(
        textwrap.dedent(
            """
            import json
            import sys

            for line in sys.stdin:
                msg = json.loads(line)
                method = msg.get("method")
                if method == "initialize":
                    print(json.dumps({"id": msg["id"], "result": {
                        "userAgent": "fake-app-server/1",
                        "codexHome": "/tmp/fake",
                        "platformFamily": "test",
                        "platformOs": "test"
                    }}), flush=True)
                elif method == "model/list":
                    print(json.dumps({"method": "turn/started", "params": {
                        "threadId": "thr-fake", "turn": {"id": "turn-fake"}
                    }}), flush=True)
                    print(json.dumps({"id": msg["id"], "result": {
                        "data": [], "nextCursor": None
                    }}), flush=True)
                    print(json.dumps({"id": "approval-1", "method":
                        "item/commandExecution/requestApproval", "params": {
                            "threadId": "thr-fake", "turnId": "turn-fake"
                        }}), flush=True)
                elif "id" in msg and ("result" in msg or "error" in msg):
                    pass
            """
        ),
        encoding="utf-8",
    )
    notifications: list[tuple[str, Any]] = []
    requests: list[tuple[Any, str, Any]] = []
    client = AppServerClient(
        launch_args_override=(sys.executable, "-u", str(script)),
        notification_handler=lambda method, params: notifications.append(
            (method, params)
        ),
        server_request_handler=lambda rpc_id, method, params: requests.append(
            (rpc_id, method, params)
        ),
        request_timeout=5,
    )
    try:
        metadata = client.start()
        assert metadata["userAgent"] == "fake-app-server/1"
        assert client.request("model/list", {}) == {"data": [], "nextCursor": None}
        deadline = time.monotonic() + 2
        while not requests and time.monotonic() < deadline:
            time.sleep(0.01)
        assert notifications[0][0] == "turn/started"
        assert requests[0][0] == "approval-1"
        client.respond("approval-1", {"decision": "decline"}, None)
    finally:
        client.close()


def test_raw_client_surfaces_rpc_error(tmp_path: Path) -> None:
    script = tmp_path / "fake_error_server.py"
    script.write_text(
        textwrap.dedent(
            """
            import json
            import sys
            for line in sys.stdin:
                msg = json.loads(line)
                if msg.get("method") == "initialize":
                    print(json.dumps({"id": msg["id"], "result": {}}), flush=True)
                elif "id" in msg:
                    print(json.dumps({"id": msg["id"], "error": {
                        "code": -32601, "message": "missing"
                    }}), flush=True)
            """
        ),
        encoding="utf-8",
    )
    client = AppServerClient(
        launch_args_override=(sys.executable, "-u", str(script)),
        request_timeout=5,
    )
    try:
        client.start()
        with pytest.raises(RpcError) as raised:
            client.request("no/such/method", {})
        assert raised.value.code == -32601
    finally:
        client.close()


def test_windows_npm_shim_resolves_to_node_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows npm shim resolution must pick the native codex.exe payload."""
    import pathlib as _pl

    shim = tmp_path / "codex.CMD"
    script = tmp_path / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
    script.parent.mkdir(parents=True)
    script.write_text("// fake", encoding="utf-8")
    native = (
        script.parent.parent
        / "node_modules"
        / "@openai"
        / "codex-win32-x64"
        / "vendor"
        / "x86_64-pc-windows-msvc"
        / "bin"
        / "codex.exe"
    )
    native.parent.mkdir(parents=True)
    native.write_bytes(b"fake")
    shim.write_text(
        '"%_prog%" "%dp0%\\node_modules\\@openai\\codex\\bin\\codex.js" %*',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        shutil,
        "which",
        lambda value: (
            str(tmp_path / "node.exe") if value in {"node", "node.exe"} else None
        ),
    )
    (tmp_path / "node.exe").write_bytes(b"fake")

    original_name = os.name
    original_new = _pl.Path.__new__

    def _safe_new(cls, *args, **kwargs):
        if cls is _pl.Path:
            cls = _pl.PosixPath
        return original_new(cls, *args, **kwargs)

    try:
        os.name = "nt"
        _pl.Path.__new__ = staticmethod(_safe_new)  # type: ignore[method-assign]
        resolved = AppServerClient._resolve_launch_command(shim)
    finally:
        os.name = original_name
        _pl.Path.__new__ = original_new  # type: ignore[method-assign]

    assert resolved == [str(native.resolve())]

def test_goal_start_only_resumes_unloaded_existing_thread(
    gateway: tuple[CodexAppGateway, FakeClient],
) -> None:
    app, client = gateway
    app.goal(
        {
            "action": "start",
            "threadId": "thr-a",
            "objective": "Continue safely",
        }
    )
    methods = [method for method, _ in client.requests]
    assert "thread/read" in methods
    assert "thread/resume" not in methods


def test_state_store_marks_interrupted_jobs_orphaned(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    first = StateStore(path)
    record = first.create("goal", {"action": "start", "objective": "Persist"})
    first.transition(record["jobId"], "running", thread_id="thr-persisted")
    first.close()

    second = StateStore(path)
    try:
        assert second.recover_interrupted() == 1
        recovered = second.get(record["jobId"], include_history=True)
        assert recovered is not None
        assert recovered["state"] == "orphaned"
        assert recovered["threadId"] == "thr-persisted"
        assert recovered["history"][-1]["details"] == {"reason": "gateway_restart"}
    finally:
        second.close()


def test_state_store_finds_active_lane() -> None:
    store = StateStore(":memory:")
    try:
        first = store.create(
            "turn",
            {"threadId": "thr-a", "prompt": "work", "_lane": "codex/one"},
        )
        assert store.find_active_lane("codex/one")["jobId"] == first["jobId"]
        store.transition(first["jobId"], "succeeded")
        assert store.find_active_lane("codex/one") is None
    finally:
        store.close()


def test_state_store_lane_exclusion_is_cross_connection(tmp_path: Path) -> None:
    path = tmp_path / "shared.sqlite3"
    first = StateStore(path)
    second = StateStore(path)
    try:
        created = first.create(
            "turn",
            {"threadId": "thr-a", "prompt": "one", "_lane": "codex/shared"},
        )
        with pytest.raises(GatewayError) as raised:
            second.create(
                "turn",
                {"threadId": "thr-b", "prompt": "two", "_lane": "codex/shared"},
            )
        assert raised.value.code == "LANE_BUSY"
        assert raised.value.details["jobId"] == created["jobId"]
    finally:
        second.close()
        first.close()


def test_state_store_lane_resume_conflict_is_structured() -> None:
    store = StateStore(":memory:")
    try:
        old = store.create(
            "turn",
            {"threadId": "thr-old", "prompt": "old", "_lane": "codex/shared"},
        )
        store.transition(old["jobId"], "orphaned")
        active = store.create(
            "turn",
            {"threadId": "thr-new", "prompt": "new", "_lane": "codex/shared"},
        )
        with pytest.raises(GatewayError) as raised:
            store.transition(old["jobId"], "queued")
        assert raised.value.code == "LANE_BUSY"
        assert raised.value.details["jobId"] == active["jobId"]
    finally:
        store.close()


def test_state_store_migrates_v1_database(tmp_path: Path) -> None:
    path = tmp_path / "old.sqlite3"
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE jobs (
            job_id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            state TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            request_json TEXT NOT NULL,
            thread_id TEXT,
            turn_id TEXT,
            result_json TEXT,
            error TEXT
        );
        CREATE TABLE job_transitions (
            transition_id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            at TEXT NOT NULL,
            state TEXT NOT NULL,
            details_json TEXT
        );
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """
    )
    db.commit()
    db.close()
    store = StateStore(path)
    try:
        columns = {row["name"] for row in store._db.execute("PRAGMA table_info(jobs)")}
        assert "lane" in columns
        record = store.create(
            "turn",
            {"threadId": "thr-a", "prompt": "work", "_lane": "codex/migrated"},
        )
        assert record["lane"] == "codex/migrated"
    finally:
        store.close()


def test_job_manager_rejects_a_second_active_lane(
    gateway: tuple[CodexAppGateway, FakeClient],
) -> None:
    app, _ = gateway
    first = app.job(
        {
            "action": "start",
            "kind": "turn",
            "request": {
                "threadId": "thr-a",
                "prompt": "first",
                "_lane": "codex/locked",
            },
        }
    )
    assert first["job"]["state"] in {"queued", "running"}
    with pytest.raises(GatewayError) as raised:
        app.job(
            {
                "action": "start",
                "kind": "turn",
                "request": {
                    "threadId": "thr-b",
                    "prompt": "second",
                    "_lane": "codex/locked",
                },
            }
        )
    assert raised.value.code == "LANE_BUSY"
    app.close()


def test_doctor_uses_only_app_server_reads(
    gateway: tuple[CodexAppGateway, FakeClient],
) -> None:
    app, client = gateway
    result = app.doctor()
    assert result["ok"] is True
    methods = [method for method, _ in client.requests]
    assert methods == [
        "account/read",
        "configRequirements/read",
        "windowsSandbox/readiness",
    ]


def test_audit_sanitizer_never_emits_prompt_or_secret() -> None:
    result = sanitize_event(
        {
            "tool": "codex_app_turn",
            "action": "start",
            "prompt": "must never be emitted",
            "token": "must never be emitted",
        }
    )
    assert result == {"tool": "codex_app_turn", "action": "start"}
    redacted = sanitize_event(
        {"tool": "codex_app_status", "cwd": r"C:\Users\x\.codex\auth.json"}
    )
    assert redacted["outcome"] == "audit_suppressed"
    assert redacted["error"] == "AUDIT_REDACTED"


def test_background_goal_job_reaches_durable_success(
    gateway: tuple[CodexAppGateway, FakeClient],
    allowed_root: Path,
) -> None:
    app, client = gateway

    original_request = client.request

    def completed_goal(
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> Any:
        if method == "thread/goal/get":
            return {
                "goal": {
                    "objective": "Finish",
                    "status": "complete",
                    "tokenBudget": 1000,
                }
            }
        return original_request(method, params, timeout=timeout)

    client.request = completed_goal  # type: ignore[method-assign]
    started = app.job(
        {
            "action": "start",
            "kind": "goal",
            "request": {
                "cwd": str(allowed_root),
                "objective": "Finish",
                "tokenBudget": 1000,
            },
        }
    )
    job_id = started["job"]["jobId"]
    deadline = time.monotonic() + 3
    current = None
    while time.monotonic() < deadline:
        current = app.job({"action": "get", "jobId": job_id})["job"]
        if current["state"] == "succeeded":
            break
        time.sleep(0.02)
    assert current is not None
    assert current["state"] == "succeeded"
    assert current["threadId"] == "thr-new"
    with pytest.raises(GatewayError) as resume:
        app.job({"action": "resume", "jobId": job_id})
    assert resume.value.code == "JOB_NOT_RESUMABLE"
    app.close()
    app.close()


def test_orphaned_goal_job_can_be_cancelled(
    gateway: tuple[CodexAppGateway, FakeClient],
) -> None:
    app, _ = gateway
    manager = app._job_manager()
    record = manager.store.create(
        "goal",
        {"action": "start", "objective": "Old goal", "threadId": "thr-old"},
    )
    manager.store.transition(
        record["jobId"],
        "orphaned",
        thread_id="thr-old",
    )
    cancelled = app.job({"action": "cancel", "jobId": record["jobId"]})
    assert cancelled["ok"] is True
    assert cancelled["job"]["state"] == "cancelled"


def test_app_server_lane_prepares_lists_and_collects_diff(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    lanes = tmp_path / "lanes"
    repo.mkdir()

    def git(*args: str, cwd: Path = repo) -> None:
        subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    git("init")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    git("add", "base.txt")
    git("commit", "-m", "base")

    client = FakeClient()
    app = CodexAppGateway(
        policy=GatewayPolicy(
            allowed_roots=(tmp_path.resolve(),),
            allow_full_access=True,
            allow_unsafe_rpc=True,
        ),
        client=client,  # type: ignore[arg-type]
        state_path=":memory:",
    )
    prepared = app.lane(
        {
            "action": "prepare",
            "repoRoot": str(repo),
            "lanesParent": str(lanes),
            "lane": "migration",
        }
    )
    worktree = Path(prepared["worktreePath"])
    assert prepared["lane"] == "codex/migration"
    assert worktree.is_dir()
    assert (
        app.lane({"action": "list", "repoRoot": str(repo)})["lanes"][0]["lane"]
        == "codex/migration"
    )

    (worktree / "new.txt").write_text("new\n", encoding="utf-8")
    diff = collect_diff(worktree)
    assert diff["changedFiles"] == ["new.txt"]
    via_tool = app.lane(
        {
            "action": "diff",
            "repoRoot": str(repo),
            "lanesParent": str(lanes),
            "lane": "migration",
        }
    )
    assert via_tool["changedFileCount"] == 1
    app.close()


def test_app_server_lane_background_turn_is_durable(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    lanes = tmp_path / "lanes"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=repo, check=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "base.txt"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "base"],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
    )
    client = FakeClient()
    app = CodexAppGateway(
        policy=GatewayPolicy(
            allowed_roots=(tmp_path.resolve(),),
            allow_full_access=True,
            allow_unsafe_rpc=True,
        ),
        client=client,  # type: ignore[arg-type]
        state_path=":memory:",
    )
    started = app.lane(
        {
            "action": "start",
            "repoRoot": str(repo),
            "lanesParent": str(lanes),
            "lane": "background",
            "goal": "Inspect the lane",
            "sandbox": "danger-full-access",
            "approvalPolicy": "never",
        }
    )
    job_id = started["job"]["jobId"]
    deadline = time.monotonic() + 2
    while not any(method == "turn/start" for method, _ in client.requests):
        assert time.monotonic() < deadline
        time.sleep(0.01)
    assert client.notification_handler is not None
    client.notification_handler(
        "turn/completed",
        {
            "threadId": started["threadId"],
            "turn": {"id": "turn-new", "status": "completed"},
        },
    )
    state = ""
    while time.monotonic() < deadline:
        polled = app.lane({"action": "poll", "jobId": job_id})
        state = polled["job"]["state"]
        if state == "succeeded":
            break
        time.sleep(0.02)
    assert state == "succeeded"
    app.close()


def test_http_transport_health_auth_and_tools_list(
    gateway: tuple[CodexAppGateway, FakeClient],
) -> None:
    app, _ = gateway
    server = create_http_server(
        host="127.0.0.1",
        port=0,
        gateway=app,
        token="probe-secret",
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(f"{base}/healthz", timeout=2) as response:
            assert json.load(response)["ok"] is True
        unauthorized = Request(
            f"{base}/mcp",
            data=json.dumps({"id": 1, "method": "tools/list", "params": {}}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(HTTPError) as raised:
            urlopen(unauthorized, timeout=2)
        assert raised.value.code == 401
        authorized = Request(
            f"{base}/mcp",
            data=json.dumps({"id": 2, "method": "tools/list", "params": {}}).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer probe-secret",
            },
        )
        with urlopen(authorized, timeout=2) as response:
            payload = json.load(response)
        assert payload["id"] == 2
        assert len(payload["result"]["tools"]) == 24
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_http_non_loopback_requires_token(
    gateway: tuple[CodexAppGateway, FakeClient],
) -> None:
    app, _ = gateway
    with pytest.raises(ValueError):
        create_http_server(host="0.0.0.0", port=0, gateway=app, token="")


def test_http_token_file_and_inflight_configuration(
    gateway: tuple[CodexAppGateway, FakeClient],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app, _ = gateway
    token_path = tmp_path / "http-token"
    token_path.write_text("file-secret\n", encoding="utf-8")
    monkeypatch.setenv("CODEX_APP_MCP_HTTP_TOKEN_FILE", str(token_path))
    monkeypatch.setenv("CODEX_APP_MCP_HTTP_MAX_INFLIGHT", "7")
    server = create_http_server(host="127.0.0.1", port=0, gateway=app)
    try:
        assert server.token == "file-secret"
        assert server.inflight._initial_value == 7
    finally:
        server.server_close()


def test_http_token_sources_and_inflight_limit_are_validated(
    gateway: tuple[CodexAppGateway, FakeClient],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app, _ = gateway
    token_path = tmp_path / "http-token"
    token_path.write_text("file-secret", encoding="utf-8")
    monkeypatch.setenv("CODEX_APP_MCP_HTTP_TOKEN", "environment-secret")
    monkeypatch.setenv("CODEX_APP_MCP_HTTP_TOKEN_FILE", str(token_path))
    with pytest.raises(ValueError, match="only one"):
        create_http_server(host="127.0.0.1", port=0, gateway=app)

    monkeypatch.delenv("CODEX_APP_MCP_HTTP_TOKEN")
    monkeypatch.delenv("CODEX_APP_MCP_HTTP_TOKEN_FILE")
    with pytest.raises(ValueError, match="between 1 and 256"):
        create_http_server(
            host="127.0.0.1",
            port=0,
            gateway=app,
            max_inflight=0,
        )


def test_http_current_protocol_headers_are_validated(
    gateway: tuple[CodexAppGateway, FakeClient],
) -> None:
    app, _ = gateway
    server = create_http_server(host="127.0.0.1", port=0, gateway=app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}/mcp"
    try:
        params = {
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                "io.modelcontextprotocol/clientInfo": {
                    "name": "pytest",
                    "version": "1",
                },
                "io.modelcontextprotocol/clientCapabilities": {},
            }
        }
        request = Request(
            endpoint,
            data=json.dumps(
                {"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": params}
            ).encode(),
            headers={
                "Content-Type": "application/json",
                "MCP-Protocol-Version": "2026-07-28",
                "Mcp-Method": "tools/list",
            },
        )
        with urlopen(request, timeout=2) as response:
            payload = json.load(response)
        assert len(payload["result"]["tools"]) == 24

        mismatch = Request(
            endpoint,
            data=json.dumps(
                {"jsonrpc": "2.0", "id": 4, "method": "tools/list", "params": params}
            ).encode(),
            headers={
                "Content-Type": "application/json",
                "MCP-Protocol-Version": "2026-07-28",
                "Mcp-Method": "tools/call",
            },
        )
        with pytest.raises(HTTPError) as raised:
            urlopen(mismatch, timeout=2)
        assert raised.value.code == 400
        error = json.loads(raised.value.read())
        assert error["error"]["code"] == -32602
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_protocol_schema_index_extracts_method_contract() -> None:
    indexed = _methods(
        {
            "definitions": {
                "ConfigReadParams": {
                    "type": "object",
                    "properties": {"includeLayers": {"type": "boolean"}},
                }
            },
            "oneOf": [
                {
                    "title": "ConfigRead",
                    "description": "read config",
                    "properties": {
                        "method": {"enum": ["config/read"]},
                        "params": {"$ref": "#/definitions/ConfigReadParams"},
                    },
                }
            ],
        }
    )
    assert indexed["config/read"]["title"] == "ConfigRead"
    assert indexed["config/read"]["paramsRef"] == "#/definitions/ConfigReadParams"
    assert indexed["config/read"]["paramsSchema"]["properties"] == {
        "includeLayers": {"type": "boolean"}
    }


def test_admin_surface_is_typed_and_mutations_are_gated(allowed_root: Path) -> None:
    class Protocol:
        @staticmethod
        def supports(method: str) -> bool:
            return method in {"account/usage/read", "remoteControl/enable"}

    client = FakeClient()
    app = CodexAppGateway(
        policy=GatewayPolicy(allowed_roots=(allowed_root.resolve(),)),
        client=client,  # type: ignore[arg-type]
        state_path=":memory:",
    )
    app._protocol = Protocol()  # type: ignore[assignment]
    result = app.admin({"operation": "account.usage"})
    assert result["method"] == "account/usage/read"
    with pytest.raises(GatewayError) as raised:
        app.admin({"operation": "remote.enable"})
    assert raised.value.code == "UNSAFE_RPC_DISABLED"
    app.close()


def test_rrule_timezone_daily_and_once() -> None:
    start = datetime(2026, 7, 29, 16, 0, tzinfo=timezone.utc)
    first = next_occurrence(
        "RRULE:FREQ=DAILY;BYHOUR=9;BYMINUTE=30",
        start=start,
        after=start,
        timezone_name="America/Los_Angeles",
    )
    assert first == datetime(2026, 7, 29, 16, 30, tzinfo=timezone.utc)
    assert (
        next_occurrence(
            "RRULE:FREQ=ONCE",
            start=start,
            after=start,
            timezone_name="UTC",
        )
        is None
    )


def test_schedule_is_idempotent_and_manual_trigger_is_durable(
    allowed_root: Path,
) -> None:
    app = CodexAppGateway(
        policy=GatewayPolicy(allowed_roots=(allowed_root.resolve(),)),
        client=FakeClient(),  # type: ignore[arg-type]
        state_path=":memory:",
    )
    payload = {
        "action": "create",
        "idempotencyKey": "daily-probe",
        "name": "Daily probe",
        "timezone": "UTC",
        "rrule": "RRULE:FREQ=DAILY;BYHOUR=23;BYMINUTE=59",
        "startAt": "2099-01-01T23:59:00+00:00",
        "request": {
            "kind": "turn",
            "request": {
                "threadId": "thr-existing",
                "prompt": "scheduled probe",
            },
        },
        "retryCount": 2,
        "retryBackoffSeconds": 1,
    }
    created = app.schedule(payload)
    duplicate = app.schedule(payload)
    assert created["created"] is True
    assert duplicate["created"] is False
    assert duplicate["schedule"]["scheduleId"] == created["schedule"]["scheduleId"]
    triggered = app.schedule(
        {
            "action": "trigger",
            "scheduleId": created["schedule"]["scheduleId"],
        }
    )
    assert triggered["run"]["state"] == "running"
    assert triggered["run"]["jobId"].startswith("job-")
    runs = app.schedule(
        {
            "action": "runs",
            "scheduleId": created["schedule"]["scheduleId"],
        }
    )
    assert runs["runs"][0]["runId"] == triggered["run"]["runId"]
    with pytest.raises(GatewayError) as raised:
        app.schedule(
            {
                "action": "trigger",
                "scheduleId": created["schedule"]["scheduleId"],
            }
        )
    assert raised.value.code == "SCHEDULE_BUSY"
    app.close()


def test_schedule_idempotency_conflict_is_explicit(allowed_root: Path) -> None:
    app = CodexAppGateway(
        policy=GatewayPolicy(allowed_roots=(allowed_root.resolve(),)),
        client=FakeClient(),  # type: ignore[arg-type]
        state_path=":memory:",
    )
    base = {
        "action": "create",
        "idempotencyKey": "same-key",
        "name": "First",
        "timezone": "UTC",
        "rrule": "RRULE:FREQ=ONCE",
        "startAt": "2099-01-01T00:00:00+00:00",
        "request": {
            "kind": "turn",
            "request": {"threadId": "thr-existing", "prompt": "first"},
        },
    }
    app.schedule(base)
    with pytest.raises(GatewayError) as raised:
        app.schedule({**base, "name": "Different"})
    assert raised.value.code == "IDEMPOTENCY_CONFLICT"
    app.close()


def test_due_schedule_launches_without_an_inbound_tick(
    allowed_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODEX_APP_MCP_SCHEDULER_POLL_SECONDS", "0.2")
    app = CodexAppGateway(
        policy=GatewayPolicy(allowed_roots=(allowed_root.resolve(),)),
        client=FakeClient(),  # type: ignore[arg-type]
        state_path=":memory:",
    )
    start_at = (datetime.now(timezone.utc) + timedelta(seconds=0.3)).isoformat()
    created = app.schedule(
        {
            "action": "create",
            "idempotencyKey": "automatic-once",
            "name": "Automatic once",
            "timezone": "UTC",
            "rrule": "RRULE:FREQ=ONCE",
            "startAt": start_at,
            "request": {
                "kind": "turn",
                "request": {
                    "threadId": "thr-existing",
                    "prompt": "automatic probe",
                },
            },
        }
    )
    schedule_id = created["schedule"]["scheduleId"]
    deadline = time.monotonic() + 3
    runs: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        runs = app.schedule({"action": "runs", "scheduleId": schedule_id, "limit": 10})[
            "runs"
        ]
        if runs and runs[0]["state"] != "claimed":
            break
        time.sleep(0.05)
    assert runs and runs[0]["state"] == "running"
    assert runs[0]["jobId"].startswith("job-")
    app.close()


def test_app_server_overload_is_retried_with_bounded_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RetryClient(AppServerClient):
        def __init__(self) -> None:
            super().__init__(launch_args_override=["unused"])
            self.calls = 0
            self.metadata = {}

        @property
        def running(self) -> bool:
            return True

        def _request(
            self,
            method: str,
            params: dict[str, Any],
            *,
            timeout: float | None,
        ) -> Any:
            self.calls += 1
            if self.calls < 3:
                raise RpcError(-32001, "overloaded")
            return {"accepted": True}

    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    client = RetryClient()
    assert client.request("model/list", {}, overload_retries=3) == {"accepted": True}
    assert client.calls == 3
