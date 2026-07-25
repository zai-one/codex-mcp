"""Read-only probes for Codex binary, auth presence, git, and lanes."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Optional

try:
    from .guard import (
        ALLOWED_SANDBOX_MODES,
        DEFAULT_CODEX_BIN,
        DEFAULT_TIMEOUT_SECONDS,
        DOCTOR_TIMEOUT_SECONDS,
        HARD_CAP_TIMEOUT_SECONDS,
        SANDBOX_READ_ONLY,
        SANDBOX_WORKSPACE_WRITE,
        structured_error,
        validate_codex_bin,
    )
    from .runner import (
        GitRunner,
        SubprocessRunner,
        WhichFn,
        collect_diff,
        default_git_runner,
        default_subprocess_runner,
        default_which,
        run_readonly_cli,
    )
except ImportError:  # pragma: no cover
    from guard import (
        ALLOWED_SANDBOX_MODES,
        DEFAULT_CODEX_BIN,
        DEFAULT_TIMEOUT_SECONDS,
        DOCTOR_TIMEOUT_SECONDS,
        HARD_CAP_TIMEOUT_SECONDS,
        SANDBOX_READ_ONLY,
        SANDBOX_WORKSPACE_WRITE,
        structured_error,
        validate_codex_bin,
    )
    from runner import (
        GitRunner,
        SubprocessRunner,
        WhichFn,
        collect_diff,
        default_git_runner,
        default_subprocess_runner,
        default_which,
        run_readonly_cli,
    )

try:
    from . import __version__ as PACKAGE_VERSION
except ImportError:  # pragma: no cover
    PACKAGE_VERSION = "0.1.0"

_VERSION_RE = re.compile(r"codex-cli\s+(\S+)", re.IGNORECASE)
_POSITIVE_AUTH = re.compile(r"logged\s+in", re.IGNORECASE)
_NEGATIVE_AUTH = re.compile(
    r"(not\s+logged\s+in|please\s+login|unauthorized)",
    re.IGNORECASE,
)
_METHOD_CHATGPT = re.compile(r"chatgpt", re.IGNORECASE)
_METHOD_API = re.compile(r"api\s*key", re.IGNORECASE)


def probe_codex_version(
    *,
    codex_bin: str = DEFAULT_CODEX_BIN,
    subprocess_runner: Optional[SubprocessRunner] = None,
    which: Optional[WhichFn] = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Run ``codex --version`` and parse ``codex-cli <semver>``."""
    result = run_readonly_cli(
        ["--version"],
        codex_bin=codex_bin,
        timeout=timeout,
        subprocess_runner=subprocess_runner,
        which=which,
    )
    if not result.get("ok", True) and result.get("error"):
        return {**result, "version": None, "binary_found": False}
    if result.get("missing"):
        return structured_error("CODEX_MISSING", "codex binary not found", version=None, binary_found=False)

    stdout = (result.get("stdout") or "").strip()
    stderr = (result.get("stderr") or "").strip()
    text = stdout or stderr
    match = _VERSION_RE.search(text)
    version = match.group(1) if match else (text.split()[-1] if text else None)
    ok = result.get("returncode", 1) == 0
    return {
        "ok": ok,
        "binary_found": True,
        "version": version,
        "raw": text[:200],
    }


