"""End-to-end probe of MCP stdio -> gateway -> live Codex app-server."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]


def request(
    proc: subprocess.Popen[str],
    request_id: int,
    method: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    assert proc.stdin is not None and proc.stdout is not None
    proc.stdin.write(
        json.dumps(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
            separators=(",", ":"),
        )
        + "\n"
    )
    proc.stdin.flush()
    line = proc.stdout.readline()
    if not line:
        raise RuntimeError(f"MCP stdout closed while waiting for {method}")
    response = json.loads(line)
    if response.get("id") != request_id:
        raise RuntimeError(f"unexpected MCP response id: {response.get('id')}")
    return response


def main() -> int:
    proc = subprocess.Popen(
        [sys.executable, "-m", "codex_app_mcp"],
        cwd=REPO_ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    evidence: dict[str, Any] = {}
    try:
        evidence["initialize"] = request(
            proc,
            1,
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "live-probe", "version": "1"},
            },
        )
        evidence["tools"] = request(proc, 2, "tools/list", {})
        evidence["status"] = request(
            proc,
            3,
            "tools/call",
            {"name": "codex_app_status", "arguments": {"includeStderr": True}},
        )
        evidence["modes"] = request(
            proc,
            4,
            "tools/call",
            {
                "name": "codex_app_discover",
                "arguments": {"action": "collaboration_modes"},
            },
        )
        evidence["protocol"] = request(
            proc,
            5,
            "tools/call",
            {
                "name": "codex_app_protocol",
                "arguments": {"action": "summary"},
            },
        )
        evidence["runtime"] = request(
            proc,
            6,
            "tools/call",
            {
                "name": "codex_app_runtime",
                "arguments": {"action": "metrics"},
            },
        )
        tools = evidence["tools"]["result"]["tools"]
        status = evidence["status"]["result"]["structuredContent"]
        modes = evidence["modes"]["result"]["structuredContent"]
        protocol = evidence["protocol"]["result"]["structuredContent"]
        runtime = evidence["runtime"]["result"]["structuredContent"]
        ok = (
            len(tools) == 20
            and status.get("ok") is True
            and modes.get("ok") is True
            and protocol.get("callableRequestCount", 0) > 0
            and runtime.get("ok") is True
            and evidence["initialize"]["result"]["protocolVersion"] == "2025-11-25"
        )
    finally:
        if proc.stdin is not None:
            proc.stdin.close()
            proc.stdin = None
        try:
            _, stderr = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            _, stderr = proc.communicate(timeout=5)
        evidence["outerExitCode"] = proc.returncode
        evidence["stderrTail"] = stderr.splitlines()[-20:]
    evidence["ok"] = bool(ok and proc.returncode == 0)
    print(json.dumps(evidence, ensure_ascii=False, default=str))
    return 0 if evidence["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
