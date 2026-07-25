# EVIDENCE-ROUND5 — полный поверхностный аудит

Дата: 2026-07-25  
Ветка lane: `grok/codexmcp-r5`  
Спека: [`GOAL-ROUND5-FULL-AUDIT.md`](GOAL-ROUND5-FULL-AUDIT.md)  
Shell lane — **git-only**. `pytest` / `--self-test` / `--smoke-delegate` **не**
запускались; гейты исполняет интегратор. Baseline: 169 tests, `--self-test` PASS
(1 skipped). После правок ожидаем **строго > 169** тестов.

Правило раунда: find → prove → fix → cover. Ниже — по секциям A–G.

---

## A. Опции и пути, которые ни разу не гонялись end-to-end

### A1. `output_schema`

**Атака.** Трассировка `run_delegation`: `mkstemp` → запись → argv
`--output-schema` → spawn → `finally: unlink`.

**Сломано.** Путь schema-файла присваивался **после** `write`. Если запись
падала (например, non-UTF-8 / surrogate), `schema_file` оставался `None`, и
`finally` не удалял файл, созданный `mkstemp`.

**Фикс.** Присваивать `schema_file = Path(sname)` сразу после `mkstemp`; при
сбое `fdopen` закрывать fd; `validate_output_schema` отвергает non-UTF-8.
Temp-файлы — в process temp dir (не worktree).

**Регрессии.** `TestOutputSchemaTempLifecycle` (cleanup на success / CODEX_MISSING /
write-fail; reject array / oversize / surrogate).

**Держится по трассировке.** На timeout и exception после mkstemp `finally`
всегда unlink'ает оба пути. Argv-build failure (`GuardError`) тоже проходит
через `finally`.

### A2. `ephemeral`

**Проверено.** `--ephemeral` ∈ `EXEC_FLAGS` / `EXEC_REVIEW_FLAGS`
(`cli_contract.py` = CODEX-CLI-FACTS.md). `build_exec_argv(ephemeral=True)`
эмитит флаг; `assert_flags_in_contract` принимает. Resume по-прежнему
fail-closed — ephemeral не взаимодействует с persisted session (resume не
эмитится). Регрессия: `TestEphemeralFlag`.

### A3. `codex_delegate_review`

**Атака / сценарии.**

| Вход | Было | Стало |
|---|---|---|
| worktree удалён, branch жив | `exists()` — file мог пройти | `is_dir()` → `WORKTREE_MISSING` |
| instructions = whitespace | default prompt | default prompt (явный strip) |
| uncommitted + base_ref | оба в argv | оба в argv (легально для CLI) |
| error stream без turn.completed | status error | без изменений, тест |
| timeout | status timeout | без изменений, тест |
| lane busy | — | `LANE_BUSY` (секция D) |

Argv review строится через `build_review_argv` + `assert_argv_safe` +
`assert_flags_in_contract` — флаг вне контракта не проходит.

### A4. `codex_delegate_models` (reduction)

**Сломано.**

1. `"models": "gpt-5"` (не list) → итерация по **символам** строки.
2. Объект без `models` → `list(raw.values())` мог подхватить мусор.
3. Нет верхней границы stdout (5 MB payload).

**Фикс.** `models` обязан быть list (или отсутствует → пустой каталог);
`MODELS_TOO_LARGE` при `len(stdout) > MAX_MODELS_STDOUT_CHARS` (1e6);
записи без slug пропускаются.

**Регрессии.** `TestModelsReductionBounds`.

### A5. `codex_delegate_lanes` (porcelain)

**Сломано.** Пути с пробелами/non-ASCII git C-quote'ит (`"path with space"`,
octal escapes) — парсер оставлял кавычки. Detached / prunable / missing dir
могли вести себя непредсказуемо.

**Фикс.** `decode_git_path` (C-unquote + octal → UTF-8); missing dir →
`worktree_missing: true` без raise; detached без `branch` не попадает в
codex-lanes; `prunable` не роняет парсер.

**Регрессии.** `TestLanesPorcelainParsing`.

---

## B. Путь репозитория с пробелом

Контекст: `C:\Users\codex\Documents\Projects\MCP\Codex CLI`.

**Проверено.**

* Argv строится list'ом (`Popen(argv)`), **без** shell и **без** ручных кавычек
  вокруг `--cd` / `-o` / worktree path.
* `worktree_path_for_lane` / `resolve_lanes_parent` — `pathlib.Path` (пробелы и
  non-ASCII сохраняются).
