# 连接中心架构设计

> **文档类型**：架构设计（系统级横切关注点）
> **状态**：📋 待开发
> **创建日期**：2026-07-30
> **触发场景**：现有「API 配置 + 环境变量」藏在管理后台「租户管理 - 编辑租户 - 数字员工授权」弹窗里，租户管理员要配 API Key 必须找平台管理员开权限进管理后台，体验割裂；同时缺少对标 Coze / Dify / n8n 的「连接器」抽象。
> **关联规范**：[backend_dev.md](../../.claude/rules/backend_dev.md) / [database_dev.md](../../.claude/rules/database_dev.md) / [cache_usage.md](cache_usage.md) / [file_usage.md](file_usage.md)
> **关联想法登记**：[ideas.md](../ideas.md) 系统功能 #48
> **关联开发计划**：[plan-connection-center.md](../plans/plan-connection-center.md)

---

## 0. 文档定位

本文档为整个系统定义「连接中心」--租户管理员自助管理 API 配置、环境变量、内置连接器、自定义连接器的统一入口。**对标行业标杆（Coze、Dify、n8n、Anthropic Claude）的「连接器」一级能力**，把分散在管理后台的配置能力搬到租户前台，并扩展出连接器抽象层。

### 设计原则

1. **搬迁而非改造**：现有「API 配置 + 环境变量」功能从管理后台**搬迁**到租户前台，原功能保留给平台管理员，后端 API 直接复用。
2. **加密与不加密双轨**：现有 `subagent_env_vars` 表暂不加密升级（避免破坏性变更），仅新建 `tenant_connector_configs` 表用 Fernet 加密。
3. **租户隔离优先**：所有 API 复用 `require_admin` + `get_current_tenant_id()`，租户管理员只能操作本租户数据。
4. **渐进式抽象**：Phase 1 搬迁 + 框架立起来，Phase 2-3 补内置连接器，Phase 4 自定义连接器（MCP/OpenAPI 双轨待调研后定）。
5. **现有功能不动**：`TenantMgmt.vue` 的环境变量/API 配置弹窗**保留**给平台管理员，不删除。

---

## 1. 行业调研结论（联网受限，关键数字需核实官方文档）

| 产品 | 内置连接器数 | 自定义方式 | MCP 支持 | 作用域 | 测试连接 |
|------|------------|-----------|---------|--------|---------|
| Coze/扣子 | 数百 | OpenAPI Schema 粘贴 | ✅ | Bot 级可团队共享 | ✅ |
| Dify | 30~50 | OpenAPI JSON 导入 | ✅ 双向 | workspace 级 | ✅ 每工具有「授权测试」 |
| 千帆 AppBuilder | ~30 | OpenAPI | ✅ | 应用级 | ✅ |
| n8n | 400+ | 节点配置 | ✅ MCP 节点 | workspace 级 | ✅ AES-256 加密 |
| Anthropic Claude | ~10 | MCP JSON | ✅ MCP+OAuth | 用户/workspace | ✅ |

**关键借鉴**：
- **作用域**：选 workspace 级（即租户级），租户管理员配置一次，全员可用--对齐 Dify 模式
- **加密**：Fernet AES-128（项目已有 `wecom_personal_rpa/secret_crypto.py` 可复用）
- **测试连接**：每个连接器提供「测试连接」按钮，调用最小请求验证
- **配置 schema**：JSON Schema 描述配置项，前端动态渲染表单（对齐 Dify/Coze）

---

## 2. 整体架构

### 2.1 用户入口

租户前台侧边栏新增「连接中心」一级菜单（仅租户管理员可见），路径 `/t/:tenant_id/connections`，单页面 4 Tab：

| Tab | 内容 | 数据来源 |
|-----|------|---------|
| 1. API 配置 | 数字员工 API 配置文件（Markdown）上传 | 复用 `tenant_config_file.py` |
| 2. 环境变量 | 数字员工环境变量（name/value/description） | 复用 `subagent_env_var.py` |
| 3. 内置连接器 | 12-15 个系统预置连接器卡片网格 + 配置弹窗 + 测试连接 | 新建 `connector_center.py` + `tenant_connector_configs` 表 |
| 4. 自定义连接器 | 用户自定义 MCP/OpenAPI 连接器列表 | Phase 4 实现 |

### 2.2 后端目录结构

