# 连接中心架构设计

> **文档类型**：架构设计（系统级横切关注点）
> **状态**：📋 待开发
> **创建日期**：2026-07-30
> **触发场景**：现有「API 配置 + 环境变量」藏在管理后台「租户管理 - 编辑租户 - 数字员工授权」弹窗里，租户管理员要配 API Key 必须找平台管理员开权限进管理后台，体验割裂；同时缺少对标 Coze / Dify / n8n 的「连接器」抽象。
> **关联规范**：[backend_dev.md](../../.claude/rules/backend_dev.md) / [database_dev.md](../../.claude/rules/database_dev.md) / [cache_usage.md](cache_usage.md) / [file_usage.md](file_usage.md)
> **关联想法登记**：[ideas.md](../ideas.md) 系统功能 #48

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
- **加密**：Fernet AES-128（项目已有 `src/core/secret_crypto.py` 可复用）
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

### 2.1.1 微信 Agent

连接中心新增「微信 Agent」工作区，对照[企业微信会话工作台](../design/wecom/wecom-conversation-operations-design.md#6-三表和前端的最小边界)组织群聊读取和总结。给会话登记时输入微信显示的完整群名。读取前在目标电脑的 Windows 微信中打开该群，由微信 CLI 通过 UIA 核对当前会话并采集其可见历史。界面说明实际读取范围，并区分会话不匹配、没有可总结的文字和读取失败。本机命令 `read --domain group-history` 复用现有历史翻页，只在已选会话的完整标题一致且 UIA 证实群聊类型时采集。此前的 CLI 会拒绝未知读取域，避免误落到通用单聊读取。

用户于 2026-11-23 确认通过 CLI 读取当天聊天记录并交给现有模型总结。页面明确说明这一用途，用户输入完整群名，确认当前显示的设备后手动点击「生成今日总结」。设备切换或群名修改后重新确认。解析仅选择可由 UIA 时间标签证实属于北京时间今天的文字。星期标签只用于核实早于北京时间当天的记录。与采集日本地星期几相同的标签，以及换算后落在北京时间当天的星期标签，均视为日期未知。可信的前日时间标记可以证明其前面的记录不属于当天，标记之后的日期缺失继续计入覆盖缺口。不能确认当天完整覆盖时标明部分读取，不能确认日期的文字不提交。页面展示摘要、覆盖范围和运行状态，业务原文不进入会话记录、模型工具历史或结果回执，图片、附件和截图不传输。首期提供单群手动总结。

复用用户在本地工具页面选定的设备及 Runtime 标准串行调用。微信 CLI 签名工具 `weixin_group_daily_summary` 核对会话、采集并筛选当天文字，向相同配对服务的专属总结端点发起一次请求。服务端根据该次用户发起的作业和一次性凭证确定归属，在创建时及调用模型前核对积分，由现有模型网关总结，仅摘要及覆盖统计走普通结果回执。授权、兼容性与字段见[Runtime 共同契约](runner-desktop-runtime-integration-contract.md#微信正文仅限本机共同检查点)。安装和验收记录见[开发计划](../plans/plan-weixin-agent.md)。

#### 多群跟踪与近七天总结

[可交互 UI 原型](../design/weixin/weixin-group-tracking-prototype.html)。

微信 Agent 沿用连接中心入口和当前选定的 Windows 设备。页面左侧是跟踪群聊，右侧提供「生成总结」「定时总结」与「总结历史」。窄屏按群列表、总结操作、结果顺序纵向排列。

| 场景 | 交互 |
|---|---|
| 保存跟踪群 | 点击「添加群聊」，输入微信会话列表显示的完整名称后保存。标题旁的成员人数无需输入。列表允许多条配置，返回页面时从当前租户下当前用户的配置加载。保存时提示同名，实际执行时仍核对唯一目标。群改名可编辑原配置，移除跟踪需确认，历史仍可按对应群筛选，保留生成时的群名。 |
| 选群和时间 | 勾选一个或多个已保存的群，可全选。默认「今天」沿用北京时间当天零点至提交时刻，另有「最近 24 小时」「最近 3 天」「最近 7 天」及自定义起止。界面显示实际起止和时区。最近七天按提交时间回溯 168 小时，起止不得超出此范围。 |
| 发起总结 | 显示选中的设备、群数和时间范围。选好群和时段后，点击「生成总结」即提交本次任务。添加群聊只保存配置。 |
| 等待与重试 | 同一设备串行定位并读取所选群，每群一份总结。列表显示等待、读取中、已保存、部分读取、无文字、失败。遇到同名或无法核实目标时，提示用户在微信中打开该群，点击「仅重试此群」即可按原时段重新提交。其他群结果可继续回看。 |
| 配置定时总结 | 新建任务时填写名称、选择一组已保存的群聊、频率和北京时间执行时刻，预览下次执行时间。首版支持每天、每周，以及执行当天或截至计划时刻的最近 24 小时、3 天、7 天。任务保存后可编辑、暂停、恢复，通知配置随任务保存。 |
| 选择执行通知 | 任务默认关闭通知。勾选「发送执行结果通知」后，选择「默认通知配置」或「群机器人通知配置」。默认沿用站内信和个人通知偏好。群机器人按任务选择一条有权限使用的已有定时任务通知规则。界面只显示规则名和渠道，缺少可用规则时提示到现有「通知规则」页面配置。 |
| 回看历史 | 「总结历史」按执行时间倒序，每次执行占一行，展示时间、手动或定时来源、群数和结果概况。点击一行进入该次详情，同页查看所有群的摘要、读取条数、覆盖状态及失败原因。筛选群聊只缩小列表至包含该群的批次，详情仍显示该次的全部群。摘要入库后可在设备离线时查看。结果使用独立的「微信群」顶栏标识群名，正文渲染 Markdown 标题、列表、加粗和表格。 |

手动和定时任务均按群独立出结果，写入同一个总结历史。群配置、定时任务和摘要按租户及用户归属保存。UIA 在任务设备上核实群聊身份、时间证据和覆盖后采集。手动任务以点击时的配置为准。定时任务沿用保存的设备与群聊，每次按计划时间确定范围，已移除的群退出后续执行。聊天正文只进入本次模型请求，摘要和执行元数据入库。旧版设备的今日总结和已有记录按兼容方案保留。

历史使用每次执行的唯一标识关联逐群结果，以北京时间展示执行时间。同一时刻触发的不同执行各自成组。整次定时执行结束后按已有规则投递通知。默认通知和群机器人统一发送任务名、执行状态和该次的结果链接。打开链接后登录并核对任务归属，详情页展示本次全部群的摘要。投递失败单独展示，已入库的总结仍可回看。设备离线或微信未就绪时记录执行失败，是否通知按所选规则。复用[定时任务通知规则](scheduled-task-runner-refactor-design.md#33-通知规则选用2026-11-02-更新)和[执行结果通知入口](scheduled-task-runner-refactor-design.md#155-通知直达本次执行结果2026-11-03-追加)。设备协议见 [Runtime 共同契约](runner-desktop-runtime-integration-contract.md#微信正文仅限本机共同检查点)。部署与验收见[开发计划](../plans/plan-weixin-agent-group-tracking.md)。

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
│   └── secret_crypto.py           # 【新】公共加密模块（自渠道模块最小抽取）
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

新建 `src/core/secret_crypto.py`，从渠道侧模块抽取 `encrypt_secret` / `decrypt_secret` 函数（已落地）：

- **算法**：Fernet（AES-128-CBC + HMAC-SHA256）
- **主密钥环境变量优先级**：`APP_SECRET_KEY` > `RPA_SECRET_KEY`（向后兼容，现有部署不改）
- **缺失时**：抛 `RuntimeError`（启动安全，与现状一致）
- **原渠道侧 `secret_crypto.py` 改造**：改为 `from src.core.secret_crypto import encrypt_secret, decrypt_secret`，保留旧环境变量回退（原渠道已清退，公共模块保留）

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
| 加密能力 | `src/core/secret_crypto.py` | 公共 Fernet 模块（已抽取落地） |
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
