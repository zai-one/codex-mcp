"""Minimal stateless MCP Streamable HTTP transport using the standard library."""

from __future__ import annotations

import hmac
import json
import os
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

from .gateway import CodexAppGateway
from .server import (
    CURRENT_PROTOCOL_VERSION,
    LEGACY_PROTOCOL_VERSIONS,
    MAX_MCP_FRAME_BYTES,
    SUPPORTED_PROTOCOL_VERSIONS,
    dispatch_jsonrpc,
)

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def _configured_token(explicit: Optional[str]) -> Optional[str]:
    if explicit is not None:
        value = str(explicit).strip()
        return value or None
    value = os.environ.get("CODEX_APP_MCP_HTTP_TOKEN", "").strip()
    token_file = os.environ.get("CODEX_APP_MCP_HTTP_TOKEN_FILE", "").strip()
    if value and token_file:
        raise ValueError(
            "set only one of CODEX_APP_MCP_HTTP_TOKEN and "
            "CODEX_APP_MCP_HTTP_TOKEN_FILE"
        )
    if not token_file:
        return value or None
    path = Path(token_file).expanduser().resolve()
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ValueError("cannot read CODEX_APP_MCP_HTTP_TOKEN_FILE") from exc
    if not value:
        raise ValueError("CODEX_APP_MCP_HTTP_TOKEN_FILE is empty")
    return value


def _configured_max_inflight(explicit: Optional[int]) -> int:
    raw: Any = (
        explicit
        if explicit is not None
        else os.environ.get("CODEX_APP_MCP_HTTP_MAX_INFLIGHT", "16")
    )
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("HTTP max inflight must be an integer") from exc
    if value < 1 or value > 256:
        raise ValueError("HTTP max inflight must be between 1 and 256")
    return value


def _origins() -> frozenset[str]:
    return frozenset(
        item.strip()
        for item in os.environ.get("CODEX_APP_MCP_HTTP_ALLOWED_ORIGINS", "").split(";")
        if item.strip()
    )


class GatewayHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        gateway: CodexAppGateway,
        *,
        token: Optional[str],
        allowed_origins: frozenset[str],
        max_inflight: int,
    ) -> None:
        self.gateway = gateway
        self.gateway.start_background()
        self.token = token
        self.allowed_origins = allowed_origins
        self.inflight = threading.BoundedSemaphore(max_inflight)
        super().__init__(server_address, GatewayHTTPRequestHandler)

    def server_close(self) -> None:
        try:
            self.gateway.close()
        finally:
            super().server_close()


