"""Audit MCP gateway coverage against a concrete Codex app-server schema."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from codex_app_mcp.client import AppServerClient  # noqa: E402
from codex_app_mcp.gateway import ADMIN_OPERATIONS  # noqa: E402
from codex_app_mcp.policy import READONLY_RPC_METHODS, GatewayPolicy  # noqa: E402

TYPED_METHODS = frozenset(
    {
        "account/read",
        "app/list",
        "collaborationMode/list",
        "command/exec",
        "command/exec/resize",
        "command/exec/terminate",
        "command/exec/write",
        "configRequirements/read",
        "experimentalFeature/list",
        "fs/copy",
        "fs/createDirectory",
        "fs/getMetadata",
        "fs/readDirectory",
        "fs/readFile",
        "fs/remove",
        "fs/unwatch",
        "fs/watch",
        "fs/writeFile",
        "hooks/list",
        "mcpServer/tool/call",
        "mcpServerStatus/list",
        "model/list",
        "permissionProfile/list",
        "plugin/list",
        "process/kill",
        "process/resizePty",
        "process/spawn",
        "process/writeStdin",
        "review/start",
        "skills/list",
        "thread/archive",
        "thread/backgroundTerminals/clean",
        "thread/backgroundTerminals/list",
        "thread/backgroundTerminals/terminate",
        "thread/compact/start",
        "thread/delete",
        "thread/fork",
        "thread/goal/clear",
        "thread/goal/get",
        "thread/goal/set",
        "thread/inject_items",
        "thread/items/list",
        "thread/list",
        "thread/loaded/list",
        "thread/metadata/update",
        "thread/name/set",
        "thread/read",
        "thread/resume",
        "thread/rollback",
        "thread/shellCommand",
        "thread/start",
        "thread/turns/list",
        "thread/unarchive",
        "thread/unsubscribe",
        "turn/interrupt",
        "turn/start",
        "turn/steer",
        "windowsSandbox/readiness",
    }
    | {method for method, _mutating in ADMIN_OPERATIONS.values()}
)


def _methods(path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    values: set[str] = set()
    for row in data.get("oneOf", []):
        try:
            enum = row["properties"]["method"]["enum"]
        except (KeyError, TypeError):
            continue
        values.update(str(item) for item in enum)
    return sorted(values)


def _resolve_command(codex_bin: str) -> list[str]:
    resolved = codex_bin
    if not os.path.isabs(resolved):
        found = shutil.which(resolved)
        if not found:
            raise FileNotFoundError(f"codex binary not found: {resolved}")
        resolved = found
    return AppServerClient._resolve_launch_command(Path(resolved))


def _audit_schema(output: Path, *, source: str) -> dict[str, Any]:
    client_methods = _methods(output / "ClientRequest.json")
    server_requests = _methods(output / "ServerRequest.json")
    notifications = _methods(output / "ServerNotification.json")

    callable_methods = [
        method
        for method in client_methods
        if method != "initialize" and not method.startswith("mock/")
    ]
    policy = GatewayPolicy(
        allowed_roots=(Path.cwd().resolve(),),
        allowed_rpc_methods=frozenset({"*"}),
        allow_unsafe_rpc=True,
    )
    rejected: list[dict[str, str]] = []
    for method in callable_methods:
        try:
            policy.validate_rpc_method(method)
        except Exception as exc:  # noqa: BLE001 - audit should report every row
            rejected.append({"method": method, "error": str(exc)})
    typed = sorted(set(callable_methods) & TYPED_METHODS)
    readonly = sorted(set(callable_methods) & READONLY_RPC_METHODS)
    generic_only = sorted(set(callable_methods) - TYPED_METHODS - READONLY_RPC_METHODS)
    return {
        "ok": not rejected,
        "schemaSource": source,
        "clientRequestCount": len(client_methods),
        "callableRequestCount": len(callable_methods),
        "typedRequestCount": len(typed),
        "readonlyRequestCount": len(readonly),
        "genericOnlyRequestCount": len(generic_only),
        "serverRequestCount": len(server_requests),
        "notificationCount": len(notifications),
        "typedMethods": typed,
        "genericOnlyMethods": generic_only,
        "rejectedMethods": rejected,
        "serverRequests": server_requests,
    }


def audit(codex_bin: str, schema_dir: str | None = None) -> dict[str, Any]:
    if schema_dir is not None:
        output = Path(schema_dir).expanduser().resolve()
        for filename in (
            "ClientRequest.json",
            "ServerRequest.json",
            "ServerNotification.json",
        ):
            if not (output / filename).is_file():
                raise FileNotFoundError(f"schema file not found: {output / filename}")
        result = _audit_schema(output, source=str(output))
        result["codexBin"] = None
        return result
    with tempfile.TemporaryDirectory(prefix="codex-app-mcp-schema-") as raw:
        output = Path(raw)
        command = [
            *_resolve_command(codex_bin),
            "app-server",
            "generate-json-schema",
            "--experimental",
            "--out",
            str(output),
        ]
        generated = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=120,
        )
        if generated.returncode != 0:
            raise RuntimeError(
                generated.stderr or generated.stdout or "schema generation failed"
            )
        result = _audit_schema(output, source=f"generated:{codex_bin}")
        result["codexBin"] = codex_bin
        return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--codex-bin",
        default=os.environ.get("CODEX_APP_MCP_BIN", "codex"),
    )
    parser.add_argument(
        "--schema-dir",
        help="Audit an already-generated app-server schema directory instead of a binary.",
    )
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()
    result = audit(args.codex_bin, args.schema_dir)
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=None if args.compact else 2,
        )
    )
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
