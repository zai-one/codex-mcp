# EVIDENCE-ROUND4 — skeptic pass по дельте интегратора (D1..D4)

Дата: 2026-07-25  
Ветка lane: `grok/codexmcp-r2`  
Спека: [`GOAL-ROUND4-SKEPTIC-DELTA.md`](GOAL-ROUND4-SKEPTIC-DELTA.md)  
Предыдущий skeptic: R1–R11 (закрыты). Дельта D1–D4 написана интегратором **после**
того раунда и до этого прохода никем не атаковалась.

Shell lane — **git-only**. `pytest` / `--self-test` / `--smoke-delegate` здесь
**не** запускались; цифры гейтов ниже — baseline интегратора (см.
[`EVIDENCE-ROUND3-INTEGRATOR.md`](EVIDENCE-ROUND3-INTEGRATOR.md)), а не повторный
прогон в этой lane.

## Baseline интегратора (не переизмеряли)

| Гейт | Значение |
|---|---|
| `py -3 -m pytest tests -q` | **148 passed** |
| `py -3 -m codex_delegate --self-test` | **RESULT: PASS (1 skipped)** за 48s |
| `py -3 -m codex_delegate --smoke-delegate` | **SMOKE PASS** с sentinel |
| doctor wall clock | **45.2s** при bound 45s (было 240s) |

После наших правок ожидаем **строго > 148** тестов на стороне интегратора.

---

## D1 — `process.py`: `run_bounded` + `kill_process_tree`

### D1.1 Deadlock на большом выводе

**Трассировка.** Единственный spawn-путь — `run_bounded` → `Popen(stdout=PIPE,
stderr=PIPE, stdin=PIPE|DEVNULL)` → **всегда** `proc.communicate(...)`. Нет
ветки `stdin.write` / `stdout.read` в обход `communicate`. Goal до 60_000
символов уходит аргументом `input=` в `communicate`; JSONL читается тем же
вызовом. После timeout — второй `communicate(timeout=TREE_KILL_GRACE_SECONDS)`,
тоже через `communicate`.

**Вывод.** Deadlock pipe-buffer на штатных путях не воспроизводится: оба
направления закрыты контрактом `communicate`. Тест
`test_all_io_goes_through_communicate` фиксирует PIPE + 60k input + один
вызов `communicate` на success-пути.

### D1.2 Второй `communicate` после kill

**Трассировка.** При `TimeoutExpired` из первого `communicate` Python кладёт
частичный stdout/stderr на исключение; retry «не теряет вывод» (контракт
CPython). Код теперь:

1. `kill_process_tree(proc)`;
2. второй `communicate(grace)`;
3. если и он timeout — берём partial с second/first exception, а не `""`;
4. в stderr **всегда** дописывается `timed out after {timeout}s` (раньше
   непустой partial stderr затирал сообщение о timeout — `stderr or msg`).

Кто читает stdout после timeout:

- `run_doctor_json` — сначала `if result.get("timedOut")` → `DOCTOR_TIMEOUT`,
  stdout не парсится как success;
- `run_delegation` — `if timed_out: status = "timeout"`, `ok = status == "ok"`.

`parse_event_stream` на полустроке: `json.loads` → `unparsed_lines += 1`,
`turn_completed` остаётся False, **никогда не raise**. Тест
`test_half_line_jsonl_does_not_look_like_success`.

**Что чинили.** Потерю partial при abandon-pipes; потерю timeout-маркера при
непустом stderr.

### D1.3 Kill уже мёртвого процесса / recycle PID

**Windows.** `taskkill /F /T /PID` с `check=False` — ненулевой rc на мёртвый
PID глотается. `FileNotFoundError` (нет `taskkill` на PATH) тоже глотается
внешним `except Exception`, затем `proc.kill()`. Тесты:
`test_taskkill_against_dead_pid_is_swallowed`,
`test_taskkill_missing_falls_back_to_proc_kill`.

**Окно recycle PID.** Между выходом child и `taskkill`/`killpg` OS может
переиспользовать PID. Закрыть из userland нельзя; в docstring
`kill_process_tree` это сказано явно. Fallback `proc.kill()` бьёт по handle
исходного `Popen` и безопасен, если child уже reaped.

### D1.4 POSIX `killpg` vs собственная process group — **сломано, починено**

