#!/usr/bin/env python3
"""Copy skills/codex-mcp → .claude/.codex/.agents mirrors."""
from pathlib import Path
import shutil
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "skills" / "codex-mcp"
for host in (".claude", ".codex", ".agents"):
    dest = ROOT / host / "skills" / "codex-mcp"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(SRC, dest)
    print("synced", dest.relative_to(ROOT))
