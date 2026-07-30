"""Probe MCP HTTP -> gateway -> live Codex app-server."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _post(endpoint: str, token: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = Request(
        endpoint,
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def main() -> int:
    port = _free_port()
    token = "local-http-probe-token"
    env = os.environ.copy()
    env["CODEX_APP_MCP_HTTP_TOKEN"] = token
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "codex_app_mcp",
            "--transport",
            "http",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=str(REPO_ROOT),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
    )
    base = f"http://127.0.0.1:{port}"
    evidence: dict[str, Any] = {"port": port}
    try:
        deadline = time.monotonic() + 30
        while True:
            if proc.poll() is not None:
                raise RuntimeError(
                    f"HTTP gateway exited early with code {proc.returncode}"
                )
            try:
                with urlopen(f"{base}/healthz", timeout=1) as response:
                    evidence["health"] = json.load(response)
                break
            except (URLError, TimeoutError):
                if time.monotonic() >= deadline:
                    raise RuntimeError("HTTP gateway did not become healthy")
                time.sleep(0.1)

        unauthorized = Request(
            f"{base}/mcp",
            data=b'{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}',
            headers={"Content-Type": "application/json"},
        )
        try:
            urlopen(unauthorized, timeout=5)
            evidence["unauthorizedStatus"] = 200
        except HTTPError as exc:
            evidence["unauthorizedStatus"] = exc.code

        initialized = _post(
            f"{base}/mcp",
            token,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "http-probe", "version": "1"},
                },
            },
        )
        listed = _post(
            f"{base}/mcp",
            token,
            {"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}},
        )
        status = _post(
            f"{base}/mcp",
            token,
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "codex_app_status", "arguments": {}},
            },
        )
        doctor = _post(
            f"{base}/mcp",
            token,
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "codex_app_doctor", "arguments": {}},
            },
        )
        protocol = _post(
            f"{base}/mcp",
            token,
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {
                    "name": "codex_app_protocol",
                    "arguments": {"action": "summary"},
                },
            },
        )
        status_data = status["result"]["structuredContent"]
        doctor_data = doctor["result"]["structuredContent"]
        protocol_data = protocol["result"]["structuredContent"]
        evidence.update(
            {
                "protocolVersion": initialized["result"]["protocolVersion"],
                "toolCount": len(listed["result"]["tools"]),
                "appServerRunning": status_data.get("running"),
                "appServerUserAgent": (status_data.get("metadata") or {}).get(
                    "userAgent"
                ),
                "doctorOk": doctor_data.get("ok"),
                "protocolCallableCount": protocol_data.get("callableRequestCount"),
                "doctorChecks": {
                    key: value.get("ok")
                    for key, value in (doctor_data.get("checks") or {}).items()
                    if isinstance(value, dict)
                },
            }
        )
        evidence["ok"] = bool(
            evidence["health"].get("ok")
            and evidence["unauthorizedStatus"] == 401
            and evidence["toolCount"] == 20
            and evidence["appServerRunning"]
            and evidence["doctorOk"]
            and evidence["protocolCallableCount"] > 0
        )
    except Exception as exc:
        evidence["ok"] = False
        evidence["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        proc.terminate()
        try:
            stdout, stderr = proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate(timeout=5)
        evidence["outerExitCode"] = proc.returncode
        evidence["terminatedByProbe"] = True
        evidence["stdoutTail"] = stdout.splitlines()[-10:]
        evidence["stderrTail"] = stderr.splitlines()[-20:]
    print(json.dumps(evidence, ensure_ascii=False))
    return 0 if evidence["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
