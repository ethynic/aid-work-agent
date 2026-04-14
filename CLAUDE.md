# CLAUDE.md

本文档为 Claude Code (claude.ai/code) 在本项目中工作时提供指导。

## 项目概览

AID Work Agent 是企业员工智能代理系统，通过对话式 AI 处理企业员工的日常任务。系统使用国内大模型提供商（Qwen/DashScope 和 ZhipuAI），支持多渠道接入（企业微信、钉钉、飞书）。系统支持多租户，可承载数百并发用户。

**设计原则**：稳定性和可预测性优先于创造力。专业、简洁的回复。不确定时诚实承认。不幽默、不娱乐、不学术猜测。敏感信息必须加密，绝不以明文形式返回给用户。

## 命令

### 后端
```bash
pip install -r requirements.txt          # 安装依赖
python -m src.main                       # 启动 FastAPI 服务器（端口 8000）
CLI_MODE=true python -m src.main         # CLI 聊天模式（不启动 Web 服务器）
python gradio_app.py                     # Gradio 调试 UI（端口 7860）
gunicorn -c deploy/gunicorn.conf.py src.main:app  # 生产环境服务器
```

### 测试
```bash
# 后端（pytest）
pytest                                    # 运行全部（默认跳过 e2e）
pytest tests/unit/                        # 只跑单元测试
pytest tests/integration/                 # 只跑集成测试
pytest -m tools                           # 按组件：tools / skills / agent / email / browser / search
pytest -m e2e                             # 端到端测试（需要真实凭证）
pytest --cov=src --cov-report=term-missing  # 带覆盖率
pytest -m "integration and tools"         # 组合筛选
```

### Docker
```bash
docker compose up -d                     # 开发环境
docker compose -f docker-compose.prod.yml up -d --build  # 生产环境
```

## 开发规范

### 后端开发规范

#### 日志规范
**本项目后端统一使用 `loguru` 作为日志库，禁止使用标准库 `logging`。**

```python
from loguru import logger

# 后端日志：复杂业务逻辑长期保留
logger.info('后端日志：开始处理用户请求')

# 后端日志：异常捕获
logger.error(f'后端日志：数据库连接失败: {e}', exc_info=True)

# 临时调试日志（bug 修复后删除）
logger.debug(f'临时调试：请求参数 {params}')
```

#### 错误处理规范
所有 API 错误响应必须包含 `debug` 字段，且必须过滤敏感信息：

```python
import re

SENSITIVE_PATTERNS = [
    r'password["\s:=]+\S+',
    r'api[_-]?key["\s:=]+\S+',
    r'token["\s:=]+\S+',
]

def sanitize_error_info(error_msg: str) -> str:
    for pattern in SENSITIVE_PATTERNS:
        error_msg = re.sub(pattern, lambda m: m.group(0).split('=')[0] + '=***', error_msg, flags=re.IGNORECASE)
    return error_msg

# 错误响应格式
return {
    "success": False,
    "error": "操作失败，请稍后重试",
    "debug": sanitize_error_info(str(e))
}
```

#### 异步/同步开发规范
**避免 `await` 调用同步方法导致的 `TypeError`**：

| 类型 | 定义 | 调用 |
|------|------|------|
| 同步 | `def method()` | `obj.method()` |
| 异步 | `async def method()` | `await obj.method()` |

异步路由调用同步服务时，使用 `asyncio.to_thread()`：
```python
import asyncio

@router.get("/data")
async def get_data():
    service = MyService()
    result = await asyncio.to_thread(service.sync_method)  # ✅ 正确
    return result
```

#### Gunicorn 多 Worker 进程内存隔离
**核心问题**：Gunicorn 启动多个 worker 进程时，每个 worker 拥有独立的 Python 内存空间。

| 方案 | 适用场景 |
|------|---------|
| 磁盘刷新 | 低频读操作（如管理后台配置读取） |
| Redis 共享缓存 | 高频读操作 |
| 数据库 | 持久化数据 |
| 单 worker | 开发/调试（`gunicorn --workers 1`） |

> **磁盘/数据库是共享的，内存是隔离的。** 任何依赖内存状态且跨请求的读写操作，都必须考虑多 worker 一致性。

#### API 接口命名规范
接口名称应与 Python 方法名保持一致，使用具体、有明确指向性的命名：

```python
# ✅ 正确
@router.post("/search_documents")
async def search_documents(request: SearchRequest):

# ❌ 错误
@router.post("/search")
async def search_documents(request: SearchRequest):
```

### Docker

```
需求分析 → 编写测试 → 审核通过 → 运行测试(红灯) → 实现代码 → 测试通过(绿灯) → 重构 → 重复
```

| 项目 | 要求 |
|------|------|
| 命名 | `test_<场景>_<预期结果>` |
| 覆盖率 | 目标 100% |
| 覆盖范围 | 正常路径、边界条件、异常路径 |

#### Git 提交规范
**不要自动提交代码，提交代码仅能由用户发起**
1. 提交前执行 `git fetch` 拉取远程最新代码
2. 检查是否有冲突：`git status` 或 `git diff origin/master`
3. 如有冲突先解决冲突再提交
4. 提交后立即 `git push` 推送到远程


## 架构