class GatewayHTTPRequestHandler(BaseHTTPRequestHandler):
    server: GatewayHTTPServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        # MCP stdout must stay protocol-only; HTTP access logs go to stderr.
        print(f"codex-app-mcp http: {format % args}", file=os.sys.stderr)

    def do_GET(self) -> None:
        if not self._origin_allowed():
            return
        if self.path == "/healthz":
            self._json(HTTPStatus.OK, {"ok": True, "service": "codex-app-mcp"})
            return
        if self.path == "/readyz":
            if not self._authorized():
                return
            try:
                payload = self.server.gateway.status(include_stderr=False)
                self._json(HTTPStatus.OK, payload)
            except Exception as exc:
                self._json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                )
            return
        if self.path == "/mcp":
            self._json(
                HTTPStatus.METHOD_NOT_ALLOWED,
                {
                    "ok": False,
                    "error": "SSE_NOT_SUPPORTED",
                    "message": "This stateless server has no unsolicited SSE stream; use POST /mcp.",
                },
            )
            return
        self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "NOT_FOUND"})

    def do_POST(self) -> None:
        if self.path != "/mcp":
            self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "NOT_FOUND"})
            return
        if not self._authorized() or not self._origin_allowed():
            return
        if not self.server.inflight.acquire(blocking=False):
            self._json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"ok": False, "error": "SERVER_BUSY"},
                extra_headers={"Retry-After": "1"},
            )
            return
        try:
            self._handle_mcp_post()
        finally:
            self.server.inflight.release()

    def _handle_mcp_post(self) -> None:
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip()
        if content_type != "application/json":
            self._json(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                {"ok": False, "error": "CONTENT_TYPE_INVALID"},
            )
            return
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length or "")
        except ValueError:
            self._json(
                HTTPStatus.LENGTH_REQUIRED,
                {"ok": False, "error": "CONTENT_LENGTH_REQUIRED"},
            )
            return
        if length < 0 or length > MAX_MCP_FRAME_BYTES:
            self._json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"ok": False, "error": "REQUEST_TOO_LARGE"},
            )
            return
        body = self.rfile.read(length)
        try:
            message = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json(
                HTTPStatus.BAD_REQUEST,
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "Parse error"},
                },
            )
            return
        protocol_error = self._validate_protocol_headers(message)
        if protocol_error is not None:
            self._json(HTTPStatus.BAD_REQUEST, protocol_error)
            return
        response = dispatch_jsonrpc(message, self.server.gateway)
        if response is None:
            self.send_response(HTTPStatus.ACCEPTED)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._json(HTTPStatus.OK, response)

    def _authorized(self) -> bool:
        token = self.server.token
        if token is None:
            return True
        header = self.headers.get("Authorization", "")
        prefix = "Bearer "
        supplied = header[len(prefix) :] if header.startswith(prefix) else ""
        if supplied and hmac.compare_digest(supplied, token):
            return True
        self._json(
            HTTPStatus.UNAUTHORIZED,
            {"ok": False, "error": "UNAUTHORIZED"},
            extra_headers={"WWW-Authenticate": "Bearer"},
        )
        return False

    def _origin_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        if origin in self.server.allowed_origins:
            return True
        self._json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "ORIGIN_NOT_ALLOWED"})
        return False

    def _validate_protocol_headers(self, message: Any) -> Optional[dict[str, Any]]:
        # Batch is retained as a compatibility extension, but current 2026
        # metadata/header validation applies only to the standard single request.
        if not isinstance(message, dict):
            return None
        method = message.get("method")
        if not isinstance(method, str):
            return None
        requested = self.headers.get("MCP-Protocol-Version")
        if requested is None:
            # Legacy Streamable HTTP clients negotiate through initialize and
            # do not include a per-request protocol header.
            return None
        if requested not in SUPPORTED_PROTOCOL_VERSIONS:
            return {
                "jsonrpc": "2.0",
                "id": message.get("id"),
                "error": {
                    "code": -32022,
                    "message": "Unsupported protocol version",
                    "data": {
                        "supported": list(SUPPORTED_PROTOCOL_VERSIONS),
                        "requested": requested,
                    },
                },
            }
        if requested in LEGACY_PROTOCOL_VERSIONS:
            return None
        assert requested == CURRENT_PROTOCOL_VERSION
        params = message.get("params")
        meta = params.get("_meta") if isinstance(params, dict) else None
        meta_version = (
            meta.get("io.modelcontextprotocol/protocolVersion")
            if isinstance(meta, dict)
            else None
        )
        client_info = (
            meta.get("io.modelcontextprotocol/clientInfo")
            if isinstance(meta, dict)
            else None
        )
        client_capabilities = (
            meta.get("io.modelcontextprotocol/clientCapabilities")
            if isinstance(meta, dict)
            else None
        )
        header_method = self.headers.get("Mcp-Method")
        header_name = self.headers.get("Mcp-Name")
        body_name = params.get("name") if isinstance(params, dict) else None
        invalid = (
            meta_version != requested
            or not isinstance(client_info, dict)
            or not isinstance(client_info.get("name"), str)
            or not isinstance(client_info.get("version"), str)
            or not isinstance(client_capabilities, dict)
            or header_method != method
            or (method == "tools/call" and header_name != body_name)
        )
        if not invalid:
            return None
        return {
            "jsonrpc": "2.0",
            "id": message.get("id"),
            "error": {
                "code": -32602,
                "message": "Invalid Params: current MCP headers/_meta are missing or inconsistent",
            },
        }

    def _json(
        self,
        status: HTTPStatus,
        payload: Any,
        *,
        extra_headers: Optional[dict[str, str]] = None,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)


def create_http_server(
    *,
    host: str,
    port: int,
    gateway: Optional[CodexAppGateway] = None,
    token: Optional[str] = None,
    max_inflight: Optional[int] = None,
) -> GatewayHTTPServer:
    host = str(host).strip()
    if not host:
        raise ValueError("HTTP host must not be empty")
    if port < 0 or port > 65_535:
        raise ValueError("HTTP port must be between 0 and 65535")
    configured = _configured_token(token)
    if host.lower() not in LOOPBACK_HOSTS and not configured:
        raise ValueError("non-loopback HTTP bind requires an HTTP bearer token")
    return GatewayHTTPServer(
        (host, port),
        gateway or CodexAppGateway(),
        token=configured,
        allowed_origins=_origins(),
        max_inflight=_configured_max_inflight(max_inflight),
    )


def serve_http(*, host: str, port: int) -> None:
    server = create_http_server(host=host, port=port)
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()
