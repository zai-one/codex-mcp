# 安装与连接（简体中文）
> ### ⛔ 先安装 CLI
>
> 必须先安装 **Codex CLI** 并完成 `codex login`（同一机器/用户）。见 [START_HERE.md](../START_HERE.md)。技能：`install-codex-mcp`。


完整指南：安装 **codex-app-mcp**、通过 stdio 或 HTTP 运行，并连接常见
MCP 宿主。版本 **0.5.0**。

其他语言：[English](en.md) · [Русский](ru.md) · [Español](es.md)

## 本软件是什么

`codex-app-mcp` 是面向其他代理与 IDE 的 **受治理 MCP 网关**。它 **不是**
Codex 本体的替代品。

| 层级 | 作用 |
|---|---|
| **Codex CLI + app-server** | 后端：模型、线程、回合、目标、沙箱 |
| **codex-app-mcp** | MCP 表面（stdio / 带 bearer 的 HTTP），含策略、任务、lanes |
| **MCP 宿主** | Claude Desktop、Claude Code、Cursor、VS Code、Continue、远程代理 |

对接模型的身份验证始终是 **本地 Codex CLI 会话**（`codex login` →
`CODEX_HOME`）。本网关不要求你把 OpenAI API key 或 ChatGPT OAuth 写入
MCP JSON。

## 前置条件

1. **Python 3.10+**（`python3 --version` 或 `py -3 --version`）
2. 已安装 **Codex CLI** 并在 `PATH` 中（或设置 `CODEX_APP_MCP_BIN`）
3. **已登录的 Codex 会话**：在将运行网关的同一台机器、同一用户下执行
   `codex login`
4. 本仓库克隆（或已安装的 wheel 包）

验证 Codex：

```bash
codex --version
codex login
```

## 安装

在仓库根目录：

```bash
cd <path-to-repository>
python -m pip install -e ".[test]"
```

Windows（PowerShell）：

```powershell
Set-Location "<path-to-repository>"
py -3 -m pip install -e ".[test]"
```

将安装控制台入口 `codex-app-mcp` 与模块 `codex_app_mcp`。

## 配置项目根目录（项目路径操作必需）

根目录默认 **失败关闭（fail-closed）**。在设置允许列表之前，
`cwd` / `repoRoot` 相关操作会被拒绝。

```bash
export CODEX_APP_MCP_ALLOWED_ROOTS="/path/to/allowed/project;/path/to/other/project"
```

PowerShell：

```powershell
$env:CODEX_APP_MCP_ALLOWED_ROOTS = "D:\Projects\example;D:\Work\example"
```

可选的完全访问配置（仅限受信本机）：

```bash
export CODEX_APP_MCP_ALLOW_FULL_ACCESS=1
export CODEX_APP_MCP_DEFAULT_SANDBOX=danger-full-access
export CODEX_APP_MCP_DEFAULT_APPROVAL_POLICY=never
```

## 运行：stdio MCP

默认传输为 stdio（多数桌面 MCP 客户端会拉起该进程）：

```bash
codex-app-mcp
# 等价：
python -m codex_app_mcp
```

进程在 stdin/stdout 上使用 MCP JSON-RPC。不要让交互式 shell 混入协议字节。

## 运行：HTTP MCP（bearer）

生成 **运营方本地** 密钥（绝不要使用 OpenAI/Codex OAuth 令牌）：

```bash
export CODEX_APP_MCP_HTTP_TOKEN="$(openssl rand -hex 32)"
codex-app-mcp --transport http --host 127.0.0.1 --port 8765
```

服务部署更推荐令牌文件（与环境变量值互斥）：

```bash
export CODEX_APP_MCP_HTTP_TOKEN_FILE="/path/to/codex-app-mcp.token"
codex-app-mcp --transport http --host 127.0.0.1 --port 8765
```

端点：

