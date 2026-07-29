# Codex app-server → MCP: исследование

Срез: 29 июля 2026 года. Проверены release binaries `0.145.0`, `0.146.0`,
официальная документация и schema snapshot GitHub `main`.

## Канонические источники

- [OpenAI app-server README](https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md)
- [OpenAI app-server source](https://github.com/openai/codex/tree/main/codex-rs/app-server)
- [OpenAI app-server test client](https://github.com/openai/codex/blob/main/codex-rs/app-server-test-client/README.md)
- [Agent Bridge discussion](https://github.com/openai/codex/discussions/15374)
- [raysonmeng/agent-bridge](https://github.com/raysonmeng/agent-bridge)

Для каждой установленной версии дополнительно генерировалась её собственная
JSON Schema:

```text
codex app-server generate-json-schema --out <dir>
```

Это важнее ручного копирования README: official schema version-specific.

## Вывод

Найденные community bridges решают конкретную связку агентов или дают
облегчённый session wrapper. Готового MCP, объединяющего полный app-server
protocol, persistent goals, full-access policy, worktree lanes, durable jobs,
server requests, downstream SaaS, audit и HTTP boundary, не найдено.

Поэтому реализация строится непосредственно на official app-server protocol,
а не поверх `codex exec` и не форкает community wrapper.

## Что подтверждено документацией

- bidirectional JSON-RPC 2.0 через JSONL stdio;
- обязательный initialize/initialized handshake;
- thread → turn → item lifecycle;
- persistent thread start/resume/fork/read/archive/delete;
- streamed notifications и server-initiated approval/input requests;
- per-turn model, reasoning, cwd, sandbox/permissions и approval policy;
- persisted thread goals;
- native review;
- commands, filesystem, background terminals и unsandboxed shell command;
- downstream MCP, apps, plugins, skills, auth/config/marketplace endpoints;
- source-main experimental process, environments, memory, realtime, remote
  control и paginated thread history;
- version-specific TypeScript/JSON schema generation.

Официальный WebSocket listener помечен experimental/unsupported. Gateway
поэтому использует JSONL stdio к дочернему app-server и предоставляет
собственный bearer HTTP наружу.

## Release и source-main drift

`0.146.0` default schema:

```text
90 client methods
89 callable after initialize
10 server requests
70 notifications
```

Текущий exact experimental schema настроенного бинарника:

```text
126 client methods
124 callable after initialize and mock exclusion
11 server requests
70 notifications
```

Разница включает `process/*`, environments, memory mode/reset, realtime,
remote control, richer search/settings и другие experimental endpoints.
Gateway имеет typed route для всех 124 callable methods и raw allowlisted RPC
для будущих valid method names. Coverage audit не отверг ни одного метода.

## Решения реализации

- один long-lived app-server child вместо subprocess на каждую задачу;
- exact request IDs и параллельный reader;
- все notifications routed в bounded cursor event buffers;
- human/security server requests routed в pending actions, а безопасный
  `currentTime/read` source-main запрос получает automatic Unix timestamp;
- dynamic tool/MCP elicitation не отвергаются автоматически;
- model/effort проверяются по live `model/list`;
- project paths ограничены allowed roots;
- full access задаётся только typed opt-in;
- raw mutations требуют operator method allowlist;
- downstream MCP требует одновременно server и tool allowlists;
- worktree lanes используют app-server threads/turns;
- jobs и lane ownership хранятся в SQLite;
- HTTP является отдельным MCP transport, а не попыткой использовать
  experimental app-server WebSocket как публичный service endpoint.

## Эксплуатационный вывод

Интерактивный `CODEX_HOME` с десятками plugins/MCP может сильно замедлить
startup и увеличить вероятность provider/plugin failures. Для SaaS/control
plane нужен отдельный service `CODEX_HOME` с только необходимыми
интеграциями, отдельными token, process и job DB на tenant.
