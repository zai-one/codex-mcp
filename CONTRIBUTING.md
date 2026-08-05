# Contributing

Thanks for improving `codex-app-mcp`. This document covers local development,
tests, probes, and pull requests.

## Prerequisites

- Python **3.10+**
- Official **Codex CLI** installed and available as `codex` (or set
  `CODEX_APP_MCP_BIN`)
- For live probes: authenticated local session via `codex login`
- Git

## Setup

```bash
cd <path-to-repository>
python -m pip install -e ".[test]"
```

Optional: copy non-secret defaults for local experiments:

```bash
cp .env.example .env
# Edit .env locally; never commit secrets
```

## Code layout

| Path | Role |
|---|---|
| `codex_app_mcp/` | Runtime package (gateway, policy, HTTP, MCP tools) |
| `tests/` | Automated unit and transport tests |
| `scripts/` | Protocol and live transport probes |
| `docs/` | Reference, verification, install guides |
| `examples/` | Placeholder MCP client configs |

## Tests

Run the automated suite (no external model usage required for the default
tests):

```bash
python -m pytest -q
```

Also keep the package importable and free of syntax errors:

```bash
python -m compileall -q codex_app_mcp scripts tests
```

If you change MCP tool schemas, policy gates, HTTP auth, or protocol
dispatch, add or update tests under `tests/test_codex_app_mcp.py`.

## Protocol checks

Schema and method coverage against the configured `codex` binary:

```bash
python scripts/check_protocol.py
python scripts/audit_protocol.py
```

These commands require a working Codex CLI that supports
`generate-json-schema --experimental`. Run them after upgrading Codex.

## Live probes

Probes exercise the gateway against a real app-server when available.

```bash
# Stdio MCP smoke
python scripts/probe_stdio.py

# HTTP bearer smoke (starts a temporary loopback server)
python scripts/probe_http.py

# Full matrix (mutates only temporary repositories it creates)
python scripts/probe_full.py

# Optional persisted-goal probe (may consume model usage)
python scripts/probe_goal.py --goal
```

Do not point probes at production repositories or shared tenant
`CODEX_HOME` directories. Prefer disposable roots and a local session.

## Coding guidelines

- Prefer fail-closed policy: empty roots deny path work; full access and
  unsafe RPC stay opt-in.
- Never log prompts, responses, patches, tokens, or environment secrets in
  audit output.
- Keep examples and docs free of real paths, emails, usernames, and tokens.
- Do not reintroduce the removed `codex exec` / `codex_delegate` runtime.
- Match existing style (type hints, small focused modules, pytest).

## Pull request process

1. **Branch** from the default branch with a short descriptive name.
2. **Change** only what the PR needs; avoid drive-by refactors.
3. **Verify** locally:
   - `python -m pytest -q`
   - `python -m compileall -q codex_app_mcp scripts tests`
   - relevant probes if you touch transport or live integration
4. **Document** user-visible changes in `README.md` and/or `docs/` when
   behavior or env vars change. Keep install i18n in sync when you edit
   `docs/install/en.md`.
5. **Security**: confirm no secrets, token files, or personal
   `CODEX_HOME` content are included.
6. Open a PR with:
   - summary of the change and motivation
   - test/probe commands run
   - notes on Codex CLI versions if protocol-related
7. Address review feedback; keep history readable (rebase or squash as the
   maintainers prefer).

## Versioning

Package version is declared in `pyproject.toml` and
`codex_app_mcp/__init__.py` (currently **0.4.0**). Bump both together only
when maintainers intend a release.

## Reporting bugs

Use the public issue tracker for non-security bugs. For vulnerabilities,
follow [SECURITY.md](SECURITY.md). Attach logs only after redacting
prompts, tokens, and private paths.

## License and conduct

Contribute under the same terms as the repository. Be respectful in issues
and reviews; focus on technical merit and clear reproduction steps.
