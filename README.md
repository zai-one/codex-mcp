# codex_delegate — governed MCP-делегация в Codex CLI

Dev-only stdio MCP-сервер: оркестратор (например Claude) передаёт coding-цель
**локальному Codex CLI** (`codex exec`) в изолированном git worktree ветки
`codex/*` и получает обратно ветку + diffstat. Без push, без merge, без обхода
sandbox/approvals.

## Два сервера — не путать

| Канал | Что это | Зачем |
|---|---|---|
| `codex mcp-server` | встроенный MCP Codex CLI | консультации/сессии «внутри» Codex |
| `codex_delegate` (этот репозиторий) | governed delegation surface | отдать goal во **внешний** headless `codex exec` в отдельном worktree |

`codex_delegate` **не** управляет MCP-серверами Codex, не логинит, не пушит и
не мержит. Это только локальный канал делегирования для dev-оркестратора.

## Быстрый старт

```powershell
# зависимости: Python 3.10+, git, codex-cli на PATH, pytest для тестов
py -3 -m pytest tests -q
py -3 -m codex_delegate --self-test
py -3 -m codex_delegate --smoke-delegate   # реальный plan-only прогон
```

### Провод в `claude_desktop_config.json`

Форма ниже проверена запуском: абсолютный путь к интерпретатору + абсолютный
путь к `server.py`, без опоры на `cwd` (пакет поднимает себя сам через
dual-import, прогнано из чужого рабочего каталога).

```json
{
  "mcpServers": {
    "codex-delegate": {
      "command": "C:\\Users\\<you>\\AppData\\Local\\Programs\\Python\\Python314\\python.exe",
      "args": ["C:\\path\\to\\Codex CLI\\codex_delegate\\server.py"],
      "env": {
        "CODEX_DELEGATE_ALLOWED_ROOTS": "C:\\path\\to\\your-repo",
        "CODEX_DELEGATE_LANES_PARENT": "C:\\path\\to\\codex-lanes",
        "CODEX_DELEGATE_IGNORE_USER_CONFIG": "1"
      }
    }
  }
}
```

Бинарник Codex **не** передаётся клиентом — только через `CODEX_DELEGATE_BIN`
или PATH. Пустой `CODEX_DELEGATE_ALLOWED_ROOTS` = fail closed
(`ALLOWED_ROOTS_EMPTY`), делегация никуда не поедет.

## Переменные окружения

| Переменная | Смысл | По умолчанию |
|---|---|---|
| `CODEX_DELEGATE_BIN` | имя/путь бинарника | `codex` |
| `CODEX_DELEGATE_ALLOWED_ROOTS` | абсолютные корни через `;` или JSON-массив | пусто → fail closed |
| `CODEX_DELEGATE_REPO_ROOT` | один корень, fallback | — |
| `CODEX_DELEGATE_LANES_PARENT` | родитель worktree | `<repo>.parent/codex-lanes` |
| `CODEX_DELEGATE_BASE_REF` | базовый ref | `HEAD` |
| `CODEX_DELEGATE_MODEL` | модель по умолчанию | — |
| `CODEX_DELEGATE_REASONING_EFFORT` | effort | — |
| `CODEX_DELEGATE_TIMEOUT_SECONDS` | таймаут | `900` (cap `3600`) |
| `CODEX_DELEGATE_IGNORE_USER_CONFIG` | `--ignore-user-config` | **вкл.** |
| `CODEX_DELEGATE_SELF_TEST_FAIL_ON_SKIP` | `--self-test` падает при любом SKIP | выкл. |

### Зачем `--ignore-user-config` по умолчанию

Операторский `$CODEX_HOME/config.toml` подключает внешние MCP и >130 skills к
каждой сессии. Делегированная lane должна быть **герметичной** — без Telegram,
infra-инструментов и desktop-доступа. Auth по-прежнему резолвится из
`CODEX_HOME`; сервер **никогда** не читает `auth.json`.

## Инструменты MCP

| Tool | Назначение |
|---|---|
| `codex_delegate` | выполнить goal в worktree, `-s workspace-write` |
| `codex_delegate_plan` | plan-only, принудительно `-s read-only` |
| `codex_delegate_review` | `codex exec review` в существующей lane (cwd = worktree) |
| `codex_delegate_status` | health JSON |
| `codex_delegate_doctor` | `codex doctor --json` |
| `codex_delegate_models` | урезанный каталог `codex debug models` |
| `codex_delegate_lanes` | список lane `codex/*` |

Схемы клиента **не** содержат `codex_bin`, `add_dir`, raw `config`. Если
`codex_bin` всё же протащен — `CODEX_BIN_CLIENT_FORBIDDEN`.

## Sandbox (честно, по живым probe)

