# 连接中心开发计划

> **关联设计文档**：[connection-center-design.md](../system/connection-center-design.md)
> **关联想法登记**：[ideas.md](../ideas.md) 系统功能 #48
> **状态**：📋 待开发
> **创建日期**：2026-07-30
> **流程规范**：按 [.claude/rules/dev_workflow.md](../../.claude/rules/dev_workflow.md) 三智能体开发流程

---

## 总体路线

按用户决策，分 4 个 Phase 实施：

| Phase | 内容 | 估时 | 流程 |
|-------|------|------|------|
| Phase 1 | 搬迁现有功能到前台 + 菜单 + 路由 + 文档登记 | 1-2 天 | 开发+测试（简化） |
| Phase 2 | 内置连接器框架 + 5 个高优先级连接器 + 加密 + 新表 | 3-5 天 | 完整三智能体 |
| Phase 3 | 补齐剩余 7-10 个内置连接器 | 2-3 天 | 完整三智能体 |
| Phase 4 | 自定义连接器（启动前先调研） | 5-7 天 | 完整三智能体 |

**总计**：12-17 人天。

---

## Phase 1：搬迁 + 菜单（最小可用）

**目标**：把现有功能搬到前台，租户管理员可见，UI 框架立起来。

### 后端

**无新增**。复用 `src/api/subagent_env_var.py`、`src/api/tenant_config_file.py`。

### 前端

#### 任务 1.1：新建 ConnectionCenter.vue 主页面

`frontend/src/components/connections/ConnectionCenter.vue`

- 4 Tab 容器，用项目现有 Tab 模式（参考 `TenantMgmt.vue` 的 tab 切换）
- Tab 1、Tab 2 完整实现
- Tab 3、Tab 4 显示「即将上线」占位
- 顶部用 `AppHeader`（标题「连接中心」+ 汉堡按钮 + 更多菜单）

#### 任务 1.2：新建 ApiConfigTab.vue

`frontend/src/components/connections/ApiConfigTab.vue`

- 搬迁 `TenantMgmt.vue` 的 API 配置 md 文件上传逻辑
- 左侧列出 `configSupportedAgents` 数字员工
- 右侧显示当前选中数字员工的 API 配置文件（md）+ 上传/下载/删除按钮

#### 任务 1.3：新建 EnvVarsTab.vue

`frontend/src/components/connections/EnvVarsTab.vue`

- 搬迁 `TenantMgmt.vue` 的环境变量弹窗逻辑
- 左侧 subagent 列表 + 右侧 name/value/description 表格
- 改为内嵌页面而非弹窗
- 全量覆盖保存（沿用现有 PUT 接口语义）

#### 任务 1.4：修改 MenuSidebar.vue 加一级菜单

`frontend/src/components/MenuSidebar.vue`

在「知识中心」(L116-132) 之后、「管理菜单」(L134-157) 之前，加一个「连接中心」一级菜单项：

```vue
<!-- 连接中心入口：一级菜单，仅租户管理员可见（手机端隐藏） -->
<button
  v-if="isTenantAdmin && !props.isMobile"
  @click="router.push(`/t/${tenantId}/connections`)"
  :class="[
    'w-full flex items-center gap-3 px-3 py-2.5 rounded-lg transition-colors text-sm',
    route.path === `/t/${tenantId}/connections`
      ? 'bg-primary-50 text-primary-700 font-medium'
      : 'text-gray-600 hover:bg-gray-50'
  ]"
>
  <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
    <path d="M9 7V3M15 7V3M9 21v-4M15 21v-4M5 12H3M21 12h-2M7 9h10a2 2 0 012 2v2a2 2 0 01-2 2H7a2 2 0 01-2-2v-2a2 2 0 012-2z" />
  </svg>
  <span>连接中心</span>
</button>
```

#### 任务 1.5：修改 agentRoutes.ts 加路由

`frontend/src/router/agentRoutes.ts`

在 `/t/:tenant_id` children（L77-153）中加一条：

```ts
{ path: 'connections', name: 'tenant-connections', component: () => import('@/components/connections/ConnectionCenter.vue') },
```

#### 任务 1.6：新建 connections.ts 占位

