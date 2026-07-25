# EVIDENCE-ROUND2 — skeptic pass (R1..R11)

Дата: 2026-07-25  
Ветка lane: `grok/codexmcp-r1`  
Источник флагов CLI: [`CODEX-CLI-FACTS.md`](CODEX-CLI-FACTS.md) (codex-cli 0.144.1)  
Спека: [`GOAL-ROUND2-SKEPTIC.md`](GOAL-ROUND2-SKEPTIC.md)

## Гейты, которые integrator уже прогнал (до правок)

| Гейт | Результат integrator |
|---|---|
| `py -3 -m pytest tests -q` | **104 passed** |
| `py -3 -m codex_delegate --self-test` | `RESULT: PASS`, но **5m17s** и ложный PASS у doctor (R5) |
| `py -3 -m codex_delegate --smoke-delegate` | `SMOKE PASS`, но goal/вердикт ничего не доказывали (R11) |

Shell lane по-прежнему **git-only** — python/pytest здесь не запускались. Не
утверждаем, что прогнали; ожидаем первый зелёный прогон у integrator.

## Находки и исправления

### R1 — CRITICAL — `exec resume` мёртв (`--cd`/`-s` отвергаются)

- **Факт:** `codex exec resume … --cd . -s read-only …` → `unexpected argument '--cd'`.
- **Было:** `build_exec_argv(resume=…)` эмитил resume + `--cd` + `-s`.
- **Стало:** resume fail-closed (`RESUME_UNSUPPORTED`) в `build_exec_argv`,
  `assert_argv_safe`, handlers (в т.ч. smuggle через additionalProperties).
  Поле `resume` убрано из схем `codex_delegate` / `codex_delegate_plan`.
  Набор флагов resume записан в `CODEX-CLI-FACTS.md` и `cli_contract.py`.

### R2 — CRITICAL — `exec review` отвергает `--color`

- **Факт:** `codex exec review --color never --json -` → `unexpected argument '--color'`.
- **Было:** handlers собирали `--color never`.
- **Стало:** `build_review_argv` без `--color`/`--cd`/`-s`; sandbox в ответе —
  скаляр `"read-only"` + `sandbox_is_codex_default: true` (не prose-строка).

### R3 — HIGH — doctor блокирует клиент на минуты

- **Факт:** `doctor --json` TIMEOUT after **277.7s** (stdin уже DEVNULL).
- **Стало:** `DOCTOR_TIMEOUT_SECONDS = 45.0` (≤60); `run_doctor_json` возвращает
  `{"ok": false, "error": "DOCTOR_TIMEOUT", …}`; `build_status_report` doctor
  не вызывает. Измерение зафиксировано в `CODEX-CLI-FACTS.md`.

### R4 — HIGH — children наследуют stdin MCP-сервера

- **Было:** `subprocess.run(..., input=None)` → stdin наследуется (JSON-RPC канал).
- **Стало:** `stdin=DEVNULL` когда `input_text is None` (subprocess + git runners);
  тесты патчат `subprocess.run` и проверяют kwargs.

### R5 — MEDIUM — self-test печатал PASS при `ok: false`

- **Было:** doctor row считал «dict вернулся» успехом.
- **Стало:** `self_test_tool_row` требует `ok is True`; unit-тест на FAIL.

### R6 — MEDIUM — audit: объекты и null-стена

- **Стало:** `sanitize_event` коэрсит только scalars, опускает `None`, дропает
  dict/list; `changed_file_count` эмитится только при наличии `changed_files`.

### R7 — MEDIUM — immune system (центр раунда)

- **Стало:** `codex_delegate/cli_contract.py` — таблицы `EXEC_FLAGS` /
  `EXEC_RESUME_FLAGS` / `EXEC_REVIEW_FLAGS` + `assert_flags_in_contract`.
  Wired в `assert_argv_safe`. Conformance-тесты: argv от `build_exec_argv` /
  `build_review_argv` → каждый `-…` токен ∈ set; re-add `--color` к review и
  `--cd` к resume → fail.

### R8 — LOW — non-numeric timeout env → INTERNAL_ERROR

- **Стало:** `_default_timeout` передаёт raw в `validate_timeout` → `TIMEOUT_INVALID`.

### R9 — LOW — process cwd не pin'ился

- **Стало:** `run_delegation` передаёт worktree как cwd **и** сохраняет `--cd`.

### R10 — LOW — silent truncation

- **Стало:** маркер `…(truncated)` в `events._truncate` и `runner._truncate`.

### R11 — MEDIUM — smoke PASS без проверки ответа

- **Стало:** goal — read-only sentinel `CODEX_DELEGATE_SMOKE_OK`;
  `evaluate_smoke_result` требует `ok is True` **и** sentinel в summary **и**
  `changed_files == []`.

## Дополнительный hunt (класс R1/R2)

| Эмит | Проверено против реального option set |
|---|---|
| plain `exec` flags (`--cd`, `-s`, `--json`, `--color`, `-o`, `-m`, `-c`, …) | ∈ `EXEC_FLAGS` |
| `exec review` flags | ∈ `EXEC_REVIEW_FLAGS`; нет `--color`/`--cd`/`-s` |
| `exec resume` | **не эмитим**; contract + runtime reject |
| `-c model_reasoning_effort` | ключ реальный; value **не** enforced CLI — честно в README/comments |
| `-c sandbox_mode` | запрещён (`ARGV_FORBIDDEN_FLAG`) |
| readonly: `--version`, `login status`, `doctor --json`, `debug models` | allowlist verb; не exec-флаги |
| forbidden: `--dangerously-*`, `--add-dir`, `--ignore-rules`, … | `FORBIDDEN_CLI_FLAGS` |

## Что осталось неверифицированным в этой lane

1. Полный `pytest` / `--self-test` / `--smoke-delegate` на integrator-хосте
   (git-only shell). После правок self-test **честно** упадёт на doctor, если
   `doctor --json` всё ещё timeout'ится — это корректный FAIL, не ложный PASS.
2. Живой smoke зависит от auth + модели; sentinel-goal должен пройти в plan-only.
3. Полный `--help` dump resume/review на будущих версиях CLI — contract нужно
   обновлять при апгрейде codex-cli.

## DONE conditions (ожидание integrator)

1. `pytest` all green, **строго > 104** тестов.
2. Ни один argv-флаг вне real option set (R7 table + tests).
3. `resume` → `RESUME_UNSUPPORTED`, нет в схемах.
4. Doctor bounded + `DOCTOR_TIMEOUT`.
5. Children не наследуют stdin.
6. Self-test не PASS при `ok: false`.
7. Audit: только scalars, без `None`-ключей.
