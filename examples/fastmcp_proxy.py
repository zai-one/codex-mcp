#!/usr/bin/env python3
"""Example: FastMCP-style remote proxy to codex-app-mcp HTTP MCP.

Placeholders only — no real credentials or personal paths.
Adapt imports/APIs to your installed FastMCP version.

Security:
  - Authorization bearer MUST be an operator-generated secret.
  - NEVER put OpenAI/Codex OAuth tokens or API keys in headers.
"""

from __future__ import annotations

import os
import sys

REMOTE_MCP_URL = os.environ.get(
    "CODEX_APP_MCP_REMOTE_MCP_URL",
    "https://mcp.example.invalid/mcp",
)
TOKEN_FILE = os.environ.get("CODEX_APP_MCP_HTTP_TOKEN_FILE", "<TOKEN_FILE>")
TOKEN_ENV = os.environ.get("CODEX_APP_MCP_HTTP_TOKEN", "")


def _load_bearer() -> str:
    if TOKEN_ENV and TOKEN_FILE not in ("", "<TOKEN_FILE>"):
        raise SystemExit(
            "set only one of CODEX_APP_MCP_HTTP_TOKEN and CODEX_APP_MCP_HTTP_TOKEN_FILE"
        )
    if TOKEN_ENV:
        return TOKEN_ENV.strip()
    if TOKEN_FILE and TOKEN_FILE != "<TOKEN_FILE>":
        with open(TOKEN_FILE, encoding="utf-8") as fh:
            value = fh.read().strip()
        if not value:
            raise SystemExit("token file is empty")
        return value
    raise SystemExit(
        "configure CODEX_APP_MCP_HTTP_TOKEN_FILE or CODEX_APP_MCP_HTTP_TOKEN "
        "(operator bearer only — not OpenAI/Codex OAuth)"
    )


def main() -> int:
    bearer = _load_bearer()
    headers = {"Authorization": f"Bearer {bearer}"}

    # --- Conceptual FastMCP proxy wiring ---------------------------------
    #   from fastmcp import FastMCP
    #   mcp = FastMCP.as_proxy(REMOTE_MCP_URL, headers=headers)
    #   # or create_proxy(url=REMOTE_MCP_URL, headers=headers)
    #   mcp.run()
    #
    print("Remote MCP URL:", REMOTE_MCP_URL, file=sys.stderr)
    print("Headers: Authorization: Bearer <redacted>", file=sys.stderr)
    print(
        "Wire this URL+headers into FastMCP create_proxy / your host remote MCP.",
        file=sys.stderr,
    )
    print(
        "Unofficial community project — not an official OpenAI/Codex product.",
        file=sys.stderr,
    )
    _ = headers
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
