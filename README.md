# Codex app-server MCP

Управляемый MCP/HTTP gateway для полного управления Codex через
`codex app-server`. Старый runtime на `codex exec` удалён: все рабочие
операции теперь выполняются через persistent app-server connection.

Важно: установленный бинарник `codex` остаётся обязательным. Gateway запускает
`codex app-server`; удалён только прежний пакет `codex_delegate`.

## Возможности

- persistent threads, turns, steering, interrupt, fork, archive и rollback;
- persisted autonomous goals с token budget и terminal statuses;
- live model catalog и выбор reasoning effort;
- native review, shell commands, command sessions и filesystem v2;
- события, approvals, user input, dynamic tools и server requests;
- durable background jobs в SQLite;
- timezone-aware RRULE schedules с idempotency, retry и misfire policy;
- изолированные git worktree lanes вместо `codex exec`;
- динамический каталог exact schema конкретного бинарника;
- typed account/config/plugin/environment/search/memory/realtime/remote control;
- downstream MCP/SaaS tools через server/tool allowlists;
- полный forward-compatible app-server RPC с operator allowlist;
- stdio MCP и bearer-защищённый Streamable HTTP;
- app-server doctor и secret-safe JSONL audit.
- overload backoff, runtime metrics и явный restart app-server.

Полный контракт, конфигурация и примеры:
[codex_app_mcp/README.md](codex_app_mcp/README.md).

Матрица переноса старого API:
[APP-SERVER-MCP-MIGRATION.md](APP-SERVER-MCP-MIGRATION.md).

Исследование и доказательства:

- [APP-SERVER-MCP-RESEARCH.md](APP-SERVER-MCP-RESEARCH.md)
- [APP-SERVER-MCP-VERIFICATION.md](APP-SERVER-MCP-VERIFICATION.md)

## Установка и запуск

```powershell
cd "D:\ZAI\MCP\Codex CLI"
py -3 -m pip install -e ".[test]"

$env:CODEX_APP_MCP_ALLOWED_ROOTS = "D:\Projects;D:\Work"
$env:CODEX_APP_MCP_ALLOW_FULL_ACCESS = "1"
$env:CODEX_APP_MCP_DEFAULT_SANDBOX = "danger-full-access"
$env:CODEX_APP_MCP_DEFAULT_APPROVAL_POLICY = "never"

# MCP stdio
py -3 -m codex_app_mcp

# HTTP service
$env:CODEX_APP_MCP_HTTP_TOKEN = "<long-random-secret>"
py -3 -m codex_app_mcp --transport http --host 127.0.0.1 --port 8765
```

`danger-full-access` задаётся либо как default выше, либо полем
`"sandbox": "danger-full-access"` в `thread`, `turn`, `lane` и `command`.
Gateway никогда не включает его скрыто: требуется
`CODEX_APP_MCP_ALLOW_FULL_ACCESS=1`.

Для stateful/raw RPC и мутирующих filesystem операций отдельно требуется:

```powershell
$env:CODEX_APP_MCP_ALLOW_UNSAFE_RPC = "1"
$env:CODEX_APP_MCP_ALLOWED_RPC_METHODS = "*"
```

`*` удобно для доверенного локального control plane. Для внешнего SaaS лучше
перечислить методы явно.

## Проверка

```powershell
py -3 -m pytest tests -q
py -3 -m compileall -q codex_app_mcp scripts
py -3 scripts\check_app_server_protocol.py
py -3 scripts\audit_app_server_coverage.py
py -3 scripts\codex_app_mcp_stdio_probe.py
py -3 scripts\codex_app_mcp_http_probe.py
```

Полный live probe с реальными записью, turn, shell, review и lane:

```powershell
$env:CODEX_APP_MCP_ALLOW_FULL_ACCESS = "1"
$env:CODEX_APP_MCP_ALLOW_UNSAFE_RPC = "1"
py -3 scripts\codex_app_mcp_full_live_probe.py
```