`frontend/src/api/connections.ts`

- 先放占位 API 客户端（empty stubs）
- Phase 2 填充具体接口

### Phase 1 验证

- 后端 import 检查：`docker exec aid-agent-api python -c "from src.api.subagent_env_var import router"`
- 前端 build：`cd frontend && npm run build` 0 错误
- 手动测试：
  - 登录租户前台 `/t/{tenant_id}/`，看到左侧菜单「连接中心」
  - 点击进入，看到 4 Tab，Tab 1/2 能配 API 配置文件和环境变量，Tab 3/4 显示「即将上线」
  - 配置的环境变量在 `subagent_env_vars` 表中能查到（验证搬迁后功能等价）

### 注意事项

- 现有 `TenantMgmt.vue` 的环境变量/API 配置弹窗**不要删**，保留给平台管理员
- Tab 1/2 复用现有 API，无需新增后端逻辑

---

## Phase 2：内置连接器框架 + 5 个高优先级连接器

**目标**：搭出连接器注册机制 + 实现 5 个最常用连接器，验证整套流程跑通。

### 后端

#### 任务 2.1：新建 tenant_connector_configs 表

`deploy/init-postgres.sql` 新增表定义（见设计文档 §2.4）。

`deploy/db_update.sql` 同步增量条目：

```sql
-- 2026-7-30，新增 tenant_connector_configs 表，连接中心用
CREATE TABLE IF NOT EXISTS tenant_connector_configs (...);
CREATE INDEX IF NOT EXISTS idx_tenant_connector_configs_tenant ON tenant_connector_configs(tenant_id);
```

#### 任务 2.2：抽取 src/core/secret_crypto.py

从 `src/channels/wecom_personal_rpa/secret_crypto.py` 抽取 `encrypt_secret` / `decrypt_secret`：

- 主密钥环境变量优先级：`APP_SECRET_KEY` > `RPA_SECRET_KEY`（向后兼容）
- 缺失时抛 RuntimeError
- `wecom_personal_rpa/secret_crypto.py` 改为 re-export 公共模块

#### 任务 2.3：新建 src/db/connector_config.py

`ConnectorConfigDB` 类，CRUD + 加密字段自动加解密：

- `list_by_tenant(tenant_id)` - 列出租户所有配置（返回掩码 secret）
- `get_config(tenant_id, connector_id)` - 获取单个配置（返回掩码 secret）
- `get_config_with_secret(tenant_id, connector_id)` - 获取含明文 secret 的配置（仅内部使用）
- `upsert_config(tenant_id, connector_id, config, sensitive_data, user_id)` - 保存配置（敏感字段加密入库）
- `delete_config(tenant_id, connector_id)`
- `update_status(tenant_id, connector_id, status, error_msg)` - 测试连接后更新状态

#### 任务 2.4：新建 src/connectors/base.py

`BaseConnector` 基类 + `ConnectorRegistry` 注册中心（见设计文档 §3.3）。

**`src/connectors/__init__.py` 必须遵守 `__init__.py` 反模式规范**：用模块级 `__getattr__` 懒加载。

#### 任务 2.5：新建 src/api/connector_center.py

路由前缀 `/api/saas/tenant/connections`：

| 端点 | 方法 | 用途 |
|------|------|------|
| `/builtin` | GET | 列出所有内置连接器定义（不含配置） |
| `/configs` | GET | 列出租户已配置的连接器（含掩码 secret） |
| `/configs/{connector_id}` | GET | 获取单个配置 |
| `/configs/{connector_id}` | PUT | 保存配置（敏感字段加密入库） |
| `/configs/{connector_id}` | DELETE | 删除配置 |
| `/configs/{connector_id}/test` | POST | 测试连接 |

所有端点 `Depends(require_admin)` + `get_current_tenant_id()`。

#### 任务 2.6：实现 5 个高优先级连接器

