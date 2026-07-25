"""Authoritative Codex CLI flag-set contract for sub-commands we invoke.

Source of truth: ``CODEX-CLI-FACTS.md`` (live probes of codex-cli 0.144.1).
Every flag this package emits for ``exec`` / ``exec resume`` / ``exec review``
must appear in the matching set below. Runtime gate: ``assert_flags_in_contract``
(wired into ``assert_argv_safe``). Conformance tests walk produced argv.

Do not invent flags. If a flag is not in CODEX-CLI-FACTS.md, it does not exist.
"""

from __future__ import annotations

from typing import Optional, Sequence

# ---------------------------------------------------------------------------
# Sub-command flag sets (transcribed from CODEX-CLI-FACTS.md + live probes)
# ---------------------------------------------------------------------------

# ``codex exec --help`` (plain exec). Reproduction baseline: CODEX-CLI-FACTS.md.
EXEC_FLAGS: frozenset[str] = frozenset({
    "-c", "--config",
    "--enable", "--disable",
    "--strict-config",
    "-i", "--image",
    "-m", "--model",
    "--oss", "--local-provider",
    "-p", "--profile",
    "-s", "--sandbox",
    "--dangerously-bypass-approvals-and-sandbox",
    "--dangerously-bypass-hook-trust",
    "-C", "--cd",
    "--add-dir",
    "--skip-git-repo-check",
    "--ephemeral",
    "--ignore-user-config",
    "--ignore-rules",
    "--output-schema",
    "--color",
    "--json",
    "-o", "--output-last-message",
    "-h", "--help",
})

# ``codex exec resume`` — NO --cd, NO -s/--sandbox.
# Reproduction:
#   codex exec resume 00000000-0000-0000-0000-000000000000 --cd . -s read-only --json -o z.txt -
#   → error: unexpected argument '--cd' found
EXEC_RESUME_FLAGS: frozenset[str] = frozenset({
    "-c", "--config",
    "--last", "--all",
    "--enable", "--disable",
    "-i", "--image",
    "--strict-config",
    "-m", "--model",
    "--dangerously-bypass-approvals-and-sandbox",
    "--dangerously-bypass-hook-trust",
    "--skip-git-repo-check",
    "--ephemeral",
    "--ignore-user-config",
    "--ignore-rules",
    "--output-schema",
    "--json",
    "-o", "--output-last-message",
    "-h", "--help",
})

# ``codex exec review`` — NO --cd, NO -s/--sandbox, NO --color.
# Reproduction:
#   codex exec review --color never --json -
#   → error: unexpected argument '--color' found
EXEC_REVIEW_FLAGS: frozenset[str] = frozenset({
    "-c", "--config",
    "--uncommitted",
    "--base",
    "--enable",
    "--commit",
    "--disable",
    "--strict-config",
    "--title",
    "-m", "--model",
    "--dangerously-bypass-approvals-and-sandbox",
    "--dangerously-bypass-hook-trust",
    "--skip-git-repo-check",
    "--ephemeral",
    "--ignore-user-config",
    "--ignore-rules",
    "--output-schema",
    "--json",
    "-o", "--output-last-message",
    "-h", "--help",
})

SUBCOMMAND_FLAG_SETS: dict[str, frozenset[str]] = {
    "exec": EXEC_FLAGS,
    "exec resume": EXEC_RESUME_FLAGS,
    "exec review": EXEC_REVIEW_FLAGS,
}

# Sub-commands this package is allowed to spawn. ``exec resume`` is deliberately
# absent: resume cannot take -s, so sandbox is not caller-controlled (fail closed).
SUPPORTED_SUBCOMMANDS: frozenset[str] = frozenset({"exec", "exec review"})


def _guard_error(code: str, message: str) -> Exception:
    """Local import avoids circular import with guard.py."""
    try:
        from .guard import GuardError
    except ImportError:  # pragma: no cover
        from guard import GuardError
    return GuardError(code, message)


def detect_exec_subcommand(argv: Sequence[str]) -> Optional[str]:
    """Return ``exec`` / ``exec resume`` / ``exec review`` when present, else None."""
    tokens = [str(t) for t in argv]
    if "exec" not in tokens:
        return None
    idx = tokens.index("exec")
    if idx + 1 < len(tokens):
        nxt = tokens[idx + 1]
        if nxt == "resume":
            return "exec resume"
        if nxt == "review":
            return "exec review"
    return "exec"


def normalize_flag_token(token: str) -> str:
    """Strip ``=value`` from long options; leave short flags unchanged."""
    tok = str(token)
    if tok.startswith("--") and "=" in tok:
        return tok.split("=", 1)[0]
    return tok


def extract_flag_tokens(argv: Sequence[str]) -> list[str]:
    """Return every argv token that is a CLI flag (starts with ``-``, not bare ``-``)."""
    flags: list[str] = []
    for tok in argv:
        s = str(tok)
        if s == "-":
            continue  # stdin prompt sentinel
        if s.startswith("-"):
            flags.append(normalize_flag_token(s))
    return flags


def flag_set_for(subcommand: str) -> frozenset[str]:
    """Lookup the accepted option set; raise if the sub-command is unknown."""
    key = subcommand.strip().lower()
    if key not in SUBCOMMAND_FLAG_SETS:
        raise _guard_error(
            "ARGV_UNKNOWN_SUBCOMMAND",
            f"no flag-set contract for sub-command: {subcommand!r}",
        )
    return SUBCOMMAND_FLAG_SETS[key]


def assert_flags_in_contract(
    argv: Sequence[str],
    *,
    subcommand: Optional[str] = None,
) -> None:
    """Reject any flag not in the real sub-command's accepted option set.

    Also fail closed on ``exec resume`` (unsupported by this package — see R1).
    """
    detected = subcommand or detect_exec_subcommand(argv)
    if detected is None:
        return
    if detected == "exec resume" or detected not in SUPPORTED_SUBCOMMANDS:
        if detected == "exec resume":
            raise _guard_error(
                "RESUME_UNSUPPORTED",
                "codex exec resume cannot take --cd/-s; resume is fail-closed",
            )
        raise _guard_error(
            "ARGV_UNKNOWN_SUBCOMMAND",
            f"sub-command not supported by codex_delegate: {detected!r}",
        )

    allowed = flag_set_for(detected)
    for flag in extract_flag_tokens(argv):
        if flag not in allowed:
            raise _guard_error(
                "ARGV_FLAG_NOT_IN_CONTRACT",
                f"flag {flag!r} is not accepted by `{detected}` "
                f"(see CODEX-CLI-FACTS.md / cli_contract.py)",
            )
