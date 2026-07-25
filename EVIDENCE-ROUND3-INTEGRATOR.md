# EVIDENCE-ROUND3 — верификация интегратора (Claude)

Раунд 3 — не реализация, а проверка. Исполнитель (Grok) не мог запускать python/pytest внутри
lane: его shell-allowlist ограничен git. Все гейты и все замеры ниже выполнены интегратором на
хосте win11, `codex-cli 0.144.1`, `git 2.54.0.windows.1`, Python 3.14.

## 1. Гейты

| Гейт | Раунд 1 | После скептик-раунда | После правок интегратора |
|---|---|---|---|
| `py -3 -m pytest tests -q` | 104 passed | 143 passed | **148 passed** |
| `py -3 -m codex_delegate --self-test` | PASS за 5m17s (одна ложная строка PASS) | FAIL за 4m52s | **PASS (1 skipped) за 48s** |
| `py -3 -m codex_delegate --smoke-delegate` | SMOKE PASS, но вхолостую | — | **SMOKE PASS с sentinel** |
| stdio-сервер (`initialize` + `tools/list`) | — | — | **7 инструментов, отвечает** |

## 2. Сверка таблицы флагов с живым бинарём

Главный риск проекта — исполнитель «перепишет» флаги из спеки, и они разойдутся с реальностью.
Поэтому `cli_contract.py` сверен не со спекой, а с выводом самого CLI: для каждой подкоманды
разобран `--help` и посчитана двусторонняя разница.

```
--- exec ---
  declared-but-not-real : NONE
  real-but-not-declared : ['--version', '-V']
--- exec resume ---
  declared-but-not-real : NONE
  real-but-not-declared : NONE
--- exec review ---
  declared-but-not-real : NONE
  real-but-not-declared : NONE
```

Ни одного флага, объявленного в таблице и отсутствующего в бинарнике. Оставшаяся дельта —
`--version`/`-V` у `exec`: таблица их не перечисляет, но пакет их и не эмитит; это сужение, а не
ложное утверждение.

## 3. Проверка R1..R11 поведенчески

Проверялось поведение shipped-кода (моками подменялись только spawn и git), а не наличие строк в
исходниках. Итог — 13/13:

| Проверка | Результат |
|---|---|
| R1 `resume` отсутствует в схемах обоих delegate-инструментов | NONE exposing resume |
| R1 argv с `exec resume` отбивается | `RESUME_UNSUPPORTED` |
| R1 `resume`, протащенный в аргументах клиента | отказ, **0 спавнов** |
| R2 argv review не содержит флагов вне контракта | offending flags: none, cwd = worktree |
| R3 отдельный лимит doctor | `DOCTOR_TIMEOUT_SECONDS = 45.0` |
| R3 таймаут возвращает структуру | `DOCTOR_TIMEOUT` |
| R4 stdin | `DEVNULL` без input, passthrough с input |
| R5 self-test на `ok:false` | не PASS |
| R6 audit | только скаляры, ни одного `None`-ключа |
| R6 audit на пути `auth.json` | `AuditError` |
| R9 process cwd делегации | закреплён на worktree |
| R10 усечение | маркер `…(truncated)` |
| R11 smoke | sentinel + проверка `changed_files` |

## 4. Два дефекта, найденных уже после скептик-раунда

Их не было в списке R1..R11 — они вскрылись на финальном прогоне и починены интегратором.

### 4.1 Объявленный таймаут не ограничивал реальное время

`--self-test` занимал 4m52s при лимите doctor 45s. Замер напрямую:

```
declared bound: 45.0
wall clock: 240.0s | error: DOCTOR_TIMEOUT
```

Причина: `subprocess.run(timeout=...)` убивает только прямого потомка, после чего продолжает
ждать в `communicate()`, пока не завершатся все процессы, держащие унаследованные хендлы
stdout/stderr. `codex doctor` порождает внуков — они и держали pipe'ы. Таймаут, который не
ограничивает wall clock, таймаутом не является.

Фикс (`process.py`): спавн переведён на `Popen`; по `TimeoutExpired` убивается всё дерево
(`taskkill /F /T /PID` на Windows, `killpg` на POSIX через `start_new_session`), затем один
ограниченный добор pipe'ов, и если и после этого их кто-то держит — они бросаются, а не блокируют
вызывающего. Замер после фикса:

```
bound=45s  wall=45.2s  error=DOCTOR_TIMEOUT
VERDICT: BOUND HELD
```

Покрытие: `TestTimeoutKillsProcessTree` — дерево убивается (а не только потомок), на Windows
именно `/T`, и при намертво удерживаемых pipe'ах вызов возвращается, а не висит.

### 4.2 Неотвечающий вендорский `doctor` объявлял сломанным наш сервер

После фикса R5 (`ok:false` больше не печатается как PASS) `--self-test` стал давать
`RESULT: FAIL` на этом хосте — из-за `codex doctor`, который не возвращается, а не из-за кода
пакета. Обе крайности плохи: печатать PASS на упавшем инструменте — нечестно, объявлять сервер
сломанным из-за вендора — неинформативно.

Фикс (`__main__.py`): третье состояние `SKIP` для кодов из `VENDOR_SKIP_ERROR_CODES`
(сейчас — `DOCTOR_TIMEOUT`). Инвариант R5 сохранён: строка с `ok:false` **никогда** не PASS.
`RESULT` считает только PASS/FAIL, количество skipped печатается рядом. Покрытие:
`test_vendor_timeout_is_skipped_not_failed`, `test_our_own_failure_is_not_skipped`,
`test_ok_false_is_never_pass`.

Итоговая таблица:

```
codex_delegate_doctor  SKIP  DOCTOR_TIMEOUT (vendor-side; bound enforced)
RESULT: PASS (1 skipped)
```

## 5. Что осталось непроверенным

- **`codex doctor --json` на этом хосте не отрабатывает вообще.** Это поведение вендорского
  бинарника; наш путь ограничен и деградирует честно, но содержимого отчёта doctor мы не видели
  ни разу.
- **`-c model_reasoning_effort="…"`** — CLI не валидирует значение (bogus не даёт ошибки), поэтому
  фактическое применение effort не подтверждено. Валидируем allowlist сами, enforcement не обещаем.
- **`--ignore-user-config`** проверен только на то, что принимается и что сессия поднимается; что
  именно он отсекает из операторского конфига, построчно не сверялось.
- **Sandbox-энфорсмент** подтверждён на записи файла (probes A/B/C). Сетевой доступ в
  `workspace-write` отдельно не проверялся — мы полагаемся на дефолт Codex и никаких `-c`
  override'ов на эту тему не эмитим.
- **Живой прогон `codex_delegate_review`** против настоящей lane с диффом не делался: argv
  проверен на соответствие контракту и cwd, но полный ответ review не снимался.
- Delegate в режиме **`workspace-write` на реальной задаче** прогонялся только через smoke в
  plan-only. Первый настоящий execute-lane стоит запускать под наблюдением.
