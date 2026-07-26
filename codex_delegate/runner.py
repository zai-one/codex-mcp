"""Headless Codex spawn, readonly CLI allowlist, and high-level delegate()."""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from typing import Any, Optional, Sequence

try:
    from .events import parse_event_stream
    from .guard import (
        DEFAULT_CODEX_BIN,
        FORBIDDEN_CLI_FLAGS,
        GuardError,
        STDIN_PROMPT_SENTINEL,
        assert_argv_safe,
        build_exec_argv,
        confine_path_to_root,
        structured_error,
        validate_codex_bin,
        validate_goal,
        validate_model,
        validate_output_schema,
        validate_reasoning_effort,
        validate_sandbox_mode,
        validate_timeout,
    )
    from .process import (
        GitRunner,
        SubprocessRunner,
        WhichFn,
        default_git_runner,
        default_subprocess_runner,
        default_which,
        reject_forbidden_flags,
    )
    from .worktree import (
        collect_diff,
        is_path_inside,
        prepare_worktree,
        resolve_lanes_parent,
        worktree_path_for_lane,
    )
except ImportError:  # pragma: no cover - flat launch
    from events import parse_event_stream
    from guard import (
        DEFAULT_CODEX_BIN,
        FORBIDDEN_CLI_FLAGS,
        GuardError,
        STDIN_PROMPT_SENTINEL,
        assert_argv_safe,
        build_exec_argv,
        confine_path_to_root,
        structured_error,
        validate_codex_bin,
        validate_goal,
        validate_model,
        validate_output_schema,
        validate_reasoning_effort,
        validate_sandbox_mode,
        validate_timeout,
    )
    from process import (
        GitRunner,
        SubprocessRunner,
        WhichFn,
        default_git_runner,
        default_subprocess_runner,
        default_which,
        reject_forbidden_flags,
    )
    from worktree import (
        collect_diff,
        is_path_inside,
        prepare_worktree,
        resolve_lanes_parent,
        worktree_path_for_lane,
    )

# Re-export for callers/tests that import from runner
__all__ = [
    "GitRunner",
    "SubprocessRunner",
    "WhichFn",
    "collect_diff",
    "default_git_runner",
    "default_subprocess_runner",
    "default_which",
    "delegate",
    "is_path_inside",
    "parse_event_stream",
    "prepare_worktree",
    "resolve_lanes_parent",
    "run_delegation",
    "run_readonly_cli",
    "worktree_path_for_lane",
]

_READONLY_CLI_ALLOW = frozenset({"--version", "-V", "doctor", "features", "debug", "login"})
_READONLY_CLI_REJECT = frozenset({
    "exec", "e", "logout", "update", "mcp", "mcp-server", "plugin", "apply", "cloud",
    "app", "app-server", "remote-control", "archive", "delete", "unarchive", "fork",
    "resume", "sandbox", "exec-server", "completion",
})
_MUTATING_SECOND_TOKENS = frozenset({
    "enable", "disable", "fix", "install", "uninstall", "add", "remove", "set",
})
_TRUNC_STDOUT = 2000
_TRUNC_STDERR = 1000


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


def _resolve_bin(bin_v: str, which_fn: WhichFn) -> Optional[str]:
    if os.path.isabs(bin_v):
        return bin_v if Path(bin_v).exists() else which_fn(bin_v)
    return which_fn(bin_v)


