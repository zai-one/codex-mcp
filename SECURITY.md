# Security policy

## Overview

`codex-app-mcp` is a local MCP gateway for the Codex **app-server** process.
It does **not** implement OpenAI or Codex OAuth, and it does **not** store
API keys for model access. Authentication to Codex is always the local CLI
session created by `codex login`.

## Secrets that must never appear in the repository

Do **not** commit any of the following:

- OpenAI API keys or organization secrets
- Codex / ChatGPT OAuth tokens, refresh tokens, or cookie jars
- HTTP bearer tokens for this gateway
- Paths or contents of a personal `CODEX_HOME` auth store
- Private project roots, hostnames, emails, or personal usernames in examples

Use placeholders only in docs and examples (for example
`<path-to-repository>`, `<long-random-secret>`, `/path/to/allowed/project`).

## How Codex authentication works

1. Install the official `codex` CLI.
2. Authenticate once with `codex login`.
3. Codex stores the session under the local `CODEX_HOME` directory
   (default: the user's home Codex directory, typically `~/.codex`).
4. This gateway starts `codex app-server` as a child process and inherits that
   local session. It never asks you to paste an OpenAI key into MCP config.

If `codex login` has not been completed, tools such as `codex_app_status` and
`codex_app_doctor` will report an unready backend; fix auth on the host, not
by adding tokens to this repository.

## HTTP bearer token (operator-generated local secret)

When the gateway runs with `--transport http`, callers authenticate with a
**local operator secret**, not with OpenAI credentials:

| Variable | Purpose |
|---|---|
| `CODEX_APP_MCP_HTTP_TOKEN` | Bearer token value in the process environment |
| `CODEX_APP_MCP_HTTP_TOKEN_FILE` | Path to a file whose contents are the bearer token |

Rules:

- Generate the secret yourself (for example `openssl rand -hex 32`).
- Prefer `CODEX_APP_MCP_HTTP_TOKEN_FILE` for services so the secret is not
  visible in process listings or shell history.
- Set **only one** of the two variables; setting both is an error.
- Default bind is `127.0.0.1`. Non-loopback binds are refused without a token.
- Put any non-local deployment behind TLS (reverse proxy or tunnel).
- **Never** put OpenAI, Codex, or ChatGPT OAuth material into the HTTP token
  variables. The HTTP bearer only protects the MCP HTTP surface of *this*
  process.

Example (placeholders only):

```bash
export CODEX_APP_MCP_HTTP_TOKEN="$(openssl rand -hex 32)"
codex-app-mcp --transport http --host 127.0.0.1 --port 8765
```

Or with a token file:

```bash
export CODEX_APP_MCP_HTTP_TOKEN_FILE="/path/to/codex-app-mcp.token"
# File contents: a single line with the random secret (never committed)
codex-app-mcp --transport http --host 127.0.0.1 --port 8765
```

## Project roots fail closed

Filesystem and project `cwd` / `repoRoot` operations fail closed until roots
are configured:

```bash
export CODEX_APP_MCP_ALLOWED_ROOTS="/path/to/allowed/project;/path/to/other/project"
```

With an empty allowlist, project path operations are rejected
(`ALLOWED_ROOTS_EMPTY`). Full-host and high-risk capabilities require
explicit opt-in:

```bash
export CODEX_APP_MCP_ALLOW_FULL_ACCESS=1
export CODEX_APP_MCP_DEFAULT_SANDBOX=danger-full-access
export CODEX_APP_MCP_DEFAULT_APPROVAL_POLICY=never
```

Stateful administrative and raw RPC mutations use a **separate** gate:

```bash
export CODEX_APP_MCP_ALLOW_UNSAFE_RPC=1
export CODEX_APP_MCP_ALLOWED_RPC_METHODS="thread/delete;config/value/write"
```

Prefer a narrow method allowlist. Use `CODEX_APP_MCP_ALLOWED_RPC_METHODS=*`
only inside a trusted local control plane.

## Downstream MCP / SaaS allowlists

Calls through `codex_app_mcp_call` require both server and tool allowlists:

```bash
export CODEX_APP_MCP_ALLOWED_SERVERS="example-server"
export CODEX_APP_MCP_ALLOWED_TOOLS="example-server/example_tool"
```

Which servers exist is determined by the configured `CODEX_HOME`, not by
this package shipping credentials.

## Audit and data handling

- Audit JSONL records scalar metadata only (tool, action, outcome, IDs,
  lane, sandbox, cwd, duration, goal length/hash).
- Prompts, responses, patches, commands, environment variables, tokens, and
  credentials are never written to audit output.
- Durable job state in SQLite may contain prompts and operator text; protect
  the state path and service account with host ACLs.

## Recommended operator checklist

- [ ] No API keys, OAuth tokens, or bearer secrets in git
- [ ] `codex login` completed on the host that runs the gateway
- [ ] `CODEX_APP_MCP_ALLOWED_ROOTS` set to the intended project trees
- [ ] Full access and unsafe RPC left off unless deliberately required
- [ ] HTTP bound to loopback unless a TLS reverse proxy is in place
- [ ] HTTP bearer generated with a CSPRNG and stored outside the repo
- [ ] Separate `CODEX_HOME`, token, and SQLite state per tenant/service
- [ ] Audit path and state database readable only by the service account

## Reporting security issues

If you believe you have found a vulnerability in this gateway:

1. **Do not** open a public issue that includes exploit details, tokens, or
   private paths.
2. Report privately through the repository host's security contact or
   private vulnerability reporting channel (for example the host's
   "Report a vulnerability" / security advisory flow for this repository).
3. Include: affected version (see `pyproject.toml` / package `0.4.0`),
   environment (OS, `codex` CLI version), reproduction steps, and impact.
4. Allow a reasonable time for assessment before public disclosure.

Issues that are ordinary bugs, documentation gaps, or feature requests may
use the normal public issue tracker **without** secrets attached.

## Out of scope

- Security of the upstream `codex` CLI, app-server protocol, or OpenAI
  services (report those to the respective maintainers).
- Misconfiguration of host firewalls, reverse proxies, or MCP client
  machines outside this package.

## Token economy & remote (VPS)

- Economy mode shrinks host-agent payloads (compact polls / playbooks). It does
  **not** relax auth or root policy.
- HTTP bearer tokens are **operator-generated secrets**, never OAuth/session
  material from Grok or Codex CLI.
- Prefer TLS reverse proxy for any non-loopback exposure. Treat the bearer as
  single-tenant and rotate it if leaked.
- This project is **unofficial** and not affiliated with xAI, Grok, OpenAI,
  Codex, or Anthropic.

