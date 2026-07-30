"""Version-aware introspection for the concrete Codex app-server binary."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Mapping, Optional

from .client import AppServerClient
from .errors import GatewayError
from .policy import READONLY_RPC_METHODS, validate_limit, validate_text


def _methods(document: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    definitions = document.get("definitions")
    if not isinstance(definitions, Mapping):
        definitions = {}
    for variant in document.get("oneOf", []):
        if not isinstance(variant, Mapping):
            continue
        properties = variant.get("properties")
        if not isinstance(properties, Mapping):
            continue
        method_schema = properties.get("method")
        if not isinstance(method_schema, Mapping):
            continue
        values = method_schema.get("enum")
        if not isinstance(values, list) or len(values) != 1:
            continue
        method = values[0]
        if not isinstance(method, str):
            continue
        params = properties.get("params")
        params_ref = params.get("$ref") if isinstance(params, Mapping) else None
        resolved_params = params if isinstance(params, Mapping) else {}
        if isinstance(params_ref, str) and params_ref.startswith("#/definitions/"):
            definition = definitions.get(params_ref.rsplit("/", 1)[-1])
            if isinstance(definition, Mapping):
                resolved_params = definition
        result[method] = {
            "method": method,
            "title": variant.get("title"),
            "description": variant.get("description"),
            "paramsRef": params_ref,
            "paramsSchema": resolved_params,
        }
    return result


class ProtocolRegistry:
    """Generate and cache schemas from the configured executable.

    Schema generation is a read-only binary subcommand. Runtime control goes
    exclusively through the long-lived app-server JSON-RPC connection.
    """

    def __init__(self, codex_bin: str = "codex", *, ttl_seconds: float = 300.0) -> None:
        self.codex_bin = codex_bin
        self.ttl_seconds = max(5.0, float(ttl_seconds))
        self._lock = threading.Lock()
        self._loaded_at = 0.0
        self._snapshot: Optional[dict[str, Any]] = None

    def handle(self, args: Mapping[str, Any]) -> dict[str, Any]:
        action = str(args.get("action") or "summary")
        snapshot = self.snapshot(force=action == "refresh" or bool(args.get("force")))
        if action in {"summary", "refresh"}:
            return {"ok": True, **self._summary(snapshot)}
        if action == "methods":
            kind = str(args.get("kind") or "client")
            catalog = self._catalog(snapshot, kind)
            query = str(args.get("query") or "").casefold()
            values = [
                item
                for item in catalog.values()
                if not query or query in item["method"].casefold()
            ]
            values.sort(key=lambda item: item["method"])
            limit = validate_limit(args.get("limit"), default=200)
            return {
                "ok": True,
                "kind": kind,
                "count": len(values),
                "methods": values[:limit],
                "truncated": len(values) > limit,
            }
        if action == "method":
            method = validate_text(args.get("method"), "method")
            for kind in ("client", "serverRequest", "notification"):
                item = self._catalog(snapshot, kind).get(method)
                if item is not None:
                    return {"ok": True, "kind": kind, **item}
            raise GatewayError(
                "PROTOCOL_METHOD_NOT_FOUND",
                f"method is not present in the configured app-server schema: {method}",
            )
        raise GatewayError(
            "PROTOCOL_ACTION_INVALID", f"unknown protocol action: {action}"
        )

    def snapshot(self, *, force: bool = False) -> dict[str, Any]:
        with self._lock:
            if (
                not force
                and self._snapshot is not None
                and time.monotonic() - self._loaded_at < self.ttl_seconds
            ):
                return self._snapshot
            self._snapshot = self._generate()
            self._loaded_at = time.monotonic()
            return self._snapshot

    def supports(self, method: str) -> bool:
        return method in self.snapshot()["client"]

    def _generate(self) -> dict[str, Any]:
        command = self._resolve_command()
        with tempfile.TemporaryDirectory(prefix="codex-app-mcp-schema-") as raw:
            output = Path(raw)
            completed = subprocess.run(
                [
                    *command,
                    "app-server",
                    "generate-json-schema",
                    "--experimental",
                    "--out",
                    str(output),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                check=False,
            )
            if completed.returncode != 0:
                raise GatewayError(
                    "PROTOCOL_SCHEMA_GENERATION_FAILED",
                    (
                        completed.stderr
                        or completed.stdout
                        or "schema generation failed"
                    )[-2_000:],
                    returncode=completed.returncode,
                )
            names = {
                "client": "ClientRequest.json",
                "serverRequest": "ServerRequest.json",
                "notification": "ServerNotification.json",
            }
            catalogs: dict[str, Any] = {}
            for kind, filename in names.items():
                path = output / filename
                if not path.is_file():
                    raise GatewayError(
                        "PROTOCOL_SCHEMA_INCOMPLETE",
                        f"generated schema is missing {filename}",
                    )
                catalogs[kind] = _methods(json.loads(path.read_text(encoding="utf-8")))
            bundle = output / "codex_app_server_protocol.schemas.json"
            return {
                **catalogs,
                "generatedAt": time.time(),
                "codexBin": self.codex_bin,
                "command": [Path(command[0]).name, *command[1:]],
                "bundleBytes": bundle.stat().st_size if bundle.is_file() else None,
            }

    def _resolve_command(self) -> list[str]:
        resolved = self.codex_bin
        if not os.path.isabs(resolved):
            found = shutil.which(resolved)
            if not found:
                raise GatewayError(
                    "CODEX_BINARY_NOT_FOUND", f"codex binary not found: {resolved}"
                )
            resolved = found
        elif not Path(resolved).is_file():
            raise GatewayError(
                "CODEX_BINARY_NOT_FOUND", f"codex binary not found: {resolved}"
            )
        return AppServerClient._resolve_launch_command(Path(resolved))

    @staticmethod
    def _catalog(snapshot: Mapping[str, Any], kind: str) -> dict[str, dict[str, Any]]:
        if kind not in {"client", "serverRequest", "notification"}:
            raise GatewayError(
                "PROTOCOL_KIND_INVALID",
                "kind must be client, serverRequest, or notification",
            )
        catalog = snapshot.get(kind)
        return dict(catalog) if isinstance(catalog, Mapping) else {}

    @staticmethod
    def _summary(snapshot: Mapping[str, Any]) -> dict[str, Any]:
        client = snapshot["client"]
        callable_methods = [
            method
            for method in client
            if method != "initialize" and not method.startswith("mock/")
        ]
        return {
            "generatedAt": snapshot["generatedAt"],
            "codexBin": snapshot["codexBin"],
            "command": snapshot["command"],
            "bundleBytes": snapshot["bundleBytes"],
            "clientRequestCount": len(client),
            "callableRequestCount": len(callable_methods),
            "readonlyRequestCount": sum(
                method in READONLY_RPC_METHODS for method in callable_methods
            ),
            "serverRequestCount": len(snapshot["serverRequest"]),
            "notificationCount": len(snapshot["notification"]),
        }
