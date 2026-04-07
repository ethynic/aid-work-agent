# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AID Work Agent is an enterprise intelligent agent system (企业员工智能代理系统) built for handling daily enterprise employee tasks via conversational AI. It uses Chinese domestic LLM providers (Qwen/DashScope and ZhipuAI) and supports multi-channel access (WeCom, DingTalk, Feishu). The system is multi-tenant and supports hundreds of concurrent users.

**Design principles**: Stability and predictability over creativity. Professional, concise responses. When uncertain, honestly admit it. No humor, no entertainment, no academic speculation. Sensitive information must be encrypted and never returned in plaintext to users.

## Commands

### Backend
```bash
pip install -r requirements.txt          # Install dependencies
python -m src.main                       # Start FastAPI server (port 8000)
CLI_MODE=true python -m src.main         # CLI chat mode (no web server)
python gradio_app.py                     # Gradio debug UI (port 7860)
gunicorn -c deploy/gunicorn.conf.py src.main:app  # Production server
```

### Frontend
```bash
cd frontend && npm install               # Install frontend deps
cd frontend && npm run dev               # Dev server (port 5173)
cd frontend && npm run build             # Production build
```

### Testing
```bash
# 后端（pytest）
pytest                                    # 运行全部（默认跳过 e2e）
pytest tests/unit/                        # 只跑单元测试
pytest tests/integration/                 # 只跑集成测试
pytest -m tools                           # 按组件：tools / skills / agent / email / browser / search
pytest -m e2e                             # 端到端测试（需要真实凭证）
pytest --cov=src --cov-report=term-missing  # 带覆盖率
pytest -m "integration and tools"         # 组合筛选

# 前端（Vitest）
cd frontend && npm test                   # 运行前端测试
cd frontend && npm run test:coverage      # 带覆盖率
cd frontend && npm run test:watch         # 监听模式
```

### Docker
```bash
docker compose up -d                     # Dev environment
docker compose -f docker-compose.prod.yml up -d --build  # Production
```

## Architecture

### Request Flow
```
User/Channel (WeCom, DingTalk, Feishu, Web)
  → FastAPI (src/main.py) — HTTP routes, SSE streaming
    → Master Agent (src/core/agent.py) — LLM agent loop (max 20 iterations)
      → LLM Gateway (src/llm/gateway.py) + Tool Registry (src/tools/)
        → SubAgent Executor / Skill Executor / Direct Tool Execution
```

### Core Components

**Agent** (`src/core/agent.py`): The brain. A single `Agent` class serves as both master and sub-agent (via `is_master` flag). The `master_agent` singleton is exported from `src/core/__init__.py`. The agent loop calls LLM, executes tools, accumulates results, and pushes progress events via callbacks.

**Tool System** (`src/tools/`): Tools inherit `BaseTool` and implement `async execute(args)`. **Critical**: Tool schemas for LLM function calling are defined in `AGENT_TOOLS` list at the top of `agent.py`, which is SEPARATE from `ToolRegistry` implementations. Adding a tool requires updating both places: the schema in `AGENT_TOOLS` and registration in `Agent._register_builtin_tools()`.

**Skill System** (`src/core/skill_*.py` + `src/skills/`): Domain knowledge extension packages stored as directories with `SKILL.md` files (YAML frontmatter + Markdown). `SkillRegistry` discovers and indexes skills. Skills are loaded into context when the LLM calls `use_skill`, and executed via `skill_execute`. Skills support auto-matching by file extension.

**SubAgent System** (`subagents/` + `src/subagents/`): Defined in `subagents/<name>/SUBAGENT.md` (YAML + Markdown). The master agent delegates tasks via `delegate_to_subagent`. `SubagentExecutor` instantiates a child `Agent(is_master=False)` and runs it in a separate thread.

**LLM Gateway** (`src/llm/gateway.py`): Unified interface to `qwen` (DashScope) and `zhipu` (ZhipuAI) providers. All calls go through `chat_with_tools()`. Provider is switchable via `llm.provider` in `configs/config.yaml`. `KeyPool` manages multiple API keys with semaphore-based concurrency control.

**Channel System** (`src/channels/`): Each channel (WeCom, DingTalk, Feishu) extends `ChannelAdapter`. `ChannelManager` dispatches messages. Enabled/disabled via `configs/config.yaml` per-channel `enabled` flag.

**Memory** (`src/memory/short_term.py`): `ShortTermMemory` uses a deque-based sliding window per session_id, with configurable max_messages and TTL.

**Knowledge Base** (`src/knowledge/`): RAG pipeline with parsers → chunker → embedding (via LLM gateway) → sqlite-vec vector DB → hybrid retriever.

**Database** (`src/db/`): Default SQLite (`aid_work_agent.db`), configurable via `DATABASE_URL` env var to PostgreSQL or MySQL. Used for session persistence and auth.