```
src/
├── api/
│   ├── subagent_env_var.py         # 复用（Tab 2）
│   ├── tenant_config_file.py       # 复用（Tab 1）
│   └── connector_center.py        # 【新】连接器 API（Tab 3 + Tab 4）
├── db/
│   ├── subagent_env_var.py         # 复用
│   └── connector_config.py        # 【新】连接器配置 DB 层
├── core/
│   └── secret_crypto.py           # 【新】公共加密模块（抽取自 wecom_personal_rpa）
├── connectors/                    # 【新】内置连接器目录
│   ├── base.py                    # BaseConnector 基类 + ConnectorRegistry
│   ├── tavily.py
│   ├── email_smtp.py
│   ├── feishu.py
│   ├── dingtalk.py
│   ├── wecom.py
│   ├── qichacha.py
│   ├── amap.py
│   ├── weather.py
│   ├── baidu_translate.py
│   ├── web_fetch.py
│   ├── ocr.py
│   └── image_gen.py
└── mcp/
    └── client.py                  # 【新·Phase 4】MCP Client（待调研后定）
```

### 2.3 前端目录结构

```
frontend/src/
├── components/
│   ├── connections/               # 【新】
│   │   ├── ConnectionCenter.vue   # 主页面（4 Tab 容器）
│   │   ├── ApiConfigTab.vue        # Tab 1
│   │   ├── EnvVarsTab.vue          # Tab 2
│   │   ├── BuiltinConnectorsTab.vue # Tab 3
│   │   ├── CustomConnectorsTab.vue # Tab 4（Phase 4 完整实现）
│   │   └── ConnectorCard.vue      # 连接器卡片组件
│   └── MenuSidebar.vue             # 修改：加「连接中心」一级菜单
├── router/
│   └── agentRoutes.ts              # 修改：加路由
└── api/
    └── connections.ts              # 【新】连接器 API 客户端
```

### 2.4 数据库新表

```sql
CREATE TABLE IF NOT EXISTS tenant_connector_configs (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT,
    connector_type TEXT NOT NULL,         -- builtin / custom_mcp / custom_openapi
    connector_id TEXT NOT NULL,           -- 内置标识或自定义连接器 slug
    name TEXT NOT NULL,                   -- 显示名
    config_json TEXT,                     -- 非敏感配置 JSON（base_url、app_id 等）
    encrypted_secret TEXT,                -- 敏感字段加密（API Key、Secret 等）
    status TEXT DEFAULT 'untested',       -- active / error / untested / disabled
    last_tested_at TIMESTAMP,
    last_test_error TEXT,
    scope_subagents TEXT[],               -- 作用域：空数组=全部子智能体可用
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(tenant_id, connector_type, connector_id)
);
CREATE INDEX IF NOT EXISTS idx_tenant_connector_configs_tenant ON tenant_connector_configs(tenant_id);
```

**字段说明**：
- `connector_type`：枚举 `builtin`（内置）/ `custom_mcp`（自定义 MCP）/ `custom_openapi`（自定义 OpenAPI）
- `config_json`：非敏感配置项 JSON，如 `{"base_url": "https://api.tavily.com", "app_id": "xxx"}`
- `encrypted_secret`：敏感字段单独加密，存储 Fernet 密文（urlsafe base64 字符串）
- `status`：测试连接后的状态机，`untested`（未测）→ `active`（测通） / `error`（测失败）
- `scope_subagents`：作用域控制，空数组表示对所有子智能体可用；非空数组则只对列出的 subagent 可用

---

## 3. 关键技术决策

### 3.1 菜单位置

照搬 `MenuSidebar.vue` 的「知识中心」一级菜单模式（L116-132），放在「知识中心」之后、「管理菜单」之前。**不做 flyout 二级菜单**，直接 `router.push` 跳转到 `/t/:tenant_id/connections` 单页面，4 Tab 在页面内切换。

**理由**：用户已决策「一级菜单 + 4 Tab 单页面」；连接中心是租户管理员的高频独立操作，比「管理菜单」里的子项更接近业务。

### 3.2 加密模块抽取

新建 `src/core/secret_crypto.py`，从 `src/channels/wecom_personal_rpa/secret_crypto.py` 抽取 `encrypt_secret` / `decrypt_secret` 函数：

- **算法**：Fernet（AES-128-CBC + HMAC-SHA256）
- **主密钥环境变量优先级**：`APP_SECRET_KEY` > `RPA_SECRET_KEY`（向后兼容，现有部署不改）
- **缺失时**：抛 `RuntimeError`（启动安全，与现状一致）
- **`wecom_personal_rpa/secret_crypto.py` 改造**：改为 `from src.core.secret_crypto import encrypt_secret, decrypt_secret`，保留旧环境变量回退

