"""Trusted root / lanes-parent / binary resolution (fail closed)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

try:
    from .guard import (
        DEFAULT_CODEX_BIN,
        GuardError,
        parse_allowed_roots_env,
        path_in_allowlist,
        paths_equal,
        validate_codex_bin,
    )
    from .worktree import is_path_inside, resolve_lanes_parent
except ImportError:  # pragma: no cover
    from guard import (
        DEFAULT_CODEX_BIN,
        GuardError,
        parse_allowed_roots_env,
        path_in_allowlist,
        paths_equal,
        validate_codex_bin,
    )
    from worktree import is_path_inside, resolve_lanes_parent


def load_allowed_roots(
    env: Optional[Mapping[str, str]] = None,
    injected: Optional[Sequence[Path | str]] = None,
) -> list[Path]:
    """Load allowed repo roots. Injection wins, then env allowlist, then single root."""
    if injected is not None:
        return [Path(p) for p in injected]
    e = env if env is not None else os.environ
    multi = e.get("CODEX_DELEGATE_ALLOWED_ROOTS")
    if multi:
        return parse_allowed_roots_env(multi)
    single = e.get("CODEX_DELEGATE_REPO_ROOT")
    if single and single.strip():
        return [Path(single.strip())]
    return []


def resolve_trusted_repo_root(
    args: Mapping[str, Any],
    *,
    repo_root: Optional[Path | str] = None,
    allowed_roots: Optional[Sequence[Path | str]] = None,
) -> Path:
    """Resolve a trusted repo root from allowlist + optional client value."""
    roots = [Path(p) for p in (allowed_roots if allowed_roots is not None else load_allowed_roots())]
    if repo_root is not None:
        if not roots:
            return Path(repo_root)
        if path_in_allowlist(repo_root, roots):
            return Path(repo_root).resolve()
        for r in roots:
            if paths_equal(repo_root, r):
                return Path(r).resolve()
        raise GuardError("REPO_ROOT_UNTRUSTED", f"repo_root not in allowlist: {repo_root}")

    if not roots:
        raise GuardError(
            "ALLOWED_ROOTS_EMPTY",
            "no allowed roots configured; set CODEX_DELEGATE_ALLOWED_ROOTS or "
            "CODEX_DELEGATE_REPO_ROOT",
        )

    client = args.get("repo_root") if args else None
    if client is not None and str(client).strip():
        client_path = Path(str(client).strip())
        try:
            resolved = client_path.resolve()
        except OSError as exc:
            raise GuardError("REPO_ROOT_UNTRUSTED", f"repo_root not resolvable: {exc}") from exc
        if not path_in_allowlist(resolved, roots):
            raise GuardError(
                "REPO_ROOT_UNTRUSTED",
                f"client repo_root not in allowlist: {resolved}",
            )
        return resolved

    return Path(roots[0]).resolve()


def resolve_trusted_lanes_parent(
    args: Mapping[str, Any],
    *,
    repo_root: Path | str,
    env: Optional[Mapping[str, str]] = None,
) -> Path:
    """Resolve lanes parent; must never resolve inside repo_root."""
    e = env if env is not None else os.environ
    pinned = e.get("CODEX_DELEGATE_LANES_PARENT")
    client = None
    if args and args.get("lanes_parent") is not None and str(args.get("lanes_parent")).strip():
        client = Path(str(args["lanes_parent"]).strip())

    if pinned and pinned.strip():
        pin = Path(pinned.strip())
        if client is not None:
            try:
                client_res = client.resolve()
                pin_res = pin.resolve()
            except OSError as exc:
                raise GuardError("LANES_PARENT_UNTRUSTED", f"lanes_parent not resolvable: {exc}") from exc
            if not (paths_equal(client_res, pin_res) or is_path_inside(client_res, pin_res)):
                raise GuardError(
                    "LANES_PARENT_UNTRUSTED",
                    f"client lanes_parent not under pin: {client_res}",
                )
            result = client_res
        else:
            result = pin.resolve()
    elif client is not None:
        result = client.resolve()
    else:
        result = resolve_lanes_parent(repo_root)

    if is_path_inside(result, repo_root) or paths_equal(result, repo_root):
        raise GuardError(
            "LANES_PARENT_INSIDE_REPO",
            f"lanes_parent resolves inside repo_root: {result}",
        )
    return result


def resolve_server_codex_bin(args: Mapping[str, Any], env: Optional[Mapping[str, str]] = None) -> str:
    """Resolve binary from env only; client-supplied codex_bin fails closed."""
    if args and "codex_bin" in args:
        validate_codex_bin(args.get("codex_bin"), from_client=True)
    e = env if env is not None else os.environ
    return validate_codex_bin(e.get("CODEX_DELEGATE_BIN") or DEFAULT_CODEX_BIN, from_client=False)
