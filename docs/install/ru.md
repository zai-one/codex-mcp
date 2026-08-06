# Установка и подключение (русский)
> **Быстрая установка:** `curl -fsSL https://raw.githubusercontent.com/zai-one/codex-mcp/main/scripts/install.sh | bash -s -- --project "$HOME/code/my-project"` — затем `codex login`. См. [EASY.md](../EASY.md).

> ### ⛔ Сначала CLI
>
> MCP **не работает** без **Codex CLI** и `codex login` на той же машине/пользователе. См. [START_HERE.md](../START_HERE.md) (EN). Скилл: `install-codex-mcp`.


Полное руководство по установке **codex-app-mcp**, запуску через stdio или
HTTP и подключению популярных MCP-хостов. Версия **0.5.0**.

Другие языки: [English](en.md) · [简体中文](zh-CN.md) · [Español](es.md)

## Что это за пакет

`codex-app-mcp` — **управляемый MCP-шлюз** для других агентов и IDE. Это
**не** замена самого Codex.

| Слой | Роль |
|---|---|
| **Codex CLI + app-server** | Бэкенд: модели, потоки, ходы, цели, песочница |
| **codex-app-mcp** | MCP-поверхность (stdio / HTTP с bearer) с политикой, задачами, lanes |
| **MCP-хост** | Claude Desktop, Claude Code, Cursor, VS Code, Continue, удалённые агенты |

Аутентификация к моделям — всегда **локальная сессия Codex CLI**
(`codex login` → `CODEX_HOME`). Шлюз не просит вставлять OpenAI API key
или ChatGPT OAuth в MCP JSON.

## Предварительные требования

1. **Python 3.10+** (`python3 --version` или `py -3 --version`)
2. Установленный **Codex CLI** в `PATH` (или переменная `CODEX_APP_MCP_BIN`)
3. **Аутентифицированная сессия Codex**: выполните `codex login` на той же
   машине (и под тем же пользователем), где будет работать шлюз
4. Клон этого репозитория (или установленный wheel пакета)

Проверка Codex:

```bash
codex --version
codex login
```

## Установка

Из корня репозитория:

```bash
cd <path-to-repository>
python -m pip install -e ".[test]"
```

Windows (PowerShell):

```powershell
Set-Location "<path-to-repository>"
py -3 -m pip install -e ".[test]"
```

Устанавливаются точка входа `codex-app-mcp` и модуль `codex_app_mcp`.

## Корни проектов (обязательно для работы с путями)

Корни **закрыты по умолчанию** (fail-closed). Пока не задан allowlist,
операции с `cwd` / `repoRoot` отклоняются.

```bash
export CODEX_APP_MCP_ALLOWED_ROOTS="/path/to/allowed/project;/path/to/other/project"
```

PowerShell:

```powershell
$env:CODEX_APP_MCP_ALLOWED_ROOTS = "D:\Projects\example;D:\Work\example"
```

Необязательный профиль полного доступа (только доверенная локальная машина):

```bash
export CODEX_APP_MCP_ALLOW_FULL_ACCESS=1
export CODEX_APP_MCP_DEFAULT_SANDBOX=danger-full-access
export CODEX_APP_MCP_DEFAULT_APPROVAL_POLICY=never
```

## Запуск: stdio MCP

Транспорт по умолчанию — stdio (его порождают большинство desktop MCP-клиентов):

```bash
codex-app-mcp
# эквивалент:
python -m codex_app_mcp
```

Процесс говорит MCP JSON-RPC на stdin/stdout. Не смешивайте протокольные
байты с интерактивными оболочками.

## Запуск: HTTP MCP (bearer)

Сгенерируйте **локальный операторский** секрет (никогда не OpenAI/Codex OAuth):

```bash
export CODEX_APP_MCP_HTTP_TOKEN="$(openssl rand -hex 32)"
codex-app-mcp --transport http --host 127.0.0.1 --port 8765
```

Для сервисов предпочтителен файл с токеном (взаимоисключающе с env-значением):

```bash
export CODEX_APP_MCP_HTTP_TOKEN_FILE="/path/to/codex-app-mcp.token"
codex-app-mcp --transport http --host 127.0.0.1 --port 8765
```

Эндпоинты:

