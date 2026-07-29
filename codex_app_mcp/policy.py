"""Fail-closed policy and validation for the app-server MCP surface."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

from .errors import GatewayError

MAX_TEXT_CHARS = 100_000
MAX_SCHEMA_CHARS = 200_000
MAX_LIMIT = 200
THREAD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
EFFORT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
RPC_METHOD_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*(?:/[A-Za-z0-9_.-]+)*$")
GOAL_STATUSES = frozenset(
    {"active", "paused", "blocked", "usageLimited", "budgetLimited", "complete"}
)
APPROVAL_POLICIES = frozenset({"untrusted", "on-request", "never"})
SANDBOXES = frozenset({"read-only", "workspace-write", "danger-full-access"})
APPROVAL_REVIEWERS = frozenset({"user", "auto_review", "guardian_subagent"})
FORBIDDEN_CONFIG_PREFIXES = frozenset(
    {
        "approval",
        "features",
        "hooks",
        "mcp_servers",
        "notify",
        "permissions",
        "plugins",
        "profiles",
        "projects",
        "sandbox",
        "skills",
        "windows",
    }
)

# A generic escape hatch is intentionally read-only. Stateful work belongs to
# the typed gateway methods, where path/model/sandbox policy can be enforced.
READONLY_RPC_METHODS = frozenset(
    {
        "account/read",
        "app/list",
        "collaborationMode/list",
        "config/read",
        "configRequirements/read",
        "experimentalFeature/list",
        "hooks/list",
        "mcpServer/resource/read",
        "mcpServerStatus/list",
        "model/list",
        "modelProvider/capabilities/read",
        "permissionProfile/list",
        "plugin/list",
        "plugin/read",
        "skills/list",
        "thread/goal/get",
        "thread/items/list",
        "thread/list",
        "thread/loaded/list",
        "thread/read",
        "thread/turns/list",
    }
)


def _parse_list(raw: Optional[str]) -> tuple[str, ...]:
    if not raw:
        return ()
    text = raw.strip()
    if not text:
        return ()
    if text.startswith("["):
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise GatewayError("CONFIG_INVALID", f"invalid JSON list: {exc}") from exc
        if not isinstance(value, list) or not all(
            isinstance(item, str) for item in value
        ):
            raise GatewayError("CONFIG_INVALID", "expected a JSON array of strings")
        return tuple(item.strip() for item in value if item.strip())
    return tuple(item.strip() for item in text.split(";") if item.strip())


def _env_true(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


@dataclass(frozen=True)
class GatewayPolicy:
    allowed_roots: tuple[Path, ...]
    allow_full_access: bool = False
    allowed_mcp_servers: frozenset[str] = frozenset()
    allowed_mcp_tools: frozenset[str] = frozenset()
    allowed_thread_config_keys: frozenset[str] = frozenset()
    allow_readonly_rpc: bool = True
    allowed_rpc_methods: frozenset[str] = frozenset()
    allow_unsafe_rpc: bool = False

    @classmethod
    def from_env(cls) -> "GatewayPolicy":
        roots = tuple(
            Path(value).expanduser().resolve()
            for value in _parse_list(os.environ.get("CODEX_APP_MCP_ALLOWED_ROOTS"))
        )
        return cls(
            allowed_roots=roots,
            allow_full_access=_env_true("CODEX_APP_MCP_ALLOW_FULL_ACCESS"),
            allowed_mcp_servers=frozenset(
                _parse_list(os.environ.get("CODEX_APP_MCP_ALLOWED_SERVERS"))
            ),
            allowed_mcp_tools=frozenset(
                _parse_list(os.environ.get("CODEX_APP_MCP_ALLOWED_TOOLS"))
            ),
            allowed_thread_config_keys=frozenset(
                _parse_list(os.environ.get("CODEX_APP_MCP_ALLOWED_CONFIG_KEYS"))
            ),
            allow_readonly_rpc=not _env_true(
                "CODEX_APP_MCP_DISABLE_READONLY_RPC", default=False
            ),
            allowed_rpc_methods=frozenset(
                _parse_list(os.environ.get("CODEX_APP_MCP_ALLOWED_RPC_METHODS"))
            ),
            allow_unsafe_rpc=_env_true("CODEX_APP_MCP_ALLOW_UNSAFE_RPC"),
        )

    def validate_cwd(self, cwd: Any) -> str:
        if not isinstance(cwd, str) or not cwd.strip():
            raise GatewayError("CWD_REQUIRED", "cwd must be a non-empty absolute path")
        candidate = Path(cwd).expanduser()
        if not candidate.is_absolute():
            raise GatewayError(
                "CWD_NOT_ABSOLUTE", "cwd must be absolute", cwd=str(candidate)
            )
        try:
            resolved = candidate.resolve()
        except OSError as exc:
            raise GatewayError("CWD_INVALID", f"cannot resolve cwd: {exc}") from exc
        if not resolved.exists() or not resolved.is_dir():
            raise GatewayError(
                "CWD_NOT_FOUND", "cwd does not exist or is not a directory"
            )
        if not self.allowed_roots:
            raise GatewayError(
                "ALLOWED_ROOTS_EMPTY",
                "CODEX_APP_MCP_ALLOWED_ROOTS is empty; project operations fail closed",
            )
        for root in self.allowed_roots:
            try:
                resolved.relative_to(root)
                return str(resolved)
            except ValueError:
                continue
        raise GatewayError("CWD_NOT_ALLOWED", "cwd is outside allowlisted roots")

    def validate_sandbox(self, value: Any) -> str:
        if not isinstance(value, str) or value not in SANDBOXES:
            raise GatewayError(
                "SANDBOX_INVALID",
                f"sandbox must be one of {sorted(SANDBOXES)}",
            )
        if value == "danger-full-access" and not self.allow_full_access:
            raise GatewayError(
                "FULL_ACCESS_DISABLED",
                "danger-full-access requires CODEX_APP_MCP_ALLOW_FULL_ACCESS=1",
            )
        return value

    def validate_path(
        self,
        value: Any,
        *,
        field: str = "path",
        must_exist: bool = True,
        directory: Optional[bool] = None,
    ) -> str:
        """Validate an absolute path against the same roots used for project cwd.

        Filesystem and process RPCs need path validation even when the target is
        a file or a not-yet-created path, so ``validate_cwd`` alone is not
        sufficient.
        """
        if not isinstance(value, str) or not value.strip():
            raise GatewayError(
                "PATH_REQUIRED", f"{field} must be a non-empty absolute path"
            )
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            raise GatewayError("PATH_NOT_ABSOLUTE", f"{field} must be absolute")
        try:
            resolved = candidate.resolve(strict=False)
        except OSError as exc:
            raise GatewayError(
                "PATH_INVALID", f"cannot resolve {field}: {exc}"
            ) from exc
        if not self.allowed_roots:
            raise GatewayError(
                "ALLOWED_ROOTS_EMPTY",
                "CODEX_APP_MCP_ALLOWED_ROOTS is empty; path operations fail closed",
            )
        if not any(_is_relative_to(resolved, root) for root in self.allowed_roots):
            raise GatewayError(
                "PATH_NOT_ALLOWED", f"{field} is outside allowlisted roots"
            )
        if must_exist and not resolved.exists():
            raise GatewayError("PATH_NOT_FOUND", f"{field} does not exist: {resolved}")
        if must_exist and directory is True and not resolved.is_dir():
            raise GatewayError(
                "PATH_NOT_DIRECTORY", f"{field} is not a directory: {resolved}"
            )
        if must_exist and directory is False and not resolved.is_file():
            raise GatewayError("PATH_NOT_FILE", f"{field} is not a file: {resolved}")
        return str(resolved)

    def validate_rpc_method(self, value: Any) -> str:
        if not isinstance(value, str) or not RPC_METHOD_RE.fullmatch(value):
            raise GatewayError(
                "RPC_METHOD_INVALID", "method is not a valid app-server RPC name"
            )
        if value == "initialize" or value.startswith("mock/"):
            raise GatewayError(
                "RPC_METHOD_FORBIDDEN", f"method cannot be proxied: {value}"
            )
        if value in READONLY_RPC_METHODS and self.allow_readonly_rpc:
            return value
        allowed = value in self.allowed_rpc_methods or "*" in self.allowed_rpc_methods
        if not allowed:
            raise GatewayError(
                "RPC_METHOD_NOT_ALLOWED",
                "method is not on CODEX_APP_MCP_ALLOWED_RPC_METHODS",
            )
        if not self.allow_unsafe_rpc:
            raise GatewayError(
                "UNSAFE_RPC_DISABLED",
                "stateful/raw RPC requires CODEX_APP_MCP_ALLOW_UNSAFE_RPC=1",
            )
        return value

    def validate_nested_mcp(self, server: Any, tool: Any) -> tuple[str, str]:
        if not isinstance(server, str) or not server:
            raise GatewayError(
                "MCP_SERVER_INVALID", "server must be a non-empty string"
            )
        if not isinstance(tool, str) or not tool:
            raise GatewayError("MCP_TOOL_INVALID", "tool must be a non-empty string")
        if server not in self.allowed_mcp_servers:
            raise GatewayError(
                "MCP_SERVER_NOT_ALLOWED", f"MCP server is not allowlisted: {server}"
            )
        full_name = f"{server}/{tool}"
        if (
            tool not in self.allowed_mcp_tools
            and full_name not in self.allowed_mcp_tools
        ):
            raise GatewayError(
                "MCP_TOOL_NOT_ALLOWED", f"MCP tool is not allowlisted: {full_name}"
            )
        return server, tool

    def validate_thread_config(self, value: Any) -> dict[str, Any]:
        config = validate_params(value, "config")
        for key in config:
            if not isinstance(key, str) or not key:
                raise GatewayError("CONFIG_KEY_INVALID", "config keys must be strings")
            lowered = key.lower()
            sensitive = any(
                lowered == prefix
                or lowered.startswith(prefix + ".")
                or lowered.startswith(prefix + "_")
                for prefix in FORBIDDEN_CONFIG_PREFIXES
            )
            if sensitive or lowered == "model_reasoning_effort":
                raise GatewayError(
                    "CONFIG_KEY_FORBIDDEN",
                    f"security-sensitive thread config key is forbidden: {key}",
                )
            if key not in self.allowed_thread_config_keys:
                raise GatewayError(
                    "CONFIG_KEY_NOT_ALLOWED",
                    f"thread config key is not allowlisted: {key}",
                )
        return config


def validate_text(value: Any, field: str, *, required: bool = True) -> Optional[str]:
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise GatewayError(f"{field.upper()}_INVALID", f"{field} must be a string")
    text = value.strip()
    if required and not text:
        raise GatewayError(f"{field.upper()}_EMPTY", f"{field} must not be empty")
    if "\x00" in text:
        raise GatewayError(f"{field.upper()}_INVALID", f"{field} contains a null byte")
    if len(text) > MAX_TEXT_CHARS:
        raise GatewayError(
            f"{field.upper()}_TOO_LONG",
            f"{field} exceeds {MAX_TEXT_CHARS} characters",
        )
    return text


def validate_thread_id(value: Any) -> str:
    if not isinstance(value, str) or not THREAD_ID_RE.fullmatch(value):
        raise GatewayError("THREAD_ID_INVALID", "threadId has an invalid shape")
    return value


def validate_model(value: Any) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or not MODEL_RE.fullmatch(value):
        raise GatewayError("MODEL_INVALID", "model has an invalid shape")
    return value


def validate_effort(value: Any) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or not EFFORT_RE.fullmatch(value):
        raise GatewayError("EFFORT_INVALID", "effort has an invalid shape")
    return value


def validate_limit(value: Any, *, default: int = 50) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise GatewayError("LIMIT_INVALID", "limit must be an integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise GatewayError("LIMIT_INVALID", "limit must be an integer") from exc
    if number < 1 or number > MAX_LIMIT:
        raise GatewayError("LIMIT_INVALID", f"limit must be between 1 and {MAX_LIMIT}")
    return number


def validate_params(value: Any, field: str = "params") -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise GatewayError(f"{field.upper()}_INVALID", f"{field} must be a JSON object")
    try:
        encoded = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise GatewayError(
            f"{field.upper()}_INVALID", f"{field} must be JSON serializable"
        ) from exc
    if len(encoded) > MAX_SCHEMA_CHARS:
        raise GatewayError(f"{field.upper()}_TOO_LARGE", f"{field} is too large")
    return dict(value)
