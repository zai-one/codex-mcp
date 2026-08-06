from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_verify_skills_script_passes() -> None:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "verify_skills.py")],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_router_skill_structure() -> None:
    skill = ROOT / "skills" / "codex-mcp"
    assert (skill / "SKILL.md").is_file()
    assert (skill / "references" / "brainstorm.md").is_file()
    text = (skill / "SKILL.md").read_text(encoding="utf-8")
    assert "codex login" in text


def test_easy_install_is_the_path() -> None:
    easy = (ROOT / "docs" / "EASY.md").read_text(encoding="utf-8")
    assert "install.sh" in easy
    assert "codex login" in easy
