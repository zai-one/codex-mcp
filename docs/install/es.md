# Instalación y conexión (español)

Guía completa para instalar **codex-app-mcp**, ejecutarlo por stdio o HTTP y
conectar hosts MCP habituales. Versión **0.5.0**.

Otros idiomas: [English](en.md) · [Русский](ru.md) · [简体中文](zh-CN.md)

## Qué es este paquete

`codex-app-mcp` es una **pasarela MCP gobernada** para otros agentes e IDE.
**No** sustituye a Codex.

| Capa | Rol |
|---|---|
| **Codex CLI + app-server** | Backend: modelos, hilos, turnos, objetivos, sandbox |
| **codex-app-mcp** | Superficie MCP (stdio / HTTP con bearer) con política, jobs, lanes |
| **Host MCP** | Claude Desktop, Claude Code, Cursor, VS Code, Continue, agentes remotos |

La autenticación hacia modelos es siempre la **sesión local del CLI Codex**
(`codex login` → `CODEX_HOME`). Esta pasarela no pide pegar una clave de
API de OpenAI ni OAuth de ChatGPT en el JSON de MCP.

## Requisitos previos

1. **Python 3.10+** (`python3 --version` o `py -3 --version`)
2. **Codex CLI** instalado y en el `PATH` (o `CODEX_APP_MCP_BIN`)
3. **Sesión Codex autenticada**: ejecute `codex login` en la misma máquina
   (y el mismo usuario) que ejecutará la pasarela
4. Un clon de este repositorio (o un wheel instalado del paquete)

Verificar Codex:

```bash
codex --version
codex login
```

## Instalación

Desde la raíz del repositorio:

```bash
cd <path-to-repository>
python -m pip install -e ".[test]"
```

Windows (PowerShell):

```powershell
Set-Location "<path-to-repository>"
py -3 -m pip install -e ".[test]"
```

Se instalan el entrypoint `codex-app-mcp` y el módulo `codex_app_mcp`.

## Configurar raíces de proyecto (obligatorio para rutas)

Las raíces fallan **cerradas (fail-closed)**. Hasta definir una lista
permitida, las operaciones con `cwd` / `repoRoot` se rechazan.

```bash
export CODEX_APP_MCP_ALLOWED_ROOTS="/path/to/allowed/project;/path/to/other/project"
```

PowerShell:

```powershell
$env:CODEX_APP_MCP_ALLOWED_ROOTS = "D:\Projects\example;D:\Work\example"
```

Perfil opcional de acceso total (solo máquina local de confianza):

```bash
export CODEX_APP_MCP_ALLOW_FULL_ACCESS=1
export CODEX_APP_MCP_DEFAULT_SANDBOX=danger-full-access
export CODEX_APP_MCP_DEFAULT_APPROVAL_POLICY=never
```

## Ejecutar: MCP stdio

El transporte por defecto es stdio (lo que la mayoría de clientes de
escritorio arrancan):

```bash
codex-app-mcp
# equivalente:
python -m codex_app_mcp
```

El proceso habla MCP JSON-RPC en stdin/stdout. No mezcle bytes de protocolo
con shells interactivos.

## Ejecutar: MCP HTTP (bearer)

Genere un secreto **local de operador** (nunca un token OAuth de OpenAI/Codex):

```bash
export CODEX_APP_MCP_HTTP_TOKEN="$(openssl rand -hex 32)"
codex-app-mcp --transport http --host 127.0.0.1 --port 8765
```

Prefiera un archivo de token para servicios (excluyente con el valor en env):

```bash
export CODEX_APP_MCP_HTTP_TOKEN_FILE="/path/to/codex-app-mcp.token"
codex-app-mcp --transport http --host 127.0.0.1 --port 8765
```

Endpoints:

| Método | Ruta | Auth | Significado |
|---|---|---|---|
| `GET` | `/healthz` | no | vivacidad del proceso |
| `GET` | `/readyz` | bearer | inicia/sondea app-server |
| `POST` | `/mcp` | bearer | MCP JSON-RPC |
| `GET` | `/mcp` | — | no soportado (sin SSE no solicitado) |

Bind por defecto: **127.0.0.1**. Bind no-loopback exige token; acceso remoto
solo detrás de reverse proxy TLS o túnel SSH/VPN.

Ejemplo de llamada:

```bash
curl -sS \
  -H "Authorization: Bearer <long-random-secret>" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}' \
  http://127.0.0.1:8765/mcp
```

## Claude Desktop

