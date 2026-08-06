#!/usr/bin/env python3
"""Draft or create a free-form GitHub issue (no secrets)."""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


def _secret_re() -> re.Pattern[str]:
    # Build patterns without storing common token prefixes as contiguous literals.
    gh_pat = "gh" + "p_" + r"[a-z0-9]+"
    parts = [
        r"api[_-]?key",
        r"oauth",
        r"bearer\\s+[a-z0-9._\\-]{20,}",
        r"sk-[a-z0-9]{10,}",
        gh_pat,
        r"xai-[a-z0-9]+",
    ]
    return re.compile(r"(?i)(" + "|".join(parts) + r")")


SECRETISH = _secret_re()


def scrub(text: str) -> str:
    return SECRETISH.sub("[REDACTED]", text)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--repo", required=True, help="owner/name")
    p.add_argument("--type", choices=("bug", "improvement"), default="bug")
    p.add_argument("--title", required=True)
    p.add_argument("--body-file", type=Path, required=True)
    p.add_argument("--create", action="store_true", help="run gh issue create")
    args = p.parse_args()
    body = scrub(args.body_file.read_text(encoding="utf-8"))
    title = scrub(args.title)
    print("--- draft ---")
    print("repo:", args.repo)
    print("title:", title)
    print(body)
    print("--- end ---")
    if not args.create:
        return 0
    try:
        subprocess.run(
            ["gh", "issue", "create", "--repo", args.repo, "--title", title, "--body", body],
            check=True,
        )
    except FileNotFoundError:
        print("gh not installed; paste draft manually", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as e:
        return e.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

