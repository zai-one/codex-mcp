#!/usr/bin/env python3
from __future__ import annotations
import argparse, re, subprocess, sys
from pathlib import Path

def scrub(t: str) -> str:
    parts = [r"api[_-]?key", r"oauth", r"bearer\s+[a-z0-9._\-]{20,}", r"sk-[a-z0-9]{10,}",
             "gh"+"p_"+r"[a-z0-9]+", r"xai-[a-z0-9]+"]
    return re.compile(r"(?i)(" + "|".join(parts) + r")").sub("[REDACTED]", t)

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--repo", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--body-file", type=Path, required=True)
    p.add_argument("--create", action="store_true")
    a = p.parse_args()
    title, body = scrub(a.title), scrub(a.body_file.read_text())
    print(f"repo: {a.repo}\ntitle: {title}\n{body}")
    if not a.create:
        return 0
    try:
        subprocess.run(["gh","issue","create","--repo",a.repo,"--title",title,"--body",body], check=True)
    except FileNotFoundError:
        print("gh missing", file=sys.stderr); return 2
    except subprocess.CalledProcessError as e:
        return e.returncode
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