def run_delegation(
    *,
    goal: str,
    worktree: Path | str,
    codex_bin: str = DEFAULT_CODEX_BIN,
    sandbox: Optional[str] = None,
    plan_only: bool = False,
    model: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
    output_schema: Any = None,
    ignore_user_config: bool = True,
    ephemeral: bool = False,
    resume: Any = None,
    timeout_seconds: Optional[float] = None,
    subprocess_runner: Optional[SubprocessRunner] = None,
    which: Optional[WhichFn] = None,
) -> dict[str, Any]:
    """Spawn headless ``codex exec`` against a prepared worktree."""
    run = subprocess_runner or default_subprocess_runner
    which_fn = which or default_which
    last_message_file: Optional[Path] = None
    schema_file: Optional[Path] = None

    try:
        goal_text = validate_goal(goal)
        model_v = validate_model(model)
        effort_v = validate_reasoning_effort(reasoning_effort)
        sandbox_v = validate_sandbox_mode(sandbox, plan_only=plan_only)
        timeout_v = validate_timeout(timeout_seconds)
        schema_json = validate_output_schema(output_schema)
        bin_v = validate_codex_bin(codex_bin, from_client=False)
        wt = Path(worktree)

        # Temp files live in the process temp dir (not the worktree) so they
        # never pollute the lane diff. Track the path *before* any write so a
        # mid-write exception still unlinks in ``finally``.
        fd, tmp_name = tempfile.mkstemp(prefix="codex-delegate-last-", suffix=".txt")
        os.close(fd)
        last_message_file = Path(tmp_name)

        schema_path: Optional[str] = None
        if schema_json is not None:
            sfd, sname = tempfile.mkstemp(prefix="codex-delegate-schema-", suffix=".json")
            schema_file = Path(sname)
            try:
                with os.fdopen(sfd, "w", encoding="utf-8") as sf:
                    sf.write(schema_json)
            except BaseException:
                # fdopen owns the fd only on success; if it failed, close sfd.
                try:
                    os.close(sfd)
                except OSError:
                    pass
                raise
            schema_path = str(schema_file)

        argv = build_exec_argv(
            codex_bin=bin_v,
            worktree=str(wt),
            last_message_path=str(last_message_file),
            sandbox=sandbox_v,
            plan_only=plan_only,
            model=model_v,
            reasoning_effort=effort_v,
            output_schema_path=schema_path,
            ignore_user_config=ignore_user_config,
            ephemeral=ephemeral,
            resume=resume,
        )
        assert_argv_safe(argv)
        cd_idx = argv.index("--cd")
        confine_path_to_root(argv[cd_idx + 1], wt, field="--cd")

        found = _resolve_bin(bin_v, which_fn)
        if not found and not os.path.isabs(bin_v):
            return structured_error("CODEX_MISSING", f"codex binary not found: {bin_v}")
        if not os.path.isabs(bin_v):
            if not found:
                return structured_error("CODEX_MISSING", f"codex binary not found: {bin_v}")
            argv = [found, *argv[1:]]

        started = time.monotonic()
        # Pin process cwd to the worktree (defence in depth alongside --cd).
        proc = run(argv, wt, timeout_v, input_text=goal_text)
        elapsed = time.monotonic() - started

        if proc.get("missing"):
            return structured_error("CODEX_MISSING", f"codex binary not found: {bin_v}")

        events = parse_event_stream(proc.get("stdout") or "")
        timed_out = bool(proc.get("timedOut"))
        returncode = int(proc.get("returncode") or 0)
        errors = list(events.get("errors") or [])
        turn_failed = bool(events.get("turn_failed"))
        turn_completed = bool(events.get("turn_completed"))

        if timed_out:
            status = "timeout"
        elif returncode != 0 or turn_failed or (errors and not turn_completed):
            status = "error"
        else:
            status = "ok"

        summary = None
        if last_message_file is not None and last_message_file.exists():
            try:
                summary = last_message_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                summary = None
        if not summary:
            summary = events.get("last_message")
        if not summary:
            summary = _truncate(proc.get("stdout") or "", 500)

        ok = status == "ok"
        result: dict[str, Any] = {
            "ok": ok,
            "status": status,
            "returncode": returncode,
            "elapsed_seconds": round(elapsed, 3),
            "summary": summary,
            "thread_id": events.get("thread_id"),
            "usage": events.get("usage"),
            "errors": errors,
            "commands": events.get("commands") or [],
            "plan_only": plan_only,
            "sandbox": sandbox_v,
            "cwd": str(wt),
            "goal_chars": len(goal_text),
            "stdout_truncated": _truncate(proc.get("stdout") or "", _TRUNC_STDOUT),
            "stderr_truncated": _truncate(proc.get("stderr") or "", _TRUNC_STDERR),
        }
        if not ok:
            if status == "timeout":
                result["error"] = "TIMEOUT"
                result["message"] = f"codex exec timed out after {timeout_v}s"
            else:
                result["error"] = "EXEC_FAILED"
                result["message"] = errors[0] if errors else f"codex exec failed with code {returncode}"
        return result
    except GuardError as exc:
        return structured_error(exc.code, exc.message)
    finally:
        for path in (last_message_file, schema_file):
            if path is not None:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass


