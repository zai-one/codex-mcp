"""Pure policy, validators, and argv construction. Zero I/O."""

from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

DEFAULT_CODEX_BIN = "codex"
ALLOWED_BIN_BASENAMES = frozenset({"codex", "codex.exe", "codex.cmd", "codex.bat"})
LANE_PREFIX = "codex"
RESERVED_LANE_NAMES = frozenset({"dev", "master", "main", "release", "prod", "production", "rc"})

SANDBOX_READ_ONLY = "read-only"
SANDBOX_WORKSPACE_WRITE = "workspace-write"
SANDBOX_DANGER_FULL_ACCESS = "danger-full-access"
ALLOWED_SANDBOX_MODES = frozenset({SANDBOX_READ_ONLY, SANDBOX_WORKSPACE_WRITE})

FORBIDDEN_CLI_FLAGS = frozenset({
    "--dangerously-bypass-approvals-and-sandbox",
    "--dangerously-bypass-hook-trust",
    "--ignore-rules",
    "--add-dir",
    "--oss",
    "--local-provider",
    "--remote",
    "--remote-auth-token-env",
})

ALLOWED_REASONING_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max", "ultra"})

DEFAULT_TIMEOUT_SECONDS = 900.0
MIN_TIMEOUT_SECONDS = 30.0
HARD_CAP_TIMEOUT_SECONDS = 3600.0
# Bounded probe for `codex doctor --json` (measured hang ≥277s without a bound;
# see CODEX-CLI-FACTS.md). Keep ≤ 60s; do not "optimise" this away.
DOCTOR_TIMEOUT_SECONDS = 45.0
MAX_GOAL_CHARS = 60_000
MAX_MODEL_CHARS = 128
MAX_OUTPUT_SCHEMA_CHARS = 16_000
STDIN_PROMPT_SENTINEL = "-"
TRUNCATION_MARKER = "…(truncated)"

_LANE_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")


class GuardError(Exception):
    """Policy violation with a structured error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def structured_error(code: str, message: str, **extra: Any) -> dict[str, Any]:
    """Return a fail-closed structured error dict."""
    result: dict[str, Any] = {"ok": False, "error": code, "message": message}
    result.update(extra)
    return result


def normalize_lane(name: Any) -> str:
    """Normalize a lane name to ``codex/<slug>``."""
    if name is None:
        raise GuardError("LANE_EMPTY", "lane name is empty")
    raw = str(name).strip()
    if not raw:
        raise GuardError("LANE_EMPTY", "lane name is empty")

    if raw.startswith(f"{LANE_PREFIX}/"):
        slug = raw[len(LANE_PREFIX) + 1 :]
    elif "/" in raw:
        raise GuardError("LANE_INVALID", f"lane name has invalid form: {raw!r}")
    else:
        slug = raw

    slug = slug.strip().lower()
    if not slug:
        raise GuardError("LANE_EMPTY", "lane name is empty")
    if not _LANE_SLUG_RE.match(slug):
        raise GuardError(
            "LANE_INVALID",
            f"lane slug must match ^[a-z0-9][a-z0-9-]*$: {slug!r}",
        )
    if slug in RESERVED_LANE_NAMES:
        raise GuardError("LANE_RESERVED", f"lane name is reserved: {slug!r}")
    return f"{LANE_PREFIX}/{slug}"


def validate_goal(goal: Any) -> str:
    """Validate and strip a goal string."""
    if goal is None:
        raise GuardError("GOAL_EMPTY", "goal is empty")
    if not isinstance(goal, str):
        raise GuardError("GOAL_INVALID", "goal must be a string")
    text = goal.strip()
    if not text:
        raise GuardError("GOAL_EMPTY", "goal is empty")
    if "\x00" in text:
        raise GuardError("GOAL_INVALID", "goal contains null bytes")
    if len(text) > MAX_GOAL_CHARS:
        raise GuardError(
            "GOAL_TOO_LONG",
            f"goal exceeds {MAX_GOAL_CHARS} characters ({len(text)})",
        )
    return text


def validate_model(value: Any) -> Optional[str]:
    """Validate an optional model slug."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise GuardError("MODEL_INVALID", "model must be a string")
    text = value.strip()
    if not text:
        return None
    if len(text) > MAX_MODEL_CHARS:
        raise GuardError("MODEL_INVALID", f"model exceeds {MAX_MODEL_CHARS} characters")
    if text.startswith("-"):
        raise GuardError("MODEL_INVALID", "model must not start with '-'")
    if _CONTROL_CHAR_RE.search(text):
        raise GuardError("MODEL_INVALID", "model contains control characters")
    return text


