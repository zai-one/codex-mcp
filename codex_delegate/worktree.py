"""Git worktree preparation and diffstat collection (no push/merge)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

try:
    from .guard import structured_error
    from .process import GitRunner, default_git_runner
except ImportError:  # pragma: no cover
    from guard import structured_error
    from process import GitRunner, default_git_runner

_TRUNC_DIFFSTAT = 8000


def _truncate(text: Any, limit: int) -> str:
    try:
        from .guard import TRUNCATION_MARKER
    except ImportError:  # pragma: no cover
        from guard import TRUNCATION_MARKER
    s = "" if text is None else str(text)
    if len(s) <= limit:
        return s
    marker = TRUNCATION_MARKER
    if limit <= len(marker):
        return marker[:limit]
    return s[: limit - len(marker)] + marker


def resolve_lanes_parent(repo_root: Path | str, lanes_parent: Optional[Path | str] = None) -> Path:
    """Default sibling directory ``<repo_root>.parent / "codex-lanes"``."""
    if lanes_parent is not None:
        return Path(lanes_parent)
    return Path(repo_root).parent / "codex-lanes"


def worktree_path_for_lane(lanes_parent: Path | str, lane: str) -> Path:
    """Map ``codex/<slug>`` → ``<lanes_parent>/<slug>``."""
    slug = lane.split("/", 1)[-1] if "/" in lane else lane
    return Path(lanes_parent) / slug


def is_path_inside(child: Path | str, parent: Path | str) -> bool:
    """True when child resolves inside parent."""
    try:
        c = Path(child).resolve()
        p = Path(parent).resolve()
        c.relative_to(p)
        return True
    except (OSError, ValueError):
        return False


def prepare_worktree(
    *,
    repo_root: Path | str,
    lane: str,
    base_ref: str,
    lanes_parent: Optional[Path | str] = None,
    git_runner: Optional[GitRunner] = None,
    timeout: float = 60.0,
    require_clean_base: bool = True,
) -> dict[str, Any]:
    """Create or reuse a ``codex/*`` worktree outside the main repo tree."""
    git = git_runner or default_git_runner
    root = Path(repo_root)

    ver = git(["--version"], root, timeout)
    if ver.get("missing") or ver.get("returncode", 1) != 0:
        return structured_error("GIT_MISSING", "git is not available")

    base_check = git(["rev-parse", "--verify", base_ref], root, timeout)
    if base_check.get("returncode", 1) != 0:
        return structured_error("BASE_UNREACHABLE", f"base ref unreachable: {base_ref}")

    if require_clean_base:
        status = git(["status", "--porcelain"], root, timeout)
        if status.get("returncode", 1) != 0:
            return structured_error("BASE_DIRTY", "could not check base tree cleanliness")
        if (status.get("stdout") or "").strip():
            return structured_error("BASE_DIRTY", "base repository working tree is dirty")

    parent = resolve_lanes_parent(root, lanes_parent)
    wt_path = worktree_path_for_lane(parent, lane)

    if is_path_inside(wt_path, root):
        return structured_error(
            "WORKTREE_INSIDE_REPO",
            f"worktree path resolves inside repo: {wt_path}",
        )

    branch = lane

    if wt_path.exists():
        head = git(["rev-parse", "--abbrev-ref", "HEAD"], wt_path, timeout)
        current = (head.get("stdout") or "").strip()
        if head.get("returncode", 1) != 0 or current != branch:
            return structured_error(
                "WORKTREE_EXISTS_CONFLICT",
                f"worktree exists but HEAD is {current!r}, expected {branch!r}",
            )
        return {
            "ok": True,
            "lane": lane,
            "branch": branch,
            "worktree_path": str(wt_path),
            "base_ref": base_ref,
            "reused": True,
        }

    parent.mkdir(parents=True, exist_ok=True)

    add = git(["worktree", "add", "-b", branch, str(wt_path), base_ref], root, timeout)
    if add.get("returncode", 1) != 0:
        add2 = git(["worktree", "add", str(wt_path), branch], root, timeout)
        if add2.get("returncode", 1) != 0:
            msg = (add2.get("stderr") or add.get("stderr") or "worktree add failed").strip()
            return structured_error("WORKTREE_CREATE_FAILED", _truncate(msg, 500))

    if not wt_path.exists():
        return structured_error(
            "WORKTREE_MISSING_AFTER_ADD",
            f"worktree path missing after add: {wt_path}",
        )

    return {
        "ok": True,
        "lane": lane,
        "branch": branch,
        "worktree_path": str(wt_path),
        "base_ref": base_ref,
        "reused": False,
    }


def collect_diff(
    worktree_path: Path | str,
    *,
    git_runner: Optional[GitRunner] = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Collect changed files and a truncated diffstat. Never returns a full patch."""
    git = git_runner or default_git_runner
    wt = Path(worktree_path)

    name_only = git(["diff", "--name-only", "HEAD"], wt, timeout)
    porcelain = git(["status", "--porcelain"], wt, timeout)
    stat = git(["diff", "--stat", "HEAD"], wt, timeout)

    files: set[str] = set()
    for line in (name_only.get("stdout") or "").splitlines():
        name = line.strip()
        if name:
            files.add(name)
    for line in (porcelain.get("stdout") or "").splitlines():
        entry = line[3:].strip() if len(line) >= 3 else line.strip()
        if " -> " in entry:
            entry = entry.split(" -> ", 1)[-1].strip()
        if entry:
            files.add(entry)

    ordered = sorted(files)
    return {
        "ok": True,
        "changed_files": ordered,
        "changed_file_count": len(ordered),
        "diffstat": _truncate((stat.get("stdout") or "").strip(), _TRUNC_DIFFSTAT),
    }