Ubicaciones típicas de configuración:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`
- Linux: `~/.config/Claude/claude_desktop_config.json`

Ejemplo (solo marcadores de posición) — también en
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

Si `codex-app-mcp` no está en el `PATH`:

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

Reinicie Claude Desktop. Confirme que aparecen herramientas como
`codex_app_status`.

## Claude Code (`.mcp.json`)

Configuración de proyecto o usuario — ver
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

Colóquelo como `.mcp.json` en la raíz del proyecto o intégralo en la
configuración MCP de Claude Code. El proceso debe ejecutarse como el usuario
con sesión `codex login`.

## Cursor (`mcp.json`)

Configuración MCP de Cursor — ejemplo
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

HTTP (si su build de Cursor admite servidores MCP por URL):

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

En `headers` solo el bearer HTTP local de esta pasarela, nunca OAuth de
OpenAI/Codex.

## VS Code / Continue

### VS Code

Use la configuración de servidores MCP del editor. Forma stdio:

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

En la configuración de Continue registre un servidor MCP stdio con los
mismos `command` / `args` / `env`. Directorios de trabajo solo en raíces
permitidas.

## ChatGPT (web) y agentes remotos

**ChatGPT web no aloja de forma nativa MCP stdio local** como Claude Desktop
o Cursor. El `codex-app-mcp` local de su portátil no aparece automáticamente
en chatgpt.com.

Si un producto admite **conectores MCP remotos**:

1. Ejecute la pasarela en un host con `codex login`.
2. Exponga **solo** el endpoint HTTP MCP mediante **reverse proxy TLS** o
   túnel privado (SSH, VPN).
3. Protéjalo con bearer de operador
   (`CODEX_APP_MCP_HTTP_TOKEN` o `CODEX_APP_MCP_HTTP_TOKEN_FILE`).
4. **Nunca** ponga claves de API de OpenAI ni OAuth de Codex/ChatGPT en la
   configuración del token HTTP; permanecen en el servidor como sesión CLI
   local.
5. Mantenga `ALLOWED_ROOTS` y los gates full-access / unsafe estrictos en el
   host remoto.

Cualquier conector de ChatGPT (si existe) debe apuntar a un endpoint
**HTTPS remoto** bajo su control, no a un puerto LAN sin autenticación.

## Primera verificación

```bash
python -m pytest -q
python scripts/probe_stdio.py
python scripts/probe_http.py
```

En un cliente MCP invoque **`codex_app_status`** (y opcionalmente
`codex_app_doctor`). Si falla, corrija primero `codex login` y
`CODEX_APP_MCP_BIN`.

Comprobaciones opcionales:

```bash
python scripts/check_protocol.py
python scripts/audit_protocol.py
python scripts/probe_full.py
```

## Variables de entorno

Los valores son **predeterminados o marcadores de posición**. No confirme
secretos reales. Ver también [`.env.example`](../../.env.example) y
[`examples/http.env.example`](../../examples/http.env.example).

| Variable | Predeterminado | Descripción |
|---|---|---|
| `CODEX_APP_MCP_BIN` | `codex` | Binario Codex que provee `app-server` |
| `CODEX_HOME` | home Codex del usuario | Auth, config e hilos de app-server |
| `CODEX_APP_MCP_ALLOWED_ROOTS` | vacío | Lista `;` o array JSON; vacío = fail-closed |
| `CODEX_APP_MCP_ALLOW_FULL_ACCESS` | `0` | Permitir `danger-full-access` y `process/*` |
| `CODEX_APP_MCP_DEFAULT_SANDBOX` | sin definir | `read-only` / `workspace-write` / `danger-full-access` |
| `CODEX_APP_MCP_DEFAULT_APPROVAL_POLICY` | sin definir | `untrusted` / `on-request` / `never` |
| `CODEX_APP_MCP_ALLOW_UNSAFE_RPC` | `0` | Gate para admin stateful / mutaciones raw |
| `CODEX_APP_MCP_ALLOWED_RPC_METHODS` | vacío | Allowlist `;` o `*` (solo local confiable) |
| `CODEX_APP_MCP_ALLOWED_SERVERS` | vacío | Allowlist de servidores MCP downstream |
| `CODEX_APP_MCP_ALLOWED_TOOLS` | vacío | Allowlist de tools o `server/tool` |
| `CODEX_APP_MCP_ALLOWED_CONFIG_KEYS` | vacío | Claves de config de hilo no de seguridad |
| `CODEX_APP_MCP_CONFIG_OVERRIDES` | vacío | Overrides de arranque de app-server |
| `CODEX_APP_MCP_REQUEST_TIMEOUT_SECONDS` | `30` | Timeout RPC ordinario |
| `CODEX_APP_MCP_OPERATION_TIMEOUT_SECONDS` | `180` | Timeout de operaciones largas |
| `CODEX_APP_MCP_ACTION_TIMEOUT_SECONDS` | `60` | Timeout de server-request pendiente |
| `CODEX_APP_MCP_STATE_PATH` | bajo `CODEX_HOME` | Ruta SQLite de jobs durables |
| `CODEX_APP_MCP_SCHEDULER_ENABLED` | `1` | Autoinicio de schedules durables |
| `CODEX_APP_MCP_SCHEDULER_POLL_SECONDS` | `1` | Intervalo de sondeo del scheduler |
| `CODEX_APP_MCP_PROTOCOL_CACHE_SECONDS` | `300` | TTL de caché de schema exacto |
| `CODEX_APP_MCP_AUDIT` | `1` | Auditoría sin secretos on/off |
| `CODEX_APP_MCP_AUDIT_PATH` | stderr | Archivo JSONL de auditoría opcional |
| `CODEX_APP_MCP_PRINCIPAL` | `local` | Etiqueta de principal en auditoría |
| `CODEX_APP_MCP_TRANSPORT` | `stdio` | `stdio` o `http` |
| `CODEX_APP_MCP_HTTP_HOST` | `127.0.0.1` | Dirección de bind HTTP |
| `CODEX_APP_MCP_HTTP_PORT` | `8765` | Puerto HTTP |
| `CODEX_APP_MCP_HTTP_TOKEN` | vacío | Secreto bearer; **no** OAuth de OpenAI |
| `CODEX_APP_MCP_HTTP_TOKEN_FILE` | vacío | Ruta al archivo bearer; exclusivo con token env |
| `CODEX_APP_MCP_HTTP_MAX_INFLIGHT` | `16` | MCP HTTP concurrentes (1–256) |
| `CODEX_APP_MCP_HTTP_ALLOWED_ORIGINS` | vacío | Lista `;` de Origins exactos del navegador |
| `CODEX_APP_MCP_LANES_PARENT` | sin definir | Directorio padre de worktree lanes |

## Por qué no hay WebSocket

El listener **WebSocket** oficial de app-server está marcado como
experimental / no soportado. Este paquete expone deliberadamente:

- **stdio** para hosts MCP locales, y
- **HTTP** (`POST /mcp` con bearer) para planos de control remotos.

No hay transporte MCP WebSocket soportado, ni flujo SSE no solicitado en
`GET /mcp`. Sondee eventos con la herramienta `codex_app_events`.

## Lista de comprobación de seguridad

- [ ] El repositorio y los ejemplos **no** contienen API keys, tokens OAuth
      ni rutas personales
- [ ] El host tiene `codex login`; la pasarela usa solo `CODEX_HOME` local
- [ ] `CODEX_APP_MCP_ALLOWED_ROOTS` definido (o fail-closed intencional)
- [ ] `ALLOW_FULL_ACCESS` / `ALLOW_UNSAFE_RPC` desactivados si no hacen falta
- [ ] HTTP en `127.0.0.1` con bearer CSPRNG, o proxy TLS + bearer
- [ ] `HTTP_TOKEN` / `TOKEN_FILE` nunca en git
- [ ] El bearer HTTP **no** es una credencial OAuth de OpenAI/Codex
- [ ] Token, `CODEX_HOME` y SQLite separados por tenant/servicio
- [ ] Archivos de auditoría y estado protegidos con ACL

Política completa: [SECURITY.md](../../SECURITY.md).

## Lectura adicional

- [Referencia](../REFERENCE.md)
- [Verificación](../VERIFICATION.md)
- [Migración](../MIGRATION.md)
- [Contribuir](../../CONTRIBUTING.md)
- [Seguridad](../../SECURITY.md)


---

## Aviso legal (producto no oficial)

> **Proyecto comunitario.** **No** es un producto oficial de **OpenAI**, **Codex**,
> Anthropic, xAI ni Grok. La autenticación es el **`codex login` local**
> (`CODEX_HOME`). **Nunca** ponga OAuth de OpenAI/Codex ni API keys en la
> configuración MCP ni en el bearer HTTP.

---

## Economía de tokens

El host orquesta con llamadas cortas; **Codex app-server** ejecuta el bucle
largo bajo un token budget (local o VPS).

| Variable | Propósito |
|---|---|
| `CODEX_APP_MCP_ECONOMY=1` | Modo economy / playbook |

Herramienta: **`codex_app_economy`**. Prefiera `codex_app_goal` + `tokenBudget`
(16k–40k). Guía: [../economy.md](../economy.md) (EN).

---

## FastMCP

| Ruta | Cómo |
|---|---|
| stdio local | `codex-app-mcp` / `python -m codex_app_mcp` |
| Proxy remoto | HTTP nativo + TLS; FastMCP `create_proxy` + bearer |

[fastmcp.md](fastmcp.md) · [../../examples/fastmcp_proxy.py](../../examples/fastmcp_proxy.py)

---

## VPS (HTTP ya nativo)

```bash
export CODEX_APP_MCP_ALLOWED_ROOTS="<PROJECT_ROOT>"
export CODEX_APP_MCP_HTTP_TOKEN_FILE="<TOKEN_FILE>"
codex-app-mcp --transport http --host 127.0.0.1 --port 8765
```

Bearer = secreto del operador, **no** OAuth de OpenAI.  
[vps.md](vps.md) · [../../examples/vps.systemd.service](../../examples/vps.systemd.service) ·
[../../examples/http.env.example](../../examples/http.env.example)

---

## Variables de economy / HTTP

| Variable | Descripción |
|---|---|
| `CODEX_APP_MCP_ECONOMY` | Activa el playbook de economy |
| `CODEX_APP_MCP_HTTP_TOKEN` | Bearer en env |
| `CODEX_APP_MCP_HTTP_TOKEN_FILE` | Ruta al bearer (`<TOKEN_FILE>`) |
| `CODEX_APP_MCP_HTTP_HOST` / `PORT` | Por defecto `127.0.0.1:8765` |
