# EVIDENCE-ROUND1 — codex_delegate

Дата: 2026-07-24  
Ветка lane: текущая worktree-ветка репозитория `codexmcp-r1`  
Источник флагов CLI: [`CODEX-CLI-FACTS.md`](CODEX-CLI-FACTS.md) (codex-cli 0.144.1)

## Что сдано (секция 1 GOAL-ROUND1)

| Путь | Статус |
|---|---|
| `codex_delegate/__init__.py` | сдан |
| `codex_delegate/__main__.py` | сдан (`--self-test`, `--smoke-delegate`, default stdio) |
| `codex_delegate/guard.py` | сдан (pure policy, argv, fail-closed коды) |
| `codex_delegate/runner.py` | сдан (worktree, spawn, event stream, diffstat) |
| `codex_delegate/status.py` | сдан (version/auth/git/doctor/models/lanes/status) |
| `codex_delegate/audit.py` | сдан (redacted JSONL stderr, fail-closed sanitize) |
| `codex_delegate/server.py` | сдан (JSON-RPC 2.0 MCP, 7 tools) |
| `codex_delegate/process.py` | split-out: git/subprocess runners (лимит строк) |
| `codex_delegate/events.py` | split-out: parse_event_stream |
| `codex_delegate/worktree.py` | split-out: prepare_worktree / collect_diff |
| `codex_delegate/roots.py` | split-out: trusted root resolution |
| `codex_delegate/handlers.py` | split-out: tool dispatch |
| `codex_delegate/README.md` | сдан (контракт, env, error codes) |
| `tests/test_codex_delegate.py` | сдан (≥16 групп поведения из §9) |
| `README.md` | сдан (операторский, русский) |
| `EVIDENCE-ROUND1.md` | этот файл |

## Ограничение среды lane

Эта agent-lane имеет **git-only shell allowlist**: нельзя запускать `python`,
`pytest`, `node`, `npm` внутри lane. Поэтому ниже — **честный** статус DONE-гейтов.

| DONE-гейт (GOAL §10) | Статус в lane | Комментарий |
|---|---|---|
| `py -3 -m pytest tests -q` → all green, ≥45 tests | **не запускался в lane** | Тесты написаны под §9; ожидается первый прогон на integrator-хосте |
| `py -3 -m codex_delegate --self-test` → `RESULT: PASS` | **не запускался в lane** | Реализован; дергает version/auth/git + in-process initialize/tools |
| `py -3 -m codex_delegate --smoke-delegate` → `SMOKE PASS` | **не запускался в lane** | Реализован; plan-only в throwaway git repo + temp cleanup |
| `python codex_delegate/server.py` + initialize/tools/list | **не запускался в lane** | Dual-import + `serve_stdio` поддерживают line JSON и Content-Length |
| README.md (RU) | сдан | два сервера, wiring, env, tools, limits |
| EVIDENCE-ROUND1.md (RU) | сдан | этот файл |
| Лимит строк модулей | соблюдён на этапе написания | `guard.py`/`server.py` ≤700; остальные ≤500 |

### Команды, которые integrator должен прогнать

```powershell
cd <repo-root>
py -3 -m pytest tests -q
py -3 -m codex_delegate --self-test
# при наличии codex login и сети/модели:
py -3 -m codex_delegate --smoke-delegate
```

Ручная проверка stdio:

```powershell
# одна строка JSON-RPC на stdin, ответ на stdout
@"
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}
{"jsonrpc":"2.0","id":2,"method":"tools/list"}
"@ | py -3 codex_delegate/server.py
```

Ожидание: `serverInfo.name == "codex-delegate"`, ровно семь имён tools.

## Что покрыто тестами (поведение, не substring source)

1. `normalize_lane` — slug, prefix, reserved, charset, empty  
2. `validate_sandbox_mode` — danger forbidden, plan conflict, defaults  
3. `build_exec_argv` — `-` в конце, `--cd`, `-s`, `--json`, no forbidden, no `-c sandbox_mode`, goal не в argv, resume forms  
4. `assert_argv_safe` — каждый error code отдельным bad argv  
5. `validate_codex_bin` — client forbidden, `python.exe` reject, `codex.cmd` ok  
6. `prepare_worktree` — dirty/unreachable/inside-repo/fallback/reuse/conflict  
7. Forbidden git verbs — `push`/`merge`/`pull`/`reset`, в т.ч. за `-C` / `-c`  
8. `parse_event_stream` — sample stream из FACTS + garbage → `unparsed_lines`  
9. Probe-A regression — rc=0 + refusal + `turn.completed` → `status=="ok"`; error без complete → `error`  
10. Timeout → `status=="timeout"`, `ok is False`  
11. Goal только через `input_text`, не в argv  
12. `delegate` собирает diffstat даже при падении executor  
13. Allowlist roots / path escape / empty / lanes inside repo / client bin  
14. `sanitize_event` — auth.json, bearer, OPENAI_API_KEY; drop `goal`; allowlist fields  
15. JSON-RPC initialize / tools/list×7 / unknown tool isError / notification→None  
16. `run_readonly_cli` — reject exec/logout/mcp/features enable/login --with-api-key; accept doctor/debug models  

## Политика, зафиксированная в коде (без выдуманных флагов)

- Только флаги/сабкоманды из `CODEX-CLI-FACTS.md`.  
- Запрещены: `--dangerously-bypass-approvals-and-sandbox`,
  `--dangerously-bypass-hook-trust`, `--ignore-rules`, `--add-dir`,
  `--oss`, `--local-provider`, `--remote*`, `-s danger-full-access`.  
- Нет helper-ов, которые собирают `git push|merge|pull|rebase|cherry-pick|reset|clean`.  
- Auth: только `codex login status` (exit + фразы); `auth_file_read: false`; stdout auth не эхается.  
- Prompt всегда stdin sentinel `-` (Windows argv limits + process list).  
- Last-message файл (`-o`) создаётся в system temp **вне** worktree, удаляется в `finally`.  
- `--ignore-user-config` по умолчанию **on** (герметичность lane).  

## Оставшиеся ограничения

1. **Гейты pytest/self-test/smoke не прогнаны в этой lane** из-за shell allowlist — см. таблицу выше.  
2. `codex exec review` не принимает `--cd`/`-s`; read-only posture = default sandbox Codex (probe A), cwd процесса = worktree.  
3. Нет turn-cap в CLI 0.144.1 — единственная граница = wall-clock timeout.  
4. `-c model_reasoning_effort=...` best-effort: CLI не валидирует значение на parse (FACTS); мы валидируем сами.  
5. Успех ≠ `returncode==0`: обязателен разбор JSONL (probe A).  
6. Сервер dev-only: нет multi-tenant auth, нет remote transport, только stdio.  
7. `collect_diff` не возвращает full patch (только names + truncated diffstat).  

## Git

Один conventional commit на lane-ветке, без push и без merge в другие ветки.
