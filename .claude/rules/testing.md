# 测试指南

## 测试目录结构

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

## 测试分层规则

| 层级 | 位置 | 原则 | Mock 范围 |
|------|------|------|-----------|
| **单元** | `tests/unit/` | 测试单个函数/类，不依赖外部服务 | Mock 所有外部调用（LLM API、数据库、网络、文件系统） |
| **集成** | `tests/integration/` | 测试组件间协作，验证真实交互 | 只 mock 最外层边界（LLM API 响应、SMTP），内部组件用真实实例 |
| **E2E** | `tests/e2e/` | 测试完整用户流程，需要真实凭证 | 不 mock，真实 API 调用，通过 `conftest.py` 自动 skip 无凭证的测试 |

## 测试流程

```
需求分析 → 编写测试 → 审核通过 → 运行测试(红灯) → 实现代码 → 测试通过(绿灯) → 重构 → 重复
```

| 项目 | 要求 |
|------|------|
| 命名 | `test_<场景>_<预期结果>` |
| 覆盖率 | 目标 100% |
| 覆盖范围 | 正常路径、边界条件、异常路径 |

## 为新功能编写测试

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

## 可用 Markers

`pytest -m <marker>` 选择测试类型：
- `unit` / `integration` / `e2e` — 按层级
- `tools` / `skills` / `agent` / `channels` / `api` — 按组件
- `email` / `browser` / `search` / `llm` / `db` — 按外部依赖
- `slow` — 耗时 > 5 秒

## 共享 Fixtures（tests/conftest.py）

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

## 注意事项

- **导入 `src.core.*` 会触发 `master_agent` 单例创建**。`tests/conftest.py` 已 mock `VectorDBSQLite` 解决此问题。如果遇到新的导入链问题，在 `conftest.py` 中添加 mock。
- **异步测试**使用 `@pytest.mark.asyncio`，`pytest.ini` 中 `asyncio_mode = auto` 已全局启用。
- **旧测试文件**（`tests/test_*.py`）已迁移到子目录，通过 `collect_ignore` 跳过收集，可后续清理删除。

## 运行测试

```bash
pytest                                    # 运行全部（默认跳过 e2e）
pytest tests/unit/                        # 只跑单元测试
pytest tests/integration/                 # 只跑集成测试
pytest -m tools                           # 按组件：tools / skills / agent / email / browser / search
pytest -m e2e                             # 端到端测试（需要真实凭证）
pytest --cov=src --cov-report=term-missing  # 带覆盖率
pytest -m "integration and tools"         # 组合筛选
```