def validate_reasoning_effort(value: Any) -> Optional[str]:
    """Validate an optional reasoning effort level."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise GuardError("REASONING_EFFORT_INVALID", "reasoning_effort must be a string")
    text = value.strip().lower()
    if not text:
        return None
    if text not in ALLOWED_REASONING_EFFORTS:
        raise GuardError(
            "REASONING_EFFORT_INVALID",
            f"reasoning_effort must be one of {sorted(ALLOWED_REASONING_EFFORTS)}",
        )
    return text


def validate_sandbox_mode(value: Any, *, plan_only: bool = False) -> str:
    """Validate sandbox mode with plan-only constraints."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return SANDBOX_READ_ONLY if plan_only else SANDBOX_WORKSPACE_WRITE

    if not isinstance(value, str):
        raise GuardError("SANDBOX_INVALID", "sandbox must be a string")
    mode = value.strip().lower()
    if mode == SANDBOX_DANGER_FULL_ACCESS:
        raise GuardError(
            "SANDBOX_FORBIDDEN",
            "danger-full-access is forbidden",
        )
    if mode not in ALLOWED_SANDBOX_MODES:
        raise GuardError("SANDBOX_INVALID", f"unknown sandbox mode: {mode!r}")
    if plan_only and mode != SANDBOX_READ_ONLY:
        raise GuardError(
            "SANDBOX_PLAN_CONFLICT",
            "plan_only requires read-only sandbox",
        )
    return mode


