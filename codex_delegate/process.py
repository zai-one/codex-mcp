"""Injectable process runners (git + subprocess). UTF-8 decode, fail-closed verbs."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

try:
    from .guard import FORBIDDEN_CLI_FLAGS, GuardError
except ImportError:  # pragma: no cover
    from guard import FORBIDDEN_CLI_FLAGS, GuardError

GitRunner = Callable[[Sequence[str], Optional[Path], float], dict]
SubprocessRunner = Callable[..., dict]
WhichFn = Callable[[str], Optional[str]]

_FORBIDDEN_GIT_VERBS = frozenset({
    "push", "merge", "pull", "rebase", "cherry-pick", "reset", "clean",
})

# Extra budget granted once, after a tree kill, to drain pipes before abandoning.
TREE_KILL_GRACE_SECONDS = 10.0


def result_dict(
    args: Sequence[str],
    returncode: int,
    stdout: str = "",
    stderr: str = "",
    timed_out: bool = False,
    *,
    missing: bool = False,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "args": list(args),
        "returncode": returncode,
        "stdout": stdout,
        "stderr": stderr,
        "timedOut": timed_out,
    }
    if missing:
        out["missing"] = True
    return out


def skip_git_globals(args: Sequence[str]) -> list[str]:
    """Skip global git options before the verb (``-C <path>``, ``-c <kv>``, etc.)."""
    tokens = list(args)
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t in {"-C", "-c"}:
            i += 2
            continue
        if t.startswith("-C") and t != "-C":
            i += 1
            continue
        if t.startswith("-c") and t != "-c" and "=" in t:
            i += 1
            continue
        if t in {"--git-dir", "--work-tree", "--namespace", "--super-prefix"}:
            i += 2
            continue
        if t.startswith("-") and t not in {"--"}:
            if t in {
                "--bare", "--no-replace-objects", "--literal-pathspecs",
                "--glob-pathspecs", "--noglob-pathspecs", "--icase-pathspecs",
            }:
                i += 1
                continue
            break
        break
    return tokens[i:]


def reject_forbidden_git_args(args: Sequence[str]) -> None:
    rest = skip_git_globals(args)
    if not rest:
        return
    verb = rest[0].lower()
    if verb in _FORBIDDEN_GIT_VERBS:
        raise GuardError("GIT_VERB_FORBIDDEN", f"git verb forbidden: {verb}")


def reject_forbidden_flags(args: Sequence[str]) -> None:
    for tok in args:
        if tok in FORBIDDEN_CLI_FLAGS or str(tok).startswith("--dangerously"):
            raise GuardError("ARGV_FORBIDDEN_FLAG", f"forbidden flag: {tok}")


def kill_process_tree(proc: "subprocess.Popen[Any]") -> None:
    """Kill the child **and its descendants**.

    ``subprocess.run(timeout=...)`` only kills the direct child, then keeps
    blocking in ``communicate()`` until every process still holding the
    inherited stdout/stderr pipe handles exits. Measured on this host: a 45s
    bound on ``codex doctor --json`` produced 240s of wall clock, because the
    grandchildren kept the pipes open. A declared timeout that does not bound
    wall clock is not a timeout.
    """
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=TREE_KILL_GRACE_SECONDS,
                check=False,
                stdin=subprocess.DEVNULL,
            )
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:  # noqa: BLE001 — best effort; proc.kill() below is the fallback
        pass
    try:
        proc.kill()
    except Exception:  # noqa: BLE001
        pass


def run_bounded(
    cmd: Sequence[str],
    cwd: Optional[Path],
    timeout: float,
    *,
    input_text: Optional[str] = None,
) -> dict[str, Any]:
    """Spawn with a timeout that actually bounds wall clock.

    stdin is ``DEVNULL`` unless ``input_text`` is supplied: the MCP server's
    stdin is the JSON-RPC channel, and a child that reads it can corrupt or
    deadlock the session.
    """
    argv = [str(a) for a in cmd]
    popen_kwargs: dict[str, Any] = {
        "cwd": str(cwd) if cwd is not None else None,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "stdin": subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if os.name != "nt":
        # Own process group so killpg reaches descendants.
        popen_kwargs["start_new_session"] = True

    try:
        proc = subprocess.Popen(argv, **popen_kwargs)  # noqa: S603
    except FileNotFoundError:
        return result_dict(argv, 127, "", f"binary not found: {argv[0] if argv else '?'}", missing=True)

    try:
        stdout, stderr = proc.communicate(input=input_text, timeout=timeout)
        return result_dict(argv, proc.returncode, stdout or "", stderr or "")
    except subprocess.TimeoutExpired:
        kill_process_tree(proc)
        try:
            stdout, stderr = proc.communicate(timeout=TREE_KILL_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            # Pipes are still held by something we could not reach. Abandon them
            # rather than block the caller past its own bound.
            stdout, stderr = "", ""
        return result_dict(
            argv, 124, stdout or "", stderr or f"timed out after {timeout}s", timed_out=True
        )


def default_git_runner(
    args: Sequence[str],
    cwd: Optional[Path],
    timeout: float,
) -> dict[str, Any]:
    """Real git runner. Rejects forbidden verbs before spawn."""
    reject_forbidden_git_args(args)
    result = run_bounded(["git", *[str(a) for a in args]], cwd, timeout)
    if result.get("missing"):
        result["stderr"] = "git not found"
    return result


def default_subprocess_runner(
    args: Sequence[str],
    cwd: Optional[Path],
    timeout: float,
    *,
    input_text: Optional[str] = None,
) -> dict[str, Any]:
    """Real Codex spawn with UTF-8 decoding, forbidden-flag gate, bounded wall clock."""
    reject_forbidden_flags(args)
    return run_bounded(args, cwd, timeout, input_text=input_text)


def default_which(name: str) -> Optional[str]:
    return shutil.which(name)
