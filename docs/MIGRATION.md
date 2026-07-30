# Миграция на Codex app-server

Срез: 29 июля 2026 года. Gateway `0.4.0`, проверенный runtime
`codex-cli 0.146.0`.

## Итог

Пакет `codex_delegate`, вызывавший `codex exec`, удалён. Его девять операций
перенесены на persistent `codex app-server`:

| Старый MCP tool | Замена | Паритет и расширение |
|---|---|---|
| `codex_delegate` | `codex_app_lane action=run` | worktree, model/effort, output schema, diff; плюс threadId/jobId/events |
| `codex_delegate_plan` | `codex_app_lane action=run, planOnly=true` | Plan mode + forced read-only |
| `codex_delegate_start` | `codex_app_lane action=start` | durable SQLite job |
| `codex_delegate_poll` | `codex_app_lane action=poll` | durable state/history + final diff |
| `codex_delegate_review` | `codex_app_lane action=review` | native `review/start`, все official targets |
| `codex_delegate_status` | `codex_app_status` | PID, app-server metadata, effective policy, audit |
| `codex_delegate_doctor` | `codex_app_doctor` | app-server/account/config/Windows readiness |
| `codex_delegate_models` | `codex_app_discover action=models` | live models и supported efforts |
| `codex_delegate_lanes` | `codex_app_lane action=list` | реальные `codex/*` worktrees |

Сохранены fail-closed roots, clean-base guard, `codex/*` naming, worktree
изоляция, diffstat, timeout, output schema, безопасный argv, audit и
single-active-job-per-lane. Последняя гарантия обеспечена SQLite partial unique
index, то есть работает не только между потоками одного процесса.

## Что появилось сверх старого CLI runtime

- persistent/resumable thread lifecycle;
- steer и interrupt активного turn;
- autonomous persisted goals и периодические continuation turns;
- native events/approvals/user input;
- dynamic client tools и MCP elicitation;
- thread shell command и controllable command sessions;
- filesystem v2;
- downstream MCP/SaaS call;
- apps/plugins/skills/hooks discovery;
- raw RPC для новых методов без ожидания релиза gateway;
- bearer HTTP control plane;
- exact-schema protocol introspection;
- typed administrative APIs and durable timezone-aware schedules.

## Sandbox

Для запуска без Codex sandbox:

```powershell
$env:CODEX_APP_MCP_ALLOW_FULL_ACCESS = "1"
$env:CODEX_APP_MCP_DEFAULT_SANDBOX = "danger-full-access"
$env:CODEX_APP_MCP_DEFAULT_APPROVAL_POLICY = "never"
```

Или укажите в конкретном MCP вызове:

```json
{
  "action": "run",
  "lane": "feature-x",
  "repoRoot": "D:\\Projects\\service",
  "goal": "Реализуй задачу и запусти проверки",
  "sandbox": "danger-full-access",
  "approvalPolicy": "never"
}
```

В wire app-server это переводится в `sandboxPolicy.type=dangerFullAccess`.
Experimental `process/*` вообще запускает host process без Codex sandbox и
поэтому также требует `CODEX_APP_MCP_ALLOW_FULL_ACCESS=1`.

## Покрытие протокола

Default schema `0.146.0` содержала 90 client request methods: `initialize`
плюс 89 вызываемых методов, 10 server requests и 70 notifications.

Текущий exact experimental schema настроенного бинарника содержит 126 client
methods, из них 124 вызываемых после исключения `initialize` и `mock/*`,
11 server requests и 70 notifications. Все 124 имеют typed route и также
принимаются raw RPC policy.

- основные coding/lifecycle операции имеют typed MCP tools;
- безопасные read методы имеют built-in read-only RPC allowlist;
- все 124 вызываемых метода принимаются forward-compatible
  `codex_app_rpc`, если оператор включил unsafe RPC и разрешил метод;
- все 11 server requests маршрутизируются в pending actions и получают ответ
  через `codex_app_events`.

`currentTime/read` безопасно отвечает
автоматически; остальные запросы по-прежнему требуют явного ответа оператора.

Не все stateful методы вызываются live-тестом: login/logout, billing credits,
plugin install/uninstall, config writes и sandbox setup меняют внешнее
состояние. Их protocol coverage проверяется сгенерированной схемой и raw RPC
policy, а выполнение требует явного operator allowlist.

## Что именно удалено

- Python package `codex_delegate`;
- MCP entrypoint и девять старых tool names;
- subprocess logic `codex exec` / `codex exec review`;
- старые CLI-contract и CLI-specific tests.

Исторические round/evidence/CLI-facts документы удалены после завершения
миграции. Актуальными источниками остаются `README.md`, `docs/REFERENCE.md`
и `docs/VERIFICATION.md`.