def validate_session_id(value: Any) -> Optional[str]:
    """Validate an optional session UUID."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise GuardError("SESSION_ID_INVALID", "session id must be a string")
    text = value.strip()
    if not text:
        return None
    try:
        parsed = uuid.UUID(text)
    except (ValueError, AttributeError, TypeError) as exc:
        raise GuardError("SESSION_ID_INVALID", f"session id is not a UUID: {text!r}") from exc
    return str(parsed)


def validate_timeout(value: Any) -> float:
    """Validate timeout seconds. Does not silently clamp caller values."""
    if value is None:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        timeout = float(value)
    except (TypeError, ValueError) as exc:
        raise GuardError("TIMEOUT_INVALID", f"timeout must be a number: {value!r}") from exc
    if timeout < MIN_TIMEOUT_SECONDS or timeout > HARD_CAP_TIMEOUT_SECONDS:
        raise GuardError(
            "TIMEOUT_INVALID",
            f"timeout must be between {MIN_TIMEOUT_SECONDS} and {HARD_CAP_TIMEOUT_SECONDS}",
        )
    return timeout


def validate_output_schema(value: Any) -> Optional[str]:
    """Accept a dict or JSON string; return compact JSON object text."""
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise GuardError("OUTPUT_SCHEMA_INVALID", f"output_schema is not valid JSON: {exc}") from exc
    elif isinstance(value, dict):
        parsed = value
    else:
        raise GuardError("OUTPUT_SCHEMA_INVALID", "output_schema must be a JSON object or string")

    if not isinstance(parsed, dict):
        raise GuardError("OUTPUT_SCHEMA_INVALID", "output_schema must be a JSON object")
    compact = json.dumps(parsed, separators=(",", ":"), ensure_ascii=False)
    if len(compact) > MAX_OUTPUT_SCHEMA_CHARS:
        raise GuardError(
            "OUTPUT_SCHEMA_TOO_LONG",
            f"output_schema exceeds {MAX_OUTPUT_SCHEMA_CHARS} characters",
        )
    return compact


def validate_codex_bin(value: Any, *, from_client: bool = False) -> str:
    """Validate the codex binary name/path. Client values always fail closed."""
    if from_client:
        raise GuardError(
            "CODEX_BIN_CLIENT_FORBIDDEN",
            "client may not supply codex_bin",
        )
    if value is None:
        return DEFAULT_CODEX_BIN
    if not isinstance(value, str):
        raise GuardError("CODEX_BIN_INVALID", "codex_bin must be a string")
    text = value.strip()
    if not text:
        return DEFAULT_CODEX_BIN

    basename = Path(text).name
    if basename.lower() not in {b.lower() for b in ALLOWED_BIN_BASENAMES}:
        raise GuardError(
            "CODEX_BIN_INVALID",
            f"codex_bin basename not allowed: {basename!r}",
        )
    # Bare name or absolute path only.
    if os.path.sep in text or (os.path.altsep and os.path.altsep in text):
        path = Path(text)
        if not path.is_absolute():
            raise GuardError(
                "CODEX_BIN_INVALID",
                "codex_bin path must be absolute or a bare name",
            )
    return text


def parse_allowed_roots_env(raw: Any) -> list[Path]:
    """Parse allowed roots from ``;``/newline-separated text or a JSON array."""
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        items = [str(x).strip() for x in raw if str(x).strip()]
    else:
        text = str(raw).strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, list):
                items = [str(x).strip() for x in parsed if str(x).strip()]
            else:
                items = [p.strip() for p in re.split(r"[;\n\r]+", text) if p.strip()]
        else:
            items = [p.strip() for p in re.split(r"[;\n\r]+", text) if p.strip()]
    return [Path(p) for p in items]


def paths_equal(a: Any, b: Any) -> bool:
    """Compare paths for equality (case-insensitive on Windows)."""
    pa = Path(a)
    pb = Path(b)
    try:
        sa = str(pa.resolve())
        sb = str(pb.resolve())
    except OSError:
        sa = str(pa)
        sb = str(pb)
    if os.name == "nt":
        return sa.lower() == sb.lower()
    return sa == sb


def path_in_allowlist(candidate: Any, allowlist: Sequence[Any]) -> bool:
    """True when ``candidate.resolve()`` equals an allowlist entry's resolve()."""
    try:
        cand = Path(candidate).resolve()
    except OSError:
        return False
    for entry in allowlist:
        try:
            root = Path(entry).resolve()
        except OSError:
            continue
        if paths_equal(cand, root):
            return True
    return False


def confine_path_to_root(path: Any, root: Any, *, field: str = "path") -> Path:
    """Ensure ``path`` resolves inside ``root``; raise PATH_ESCAPE otherwise."""
    try:
        resolved = Path(path).resolve()
        root_resolved = Path(root).resolve()
    except OSError as exc:
        raise GuardError("PATH_ESCAPE", f"{field} could not be resolved: {exc}") from exc

    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise GuardError(
            "PATH_ESCAPE",
            f"{field} escapes root: {resolved} not under {root_resolved}",
        ) from exc
    return resolved


