"""CLI entry: python -m codex_delegate [--self-test | --smoke-delegate]."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping, Optional

try:
    from .server import (
        SERVER_NAME,
        TOOL_NAMES,
        handle_jsonrpc,
        handle_tool_call,
        serve_stdio,
    )
    from .status import probe_auth_presence, probe_codex_version, probe_git_available
except ImportError:  # pragma: no cover
    from server import (
        SERVER_NAME,
        TOOL_NAMES,
        handle_jsonrpc,
        handle_tool_call,
        serve_stdio,
    )
    from status import probe_auth_presence, probe_codex_version, probe_git_available

# Smoke must ask a read-only question with a verifiable token (R11).
SMOKE_SENTINEL = "CODEX_DELEGATE_SMOKE_OK"
SMOKE_GOAL = (
    "Do not create, modify, or delete any files. "
    f"Reply with exactly this token on its own line and nothing else: {SMOKE_SENTINEL}"
)


def _row(name: str, ok: bool, detail: str = "", *, skipped: bool = False) -> dict[str, Any]:
    return {"name": name, "ok": ok, "detail": detail, "skipped": skipped}


# Failures owned by the vendor binary, not by this server. They are reported as
# SKIP: never PASS (R5 — a tool with ok:false must not read as healthy), but they
# do not condemn our own wiring either. `codex doctor --json` does not return on
# this host; the bound is enforced by us and the tool degrades honestly.
VENDOR_SKIP_ERROR_CODES = frozenset({"DOCTOR_TIMEOUT"})


def self_test_tool_row(name: str, result: Mapping[str, Any]) -> dict[str, Any]:
    """Map a tool result to a self-test row.

    A tool that returned ``ok: false`` MUST NOT produce a PASS row. Audit
    ``outcome`` and the self-test verdict for the same call must never
    disagree (R5). A vendor-side unresponsiveness listed in
    ``VENDOR_SKIP_ERROR_CODES`` yields SKIP, which is reported but does not
    fail the run.
    """
    ok = bool(result.get("ok"))
    if ok:
        detail = "ok"
        if result.get("error"):
            detail = str(result.get("error"))
        return _row(name, True, detail)

    code = str(result.get("error") or "")
    detail = str(result.get("error") or result.get("message") or "failed")
    if code in VENDOR_SKIP_ERROR_CODES:
        return _row(name, False, f"{detail} (vendor-side; bound enforced)", skipped=True)
    return _row(name, False, detail)


def run_self_test() -> int:
    """PASS/FAIL table without real delegation."""
    rows: list[dict[str, Any]] = []

    # Binary / version
    which = shutil.which("codex")
    rows.append(_row("binary", bool(which), which or "codex not on PATH"))

    ver = probe_codex_version()
    rows.append(_row(
        "version",
        bool(ver.get("ok") and ver.get("version")),
        str(ver.get("version") or ver.get("message") or ver.get("error") or ""),
    ))

    auth = probe_auth_presence()
    rows.append(_row(
        "auth_presence",
        bool(auth.get("ok")),
        f"present={auth.get('auth_present')} method={auth.get('method')}",
    ))

    git = probe_git_available()
    rows.append(_row(
        "git",
        bool(git.get("available")),
        str(git.get("version") or git.get("message") or ""),
    ))

    # in-process initialize
    init = handle_jsonrpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    init_ok = (
        isinstance(init, dict)
        and (init.get("result") or {}).get("serverInfo", {}).get("name") == SERVER_NAME
    )
    rows.append(_row("initialize", init_ok, SERVER_NAME if init_ok else str(init)))

    listed = handle_jsonrpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tools = ((listed or {}).get("result") or {}).get("tools") or []
    names = sorted(t.get("name") for t in tools if isinstance(t, dict))
    expected = sorted(TOOL_NAMES)
    list_ok = names == expected
    rows.append(_row("tools/list", list_ok, ",".join(names)))

    # read-only status tools via handle_tool_call
    tmp_root = Path(tempfile.mkdtemp(prefix="codex-delegate-selftest-"))
    try:
        status = handle_tool_call(
            "codex_delegate_status",
            {},
            allowed_roots=[tmp_root],
        )
        rows.append(self_test_tool_row("codex_delegate_status", status))

        doctor = handle_tool_call("codex_delegate_doctor", {}, allowed_roots=[tmp_root])
        rows.append(self_test_tool_row("codex_delegate_doctor", doctor))

        models = handle_tool_call("codex_delegate_models", {}, allowed_roots=[tmp_root])
        rows.append(self_test_tool_row("codex_delegate_models", models))

        lanes = handle_tool_call(
            "codex_delegate_lanes",
            {},
            repo_root=tmp_root,
            allowed_roots=[tmp_root],
            git_runner=_noop_git_runner,
        )
        rows.append(self_test_tool_row("codex_delegate_lanes", lanes))
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    # print table
    width = max(len(r["name"]) for r in rows)
    failed = 0
    skipped = 0
    for r in rows:
        if r["ok"]:
            flag = "PASS"
        elif r.get("skipped"):
            flag = "SKIP"
            skipped += 1
        else:
            flag = "FAIL"
            failed += 1
        print(f"{r['name']:<{width}}  {flag}  {r['detail']}")

    print()
    suffix = f" ({skipped} skipped)" if skipped else ""
    print(f"RESULT: {'PASS' if failed == 0 else 'FAIL'}{suffix}")
    return 0 if failed == 0 else 1


def _noop_git_runner(args, cwd, timeout):  # type: ignore[no-untyped-def]
    return {
        "args": list(args),
        "returncode": 0,
        "stdout": "",
        "stderr": "",
        "timedOut": False,
    }


def evaluate_smoke_result(result: Mapping[str, Any], *, sentinel: str = SMOKE_SENTINEL) -> bool:
    """Smoke passes only when ok, sentinel is in summary, and no files changed."""
    if result.get("ok") is not True:
        return False
    summary = result.get("summary") or ""
    if sentinel not in str(summary):
        return False
    changed = result.get("changed_files")
    if changed is None or list(changed) != []:
        return False
    return True


def run_smoke_delegate() -> int:
    """One real, bounded, plan-only run in a throwaway git repo."""
    tmp = Path(tempfile.mkdtemp(prefix="codex-delegate-smoke-"))
    lanes_parent = tmp / "lanes"
    repo = tmp / "repo"
    try:
        repo.mkdir(parents=True)
        lanes_parent.mkdir(parents=True)

        def git(args: list[str], cwd: Path) -> None:
            import subprocess
            subprocess.run(
                ["git", *args],
                cwd=str(cwd),
                check=True,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                stdin=subprocess.DEVNULL,
            )

        git(["init"], repo)
        git(["config", "user.email", "smoke@example.com"], repo)
        git(["config", "user.name", "Smoke Test"], repo)
        (repo / "README.md").write_text("# smoke\n", encoding="utf-8")
        git(["add", "README.md"], repo)
        git(["commit", "-m", "init"], repo)

        try:
            from .runner import delegate
        except ImportError:
            from runner import delegate

        result = delegate(
            goal=SMOKE_GOAL,
            lane="smoke",
            repo_root=repo,
            base_ref="HEAD",
            lanes_parent=lanes_parent,
            plan_only=True,
            model=os.environ.get("CODEX_DELEGATE_MODEL") or "gpt-5.4-mini",
            timeout_seconds=120.0,
            require_clean_base=False,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        if evaluate_smoke_result(result):
            print("SMOKE PASS")
            return 0
        print("SMOKE FAIL")
        return 1
    except Exception as exc:
        print(f"SMOKE FAIL: {type(exc).__name__}: {exc}")
        return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv: Optional[list[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--self-test" in args:
        return run_self_test()
    if "--smoke-delegate" in args:
        return run_smoke_delegate()
    serve_stdio()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