| 方法 | 路径 | 鉴权 | 含义 |
|---|---|---|---|
| `GET` | `/healthz` | 无 | 进程存活 |
| `GET` | `/readyz` | bearer | 启动/探测 app-server |
| `POST` | `/mcp` | bearer | MCP JSON-RPC |
| `GET` | `/mcp` | — | 不支持（无主动 SSE 流） |

默认绑定 **127.0.0.1**。非回环绑定需要令牌；远程访问须经 TLS 反向代理或
SSH/VPN 隧道。

调用示例：

```bash
curl -sS \
  -H "Authorization: Bearer <long-random-secret>" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}' \
  http://127.0.0.1:8765/mcp
```

## Claude Desktop

典型配置路径：

- macOS：`~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows：`%APPDATA%\Claude\claude_desktop_config.json`
- Linux：`~/.config/Claude/claude_desktop_config.json`

示例（仅占位符）— 亦见
[`examples/claude_desktop.mcp.json`](../../examples/claude_desktop.mcp.json)：

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

若 `codex-app-mcp` 不在 `PATH` 中：

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

修改配置后重启 Claude Desktop，确认工具列表中出现 `codex_app_status` 等。

## Claude Code（`.mcp.json`）

项目或用户 MCP 配置 — 见
[`examples/claude-code.mcp.json`](../../examples/claude-code.mcp.json)：

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

可作为项目根目录的 `.mcp.json`，或合并到 Claude Code MCP 设置。进程用户
须拥有 `codex login` 会话。

## Cursor（`mcp.json`）

Cursor MCP 配置 — 示例
[`examples/cursor.mcp.json`](../../examples/cursor.mcp.json)：

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

HTTP（若 Cursor 版本支持 URL 型 MCP）：

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

`headers` 中只放本网关本地 HTTP bearer，不要放 OpenAI/Codex OAuth。

## VS Code / Continue

### VS Code

使用编辑器的 MCP 服务器设置。stdio 形式：

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

在 Continue 配置中注册相同 `command` / `args` / `env` 的 stdio MCP 服务器。
工作目录仅限允许列表中的根路径。

## ChatGPT（网页）与远程代理

**ChatGPT 网页不会像 Claude Desktop 或 Cursor 那样原生托管本地 stdio MCP**。
笔记本上的本地 `codex-app-mcp` 不会自动出现在 chatgpt.com 中。

若产品路径支持 **远程 MCP 连接器**：

1. 在已执行 `codex login` 的主机上运行网关。
2. 仅通过 **TLS 反向代理** 或私有隧道（SSH、VPN）暴露 HTTP MCP 端点。
3. 使用运营方 bearer（`CODEX_APP_MCP_HTTP_TOKEN` 或 `TOKEN_FILE`）保护。
4. **切勿** 将 OpenAI API key 或 Codex/ChatGPT OAuth 写入 HTTP 令牌配置；
   它们保留在服务器上的本地 CLI 会话中。
5. 在远程主机上收紧 `ALLOWED_ROOTS` 以及 full-access / unsafe RPC 开关。

任何 ChatGPT 连接器（若产品提供）必须指向由你控制的 **远程 HTTPS** 端点，
而不是未鉴权的局域网端口。

## 首次验证

```bash
python -m pytest -q
python scripts/probe_stdio.py
python scripts/probe_http.py
```

在 MCP 客户端中调用 **`codex_app_status`**（可选 `codex_app_doctor`）。
失败时先修复 `codex login` 与 `CODEX_APP_MCP_BIN`。

可选深度检查：

```bash
python scripts/check_protocol.py
python scripts/audit_protocol.py
python scripts/probe_full.py
```

## 环境变量

下表为 **默认值或占位符**。请勿提交真实密钥。另见
[`.env.example`](../../.env.example) 与
[`examples/http.env.example`](../../examples/http.env.example)。

| 变量 | 默认 | 说明 |
|---|---|---|
| `CODEX_APP_MCP_BIN` | `codex` | 提供 `app-server` 的 Codex 可执行文件 |
| `CODEX_HOME` | 用户 Codex 主目录 | app-server 的认证、配置、线程 |
| `CODEX_APP_MCP_ALLOWED_ROOTS` | 空 | `;` 列表或 JSON 数组；空 = fail-closed |
| `CODEX_APP_MCP_ALLOW_FULL_ACCESS` | `0` | 允许 `danger-full-access` 与实验性 `process/*` |
| `CODEX_APP_MCP_DEFAULT_SANDBOX` | 未设置 | `read-only` / `workspace-write` / `danger-full-access` |
| `CODEX_APP_MCP_DEFAULT_APPROVAL_POLICY` | 未设置 | `untrusted` / `on-request` / `never` |
| `CODEX_APP_MCP_ALLOW_UNSAFE_RPC` | `0` | 有状态管理 / 原始变更的门禁 |
| `CODEX_APP_MCP_ALLOWED_RPC_METHODS` | 空 | `;` 允许列表或 `*`（仅受信本机） |
| `CODEX_APP_MCP_ALLOWED_SERVERS` | 空 | 下游 MCP 服务器允许列表 |
| `CODEX_APP_MCP_ALLOWED_TOOLS` | 空 | 工具或 `server/tool` 允许列表 |
| `CODEX_APP_MCP_ALLOWED_CONFIG_KEYS` | 空 | 非安全类 thread 配置键 |
| `CODEX_APP_MCP_CONFIG_OVERRIDES` | 空 | app-server 启动配置覆盖 |
| `CODEX_APP_MCP_REQUEST_TIMEOUT_SECONDS` | `30` | 普通 RPC 超时 |
| `CODEX_APP_MCP_OPERATION_TIMEOUT_SECONDS` | `180` | 长操作超时 |
| `CODEX_APP_MCP_ACTION_TIMEOUT_SECONDS` | `60` | 挂起服务端请求超时 |
| `CODEX_APP_MCP_STATE_PATH` | 位于 `CODEX_HOME` 下 | 持久任务 SQLite 路径 |
| `CODEX_APP_MCP_SCHEDULER_ENABLED` | `1` | 自动启动持久调度 |
| `CODEX_APP_MCP_SCHEDULER_POLL_SECONDS` | `1` | 调度轮询间隔 |
| `CODEX_APP_MCP_PROTOCOL_CACHE_SECONDS` | `300` | 精确 schema 缓存 TTL |
| `CODEX_APP_MCP_AUDIT` | `1` | 无密钥审计开关 |
| `CODEX_APP_MCP_AUDIT_PATH` | stderr | 可选 JSONL 审计文件 |
| `CODEX_APP_MCP_PRINCIPAL` | `local` | 审计主体标签 |
| `CODEX_APP_MCP_TRANSPORT` | `stdio` | `stdio` 或 `http` |
| `CODEX_APP_MCP_HTTP_HOST` | `127.0.0.1` | HTTP 绑定地址 |
| `CODEX_APP_MCP_HTTP_PORT` | `8765` | HTTP 端口 |
| `CODEX_APP_MCP_HTTP_TOKEN` | 空 | Bearer 密钥；**不是** OpenAI OAuth |
| `CODEX_APP_MCP_HTTP_TOKEN_FILE` | 空 | Bearer 文件路径；与 token 环境变量互斥 |
| `CODEX_APP_MCP_HTTP_MAX_INFLIGHT` | `16` | 并发 HTTP MCP（1–256） |
| `CODEX_APP_MCP_HTTP_ALLOWED_ORIGINS` | 空 | `;` 精确浏览器 Origin 列表 |
| `CODEX_APP_MCP_LANES_PARENT` | 未设置 | worktree lanes 父目录 |

## 为何没有 WebSocket

官方 Codex app-server **WebSocket** 监听器被标记为 experimental /
unsupported。本包有意仅提供：

- 面向本地 MCP 宿主的 **stdio**；
- 面向远程控制面的 **HTTP**（`POST /mcp` + bearer）。

此处 **没有** 受支持的 WebSocket MCP 传输，`GET /mcp` 也没有主动 SSE 流。
请用 `codex_app_events` 工具轮询事件。

## 安全检查清单

- [ ] 仓库与示例中 **不含** API key、OAuth 令牌或个人路径
- [ ] 主机已完成 `codex login`；网关仅使用本地 `CODEX_HOME`
- [ ] 已设置 `CODEX_APP_MCP_ALLOWED_ROOTS`（或明确接受 fail-closed）
- [ ] 非必要不开启 `ALLOW_FULL_ACCESS` / `ALLOW_UNSAFE_RPC`
- [ ] HTTP 绑定 `127.0.0.1` 并使用 CSPRNG bearer，或 TLS 代理 + bearer
- [ ] 切勿提交 `HTTP_TOKEN` / `TOKEN_FILE`
- [ ] HTTP bearer **不是** OpenAI 或 Codex OAuth 凭证
- [ ] 每个租户/服务使用独立的 token、`CODEX_HOME` 与 SQLite
- [ ] 审计与状态文件受 ACL 保护

完整策略：[SECURITY.md](../../SECURITY.md)。

## 延伸阅读

- [参考手册](../REFERENCE.md)
- [验证记录](../VERIFICATION.md)
- [迁移说明](../MIGRATION.md)
- [贡献指南](../../CONTRIBUTING.md)
- [安全策略](../../SECURITY.md)


---

## 免责声明（非官方产品）

> **社区项目。** 这 **不是** **OpenAI**、**Codex**、Anthropic、xAI 或 Grok 的官方产品。
> 鉴权仅使用本机 **`codex login`**（`CODEX_HOME`）。**切勿** 将 OpenAI/Codex OAuth
> 或 API key 写入 MCP 配置或 HTTP bearer。

---

## Token 经济

宿主代理用短工具调用编排；**Codex app-server** 在本机或 VPS 上按 token 预算跑长循环。

| 环境变量 | 作用 |
|---|---|
| `CODEX_APP_MCP_ECONOMY=1` | 启用 economy playbook |

工具：**`codex_app_economy`**。推荐 `codex_app_goal` + `tokenBudget`（16k–40k）。  
详见：[../economy.md](../economy.md)（英文）。

---

## FastMCP

| 路径 | 方式 |
|---|---|
| 本地 stdio | `codex-app-mcp` / `python -m codex_app_mcp` |
| 远程代理 | 原生 HTTP + TLS；FastMCP `create_proxy` + bearer |

[fastmcp.md](fastmcp.md) · [../../examples/fastmcp_proxy.py](../../examples/fastmcp_proxy.py)

---

## VPS（HTTP 已原生支持）

```bash
export CODEX_APP_MCP_ALLOWED_ROOTS="<PROJECT_ROOT>"
export CODEX_APP_MCP_HTTP_TOKEN_FILE="<TOKEN_FILE>"
codex-app-mcp --transport http --host 127.0.0.1 --port 8765
```

Bearer 为运维随机密钥，**不是** OpenAI OAuth。  
[vps.md](vps.md) · [../../examples/vps.systemd.service](../../examples/vps.systemd.service) ·
[../../examples/http.env.example](../../examples/http.env.example)

---

## Economy 相关环境变量

| 变量 | 说明 |
|---|---|
| `CODEX_APP_MCP_ECONOMY` | 启用 economy playbook |
| `CODEX_APP_MCP_HTTP_TOKEN` | HTTP bearer（env） |
| `CODEX_APP_MCP_HTTP_TOKEN_FILE` | bearer 文件路径（`<TOKEN_FILE>`） |
| `CODEX_APP_MCP_HTTP_HOST` / `PORT` | 默认 `127.0.0.1:8765` |