def build_execution_profile(*, plan_only: bool = False) -> dict[str, Any]:
    """Build the sandbox/execution profile for a delegation run.

    Confinement is Codex's own OS sandbox selected via ``-s``, proven on Windows
    by probes A/C in CODEX-CLI-FACTS.md. We add no pattern-based deny list,
    because Codex has no per-command allow/deny flags — inventing one would be
    theatre. ``writable_roots`` is conceptually ``["<cd>"]`` (the worktree
    passed via ``--cd``). Network access and git-metadata writes are not
    granted by this profile.
    """
    if plan_only:
        return {
            "sandbox": SANDBOX_READ_ONLY,
            "writable_roots": ["<cd>"],
            "network_access": False,
            "git_metadata_writable": False,
            "mode": "plan",
        }
    return {
        "sandbox": SANDBOX_WORKSPACE_WRITE,
        "writable_roots": ["<cd>"],
        "network_access": False,
        "git_metadata_writable": False,
        "mode": "execute",
    }


def build_exec_argv(
    *,
    codex_bin: str,
    worktree: str,
    last_message_path: str,
    sandbox: str,
    plan_only: bool = False,
    model: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
    output_schema_path: Optional[str] = None,
    ignore_user_config: bool = True,
    ephemeral: bool = False,
    resume: Any = None,
) -> list[str]:
    """Build a safe plain ``codex exec`` argv. Prompt is always the stdin sentinel ``-``.

    ``resume`` is fail-closed: ``codex exec resume`` rejects ``--cd`` and ``-s``,
    so a resumed session would run under the Codex default sandbox (not one we
    selected). See R1 / CODEX-CLI-FACTS.md sub-command flag sets.
    """
    del plan_only  # sandbox already encodes plan vs execute; kept for call-site clarity
    if resume is not None and resume is not False:
        raise GuardError(
            "RESUME_UNSUPPORTED",
            "codex exec resume cannot take --cd/-s; resume is fail-closed by codex_delegate",
        )
    if sandbox not in ALLOWED_SANDBOX_MODES:
        raise GuardError("SANDBOX_INVALID", f"sandbox not allowed: {sandbox!r}")

    # Plain exec only. --color is accepted by `codex exec` (not by `exec review`).
    argv: list[str] = [
        codex_bin, "exec",
        "--cd", str(worktree),
        "-s", sandbox,
        "--json",
        "--color", "never",
        "-o", str(last_message_path),
    ]

    if ignore_user_config:
        argv.append("--ignore-user-config")
    if ephemeral:
        argv.append("--ephemeral")
    if model:
        argv.extend(["-m", model])
    if reasoning_effort:
        effort = validate_reasoning_effort(reasoning_effort)
        if effort:
            # Best-effort only: the CLI does NOT reject bogus values for this key
            # (CODEX-CLI-FACTS.md). We validate against ALLOWED_REASONING_EFFORTS
            # ourselves and never claim CLI-level enforcement.
            argv.extend(["-c", f'model_reasoning_effort="{effort}"'])
    if output_schema_path:
        argv.extend(["--output-schema", str(output_schema_path)])

    argv.append(STDIN_PROMPT_SENTINEL)
    return argv


def build_review_argv(
    *,
    codex_bin: str,
    model: Optional[str] = None,
    base_ref: Optional[str] = None,
    uncommitted: bool = False,
    ignore_user_config: bool = True,
) -> list[str]:
    """Build ``codex exec review`` argv (no --cd, no -s, no --color).

    Working directory is the process cwd (set by the runner). Sandbox is the
    Codex default (probe A: read-only). Option set: CODEX-CLI-FACTS.md /
    cli_contract.EXEC_REVIEW_FLAGS.
    """
    argv: list[str] = [codex_bin, "exec", "review", "--json"]
    if uncommitted:
        argv.append("--uncommitted")
    if base_ref:
        argv.extend(["--base", str(base_ref)])
    if model:
        argv.extend(["-m", model])
    if ignore_user_config:
        argv.append("--ignore-user-config")
    argv.append(STDIN_PROMPT_SENTINEL)
    return argv


