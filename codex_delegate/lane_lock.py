"""Per-repo / per-lane run serialisation for the long-lived MCP server.

Two ``tools/call`` arrivals for the same lane (or concurrent ``git worktree add``
against the same repo) can corrupt the worktree registry. Policy is fail-closed:

* at most one in-flight run per ``(repo_root, lane)`` — second caller gets
  ``LANE_BUSY``;
* ``prepare_worktree`` holds a per-repo lock for the duration of its git calls
  so two lanes cannot race ``worktree add`` on the same repository.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

try:
    from .guard import structured_error
except ImportError:  # pragma: no cover
    from guard import structured_error

_guard = threading.Lock()
_active_lanes: set[tuple[str, str]] = set()
_repo_locks: dict[str, threading.Lock] = {}


def _norm_repo_key(repo_root: Path | str) -> str:
    try:
        text = str(Path(repo_root).resolve())
    except OSError:
        text = str(repo_root)
    # Windows paths are case-insensitive; normalise so locks collide correctly.
    return text.replace("\\", "/").casefold()


def _lane_key(repo_root: Path | str, lane: str) -> tuple[str, str]:
    return (_norm_repo_key(repo_root), str(lane))


def try_acquire_lane(repo_root: Path | str, lane: str) -> Optional[dict[str, Any]]:
    """Mark ``lane`` busy for ``repo_root``. Return a structured error if busy."""
    key = _lane_key(repo_root, lane)
    with _guard:
        if key in _active_lanes:
            return structured_error(
                "LANE_BUSY",
                f"lane already has a run in flight: {lane}",
                lane=lane,
            )
        _active_lanes.add(key)
    return None


def release_lane(repo_root: Path | str, lane: str) -> None:
    key = _lane_key(repo_root, lane)
    with _guard:
        _active_lanes.discard(key)


@contextmanager
def lane_run_scope(repo_root: Path | str, lane: str) -> Iterator[Optional[dict[str, Any]]]:
    """Context manager: yield a structured error if busy, else None while held."""
    err = try_acquire_lane(repo_root, lane)
    if err is not None:
        yield err
        return
    try:
        yield None
    finally:
        release_lane(repo_root, lane)


def repo_git_lock(repo_root: Path | str) -> threading.Lock:
    """Return the re-entrant-safe mutex for git worktree mutations on this repo."""
    key = _norm_repo_key(repo_root)
    with _guard:
        lock = _repo_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _repo_locks[key] = lock
        return lock


def reset_locks_for_tests() -> None:
    """Drop all lock state — test helper only."""
    with _guard:
        _active_lanes.clear()
        _repo_locks.clear()