**API 返回掩码**：列表查询时对 `encrypted_secret` 解密后做掩码处理（`sk-***...***ab12` 格式），不返回明文。

### 3.3 连接器注册机制（仿 ToolRegistry 模式）

`src/connectors/base.py` 定义基类与注册中心：

```python
class BaseConnector:
    connector_id: str          # 如 "tavily"
    display_name: str          # 如 "Tavily 搜索"
    category: str              # 如 "search" / "communication" / "enterprise"
    config_schema: dict        # JSON Schema 描述配置项
    sensitive_keys: list[str]  # 哪些字段需要加密 ["api_key"]

    async def test_connection(self, config: dict) -> dict:
        """返回 {success: bool, message: str, latency_ms: int}"""
        raise NotImplementedError


class ConnectorRegistry:
    """自动发现并注册所有内置连接器"""
    _connectors: dict[str, BaseConnector] = {}

    @classmethod
    def register(cls, connector_cls):
        instance = connector_cls()
        cls._connectors[instance.connector_id] = instance
        return connector_cls

    @classmethod
    def list_all(cls) -> list[BaseConnector]:
        return list(cls._connectors.values())

    @classmethod
    def get(cls, connector_id: str) -> BaseConnector | None:
        return cls._connectors.get(connector_id)
```

**`src/connectors/__init__.py` 必须遵守 backend_dev.md 的 `__init__.py` 反模式规范**：用模块级 `__getattr__` 懒加载，避免顶层实例化触发副作用。

### 3.4 测试连接实现

每个连接器自己实现 `test_connection`，返回统一结构：

```python
async def test_connection(self, config: dict) -> dict:
    return {
        "success": True/False,
        "message": "连接成功" / "API Key 无效" / "网络超时",
        "latency_ms": 234,
    }
```

**异步包装**：连接器的 `test_connection` 必须是 `async def`，内部调用同步阻塞 API（如 `smtplib.SMTP.login`、`requests.get`）时按 backend_dev.md「假异步」规范用 `asyncio.to_thread` 包裹。

**测试策略**（每个连接器最小请求）：
- Tavily：调 `search` API，query="test"
- SMTP/IMAP：`smtplib.SMTP.login(user, password)`
- 飞书：获取 `tenant_access_token`
- 钉钉：获取 `access_token`
- 企业微信：获取 `access_token`
- 企查查：调 `ping` 接口
- 高德地图：调 IP 定位接口
- 天气：调实时天气接口
- 百度翻译：调 translate 接口，q="hello"
- 网页抓取：HTTP HEAD 请求
- OCR：调用文字识别接口，传最小图片
- 图片生成：调用模型列表接口

### 3.5 作用域机制

`scope_subagents: TEXT[]` 字段，空数组 = 全部子智能体可用。

子智能体执行时（在 `agent.py` 或 subagent executor 中），按 `tenant_id` 拉取已激活（`status='active'`）的连接器配置，注入到 subagent 上下文。具体注入点 Phase 2 实现时再定，可能扩展 `subagent_definitions` 的运行时上下文加载逻辑。

### 3.6 配置 Schema 设计

每个连接器在类上声明 `config_schema`（JSON Schema 格式），前端按 schema 动态渲染表单：

```python
class TavilyConnector(BaseConnector):
    connector_id = "tavily"
    display_name = "Tavily 搜索"
    category = "search"
    config_schema = {
        "type": "object",
        "properties": {
            "api_key": {"type": "string", "title": "API Key", "format": "password"},
            "base_url": {"type": "string", "title": "Base URL", "default": "https://api.tavily.com"},
        },
        "required": ["api_key"],
    }
    sensitive_keys = ["api_key"]
```

前端用 `vue-json-schema-form` 或自实现的简单 schema 渲染器（项目目前没有现成组件，Phase 2 实现时再决定）。

---

## 4. 内置连接器清单

按使用频率分 3 批实现：

### Phase 2 - 高优先级

| connector_id | 名称 | 类别 | 测试连接方式 |
|--------------|------|------|------------|
| `feishu` | 飞书 | communication | 获取 tenant_access_token |
| `qichacha` | 企查查 | enterprise | 调 ping 接口 |
| `wecom` | 企业微信 | communication | 获取 access_token |
| `dingtalk` | 钉钉 | communication |

### Phase 3 - 补齐

| connector_id | 名称 | 类别 |
|--------------|------|------|
| `amap` | 高德地图 | utility |
| `baidu_translate` | 百度翻译 | utility |
| `web_fetch` | 网页抓取 | utility |
| `ocr` | OCR 识别 | utility |
| `image_gen` | 图片生成（Qwen-Image） | ai |

