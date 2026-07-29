"""Isolate app-server thread/start crashes one request shape at a time."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from codex_app_mcp.client import AppServerClient  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--codex-bin", default=os.environ.get("CODEX_APP_MCP_BIN", "codex")
    )
    args = parser.parse_args()
    failures = 0
    with tempfile.TemporaryDirectory(prefix="thread-shapes-", dir="C:\\tmp") as raw:
        cwd = str(Path(raw).resolve())
        shapes: list[tuple[str, dict[str, Any]]] = [
            ("cwd", {"cwd": cwd}),
            ("cwd_sandbox", {"cwd": cwd, "sandbox": "danger-full-access"}),
            (
                "cwd_sandbox_approval",
                {
                    "cwd": cwd,
                    "sandbox": "danger-full-access",
                    "approvalPolicy": "never",
                },
            ),
            (
                "cwd_model",
                {"cwd": cwd, "model": "gpt-5.6-sol"},
            ),
            (
                "full",
                {
                    "cwd": cwd,
                    "model": "gpt-5.6-sol",
                    "config": {"model_reasoning_effort": "low"},
                    "sandbox": "danger-full-access",
                    "approvalPolicy": "never",
                    "ephemeral": False,
                },
            ),
        ]
        for name, params in shapes:
            client = AppServerClient(codex_bin=args.codex_bin, request_timeout=60)
            started = time.monotonic()
            try:
                metadata = client.start()
                result = client.request("thread/start", params, timeout=120)
                thread = result.get("thread", {})
                thread_id = thread.get("id")
                if thread_id:
                    client.request("thread/delete", {"threadId": thread_id})
                payload = {
                    "shape": name,
                    "ok": True,
                    "elapsedSeconds": round(time.monotonic() - started, 3),
                    "version": metadata.get("userAgent"),
                    "threadId": thread_id,
                }
            except Exception as exc:  # noqa: BLE001 - diagnostic matrix
                failures += 1
                payload = {
                    "shape": name,
                    "ok": False,
                    "elapsedSeconds": round(time.monotonic() - started, 3),
                    "error": f"{type(exc).__name__}: {exc}",
                    "stderrTail": client.stderr_tail[-20:],
                }
            finally:
                client.close()
            print(json.dumps(payload, ensure_ascii=False), flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