* Porcelain / name-only / status — через `decode_git_path`.
* Audit: path scalars; credential paths (`.codex`, `auth.json`) → suppress.

**Регрессии.** `TestPathsWithSpaces`, decode/collect_diff quoted paths.

---

## C. Утечки ресурсов и состояния

### C1. Temp files

Доказано конструкцией + тестами A1: `last_message_file` и `schema_file`
unlink'аются в `finally` на success, CODEX_MISSING, timeout, write-fail.
Review temp-файлов не создаёт.

### C2. Process handles

**Сломано (накопление).** После abandon-pipes `Popen` не reaped — на
long-lived stdio сервере зомби/хендлы копятся на каждый timeout.

**Фикс.** После tree-kill: `proc.wait(timeout=TREE_KILL_GRACE_SECONDS)`
best-effort. Тест timeout-path проверяет `wait_calls >= 1`.

### C3. Worktrees после failed delegation

**Решение (явно в docstring `prepare_worktree`).** Оставшийся worktree —
**operator evidence**, не мусор. Авто-delete запрещён (потеря диагноза).
Reuse при matching HEAD; конфликт → `WORKTREE_EXISTS_CONFLICT`.

---

## D. Concurrency

**Политика (fail-closed, err toward refuse):**

1. Не больше одного in-flight run на `(repo_root, lane)` → `LANE_BUSY`.
2. `prepare_worktree` держит per-repo lock на время git-вызовов (гонка
   `worktree add`).

Модуль: `codex_delegate/lane_lock.py`. Обёртка в `delegate()` и review handler.

**Граница гарантии (правка интегратора, раунд 5).** Формулировка выше была шире
действительности. `_active_lanes` — модульное множество, `repo_git_lock` —
`threading.Lock` в модульном словаре: сериализуются **только вызовы внутри
одного процесса**. Второй экземпляр MCP-сервера или скрипт, запущенный рядом с
живым сервером, не разделяют это состояние и всё ещё могут гонять
`git worktree add` по одному репозиторию. Кросс-процессный случай **не закрыт**;
для него нужен on-disk lock, а это отдельное решение со своим режимом отказа
(протухшая блокировка после падения процесса). Не переписывать это обратно в
«гонки worktree невозможны».

**Регрессии.** `TestConcurrencyLaneBusy`, `TestLaneLockScopeIsProcessLocal`
(последний фиксирует именно границу: после сброса состояния, эмулирующего новый
процесс, тот же лейн захватывается повторно).

Temp-file name collisions: `mkstemp` уникален — гонки имён нет.

`BASE_DIRTY` flapping из-за чужого lane: worktree **вне** main tree; dirty
main tree — отдельный сигнал оператора, не сериализуем чужие правки.

---

## E. Protocol robustness (`server.py`)

| Вход | Поведение |
|---|---|
| batch (JSON array) | `-32600` Batch not supported |
| non-object body | `-32600` Invalid Request |
| `id: null` / float | ответ с тем же id |
| `params` list | `TOOL_UNKNOWN` (нет name) |
| `arguments` list | `TOOL_ARGUMENTS_INVALID` |
| Content-Length negative / > 2MB | error frame + **закрытие** (desync) |
| Content-Length non-numeric | frame drop, сервер жив |
| unknown method notification | `None` (тихо) |
| unknown method + id | `-32601` |
| arguments > 2MB | `TOOL_ARGUMENTS_TOO_LARGE` |

**Регрессии.** `TestProtocolRobustness`.

---

## F. Guard bypasses

### F1. Флаг в argv в обход контракта

Review: `build_review_argv` → `assert_argv_safe` → `assert_flags_in_contract`.
Отдельного «голого» argv builder'а нет.

### F2. `-c` / `--config` sandbox_mode

**Сломано.** Ловился только токен `-c` + следующий; не ловились
`--config …`, `-csandbox_mode=…`, `--config=…`, quoted/whitespace key, второй
`-c` после benign.

**Фикс.** `_reject_sandbox_mode_config_overrides` — все перечисленные формы.

**Не закрыто (и сказано прямо):** содержимое operator `config.toml` при
осознанно выключенном `--ignore-user-config` — вне argv.

### F3. `normalize_lane`

**Сломано.** Нет лимита длины slug; Windows device names (`con`, `nul`,
`com1`, …) создавали бы unopenable directory.

