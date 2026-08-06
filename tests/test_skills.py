from __future__ import annotations
import subprocess, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]

def test_verify_skills_script_passes() -> None:
    proc = subprocess.run([sys.executable, str(ROOT/"scripts"/"verify_skills.py")], capture_output=True, text=True, cwd=ROOT)
    assert proc.returncode == 0, proc.stdout + proc.stderr

def test_router_skill_structure() -> None:
    skill = ROOT/"skills"/"codex-mcp"
    assert (skill/"references"/"tools.md").is_file()
    assert (skill/"references"/"hosts.md").is_file()
    text = (skill/"SKILL.md").read_text()
    assert "codex login" in text
    assert "token budget" in text.lower()
    assert len(text.split("---", 2)[-1].split()) < 280

def test_easy_install_is_the_path() -> None:
    easy = (ROOT/"docs"/"EASY.md").read_text()
    assert "install.sh" in easy
    assert "codex login" in easy