def probe_auth_presence(
    *,
    codex_bin: str = DEFAULT_CODEX_BIN,
    subprocess_runner: Optional[SubprocessRunner] = None,
    which: Optional[WhichFn] = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Probe auth via ``codex login status``. Never returns raw credentials."""
    result = run_readonly_cli(
        ["login", "status"],
        codex_bin=codex_bin,
        timeout=timeout,
        subprocess_runner=subprocess_runner,
        which=which,
    )
    if result.get("error") and not result.get("returncode") and "stdout" not in result:
        return {
            "ok": False,
            "auth_present": False,
            "method": None,
            "auth_file_read": False,
            "error": result.get("error"),
            "message": result.get("message"),
        }
    if result.get("missing"):
        return {
            "ok": False,
            "auth_present": False,
            "method": None,
            "auth_file_read": False,
            "error": "CODEX_MISSING",
            "message": "codex binary not found",
        }

    stdout = (result.get("stdout") or "").strip()
    stderr = (result.get("stderr") or "").strip()
    text = stdout or stderr
    rc = result.get("returncode", 1)

    auth_present = False
    method: Optional[str] = None
    if rc == 0 and _POSITIVE_AUTH.search(text) and not _NEGATIVE_AUTH.search(text):
        auth_present = True
        if _METHOD_CHATGPT.search(text):
            method = "ChatGPT"
        elif _METHOD_API.search(text):
            method = "API key"
    elif _NEGATIVE_AUTH.search(text):
        auth_present = False

    return {
        "ok": True,
        "auth_present": auth_present,
        "method": method,
        "auth_file_read": False,
    }


def probe_git_available(
    *,
    git_runner: Optional[GitRunner] = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Probe ``git --version``."""
    git = git_runner or default_git_runner
    result = git(["--version"], None, timeout)
    if result.get("missing"):
        return {"ok": False, "available": False, "version": None, "error": "GIT_MISSING", "message": "git not found"}
    text = (result.get("stdout") or "").strip()
    ok = result.get("returncode", 1) == 0
    return {
        "ok": ok,
        "available": ok,
        "version": text or None,
    }


def run_doctor_json(
    *,
    codex_bin: str = DEFAULT_CODEX_BIN,
    subprocess_runner: Optional[SubprocessRunner] = None,
    which: Optional[WhichFn] = None,
    timeout: float = DOCTOR_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Run ``codex doctor --json`` (already redacted by Codex).

    Uses ``DOCTOR_TIMEOUT_SECONDS`` (≤ 60s), not the delegation timeout.
    Live measurement: ``codex doctor --json`` with stdin at DEVNULL hung for
    ≥277s on codex-cli 0.144.1 (see CODEX-CLI-FACTS.md). On expiry return
    structured ``DOCTOR_TIMEOUT`` instead of hanging the MCP client.
    """
    bound = min(float(timeout), DOCTOR_TIMEOUT_SECONDS) if timeout else DOCTOR_TIMEOUT_SECONDS
    # Never allow a caller to raise the doctor bound above the package cap.
    bound = max(1.0, min(bound, DOCTOR_TIMEOUT_SECONDS))
    result = run_readonly_cli(
        ["doctor", "--json"],
        codex_bin=codex_bin,
        timeout=bound,
        subprocess_runner=subprocess_runner,
        which=which,
    )
    if result.get("error") and "stdout" not in result:
        return result
    if result.get("missing"):
        return structured_error("CODEX_MISSING", "codex binary not found")
    if result.get("timedOut"):
        return structured_error(
            "DOCTOR_TIMEOUT",
            f"codex doctor --json timed out after {bound}s "
            f"(live hang measured ≥277s; bound is DOCTOR_TIMEOUT_SECONDS)",
            timeout_seconds=bound,
        )

    stdout = result.get("stdout") or ""
    try:
        parsed = json.loads(stdout)
        return {
            "ok": result.get("returncode", 1) == 0,
            "doctor": parsed,
            "returncode": result.get("returncode"),
        }
    except (json.JSONDecodeError, TypeError, ValueError):
        return {
            "ok": result.get("returncode", 1) == 0,
            "doctor": None,
            "text_preview": (stdout or result.get("stderr") or "")[:1000],
            "returncode": result.get("returncode"),
        }


def run_models(
    *,
    codex_bin: str = DEFAULT_CODEX_BIN,
    subprocess_runner: Optional[SubprocessRunner] = None,
    which: Optional[WhichFn] = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Run ``codex debug models`` and reduce to a compact catalog."""
    result = run_readonly_cli(
        ["debug", "models"],
        codex_bin=codex_bin,
        timeout=timeout,
        subprocess_runner=subprocess_runner,
        which=which,
    )
    if result.get("error") and "stdout" not in result:
        return result
    if result.get("missing"):
        return structured_error("CODEX_MISSING", "codex binary not found")

    stdout = result.get("stdout") or ""
    try:
        raw = json.loads(stdout)
    except (json.JSONDecodeError, TypeError, ValueError):
        return {
            "ok": False,
            "models": [],
            "error": "MODELS_PARSE_FAILED",
            "message": "could not parse codex debug models output",
            "text_preview": stdout[:500],
        }

    models_list: list[Any]
    if isinstance(raw, list):
        models_list = raw
    elif isinstance(raw, dict):
        models_list = raw.get("models") or raw.get("data") or list(raw.values())
        if models_list and not isinstance(models_list[0], dict):
            models_list = [raw] if "slug" in raw or "id" in raw else []
    else:
        models_list = []

    reduced: list[dict[str, Any]] = []
    for item in models_list:
        if not isinstance(item, dict):
            continue
        slug = item.get("slug") or item.get("id") or item.get("name")
        if not slug:
            continue
        levels = item.get("supported_reasoning_levels") or []
        efforts: list[str] = []
        if isinstance(levels, list):
            for lvl in levels:
                if isinstance(lvl, dict) and lvl.get("effort"):
                    efforts.append(str(lvl["effort"]))
                elif isinstance(lvl, str):
                    efforts.append(lvl)
        reduced.append({
            "slug": str(slug),
            "display_name": item.get("display_name") or item.get("displayName") or str(slug),
            "default_reasoning_level": (
                item.get("default_reasoning_level")
                or item.get("defaultReasoningLevel")
                or (efforts[0] if efforts else None)
            ),
            "supported_reasoning_levels": efforts,
        })

    return {
        "ok": result.get("returncode", 1) == 0,
        "models": reduced,
        "count": len(reduced),
    }


def list_lanes(
    repo_root: Path | str,
    *,
    git_runner: Optional[GitRunner] = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """List ``codex/*`` worktrees with per-lane changed_files count + diffstat."""
    git = git_runner or default_git_runner
    root = Path(repo_root)
    result = git(["worktree", "list", "--porcelain"], root, timeout)
    if result.get("missing"):
        return structured_error("GIT_MISSING", "git not found", lanes=[])
    if result.get("returncode", 1) != 0:
        return structured_error(
            "WORKTREE_LIST_FAILED",
            (result.get("stderr") or "worktree list failed")[:500],
            lanes=[],
        )

    lanes: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in (result.get("stdout") or "").splitlines():
        if line.startswith("worktree "):
            if current:
                _maybe_add_lane(current, lanes, git_runner=git, timeout=timeout)
            current = {"worktree_path": line[len("worktree "):].strip()}
        elif line.startswith("branch "):
            ref = line[len("branch "):].strip()
            # refs/heads/codex/foo
            branch = ref
            if branch.startswith("refs/heads/"):
                branch = branch[len("refs/heads/"):]
            current["branch"] = branch
        elif line.startswith("HEAD "):
            current["head"] = line[len("HEAD "):].strip()
        elif line.strip() == "":
            if current:
                _maybe_add_lane(current, lanes, git_runner=git, timeout=timeout)
                current = {}
    if current:
        _maybe_add_lane(current, lanes, git_runner=git, timeout=timeout)

    return {"ok": True, "lanes": lanes, "count": len(lanes)}


def _maybe_add_lane(
    entry: dict[str, Any],
    lanes: list[dict[str, Any]],
    *,
    git_runner: Optional[GitRunner],
    timeout: float,
) -> None:
    branch = entry.get("branch") or ""
    if not branch.startswith("codex/"):
        return
    wt = entry.get("worktree_path")
    diff = collect_diff(wt, git_runner=git_runner, timeout=timeout) if wt else {
        "changed_files": [],
        "diffstat": "",
    }
    lanes.append({
        "lane": branch,
        "branch": branch,
        "worktree_path": wt,
        "head": entry.get("head"),
        "changed_files": diff.get("changed_files") or [],
        "changed_file_count": len(diff.get("changed_files") or []),
        "diffstat": diff.get("diffstat") or "",
    })


def build_status_report(
    *,
    codex_bin: str = DEFAULT_CODEX_BIN,
    allowed_roots: Optional[list[Path | str]] = None,
    lanes_parent: Optional[str] = None,
    ignore_user_config: bool = True,
    default_base_ref: str = "HEAD",
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    subprocess_runner: Optional[SubprocessRunner] = None,
    git_runner: Optional[GitRunner] = None,
    which: Optional[WhichFn] = None,
) -> dict[str, Any]:
    """Aggregate health report for ``codex_delegate_status``."""
    which_fn = which or default_which
    try:
        bin_v = validate_codex_bin(codex_bin, from_client=False)
    except Exception:
        bin_v = codex_bin or DEFAULT_CODEX_BIN

    binary_found = bool(which_fn(bin_v) or (os.path.isabs(bin_v) and Path(bin_v).exists()))
    version_info = probe_codex_version(
        codex_bin=bin_v,
        subprocess_runner=subprocess_runner,
        which=which_fn,
    ) if binary_found else {"ok": False, "version": None, "binary_found": False}
    auth = probe_auth_presence(
        codex_bin=bin_v,
        subprocess_runner=subprocess_runner,
        which=which_fn,
    ) if binary_found else {
        "ok": False,
        "auth_present": False,
        "method": None,
        "auth_file_read": False,
    }
    git_info = probe_git_available(git_runner=git_runner)

    roots = [str(Path(r)) for r in (allowed_roots or [])]
    lanes_by_root: dict[str, str] = {}
    for r in roots:
        parent = lanes_parent or str(Path(r).parent / "codex-lanes")
        lanes_by_root[r] = parent

    return {
        "ok": True,
        "server": {
            "name": "codex-delegate",
            "version": PACKAGE_VERSION,
        },
        "codex": {
            "binary": bin_v,
            "binary_found": binary_found and bool(version_info.get("binary_found", binary_found)),
            "version": version_info.get("version"),
        },
        "auth": {
            "auth_present": bool(auth.get("auth_present")),
            "method": auth.get("method"),
            "auth_file_read": False,
        },
        "git": {
            "available": bool(git_info.get("available")),
            "version": git_info.get("version"),
        },
        "roots": {
            "allowed": roots,
            "lanes_parent_by_root": lanes_by_root,
            "configured": bool(roots),
        },
        "sandbox": {
            "plan_default": SANDBOX_READ_ONLY,
            "execute_default": SANDBOX_WORKSPACE_WRITE,
            "allowed_modes": sorted(ALLOWED_SANDBOX_MODES),
            "danger_full_access_allowed": False,
            "enforcement_note": (
                "Probe A (CODEX-CLI-FACTS.md): default codex exec sandbox is read-only and "
                "enforced on Windows. Probe B: -c sandbox_mode=... is ignored for exec; only "
                "-s controls the sandbox. Probe C: -s workspace-write grants writes under --cd. "
                "This server never emits danger-full-access or -c sandbox_mode overrides."
            ),
        },
        "config": {
            "ignore_user_config": ignore_user_config,
            "default_base_ref": default_base_ref,
            "timeout_seconds": timeout_seconds,
            "hard_cap_timeout_seconds": HARD_CAP_TIMEOUT_SECONDS,
        },
    }