| Метод | Путь | Auth | Назначение |
|---|---|---|---|
| `GET` | `/healthz` | нет | живость процесса |
| `GET` | `/readyz` | bearer | старт/проверка app-server |
| `POST` | `/mcp` | bearer | MCP JSON-RPC |
| `GET` | `/mcp` | — | не поддерживается (нет SSE-потока) |

По умолчанию bind **127.0.0.1**. Не-loopback требует токен; удалённый доступ
— только через TLS reverse proxy или SSH/VPN.

Пример вызова:

```bash
curl -sS \
  -H "Authorization: Bearer <long-random-secret>" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}' \
  http://127.0.0.1:8765/mcp
```

## Claude Desktop

Типичные пути конфигурации:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`
- Linux: `~/.config/Claude/claude_desktop_config.json`

Пример (только плейсхолдеры) — также
[`examples/claude_desktop.mcp.json`](../../examples/claude_desktop.mcp.json):

```json
{
  "mcpServers": {
    "codex-app": {
      "command": "codex-app-mcp",
      "args": [],
      "cwd": "<path-to-repository>",
      "env": {
        "CODEX_APP_MCP_ALLOWED_ROOTS": "/path/to/allowed/project",
        "CODEX_APP_MCP_ALLOW_FULL_ACCESS": "0"
      }
    }
  }
}
```

Если `codex-app-mcp` нет в `PATH`:

```json
{
  "mcpServers": {
    "codex-app": {
      "command": "<path-to-python>",
      "args": ["-m", "codex_app_mcp"],
      "cwd": "<path-to-repository>",
      "env": {
        "CODEX_APP_MCP_ALLOWED_ROOTS": "/path/to/allowed/project"
      }
    }
  }
}
```

Перезапустите Claude Desktop. Убедитесь, что видны инструменты вроде
`codex_app_status`.

## Claude Code (`.mcp.json`)

Конфиг проекта или пользователя — см.
[`examples/claude-code.mcp.json`](../../examples/claude-code.mcp.json):

```json
{
  "mcpServers": {
    "codex-app": {
      "type": "stdio",
      "command": "codex-app-mcp",
      "args": [],
      "env": {
        "CODEX_APP_MCP_ALLOWED_ROOTS": "/path/to/allowed/project"
      }
    }
  }
}
```

Положите как `.mcp.json` в корень проекта или в настройки Claude Code MCP.
Процесс должен идти от пользователя с `codex login`.

## Cursor (`mcp.json`)

Конфигурация Cursor — пример
[`examples/cursor.mcp.json`](../../examples/cursor.mcp.json):

```json
{
  "mcpServers": {
    "codex-app": {
      "command": "codex-app-mcp",
      "args": [],
      "env": {
        "CODEX_APP_MCP_ALLOWED_ROOTS": "/path/to/allowed/project"
      }
    }
  }
}
```

HTTP (если сборка Cursor поддерживает URL MCP):

```json
{
  "mcpServers": {
    "codex-app-http": {
      "url": "http://127.0.0.1:8765/mcp",
      "headers": {
        "Authorization": "Bearer <long-random-secret>"
      }
    }
  }
}
```

В `headers` — только локальный HTTP bearer шлюза, не OAuth OpenAI/Codex.

## VS Code / Continue

### VS Code

Используйте настройки MCP серверов редактора. Форма stdio:

```json
{
  "servers": {
    "codex-app": {
      "type": "stdio",
      "command": "codex-app-mcp",
      "args": [],
      "env": {
        "CODEX_APP_MCP_ALLOWED_ROOTS": "/path/to/allowed/project"
      }
    }
  }
}
```

### Continue

В конфиге Continue зарегистрируйте stdio MCP-сервер с теми же `command` /
`args` / `env`. Рабочие каталоги — только из allowlist корней.

## ChatGPT (web) и удалённые агенты

**Веб-ChatGPT не хостит локальный stdio MCP** так, как Claude Desktop или
Cursor. Локальный `codex-app-mcp` на ноутбуке не появляется автоматически на
chatgpt.com.

Если продукт поддерживает **удалённые MCP-коннекторы**:

1. Запустите шлюз на хосте с `codex login`.
2. Откройте **только** HTTP MCP через **TLS reverse proxy** или туннель
   (SSH, VPN).
3. Защитите bearer (`CODEX_APP_MCP_HTTP_TOKEN` или `TOKEN_FILE`).
4. **Никогда** не кладите OpenAI API keys или Codex/ChatGPT OAuth в HTTP
   token. Они остаются на сервере как локальная CLI-сессия.
5. Держите `ALLOWED_ROOTS` и unsafe/full-access жёсткими на удалённом хосте.

Любой коннектор ChatGPT (если доступен) должен указывать на **удалённый
HTTPS**-эндпоинт под вашим контролем.

## Первая проверка

```bash
python -m pytest -q
python scripts/probe_stdio.py
python scripts/probe_http.py
```

В MCP-клиенте вызовите **`codex_app_status`** (и при необходимости
`codex_app_doctor`). При ошибке сначала проверьте `codex login` и
`CODEX_APP_MCP_BIN`.

Дополнительно:

```bash
python scripts/check_protocol.py
python scripts/audit_protocol.py
python scripts/probe_full.py
```

## Переменные окружения

Значения — **значения по умолчанию или плейсхолдеры**. Секреты не коммитить.
См. [`.env.example`](../../.env.example) и
[`examples/http.env.example`](../../examples/http.env.example).

| Переменная | По умолчанию | Описание |
|---|---|---|
| `CODEX_APP_MCP_BIN` | `codex` | Бинарник Codex с `app-server` |
| `CODEX_HOME` | домашний Codex | Auth, конфиг, потоки app-server |
| `CODEX_APP_MCP_ALLOWED_ROOTS` | пусто | `;` или JSON-массив корней; пусто = fail-closed |
| `CODEX_APP_MCP_ALLOW_FULL_ACCESS` | `0` | Разрешить `danger-full-access` и `process/*` |
| `CODEX_APP_MCP_DEFAULT_SANDBOX` | не задано | `read-only` / `workspace-write` / `danger-full-access` |
| `CODEX_APP_MCP_DEFAULT_APPROVAL_POLICY` | не задано | `untrusted` / `on-request` / `never` |
| `CODEX_APP_MCP_ALLOW_UNSAFE_RPC` | `0` | Гейт stateful admin / raw mutations |
| `CODEX_APP_MCP_ALLOWED_RPC_METHODS` | пусто | `;` allowlist или `*` (только доверенный local) |
| `CODEX_APP_MCP_ALLOWED_SERVERS` | пусто | Allowlist downstream MCP-серверов |
| `CODEX_APP_MCP_ALLOWED_TOOLS` | пусто | Allowlist tools или `server/tool` |
| `CODEX_APP_MCP_ALLOWED_CONFIG_KEYS` | пусто | Не-security ключи thread config |
| `CODEX_APP_MCP_CONFIG_OVERRIDES` | пусто | Override запуска app-server |
| `CODEX_APP_MCP_REQUEST_TIMEOUT_SECONDS` | `30` | Таймаут обычного RPC |
| `CODEX_APP_MCP_OPERATION_TIMEOUT_SECONDS` | `180` | Таймаут длинных операций |
| `CODEX_APP_MCP_ACTION_TIMEOUT_SECONDS` | `60` | Таймаут pending server-request |
| `CODEX_APP_MCP_STATE_PATH` | под `CODEX_HOME` | SQLite durable jobs |
| `CODEX_APP_MCP_SCHEDULER_ENABLED` | `1` | Автозапуск расписаний |
| `CODEX_APP_MCP_SCHEDULER_POLL_SECONDS` | `1` | Интервал опроса scheduler |
| `CODEX_APP_MCP_PROTOCOL_CACHE_SECONDS` | `300` | TTL кэша exact schema |
| `CODEX_APP_MCP_AUDIT` | `1` | Безопасный audit on/off |
| `CODEX_APP_MCP_AUDIT_PATH` | stderr | Опциональный JSONL audit-файл |
| `CODEX_APP_MCP_PRINCIPAL` | `local` | Метка principal в audit |
| `CODEX_APP_MCP_TRANSPORT` | `stdio` | `stdio` или `http` |
| `CODEX_APP_MCP_HTTP_HOST` | `127.0.0.1` | Адрес bind HTTP |
| `CODEX_APP_MCP_HTTP_PORT` | `8765` | Порт HTTP |
| `CODEX_APP_MCP_HTTP_TOKEN` | пусто | Bearer-секрет; **не** OpenAI OAuth |
| `CODEX_APP_MCP_HTTP_TOKEN_FILE` | пусто | Файл с bearer; exclusive с token env |
| `CODEX_APP_MCP_HTTP_MAX_INFLIGHT` | `16` | Параллельные HTTP MCP (1–256) |
| `CODEX_APP_MCP_HTTP_ALLOWED_ORIGINS` | пусто | `;` список точных Origin |
| `CODEX_APP_MCP_LANES_PARENT` | не задано | Родительский каталог worktree lanes |

## Почему нет WebSocket

Официальный **WebSocket** listener app-server помечен как experimental /
unsupported. Пакет намеренно даёт:

- **stdio** для локальных MCP-хостов;
- **HTTP** (`POST /mcp` с bearer) для удалённых control plane.

Поддерживаемого WebSocket MCP-транспорта нет; нет и unsolicited SSE на
`GET /mcp`. События — через инструмент `codex_app_events`.

## Чеклист безопасности

- [ ] В репозитории и примерах **нет** API keys, OAuth и личных путей
- [ ] На хосте выполнен `codex login`; шлюз использует только локальный `CODEX_HOME`
- [ ] Задан `CODEX_APP_MCP_ALLOWED_ROOTS` (или осознанный fail-closed)
- [ ] `ALLOW_FULL_ACCESS` / `ALLOW_UNSAFE_RPC` выключены, если не нужны
- [ ] HTTP на `127.0.0.1` с CSPRNG bearer, либо TLS proxy + bearer
- [ ] `HTTP_TOKEN` / `TOKEN_FILE` не закоммичены
- [ ] HTTP bearer — **не** учётные данные OpenAI/Codex OAuth
- [ ] Отдельные token, `CODEX_HOME` и SQLite на tenant/service
- [ ] Audit и state защищены ACL

Полная политика: [SECURITY.md](../../SECURITY.md).

## Дальше

- [Справочник](../REFERENCE.md)
- [Верификация](../VERIFICATION.md)
- [Миграция](../MIGRATION.md)
- [Contributing](../../CONTRIBUTING.md)
- [Security](../../SECURITY.md)


---

## Отказ от ответственности (неофициальный продукт)

> **Сообщественный проект.** Это **не** официальный продукт **OpenAI**, **Codex**,
> Anthropic, xAI или Grok. Аутентификация — только **локальный `codex login`**
> (`CODEX_HOME`). **Никогда** не кладите OpenAI/Codex OAuth или API keys в
> MCP-конфиг или в HTTP bearer.

---

## Token economy

Хост-агент оркестрирует короткими вызовами; **Codex app-server** ведёт длинный
цикл под token budget (локально или VPS).

| Переменная | Назначение |
|---|---|
| `CODEX_APP_MCP_ECONOMY=1` | Режим economy / playbook |

Инструмент: **`codex_app_economy`**. Цель: `codex_app_goal` + `tokenBudget`
(16k–40k). Полный гид: [../economy.md](../economy.md) (EN).

---

## FastMCP

| Путь | Как |
|---|---|
| Локальный stdio | `codex-app-mcp` / `python -m codex_app_mcp` |
| Удалённый proxy | Нативный HTTP + TLS; FastMCP `create_proxy` + bearer |

[fastmcp.md](fastmcp.md) · [../../examples/fastmcp_proxy.py](../../examples/fastmcp_proxy.py)

---

## VPS (HTTP уже встроен)

```bash
export CODEX_APP_MCP_ALLOWED_ROOTS="<PROJECT_ROOT>"
export CODEX_APP_MCP_HTTP_TOKEN_FILE="<TOKEN_FILE>"
codex-app-mcp --transport http --host 127.0.0.1 --port 8765
```

Bearer — операторский секрет, **не** OpenAI OAuth.  
[vps.md](vps.md) · [../../examples/vps.systemd.service](../../examples/vps.systemd.service) ·
[../../examples/http.env.example](../../examples/http.env.example)

---

## Переменные economy / HTTP

| Переменная | Описание |
|---|---|
| `CODEX_APP_MCP_ECONOMY` | Включает economy playbook |
| `CODEX_APP_MCP_HTTP_TOKEN` | Bearer в env |
| `CODEX_APP_MCP_HTTP_TOKEN_FILE` | Путь к bearer (`<TOKEN_FILE>`) |
| `CODEX_APP_MCP_HTTP_HOST` / `PORT` | По умолчанию `127.0.0.1:8765` |