| 文件 | connector_id | 名称 | 测试连接方式 |
|------|--------------|------|------------|
| `src/connectors/tavily.py` | tavily | Tavily 搜索 | 调 search API，query="test" |
| `src/connectors/email_smtp.py` | email_smtp | SMTP/IMAP 邮箱 | `smtplib.SMTP.login` |
| `src/connectors/feishu.py` | feishu | 飞书 | 获取 tenant_access_token |
| `src/connectors/qichacha.py` | qichacha | 企查查 | 调 ping 接口 |
| `src/connectors/wecom.py` | wecom | 企业微信 | 获取 access_token |

每个连接器：
- 继承 `BaseConnector`
- 用 `@ConnectorRegistry.register` 装饰
- 声明 `connector_id`、`display_name`、`category`、`config_schema`、`sensitive_keys`
- 实现 `async def test_connection(config) -> dict`

### 前端

#### 任务 2.7：完善 frontend/src/api/connections.ts

5 个 API 客户端：

- `listBuiltinConnectors()` - 列出内置连接器定义
- `listConfigs()` - 列出租户已配置
- `getConfig(connectorId)` - 获取单个配置
- `saveConfig(connectorId, payload)` - 保存配置
- `deleteConfig(connectorId)` - 删除
- `testConnection(connectorId)` - 测试连接

#### 任务 2.8：新建 BuiltinConnectorsTab.vue

`frontend/src/components/connections/BuiltinConnectorsTab.vue`

- 卡片网格（参考 `MyDigitalEmployees.vue` 的卡片布局）
- 每张卡片显示：连接器图标、名称、状态徽章（未配置/已连接/出错）
- 「配置」按钮打开弹窗，按 `config_schema` 动态渲染表单
- 「测试连接」按钮实时调用 `test` API 并显示结果

#### 任务 2.9：新建 ConnectorCard.vue

`frontend/src/components/connections/ConnectorCard.vue`

卡片组件，含状态徽章和操作按钮。

### Phase 2 验证

- 后端单测：`./scripts/dev_test.sh tests/unit/connectors/ -p no:cacheprovider -q`
- 集成测试：`./scripts/dev_test.sh tests/integration/test_connector_flow.py -p no:cacheprovider -q -v`
  - 覆盖：保存配置→加密入库→列表查询掩码→测试连接
- 加密验证：直接查 DB `SELECT encrypted_secret FROM tenant_connector_configs WHERE tenant_id=...`，确认是密文；API 返回的 `api_key` 字段是 `tvly-***...***ab12` 掩码
- 手动测试：在 UI 配置 Tavily API Key，点「测试连接」，看到「连接成功」+ 延迟 ms

### 注意事项

- `test_connection` 必须用 `asyncio.to_thread` 包裹同步阻塞调用（smtplib、requests 等）
- 测试连接超时设为 10s，避免长时间卡住 UI
- `ConnectorRegistry` 用懒加载，避免顶层 import 触发副作用

---

## Phase 3：补齐剩余内置连接器

**目标**：覆盖企业日常 80% 场景，达到 12-15 个内置连接器。

### 后端

补齐 7-10 个连接器：

| 文件 | connector_id | 名称 | 类别 |
|------|--------------|------|------|
| `src/connectors/dingtalk.py` | dingtalk | 钉钉 | communication |
| `src/connectors/amap.py` | amap | 高德地图 | utility |
| `src/connectors/weather.py` | weather | 天气 | utility |
| `src/connectors/baidu_translate.py` | baidu_translate | 百度翻译 | utility |
| `src/connectors/web_fetch.py` | web_fetch | 网页抓取 | utility |
| `src/connectors/ocr.py` | ocr | OCR 识别 | utility |
| `src/connectors/image_gen.py` | image_gen | 图片生成（Qwen-Image） | ai |

### 前端

**无需改前端代码**。自动通过 `/api/connections/builtin` 拉取列表渲染。

### Phase 3 验证

- 列表查询 API 返回 12-15 个内置连接器
- 每个连接器都能保存配置 + 测试连接
- 单测覆盖每个连接器的 `test_connection` 方法

---

## Phase 4：自定义连接器（启动前先调研）

**重要**：本 Phase 启动前必须先完成调研，决策 MCP vs OpenAPI vs 双轨。

### 调研阶段（5-7 天）