### Key Entry Points

- `src/main.py` — FastAPI app with all HTTP routes (chat, SSE stream, upload, channel callbacks)
- `gradio_app.py` — Standalone debug UI that directly imports `master_agent`, bypassing FastAPI
- `v4_skills_agent.py` — Standalone Claude/Anthropic API demo, not part of the main system

### Configuration

Configuration loads in layers (later overrides earlier):
1. `configs/config.yaml` — Base YAML config with `${ENV_VAR}` placeholders
2. `.env` file — Environment variables
3. OS environment variables — Direct overrides

Key env vars: `LLM_PROVIDER` (qwen|zhipu), `QWEN_API_KEYS`, `ZHIPU_API_KEYS`, `DATABASE_URL`, `TAVILY_API_KEY`, channel configs (`WECOM_*`, `DINGTALK_*`, `FEISHU_*`), `SMTP_*`/`IMAP_*` for email.

## Extension Points

### Adding a New Tool
1. Create tool class in `src/tools/<category>/`, inherit `BaseTool`
2. Define Pydantic `InputModel` for parameter validation (with Chinese Field descriptions)
3. Set `name`, `description`, `display_name`, `InputModel` on the class
4. Implement `async execute(self, **kwargs) -> Dict[str, Any]`
5. Optionally override `get_display_name()` for dynamic display names
6. Register in `Agent._register_builtin_tools()`

**Schema 来源**：每个工具类通过 `InputModel`（Pydantic BaseModel）或 `parameters_schema` 定义参数 schema，`ToolRegistry.get_tool_definitions()` 自动收集。不再需要手动维护 `schemas.py`。

### Adding a New Skill
Create directory `src/skills/<name>-<version>/` with a `SKILL.md` file (see existing skills for format). Auto-loaded on restart.

### Adding a New SubAgent
Create `subagents/<name>/SUBAGENT.md` with YAML frontmatter (name, description, capabilities, triggers, tools, skills.allowed) + Markdown body. Auto-loaded on restart.

**SUBAGENT.md 格式规范**：
```
---
name: 智能体中文名
description: 一行描述
version: 1.0.0
author: system
capabilities: [...]
triggers:
  file_patterns: [...]
  keywords: [...]
tools:
  inherit: true
skills:
  allowed: [...]
context:
  max_input_tokens: 8000
  max_output_tokens: 4000
---

（此处为 Markdown body，将作为 system_prompt 使用）
```

**关键陷阱 — system_prompt 必须放在 body 中，不能放在 YAML frontmatter 里**：
- Loader 用正则 `^---\s*\n(.*?)\n---\s*\n(.*)$` 分割 frontmatter 和 body，要求第二个 `---` 必须**顶格**（无缩进）
- 如果 `system_prompt: |` 写在 YAML 中且内容包含 `---` 水平线（Markdown 分隔符），即使这些 `---` 有缩进，也很容易导致 frontmatter 没有闭合的顶格 `---`，正则匹配失败，子智能体不会被加载（静默失败，fallback 到 master agent）
- 正确做法：**不要在 frontmatter 中定义 system_prompt**，把系统提示词写在闭合 `---` 之后的 body 中。Loader 代码 `frontmatter.get("system_prompt", body.strip())` 会自动使用 body 作为 system_prompt
- URL 路由匹配：`/chat/<目录名>` 通过 `dir_name` 字段匹配（例如 `/chat/contract-archive-review` 匹配 `subagents/contract-archive-review/`）

### Adding a New Channel
Extend `src/channels/base.py` `ChannelAdapter`, implement `parse_message`/`send_message`/`verify_signature`, register in `src/main.py` lifespan.

## Testing

### 测试目录结构

```
tests/
├── conftest.py              # 根级共享 fixtures（mock 工厂、VectorDBSQLite mock）
├── fixtures/                # 测试数据（示例 SKILL.md、subagent YAML）
├── unit/                    # 单元测试 — 纯逻辑，全 mock，无外部调用
│   ├── conftest.py          # autouse 隔离 fixture
│   ├── test_models.py       # 数据模型
│   ├── test_memory.py       # ShortTermMemory
│   ├── test_tool_registry.py # ToolRegistry + ToolExecutor
│   ├── test_skill_*.py      # Skill 系统
│   ├── test_subagent_*.py   # SubAgent 系统
│   ├── test_auth.py         # 认证逻辑
│   └── tools/               # 各工具单元测试（mock 外部依赖）
│       ├── test_email_tool.py
│       ├── test_search_tool.py
│       └── test_browser_tool.py
├── integration/             # 集成测试 — 真实组件组合，选择性 mock
│   ├── test_agent_loop.py   # Agent 消息处理循环
│   ├── test_tool_execution.py # ToolExecutor + 真实 Tool
│   ├── test_skill_flow.py   # Skill 加载→匹配→执行
│   └── test_memory_to_llm.py # Memory→LLM 消息格式全链路
└── e2e/                     # 端到端测试 — 需要真实服务和凭证
    ├── test_llm_real.py     # 真实 LLM API 调用
    ├── test_email_real.py   # 真实 SMTP/IMAP
    └── test_agent_full.py   # 完整 Agent 交互

frontend/src/__tests__/      # 前端测试（Vitest + Vue Test Utils + MSW）
├── setup.ts                 # jsdom 环境 + MSW 启动
├── mocks/                   # API mock（handlers.ts、server.ts、fixtures.ts）
├── composables/             # Composable 逻辑测试
├── api/                     # API 层测试
└── components/              # 组件测试
```