### 请求流程
```
用户/渠道（企业微信、钉钉、飞书、Web）
  → FastAPI (src/main.py) — HTTP 路由、SSE 流式输出
    → 主智能体 (src/core/agent.py) — 大模型智能体循环（最多 20 轮）
      → 大模型网关 (src/llm/gateway.py) + 工具注册表 (src/tools/)
        → 子智能体执行器 / 技能执行器 / 直接工具执行
```

### 核心组件

**智能体** (`src/core/agent.py`)：核心大脑。单个 `Agent` 类同时作为主智能体和子智能体（通过 `is_master` 标志区分）。`master_agent` 单例从 `src/core/__init__.py` 导出。智能体循环调用大模型、执行工具、累积结果，并通过回调推送进度事件。

**工具系统** (`src/tools/`)：工具继承 `BaseTool` 并实现 `async execute(args)`。**关键**：用于大模型函数调用的工具 schema 定义在 `agent.py` 顶部的 `AGENT_TOOLS` 列表中，与 `ToolRegistry` 实现是**分开的**。添加工具需要同时更新两处：`AGENT_TOOLS` 中的 schema 和 `Agent._register_builtin_tools()` 中的注册。

**技能系统** (`src/core/skill_*.py` + `src/skills/`)：领域知识扩展包，存储在带 `SKILL.md` 文件（YAML 头部 + Markdown）的目录中。`SkillRegistry` 发现并索引技能。当大模型调用 `use_skill` 时，技能被加载到上下文中，并通过 `skill_execute` 执行。技能支持按文件扩展名自动匹配。

**子智能体系统** (`subagents/` + `src/subagents/`)：在 `subagents/<name>/SUBAGENT.md` 中定义（YAML + Markdown）。主智能体通过 `delegate_to_subagent` 委托任务。`SubagentExecutor` 实例化一个子 `Agent(is_master=False)` 并在线程中运行。

**大模型网关** (`src/llm/gateway.py`)：统一接口到 `qwen`（DashScope）和 `zhipu`（ZhipuAI）提供商。所有调用都通过 `chat_with_tools()`。提供商可通过 `configs/config.yaml` 中的 `llm.provider` 切换。`KeyPool` 通过信号量控制并发，管理多个 API 密钥。

**渠道系统** (`src/channels/`)：每个渠道（企业微信、钉钉、飞书）继承 `ChannelAdapter`。`ChannelManager` 分发消息。渠道通过 `configs/config.yaml` 中的 `enabled` 标志启用/禁用。

**记忆** (`src/memory/short_term.py`)：`ShortTermMemory` 使用基于 deque 的滑动窗口，按 session_id 分隔，配置 max_messages 和 TTL。

**知识库** (`src/knowledge/`)：RAG 流程，包含解析器 → 分块器 → 嵌入（通过大模型网关）→ 向量数据库 → 混合检索器。

**数据库** (`src/db/`)：默认 PostgreSQL，可通过 `DATABASE_URL` 环境变量配置为 SQLite 或 MySQL。用于会话持久化和认证。

### 关键入口点

- `src/main.py` — FastAPI 应用，包含所有 HTTP 路由（聊天、SSE 流式上传、渠道回调）
- `gradio_app.py` — 独立调试 UI，直接导入 `master_agent`，绕过 FastAPI
- `v4_skills_agent.py` — 独立的 Claude/Anthropic API 演示，不属于主系统

### 配置加载

配置按以下层级加载（后者覆盖前者）：
1. `configs/config.yaml` — 基础 YAML 配置，支持 `${ENV_VAR}` 占位符
2. `.env` 文件 — 环境变量
3. OS 环境变量 — 直接覆盖

关键环境变量：`LLM_PROVIDER`（qwen|zhipu）、`QWEN_API_KEYS`、`ZHIPU_API_KEYS`、`DATABASE_URL`、`TAVILY_API_KEY`、渠道配置（`WECOM_*`、`DINGTALK_*`、`FEISHU_*`）、邮箱配置（`SMTP_*`/`IMAP_*`）。

## 扩展点

### 添加工具
1. 在 `src/tools/<category>/` 中创建工具类，继承 `BaseTool`
2. 定义 Pydantic `InputModel` 用于参数验证（带中文 Field 描述）
3. 在类上设置 `name`、`description`、`display_name`、`InputModel`
4. 实现 `async execute(self, **kwargs) -> Dict[str, Any]`
5. 可选择重写 `get_display_name()` 用于动态显示名称
6. 在 `Agent._register_builtin_tools()` 中注册

**Schema 来源**：每个工具类通过 `InputModel`（Pydantic BaseModel）或 `parameters_schema` 定义参数 schema，`ToolRegistry.get_tool_definitions()` 自动收集。不再需要手动维护 `schemas.py`。

### 添加技能
创建目录 `src/skills/<name>-<version>/`，包含 `SKILL.md` 文件（参考现有技能格式）。重启后自动加载。

### 添加子智能体
创建 `subagents/<name>/SUBAGENT.md`，包含 YAML 头部（name、description、capabilities、triggers、tools、skills.allowed）+ Markdown 正文。重启后自动加载。

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

### 添加渠道
继承 `src/channels/base.py` 的 `ChannelAdapter`，实现 `parse_message`/`send_message`/`verify_signature`，在 `src/main.py` 生命周期中注册。

## 测试指南

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

## 语言说明

本项目使用多种语言：README、注释和提示词主要为中文（简体）。代码标识符为英文。UI 文本为中文。
