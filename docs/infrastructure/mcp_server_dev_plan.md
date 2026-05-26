# MCP Server 基础设施 — 开发计划

## Context

订单处理智能体需要对外暴露 MCP Server，让外部 AI 智能体和工具通过标准 MCP 协议调用系统内能力。MCP Server 是**通用基础设施**，不限于订单处理——未来任何子智能体都可以通过 MCP 暴露能力。

因此将 MCP Server 作为独立基础设施项目开发，与具体子智能体业务解耦。

## 设计要点

- MCP Server 作为独立进程运行（`python -m src.mcp.server`）
- 支持 stdio（本地 CLI/编辑器集成）和 SSE（远程 HTTP）两种传输模式
- 转发到现有 skill 脚本执行，不重复实现业务逻辑
- API Key 认证 + 租户隔离
- 可配置暴露哪些 skill/tool

## 开发任务

### Phase 1: MCP 协议核心（2 天）— 使用 FastMCP SDK 简化

- [x] **T1.1**: 创建 `src/mcp/__init__.py` 模块
- [x] **T1.2**: ~~实现 `src/mcp/protocol.py`~~ → 使用 `mcp` SDK（FastMCP），无需手动实现协议
- [x] **T1.3**: 实现 `src/mcp/config.py` — 配置管理
  - 从 `configs/config.yaml` 或环境变量读取 MCP 配置
  - 支持配置暴露的 skill 列表、认证密钥、端口等
- [x] **T1.4**: 编写单元测试 `tests/unit/test_mcp.py`

### Phase 2: 传输层（1.5 天）— 使用 FastMCP SDK 内置传输

- [x] **T2.1**: ~~实现 `src/mcp/transport.py`~~ → FastMCP SDK 内置 stdio / SSE / streamable-http
- [x] **T2.2**: ~~实现 SSE 模式的 HTTP 服务~~ → FastMCP SDK 处理，配置 transport 参数即可
- [x] **T2.3**: 传输层测试（由 SDK 覆盖）

### Phase 3: Tool 注册与执行（2 天）

- [x] **T3.1**: 实现 `src/mcp/tools.py` — MCP Tool 定义与注册
  - 从 skill 脚本的 CLI 命令自动生成 MCP tool schema
  - Tool schema 符合 MCP 规范（name, description, inputSchema）
- [x] **T3.2**: 实现 `src/mcp/executor.py` — Tool 调用执行
  - 解析 MCP tool call 参数
  - 转换为 skill_execute CLI 命令
  - 调用 `SkillExecutor.execute_skill_command()` 执行 skill 脚本
  - 捕获输出并返回 MCP 格式结果
- [x] **T3.3**: 实现认证中间件
  - API Key 验证（常量时间比较，防时序攻击）
  - 租户绑定：MCP 调用传递 tenant_id，自动隔离
- [x] **T3.4**: 编写执行层测试

### Phase 4: Server 主类与启动（1 天）

- [x] **T4.1**: 实现 `src/mcp/server.py` — MCP Server 主类
  - 组合 FastMCP + ToolRegistry + Executor
  - `if __name__ == "__main__"` 入口支持 `python -m src.mcp.server`
  - 支持 `python -m src.mcp` 入口
- [x] **T4.2**: 添加到 `configs/config.yaml` 的 MCP 配置段
  ```yaml
  mcp:
    enabled: ${MCP_ENABLED:-false}
    transport: ${MCP_TRANSPORT:-sse}   # stdio | sse | streamable-http
    host: ${MCP_HOST:-0.0.0.0}
    port: ${MCP_PORT:-8765}
    auth:
      enabled: ${MCP_AUTH_ENABLED:-true}
      api_keys: []                     # 或通过环境变量 MCP_API_KEYS 设置
    tools: []                          # 暴露的 skill/tool 列表
  ```
- [x] **T4.3**: 集成测试（单元测试已在 `tests/unit/test_mcp.py` 中覆盖核心逻辑）

### Phase 5: 文档与示例（0.5 天）

- [x] **T5.1**: 编写 MCP Server 使用文档 `docs/infrastructure/mcp_server.md`
  - 启动方式、配置说明、认证流程
  - 示例：用 Claude Desktop / Cursor / 自定义 MCP Client 连接
- [x] **T5.2**: 提供 `storage/mcp-examples/` 示例配置
  - `claude_desktop_config.json` — Claude Desktop stdio 配置
  - `cursor_mcp_config.json` — Cursor stdio 配置
  - `client_sse_example.py` — Python SSE 客户端示例
  - `client_stdio_example.py` — Python stdio 客户端示例

## 文件清单

| 文件 | 用途 | 状态 |
|------|------|------|
| `src/mcp/__init__.py` | 模块入口 | ✅ 已创建 |
| `src/mcp/__main__.py` | `python -m src.mcp` 入口 | ✅ 已创建 |
| `src/mcp/config.py` | 配置管理 | ✅ 已创建 |
| `src/mcp/tools.py` | Tool 注册与 schema 生成 | ✅ 已创建 |
| `src/mcp/executor.py` | Tool 调用执行（转发到 skill 脚本） | ✅ 已创建 |
| `src/mcp/server.py` | Server 主类与启动入口 | ✅ 已创建 |
| ~~`src/mcp/protocol.py`~~ | ~~MCP 协议消息处理~~ | 已由 FastMCP SDK 替代 |
| ~~`src/mcp/transport.py`~~ | ~~stdio + SSE 传输层~~ | 已由 FastMCP SDK 替代 |
| `tests/unit/test_mcp.py` | 单元测试 | ✅ 已创建 |
| `configs/config.yaml` | MCP 配置段 | ✅ 已添加 |
| `docs/infrastructure/mcp_server.md` | 使用文档 | ✅ 已创建 |
| `storage/mcp-examples/*` | 客户端配置与示例 | ✅ 已创建 |

## 依赖

- `mcp>=1.27.0` — MCP Python SDK（FastMCP）
- 现有 `src/skills/` 技能系统（skill_execute 执行路径）
- 现有 `src/core/skill_executor.py`（SkillExecutor 命令执行）
- 现有 `src/core/skill_registry.py`（SkillRegistry 工具发现）
- 现有 `configs/config.yaml`（配置加载）

## 总工期：约 7 天 → 实际约 3 天（使用 FastMCP SDK 简化了协议层和传输层）

## 参考资源

- MCP 协议规范：https://spec.modelcontextprotocol.io/
- 现有 skill 执行路径：`src/tools/skill/skill_execute_tool.py`
