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

## 数据库安全入口

- 普通pytest未明确提供`DATABASE_URL`时默认无数据库模式，实际PostgreSQL连接被测试guard拒绝；不要依赖业务`.env`或默认共享库。纯单测应mock数据库。集成SQL用例必须显式使用本机任务专属`aid_test_*`库。
- 推荐真实SQL入口：为`./scripts/dev_test.sh --isolated-db <相关测试>`明确提供本机专属PostgreSQL管理连接（管理库名为`postgres`）；脚本创建随机`aid_test_*`库，将业务与追踪连接都指向它，结束只删除自己创建的库。不能用业务库作为管理入口，URL不接受query/service/hostaddr重定向。
- 测试启动不直接加载业务`.env`；显式非法或共享库URL会在产品模块初始化前拒绝。不要增加`allow-unsafe`绕过，不要为测试读取或排查真实业务库。连接错误与验证输出不得包含DSN、密码或令牌。
- Docker自动模式在测试/helper执行前核对当前工作区的三个测试安全入口；旧容器代码不一致时拒绝执行，不上传文件或重启容器。普通容器测试为无DB模式，SQL测试用显式`--isolated-db`；容器的loopback指容器自身，不能假定宿主机的测试PG会在容器内同端口可达。无法使用匹配容器时，在当前工作区已确认的虚拟环境运行等价pytest/隔离脚本。
- 清理仅在明确隔离库模式执行，并先核对实际`current_database()`与本轮目标一致；收集测试不启动清理。独立测试需用驱动底层tripwire证明零connect/零DELETE，单独禁止Python socket不能证明C/libpq没有发起连接。

详细设计与执行证据见[测试隔离计划](../../docs/plans/plan-test-database-isolation-runtime-assertions.md)。该策略仅作用于测试入口，不改变产品数据库连接配置；不宣称隔离任意恶意Python代码。

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
- `real_browser` — 依赖真实浏览器/Node renderer 的测试（如 PPT HTML 导出、x_to_image 渲染器）。极慢且容器内可能 hang，**默认被 pytest.ini addopts 排除**；单独运行用 `pytest -m "not e2e and real_browser"`。新写此类测试必须在文件级 `pytestmark` 中加 `pytest.mark.real_browser`

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

## 已部署前端 E2E 验证：登录态注入（免验证码）

**问题**：登录接口需要图形验证码 + IP 速率限制，浏览器自动化（Playwright 等）走 UI 登录会卡在验证码。验证已部署页面行为时，不值得为绕验证码做工程。

**方案**：测试环境直接为目标用户铸短期 token 写入 `tokens` 表，再向浏览器 localStorage 注入对应 key，跳过登录页。前端 `useTenantAuth.init()` 只读 token key，随后调 `/api/saas/auth/me` 拉取用户/租户信息，注入一个 key 即完成登录态恢复。

### 步骤（以测试环境 agent2 为例）

1. **铸 token**（测试库，选目标用户如租户管理员）：

   ```sql
   INSERT INTO tokens (token, user_id, expires_at)
   VALUES ('e2e_<随机串>', '<user_id>', NOW() + interval '2 hours');
   ```

2. **注入 localStorage**：先打开站点任意页面（建立同 origin），再设 key、后跳转目标路由：

   | 目标 | localStorage key |
   |------|-----------------|
   | 租户前台 `/t/{tenant_id}` | `saas_token_{safe_tenant_id}`（safeId = tenant_id 中非 `[a-zA-Z0-9_-]` 字符替换为 `_`） |
   | 平台管理后台 `/portal` | `portal_token` |
   | 其他路径（桌面端等） | `saas_token` |

3. **跳转目标页面**正常操作验证。
4. **清理**：`DELETE FROM tokens WHERE token='e2e_<随机串>';`，验证产生的测试数据（会话、配置变更）一并删除/还原。

### 代码位置（key 解析与校验）

- key 解析：`frontend/web/composables/useTenantAuth.ts` 的 `getTokenKey()` / `resolveLoginKey()`
- 存储后端：`frontend/web/platform/credentialStore.ts`（浏览器上下文无 `window.agentDesktop` 时直接读写 localStorage）
- token 校验：`src/services/auth_service.py` `verify_token()`（Redis 缓存优先、DB 兜底、滑动续期）

### 注意

- **仅测试环境**使用，生产禁止铸 token
- 该方法用于「已部署页面的行为验证」（点检 UI 交互、验收待部署功能）；单元/集成测试仍按分层规则用 mock，不要用真页面代替
- Playwright 截图偶发复用缓存图（同 URL 幂等去重），需要留视觉证据时对关键状态连续截图并核对本地文件内容，勿只信返回的图片 URL

## 注意事项

- **导入 `src.core.*` 会触发 `master_agent` 单例创建**。`tests/conftest.py` 已 mock `VectorDBSQLite` 解决此问题。如果遇到新的导入链问题，在 `conftest.py` 中添加 mock。
- **异步测试**使用 `@pytest.mark.asyncio`，`pytest.ini` 中 `asyncio_mode = auto` 已全局启用。
- 旧根级测试文件（`tests/test_*.py`，24 个脚本式遗留）已于 2026-09-03 清理删除，conftest 的 `collect_ignore` 排除逻辑一并移除；新测试统一放 `tests/unit/`、`tests/integration/`、`tests/e2e/`。

## 运行测试

```bash
pytest                                    # 运行全部（默认跳过 e2e 与 real_browser）
pytest tests/unit/                        # 只跑单元测试
pytest tests/integration/                 # 只跑集成测试
pytest -m tools                           # 按组件：tools / skills / agent / email / browser / search
pytest -m e2e                             # 端到端测试（需要真实凭证）
pytest -m "not e2e and real_browser"      # 单独跑真实浏览器测试（慢，勿在日常回归中混跑）
pytest --cov=src --cov-report=term-missing  # 带覆盖率
pytest -m "integration and tools"         # 组合筛选
```
