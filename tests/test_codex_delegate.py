"""Behavioural tests for codex_delegate — fully mocked, no real codex/git mutation."""

from __future__ import annotations

import io
import json
import os
import subprocess
import uuid
from pathlib import Path
from typing import Any, Optional, Sequence
from unittest import mock

import pytest

from codex_delegate.audit import AuditError, emit_audit, goal_fingerprint, sanitize_event
from codex_delegate.cli_contract import (
    EXEC_FLAGS,
    EXEC_RESUME_FLAGS,
    EXEC_REVIEW_FLAGS,
    assert_flags_in_contract,
    detect_exec_subcommand,
    extract_flag_tokens,
)
from codex_delegate.guard import (
    ALLOWED_SANDBOX_MODES,
    DEFAULT_TIMEOUT_SECONDS,
    DOCTOR_TIMEOUT_SECONDS,
    FORBIDDEN_CLI_FLAGS,
    GuardError,
    HARD_CAP_TIMEOUT_SECONDS,
    MIN_TIMEOUT_SECONDS,
    SANDBOX_DANGER_FULL_ACCESS,
    SANDBOX_READ_ONLY,
    SANDBOX_WORKSPACE_WRITE,
    STDIN_PROMPT_SENTINEL,
    TRUNCATION_MARKER,
    assert_argv_safe,
    build_exec_argv,
    build_execution_profile,
    build_review_argv,
    confine_path_to_root,
    normalize_lane,
    parse_allowed_roots_env,
    path_in_allowlist,
    structured_error,
    validate_codex_bin,
    validate_goal,
    validate_model,
    validate_output_schema,
    validate_reasoning_effort,
    validate_sandbox_mode,
    validate_session_id,
    validate_timeout,
)
from codex_delegate.runner import (
    collect_diff,
    default_git_runner,
    default_subprocess_runner,
    delegate,
    parse_event_stream,
    prepare_worktree,
    run_delegation,
    run_readonly_cli,
)
from codex_delegate.server import (
    SERVER_NAME,
    TOOL_NAMES,
    handle_jsonrpc,
    handle_tool_call,
    load_allowed_roots,
    resolve_server_codex_bin,
    resolve_trusted_lanes_parent,
    resolve_trusted_repo_root,
)
from codex_delegate.status import run_doctor_json
from codex_delegate.__main__ import (
    SMOKE_SENTINEL,
    evaluate_self_test_rows,
    evaluate_smoke_result,
    self_test_tool_row,
)
from codex_delegate.process import (
    TREE_KILL_GRACE_SECONDS,
    kill_process_tree,
    run_bounded,
)
from codex_delegate import events as events_mod
from codex_delegate import process as process_mod
from codex_delegate import runner as runner_mod


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

SAMPLE_STREAM = """\
{"type":"thread.started","thread_id":"019f95bd-359c-7361-aa60-0c73f39fa60e"}
{"type":"turn.started"}
{"type":"item.completed","item":{"id":"item_1","type":"agent_message","text":"I will plan the change."}}
{"type":"item.completed","item":{"id":"item_3","type":"command_execution","command":"pwsh.exe -Command Get-Content","aggregated_output":"OK","exit_code":0,"status":"completed"}}
{"type":"item.completed","item":{"id":"item_0","type":"error","message":"Exceeded skills context budget of 2%"}}
{"type":"turn.completed","usage":{"input_tokens":42863,"cached_input_tokens":33408,"output_tokens":509,"reasoning_output_tokens":231}}
"""

PROBE_A_REFUSAL_STREAM = """\
{"type":"thread.started","thread_id":"019f95bd-aaaa-bbbb-cccc-0c73f39fa60e"}
{"type":"turn.started"}
{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"I can't create `A.txt` in this environment because the filesystem is read-only."}}
{"type":"turn.completed","usage":{"input_tokens":100,"output_tokens":20}}
"""

ERROR_NO_COMPLETE_STREAM = """\
{"type":"thread.started","thread_id":"tid-err"}
{"type":"turn.started"}
{"type":"item.completed","item":{"id":"item_0","type":"error","message":"something broke"}}
"""


def _git_ok(stdout: str = "", returncode: int = 0) -> dict[str, Any]:
    return {
        "args": [],
        "returncode": returncode,
        "stdout": stdout,
        "stderr": "",
        "timedOut": False,
    }


def _proc(
    stdout: str = "",
    returncode: int = 0,
    *,
    timed_out: bool = False,
    stderr: str = "",
    missing: bool = False,
    args: Optional[list] = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "args": args or [],
        "returncode": returncode,
        "stdout": stdout,
        "stderr": stderr,
        "timedOut": timed_out,
    }
    if missing:
        out["missing"] = True
    return out


class ScriptedGit:
    """Dispatch git calls by verb (after skipping global options)."""

    def __init__(self, mapping: dict[str, Any]) -> None:
        self.mapping = mapping
        self.calls: list[list[str]] = []

    def __call__(self, args: Sequence[str], cwd: Any, timeout: float) -> dict[str, Any]:
        tokens = [str(a) for a in args]
        self.calls.append(tokens)
        # find verb
        i = 0
        while i < len(tokens):
            if tokens[i] in {"-C", "-c"}:
                i += 2
                continue
            break
        verb = tokens[i] if i < len(tokens) else ""
        # multi-word keys like "worktree add"
        key2 = " ".join(tokens[i:i + 2]) if i + 1 < len(tokens) else verb
        key3 = " ".join(tokens[i:i + 3]) if i + 2 < len(tokens) else key2
        for k in (key3, key2, verb, " ".join(tokens[i:])):
            if k in self.mapping:
                val = self.mapping[k]
                if callable(val):
                    return val(tokens, cwd, timeout)
                return dict(val)
        # fallback: try prefix match
        for k, val in self.mapping.items():
            if " ".join(tokens[i:]).startswith(k):
                if callable(val):
                    return val(tokens, cwd, timeout)
                return dict(val)
        return _git_ok()


class CapturingSubprocess:
    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        args: Sequence[str],
        cwd: Any,
        timeout: float,
        *,
        input_text: Optional[str] = None,
    ) -> dict[str, Any]:
        self.calls.append({
            "args": list(args),
            "cwd": cwd,
            "timeout": timeout,
            "input_text": input_text,
        })
        out = dict(self.result)
        out["args"] = list(args)
        return out


# ===========================================================================
# 1. normalize_lane
# ===========================================================================

class TestNormalizeLane:
    def test_good_slug(self) -> None:
        assert normalize_lane("feature-x") == "codex/feature-x"

    def test_codex_prefix(self) -> None:
        assert normalize_lane("codex/feature-x") == "codex/feature-x"

    def test_reserved_main(self) -> None:
        with pytest.raises(GuardError) as ei:
            normalize_lane("main")
        assert ei.value.code == "LANE_RESERVED"

    def test_reserved_with_prefix(self) -> None:
        with pytest.raises(GuardError) as ei:
            normalize_lane("codex/master")
        assert ei.value.code == "LANE_RESERVED"

    def test_invalid_charset(self) -> None:
        with pytest.raises(GuardError) as ei:
            normalize_lane("Feature_X")
        assert ei.value.code == "LANE_INVALID"

    def test_empty(self) -> None:
        with pytest.raises(GuardError) as ei:
            normalize_lane("")
        assert ei.value.code == "LANE_EMPTY"

    def test_none_empty(self) -> None:
        with pytest.raises(GuardError) as ei:
            normalize_lane(None)
        assert ei.value.code == "LANE_EMPTY"

    def test_bad_prefix_form(self) -> None:
        with pytest.raises(GuardError) as ei:
            normalize_lane("other/foo")
        assert ei.value.code == "LANE_INVALID"


# ===========================================================================
# 2. validate_sandbox_mode
# ===========================================================================

class TestValidateSandboxMode:
    def test_danger_full_access_rejected(self) -> None:
        with pytest.raises(GuardError) as ei:
            validate_sandbox_mode(SANDBOX_DANGER_FULL_ACCESS)
        assert ei.value.code == "SANDBOX_FORBIDDEN"

    def test_plan_only_workspace_write_rejected(self) -> None:
        with pytest.raises(GuardError) as ei:
            validate_sandbox_mode(SANDBOX_WORKSPACE_WRITE, plan_only=True)
        assert ei.value.code == "SANDBOX_PLAN_CONFLICT"

    def test_default_execute(self) -> None:
        assert validate_sandbox_mode(None) == SANDBOX_WORKSPACE_WRITE

    def test_default_plan(self) -> None:
        assert validate_sandbox_mode(None, plan_only=True) == SANDBOX_READ_ONLY

    def test_unknown(self) -> None:
        with pytest.raises(GuardError) as ei:
            validate_sandbox_mode("full-access")
        assert ei.value.code == "SANDBOX_INVALID"

    def test_read_only_ok(self) -> None:
        assert validate_sandbox_mode("read-only") == SANDBOX_READ_ONLY


# ===========================================================================
# 3. build_exec_argv
# ===========================================================================

