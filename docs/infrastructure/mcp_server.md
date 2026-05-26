# MCP Server 使用文档

## 概述

MCP Server 是 AID Work Agent 系统的对外能力暴露层，遵循 [Model Context Protocol](https://modelcontextprotocol.io/) 规范，允许外部 AI 智能体（如 Claude Desktop、Cursor、自定义 MCP Client）通过标准协议调用系统内的 Skill 能力。

**架构原理**：MCP Server 不重复实现业务逻辑，而是将外部工具调用转发到现有的 `SkillExecutor` 执行 skill 脚本。

```
外部 AI 智能体 (Claude Desktop / Cursor / 自定义 Client)
  → MCP 协议 (JSON-RPC 2.0)
    → MCP Server (src/mcp/server.py)
      → MCPToolRegistry (查找 skill 脚本)
        → MCPExecutor (构建命令)
          → SkillExecutor (执行 skill 脚本)
            → 子进程执行 Python 脚本
```

## 启动方式

### 1. 启用 MCP Server

在 `configs/config.yaml` 中设置 `mcp.enabled: true`，或通过环境变量：

```bash
export MCP_ENABLED=true
```

### 2. 命令行启动

```bash
# stdio 模式（本地 CLI / 编辑器集成）
python -m src.mcp.server

# SSE 模式（远程 HTTP 连接，默认）
MCP_TRANSPORT=sse MCP_ENABLED=true python -m src.mcp.server

# Streamable HTTP 模式（MCP 协议 2025-11-25 新传输）
MCP_TRANSPORT=streamable-http MCP_ENABLED=true python -m src.mcp.server
```

也可以使用 `python -m src.mcp`（等效）。

### 3. 作为独立进程运行

建议在生产环境中使用 systemd 或 supervisor 管理 MCP Server 进程，与主 Web 服务分开。

## 配置说明

所有配置在 `configs/config.yaml` 的 `mcp:` 节下：

```yaml
mcp:
  enabled: false                        # 是否启用 MCP Server
  transport: sse                        # 传输模式: stdio | sse | streamable-http
  host: "0.0.0.0"                       # HTTP 监听地址（sse/streamable-http 模式）
  port: 8765                            # HTTP 监听端口
  auth:
    enabled: true                       # 是否启用 API Key 认证
    api_keys: []                        # API Key 列表
  tools:                                # 暴露的 skill 工具列表
    - skill: trade-customer
      commands: [save-customer, search-customers]
    - skill: baidu-search
      commands: [search]
```

### 环境变量覆盖

所有配置项都可以通过环境变量覆盖：

| 环境变量 | 对应配置 | 说明 |
|---------|---------|------|
| `MCP_ENABLED` | `mcp.enabled` | `true` / `false` |
| `MCP_TRANSPORT` | `mcp.transport` | `stdio` / `sse` / `streamable-http` |
| `MCP_HOST` | `mcp.host` | HTTP 监听地址 |
| `MCP_PORT` | `mcp.port` | HTTP 监听端口 |
| `MCP_AUTH_ENABLED` | `mcp.auth.enabled` | 是否启用认证 |
| `MCP_API_KEYS` | `mcp.auth.api_keys` | 逗号分隔的 API Key 列表 |

### 传输模式选择

| 模式 | 适用场景 | 说明 |
|------|---------|------|
| `stdio` | 本地 CLI、编辑器插件 | 客户端将 MCP Server 作为子进程启动，通过 stdin/stdout 通信 |
| `sse` | 远程连接（推荐） | HTTP SSE + POST，兼容性最好，支持大部分 MCP Client |
| `streamable-http` | 远程连接（新标准） | MCP 2025-11-25 新传输协议，单一端点 |

### 工具配置（tools）

`tools` 列表定义了 MCP Server 暴露哪些 skill 能力。每项包含：

- `skill`：skill 目录名称（对应 `src/skills/` 下的 skill）
- `commands`：要暴露的命令列表，每个命令对应 skill `scripts/` 目录下的同名 Python 脚本

示例：skill `trade-customer` 的目录结构为：

```
src/skills/trade-customer-1.0.0/
  ├── SKILL.md
  └── scripts/
      ├── save_customer.py      → 命令名: save-customer
      └── search_customers.py   → 命令名: search-customers
```

配置为：
```yaml
tools:
  - skill: trade-customer
    commands: [save-customer, search-customers]
```

命令名使用连字符（`save-customer`），匹配脚本文件名（`save_customer.py`）。系统会自动查找并映射。

## 认证流程

### API Key 认证

1. 在配置中设置 API Key：
   ```yaml
   mcp:
     auth:
       enabled: true
       api_keys:
         - "sk-mcp-your-secret-key-1"
         - "sk-mcp-your-secret-key-2"
   ```
   或通过环境变量：
   ```bash
   export MCP_API_KEYS="sk-mcp-key1,sk-mcp-key2"
   ```

2. 客户端在连接时需要提供 API Key（具体方式取决于客户端实现）

3. 如果 `api_keys` 为空且 `auth.enabled` 为 true，认证实际上被跳过（允许所有连接）

### 租户隔离

MCP 工具调用可传递 `tenant_id` 参数，所有 skill 脚本执行会自动携带租户上下文，确保数据隔离。

## 客户端连接示例

### Claude Desktop（stdio 模式）

编辑 `claude_desktop_config.json`（路径取决于操作系统）：

**macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
**Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "aid-work-agent": {
      "command": "python",
      "args": ["-m", "src.mcp.server"],
      "cwd": "/path/to/aid-work-agent",
      "env": {
        "MCP_ENABLED": "true",
        "MCP_TRANSPORT": "stdio"
      }
    }
  }
}
```

### Cursor（stdio 模式）

在 Cursor 设置中添加 MCP Server：

```json
{
  "mcpServers": {
    "aid-work-agent": {
      "command": "python",
      "args": ["-m", "src.mcp.server"],
      "cwd": "/path/to/aid-work-agent",
      "env": {
        "MCP_ENABLED": "true",
        "MCP_TRANSPORT": "stdio"
      }
    }
  }
}
```

### 自定义 Python MCP Client（SSE 模式）

使用 `mcp` Python SDK 连接：

```python
import asyncio
from mcp.client import Client
from mcp.client.sse import sse_client

