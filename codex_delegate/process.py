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


def _as_text(chunk: Any) -> str:
    """Normalise TimeoutExpired partial output (str / bytes / None) to str."""
    if chunk is None:
        return ""
    if isinstance(chunk, bytes):
        return chunk.decode("utf-8", errors="replace")
    return str(chunk)


def kill_process_tree(proc: "subprocess.Popen[Any]") -> None:
    """Kill the child **and its descendants**.

    ``subprocess.run(timeout=...)`` only kills the direct child, then keeps
    blocking in ``communicate()`` until every process still holding the
    inherited stdout/stderr pipe handles exits. Measured on this host: a 45s
    bound on ``codex doctor --json`` produced 240s of wall clock, because the
    grandchildren kept the pipes open. A declared timeout that does not bound
    wall clock is not a timeout.

    PID-reuse window (cannot be closed from userland): between the moment the
    child exits and our ``taskkill`` / ``killpg`` lands, the OS may reassign
    that PID to an unrelated process. We accept the race; both branches are
    best-effort and fall back to ``proc.kill()`` on the original handle, which
    is safe if the original child is already reaped.
    """
    try:
        if os.name == "nt":
            # taskkill may be absent on stripped hosts (FileNotFoundError) or
            # return non-zero against an already-dead PID — both are swallowed.
            # /T = tree; without it we reintroduce the 240s grandchild hang.
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=TREE_KILL_GRACE_SECONDS,
                check=False,
                stdin=subprocess.DEVNULL,
            )
        else:
            # start_new_session=True at spawn puts the child in its own group.
            # If that ever regresses, getpgid(child) == getpgrp() and killpg
            # would signal *our* group — including the MCP server. Refuse.
            try:
                child_pgid = os.getpgid(proc.pid)
            except (ProcessLookupError, PermissionError, OSError):
                child_pgid = None
            if child_pgid is None or child_pgid == os.getpgrp():
                # Fall through to proc.kill() below — never self-kill.
                pass
            else:
                os.killpg(child_pgid, signal.SIGKILL)
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

    All I/O goes through ``Popen.communicate()`` — never a bare
    ``stdin.write`` + pipe read — so a 60_000-char goal or a large JSONL
    stream cannot fill an OS pipe buffer and deadlock us. There is no path
    that mixes raw pipe I/O with a timeout.
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
        # Own process group so killpg reaches descendants without touching us.
        popen_kwargs["start_new_session"] = True

    try:
        proc = subprocess.Popen(argv, **popen_kwargs)  # noqa: S603
    except FileNotFoundError:
        # Only FileNotFoundError maps to missing=True (CODEX_MISSING / git-not-found).
        # PermissionError / NotADirectoryError / OSError from a present-but-unlaunchable
        # binary (or a bad cwd) propagate to the caller — same as pre-Popen semantics.
        return result_dict(argv, 127, "", f"binary not found: {argv[0] if argv else '?'}", missing=True)

    try:
        stdout, stderr = proc.communicate(input=input_text, timeout=timeout)
        return result_dict(argv, proc.returncode, stdout or "", stderr or "")
    except subprocess.TimeoutExpired as first_exc:
        kill_process_tree(proc)
        try:
            # Second drain: first communicate() may already have consumed part of
            # the stream into first_exc.stdout/stderr; retrying does not lose it
            # (CPython contract). Callers that key on timedOut (doctor → DOCTOR_TIMEOUT,
            # delegate → status=timeout) never treat partial JSONL as success.
            # parse_event_stream turns a half-line into unparsed_lines += 1 and
            # never raises; half a doctor JSON body fails json.loads → text_preview.
            stdout, stderr = proc.communicate(timeout=TREE_KILL_GRACE_SECONDS)
        except subprocess.TimeoutExpired as second_exc:
            # Pipes still held by something we could not reach. Prefer any partial
            # bytes already collected, then abandon rather than block past the bound.
            stdout = _as_text(second_exc.stdout) or _as_text(first_exc.stdout)
            stderr = _as_text(second_exc.stderr) or _as_text(first_exc.stderr)
        # Reap the child so a long-lived stdio server does not accumulate
        # zombie / unclosed Popen objects across timed-out calls. Tree kill
        # already ran above; wait is best-effort only.
        try:
            proc.wait(timeout=TREE_KILL_GRACE_SECONDS)
        except Exception:  # noqa: BLE001 — abandon; do not extend the wall clock further
            pass
        timeout_msg = f"timed out after {timeout}s"
        err = (stderr or "").rstrip()
        if err:
            err = f"{err}\n{timeout_msg}"
        else:
            err = timeout_msg
        return result_dict(argv, 124, stdout or "", err, timed_out=True)


def default_git_runner(
    args: Sequence[str],
    cwd: Optional[Path],
    timeout: float,
) -> dict[str, Any]:
    """Real git runner. Rejects forbidden verbs before spawn."""
    reject_forbidden_git_args(args)
    result = run_bounded(["git", *[str(a) for a in args]], cwd, timeout)
    if result.get("missing"):
        # missing=True is only set on FileNotFoundError, whose stderr is the
        # generic "binary not found: git". Replace with the stable probe string
        # callers already match on; no richer diagnostic is lost on this path.
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
