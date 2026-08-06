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


def test_start_here_cli_gate() -> None:
    text = (ROOT / "docs" / "START_HERE.md").read_text(encoding="utf-8")
    assert "codex login" in text
    assert "does nothing useful until" in text.lower() or "ALL of these are true" in text