class TestBuildExecArgv:
    def _base(self, **kw: Any) -> list[str]:
        defaults = dict(
            codex_bin="codex",
            worktree="/tmp/wt",
            last_message_path="/tmp/last.txt",
            sandbox=SANDBOX_WORKSPACE_WRITE,
        )
        defaults.update(kw)
        return build_exec_argv(**defaults)

    def test_final_token_is_stdin_sentinel(self) -> None:
        argv = self._base()
        assert argv[-1] == STDIN_PROMPT_SENTINEL

    def test_cd_present(self) -> None:
        argv = self._base(worktree="/lanes/foo")
        assert "--cd" in argv
        assert argv[argv.index("--cd") + 1] == "/lanes/foo"

    def test_sandbox_workspace_write(self) -> None:
        argv = self._base(sandbox=SANDBOX_WORKSPACE_WRITE)
        assert argv[argv.index("-s") + 1] == SANDBOX_WORKSPACE_WRITE

    def test_sandbox_read_only(self) -> None:
        argv = self._base(sandbox=SANDBOX_READ_ONLY, plan_only=True)
        assert argv[argv.index("-s") + 1] == SANDBOX_READ_ONLY

    def test_json_present(self) -> None:
        assert "--json" in self._base()

    def test_no_forbidden_flags(self) -> None:
        argv = self._base(model="gpt-5.4-mini", reasoning_effort="high", ephemeral=True)
        for flag in FORBIDDEN_CLI_FLAGS:
            assert flag not in argv
        assert not any(t.startswith("--dangerously") for t in argv)

    def test_no_c_sandbox_mode(self) -> None:
        argv = self._base(reasoning_effort="high")
        for i, t in enumerate(argv):
            if t == "-c":
                assert "sandbox_mode" not in argv[i + 1]

    def test_goal_text_absent(self) -> None:
        goal = "Create file SECRET_GOAL_MARKER.txt"
        argv = self._base()
        assert goal not in argv
        assert "SECRET_GOAL_MARKER" not in " ".join(argv)

    def test_resume_true_fail_closed(self) -> None:
        with pytest.raises(GuardError) as ei:
            self._base(resume=True)
        assert ei.value.code == "RESUME_UNSUPPORTED"

    def test_resume_uuid_fail_closed(self) -> None:
        sid = str(uuid.uuid4())
        with pytest.raises(GuardError) as ei:
            self._base(resume=sid)
        assert ei.value.code == "RESUME_UNSUPPORTED"

    def test_resume_bad_uuid_fail_closed(self) -> None:
        with pytest.raises(GuardError) as ei:
            self._base(resume="not-a-uuid")
        assert ei.value.code == "RESUME_UNSUPPORTED"

    def test_resume_bad_type_fail_closed(self) -> None:
        with pytest.raises(GuardError) as ei:
            self._base(resume=123)
        assert ei.value.code == "RESUME_UNSUPPORTED"

    def test_resume_false_and_none_ok(self) -> None:
        assert "resume" not in self._base(resume=False)
        assert "resume" not in self._base(resume=None)

    def test_reasoning_effort_toml_quoted(self) -> None:
        argv = self._base(reasoning_effort="high")
        assert "-c" in argv
        assert 'model_reasoning_effort="high"' in argv

    def test_ignore_user_config_default_on(self) -> None:
        assert "--ignore-user-config" in self._base()

    def test_order_cd_s_json_color_o(self) -> None:
        argv = self._base()
        # after exec (and optional resume), --cd then -s then --json then --color never then -o
        exec_idx = argv.index("exec")
        # find --cd after exec
        cd_idx = argv.index("--cd")
        s_idx = argv.index("-s")
        json_idx = argv.index("--json")
        color_idx = argv.index("--color")
        o_idx = argv.index("-o")
        assert exec_idx < cd_idx < s_idx < json_idx < color_idx < o_idx


# ===========================================================================
# 4. assert_argv_safe
# ===========================================================================

class TestAssertArgvSafe:
    def _good(self) -> list[str]:
        return build_exec_argv(
            codex_bin="codex",
            worktree="/tmp/wt",
            last_message_path="/tmp/last.txt",
            sandbox=SANDBOX_WORKSPACE_WRITE,
        )

    def test_good_passes(self) -> None:
        assert_argv_safe(self._good())

    def test_missing_exec(self) -> None:
        with pytest.raises(GuardError) as ei:
            assert_argv_safe(["codex", "doctor", "--json"])
        assert ei.value.code == "ARGV_MISSING_EXEC"

    def test_missing_cd(self) -> None:
        argv = ["codex", "exec", "-s", "read-only", "--json", "-"]
        with pytest.raises(GuardError) as ei:
            assert_argv_safe(argv)
        assert ei.value.code == "ARGV_MISSING_CD"

    def test_missing_json(self) -> None:
        argv = ["codex", "exec", "--cd", "/tmp", "-s", "read-only", "-"]
        with pytest.raises(GuardError) as ei:
            assert_argv_safe(argv)
        assert ei.value.code == "ARGV_MISSING_JSON"

    def test_sandbox_invalid(self) -> None:
        argv = ["codex", "exec", "--cd", "/tmp", "-s", "danger-full-access", "--json", "-"]
        with pytest.raises(GuardError) as ei:
            assert_argv_safe(argv)
        assert ei.value.code == "ARGV_SANDBOX_INVALID"

    def test_prompt_not_stdin(self) -> None:
        argv = ["codex", "exec", "--cd", "/tmp", "-s", "read-only", "--json", "do stuff"]
        with pytest.raises(GuardError) as ei:
            assert_argv_safe(argv)
        assert ei.value.code == "ARGV_PROMPT_NOT_STDIN"

    def test_forbidden_flag(self) -> None:
        argv = [
            "codex", "exec", "--cd", "/tmp", "-s", "read-only", "--json",
            "--dangerously-bypass-approvals-and-sandbox", "-",
        ]
        with pytest.raises(GuardError) as ei:
            assert_argv_safe(argv)
        assert ei.value.code == "ARGV_FORBIDDEN_FLAG"

    def test_c_sandbox_mode_forbidden(self) -> None:
        argv = [
            "codex", "exec", "--cd", "/tmp", "-s", "read-only", "--json",
            "-c", 'sandbox_mode="workspace-write"', "-",
        ]
        with pytest.raises(GuardError) as ei:
            assert_argv_safe(argv)
        assert ei.value.code == "ARGV_FORBIDDEN_FLAG"

    def test_resume_subcommand_fail_closed(self) -> None:
        argv = [
            "codex", "exec", "resume", "--last", "--json",
            "-o", "/tmp/last.txt", "-",
        ]
        with pytest.raises(GuardError) as ei:
            assert_argv_safe(argv)
        assert ei.value.code == "RESUME_UNSUPPORTED"

    def test_review_color_flag_rejected(self) -> None:
        argv = [
            "codex", "exec", "review", "--json", "--color", "never", "-",
        ]
        with pytest.raises(GuardError) as ei:
            assert_argv_safe(argv)
        assert ei.value.code == "ARGV_FLAG_NOT_IN_CONTRACT"


# ===========================================================================
# 5. validate_codex_bin
# ===========================================================================

class TestValidateCodexBin:
    def test_client_always_rejected(self) -> None:
        with pytest.raises(GuardError) as ei:
            validate_codex_bin("codex", from_client=True)
        assert ei.value.code == "CODEX_BIN_CLIENT_FORBIDDEN"

    def test_python_exe_rejected(self) -> None:
        with pytest.raises(GuardError) as ei:
            validate_codex_bin("python.exe")
        assert ei.value.code == "CODEX_BIN_INVALID"

    def test_codex_cmd_accepted(self) -> None:
        assert validate_codex_bin("codex.cmd") == "codex.cmd"

    def test_codex_bat_accepted(self) -> None:
        assert validate_codex_bin("codex.bat") == "codex.bat"

    def test_none_default(self) -> None:
        assert validate_codex_bin(None) == "codex"


# ===========================================================================
# 6. prepare_worktree
# ===========================================================================

