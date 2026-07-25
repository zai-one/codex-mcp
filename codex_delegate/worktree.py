"""Git worktree preparation and diffstat collection (no push/merge)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

try:
    from .guard import structured_error
    from .lane_lock import repo_git_lock
    from .process import GitRunner, default_git_runner
except ImportError:  # pragma: no cover
    from guard import structured_error
    from lane_lock import repo_git_lock
    from process import GitRunner, default_git_runner

_TRUNC_DIFFSTAT = 8000

# C-style escapes used by git when core.quotePath quotes a path.
_GIT_SIMPLE_ESCAPES = {
    "n": "\n",
    "t": "\t",
    "r": "\r",
    "a": "\a",
    "b": "\b",
    "f": "\f",
    "v": "\v",
    "\\": "\\",
    '"': '"',
}


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


def decode_git_path(raw: str) -> str:
    """Decode a git porcelain / name-only path that may be C-quoted.

    Git quotes paths containing spaces, non-ASCII, or control characters as
    double-quoted C strings with octal escapes (``core.quotePath``). Argv and
    ``Path`` construction must receive the decoded form — never with the
    surrounding quotes left in place.
    """
    s = "" if raw is None else str(raw).strip()
    if len(s) < 2 or s[0] != '"' or s[-1] != '"':
        return s
    inner = s[1:-1]
    out: list[str] = []
    i = 0
    while i < len(inner):
        ch = inner[i]
        if ch != "\\" or i + 1 >= len(inner):
            out.append(ch)
            i += 1
            continue
        nxt = inner[i + 1]
        if nxt in _GIT_SIMPLE_ESCAPES:
            out.append(_GIT_SIMPLE_ESCAPES[nxt])
            i += 2
            continue
        # Up to three octal digits (git's usual form for non-ASCII bytes).
        if nxt in "01234567":
            j = i + 1
            digits = []
            while j < len(inner) and len(digits) < 3 and inner[j] in "01234567":
                digits.append(inner[j])
                j += 1
            out.append(chr(int("".join(digits), 8)))
            i = j
            continue
        # Unknown escape: keep the escaped character (git does not emit these).
        out.append(nxt)
        i += 2
    # Git emits octal escapes as raw bytes of the path encoding (usually UTF-8).
    raw_bytes = bytes(ord(c) & 0xFF for c in out)
    try:
        return raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return "".join(out)


def resolve_lanes_parent(repo_root: Path | str, lanes_parent: Optional[Path | str] = None) -> Path:
    """Default sibling directory ``<repo_root>.parent / "codex-lanes"``."""
    if lanes_parent is not None:
        return Path(lanes_parent)
    return Path(repo_root).parent / "codex-lanes"


def worktree_path_for_lane(lanes_parent: Path | str, lane: str) -> Path:
    """Map ``codex/<slug>`` → ``<lanes_parent>/<slug>``.

    ``Path`` joins preserve spaces and non-ASCII; callers must pass the path as
    an argv element (never shell-quoted).
    """
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
    """Create or reuse a ``codex/*`` worktree outside the main repo tree.

    Contract:
    * On success the worktree directory exists on disk and HEAD is ``lane``.
    * On failure after a successful ``worktree add``, the directory may still
      exist — that is intentional operator evidence, not garbage to auto-delete.
      A later call with the same lane reuses it when HEAD matches.
    * Concurrent callers for the same repo are serialised via ``repo_git_lock``
      so two ``worktree add`` races cannot corrupt the registry.
    * Never pushes, merges, or deletes worktrees.
    """
    git = git_runner or default_git_runner
    root = Path(repo_root)

    with repo_git_lock(root):
        return _prepare_worktree_locked(
            root=root,
            lane=lane,
            base_ref=base_ref,
            lanes_parent=lanes_parent,
            git=git,
            timeout=timeout,
            require_clean_base=require_clean_base,
        )


def _prepare_worktree_locked(
    *,
    root: Path,
    lane: str,
    base_ref: str,
    lanes_parent: Optional[Path | str],
    git: GitRunner,
    timeout: float,
    require_clean_base: bool,
) -> dict[str, Any]:
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

    # Paths with spaces go in as a single argv element — never shell-quoted.
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
    """Collect changed files and a truncated diffstat. Never returns a full patch.

    Always returns a dict (never raises). Missing / unreadable worktrees yield
    an empty change set with ``ok: false`` rather than a Python exception.
    """
    git = git_runner or default_git_runner
    wt = Path(worktree_path)

    if not wt.is_dir():
        return {
            "ok": False,
            "changed_files": [],
            "changed_file_count": 0,
            "diffstat": "",
            "error": "WORKTREE_MISSING",
            "message": f"worktree path missing or not a directory: {wt}",
        }

    try:
        name_only = git(["diff", "--name-only", "HEAD"], wt, timeout)
        porcelain = git(["status", "--porcelain"], wt, timeout)
        stat = git(["diff", "--stat", "HEAD"], wt, timeout)
    except OSError as exc:
        return {
            "ok": False,
            "changed_files": [],
            "changed_file_count": 0,
            "diffstat": "",
            "error": "DIFF_FAILED",
            "message": f"could not collect diff: {exc}",
        }

    files: set[str] = set()
    for line in (name_only.get("stdout") or "").splitlines():
        name = decode_git_path(line.strip())
        if name:
            files.add(name)
    for line in (porcelain.get("stdout") or "").splitlines():
        if not line:
            continue
        # porcelain v1: XY + space + path (path may be quoted / rename pair).
        entry = line[3:] if len(line) >= 3 else line
        entry = entry.strip()
        if " -> " in entry:
            entry = entry.split(" -> ", 1)[-1].strip()
        entry = decode_git_path(entry)
        if entry:
            files.add(entry)

    ordered = sorted(files)
    return {
        "ok": True,
        "changed_files": ordered,
        "changed_file_count": len(ordered),
        "diffstat": _truncate((stat.get("stdout") or "").strip(), _TRUNC_DIFFSTAT),
    }