### 测试分层规则

| 层级 | 位置 | 原则 | Mock 范围 |
|------|------|------|-----------|
| **单元** | `tests/unit/` | 测试单个函数/类，不依赖外部服务 | Mock 所有外部调用（LLM API、数据库、网络、文件系统） |
| **集成** | `tests/integration/` | 测试组件间协作，验证真实交互 | 只 mock 最外层边界（LLM API 响应、SMTP），内部组件用真实实例 |
| **E2E** | `tests/e2e/` | 测试完整用户流程，需要真实凭证 | 不 mock，真实 API 调用，通过 `conftest.py` 自动 skip 无凭证的测试 |

### 为新功能编写测试

**新增 Tool 时**，在 `tests/unit/tools/` 添加 `test_<tool_name>.py`：
```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

pytestmark = pytest.mark.tools  # 按需加 pytest.mark.email / .browser / .search

class TestMyTool:
    def test_tool_definition(self):
        from src.tools.my_category.my_tool import MyTool
        tool = MyTool()
        assert tool.name == "my_tool"
        defn = tool.to_tool_definition()
        assert "input_schema" in defn

    @pytest.mark.asyncio
    async def test_execute_success(self):
        with patch("src.tools.my_category.my_tool.external_api") as mock_api:
            mock_api.return_value = {"data": "mocked"}
            tool = MyTool()
            result = await tool.execute(param1="value")
            assert result["success"] is True

    @pytest.mark.asyncio
    async def test_execute_missing_params(self):
        tool = MyTool()
        result = await tool.execute()
        assert result["success"] is False
```

**新增 Skill 时**，无需单独测试文件（Skill 由 `test_skill_flow.py` 集成覆盖）。如需测试 SKILL.md 解析，在 `tests/fixtures/skills/` 添加示例文件并在 `test_skill_loader.py` 补充用例。

**新增 API 路由时**，在 `tests/integration/` 添加测试，使用 FastAPI `TestClient`：
```python
pytestmark = pytest.mark.api

@pytest.mark.asyncio
async def test_my_route():
    from fastapi.testclient import TestClient
    from src.main import app
    client = TestClient(app)
    response = client.get("/api/my-route")
    assert response.status_code == 200
```

**前端组件/Composable 测试**，在 `frontend/src/__tests__/` 对应目录添加 `.test.ts` 文件。API 请求由 MSW 自动拦截（配置在 `mocks/handlers.ts`）。

### 可用 Markers

`pytest -m <marker>` 选择测试类型：
- `unit` / `integration` / `e2e` — 按层级
- `tools` / `skills` / `agent` / `channels` / `api` — 按组件
- `email` / `browser` / `search` / `llm` / `db` — 按外部依赖
- `slow` — 耗时 > 5 秒

### 共享 Fixtures（tests/conftest.py）

| Fixture | 说明 |
|---------|------|
| `mock_llm_gateway` | 完全 mock 的 LLM Gateway（chat/stream/chat_with_tools） |
| `mock_llm_response()` | 工厂：快速构建 canned LLM 响应 |
| `mock_tool()` | 工厂：创建 mock BaseTool |
| `memory` | 新鲜的 ShortTermMemory |
| `tool_registry` | 空 ToolRegistry |
| `tool_executor` | ToolExecutor + 空 registry |
| `user_email_config` | 测试邮箱配置 |
| `skills_dir` | 临时目录含示例 SKILL.md |
| `plans_dir` | 临时计划目录 |

### 注意事项

- **导入 `src.core.*` 会触发 `master_agent` 单例创建**。`tests/conftest.py` 已 mock `VectorDBSQLite` 解决此问题。如果遇到新的导入链问题，在 `conftest.py` 中添加 mock。
- **异步测试**使用 `@pytest.mark.asyncio`，`pytest.ini` 中 `asyncio_mode = auto` 已全局启用。
- **旧测试文件**（`tests/test_*.py`）已迁移到子目录，通过 `collect_ignore` 跳过收集，可后续清理删除。

## Language Note

The codebase uses mixed languages: README, comments, and prompts are primarily Chinese (Simplified). Code identifiers are English. UI text is Chinese.
