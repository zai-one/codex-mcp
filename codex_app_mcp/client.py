"""Concurrent JSON-RPC client for ``codex app-server`` over stdio."""

from __future__ import annotations

import json
import os
import platform
import random
import re
import shutil
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from . import __version__

MAX_FRAME_BYTES = 4 * 1024 * 1024


class TransportClosed(RuntimeError):
    pass


class RpcError(RuntimeError):
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(f"app-server RPC {code}: {message}")
        self.code = int(code)
        self.message = str(message)
        self.data = data


@dataclass
class _Pending:
    event: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    error: Optional[RpcError] = None


NotificationHandler = Callable[[str, Any], None]
ServerRequestHandler = Callable[[Any, str, Any], None]


class AppServerClient:
    """One long-lived app-server process with request and event multiplexing."""

    def __init__(
        self,
        *,
        codex_bin: str = "codex",
        config_overrides: Sequence[str] = (),
        cwd: Optional[str] = None,
        env: Optional[Mapping[str, str]] = None,
        client_name: str = "codex_app_mcp",
        client_title: str = "Codex App MCP Gateway",
        client_version: str = __version__,
        experimental_api: bool = True,
        request_timeout: float = 30.0,
        launch_args_override: Optional[Sequence[str]] = None,
        notification_handler: Optional[NotificationHandler] = None,
        server_request_handler: Optional[ServerRequestHandler] = None,
    ) -> None:
        self.codex_bin = codex_bin
        self.config_overrides = tuple(str(value) for value in config_overrides)
        self.cwd = cwd
        self.extra_env = dict(env or {})
        self.client_name = client_name
        self.client_title = client_title
        self.client_version = client_version
        self.experimental_api = bool(experimental_api)
        self.request_timeout = max(1.0, float(request_timeout))
        self.launch_args_override = (
            tuple(str(value) for value in launch_args_override)
            if launch_args_override is not None
            else None
        )
        self.notification_handler = notification_handler
        self.server_request_handler = server_request_handler

        self._proc: Optional[subprocess.Popen[str]] = None
        self._write_lock = threading.Lock()
        self._start_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._pending: dict[int, _Pending] = {}
        self._next_id = 1
        self._reader: Optional[threading.Thread] = None
        self._stderr_reader: Optional[threading.Thread] = None
        self._stderr: deque[str] = deque(maxlen=200)
        self._closed_error: Optional[str] = None
        self.metadata: Optional[dict[str, Any]] = None

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def pid(self) -> Optional[int]:
        return self._proc.pid if self._proc is not None else None

    @property
    def stderr_tail(self) -> list[str]:
        return list(self._stderr)

    def start(self) -> dict[str, Any]:
        with self._start_lock:
            return self._start_locked()

    def _start_locked(self) -> dict[str, Any]:
        if self.running and self.metadata is not None:
            return self.metadata
        if self._proc is not None:
            self.close()
        argv = self._build_argv()
        proc_env = os.environ.copy()
        proc_env.update(self.extra_env)
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        self._closed_error = None
        self._proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=self.cwd,
            env=proc_env,
            bufsize=1,
            creationflags=creationflags,
        )
        self._reader = threading.Thread(
            target=self._read_loop,
            name="codex-app-mcp-reader",
            daemon=True,
        )
        self._stderr_reader = threading.Thread(
            target=self._stderr_loop,
            name="codex-app-mcp-stderr",
            daemon=True,
        )
        self._reader.start()
        self._stderr_reader.start()
        try:
            result = self._request(
                "initialize",
                {
                    "clientInfo": {
                        "name": self.client_name,
                        "title": self.client_title,
                        "version": self.client_version,
                    },
                    "capabilities": {
                        "experimentalApi": self.experimental_api,
                        "mcpServerOpenaiFormElicitation": True,
                    },
                },
                timeout=max(60.0, self.request_timeout),
            )
            if not isinstance(result, dict):
                raise TransportClosed("initialize returned a non-object response")
            self.metadata = result
            self.notify("initialized", {})
            return result
        except BaseException:
            self.close()
            raise

    def request(
        self,
        method: str,
        params: Optional[Mapping[str, Any]] = None,
        *,
        timeout: Optional[float] = None,
        overload_retries: int = 3,
    ) -> Any:
        retries = max(0, min(int(overload_retries), 8))
        for attempt in range(retries + 1):
            if not self.running or self.metadata is None:
                self.start()
            try:
                return self._request(method, dict(params or {}), timeout=timeout)
            except RpcError as exc:
                if exc.code != -32001 or attempt >= retries:
                    raise
                # Official app-server overload guidance is exponential backoff
                # with jitter. An overload response means the request was not
                # admitted, so retrying does not duplicate accepted mutations.
                delay = min(8.0, 0.2 * (2**attempt))
                time.sleep(delay + random.uniform(0.0, delay * 0.25))
        raise AssertionError("unreachable")

    def _request(
        self, method: str, params: dict[str, Any], *, timeout: Optional[float]
    ) -> Any:
        if not isinstance(method, str) or not method:
            raise ValueError("method must be a non-empty string")
        with self._pending_lock:
            request_id = self._next_id
            self._next_id += 1
            pending = _Pending()
            self._pending[request_id] = pending
        try:
            self._write({"method": method, "id": request_id, "params": params})
            wait_for = (
                self.request_timeout if timeout is None else max(0.1, float(timeout))
            )
            if not pending.event.wait(wait_for):
                raise TimeoutError(f"app-server request timed out: {method}")
            if pending.error is not None:
                raise pending.error
            return pending.result
        finally:
            with self._pending_lock:
                self._pending.pop(request_id, None)

    def notify(self, method: str, params: Optional[Mapping[str, Any]] = None) -> None:
        self._write({"method": method, "params": dict(params or {})})

    def respond(
        self,
        rpc_id: Any,
        result: Optional[dict[str, Any]],
        error: Optional[dict[str, Any]],
    ) -> None:
        payload: dict[str, Any] = {"id": rpc_id}
        if error is not None:
            payload["error"] = error
        else:
            payload["result"] = result if result is not None else {}
        self._write(payload)

    def close(self) -> None:
        proc = self._proc
        self._proc = None
        self.metadata = None
        if proc is None:
            return
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            self._terminate_tree(proc)
        self._fail_all(TransportClosed("app-server client closed"))

    def _build_argv(self) -> list[str]:
        if self.launch_args_override is not None:
            if not self.launch_args_override:
                raise FileNotFoundError("launch_args_override is empty")
            return list(self.launch_args_override)
        resolved = self.codex_bin
        if not os.path.isabs(resolved):
            found = shutil.which(resolved)
            if not found:
                raise FileNotFoundError(f"codex binary not found: {resolved}")
            resolved = found
        elif not Path(resolved).exists():
            raise FileNotFoundError(f"codex binary not found: {resolved}")
        command = self._resolve_launch_command(Path(resolved))
        argv = [*command, "app-server", "--listen", "stdio://"]
        for override in self.config_overrides:
            argv.extend(["--config", override])
        return argv

    @staticmethod
    def _resolve_launch_command(resolved: Path) -> list[str]:
        """Bypass Windows npm shims so the owned PID is the real native process."""
        if os.name != "nt" or resolved.suffix.lower() not in {".cmd", ".bat"}:
            return [str(resolved)]
        try:
            shim = resolved.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return [str(resolved)]
        match = re.search(
            r'"([^"\r\n]*\.(?:js|cjs|mjs))"',
            shim,
            flags=re.IGNORECASE,
        )
        if match is None:
            return [str(resolved)]
        script_text = re.sub(
            r"%~?dp0%?",
            lambda _: str(resolved.parent) + os.sep,
            match.group(1),
            flags=re.IGNORECASE,
        )
        script = Path(script_text).resolve()
        if not script.is_file():
            return [str(resolved)]
        package_root = script.parent.parent
        machine = platform.machine().lower()
        arch = "arm64" if machine in {"arm64", "aarch64"} else "x64"
        triple = (
            "aarch64-pc-windows-msvc" if arch == "arm64" else "x86_64-pc-windows-msvc"
        )
        native = (
            package_root
            / "node_modules"
            / "@openai"
            / f"codex-win32-{arch}"
            / "vendor"
            / triple
            / "bin"
            / "codex.exe"
        )
        if native.is_file():
            return [str(native.resolve())]
        node = resolved.parent / "node.exe"
        if not node.is_file():
            found = shutil.which("node.exe") or shutil.which("node")
            if not found:
                return [str(resolved)]
            node = Path(found)
        return [str(node), str(script)]

    def _write(self, message: Mapping[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.poll() is not None or proc.stdin is None:
            raise TransportClosed(self._closed_error or "app-server is not running")
        body = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        if len(body.encode("utf-8")) > MAX_FRAME_BYTES:
            raise ValueError(f"JSON-RPC frame exceeds {MAX_FRAME_BYTES} bytes")
        with self._write_lock:
            try:
                proc.stdin.write(body + "\n")
                proc.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise TransportClosed(f"app-server stdin failed: {exc}") from exc

    def _read_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        try:
            for raw_line in proc.stdout:
                if len(raw_line.encode("utf-8", errors="replace")) > MAX_FRAME_BYTES:
                    self._stderr.append("oversized app-server frame ignored")
                    continue
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    self._stderr.append(f"non-JSON app-server stdout: {line[:500]}")
                    continue
                self._handle_message(message)
        finally:
            code = proc.poll()
            if code is None:
                try:
                    code = proc.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    code = proc.poll()
            self._closed_error = f"app-server stdout closed (exit={code})"
            self._fail_all(TransportClosed(self._closed_error))

    def _stderr_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        for line in proc.stderr:
            text = line.rstrip()
            if text:
                self._stderr.append(text[:2_000])

    def _handle_message(self, message: Any) -> None:
        if not isinstance(message, Mapping):
            return
        method = message.get("method")
        has_id = "id" in message
        if isinstance(method, str):
            params = message.get("params")
            if has_id:
                handler = self.server_request_handler
                if handler is None:
                    self.respond(
                        message.get("id"),
                        None,
                        {
                            "code": -32601,
                            "message": f"unsupported server request: {method}",
                        },
                    )
                else:
                    handler(message.get("id"), method, params)
            else:
                handler = self.notification_handler
                if handler is not None:
                    handler(method, params)
            return
        if not has_id:
            return
        request_id = message.get("id")
        if not isinstance(request_id, int):
            return
        with self._pending_lock:
            pending = self._pending.get(request_id)
        if pending is None:
            return
        error = message.get("error")
        if isinstance(error, Mapping):
            pending.error = RpcError(
                int(error.get("code") or -32000),
                str(error.get("message") or "unknown app-server error"),
                error.get("data"),
            )
        else:
            pending.result = message.get("result")
        pending.event.set()

    def _fail_all(self, error: BaseException) -> None:
        with self._pending_lock:
            values = list(self._pending.values())
        for pending in values:
            if pending.error is None:
                if isinstance(error, RpcError):
                    pending.error = error
                else:
                    pending.error = RpcError(-32000, str(error))
            pending.event.set()

    @staticmethod
    def _terminate_tree(proc: subprocess.Popen[str]) -> None:
        if proc.poll() is not None:
            return
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=15,
                )
            except (OSError, subprocess.TimeoutExpired):
                try:
                    proc.kill()
                except OSError:
                    pass
        else:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    proc.kill()
                except OSError:
                    pass