def delegate(
    *,
    goal: str,
    lane: str,
    repo_root: Path | str,
    base_ref: str = "HEAD",
    lanes_parent: Optional[Path | str] = None,
    codex_bin: str = DEFAULT_CODEX_BIN,
    sandbox: Optional[str] = None,
    plan_only: bool = False,
    model: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
    output_schema: Any = None,
    ignore_user_config: bool = True,
    ephemeral: bool = False,
    resume: Any = None,
    timeout_seconds: Optional[float] = None,
    git_runner: Optional[GitRunner] = None,
    subprocess_runner: Optional[SubprocessRunner] = None,
    which: Optional[WhichFn] = None,
    require_clean_base: bool = True,
) -> dict[str, Any]:
    """Full delegation: prepare worktree → run → collect diff (always)."""
    try:
        from .guard import normalize_lane
        from .lane_lock import lane_run_scope
    except ImportError:  # pragma: no cover
        from guard import normalize_lane
        from lane_lock import lane_run_scope

    try:
        lane_n = normalize_lane(lane)
    except GuardError as exc:
        return structured_error(exc.code, exc.message)

    with lane_run_scope(repo_root, lane_n) as busy:
        if busy is not None:
            return busy
        return _delegate_locked(
            goal=goal,
            lane_n=lane_n,
            repo_root=repo_root,
            base_ref=base_ref,
            lanes_parent=lanes_parent,
            codex_bin=codex_bin,
            sandbox=sandbox,
            plan_only=plan_only,
            model=model,
            reasoning_effort=reasoning_effort,
            output_schema=output_schema,
            ignore_user_config=ignore_user_config,
            ephemeral=ephemeral,
            resume=resume,
            timeout_seconds=timeout_seconds,
            git_runner=git_runner,
            subprocess_runner=subprocess_runner,
            which=which,
            require_clean_base=require_clean_base,
        )