async def main():
    async with sse_client("http://localhost:8765/sse") as (read, write):
        async with Client(read, write) as client:
            # 列出可用工具
            tools = await client.list_tools()
            print(f"可用工具: {[t.name for t in tools]}")

            # 调用工具
            result = await client.call_tool(
                "search",
                {"params": '{"query": "测试搜索"}'}
            )
            print(f"结果: {result}")

asyncio.run(main())
```

### cURL 测试（SSE 模式）

发送 JSON-RPC 请求：

```bash
# 初始化连接
curl -X POST http://localhost:8765/messages \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
      "protocolVersion": "2024-11-05",
      "capabilities": {},
      "clientInfo": {"name": "test-client", "version": "1.0.0"}
    }
  }'

# 列出工具
curl -X POST http://localhost:8765/messages \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 2,
    "method": "tools/list",
    "params": {}
  }'

# 调用工具
curl -X POST http://localhost:8765/messages \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 3,
    "method": "tools/call",
    "params": {
      "name": "search",
      "arguments": {"params": "{\"query\": \"测试\"}"}
    }
  }'
```

## 快速开始

### 最小化配置

1. 编辑 `configs/config.yaml`，添加要暴露的 skill 工具：
   ```yaml
   mcp:
     enabled: true
     transport: sse
     tools:
       - skill: baidu-search
         commands: [search]
   ```

2. 启动 MCP Server：
   ```bash
   python -m src.mcp.server
   ```

3. 验证服务运行：
   ```bash
   curl http://localhost:8765/messages \
     -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0.0"}}}'
   ```

## 故障排查

| 问题 | 排查方法 |
|------|---------|
| Server 启动后立即退出 | 检查 `mcp.enabled` 是否为 `true` |
| 工具列表为空 | 检查 `mcp.tools` 配置的 skill 名称是否正确（与 `src/skills/` 目录名匹配） |
| 工具调用返回"未找到脚本" | 检查 `commands` 名称与 `scripts/` 下文件名是否匹配（连字符对应下划线） |
| 连接超时 | 检查防火墙是否放行配置的端口（默认 8765） |
| 日志查看 | MCP Server 日志输出到 stderr，使用 `loguru` 记录 |

## 架构说明

### 模块结构

```
src/mcp/
  ├── __init__.py      # 模块入口
  ├── __main__.py      # python -m src.mcp 入口
  ├── config.py        # 配置加载（从 config.yaml + 环境变量）
  ├── tools.py         # MCP Tool 注册表（从 skill 脚本自动生成 schema）
  ├── executor.py      # Tool 执行器（转发到 SkillExecutor + 认证）
  └── server.py        # FastMCP Server 主类（组合所有组件）
```

### 依赖关系

```
server.py
  ├── config.py        → MCPConfig（配置）
  ├── tools.py         → MCPToolRegistry（工具发现与注册）
  ├── executor.py      → MCPExecutor（工具执行 + 认证）
  │     └── tools.py   → MCPToolDefinition
  │     └── skill_executor.py → SkillExecutor（现有系统）
  └── skill_registry.py → skill_registry（现有系统）
```

### 与主系统的关系

MCP Server 是独立进程，与主 FastAPI Web 服务解耦。两者共享：
- `configs/config.yaml` 配置
- `src/skills/` skill 定义和脚本
- `src/core/skill_executor.py` 执行引擎
- `src/core/skill_registry.py` 工具注册表

MCP Server 不依赖数据库连接，通过 skill 脚本间接访问数据库。