**Атака.** `start_new_session=True` ставится только при `os.name != "nt"`.
`killpg(getpgid(pid), SIGKILL)` без проверки: если `start_new_session` когда-нибудь
снимут, child останется в **нашей** группе, и `killpg` убьёт MCP-сервер.

**Фикс.** Перед `killpg`:

```text
child_pgid = getpgid(proc.pid)
if child_pgid is None or child_pgid == getpgrp():
    # refuse — fall through to proc.kill() only
else:
    killpg(child_pgid, SIGKILL)
```

Тесты (с mock `os.name="posix"`):  
`test_posix_killpg_refuses_own_process_group`,  
`test_posix_killpg_signals_child_group_only`,  
`test_posix_spawn_starts_new_session`.

### D1.5 `FileNotFoundError` / `missing: True`

**Трассировка.** `missing=True` выставляется **только** в `except FileNotFoundError`
вокруг `Popen`. `PermissionError` / `NotADirectoryError` пробрасываются — как
и при старом `subprocess.run`. Callers:

- `run_delegation` / `run_readonly_cli` / status probes → `CODEX_MISSING` при
  `missing`;
- tool handler ловит прочий `Exception` → `INTERNAL_ERROR`.

`.cmd` shim, который **существует**, но падает при запуске: `Popen` успешен,
`missing` не ставится — верный non-zero rc. Тесты:
`test_missing_binary_sets_missing_flag`,
`test_permission_error_is_not_missing`.

### D1.6 `default_git_runner` и stderr

При `missing` stderr принудительно `"git not found"`. Источник `missing` —
только `FileNotFoundError` с текстом `binary not found: git`; более богатой
диагностики на этом пути нет. Поведение оставлено, комментарий в коде
зафиксировал инвариант.

### D1.7 Windows без `taskkill`

`subprocess.run(["taskkill", ...])` → `FileNotFoundError` → `except Exception:
pass` → `proc.kill()`. Не raise наружу. Тест
`test_taskkill_missing_falls_back_to_proc_kill`.

---

## D2 — `SKIP` verdict в `__main__.py`

### D2.1 SKIP = замаскированный PASS? — **дыра была, закрыта**

**Сторона «нет».** Строка с `ok:false` никогда не рисуется как PASS
(`self_test_tool_row` + печать `SKIP` только при `skipped`). R5 в узком смысле
(«ok:false → PASS») не возвращён.

**Сторона «да, другой дверью».** Старый `RESULT`:

```text
RESULT: PASS if failed == 0 else FAIL
```

при **нуле** реальных PASS и N SKIP печатал бы `RESULT: PASS (N skipped)` и
exit 0 — «здорово», хотя ничего не проверено. Это и есть R5-trap из спеки.

**Фикс.** `evaluate_self_test_rows`:

- PASS только если `passed >= 1` и `failed == 0`;
- total-skip / пустая таблица → `RESULT: FAIL`, exit 1;
- опционально `fail_on_skip` (см. D2.4).

Тесты: `test_total_skip_is_not_result_pass`,
`test_one_pass_one_skip_is_result_pass`,
`test_real_failure_still_fails`.

На живом self-test интегратора doctor = 1 SKIP, остальные probe/tool rows —
реальные PASS → по-прежнему `RESULT: PASS (1 skipped)`.

### D2.2 Scope creep `VENDOR_SKIP` / env-дыра

- Множество = `frozenset({"DOCTOR_TIMEOUT"})` — тест
  `test_only_doctor_timeout_is_in_vendor_skip_set`.
- `DOCTOR_TIMEOUT` ставит только `run_doctor_json` при `result["timedOut"]`.
- Bound: константа `DOCTOR_TIMEOUT_SECONDS = 45.0`, **не** env. В
  `run_doctor_json` нет `getenv`/`environ` (тест
  `test_doctor_timeout_not_env_tunable_below_floor`).
- Аргумент `timeout=0.1` поднимается полом `max(1.0, …)` и всё равно
  capped константой — нельзя «прописать 0.1s и извинить наш косяк как vendor».

Our-side failure (`ALLOWED_ROOTS_EMPTY`, `CODEX_MISSING`, …) не входит в
skip-set → FAIL-строка. Тест `test_our_own_failure_is_not_skipped`.

### D2.3 Audit `outcome: error` vs таблица `SKIP`

**Решение:** законное различие аудиторий, не дефект R5.