---

## 5. 安全与租户隔离

### 5.1 鉴权与租户隔离

所有 API 复用 `require_admin` + `get_current_tenant_id()`：

```python
@router.get("/configs")
async def list_configs(request_admin: dict = Depends(require_admin)):
    tenant_id = get_current_tenant_id()
    # 查询时必须带 tenant_id 过滤
    configs = ConnectorConfigDB.list_by_tenant(tenant_id)
    ...
```

支持 `X-Tenant-Id` Header（平台管理员代租户操作），与现有 `/api/saas/*` 路由一致。

### 5.2 敏感信息加密

| 字段 | 加密 | 返回前端 |
|------|------|---------|
| `config_json`（base_url、app_id 等非敏感） | 不加密 | 原文返回 |
| `encrypted_secret`（API Key、Secret） | Fernet 加密入库 | 掩码返回（`sk-***...***ab12`） |

**写入路径**：
1. 前端提交配置（含 `api_key` 等敏感字段）
2. 后端拆分敏感字段，调 `secret_crypto.encrypt_secret(plaintext)` 加密
3. `config_json` 存非敏感字段 JSON，`encrypted_secret` 存密文

**读取路径**：
1. 后端查询 `tenant_connector_configs`
2. 解密 `encrypted_secret` 还原明文
3. 对敏感字段做掩码处理后返回前端

### 5.3 日志脱敏

按 backend_dev.md 的 `sanitize_error_info` 规范，所有错误响应与日志中不得出现 API Key 明文。复用 `src/core/error_log_sink.py:SENSITIVE_PATTERNS`。

### 5.4 MCP Server 进程隔离（Phase 4）

自定义 MCP 连接器的 Server 进程按 `tenant_id` 隔离环境变量，**禁止跨租户复用进程**。

---

## 6. 与现有功能的关系

### 6.1 保留的功能

| 现有功能 | 位置 | 保留原因 |
|---------|------|---------|
| 管理后台 - 租户管理 - 编辑租户 - 数字员工授权 - API 配置 | `TenantMgmt.vue` L395 | 给平台管理员代租户操作用 |
| 管理后台 - 租户管理 - 编辑租户 - 数字员工授权 - 环境变量 | `TenantMgmt.vue` L301 | 给平台管理员代租户操作用 |
| 现有 `subagent_env_vars` 表 | `deploy/init-postgres.sql:600` | 不破坏性升级，仅新建 `tenant_connector_configs` 表加密 |
| 现有 `src/mcp/` MCP Server | `src/mcp/` | 项目对外暴露工具的 MCP Server，与连接中心的「连接外部 MCP Client」方向相反 |

### 6.2 复用的资产

| 资产 | 路径 | 用途 |
|------|------|------|
| 环境变量 API | `src/api/subagent_env_var.py` | Tab 2 直接复用 |
| API 配置文件 API | `src/api/tenant_config_file.py` | Tab 1 直接复用 |
| 加密能力 | `src/channels/wecom_personal_rpa/secret_crypto.py` | Phase 2 抽取到 `src/core/secret_crypto.py` 后复用 |
| 鉴权 | `src/saas/api/tenant_auth.py:require_admin` | 新 API 直接复用 |
| 前端菜单模式 | `MenuSidebar.vue:116-132`（「知识中心」模式） | 照搬 |
| 前端路由模式 | `router/agentRoutes.ts:77-153` | 加一条 child route |
| 前端表格/弹窗 | `TenantMgmt.vue` | 搬迁逻辑到 Tab 1/Tab 2 |

---

## 7. 验证方案

### Phase 1 验证
- 后端 import 检查：`docker exec aid-agent-api python -c "from src.api.subagent_env_var import router"`
- 前端 build：`cd frontend && npm run build` 0 错误
- 手动：登录租户前台看到「连接中心」菜单，4 Tab 框架，Tab 1/2 能配，Tab 3/4 占位

### Phase 2 验证
- 单测：`./scripts/dev_test.sh tests/unit/connectors/`
- 集成测试：覆盖「保存配置->加密入库->列表查询掩码->测试连接」全链路
- 加密验证：直接查 DB `SELECT encrypted_secret FROM tenant_connector_configs` 确认是密文
- 手动：UI 配置 Tavily API Key，点「测试连接」看到「连接成功」+ 延迟 ms

### Phase 3 验证
- 列表查询 API 返回 12-15 个内置连接器
- 每个连接器都能保存配置 + 测试连接

### Phase 4 验证
按调研结论后定。
