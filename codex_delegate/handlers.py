"""MCP tool dispatch (callable without stdio)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, TextIO

try:
    from .audit import emit_audit, goal_fingerprint
    from .guard import (
        DEFAULT_TIMEOUT_SECONDS,
        FORBIDDEN_CLI_FLAGS,
        GuardError,
        assert_argv_safe,
        build_review_argv,
        normalize_lane,
        structured_error,
        validate_goal,
        validate_model,
        validate_output_schema,
        validate_reasoning_effort,
        validate_sandbox_mode,
        validate_timeout,
    )
    from .process import GitRunner, SubprocessRunner, WhichFn, default_subprocess_runner, default_which
    from .roots import (
        load_allowed_roots,
        resolve_server_codex_bin,
        resolve_trusted_lanes_parent,
        resolve_trusted_repo_root,
    )
    from . import jobs
    from .runner import delegate, parse_event_stream, worktree_path_for_lane
    from .status import build_status_report, list_lanes, run_doctor_json, run_models
except ImportError:  # pragma: no cover
    from audit import emit_audit, goal_fingerprint
    from guard import (
        DEFAULT_TIMEOUT_SECONDS,
        FORBIDDEN_CLI_FLAGS,
        GuardError,
        assert_argv_safe,
        build_review_argv,
        normalize_lane,
        structured_error,
        validate_goal,
        validate_model,
        validate_output_schema,
        validate_reasoning_effort,
        validate_sandbox_mode,
        validate_timeout,
    )
    from process import GitRunner, SubprocessRunner, WhichFn, default_subprocess_runner, default_which
    from roots import (
        load_allowed_roots,
        resolve_server_codex_bin,
        resolve_trusted_lanes_parent,
        resolve_trusted_repo_root,
    )
    import jobs
    from runner import delegate, parse_event_stream, worktree_path_for_lane
    from status import build_status_report, list_lanes, run_doctor_json, run_models


def _env_truthy(raw: Optional[str], default: bool = True) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "off", "no", ""}


def _default_base_ref(env: Optional[Mapping[str, str]] = None) -> str:
    e = env if env is not None else os.environ
    return (e.get("CODEX_DELEGATE_BASE_REF") or "HEAD").strip() or "HEAD"


def _default_model(env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    e = env if env is not None else os.environ
    raw = e.get("CODEX_DELEGATE_MODEL")
    return raw.strip() if raw and raw.strip() else None


def _default_effort(env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    e = env if env is not None else os.environ
    raw = e.get("CODEX_DELEGATE_REASONING_EFFORT")
    return raw.strip() if raw and raw.strip() else None


def _default_timeout(env: Optional[Mapping[str, str]] = None) -> float:
    """Read default timeout from env; non-numeric values → TIMEOUT_INVALID."""
    e = env if env is not None else os.environ
    raw = e.get("CODEX_DELEGATE_TIMEOUT_SECONDS")
    if raw is None or not str(raw).strip():
        return DEFAULT_TIMEOUT_SECONDS
    # Pass the raw value through validate_timeout (do not pre-float: ValueError
    # would otherwise surface as INTERNAL_ERROR).
    return validate_timeout(raw)


def _ignore_user_config(env: Optional[Mapping[str, str]] = None) -> bool:
    e = env if env is not None else os.environ
    return _env_truthy(e.get("CODEX_DELEGATE_IGNORE_USER_CONFIG"), default=True)


def _reject_resume_arg(args: Mapping[str, Any]) -> None:
    """Fail closed if resume arrives (schema omission or additionalProperties bypass)."""
    if "resume" not in args:
        return
    value = args.get("resume")
    if value is None or value is False:
        return
    raise GuardError(
        "RESUME_UNSUPPORTED",
        "codex exec resume cannot take --cd/-s; resume is fail-closed by codex_delegate",
    )


def handle_tool_call(
    name: str,
    arguments: Optional[Mapping[str, Any]] = None,
    *,
    repo_root: Optional[Path | str] = None,
    allowed_roots: Optional[Sequence[Path | str]] = None,
    git_runner: Optional[GitRunner] = None,
    subprocess_runner: Optional[SubprocessRunner] = None,
    which: Optional[WhichFn] = None,
    audit_stream: Optional[TextIO] = None,
    principal: str = "local-dev",
) -> dict[str, Any]:
    """Dispatch a tool call. Callable without stdio for tests and --self-test."""
    args: dict[str, Any] = dict(arguments or {})
    try:
        if name == "codex_delegate":
            result = _handle_delegate(
                args, plan_only=bool(args.get("plan_only", False)),
                repo_root=repo_root, allowed_roots=allowed_roots,
                git_runner=git_runner, subprocess_runner=subprocess_runner, which=which,
            )
        elif name == "codex_delegate_plan":
            result = _handle_delegate(
                args, plan_only=True,
                repo_root=repo_root, allowed_roots=allowed_roots,
                git_runner=git_runner, subprocess_runner=subprocess_runner, which=which,
            )
        elif name == "codex_delegate_start":
            result = _handle_start(
                args,
                repo_root=repo_root, allowed_roots=allowed_roots,
                git_runner=git_runner, subprocess_runner=subprocess_runner, which=which,
            )
        elif name == "codex_delegate_poll":
            result = _handle_poll(args)
        elif name == "codex_delegate_review":
            result = _handle_review(
                args, repo_root=repo_root, allowed_roots=allowed_roots,
                git_runner=git_runner, subprocess_runner=subprocess_runner, which=which,
            )
        elif name == "codex_delegate_status":
            result = _handle_status(
                allowed_roots=allowed_roots, git_runner=git_runner,
                subprocess_runner=subprocess_runner, which=which,
            )
        elif name == "codex_delegate_doctor":
            result = run_doctor_json(
                codex_bin=resolve_server_codex_bin(args),
                subprocess_runner=subprocess_runner, which=which,
            )
        elif name == "codex_delegate_models":
            result = run_models(
                codex_bin=resolve_server_codex_bin(args),
                subprocess_runner=subprocess_runner, which=which,
            )
        elif name == "codex_delegate_lanes":
            root = resolve_trusted_repo_root(args, repo_root=repo_root, allowed_roots=allowed_roots)
            result = list_lanes(root, git_runner=git_runner)
        else:
            result = structured_error("TOOL_UNKNOWN", f"unknown tool: {name}")
    except GuardError as exc:
        result = structured_error(exc.code, exc.message)
    except Exception as exc:  # pragma: no cover
        result = structured_error("INTERNAL_ERROR", f"internal error: {type(exc).__name__}: {exc}")

    try:
        audit_event: dict[str, Any] = {
            "tool": name,
            "outcome": "ok" if result.get("ok") else "error",
        }
        # Scalars only; omit None. sanitize_event also enforces this.
        for key in ("status", "error", "lane", "branch", "base_ref", "thread_id"):
            val = result.get(key) if key != "base_ref" else (result.get(key) or args.get(key))
            if key == "lane" and val is None:
                val = args.get("lane")
            if val is not None:
                audit_event[key] = val
        cwd = result.get("cwd") or result.get("worktree_path")
        if cwd is not None:
            audit_event["cwd"] = cwd
        if result.get("worktree_path") is not None:
            audit_event["worktree_path"] = result.get("worktree_path")
        if "plan_only" in result:
            audit_event["plan_only"] = result.get("plan_only")
        elif "plan_only" in args:
            audit_event["plan_only"] = args.get("plan_only")
        sandbox = result.get("sandbox")
        if isinstance(sandbox, str):
            audit_event["sandbox"] = sandbox
        # Only emit changed_file_count when the tool actually produced a change set.
        if isinstance(result.get("changed_files"), list):
            audit_event["changed_file_count"] = len(result["changed_files"])
        if result.get("elapsed_seconds") is not None:
            audit_event["elapsed_seconds"] = result.get("elapsed_seconds")
        if "goal" in args:
            fp = goal_fingerprint(str(args.get("goal") or ""))
            audit_event["goal_chars"] = fp["goal_chars"]
            audit_event["goal_sha256_8"] = fp["goal_sha256_8"]
        usage = result.get("usage")
        if isinstance(usage, dict):
            if usage.get("input_tokens") is not None:
                audit_event["usage_input_tokens"] = usage.get("input_tokens")
            if usage.get("output_tokens") is not None:
                audit_event["usage_output_tokens"] = usage.get("output_tokens")
        emit_audit(audit_event, stream=audit_stream, principal=principal)
    except Exception:
        pass
    return result


def _handle_delegate(
    args: Mapping[str, Any],
    *,
    plan_only: bool,
    repo_root: Optional[Path | str],
    allowed_roots: Optional[Sequence[Path | str]],
    git_runner: Optional[GitRunner],
    subprocess_runner: Optional[SubprocessRunner],
    which: Optional[WhichFn],
) -> dict[str, Any]:
    _reject_resume_arg(args)
    codex_bin = resolve_server_codex_bin(args)
    root = resolve_trusted_repo_root(args, repo_root=repo_root, allowed_roots=allowed_roots)
    lanes_parent = resolve_trusted_lanes_parent(args, repo_root=root)
    goal = validate_goal(args.get("goal"))
    lane = normalize_lane(args.get("lane"))
    model = validate_model(args.get("model") if args.get("model") is not None else _default_model())
    effort = validate_reasoning_effort(
        args.get("reasoning_effort") if args.get("reasoning_effort") is not None else _default_effort()
    )
    sandbox = validate_sandbox_mode(args.get("sandbox"), plan_only=plan_only)
    timeout = validate_timeout(
        args.get("timeout_seconds") if args.get("timeout_seconds") is not None else _default_timeout()
    )
    schema = validate_output_schema(args.get("output_schema"))
    base_ref = str(args.get("base_ref") or _default_base_ref()).strip() or "HEAD"
    return delegate(
        goal=goal,
        lane=lane,
        repo_root=root,
        base_ref=base_ref,
        lanes_parent=lanes_parent,
        codex_bin=codex_bin,
        sandbox=sandbox,
        plan_only=plan_only,
        model=model,
        reasoning_effort=effort,
        output_schema=schema,
        ignore_user_config=_ignore_user_config(),
        ephemeral=bool(args.get("ephemeral", False)),
        resume=None,
        timeout_seconds=timeout,
        git_runner=git_runner,
        subprocess_runner=subprocess_runner,
        which=which,
    )


def _handle_start(
    args: Mapping[str, Any],
    *,
    repo_root: Optional[Path | str],
    allowed_roots: Optional[Sequence[Path | str]],
    git_runner: Optional[GitRunner],
    subprocess_runner: Optional[SubprocessRunner],
    which: Optional[WhichFn],
) -> dict[str, Any]:
    """Detached lane: validate now, spawn later, return a job id immediately.

    The cheap guards run on the request path on purpose — a bad root, a bad lane
    or a smuggled resume must fail fast and visibly, not inside a thread whose
    only reporting channel is a later poll.
    """
    _reject_resume_arg(args)
    root = resolve_trusted_repo_root(args, repo_root=repo_root, allowed_roots=allowed_roots)
    resolve_trusted_lanes_parent(args, repo_root=root)
    lane = normalize_lane(args.get("lane"))
    validate_goal(args.get("goal"))
    plan_only = bool(args.get("plan_only", False))

    def _run() -> dict[str, Any]:
        return _handle_delegate(
            args,
            plan_only=plan_only,
            repo_root=repo_root,
            allowed_roots=allowed_roots,
            git_runner=git_runner,
            subprocess_runner=subprocess_runner,
            which=which,
        )

    job = jobs.start_job(_run, lane=lane, tool="codex_delegate_start")
    return {
        "ok": True,
        "job_id": job["job_id"],
        "lane": lane,
        "state": job["state"],
        "poll_with": "codex_delegate_poll",
        "message": (
            "Delegation started in the background. Poll with codex_delegate_poll "
            "using this job_id."
        ),
    }


def _handle_poll(args: Mapping[str, Any]) -> dict[str, Any]:
    """Read a background job back, or list them when no job_id is given."""
    raw = args.get("job_id")
    if raw is None or not str(raw).strip():
        return {"ok": True, "jobs": jobs.list_jobs(int(args.get("limit") or 20))}

    record = jobs.snapshot(str(raw).strip())
    if record is None:
        return structured_error(
            "JOB_UNKNOWN",
            f"no job with id {str(raw).strip()!r}; it never existed or was evicted",
        )

    out: dict[str, Any] = {
        "ok": True,
        "job_id": record["job_id"],
        "lane": record.get("lane"),
        "tool": record.get("tool"),
        "state": record.get("state"),
    }
    if record.get("state") in jobs.TERMINAL_STATES:
        # The envelope is handed back verbatim: a caller polling a finished job
        # must see exactly what the synchronous path would have returned.
        out["result"] = record.get("result")
        if record.get("error") is not None:
            out["error"] = record.get("error")
    return out


def _handle_review(
    args: Mapping[str, Any],
    *,
    repo_root: Optional[Path | str],
    allowed_roots: Optional[Sequence[Path | str]],
    git_runner: Optional[GitRunner],
    subprocess_runner: Optional[SubprocessRunner],
    which: Optional[WhichFn],
) -> dict[str, Any]:
    """Run codex exec review with process cwd = lane worktree (no --cd / -s / --color)."""
    del git_runner  # review does not mutate git; kept for signature symmetry
    try:
        from .lane_lock import lane_run_scope
    except ImportError:  # pragma: no cover
        from lane_lock import lane_run_scope

    _reject_resume_arg(args)
    codex_bin = resolve_server_codex_bin(args)
    root = resolve_trusted_repo_root(args, repo_root=repo_root, allowed_roots=allowed_roots)
    lanes_parent = resolve_trusted_lanes_parent(args, repo_root=root)
    lane = normalize_lane(args.get("lane"))
    wt = worktree_path_for_lane(lanes_parent, lane)
    # Directory must exist on disk — a branch that still exists after the
    # worktree was deleted is WORKTREE_MISSING, not a crash.
    if not Path(wt).is_dir():
        return structured_error("WORKTREE_MISSING", f"lane worktree does not exist: {wt}")

    model = validate_model(args.get("model"))
    timeout = validate_timeout(args.get("timeout_seconds"))
    instructions = args.get("instructions")
    # Empty / whitespace-only instructions fall back to a stable default prompt.
    prompt = (
        str(instructions).strip()
        if instructions is not None and str(instructions).strip()
        else "Review the changes in this lane."
    )

    # Both uncommitted and base_ref are legal CLI flags together; pass through.
    argv = build_review_argv(
        codex_bin=codex_bin,
        model=model,
        base_ref=str(args["base_ref"]) if args.get("base_ref") else None,
        uncommitted=bool(args.get("uncommitted")),
        ignore_user_config=_ignore_user_config(),
    )
    # Defence in depth: every review argv must pass the same gates as exec.
    assert_argv_safe(argv)

    for tok in argv:
        if tok in FORBIDDEN_CLI_FLAGS or str(tok).startswith("--dangerously"):
            return structured_error("ARGV_FORBIDDEN_FLAG", f"forbidden flag: {tok}")

    with lane_run_scope(root, lane) as busy:
        if busy is not None:
            return busy
        return _run_review(
            argv=argv,
            codex_bin=codex_bin,
            wt=Path(wt),
            lane=lane,
            prompt=prompt,
            timeout=timeout,
            subprocess_runner=subprocess_runner,
            which=which,
        )


def _run_review(
    *,
    argv: list[str],
    codex_bin: str,
    wt: Path,
    lane: str,
    prompt: str,
    timeout: float,
    subprocess_runner: Optional[SubprocessRunner],
    which: Optional[WhichFn],
) -> dict[str, Any]:
    run = subprocess_runner or default_subprocess_runner
    which_fn = which or default_which
    final_argv = list(argv)
    if not os.path.isabs(codex_bin):
        found = which_fn(codex_bin)
        if not found:
            return structured_error("CODEX_MISSING", f"codex binary not found: {codex_bin}")
        final_argv = [found, *argv[1:]]
        assert_argv_safe(final_argv)

    proc = run(final_argv, wt, timeout, input_text=prompt)
    if proc.get("missing"):
        return structured_error("CODEX_MISSING", f"codex binary not found: {codex_bin}")

    events = parse_event_stream(proc.get("stdout") or "")
    timed_out = bool(proc.get("timedOut"))
    rc = int(proc.get("returncode") or 0)
    errors = list(events.get("errors") or [])
    if timed_out:
        status = "timeout"
    elif rc != 0 or events.get("turn_failed") or (errors and not events.get("turn_completed")):
        status = "error"
    else:
        status = "ok"

    return {
        "ok": status == "ok",
        "status": status,
        "lane": lane,
        "worktree_path": str(wt),
        "summary": events.get("last_message"),
        "thread_id": events.get("thread_id"),
        "usage": events.get("usage"),
        "errors": errors,
        "returncode": rc,
        "plan_only": True,
        # Enum scalar (not a prose sentence). Review has no -s flag; posture is
        # the Codex default sandbox (probe A: read-only). See tool description.
        "sandbox": "read-only",
        "sandbox_is_codex_default": True,
    }


def _handle_status(
    *,
    allowed_roots: Optional[Sequence[Path | str]],
    git_runner: Optional[GitRunner],
    subprocess_runner: Optional[SubprocessRunner],
    which: Optional[WhichFn],
) -> dict[str, Any]:
    roots = list(allowed_roots) if allowed_roots is not None else load_allowed_roots()
    return build_status_report(
        codex_bin=resolve_server_codex_bin({}),
        allowed_roots=roots,
        lanes_parent=os.environ.get("CODEX_DELEGATE_LANES_PARENT"),
        ignore_user_config=_ignore_user_config(),
        default_base_ref=_default_base_ref(),
        timeout_seconds=_default_timeout(),
        subprocess_runner=subprocess_runner,
        git_runner=git_runner,
        which=which,
    )