def _delegate_locked(
    *,
    goal: str,
    lane_n: str,
    repo_root: Path | str,
    base_ref: str,
    lanes_parent: Optional[Path | str],
    codex_bin: str,
    sandbox: Optional[str],
    plan_only: bool,
    model: Optional[str],
    reasoning_effort: Optional[str],
    output_schema: Any,
    ignore_user_config: bool,
    ephemeral: bool,
    resume: Any,
    timeout_seconds: Optional[float],
    git_runner: Optional[GitRunner],
    subprocess_runner: Optional[SubprocessRunner],
    which: Optional[WhichFn],
    require_clean_base: bool,
) -> dict[str, Any]:
    prep = prepare_worktree(
        repo_root=repo_root,
        lane=lane_n,
        base_ref=base_ref,
        lanes_parent=lanes_parent,
        git_runner=git_runner,
        require_clean_base=require_clean_base,
    )
    if not prep.get("ok"):
        return prep

    wt_path = prep["worktree_path"]
    run_result = run_delegation(
        goal=goal,
        worktree=wt_path,
        codex_bin=codex_bin,
        sandbox=sandbox,
        plan_only=plan_only,
        model=model,
        reasoning_effort=reasoning_effort,
        output_schema=output_schema,
        ignore_user_config=ignore_user_config,
        ephemeral=ephemeral,
        resume=resume,
        timeout_seconds=timeout_seconds,
        subprocess_runner=subprocess_runner,
        which=which,
    )
    diff = collect_diff(wt_path, git_runner=git_runner)
    changed = diff.get("changed_files") or []

    run_ok = bool(run_result.get("ok"))
    status = run_result.get("status") or ("ok" if run_ok else "error")

    # An execute lane exists to produce changes. Zero changed files means the
    # delegation did not do its job, even when the process exited 0 and the
    # event stream completed a turn — measured case: Codex refused every write
    # ("the filesystem is read-only") while `-s workspace-write` was requested,
    # and the run still read as ok:true with an empty diff. Structural signal
    # only; we do not pattern-match the agent's prose. Read-only work belongs to
    # codex_delegate_plan, which is exempt from this rule.
    no_changes = run_ok and not plan_only and not changed
    if no_changes:
        run_ok = False
        status = "no_changes"

    out: dict[str, Any] = {
        "ok": run_ok,
        "lane": lane_n,
        "branch": prep.get("branch"),
        "worktree_path": wt_path,
        "status": status,
        "summary": run_result.get("summary"),
        "thread_id": run_result.get("thread_id"),
        "usage": run_result.get("usage"),
        "changed_files": changed,
        "changed_file_count": len(changed),
        "diffstat": diff.get("diffstat") or "",
        # New files are absent from `diff --stat HEAD`; without this a lane that
        # only created files shows an empty diffstat next to a non-empty
        # changed_files.
        "untracked_stat": diff.get("untracked_stat") or "",
    }
    if no_changes:
        out["error"] = "EXECUTE_NO_CHANGES"
        out["message"] = (
            "execute lane finished without changing any file; read the summary for the "
            "executor's own account (a sandbox refusal reports itself there). Use "
            "codex_delegate_plan for work that is not supposed to write."
        )
        for key in ("errors", "returncode", "elapsed_seconds", "sandbox", "plan_only", "commands"):
            if key in run_result:
                out[key] = run_result[key]
        return out
    if not out["ok"]:
        out["error"] = run_result.get("error") or "EXEC_FAILED"
        out["message"] = run_result.get("message") or "delegation failed"
        for key in ("errors", "returncode", "elapsed_seconds", "sandbox", "plan_only"):
            if key in run_result:
                out[key] = run_result[key]
    else:
        for key in ("elapsed_seconds", "sandbox", "plan_only", "commands"):
            if key in run_result:
                out[key] = run_result[key]
    return out


def run_readonly_cli(
    args: Sequence[str],
    *,
    codex_bin: str = DEFAULT_CODEX_BIN,
    timeout: float = 60.0,
    subprocess_runner: Optional[SubprocessRunner] = None,
    which: Optional[WhichFn] = None,
    cwd: Optional[Path] = None,
) -> dict[str, Any]:
    """Bounded read-only Codex invocations for the status tools."""
    run = subprocess_runner or default_subprocess_runner
    which_fn = which or default_which
    tokens = [str(a) for a in args]

    if not tokens:
        return structured_error("CLI_VERB_FORBIDDEN", "empty codex argv")
    verb = tokens[0]

    if verb in _READONLY_CLI_REJECT:
        return structured_error("CLI_VERB_FORBIDDEN", f"cli verb forbidden: {verb}")
    if verb not in _READONLY_CLI_ALLOW:
        return structured_error("CLI_VERB_FORBIDDEN", f"cli verb not allowlisted: {verb}")
    if len(tokens) >= 2 and tokens[1] in _MUTATING_SECOND_TOKENS:
        return structured_error(
            "CLI_VERB_FORBIDDEN",
            f"mutating subcommand forbidden: {tokens[0]} {tokens[1]}",
        )

    if verb == "login":
        if len(tokens) < 2 or tokens[1] != "status":
            return structured_error("CLI_VERB_FORBIDDEN", "only 'login status' is allowed")
        for t in tokens[2:]:
            if t.startswith("--with-"):
                return structured_error("CLI_VERB_FORBIDDEN", f"login flag forbidden: {t}")

    try:
        bin_v = validate_codex_bin(codex_bin, from_client=False)
    except GuardError as exc:
        return structured_error(exc.code, exc.message)

    if not os.path.isabs(bin_v):
        found = which_fn(bin_v)
        if not found:
            return structured_error("CODEX_MISSING", f"codex binary not found: {bin_v}")
        full = [found, *tokens]
    else:
        full = [bin_v, *tokens]

    try:
        reject_forbidden_flags(full)
    except GuardError as exc:
        return structured_error(exc.code, exc.message)

    return run(full, cwd, timeout)