Факты из [`CODEX-CLI-FACTS.md`](CODEX-CLI-FACTS.md) (codex-cli 0.144.1):

- **Probe A:** default `codex exec` = `read-only`, enforced на Windows.
- **Probe B:** `-c sandbox_mode=...` **не** меняет sandbox для `exec`; сервер
  никогда не эмитит этот override.
- **Probe C:** `-s workspace-write` даёт запись под `--cd`.
- `danger-full-access` и все `--dangerously-*` запрещены везде.
- **`resume` fail-closed** (`RESUME_UNSUPPORTED`): `codex exec resume` не принимает
  `--cd`/`-s`, поэтому sandbox сессии не контролируется — путь отключён.
- **`exec review`:** без `--cd`/`-s`/`--color`; cwd = worktree; sandbox = default
  Codex (`read-only`).
- **`model_reasoning_effort`:** best-effort; CLI **не** валидирует значение — мы
  проверяем allowlist сами и не обещаем enforcement на стороне бинарника.
- **`codex doctor --json`:** на этом хосте не возвращается (замер: 277.7s без
  ответа). Probe ограничен `DOCTOR_TIMEOUT_SECONDS = 45s` и отдаёт
  `DOCTOR_TIMEOUT`. Важно: одного `subprocess.run(timeout=)` для этого мало —
  он убивает только прямого потомка, а внуки держат pipe'ы, и замер давал
  **240s при объявленном лимите 45s**. Поэтому спавн идёт через `Popen` +
  kill дерева процессов (`taskkill /F /T` на Windows, `killpg` на POSIX);
  после фикса замер — **45.2s**. В `--self-test` эта строка отображается как
  `SKIP`, а не `PASS`: отказ вендорского бинарника не выдаётся за здоровье, но
  и не объявляет сломанным наш сервер. `RESULT: PASS` требует хотя бы один
  реальный PASS (полный SKIP-прогон — `FAIL`). CI без пропусков:
  `CODEX_DELEGATE_SELF_TEST_FAIL_ON_SKIP=1`.

## Известное блокирующее ограничение: запись в лейне

**На этом хосте `-s workspace-write` сейчас не даёт записи.** Проверено 2026-07-25: тот же
`codex-cli 0.144.1`, та же команда, тот же каталог, что и у успешного probe C несколькими часами
раньше — каждая попытка записи отклоняется как read-only. Воспроизведено и в обычном репозитории,
и в linked worktree. Флаг до политики доходит (видно в `codex debug prompt-input`), то есть это не
ошибка нашего argv; флаги windows-песочницы в этой сборке помечены `removed`. Подробности и
команды воспроизведения — в [`CODEX-CLI-FACTS.md`](CODEX-CLI-FACTS.md).

Что это значит на практике:

- read-only инструменты (`_plan`, `_review`, `_status`, `_models`, `_lanes`) работают;
- **execute-путь изменений не производит**, пока это так;
- delegate теперь честно возвращает `ok:false`, `status: "no_changes"`,
  `error: "EXECUTE_NO_CHANGES"` вместо `ok:true` с пустым диффом. Причина отказа видна в
  `summary` — исполнитель описывает её сам;
- обойти это мог бы только `--dangerously-bypass-approvals-and-sandbox`, который пакет запрещает
  везде. Включать его — отдельное осознанное решение владельца, не дефолт.

Проверять write-способность стоит **per session**: почему probe C прошёл, а последующие нет, не
объяснено, и выдумывать объяснение не нужно.

## Не-цели и жёсткие границы

- Нет `git push` / `merge` / `pull` / `rebase` / `cherry-pick` / `reset` / `clean`.
- Нет cloud tasks, `codex apply`, MCP/plugin management, login/logout, update.
- Worktree **никогда** не создаётся внутри основного working tree репозитория.
- Ошибки всегда structured: `{"ok": false, "error": "<CODE>", "message": "..."}`.

## Структура репозитория

```text
codex_delegate/     # пакет (вкл. cli_contract.py — immune system R7)
tests/              # pytest, полностью замокан
CODEX-CLI-FACTS.md  # единственный источник флагов CLI
GOAL-ROUND1.md      # спецификация раунда 1
GOAL-ROUND2-SKEPTIC.md  # skeptic-находки R1..R11
GOAL-ROUND4-SKEPTIC-DELTA.md  # skeptic по дельте интегратора
EVIDENCE-ROUND1.md  # что проверено в r1
EVIDENCE-ROUND2.md  # skeptic-pass и фиксы
EVIDENCE-ROUND3-INTEGRATOR.md  # гейты/замеры интегратора
EVIDENCE-ROUND4.md  # skeptic-pass по дельте D1..D4
```

Подробный контракт пакета: [`codex_delegate/README.md`](codex_delegate/README.md).
