"""Destructive-in-temp full-access live matrix.

The caller should point CODEX_HOME at an isolated authenticated profile. The
probe mutates only a newly-created temporary directory and deletes only threads
created by this run.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from codex_app_mcp.gateway import CodexAppGateway  # noqa: E402
from codex_app_mcp.policy import GatewayPolicy  # noqa: E402


def emit(name: str, ok: bool, **details: Any) -> None:
    print(
        json.dumps(
            {"check": name, "ok": ok, **details},
            ensure_ascii=False,
            default=str,
        ),
        flush=True,
    )


def git(args: list[str], cwd: Path) -> None:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout)


def wait_file(path: Path, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.1)
    raise TimeoutError(f"file was not created: {path}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--codex-bin", default=os.environ.get("CODEX_APP_MCP_BIN", "codex")
    )
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--skip-model-turns", action="store_true")
    args = parser.parse_args()

    failures: list[str] = []
    created_threads: list[str] = []
    with tempfile.TemporaryDirectory(
        prefix="codex-app-mcp-full-", dir="C:\\tmp"
    ) as raw:
        root = Path(raw).resolve()
        io_root = root / "io"
        repo = root / "repo"
        lane_repo = root / "lane-repo"
        lanes = root / "lanes"
        io_root.mkdir()
        repo.mkdir()
        lane_repo.mkdir()
        git(["init"], repo)
        git(["config", "user.email", "probe@example.invalid"], repo)
        git(["config", "user.name", "Codex App MCP Probe"], repo)
        (repo / "base.txt").write_text("base\n", encoding="utf-8")
        git(["add", "base.txt"], repo)
        git(["commit", "-m", "probe base"], repo)
        git(["init"], lane_repo)
        git(["config", "user.email", "probe@example.invalid"], lane_repo)
        git(["config", "user.name", "Codex App MCP Probe"], lane_repo)
        (lane_repo / "base.txt").write_text("base\n", encoding="utf-8")
        git(["add", "base.txt"], lane_repo)
        git(["commit", "-m", "probe base"], lane_repo)

        os.environ["CODEX_APP_MCP_BIN"] = args.codex_bin
        gateway = CodexAppGateway(
            policy=GatewayPolicy(
                allowed_roots=(root,),
                allow_full_access=True,
                allow_unsafe_rpc=True,
                allowed_rpc_methods=frozenset({"*"}),
                allow_readonly_rpc=True,
            ),
            state_path=root / "jobs.sqlite3",
        )

        def check(name: str, fn: Callable[[], Any]) -> Any:
            try:
                result = fn()
                emit(name, True, result=result)
                return result
            except Exception as exc:  # noqa: BLE001 - continue the live matrix
                failures.append(name)
                emit(
                    name,
                    False,
                    error=f"{type(exc).__name__}: {exc}",
                    stderrTail=gateway.client.stderr_tail[-20:],
                )
                return None

        try:
            status = check("status", lambda: gateway.status(include_stderr=True))
            protocol = check(
                "protocol_summary",
                lambda: gateway.protocol({"action": "summary", "force": True}),
            )
            check(
                "protocol_realtime_shape",
                lambda: gateway.protocol(
                    {"action": "method", "method": "thread/realtime/start"}
                ),
            )
            check("runtime_metrics", lambda: gateway.runtime({"action": "metrics"}))

            def account_summary() -> dict[str, Any]:
                account_result = gateway.admin({"operation": "account.read"})
                account = account_result.get("result", {}).get("account") or {}
                return {
                    "ok": account_result.get("ok"),
                    "type": account.get("type"),
                    "planType": account.get("planType"),
                }

            check("typed_admin_account", account_summary)
            models = check(
                "models", lambda: gateway.discover({"action": "models", "force": True})
            )
            check("account", account_summary)
            model_rows = (
                models.get("result", {}).get("data", [])
                if isinstance(models, dict)
                else []
            )
            model_row = next(
                (
                    row
                    for row in model_rows
                    if isinstance(row, dict) and row.get("isDefault")
                ),
                model_rows[0] if model_rows else {},
            )
            model = model_row.get("id") or model_row.get("model")
            efforts = [
                item.get("reasoningEffort")
                for item in model_row.get("supportedReasoningEfforts", [])
                if isinstance(item, dict)
            ]
            effort = "low" if "low" in efforts else (efforts[0] if efforts else None)
            emit(
                "runtime",
                bool(status and model),
                model=model,
                effort=effort,
                codexBin=args.codex_bin,
            )
            if not model:
                failures.append("runtime")

            schedule = check(
                "schedule_create",
                lambda: gateway.schedule(
                    {
                        "action": "create",
                        "idempotencyKey": "full-live-once",
                        "name": "Full live future schedule",
                        "timezone": "UTC",
                        "rrule": "RRULE:FREQ=ONCE",
                        "startAt": "2099-01-01T00:00:00+00:00",
                        "request": {
                            "kind": "turn",
                            "request": {
                                "threadId": "probe-not-launched",
                                "prompt": "This future schedule must not launch.",
                            },
                        },
                        "retryCount": 1,
                        "misfirePolicy": "run_once",
                    }
                ),
            )
            if isinstance(schedule, dict):
                schedule_id = (schedule.get("schedule") or {}).get("scheduleId")
                if schedule_id:
                    check(
                        "schedule_list",
                        lambda: gateway.schedule({"action": "list", "limit": 20}),
                    )
                    check(
                        "schedule_pause",
                        lambda: gateway.schedule(
                            {"action": "pause", "scheduleId": schedule_id}
                        ),
                    )
                    check(
                        "schedule_resume",
                        lambda: gateway.schedule(
                            {"action": "resume", "scheduleId": schedule_id}
                        ),
                    )
                    check(
                        "schedule_delete",
                        lambda: gateway.schedule(
                            {"action": "delete", "scheduleId": schedule_id}
                        ),
                    )

            if (
                isinstance(protocol, dict)
                and protocol.get("callableRequestCount", 0) >= 1
            ):
                try:
                    process_method = gateway.protocol(
                        {"action": "method", "method": "process/spawn"}
                    )
                except Exception:
                    process_method = {"ok": False}
                    emit("process_spawn_supported", True, supported=False)
                if process_method.get("ok"):
                    emit("process_spawn_supported", True, supported=True)
                    process_marker = io_root / "process.txt"
                    check(
                        "process_spawn_unsandboxed",
                        lambda: gateway.process(
                            {
                                "action": "spawn",
                                "processHandle": "full-live-process",
                                "command": [
                                    sys.executable,
                                    "-c",
                                    (
                                        "from pathlib import Path;"
                                        f"Path(r'{process_marker}').write_text("
                                        "'PROCESS_OK', encoding='utf-8')"
                                    ),
                                ],
                                "cwd": str(io_root),
                                "timeoutMs": 60_000,
                            }
                        ),
                    )
                    try:
                        wait_file(process_marker)
                        emit(
                            "process_spawn_effect",
                            process_marker.read_text(encoding="utf-8") == "PROCESS_OK",
                        )
                        if process_marker.read_text(encoding="utf-8") != "PROCESS_OK":
                            failures.append("process_spawn_effect")
                    except Exception as exc:
                        failures.append("process_spawn_effect")
                        emit(
                            "process_spawn_effect",
                            False,
                            error=f"{type(exc).__name__}: {exc}",
                        )

            command_marker = io_root / "command.txt"
            check(
                "command_exec_danger_full_access",
                lambda: gateway.command(
                    {
                        "action": "exec",
                        "command": [
                            sys.executable,
                            "-c",
                            (
                                "from pathlib import Path;"
                                f"Path(r'{command_marker}').write_text('COMMAND_OK', encoding='utf-8')"
                            ),
                        ],
                        "cwd": str(io_root),
                        "sandbox": "danger-full-access",
                        "timeoutSeconds": 60,
                    }
                ),
            )
            if (
                not command_marker.exists()
                or command_marker.read_text() != "COMMAND_OK"
            ):
                failures.append("command_exec_effect")
                emit("command_exec_effect", False)
            else:
                emit("command_exec_effect", True)

            fs_file = io_root / "fs.txt"
            check(
                "fs_write",
                lambda: gateway.filesystem(
                    {"action": "write", "path": str(fs_file), "text": "FS_OK"}
                ),
            )
            read = check(
                "fs_read",
                lambda: gateway.filesystem(
                    {"action": "read", "path": str(fs_file), "decodeText": True}
                ),
            )
            if not isinstance(read, dict) or read.get("text") != "FS_OK":
                failures.append("fs_read_content")
                emit("fs_read_content", False, result=read)
            else:
                emit("fs_read_content", True)
            check(
                "fs_metadata",
                lambda: gateway.filesystem(
                    {"action": "metadata", "path": str(fs_file)}
                ),
            )
            check(
                "fs_list",
                lambda: gateway.filesystem({"action": "list", "path": str(io_root)}),
            )
            copied = io_root / "fs-copy.txt"
            check(
                "fs_copy",
                lambda: gateway.filesystem(
                    {
                        "action": "copy",
                        "sourcePath": str(fs_file),
                        "destinationPath": str(copied),
                    }
                ),
            )
            check(
                "fs_remove",
                lambda: gateway.filesystem({"action": "remove", "path": str(copied)}),
            )

            thread = check(
                "thread_start",
                lambda: gateway.thread(
                    {
                        "action": "start",
                        "cwd": str(repo),
                        "name": "full live probe",
                        "model": model,
                        "effort": effort,
                        "sandbox": "danger-full-access",
                        "approvalPolicy": "never",
                    }
                ),
            )
            thread_id = thread.get("threadId") if isinstance(thread, dict) else None
            if thread_id:
                created_threads.append(thread_id)
                check(
                    "thread_name",
                    lambda: gateway.thread(
                        {
                            "action": "name",
                            "threadId": thread_id,
                            "name": "renamed live probe",
                        }
                    ),
                )
                check(
                    "thread_metadata",
                    lambda: gateway.thread(
                        {
                            "action": "metadata",
                            "threadId": thread_id,
                            "gitInfo": {"branch": "main"},
                        }
                    ),
                )
                check(
                    "thread_read",
                    lambda: gateway.thread(
                        {"action": "read", "threadId": thread_id, "includeTurns": True}
                    ),
                )
                check("thread_loaded", lambda: gateway.thread({"action": "loaded"}))

                if not args.skip_model_turns:
                    turn = check(
                        "turn_start",
                        lambda: gateway.turn(
                            {
                                "action": "start",
                                "threadId": thread_id,
                                "prompt": "Reply with exactly TURN_FULL_ACCESS_OK. Do not call tools.",
                                "model": model,
                                "effort": effort,
                                "sandbox": "danger-full-access",
                                "approvalPolicy": "never",
                            }
                        ),
                    )
                    if isinstance(turn, dict):
                        completion = check(
                            "turn_complete",
                            lambda: gateway._wait_for_turn(
                                thread_id,
                                turn["turnId"],
                                timeout_seconds=args.timeout,
                            ),
                        )
                        if (
                            not isinstance(completion, dict)
                            or completion.get("status") != "completed"
                        ):
                            failures.append("turn_complete_status")
                            emit("turn_complete_status", False, completion=completion)

                shell_marker = repo / "shell-command.txt"
                shell_command = (
                    f'Set-Content -LiteralPath "{shell_marker}" '
                    '-Value "SHELL_OK" -NoNewline'
                )
                check(
                    "thread_shell_command",
                    lambda: gateway.thread(
                        {
                            "action": "shell",
                            "threadId": thread_id,
                            "command": shell_command,
                        }
                    ),
                )
                try:
                    wait_file(shell_marker)
                    emit("thread_shell_effect", True)
                except Exception as exc:
                    failures.append("thread_shell_effect")
                    emit("thread_shell_effect", False, error=str(exc))

                fork = check(
                    "thread_fork",
                    lambda: gateway.thread(
                        {
                            "action": "fork",
                            "threadId": thread_id,
                            "cwd": str(repo),
                            "sandbox": "danger-full-access",
                            "approvalPolicy": "never",
                        }
                    ),
                )
                fork_id = fork.get("threadId") if isinstance(fork, dict) else None
                if fork_id:
                    created_threads.append(fork_id)
                    check(
                        "thread_fork_read",
                        lambda: gateway.thread(
                            {
                                "action": "read",
                                "threadId": fork_id,
                                "includeTurns": True,
                            }
                        ),
                    )
                    check(
                        "thread_unsubscribe",
                        lambda: gateway.thread(
                            {"action": "unsubscribe", "threadId": fork_id}
                        ),
                    )

                if not args.skip_model_turns:
                    review = check(
                        "review_start",
                        lambda: gateway.review(
                            {
                                "threadId": thread_id,
                                "target": {"type": "uncommittedChanges"},
                                "delivery": "inline",
                                "wait": True,
                                "timeoutSeconds": args.timeout,
                            }
                        ),
                    )
                    if (
                        isinstance(review, dict)
                        and review.get("completion", {}).get("status") != "completed"
                    ):
                        failures.append("review_complete_status")
                        emit("review_complete_status", False, result=review)

                check(
                    "thread_archive",
                    lambda: gateway.thread(
                        {"action": "archive", "threadId": thread_id}
                    ),
                )
                check(
                    "thread_unarchive",
                    lambda: gateway.thread(
                        {"action": "unarchive", "threadId": thread_id}
                    ),
                )

            check(
                "raw_rpc_rate_limits",
                lambda: gateway.rpc(
                    {
                        "method": "account/rateLimits/read",
                        "params": {},
                        "timeoutSeconds": 60,
                    }
                ),
            )

            if not args.skip_model_turns:
                lane = check(
                    "lane_run",
                    lambda: gateway.lane(
                        {
                            "action": "run",
                            "repoRoot": str(lane_repo),
                            "lanesParent": str(lanes),
                            "lane": "full-live",
                            "goal": (
                                "Create lane_probe.txt containing exactly LANE_APP_SERVER_OK "
                                "and then reply briefly. Do not modify any other file."
                            ),
                            "model": model,
                            "effort": effort,
                            "sandbox": "danger-full-access",
                            "approvalPolicy": "never",
                            "timeoutSeconds": args.timeout,
                        }
                    ),
                )
                if isinstance(lane, dict):
                    lane_thread = lane.get("threadId")
                    if lane_thread:
                        created_threads.append(str(lane_thread))
                    if "lane_probe.txt" not in lane.get("changedFiles", []):
                        failures.append("lane_change")
                        emit("lane_change", False, result=lane)
                    else:
                        emit("lane_change", True)

            def mcp_summary() -> dict[str, Any]:
                inventory = gateway.discover(
                    {"action": "mcp_servers", "detail": "toolsAndAuthOnly", "limit": 20}
                )
                rows = inventory.get("result", {}).get("data", [])
                return {
                    "serverCount": len(rows),
                    "servers": [
                        {
                            "name": row.get("name"),
                            "toolCount": len(row.get("tools") or {}),
                            "authStatus": row.get("authStatus"),
                        }
                        for row in rows
                        if isinstance(row, dict)
                    ],
                }

            check("mcp_status", mcp_summary)
        finally:
            for thread_id in reversed(created_threads):
                try:
                    gateway.thread({"action": "delete", "threadId": thread_id})
                    emit("thread_cleanup", True, threadId=thread_id)
                except Exception as exc:
                    emit(
                        "thread_cleanup",
                        False,
                        threadId=thread_id,
                        error=f"{type(exc).__name__}: {exc}",
                    )
            gateway.close()
            emit("gateway_closed", not gateway.client.running)
            if gateway.client.running:
                failures.append("gateway_closed")

    emit("summary", not failures, failures=failures)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