- 调研 Anthropic MCP Client SDK（Python `mcp` 包）
- 调研 Dify MCP Client 实现（开源代码可参考）
- 调研 Coze MCP 接入文档
- 评估 OpenAPI Schema 解析方案（如 `openapi-spec-validator`）
- 决策：MCP only / OpenAPI only / 双轨

### 实现阶段（按调研结论）

**如选 MCP**：
- 新建 `src/mcp/client.py`（基于 `mcp` Python SDK）
- 实现 stdio/sse/http 三种 transport
- 新建 `src/connectors/custom_mcp_runtime.py` 包装 MCP Client 调用

**如选 OpenAPI**：
- 新建 `src/connectors/openapi_runtime.py`
- 解析 Swagger JSON 生成工具调用 schema
- 用 `httpx` 调用具体端点

**如选双轨**：上述两者都做。

### 前端

新建 `CustomConnectorsTab.vue`：
- 列表 + 新建弹窗
- 表单：连接器名称 + 类型（MCP/OpenAPI）+ URL 或 JSON 粘贴框 + 认证表单 + 作用域选择
- 「测试连接」按钮：MCP 调 `tools/list`，OpenAPI 调 `/healthz` 或首个 GET 端点
- 工具列表预览：测试连接成功后展示可用工具列表

### Phase 4 验证

按调研结论后定。

---

## 三智能体开发流程

按 `.claude/rules/dev_workflow.md` 规范：

| Phase | 流程 |
|-------|------|
| Phase 1 | 简化流程（开发+测试两步，CR 可选），改动小 |
| Phase 2 | 完整三智能体（开发→测试→CodeReview），涉及加密、新表、新模块 |
| Phase 3 | 完整三智能体，每个连接器独立测试 |
| Phase 4 | 完整三智能体 + 调研报告先行 |

**关键安全检查**（每个 Phase 提交前）：
1. 后端 import 检查：`docker exec aid-agent-api python -c "from src.api.connector_center import router"`
2. 前端 build：`cd frontend && npm run build` 0 错误
3. 启动安全：`docker exec aid-agent-api python -c "from src.main import app"` 无报错

---

## 文档登记规范

按 `CLAUDE.md` 规范，开发过程中同步更新文档：

| 时机 | 动作 |
|------|------|
| Phase 1 开始 | 在 `docs/ideas.md` 添加条目，状态标 🔧 部分完成 |
| Phase 完成时 | 在 `docs/ideas.md` 中更新说明，补充完成进度 |
| 全部完成 | 移动到 `docs/ideas_finished.md`，状态标 ✅ 已完成开发 |
| 新增缓存 | 更新 `.claude/rules/cache_usage.md` |
| 新增 `to_thread` 用法 | 更新 `.claude/rules/backend_dev.md` 的「假异步案例」表 |
| 新增数据库表 | 更新 `docs/system/database_system_table.md` |

---

## 风险与回退方案

### 风险 1：加密模块抽取影响现有 wecom_personal_rpa

**影响**：`secret_crypto.py` 抽取后，现有 `wecom_rpa_clients.encrypted_secret` 加解密可能失效。

**缓解**：抽取时保持函数签名不变，`wecom_personal_rpa/secret_crypto.py` 改为 re-export。环境变量优先级 `APP_SECRET_KEY > RPA_SECRET_KEY` 保证现有部署不配置 `APP_SECRET_KEY` 时仍读 `RPA_SECRET_KEY`。

**回退**：若出现问题，直接还原 `wecom_personal_rpa/secret_crypto.py` 即可，新表 `tenant_connector_configs` 独立。

### 风险 2：连接器测试连接阻塞事件循环

**影响**：连接器 `test_connection` 内部调同步阻塞 API（smtplib、requests），若不用 `asyncio.to_thread` 包裹会阻塞 UvicornWorker。

**缓解**：所有同步阻塞调用必须用 `asyncio.to_thread` 包裹（按 backend_dev.md「假异步」规范）。CodeReview 智能体重点检查此项。

### 风险 3：租户隔离失效

**影响**：API 漏掉 `tenant_id` 过滤导致跨租户数据泄露。

**缓解**：所有 DB 查询必须 `WHERE tenant_id = %s`。CodeReview 智能体重点检查。集成测试覆盖跨租户访问场景。