**Фикс.** `MAX_LANE_SLUG_CHARS=64`; reserved device set; `CODEX/x`,
`codex//x`, `codex/CODEX/x` → `LANE_INVALID`.

### F4. `validate_codex_bin`

**Фикс.** UNC (`\\` / `//`) → reject; trailing `.` на basename/path → reject;
trailing space нормализуется `strip()` (возврат без хвоста).

**Не закрыто:** world-writable directory вокруг `codex.cmd` — без ACL-probe
на Windows (ненадёжно без привилегий); остаётся residual risk оператора.

### F5. Audit

`sanitize_event` fail-closed на `.codex` / `auth.json` / secret patterns;
`emit_audit` → `audit_suppressed`. Goal только fingerprint (8 hex).
`changed_files` в audit **не** пишутся — только count. Регрессия на
`.codex` path.

---

## G. Honesty invariants (критично)

Перечень мест, где пакет решает `ok` / `status` / печатный вердикт:

| Место | Вопрос «что даёт ложный success?» | Вердикт |
|---|---|---|
| `run_delegation` status | rc=0 + turn.completed при refusal | ok на stream-уровне; **execute** дальше режется |
| `delegate` + empty diff | execute без изменений | `EXECUTE_NO_CHANGES` (R4, подтверждено R5) |
| `delegate` plan empty | plan без diff | ok — ожидаемо |
| `_handle_review` | timeout / error stream | `ok:false` |
| `run_doctor_json` | **rc=0 + non-JSON / empty** | **было `ok:true` — СЛОМАНО** |
| `run_models` | models=string / huge | parse/size error, не ok |
| `list_lanes` | git list ok | ok = список получен (пусто — честно) |
| `build_status_report` | binary missing | `ok:true` envelope; nested `binary_found:false` |
| `self_test_tool_row` | ok:false | never PASS (R5) |
| `evaluate_self_test_rows` | all SKIP | RESULT FAIL (R5) |
| `evaluate_smoke_result` | ok без sentinel | FAIL (R5) |
| `collect_diff` | missing wt | `ok:false` (данные, не tool envelope) |

### G-finding (critical): doctor false success

**Сценарий.** `codex doctor --json` → rc=0, stdout=`not-json` или `""`.
Старый код: `ok: result.returncode==0` → **`ok:true` при `doctor: null`**.

**Фикс.** `DOCTOR_PARSE_FAILED` / empty body → `ok:false` всегда.
Валидный JSON + rc=0 → `ok:true`.

**Регрессии.** `TestHonestyInvariantsRound5`.

### Status envelope

`codex_delegate_status` с `ok:true` при отсутствующем binary — **не** баг
класса R5: это health *report*; сигнал здоровья в nested-полях
(`codex.binary_found`, `auth.auth_present`, `git.available`). Зафиксировано
тестом с явным комментарием.

---

## Что остаётся неизвестным (нужен live run)

1. Реальный `codex exec` / `exec review` / write capability под
   `-s workspace-write` на этом хосте — vendor-side read-only
   (CODEX-CLI-FACTS.md, 2026-07-25). Не обходили.
2. Поведение `codex debug models` на живом 5 MB payload — bound проверен
   unit-тестом, live размер каталога не измеряли.
3. ACL world-writable на каталог `codex.cmd` — не зондировали.

---

## CODEX-CLI-FACTS.md

Новых CLI-флагов / subcommand'ов **не** установлено. Факты git porcelain
quotePath — git, не Codex; в FACTS не добавлялись.

---

## Файлы

| Файл | Суть |
|---|---|
| `codex_delegate/guard.py` | lane reserved/length, schema UTF-8, bin UNC/dot, config override forms |
| `codex_delegate/worktree.py` | `decode_git_path`, prepare contract, collect_diff missing, repo lock |
| `codex_delegate/lane_lock.py` | **new** — LANE_BUSY + per-repo git lock |
| `codex_delegate/runner.py` | schema temp lifecycle, lane_run_scope в delegate |
| `codex_delegate/handlers.py` | review is_dir, default prompt, lane lock, assert_argv_safe |
| `codex_delegate/status.py` | doctor honesty, models bound/shape, lanes decode/missing |
| `codex_delegate/process.py` | wait after timeout (reap) |
| `codex_delegate/server.py` | batch/hostile params/Content-Length bounds |
| `tests/test_codex_delegate.py` | Round-5 regressions (A–G) |

Коммит (ожидаемый): `fix(codex-delegate): full-surface audit round`.