def assert_argv_safe(argv: Sequence[str]) -> None:
    """Defence-in-depth argv checks immediately before spawn."""
    if not argv:
        raise GuardError("ARGV_MISSING_EXEC", "argv is empty")

    tokens = [str(t) for t in argv]
    # First non-binary token must be exec
    if len(tokens) < 2 or tokens[1] != "exec":
        non_bin = tokens[1:] if len(tokens) > 1 else tokens
        if not non_bin or non_bin[0] != "exec":
            raise GuardError("ARGV_MISSING_EXEC", "argv missing 'exec' as first subcommand")

    # Detect review vs plain exec for structural requirements.
    is_review = len(tokens) >= 3 and tokens[2] == "review"
    is_resume = len(tokens) >= 3 and tokens[2] == "resume"
    if is_resume:
        raise GuardError(
            "RESUME_UNSUPPORTED",
            "codex exec resume cannot take --cd/-s; resume is fail-closed",
        )

    if not is_review:
        if "--cd" not in tokens:
            raise GuardError("ARGV_MISSING_CD", "argv missing --cd")
        cd_idx = tokens.index("--cd")
        if cd_idx + 1 >= len(tokens) or tokens[cd_idx + 1].startswith("-"):
            raise GuardError("ARGV_MISSING_CD", "argv --cd missing value")

        if "-s" not in tokens:
            raise GuardError("ARGV_SANDBOX_INVALID", "argv missing -s sandbox")
        s_idx = tokens.index("-s")
        if s_idx + 1 >= len(tokens):
            raise GuardError("ARGV_SANDBOX_INVALID", "argv -s missing value")
        sandbox_val = tokens[s_idx + 1]
        if sandbox_val not in ALLOWED_SANDBOX_MODES:
            raise GuardError("ARGV_SANDBOX_INVALID", f"argv sandbox not allowed: {sandbox_val!r}")

    if "--json" not in tokens:
        raise GuardError("ARGV_MISSING_JSON", "argv missing --json")

    if not tokens or tokens[-1] != STDIN_PROMPT_SENTINEL:
        raise GuardError("ARGV_PROMPT_NOT_STDIN", "argv final token must be stdin sentinel '-'")

    for tok in tokens:
        if tok in FORBIDDEN_CLI_FLAGS:
            raise GuardError("ARGV_FORBIDDEN_FLAG", f"forbidden flag in argv: {tok}")
        if tok.startswith("--dangerously"):
            raise GuardError("ARGV_FORBIDDEN_FLAG", f"forbidden flag in argv: {tok}")

    # Reject -c sandbox_mode=...
    i = 0
    while i < len(tokens):
        if tokens[i] == "-c":
            if i + 1 < len(tokens):
                override = tokens[i + 1]
                key = override.split("=", 1)[0].strip().strip('"').strip("'")
                if key == "sandbox_mode":
                    raise GuardError(
                        "ARGV_FORBIDDEN_FLAG",
                        "argv must not override sandbox_mode via -c",
                    )
            i += 2
            continue
        i += 1

    # R7 immune system: every emitted flag must be in the real sub-command set.
    try:
        from .cli_contract import assert_flags_in_contract
    except ImportError:  # pragma: no cover
        from cli_contract import assert_flags_in_contract
    assert_flags_in_contract(tokens)



def profile_is_read_only(profile: Mapping[str, Any]) -> bool:
    """True when the execution profile is plan/read-only."""
    return profile.get("sandbox") == SANDBOX_READ_ONLY or profile.get("mode") == "plan"


def argv_uses_stdin_prompt(argv: Sequence[str]) -> bool:
    """True when argv ends with the stdin prompt sentinel."""
    return bool(argv) and argv[-1] == STDIN_PROMPT_SENTINEL


def argv_sandbox_mode(argv: Sequence[str]) -> Optional[str]:
    """Extract the ``-s`` value from argv, if present."""
    tokens = list(argv)
    if "-s" not in tokens:
        return None
    idx = tokens.index("-s")
    if idx + 1 >= len(tokens):
        return None
    return tokens[idx + 1]
