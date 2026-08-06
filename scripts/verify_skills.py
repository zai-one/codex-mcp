#!/usr/bin/env python3
"""Validate router skill tree + mirrors (no network)."""
from __future__ import annotations
import re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = "codex-mcp"
HOST_DIRS = [ROOT/"skills", ROOT/".claude"/"skills", ROOT/".codex"/"skills", ROOT/".agents"/"skills"]
REQUIRED_REL = ['SKILL.md', 'references/operate.md', 'references/install.md', 'references/update.md', 'references/execute.md', 'references/verify.md', 'references/brainstorm.md', 'references/feedback.md', 'references/security.md', 'references/tools.md', 'references/hosts.md', 'scripts/check_ready.sh', 'scripts/update_mcp.sh', 'scripts/draft_issue.py', 'templates/goal-brief.md', 'templates/receipt.md', 'templates/issue.md', 'assets/flow.md']
FRONT = re.compile(r"^---\n(.*?)\n---\n", re.S)
BANNED = ("sk-live", "sk-proj-", "BEGIN RSA", "alexzascherinsky@")
NEEDLES = ['unofficial', 'codex login', 'token budget', 'references/']

def main() -> int:
    errors = []
    can = ROOT/"skills"/SKILL
    if not can.is_dir():
        print("SKILL VERIFY FAIL"); print(" - missing", can); return 1
    for rel in REQUIRED_REL:
        if not (can/rel).is_file():
            errors.append(f"missing {rel}")
    text = (can/"SKILL.md").read_text()
    m = FRONT.match(text)
    if not m:
        errors.append("no frontmatter")
    else:
        if re.search(rf"^name:\s*{re.escape(SKILL)}\s*$", m.group(1), re.M) is None:
            errors.append("name mismatch")
        if "description:" not in m.group(1):
            errors.append("no description")
    body = text.lower()
    for n in NEEDLES:
        if n not in body:
            errors.append(f"SKILL missing {n!r}")
    body_words = len(text.split("---", 2)[-1].split())
    if body_words > 200:
        errors.append(f"SKILL body too large ({body_words} > 280)")
    for base in HOST_DIRS:
        sd = base/SKILL
        if not sd.is_dir():
            errors.append(f"missing mirror {sd.relative_to(ROOT)}")
            continue
        for rel in REQUIRED_REL:
            a, b = can/rel, sd/rel
            if not b.is_file():
                errors.append(f"mirror missing {b.relative_to(ROOT)}")
            elif a.read_bytes() != b.read_bytes():
                errors.append(f"drift {b.relative_to(ROOT)}")
        for child in base.iterdir():
            if child.is_dir() and child.name != SKILL:
                errors.append(f"extra skill {child.relative_to(ROOT)}")
    blob = "\n".join(p.read_text(errors="replace") for p in can.rglob("*") if p.is_file())
    for b in BANNED:
        if b.lower() in blob.lower():
            errors.append(f"banned {b}")
    if "gh" + "p_" in blob:
        errors.append("banned token prefix")
    if errors:
        print("SKILL VERIFY FAIL")
        for e in errors:
            print(" -", e)
        return 1
    print("SKILL VERIFY PASS")
    print(f"  {SKILL}: {len(HOST_DIRS)} mirrors, body_words={body_words}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