| Поверхность | Сигнал | Смысл |
|---|---|---|
| audit | `outcome: "error"` | MCP-вызов вернул `ok:false` |
| self-test | `SKIP` | отказ **вендора**, не нашей проводки |

R5 запрещал `ok:false` → **PASS**. Здесь audit говорит «вызов не успешен»,
таблица — «не наш баг». Обе правды. Зафиксировано комментарием у
`VENDOR_SKIP_ERROR_CODES` в `__main__.py`.

### D2.4 Exit code и CI «без skip»

По умолчанию (как у интегратора): при реальных PASS + vendor SKIP → exit 0.

Для CI «ничего не пропущено»:

```text
CODEX_DELEGATE_SELF_TEST_FAIL_ON_SKIP=1
```

→ любой SKIP даёт `RESULT: FAIL`, exit 1. Тест `test_fail_on_skip_env_mode`.
Документировано в корневом и package README. Это **наш** env, не флаг Codex
CLI (R7 / `CODEX-CLI-FACTS.md` не затронуты).

---

## D3 — тестовая дельта

| Вопрос | Ответ |
|---|---|
| `_fake_popen` и double TimeoutExpired | `hang_forever` + счётчик `communicate_calls`; abandon-path гоняет **два** timeout |
| `poll()` | `kill_process_tree` **не** читает `poll()` — ветвление по `os.name` / pgid; `poll` оставлен для fidelity |
| Windows vs POSIX branch | Windows: assert `taskkill /F /T`; POSIX: mock `os.name` + pgid guard (раньше pragma silently skipped self-kill case) |
| R4 / DEVNULL | `is subprocess.DEVNULL` + тест, что не `None`/`PIPE` — снятие DEVNULL ломает тест |
| Theatre: return before kill | `test_timeout_kills_tree_and_returns_timed_out` требует `killed == [4242]`; без вызова kill — fail |

Добавлены тесты: self-kill guard, taskkill missing/dead, partial preserve,
communicate-only I/O, start_new_session, missing vs PermissionError, total-skip
verdict, fail_on_skip, doctor floor.

---

## D4 — docs delta

Проверены числа в README / EVIDENCE-ROUND3:

| Утверждение | Источник |
|---|---|
| 277.7s hang doctor | CODEX-CLI-FACTS.md + EVIDENCE-ROUND3 §4.1 context |
| 240s wall @ 45s bound | EVIDENCE-ROUND3 §4.1 (integrator measure) |
| 45.2s after tree-kill | EVIDENCE-ROUND3 §4.1 `VERDICT: BOUND HELD` |
| 148 pytest | EVIDENCE-ROUND3 §1 |
| PASS (1 skipped) 48s | EVIDENCE-ROUND3 §1 |

Новых неподтверждённых цифр не вводили. README: добавлены env
`CODEX_DELEGATE_SELF_TEST_FAIL_ON_SKIP`, уточнение total-skip → FAIL, ссылки
на GOAL/EVIDENCE round 3–4. Wiring snippet по-прежнему absolute-path form из
round 3.

---

## R7 discipline на дельте

Новых флагов/subcommand Codex CLI нет. `cli_contract.py` /
`CODEX-CLI-FACTS.md` не расширялись. Единственный новый knob —
**наш** env `CODEX_DELEGATE_SELF_TEST_FAIL_ON_SKIP` (self-test CI), не argv
Codex.

---

## Что изменено в коде

1. `codex_delegate/process.py` — self-kill guard; partial output on abandon;
   timeout marker always in stderr; PID-reuse comment; FileNotFoundError-only
   `missing` documented.
2. `codex_delegate/__main__.py` — `evaluate_self_test_rows` (total-skip FAIL,
   fail_on_skip); audit-vs-SKIP rationale.
3. `tests/test_codex_delegate.py` — усиление D1/D2/D3, новые поведенческие тесты.
4. `README.md`, `codex_delegate/README.md` — env + total-skip правило.
5. `EVIDENCE-ROUND4.md` — этот файл.

## Что не смогли проверить без запуска кода

- Полный `pytest` / wall-clock doctor / live `--self-test` на host интегратора
  (git-only shell). Ожидаем: all green, count > 148, self-test по-прежнему
  `PASS (1 skipped)` при живом unresponsive doctor, либо `FAIL` только если
  упали **наши** probe rows.
- Реальный `killpg` на POSIX host (lane = Windows); ветка закрыта unit-тестами
  с mock `os.name`.
- Реальный PID-reuse race — только документирован.
