"""Git worktree lanes used by the app-server-backed delegation surface."""

from __future__ import annotations

import os
import re
import subprocess
import threading
from pathlib import Path
from typing import Any, Optional

from .errors import GatewayError

LANE_RE = re.compile(r"^(?:codex/)?([A-Za-z0-9][A-Za-z0-9._-]{0,79})$")
MAX_GIT_TEXT = 8_000
_LOCKS_GUARD = threading.Lock()
_REPO_LOCKS: dict[str, threading.Lock] = {}


def normalize_lane(value: Any) -> str:
    if not isinstance(value, str):
        raise GatewayError("LANE_INVALID", "lane must be a string")
    match = LANE_RE.fullmatch(value.strip())
    if match is None:
        raise GatewayError(
            "LANE_INVALID",
            "lane must be a simple slug or codex/<slug>",
        )
    return f"codex/{match.group(1)}"


def resolve_lanes_parent(
    repo_root: Path,
    requested: Optional[str],
) -> Path:
    raw = requested or os.environ.get("CODEX_APP_MCP_LANES_PARENT")
    return (
        Path(raw).expanduser().resolve()
        if raw
        else (repo_root.parent / "codex-lanes").resolve()
    )


def lane_path(parent: Path, lane: str) -> Path:
    return (parent / lane.split("/", 1)[1]).resolve()


def _repo_lock(root: Path) -> threading.Lock:
    key = os.path.normcase(str(root.resolve()))
    with _LOCKS_GUARD:
        return _REPO_LOCKS.setdefault(key, threading.Lock())


def _git(
    args: list[str],
    cwd: Path,
    *,
    timeout: float = 60.0,
    allowed_returncodes: tuple[int, ...] = (0,),
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            timeout=max(1.0, float(timeout)),
            check=False,
        )
    except FileNotFoundError as exc:
        raise GatewayError("GIT_MISSING", "git executable is not available") from exc
    except subprocess.TimeoutExpired as exc:
        raise GatewayError(
            "GIT_TIMEOUT", f"git {' '.join(args[:2])} timed out"
        ) from exc
    if result.returncode not in allowed_returncodes:
        message = (result.stderr or result.stdout or "git command failed").strip()
        raise GatewayError(
            "GIT_FAILED",
            message[:1_000],
            returnCode=result.returncode,
            gitArgs=args,
        )
    return result


def prepare_worktree(
    *,
    repo_root: Path,
    lane: str,
    base_ref: str = "HEAD",
    lanes_parent: Optional[str] = None,
    require_clean_base: bool = True,
    timeout: float = 60.0,
) -> dict[str, Any]:
    root = repo_root.resolve()
    branch = normalize_lane(lane)
    parent = resolve_lanes_parent(root, lanes_parent)
    target = lane_path(parent, branch)
    try:
        target.relative_to(root)
    except ValueError:
        pass
    else:
        raise GatewayError(
            "WORKTREE_INSIDE_REPO",
            f"lane worktree cannot be inside the base repository: {target}",
        )

    with _repo_lock(root):
        _git(["rev-parse", "--show-toplevel"], root, timeout=timeout)
        _git(["rev-parse", "--verify", base_ref], root, timeout=timeout)
        if require_clean_base:
            status = _git(
                ["status", "--porcelain"], root, timeout=timeout
            ).stdout.strip()
            if status:
                raise GatewayError(
                    "BASE_DIRTY",
                    "base repository has staged, unstaged, or untracked changes",
                )
        if target.exists():
            if not target.is_dir():
                raise GatewayError(
                    "WORKTREE_EXISTS_CONFLICT",
                    f"lane path exists and is not a directory: {target}",
                )
            current = _git(
                ["rev-parse", "--abbrev-ref", "HEAD"], target, timeout=timeout
            ).stdout.strip()
            if current != branch:
                raise GatewayError(
                    "WORKTREE_EXISTS_CONFLICT",
                    f"lane worktree is on {current!r}, expected {branch!r}",
                )
            return {
                "lane": branch,
                "branch": branch,
                "worktreePath": str(target),
                "baseRef": base_ref,
                "reused": True,
            }

        parent.mkdir(parents=True, exist_ok=True)
        created = _git(
            ["worktree", "add", "-b", branch, str(target), base_ref],
            root,
            timeout=timeout,
            allowed_returncodes=(0, 128),
        )
        if created.returncode != 0:
            # The branch may already exist after an earlier worktree was removed.
            _git(["worktree", "add", str(target), branch], root, timeout=timeout)
        if not target.is_dir():
            raise GatewayError(
                "WORKTREE_MISSING_AFTER_ADD",
                f"git reported success but lane path is absent: {target}",
            )
        return {
            "lane": branch,
            "branch": branch,
            "worktreePath": str(target),
            "baseRef": base_ref,
            "reused": False,
        }


def collect_diff(worktree: Path, *, timeout: float = 60.0) -> dict[str, Any]:
    wt = worktree.resolve()
    if not wt.is_dir():
        raise GatewayError("WORKTREE_MISSING", f"lane worktree is absent: {wt}")
    status = _git(
        ["status", "--porcelain=v1", "-z", "--untracked-files=all"],
        wt,
        timeout=timeout,
    ).stdout
    changed: list[str] = []
    for record in status.split("\0"):
        if len(record) < 4:
            continue
        path = record[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path and path not in changed:
            changed.append(path)
    changed.sort()
    stat = _git(["diff", "--stat", "HEAD"], wt, timeout=timeout).stdout.strip()
    untracked = [
        record[3:] for record in status.split("\0") if record.startswith("?? ")
    ]
    untracked_rows: list[str] = []
    for path in untracked[:50]:
        result = _git(
            ["diff", "--no-index", "--stat", "--", os.devnull, path],
            wt,
            timeout=timeout,
            allowed_returncodes=(0, 1),
        )
        untracked_rows.extend(
            line for line in result.stdout.splitlines() if "|" in line
        )
    return {
        "changedFiles": changed,
        "changedFileCount": len(changed),
        "diffstat": stat[:MAX_GIT_TEXT],
        "untrackedStat": "\n".join(untracked_rows)[:MAX_GIT_TEXT],
    }


def list_lanes(repo_root: Path, *, timeout: float = 60.0) -> list[dict[str, Any]]:
    root = repo_root.resolve()
    raw = _git(["worktree", "list", "--porcelain"], root, timeout=timeout).stdout
    rows: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in [*raw.splitlines(), ""]:
        if not line:
            branch = current.get("branch")
            if isinstance(branch, str) and branch.startswith("refs/heads/codex/"):
                rows.append(
                    {
                        "lane": branch.removeprefix("refs/heads/"),
                        "branch": branch.removeprefix("refs/heads/"),
                        "worktreePath": current.get("worktree"),
                        "head": current.get("HEAD"),
                        "detached": bool(current.get("detached")),
                    }
                )
            current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value if value else True
    return rows
