"""Generate the installed app-server schema and fail on required protocol drift."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from codex_app_mcp.client import AppServerClient  # noqa: E402

REQUIRED_METHODS = (
    "initialize",
    "model/list",
    "collaborationMode/list",
    "thread/start",
    "thread/list",
    "thread/read",
    "thread/resume",
    "thread/fork",
    "thread/archive",
    "thread/unsubscribe",
    "thread/name/set",
    "thread/goal/set",
    "thread/goal/get",
    "thread/goal/clear",
    "turn/start",
    "turn/steer",
    "turn/interrupt",
    "mcpServer/tool/call",
    "item/tool/call",
)
REQUIRED_GOAL_STATUSES = (
    "active",
    "paused",
    "blocked",
    "usageLimited",
    "budgetLimited",
    "complete",
)


def main() -> int:
    launch = AppServerClient(
        codex_bin=os.environ.get("CODEX_APP_MCP_BIN", "codex")
    )._build_argv()
    try:
        app_index = launch.index("app-server")
    except ValueError:
        print(json.dumps({"ok": False, "error": "APP_SERVER_ARG_NOT_FOUND"}))
        return 1
    base = launch[:app_index]
    with tempfile.TemporaryDirectory(prefix="codex-app-schema-") as raw_dir:
        out = Path(raw_dir)
        completed = subprocess.run(
            [
                *base,
                "app-server",
                "generate-json-schema",
                "--experimental",
                "--out",
                str(out),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
        )
        bundle = out / "codex_app_server_protocol.schemas.json"
        if completed.returncode != 0 or not bundle.is_file():
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": "SCHEMA_GENERATION_FAILED",
                        "returncode": completed.returncode,
                        "stderr": completed.stderr[-2000:],
                    },
                    ensure_ascii=False,
                )
            )
            return 1
        text = bundle.read_text(encoding="utf-8")
        missing_methods = [
            method for method in REQUIRED_METHODS if f'"{method}"' not in text
        ]
        missing_statuses = [
            status for status in REQUIRED_GOAL_STATUSES if f'"{status}"' not in text
        ]
        result = {
            "ok": not missing_methods and not missing_statuses,
            "binary": base,
            "requiredMethodCount": len(REQUIRED_METHODS),
            "missingMethods": missing_methods,
            "missingGoalStatuses": missing_statuses,
            "schemaBytes": bundle.stat().st_size,
        }
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
