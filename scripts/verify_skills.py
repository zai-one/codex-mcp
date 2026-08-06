#!/usr/bin/env python3
"""Validate router skill tree + mirrors (no network)."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = "codex-mcp"
HOST_DIRS = [
    ROOT / "skills",
    ROOT / ".claude" / "skills",
    ROOT / ".codex" / "skills",
    ROOT / ".agents" / "skills",
]
REQUIRED_REL = [
    "SKILL.md",
    "references/modes.md",
    "references/install.md",
    "references/operate.md",
    "references/update.md",
    "references/executor.md",
    "references/verifier.md",
    "references/brainstorm.md",
    "references/feedback-issues.md",
    "references/security.md",
    "scripts/check_ready.sh",
    "scripts/update_mcp.sh",
    "scripts/draft_issue.py",
    "templates/goal-brief.md",
    "templates/receipt-short.md",
    "templates/issue-bug.md",
    "templates/issue-improvement.md",
    "assets/flow-modes.md",
]
FRONT = re.compile(r"^---\n(.*?)\n---\n", re.S)
BANNED = ("sk-live", "sk-proj-", "BEGIN RSA", "ghp_", "alexzascherinsky@")


def main() -> int:
    errors: list[str] = []
    canonical = ROOT / "skills" / SKILL
    if not canonical.is_dir():
        print("SKILL VERIFY FAIL\n - missing skills/codex-mcp")
        return 1
    for rel in REQUIRED_REL:
        if not (canonical / rel).is_file():
            errors.append(f"missing {rel}")
    text = (canonical / "SKILL.md").read_text(encoding="utf-8")
    m = FRONT.match(text)
    if not m:
        errors.append("no frontmatter")
    else:
        fm = m.group(1)
        if re.search(rf"^name:\s*{re.escape(SKILL)}\s*$", fm, re.M) is None:
            errors.append("name mismatch")
        if "description:" not in fm:
            errors.append("missing description")
    body = text.lower()
    for needle in ("unofficial", "codex login", "executor", "verifier", "brainstorm", "references/"):
        if needle not in body:
            errors.append(f"SKILL.md missing {needle!r}")
    for base in HOST_DIRS:
        skill_dir = base / SKILL
        if not skill_dir.is_dir():
            errors.append(f"missing mirror {skill_dir.relative_to(ROOT)}")
            continue
        for rel in REQUIRED_REL:
            a = canonical / rel
            b = skill_dir / rel
            if not b.is_file():
                errors.append(f"mirror missing {b.relative_to(ROOT)}")
            elif a.is_file() and b.read_bytes() != a.read_bytes():
                errors.append(f"mirror drift {b.relative_to(ROOT)}")
        for child in base.iterdir():
            if child.is_dir() and child.name != SKILL:
                errors.append(f"extra skill dir (collapse old): {child.relative_to(ROOT)}")
    blob = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in canonical.rglob("*") if p.is_file())
    for banned in BANNED:
        if banned.lower() in blob.lower():
            errors.append(f"banned pattern {banned}")
    if errors:
        print("SKILL VERIFY FAIL")
        for e in errors:
            print(" -", e)
        return 1
    print("SKILL VERIFY PASS")
    print(f"  {SKILL}: {len(HOST_DIRS)} mirrors, {len(REQUIRED_REL)} paths")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