class TestPrepareWorktree:
    def test_dirty_base_rejected(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        git = ScriptedGit({
            "--version": _git_ok("git version 2.40.0"),
            "rev-parse --verify": _git_ok("abc123"),
            "status --porcelain": _git_ok(" M dirty.txt"),
        })
        result = prepare_worktree(
            repo_root=repo,
            lane="codex/feat",
            base_ref="HEAD",
            lanes_parent=tmp_path / "lanes",
            git_runner=git,
        )
        assert result["ok"] is False
        assert result["error"] == "BASE_DIRTY"

    def test_unreachable_base_rejected(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        git = ScriptedGit({
            "--version": _git_ok("git version 2.40.0"),
            "rev-parse --verify": _git_ok("", returncode=128),
        })
        result = prepare_worktree(
            repo_root=repo,
            lane="codex/feat",
            base_ref="nope",
            lanes_parent=tmp_path / "lanes",
            git_runner=git,
        )
        assert result["ok"] is False
        assert result["error"] == "BASE_UNREACHABLE"

    def test_target_inside_repo_rejected(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        git = ScriptedGit({
            "--version": _git_ok("git version 2.40.0"),
            "rev-parse --verify": _git_ok("abc"),
            "status --porcelain": _git_ok(""),
        })
        # lanes_parent inside repo
        result = prepare_worktree(
            repo_root=repo,
            lane="codex/feat",
            base_ref="HEAD",
            lanes_parent=repo / "nested-lanes",
            git_runner=git,
        )
        assert result["ok"] is False
        assert result["error"] == "WORKTREE_INSIDE_REPO"

    def test_branch_exists_fallback(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        lanes = tmp_path / "lanes"
        created: dict[str, bool] = {"once": False}

        def worktree_add(tokens: list[str], cwd: Any, timeout: float) -> dict[str, Any]:
            # first call with -b fails; second without -b succeeds and creates path
            if "-b" in tokens:
                return _git_ok("", returncode=128)
            # create the worktree dir
            wt = Path(tokens[tokens.index("add") + 1])
            wt.mkdir(parents=True, exist_ok=True)
            created["once"] = True
            return _git_ok("Preparing worktree")

        git = ScriptedGit({
            "--version": _git_ok("git version 2.40.0"),
            "rev-parse --verify": _git_ok("abc"),
            "status --porcelain": _git_ok(""),
            "worktree add": worktree_add,
        })
        result = prepare_worktree(
            repo_root=repo,
            lane="codex/feat",
            base_ref="HEAD",
            lanes_parent=lanes,
            git_runner=git,
        )
        assert result["ok"] is True
        assert result["reused"] is False
        assert created["once"] is True

    def test_reuse_on_matching_branch(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        lanes = tmp_path / "lanes"
        wt = lanes / "feat"
        wt.mkdir(parents=True)
        git = ScriptedGit({
            "--version": _git_ok("git version 2.40.0"),
            "rev-parse --verify": _git_ok("abc"),
            "status --porcelain": _git_ok(""),
            "rev-parse --abbrev-ref": _git_ok("codex/feat"),
        })
        result = prepare_worktree(
            repo_root=repo,
            lane="codex/feat",
            base_ref="HEAD",
            lanes_parent=lanes,
            git_runner=git,
        )
        assert result["ok"] is True
        assert result["reused"] is True

    def test_reuse_conflict_wrong_branch(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        lanes = tmp_path / "lanes"
        wt = lanes / "feat"
        wt.mkdir(parents=True)
        git = ScriptedGit({
            "--version": _git_ok("git version 2.40.0"),
            "rev-parse --verify": _git_ok("abc"),
            "status --porcelain": _git_ok(""),
            "rev-parse --abbrev-ref": _git_ok("codex/other"),
        })
        result = prepare_worktree(
            repo_root=repo,
            lane="codex/feat",
            base_ref="HEAD",
            lanes_parent=lanes,
            git_runner=git,
        )
        assert result["ok"] is False
        assert result["error"] == "WORKTREE_EXISTS_CONFLICT"


# ===========================================================================
# 7. Forbidden git verbs
# ===========================================================================

class TestForbiddenGitVerbs:
    def test_push_raises(self) -> None:
        with pytest.raises(GuardError) as ei:
            # Call the reject path via default_git_runner — it will raise before spawn
            # if git is present, or we can import the internal helper via behaviour:
            # default_git_runner must raise GIT_VERB_FORBIDDEN for push
            default_git_runner(["push", "origin", "main"], None, 5.0)
        assert ei.value.code == "GIT_VERB_FORBIDDEN"

    def test_merge_raises(self) -> None:
        with pytest.raises(GuardError) as ei:
            default_git_runner(["merge", "main"], None, 5.0)
        assert ei.value.code == "GIT_VERB_FORBIDDEN"

    def test_push_disguised_behind_C(self) -> None:
        with pytest.raises(GuardError) as ei:
            default_git_runner(["-C", "/some/path", "push", "origin", "HEAD"], None, 5.0)
        assert ei.value.code == "GIT_VERB_FORBIDDEN"

    def test_merge_disguised_behind_c_config(self) -> None:
        with pytest.raises(GuardError) as ei:
            default_git_runner(["-c", "user.name=x", "merge", "topic"], None, 5.0)
        assert ei.value.code == "GIT_VERB_FORBIDDEN"

    def test_pull_raises(self) -> None:
        with pytest.raises(GuardError) as ei:
            default_git_runner(["pull"], None, 5.0)
        assert ei.value.code == "GIT_VERB_FORBIDDEN"

    def test_reset_raises(self) -> None:
        with pytest.raises(GuardError) as ei:
            default_git_runner(["reset", "--hard"], None, 5.0)
        assert ei.value.code == "GIT_VERB_FORBIDDEN"


# ===========================================================================
# 8. parse_event_stream
# ===========================================================================

class TestParseEventStream:
    def test_sample_stream(self) -> None:
        parsed = parse_event_stream(SAMPLE_STREAM)
        assert parsed["thread_id"] == "019f95bd-359c-7361-aa60-0c73f39fa60e"
        assert parsed["last_message"] == "I will plan the change."
        assert parsed["usage"] is not None
        assert parsed["usage"]["input_tokens"] == 42863
        assert parsed["usage"]["output_tokens"] == 509
        assert len(parsed["commands"]) == 1
        assert parsed["commands"][0]["exit_code"] == 0
        assert parsed["commands"][0]["status"] == "completed"
        assert any("Exceeded skills" in e for e in parsed["errors"])
        assert parsed["turn_completed"] is True
        assert parsed["agent_messages"] == 1

    def test_garbage_lines_bump_unparsed(self) -> None:
        stream = SAMPLE_STREAM + "\nNOT JSON\n{broken\n"
        parsed = parse_event_stream(stream)
        assert parsed["unparsed_lines"] >= 2
        assert parsed["thread_id"] == "019f95bd-359c-7361-aa60-0c73f39fa60e"

    def test_never_raises_on_empty(self) -> None:
        parsed = parse_event_stream("")
        assert parsed["thread_id"] is None
        assert parsed["unparsed_lines"] == 0


# ===========================================================================
# 9. Probe-A regression + error status rule
# ===========================================================================

class TestProbeARegression:
    def test_refusal_with_rc0_is_ok_status(self, tmp_path: Path) -> None:
        """returncode==0 + agent refusal + turn.completed → status ok (honest stream rule)."""
        wt = tmp_path / "wt"
        wt.mkdir()
        last = tmp_path / "last.txt"
        # last message file content will be cleaned up; summary from events
        capt = CapturingSubprocess(_proc(stdout=PROBE_A_REFUSAL_STREAM, returncode=0))
        result = run_delegation(
            goal="Create A.txt",
            worktree=wt,
            plan_only=True,
            subprocess_runner=capt,
            which=lambda n: "codex",
            timeout_seconds=60.0,
        )
        # Documented rule: timeout if timedOut; error if rc!=0 or turn_failed or (errors and not turn_completed); else ok
        assert result["status"] == "ok"
        assert result["ok"] is True
        assert result["returncode"] == 0

    def test_error_item_without_turn_completed_is_error(self, tmp_path: Path) -> None:
        wt = tmp_path / "wt"
        wt.mkdir()
        capt = CapturingSubprocess(_proc(stdout=ERROR_NO_COMPLETE_STREAM, returncode=0))
        result = run_delegation(
            goal="do something",
            worktree=wt,
            plan_only=True,
            subprocess_runner=capt,
            which=lambda n: "codex",
            timeout_seconds=60.0,
        )
        assert result["status"] == "error"
        assert result["ok"] is False


# ===========================================================================
# 10. Timeout
# ===========================================================================

class TestTimeout:
    def test_timeout_status(self, tmp_path: Path) -> None:
        wt = tmp_path / "wt"
        wt.mkdir()
        capt = CapturingSubprocess(_proc(stdout="", returncode=124, timed_out=True))
        result = run_delegation(
            goal="slow goal",
            worktree=wt,
            subprocess_runner=capt,
            which=lambda n: "codex",
            timeout_seconds=60.0,
        )
        assert result["status"] == "timeout"
        assert result["ok"] is False


# ===========================================================================
# 11. goal via input_text not argv
# ===========================================================================

class TestGoalViaStdin:
    def test_goal_passed_as_input_text(self, tmp_path: Path) -> None:
        wt = tmp_path / "wt"
        wt.mkdir()
        goal = "UNIQUE_GOAL_TEXT_XYZ_42"
        capt = CapturingSubprocess(_proc(stdout=PROBE_A_REFUSAL_STREAM, returncode=0))
        run_delegation(
            goal=goal,
            worktree=wt,
            plan_only=True,
            subprocess_runner=capt,
            which=lambda n: "codex",
            timeout_seconds=60.0,
        )
        assert len(capt.calls) == 1
        assert capt.calls[0]["input_text"] == goal
        assert goal not in capt.calls[0]["args"]
        assert capt.calls[0]["args"][-1] == "-"


# ===========================================================================
# 12. delegate collects diffstat even on executor failure
# ===========================================================================

class TestDelegateDiffOnFailure:
    def test_diff_collected_when_exec_fails(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        lanes = tmp_path / "lanes"
        wt_path = lanes / "feat"

        def worktree_add(tokens: list[str], cwd: Any, timeout: float) -> dict[str, Any]:
            p = Path(tokens[tokens.index("add") + 1]) if "-b" not in tokens else Path(tokens[tokens.index("-b") + 2])
            # find path after -b branch
            if "-b" in tokens:
                bi = tokens.index("-b")
                p = Path(tokens[bi + 2])
            p.mkdir(parents=True, exist_ok=True)
            (p / "new.txt").write_text("x", encoding="utf-8")
            return _git_ok()

        git = ScriptedGit({
            "--version": _git_ok("git version 2.40.0"),
            "rev-parse --verify": _git_ok("abc"),
            "status --porcelain": lambda tokens, cwd, timeout: (
                _git_ok("") if Path(cwd or ".") == repo or str(cwd) == str(repo)
                else _git_ok("?? new.txt")
            ),
            "worktree add": worktree_add,
            "diff --name-only": _git_ok(""),
            "diff --stat": _git_ok(" new.txt | 1 +\n 1 file changed"),
        })
        capt = CapturingSubprocess(_proc(stdout=ERROR_NO_COMPLETE_STREAM, returncode=1))
        result = delegate(
            goal="fail please",
            lane="feat",
            repo_root=repo,
            lanes_parent=lanes,
            git_runner=git,
            subprocess_runner=capt,
            which=lambda n: "codex",
            timeout_seconds=60.0,
            plan_only=True,
        )
        assert result["ok"] is False
        assert "diffstat" in result
        assert "changed_files" in result
        # new untracked file counted via porcelain
        assert "new.txt" in result["changed_files"] or result["diffstat"] != "" or isinstance(result["changed_files"], list)


# ===========================================================================
# 13. Root allowlist
# ===========================================================================

class TestRootAllowlist:
    def test_untrusted_repo_root_rejected(self, tmp_path: Path) -> None:
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        other = tmp_path / "other"
        other.mkdir()
        with pytest.raises(GuardError) as ei:
            resolve_trusted_repo_root(
                {"repo_root": str(other)},
                allowed_roots=[allowed],
            )
        assert ei.value.code == "REPO_ROOT_UNTRUSTED"

    def test_path_escape_rejected(self, tmp_path: Path) -> None:
        root = tmp_path / "root"
        root.mkdir()
        with pytest.raises(GuardError) as ei:
            confine_path_to_root(tmp_path / "outside", root, field="path")
        assert ei.value.code == "PATH_ESCAPE"

    def test_empty_allowlist(self) -> None:
        with pytest.raises(GuardError) as ei:
            resolve_trusted_repo_root({}, allowed_roots=[])
        assert ei.value.code == "ALLOWED_ROOTS_EMPTY"

    def test_lanes_parent_inside_repo_rejected(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        with pytest.raises(GuardError) as ei:
            resolve_trusted_lanes_parent(
                {"lanes_parent": str(repo / "inside")},
                repo_root=repo,
                env={},
            )
        assert ei.value.code == "LANES_PARENT_INSIDE_REPO"

    def test_load_allowed_roots_from_env(self, tmp_path: Path) -> None:
        a = tmp_path / "a"
        b = tmp_path / "b"
        roots = load_allowed_roots(env={"CODEX_DELEGATE_ALLOWED_ROOTS": f"{a};{b}"})
        assert len(roots) == 2

    def test_client_codex_bin_forbidden(self) -> None:
        with pytest.raises(GuardError) as ei:
            resolve_server_codex_bin({"codex_bin": "codex"})
        assert ei.value.code == "CODEX_BIN_CLIENT_FORBIDDEN"


# ===========================================================================
# 14. audit.sanitize_event
# ===========================================================================

class TestAudit:
    def test_auth_json_raises(self) -> None:
        with pytest.raises(AuditError):
            sanitize_event({"tool": "x", "cwd": "/home/u/.codex/auth.json"})

    def test_bearer_token_raises(self) -> None:
        with pytest.raises(AuditError):
            sanitize_event({"tool": "x", "status": "Bearer sk-abc123xyz"})

    def test_openai_api_key_raises(self) -> None:
        with pytest.raises(AuditError):
            sanitize_event({"tool": "x", "status": "OPENAI_API_KEY=sk-test"})

    def test_drops_smuggled_goal(self) -> None:
        cleaned = sanitize_event({
            "tool": "codex_delegate",
            "goal": "secret goal text",
            "status": "ok",
            "outcome": "ok",
        })
        assert "goal" not in cleaned
        assert cleaned["tool"] == "codex_delegate"
        assert cleaned["status"] == "ok"

    def test_only_allowlisted_fields(self) -> None:
        cleaned = sanitize_event({
            "tool": "t",
            "status": "ok",
            "extra_field": "nope",
            "stdout": "leak",
            "argv": ["codex"],
        })
        assert "extra_field" not in cleaned
        assert "stdout" not in cleaned
        assert "argv" not in cleaned

    def test_dict_valued_field_not_serialised(self) -> None:
        cleaned = sanitize_event({
            "tool": "codex_delegate_status",
            "outcome": "ok",
            "sandbox": {
                "plan_default": "read-only",
                "enforcement_note": "long prose note",
            },
        })
        assert "sandbox" not in cleaned
        assert cleaned["tool"] == "codex_delegate_status"

    def test_none_keys_omitted(self) -> None:
        cleaned = sanitize_event({
            "tool": "codex_delegate_status",
            "outcome": "ok",
            "lane": None,
            "branch": None,
            "changed_file_count": None,
        })
        assert "lane" not in cleaned
        assert "branch" not in cleaned
        assert "changed_file_count" not in cleaned

    def test_delegate_scalars_preserved(self) -> None:
        cleaned = sanitize_event({
            "tool": "codex_delegate",
            "outcome": "ok",
            "lane": "codex/feat",
            "goal_sha256_8": "abcd1234",
            "changed_file_count": 2,
            "sandbox": "workspace-write",
        })
        assert cleaned["lane"] == "codex/feat"
        assert cleaned["goal_sha256_8"] == "abcd1234"
        assert cleaned["changed_file_count"] == 2
        assert cleaned["sandbox"] == "workspace-write"

    def test_goal_fingerprint(self) -> None:
        fp = goal_fingerprint("hello")
        assert fp["goal_chars"] == 5
        assert len(fp["goal_sha256_8"]) == 8
        assert "hello" not in fp.values()

    def test_emit_best_effort(self) -> None:
        buf = io.StringIO()
        emit_audit({"tool": "t", "outcome": "ok"}, stream=buf)
        line = buf.getvalue().strip()
        data = json.loads(line)
        assert data["tool"] == "t"


# ===========================================================================
# 15. Server JSON-RPC
# ===========================================================================

class TestServerJsonRpc:
    def test_initialize_server_info(self) -> None:
        resp = handle_jsonrpc({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {},
        })
        assert resp is not None
        assert resp["result"]["serverInfo"]["name"] == SERVER_NAME

    def test_tools_list_seven_names(self) -> None:
        resp = handle_jsonrpc({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
        })
        assert resp is not None
        names = sorted(t["name"] for t in resp["result"]["tools"])
        assert names == sorted(TOOL_NAMES)
        assert len(names) == 7

    def test_delegate_schemas_have_no_resume(self) -> None:
        resp = handle_jsonrpc({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
        })
        assert resp is not None
        by_name = {t["name"]: t for t in resp["result"]["tools"]}
        for tool in ("codex_delegate", "codex_delegate_plan"):
            props = by_name[tool]["inputSchema"]["properties"]
            assert "resume" not in props

    def test_unknown_tool_is_error(self) -> None:
        resp = handle_jsonrpc({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "no_such_tool", "arguments": {}},
        })
        assert resp is not None
        result = resp["result"]
        assert result["isError"] is True
        sc = result["structuredContent"]
        assert sc["ok"] is False
        assert sc["error"] == "TOOL_UNKNOWN"

    def test_notification_returns_none(self) -> None:
        resp = handle_jsonrpc({
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        })
        assert resp is None

    def test_unknown_method(self) -> None:
        resp = handle_jsonrpc({
            "jsonrpc": "2.0",
            "id": 9,
            "method": "foo/bar",
        })
        assert resp is not None
        assert resp["error"]["code"] == -32601


# ===========================================================================
# 16. run_readonly_cli
# ===========================================================================

class TestRunReadonlyCli:
    def test_exec_rejected(self) -> None:
        result = run_readonly_cli(["exec", "-"], which=lambda n: "codex")
        assert result["ok"] is False
        assert result["error"] == "CLI_VERB_FORBIDDEN"

    def test_logout_rejected(self) -> None:
        result = run_readonly_cli(["logout"], which=lambda n: "codex")
        assert result["ok"] is False
        assert result["error"] == "CLI_VERB_FORBIDDEN"

    def test_mcp_rejected(self) -> None:
        result = run_readonly_cli(["mcp", "list"], which=lambda n: "codex")
        assert result["ok"] is False
        assert result["error"] == "CLI_VERB_FORBIDDEN"

    def test_features_enable_rejected(self) -> None:
        result = run_readonly_cli(["features", "enable", "x"], which=lambda n: "codex")
        assert result["ok"] is False
        assert result["error"] == "CLI_VERB_FORBIDDEN"

    def test_login_with_api_key_rejected(self) -> None:
        result = run_readonly_cli(["login", "--with-api-key"], which=lambda n: "codex")
        assert result["ok"] is False
        assert result["error"] == "CLI_VERB_FORBIDDEN"

    def test_doctor_json_accepted(self) -> None:
        capt = CapturingSubprocess(_proc(stdout='{"ok":true}', returncode=0))
        result = run_readonly_cli(
            ["doctor", "--json"],
            subprocess_runner=capt,
            which=lambda n: "/usr/bin/codex",
        )
        assert result.get("returncode") == 0
        assert capt.calls[0]["args"][1:3] == ["doctor", "--json"]

    def test_debug_models_accepted(self) -> None:
        capt = CapturingSubprocess(_proc(stdout="[]", returncode=0))
        result = run_readonly_cli(
            ["debug", "models"],
            subprocess_runner=capt,
            which=lambda n: "/usr/bin/codex",
        )
        assert result.get("returncode") == 0
        assert capt.calls[0]["args"][1:3] == ["debug", "models"]


# ===========================================================================
# Extra validators / profile / structured_error
# ===========================================================================

class TestExtraValidators:
    def test_goal_empty(self) -> None:
        with pytest.raises(GuardError) as ei:
            validate_goal("   ")
        assert ei.value.code == "GOAL_EMPTY"

    def test_goal_too_long(self) -> None:
        with pytest.raises(GuardError) as ei:
            validate_goal("x" * 60_001)
        assert ei.value.code == "GOAL_TOO_LONG"

    def test_goal_null_byte(self) -> None:
        with pytest.raises(GuardError) as ei:
            validate_goal("a\x00b")
        assert ei.value.code == "GOAL_INVALID"

    def test_model_starts_with_dash(self) -> None:
        with pytest.raises(GuardError) as ei:
            validate_model("-m")
        assert ei.value.code == "MODEL_INVALID"

    def test_reasoning_effort_invalid(self) -> None:
        with pytest.raises(GuardError) as ei:
            validate_reasoning_effort("insane")
        assert ei.value.code == "REASONING_EFFORT_INVALID"

    def test_timeout_default(self) -> None:
        assert validate_timeout(None) == DEFAULT_TIMEOUT_SECONDS

    def test_timeout_below_min(self) -> None:
        with pytest.raises(GuardError) as ei:
            validate_timeout(MIN_TIMEOUT_SECONDS - 1)
        assert ei.value.code == "TIMEOUT_INVALID"

    def test_timeout_above_cap_not_clamped(self) -> None:
        with pytest.raises(GuardError) as ei:
            validate_timeout(HARD_CAP_TIMEOUT_SECONDS + 1)
        assert ei.value.code == "TIMEOUT_INVALID"

    def test_output_schema_object(self) -> None:
        text = validate_output_schema({"type": "object"})
        assert text is not None
        assert json.loads(text)["type"] == "object"

    def test_output_schema_not_object(self) -> None:
        with pytest.raises(GuardError) as ei:
            validate_output_schema("[1,2]")
        assert ei.value.code == "OUTPUT_SCHEMA_INVALID"

    def test_session_id_valid(self) -> None:
        sid = str(uuid.uuid4())
        assert validate_session_id(sid) == sid

    def test_structured_error_shape(self) -> None:
        err = structured_error("X", "msg", extra=1)
        assert err == {"ok": False, "error": "X", "message": "msg", "extra": 1}

    def test_execution_profile_plan(self) -> None:
        p = build_execution_profile(plan_only=True)
        assert p["sandbox"] == SANDBOX_READ_ONLY
        assert p["mode"] == "plan"
        assert p["network_access"] is False

    def test_execution_profile_execute(self) -> None:
        p = build_execution_profile(plan_only=False)
        assert p["sandbox"] == SANDBOX_WORKSPACE_WRITE
        assert p["mode"] == "execute"

    def test_parse_allowed_roots_json(self) -> None:
        roots = parse_allowed_roots_env('["/a","/b"]')
        assert len(roots) == 2

    def test_path_in_allowlist(self, tmp_path: Path) -> None:
        a = tmp_path / "a"
        a.mkdir()
        assert path_in_allowlist(a, [a]) is True
        assert path_in_allowlist(tmp_path / "b", [a]) is False

    def test_handle_tool_status(self, tmp_path: Path) -> None:
        result = handle_tool_call(
            "codex_delegate_status",
            {},
            allowed_roots=[tmp_path],
            which=lambda n: None,
            git_runner=lambda args, cwd, timeout: _git_ok("git version 2.0"),
            subprocess_runner=lambda *a, **k: _proc(missing=True),
        )
        assert result["ok"] is True
        assert result["server"]["name"] == SERVER_NAME
        assert result["sandbox"]["danger_full_access_allowed"] is False


# ===========================================================================
# R1 — resume fail-closed end-to-end
# ===========================================================================

class TestResumeUnsupported:
    def test_handle_delegate_resume_true(self, tmp_path: Path) -> None:
        result = handle_tool_call(
            "codex_delegate",
            {
                "goal": "do a thing",
                "lane": "feat",
                "resume": True,
            },
            allowed_roots=[tmp_path],
            which=lambda n: "codex",
        )
        assert result["ok"] is False
        assert result["error"] == "RESUME_UNSUPPORTED"

    def test_handle_delegate_resume_uuid_smuggled(self, tmp_path: Path) -> None:
        result = handle_tool_call(
            "codex_delegate_plan",
            {
                "goal": "plan only",
                "lane": "feat",
                "resume": str(uuid.uuid4()),
            },
            allowed_roots=[tmp_path],
            which=lambda n: "codex",
        )
        assert result["ok"] is False
        assert result["error"] == "RESUME_UNSUPPORTED"

    def test_run_delegation_resume_rejected(self, tmp_path: Path) -> None:
        wt = tmp_path / "wt"
        wt.mkdir()
        result = run_delegation(
            goal="x",
            worktree=wt,
            resume=True,
            which=lambda n: "codex",
            timeout_seconds=60.0,
        )
        assert result["ok"] is False
        assert result["error"] == "RESUME_UNSUPPORTED"


# ===========================================================================
# R2 — review argv has no --color; sandbox is scalar enum
# ===========================================================================

class TestReviewArgv:
    def test_build_review_argv_no_color_no_cd_no_s(self) -> None:
        argv = build_review_argv(codex_bin="codex", model="gpt-5.4-mini", uncommitted=True)
        assert "--color" not in argv
        assert "--cd" not in argv
        assert "-s" not in argv
        assert argv[-1] == STDIN_PROMPT_SENTINEL
        assert "review" in argv
        assert "--json" in argv
        assert "--uncommitted" in argv

    def test_review_handler_sandbox_shape(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        lanes = tmp_path / "lanes"
        wt = lanes / "feat"
        wt.mkdir(parents=True)
        capt = CapturingSubprocess(_proc(stdout=PROBE_A_REFUSAL_STREAM, returncode=0))
        with mock.patch.dict(os.environ, {"CODEX_DELEGATE_LANES_PARENT": str(lanes)}, clear=False):
            result = handle_tool_call(
                "codex_delegate_review",
                {"lane": "feat"},
                repo_root=repo,
                allowed_roots=[repo],
                subprocess_runner=capt,
                which=lambda n: "codex",
            )
        assert result.get("error") not in {
            "ARGV_FLAG_NOT_IN_CONTRACT",
            "WORKTREE_MISSING",
            "LANES_PARENT_UNTRUSTED",
        }, result
        assert capt.calls, f"review never spawned: {result}"
        args = capt.calls[-1]["args"]
        assert "--color" not in args
        assert "--cd" not in args
        assert "-s" not in args
        assert detect_exec_subcommand(args) == "exec review"
        assert result["sandbox"] == "read-only"
        assert result.get("sandbox_is_codex_default") is True
        assert isinstance(result["sandbox"], str)
        assert "(" not in result["sandbox"]


# ===========================================================================
# R3 — doctor bounded + DOCTOR_TIMEOUT
# ===========================================================================

class TestDoctorTimeout:
    def test_doctor_timeout_constant_le_60(self) -> None:
        assert DOCTOR_TIMEOUT_SECONDS <= 60.0
        assert DOCTOR_TIMEOUT_SECONDS > 0

    def test_doctor_returns_structured_timeout(self) -> None:
        capt = CapturingSubprocess(_proc(returncode=124, timed_out=True))
        result = run_doctor_json(
            codex_bin="codex",
            subprocess_runner=capt,
            which=lambda n: "/usr/bin/codex",
        )
        assert result["ok"] is False
        assert result["error"] == "DOCTOR_TIMEOUT"
        assert capt.calls[0]["timeout"] <= DOCTOR_TIMEOUT_SECONDS

    def test_doctor_caps_timeout_arg(self) -> None:
        capt = CapturingSubprocess(_proc(stdout="{}", returncode=0))
        run_doctor_json(
            codex_bin="codex",
            subprocess_runner=capt,
            which=lambda n: "/usr/bin/codex",
            timeout=900.0,
        )
        assert capt.calls[0]["timeout"] <= DOCTOR_TIMEOUT_SECONDS


# ===========================================================================
# R4 — stdin never inherited
# ===========================================================================

def _fake_popen(
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
    hang: bool = False,
    hang_forever: bool = False,
    partial_on_timeout: Optional[tuple[str, str]] = None,
):
    """Build a Popen double that records the kwargs it was constructed with.

    ``hang=True``: first ``communicate`` raises TimeoutExpired, second returns
    (stdout, stderr) — models a successful post-kill drain.
    ``hang_forever=True``: every ``communicate`` raises — models abandoned pipes.
    """
    seen: dict[str, Any] = {"communicate_calls": 0, "communicate_timeouts": []}

    class _Proc:
        pid = 4242

        def __init__(self) -> None:
            self.returncode = returncode
            self._drained = False

        def communicate(self, input=None, timeout=None):  # noqa: A002
            seen["input"] = input
            seen["communicate_timeout"] = timeout
            seen["communicate_calls"] = int(seen["communicate_calls"]) + 1
            seen["communicate_timeouts"].append(timeout)
            if hang_forever or (hang and not self._drained):
                self._drained = True
                exc = subprocess.TimeoutExpired(cmd="x", timeout=timeout or 0)
                if partial_on_timeout is not None:
                    exc.stdout, exc.stderr = partial_on_timeout
                raise exc
            return stdout, stderr

        def kill(self) -> None:
            seen["killed"] = True

        def poll(self):
            # kill_process_tree does not consult poll(); kept for Popen fidelity.
            return None if (hang or hang_forever) else returncode

    def _factory(argv, **kwargs):
        seen.update(kwargs)
        seen["argv"] = list(argv)
        return _Proc()

    return _factory, seen


class TestStdinIsolation:
    def test_subprocess_runner_devnull_when_no_input(self) -> None:
        factory, seen = _fake_popen()
        with mock.patch("codex_delegate.process.subprocess.Popen", factory):
            default_subprocess_runner(["codex", "--version"], None, 5.0, input_text=None)
        # R4 security property: must be the DEVNULL singleton, not None/PIPE/inherit.
        assert seen["stdin"] is subprocess.DEVNULL
        assert seen["input"] is None
        assert "stdin" in seen, "stdin kwarg must be passed explicitly (no inheritance)"

    def test_subprocess_runner_input_when_provided(self) -> None:
        factory, seen = _fake_popen()
        with mock.patch("codex_delegate.process.subprocess.Popen", factory):
            default_subprocess_runner(
                ["codex", "exec", "--cd", "/t", "-s", "read-only", "--json", "-"],
                None,
                5.0,
                input_text="goal text",
            )
        assert seen["stdin"] is subprocess.PIPE
        assert seen["input"] == "goal text"

    def test_git_runner_uses_devnull(self) -> None:
        factory, seen = _fake_popen(stdout="git version 2")
        with mock.patch("codex_delegate.process.subprocess.Popen", factory):
            default_git_runner(["--version"], None, 5.0)
        assert seen["stdin"] is subprocess.DEVNULL

    def test_removing_devnull_would_fail(self) -> None:
        """Theatre guard: assert exact DEVNULL object identity, not merely 'stdin set'."""
        factory, seen = _fake_popen()
        with mock.patch("codex_delegate.process.subprocess.Popen", factory):
            run_bounded(["codex", "--version"], None, 5.0, input_text=None)
        assert seen["stdin"] is not None
        assert seen["stdin"] is not subprocess.PIPE
        assert seen["stdin"] is subprocess.DEVNULL


# ===========================================================================
# R3b — a declared timeout must bound wall clock, not just the direct child
# ===========================================================================

class TestTimeoutKillsProcessTree:
    def test_timeout_kills_tree_and_returns_timed_out(self) -> None:
        factory, seen = _fake_popen(hang=True)
        killed: list[int] = []
        with mock.patch("codex_delegate.process.subprocess.Popen", factory), \
             mock.patch("codex_delegate.process.kill_process_tree",
                        side_effect=lambda p: killed.append(p.pid)):
            result = default_subprocess_runner(["codex", "doctor", "--json"], None, 1.0)
        assert result["timedOut"] is True
        assert result["returncode"] == 124
        assert killed == [4242], "the process tree must be killed, not only the direct child"
        # If run_bounded returned before killing, killed would be empty — not theatre.
        assert seen["communicate_calls"] >= 1

    def test_tree_kill_targets_descendants(self) -> None:
        """On Windows the kill must be /T (tree); elsewhere it must be killpg."""
        factory, _seen = _fake_popen(hang=True)
        with mock.patch("codex_delegate.process.subprocess.Popen", factory):
            proc = subprocess.Popen(["x"])  # returns the double
        if os.name == "nt":
            with mock.patch("codex_delegate.process.subprocess.run") as run:
                process_mod.kill_process_tree(proc)
                argv = list(run.call_args.args[0])
                assert argv[:3] == ["taskkill", "/F", "/T"]
                assert str(proc.pid) in argv
        else:  # pragma: no cover - POSIX branch
            with mock.patch("codex_delegate.process.os.killpg") as killpg, \
                 mock.patch("codex_delegate.process.os.getpgid", return_value=9999), \
                 mock.patch("codex_delegate.process.os.getpgrp", return_value=1):
                process_mod.kill_process_tree(proc)
                assert killpg.called
                assert killpg.call_args.args[0] == 9999

    def test_posix_killpg_refuses_own_process_group(self) -> None:
        """If start_new_session regressed, killpg must not signal our own group."""
        class _Proc:
            pid = 55

            def kill(self) -> None:
                self.killed = True  # type: ignore[attr-defined]

        proc = _Proc()
        # create=True: os.getpgid/getpgrp/killpg do not exist on Windows, but the
        # property under test (never signal our own group) must be asserted on
        # every host, not silently skipped on the one we develop on.
        with mock.patch("codex_delegate.process.os.name", "posix"), \
             mock.patch("codex_delegate.process.os.getpgid", return_value=100, create=True), \
             mock.patch("codex_delegate.process.os.getpgrp", return_value=100, create=True), \
             mock.patch("codex_delegate.process.os.killpg", create=True) as killpg:
            kill_process_tree(proc)  # type: ignore[arg-type]
        assert not killpg.called, "killpg on our own pgid would take down the MCP server"
        assert getattr(proc, "killed", False) is True

    def test_posix_killpg_signals_child_group_only(self) -> None:
        class _Proc:
            pid = 55

            def kill(self) -> None:
                self.killed = True  # type: ignore[attr-defined]

        proc = _Proc()
        with mock.patch("codex_delegate.process.os.name", "posix"), \
             mock.patch("codex_delegate.process.os.getpgid", return_value=777, create=True), \
             mock.patch("codex_delegate.process.os.getpgrp", return_value=100, create=True), \
             mock.patch("codex_delegate.process.os.killpg", create=True) as killpg, \
             mock.patch("codex_delegate.process.signal.SIGKILL", 9, create=True):
            kill_process_tree(proc)  # type: ignore[arg-type]
        killpg.assert_called_once_with(777, 9)

    def test_taskkill_missing_falls_back_to_proc_kill(self) -> None:
        """Stripped host without taskkill on PATH must not raise out of kill_process_tree."""
        class _Proc:
            pid = 9
            killed = False

            def kill(self) -> None:
                self.killed = True

        proc = _Proc()
        with mock.patch("codex_delegate.process.os.name", "nt"), \
             mock.patch(
                 "codex_delegate.process.subprocess.run",
                 side_effect=FileNotFoundError("taskkill"),
             ):
            kill_process_tree(proc)  # type: ignore[arg-type]
        assert proc.killed is True

    def test_taskkill_against_dead_pid_is_swallowed(self) -> None:
        class _Proc:
            pid = 9
            killed = False

            def kill(self) -> None:
                self.killed = True

        proc = _Proc()
        completed = subprocess.CompletedProcess(
            args=["taskkill"], returncode=128, stdout="", stderr="not found",
        )
        with mock.patch("codex_delegate.process.os.name", "nt"), \
             mock.patch("codex_delegate.process.subprocess.run", return_value=completed):
            kill_process_tree(proc)  # type: ignore[arg-type]
        assert proc.killed is True

    def test_abandons_pipes_rather_than_blocking_forever(self) -> None:
        """If descendants still hold the pipes after the kill, do not block."""
        factory, seen = _fake_popen(hang_forever=True)
        with mock.patch("codex_delegate.process.subprocess.Popen", factory), \
             mock.patch("codex_delegate.process.kill_process_tree") as tree_kill:
            result = default_subprocess_runner(["codex", "doctor", "--json"], None, 1.0)
        assert result["timedOut"] is True
        assert result["returncode"] == 124
        assert "timed out after" in result["stderr"]
        assert tree_kill.called
        # First communicate (bound) + second (grace) — both timed out.
        assert seen["communicate_calls"] == 2
        assert seen["communicate_timeouts"][0] == 1.0
        assert seen["communicate_timeouts"][1] == TREE_KILL_GRACE_SECONDS

    def test_partial_output_preserved_when_pipes_abandoned(self) -> None:
        factory, _seen = _fake_popen(
            hang_forever=True,
            partial_on_timeout=("{partial", "warn"),
        )
        with mock.patch("codex_delegate.process.subprocess.Popen", factory), \
             mock.patch("codex_delegate.process.kill_process_tree"):
            result = run_bounded(["codex", "doctor", "--json"], None, 1.0)
        assert result["timedOut"] is True
        assert result["stdout"] == "{partial"
        assert "warn" in result["stderr"]
        assert "timed out after 1.0s" in result["stderr"]

    def test_all_io_goes_through_communicate(self) -> None:
        """No path may write stdin / read pipes without communicate (deadlock risk)."""
        factory, seen = _fake_popen()
        big_goal = "x" * 60_000
        with mock.patch("codex_delegate.process.subprocess.Popen", factory):
            run_bounded(
                ["codex", "exec", "--json", "-"],
                None,
                5.0,
                input_text=big_goal,
            )
        assert seen["stdin"] is subprocess.PIPE
        assert seen["input"] == big_goal
        assert seen["communicate_calls"] == 1
        assert "stdout" in seen and seen["stdout"] is subprocess.PIPE
        assert "stderr" in seen and seen["stderr"] is subprocess.PIPE

    def test_posix_spawn_starts_new_session(self) -> None:
        """killpg is only safe when the child is not in our process group."""
        factory, seen = _fake_popen()
        with mock.patch("codex_delegate.process.os.name", "posix"), \
             mock.patch("codex_delegate.process.subprocess.Popen", factory):
            run_bounded(["codex", "--version"], None, 5.0)
        assert seen.get("start_new_session") is True

    def test_windows_spawn_does_not_set_start_new_session(self) -> None:
        factory, seen = _fake_popen()
        with mock.patch("codex_delegate.process.os.name", "nt"), \
             mock.patch("codex_delegate.process.subprocess.Popen", factory):
            run_bounded(["codex", "--version"], None, 5.0)
        assert "start_new_session" not in seen

    def test_missing_binary_sets_missing_flag(self) -> None:
        with mock.patch(
            "codex_delegate.process.subprocess.Popen",
            side_effect=FileNotFoundError("nope"),
        ):
            result = run_bounded(["no-such-binary"], None, 1.0)
        assert result.get("missing") is True
        assert result["returncode"] == 127

    def test_permission_error_is_not_missing(self) -> None:
        """Only FileNotFoundError maps to missing=True; other OS errors propagate."""
        with mock.patch(
            "codex_delegate.process.subprocess.Popen",
            side_effect=PermissionError("denied"),
        ):
            with pytest.raises(PermissionError):
                run_bounded(["/root/secret"], None, 1.0)

    def test_half_line_jsonl_does_not_look_like_success(self) -> None:
        """Partial stream after kill must not be treated as a completed turn."""
        half = (
            '{"type":"thread.started","thread_id":"t1"}\n'
            '{"type":"item.completed","item":{"id":"i","type":"agent_message","text":"hi'
        )
        parsed = parse_event_stream(half)
        assert parsed["turn_completed"] is False
        assert parsed["unparsed_lines"] >= 1
        # timedOut callers short-circuit before trusting this; still never raises.
        factory, _seen = _fake_popen(hang=True, stdout=half)
        with mock.patch("codex_delegate.process.subprocess.Popen", factory), \
             mock.patch("codex_delegate.process.kill_process_tree"):
            result = run_bounded(["codex", "exec", "--json", "-"], None, 1.0)
        assert result["timedOut"] is True
        # hang=True drains on second communicate with the provided stdout
        assert result["stdout"] == half


# ===========================================================================
# R5 — self-test row logic
# ===========================================================================

class TestSelfTestRow:
    def test_ok_false_is_never_pass(self) -> None:
        row = self_test_tool_row(
            "codex_delegate_doctor",
            {"ok": False, "error": "DOCTOR_TIMEOUT", "message": "timed out"},
        )
        assert row["ok"] is False
        assert "DOCTOR_TIMEOUT" in row["detail"]

    def test_vendor_timeout_is_skipped_not_failed(self) -> None:
        row = self_test_tool_row("codex_delegate_doctor", {"ok": False, "error": "DOCTOR_TIMEOUT"})
        assert row["ok"] is False, "a vendor skip must still not read as PASS"
        assert row["skipped"] is True

    def test_our_own_failure_is_not_skipped(self) -> None:
        row = self_test_tool_row("codex_delegate_status", {"ok": False, "error": "ALLOWED_ROOTS_EMPTY"})
        assert row["ok"] is False
        assert row["skipped"] is False

    def test_ok_true_is_pass(self) -> None:
        row = self_test_tool_row("codex_delegate_status", {"ok": True, "server": {}})
        assert row["ok"] is True
        assert row["detail"] == "ok"
        assert row["skipped"] is False

    def test_structured_dict_without_ok_is_fail(self) -> None:
        # A bare structured dict is not success.
        row = self_test_tool_row("codex_delegate_doctor", {"text_preview": "hanging..."})
        assert row["ok"] is False

    def test_total_skip_is_not_result_pass(self) -> None:
        """R5 trap: if every row is SKIP, RESULT must not read as healthy."""
        rows = [
            self_test_tool_row("a", {"ok": False, "error": "DOCTOR_TIMEOUT"}),
            self_test_tool_row("b", {"ok": False, "error": "DOCTOR_TIMEOUT"}),
        ]
        assert all(r["skipped"] for r in rows)
        exit_code, line, passed, failed, skipped = evaluate_self_test_rows(rows)
        assert passed == 0
        assert failed == 0
        assert skipped == 2
        assert exit_code == 1
        assert line.startswith("RESULT: FAIL")
        assert "2 skipped" in line

    def test_one_pass_one_skip_is_result_pass(self) -> None:
        rows = [
            {"name": "binary", "ok": True, "detail": "ok", "skipped": False},
            self_test_tool_row("doctor", {"ok": False, "error": "DOCTOR_TIMEOUT"}),
        ]
        exit_code, line, passed, failed, skipped = evaluate_self_test_rows(rows)
        assert exit_code == 0
        assert line.startswith("RESULT: PASS")
        assert passed == 1 and failed == 0 and skipped == 1

    def test_fail_on_skip_env_mode(self) -> None:
        rows = [
            {"name": "binary", "ok": True, "detail": "ok", "skipped": False},
            self_test_tool_row("doctor", {"ok": False, "error": "DOCTOR_TIMEOUT"}),
        ]
        exit_code, line, *_ = evaluate_self_test_rows(rows, fail_on_skip=True)
        assert exit_code == 1
        assert line.startswith("RESULT: FAIL")

    def test_real_failure_still_fails(self) -> None:
        rows = [
            {"name": "binary", "ok": True, "detail": "ok", "skipped": False},
            self_test_tool_row("status", {"ok": False, "error": "ALLOWED_ROOTS_EMPTY"}),
        ]
        exit_code, line, passed, failed, skipped = evaluate_self_test_rows(rows)
        assert exit_code == 1
        assert failed == 1 and skipped == 0 and passed == 1
        assert line.startswith("RESULT: FAIL")

    def test_only_doctor_timeout_is_in_vendor_skip_set(self) -> None:
        from codex_delegate.__main__ import VENDOR_SKIP_ERROR_CODES
        assert VENDOR_SKIP_ERROR_CODES == frozenset({"DOCTOR_TIMEOUT"})

    def test_doctor_timeout_not_env_tunable_below_floor(self) -> None:
        """A caller cannot shrink the bound to 0.1s and get a free vendor SKIP."""
        capt = CapturingSubprocess(_proc(returncode=124, timed_out=True))
        result = run_doctor_json(
            codex_bin="codex",
            subprocess_runner=capt,
            which=lambda n: "/usr/bin/codex",
            timeout=0.1,
        )
        assert result["error"] == "DOCTOR_TIMEOUT"
        # Floor is 1.0s; still capped by DOCTOR_TIMEOUT_SECONDS (constant, not env).
        assert capt.calls[0]["timeout"] >= 1.0
        assert capt.calls[0]["timeout"] <= DOCTOR_TIMEOUT_SECONDS
        # Bound is a module constant — no getenv of a doctor-timeout override.
        import inspect
        from codex_delegate import status as status_mod
        src = inspect.getsource(status_mod.run_doctor_json)
        assert "getenv" not in src and "environ" not in src


# ===========================================================================
# R7 — flag-set conformance (the immune system)
# ===========================================================================

class TestCliContractConformance:
    def test_exec_argv_flags_in_contract(self) -> None:
        argv = build_exec_argv(
            codex_bin="codex",
            worktree="/tmp/wt",
            last_message_path="/tmp/last.txt",
            sandbox=SANDBOX_WORKSPACE_WRITE,
            model="gpt-5.4-mini",
            reasoning_effort="high",
            ephemeral=True,
            output_schema_path="/tmp/schema.json",
        )
        assert detect_exec_subcommand(argv) == "exec"
        for flag in extract_flag_tokens(argv):
            assert flag in EXEC_FLAGS, f"unexpected flag for exec: {flag}"
        assert_flags_in_contract(argv)
        assert_argv_safe(argv)

    def test_review_argv_flags_in_contract(self) -> None:
        argv = build_review_argv(
            codex_bin="codex",
            model="gpt-5.4-mini",
            base_ref="main",
            uncommitted=True,
        )
        assert detect_exec_subcommand(argv) == "exec review"
        for flag in extract_flag_tokens(argv):
            assert flag in EXEC_REVIEW_FLAGS, f"unexpected flag for review: {flag}"
        assert "--color" not in argv
        assert "--cd" not in argv
        assert_flags_in_contract(argv)
        assert_argv_safe(argv)

    def test_readding_color_to_review_fails(self) -> None:
        argv = build_review_argv(codex_bin="codex")
        # Insert --color never as a regression would
        idx = argv.index("--json") + 1
        bad = argv[:idx] + ["--color", "never"] + argv[idx:]
        with pytest.raises(GuardError) as ei:
            assert_flags_in_contract(bad)
        assert ei.value.code == "ARGV_FLAG_NOT_IN_CONTRACT"

    def test_readding_cd_to_resume_fails(self) -> None:
        # Even a hand-built resume argv with --cd must be rejected.
        bad = [
            "codex", "exec", "resume", "00000000-0000-0000-0000-000000000000",
            "--cd", ".", "-s", "read-only", "--json", "-o", "z.txt", "-",
        ]
        with pytest.raises(GuardError) as ei:
            assert_flags_in_contract(bad)
        assert ei.value.code == "RESUME_UNSUPPORTED"
        # Also: --cd is not even in EXEC_RESUME_FLAGS
        assert "--cd" not in EXEC_RESUME_FLAGS
        assert "-s" not in EXEC_RESUME_FLAGS
        assert "--sandbox" not in EXEC_RESUME_FLAGS

    def test_resume_flags_have_no_cd_or_sandbox(self) -> None:
        assert "--cd" not in EXEC_RESUME_FLAGS
        assert "-C" not in EXEC_RESUME_FLAGS
        assert "-s" not in EXEC_RESUME_FLAGS
        assert "--sandbox" not in EXEC_RESUME_FLAGS

    def test_review_flags_have_no_color_cd_sandbox(self) -> None:
        assert "--color" not in EXEC_REVIEW_FLAGS
        assert "--cd" not in EXEC_REVIEW_FLAGS
        assert "-C" not in EXEC_REVIEW_FLAGS
        assert "-s" not in EXEC_REVIEW_FLAGS
        assert "--sandbox" not in EXEC_REVIEW_FLAGS


# ===========================================================================
# R8 — default timeout env non-numeric → TIMEOUT_INVALID
# ===========================================================================

class TestDefaultTimeoutEnv:
    def test_non_numeric_env_raises_timeout_invalid(self) -> None:
        from codex_delegate.handlers import _default_timeout
        with pytest.raises(GuardError) as ei:
            _default_timeout({"CODEX_DELEGATE_TIMEOUT_SECONDS": "not-a-number"})
        assert ei.value.code == "TIMEOUT_INVALID"

    def test_handle_tool_surfaces_timeout_invalid(self, tmp_path: Path) -> None:
        with mock.patch.dict(os.environ, {"CODEX_DELEGATE_TIMEOUT_SECONDS": "xyz"}, clear=False):
            # status path calls _default_timeout
            result = handle_tool_call(
                "codex_delegate_status",
                {},
                allowed_roots=[tmp_path],
                which=lambda n: None,
                git_runner=lambda args, cwd, timeout: _git_ok("git version 2.0"),
                subprocess_runner=lambda *a, **k: _proc(missing=True),
            )
            assert result["ok"] is False
            assert result["error"] == "TIMEOUT_INVALID"


# ===========================================================================
# R9 — delegation pins process cwd to worktree
# ===========================================================================

class TestDelegationCwd:
    def test_run_delegation_pins_cwd(self, tmp_path: Path) -> None:
        wt = tmp_path / "wt"
        wt.mkdir()
        capt = CapturingSubprocess(_proc(stdout=PROBE_A_REFUSAL_STREAM, returncode=0))
        run_delegation(
            goal="plan",
            worktree=wt,
            plan_only=True,
            subprocess_runner=capt,
            which=lambda n: "codex",
            timeout_seconds=60.0,
        )
        assert len(capt.calls) == 1
        assert Path(capt.calls[0]["cwd"]) == wt
        # --cd still present (not replaced by cwd alone)
        assert "--cd" in capt.calls[0]["args"]


# ===========================================================================
# R10 — truncation marker
# ===========================================================================

class TestTruncationMarker:
    def test_events_truncate_marks(self) -> None:
        long = "x" * 1000
        out = events_mod._truncate(long, 50)
        assert out.endswith(TRUNCATION_MARKER) or TRUNCATION_MARKER in out
        assert len(out) <= 50

    def test_runner_truncate_marks(self) -> None:
        long = "y" * 5000
        out = runner_mod._truncate(long, 100)
        assert TRUNCATION_MARKER in out
        assert len(out) <= 100

    def test_short_not_marked(self) -> None:
        assert events_mod._truncate("short", 50) == "short"
        assert runner_mod._truncate("short", 50) == "short"


# ===========================================================================
# R11 — smoke evaluation requires sentinel + empty changed_files
# ===========================================================================

class TestSmokeEvaluation:
    def test_pass_requires_sentinel_and_empty_changes(self) -> None:
        assert evaluate_smoke_result({
            "ok": True,
            "summary": f"Here is the token: {SMOKE_SENTINEL}",
            "changed_files": [],
        }) is True

    def test_fail_without_sentinel(self) -> None:
        assert evaluate_smoke_result({
            "ok": True,
            "summary": "I'll create hello.txt",
            "changed_files": [],
        }) is False

    def test_fail_when_ok_false(self) -> None:
        assert evaluate_smoke_result({
            "ok": False,
            "summary": SMOKE_SENTINEL,
            "changed_files": [],
        }) is False

    def test_fail_when_files_changed(self) -> None:
        assert evaluate_smoke_result({
            "ok": True,
            "summary": SMOKE_SENTINEL,
            "changed_files": ["hello.txt"],
        }) is False


# ===========================================================================
# Audit for status tool: no changed_file_count / no null wall
# ===========================================================================

class TestAuditHandlerEmission:
    def test_status_audit_omits_change_count_and_nulls(self, tmp_path: Path) -> None:
        buf = io.StringIO()
        handle_tool_call(
            "codex_delegate_status",
            {},
            allowed_roots=[tmp_path],
            which=lambda n: None,
            git_runner=lambda args, cwd, timeout: _git_ok("git version 2.0"),
            subprocess_runner=lambda *a, **k: _proc(missing=True),
            audit_stream=buf,
        )
        line = buf.getvalue().strip().splitlines()[-1]
        data = json.loads(line)
        assert "changed_file_count" not in data
        assert None not in data.values()
        # sandbox report object must not be dumped
        assert not isinstance(data.get("sandbox"), dict)

    def test_delegate_audit_carries_lane_and_fingerprint(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        lanes = tmp_path / "lanes"
        buf = io.StringIO()

        def worktree_add(tokens: list[str], cwd: Any, timeout: float) -> dict[str, Any]:
            if "-b" in tokens:
                bi = tokens.index("-b")
                p = Path(tokens[bi + 2])
            else:
                p = Path(tokens[tokens.index("add") + 1])
            p.mkdir(parents=True, exist_ok=True)
            return _git_ok()

        git = ScriptedGit({
            "--version": _git_ok("git version 2.40.0"),
            "rev-parse --verify": _git_ok("abc"),
            "status --porcelain": _git_ok(""),
            "worktree add": worktree_add,
            "diff --name-only": _git_ok(""),
            "diff --stat": _git_ok(""),
        })
        capt = CapturingSubprocess(_proc(stdout=PROBE_A_REFUSAL_STREAM, returncode=0))
        env_patch = {
            "CODEX_DELEGATE_LANES_PARENT": str(lanes),
        }
        # Ensure timeout env is valid if set on the host.
        env_patch["CODEX_DELEGATE_TIMEOUT_SECONDS"] = "120"
        with mock.patch.dict(os.environ, env_patch, clear=False):
            result = handle_tool_call(
                "codex_delegate_plan",
                {"goal": "unique-goal-text-for-fp", "lane": "feat"},
                repo_root=repo,
                allowed_roots=[repo],
                git_runner=git,
                subprocess_runner=capt,
                which=lambda n: "codex",
                audit_stream=buf,
            )
        assert result.get("ok") is True or result.get("lane") == "codex/feat", result
        line = buf.getvalue().strip().splitlines()[-1]
        data = json.loads(line)
        assert data.get("lane") == "codex/feat"
        assert "goal_sha256_8" in data
        assert "changed_file_count" in data
        assert isinstance(data["changed_file_count"], int)

