# 售后服务通用子智能体 — 设计文档

> 创建日期: 2026-05-06 | 状态: 开发中（Phase 1-2 后端已完成）

---

## 1. 需求分析

### 1.1 核心需求

售后服务智能体需要处理以下场景：

| 场景 | 说明 |
|------|------|
| 订单问题 | 用户查询订单状态、物流信息、订单修改等 |
| 退换货 | 退货申请、换货处理、退款进度查询 |
| 商品使用问题 | 商品使用指导、故障排查、配件更换 |
| 售后工单 | 创建/查询/跟踪售后工单 |

### 1.2 通用性要求

本智能体作为**通用售后服务引擎**，需要服务不同企业：

1. **用户身份感知**：通过长期记忆系统自动感知当前用户的个人信息（姓名、手机号、习惯等），无需用户重复提供
2. **用户身份映射**：本系统的用户身份（user_id、手机号）需要映射到第三方企业系统的用户身份，Agent 才能以正确的身份调用外部 API
3. **企业系统对接**：每个企业的订单系统、售后系统各不相同，需要一种可插拔的外部系统集成机制
4. **统一对话流程**：无论底层对接哪个企业系统，用户侧的对话体验保持一致

### 1.3 与现有子智能体的对比

| 维度 | 现有子智能体（外贸/合同/旅游） | 售后服务子智能体 |
|------|------|------|
| 数据来源 | LLM 生成 + 内部数据库 | 外部企业系统 API（通过 http_api 工具） |
| 系统集成 | 硬编码特定系统（EAS） | JSON 配置驱动，LLM + http_api 直接调用 |
| 用户身份 | 不依赖用户信息 | 通过长期记忆感知用户信息，通过身份映射关联外部系统用户 |
| 多租户定制 | 无 | 每租户不同的 API 端点配置 + 身份映射策略 |

---

## 2. 现有基础设施评估

### 2.1 已具备的能力（可直接复用）

| 能力 | 现有组件 | 适用性 |
|------|----------|--------|
| 子智能体定义与加载 | `SubagentLoader` + `SUBAGENT.md` | ✅ 完全适用 |
| 技能系统 | `SkillLoader` + `SkillRegistry` + `SkillExecutor` | ✅ 售后流程封装为 skill |
| 用户身份传递 | `agent.process_message(user=User(...))` | ✅ Web 聊天已传递 user_id |
| 用户长期记忆 | `LongTermMemory` + `memory_{user_id}.md`（Phase 3 设计） | ✅ 自动注入用户信息 |
| 租户上下文 | `TenantContextMiddleware` + `get_current_tenant_id()` | ✅ 每租户隔离 |
| 凭据加密存储 | `encryption_manager` + Fernet | ✅ 可复用加密机制（后续按需启用） |
| 业务数据表规范 | `bs_` 前缀 + `tenant_id` 隔离 | ✅ 售后数据表遵循此规范 |
| SaaS 订阅控制 | `SubscriptionDB.get_allowed_subagent_types()` | ✅ 售后智能体作为子智能体类型注册 |
| 工具系统 | `BaseTool` + `ToolRegistry` + `ToolExecutor` | ✅ 可定义售后专用工具 |
| HTTP API 调用 | `HttpApiTool`（`src/tools/network/http_api.py`） | ✅ 直接复用，支持环境变量替换 |
| 渠道接入 | WeCom / DingTalk / Feishu 适配器 | ✅ 用户可通过 IM 直接售后 |
| 定时任务 | `CreateScheduledTaskTool` | ✅ 可用于售后跟进提醒 |

### 2.2 需新建的基础设施

| 需新建能力 | 解决方案 |
|-----------|----------|
| API 端点配置 | 通用技能 `after-sales-api`（读取租户配置文件）+ 租户配置文件 `storage/tenants/{tid}/after-sales-api.md`（管理员在智能体管理页面编辑） |
| 用户身份映射 | 手机号映射（MVP）+ 外部 ID 映射表（后续） |
| 租户级 API 凭据管理 | 新建 `subagent_env_vars` 表（子智能体环境变量，按租户和子智能体隔离），通过环境变量注入 http_api 工具 |
| 售后工单数据表 | 新建 `bs_after_sales_*` 系列表 |

> **用户身份现状**：`users` 表已有 `phone`、`wx_openid` 等字段，但 Agent 构造 `User` 对象时只传了 `user_id` 和 `name`（`main.py:558-563`）。需要扩展 `User` 模型并增加身份注入逻辑。

### 2.3 设计决策：通用技能 + 租户配置文件

**核心决策**：技能逻辑和配置数据分离。

- `after-sales-api` 是一个**通用技能**，注册在售后子智能体中，所有租户共用
- 每个租户的 API 说明文本保存在 `storage/tenants/{tenant_id}/after-sales-api.md`
- 技能的唯一职责：读取当前租户的 API 说明文件内容，返回给 LLM

**理由**：

| 维度 | 每租户复制一份 skill | 通用技能 + 租户配置文件 |
|------|---------------------|------------------------|
| 技能维护 | 改一处需同步 N 个租户副本 | 改一处生效所有租户 |
| 逻辑复用 | 每份副本逻辑完全相同，浪费 | 一份代码，N 份配置 |
| 配置管理 | 复用 skill 管理 API | 专用配置 API，体验更好 |
| 存储开销 | N 个完整的 SKILL.md + scripts | 1 个 skill + N 个纯文本 MD |

**工作流程**：

```
1. 租户管理员在售后智能体管理页面编辑 API 说明
   → 保存到 storage/tenants/{tid}/after-sales-api.md

2. 售后子智能体处理用户消息
   → LLM 调用 use_skill("after-sales-api") 加载技能
   → 技能指导 LLM 执行 skill_execute 读取当前租户的 API 配置
   → LLM 获得 API 说明文本
   → LLM 根据说明 + http_api 工具调用外部系统
   → LLM 理解响应，生成自然语言回复
```

**已有的基础设施支持**：
- `use_skill` + `skill_execute` — 加载技能 + 执行脚本
- `get_current_tenant_id()` — 获取当前租户 ID
- 租户存储目录 `storage/tenants/{tid}/` — 已有目录结构

---

## 3. 架构设计

### 3.1 整体架构

```
用户消息
  │
  ▼
售后服务子智能体（SUBAGENT.md）
  │
  ├─ [用户身份感知] ← 长期记忆 memory_{user_id}.md
  │     （姓名、偏好、习惯等，由记忆系统 Phase 3 自动注入上下文）
  │
  ├─ [用户身份映射] ← identity_mapping 配置 + DB
  │     │
  │     ├─ 手机号映射（MVP）
  │     │     users.phone → 第三方系统 API 的用户查询参数
  │     │
  │     └─ 外部 ID 映射表（后续）
  │           user_external_identities 表 → 精确的外部系统用户 ID
  │
  ├─ [意图识别] ← LLM 判断用户诉求类型
  │
  ├─ [外部系统调用] ← http_api 工具 + after-sales-api skill
  │     │
  │     ├─ LLM 通过 use_skill("after-sales-api") 加载 API 调用知识
  │     ├─ LLM 根据 skill 知识 + 用户身份信息 组装请求参数
  │     ├─ http_api 工具发起真实 HTTP 请求
  │     ├─ 凭据和地址通过 ${VAR_NAME} 环境变量注入
  │     │
  │     ├─ 企业A 的订单系统 API
  │     ├─ 企业B 的 ERP 系统 API
  │     └─ 通用 REST API
  │
  ├─ [售后内部操作] ← after_sales_tools
  │     ├─ create_ticket        创建内部工单
  │     └─ query_ticket         查询工单
  │
  └─ [回复生成] ← LLM 整合查询结果，生成专业回复
```

### 3.2 外部系统对接设计

#### 3.2.1 核心思路

复用已有的 `http_api` 工具（`src/tools/network/http_api.py`）。LLM 根据注入的 API 配置自动组装请求参数，直接调用 `http_api` 工具与外部系统交互。LLM 本身就是"适配器引擎"——阅读 API 配置、组装请求、理解响应。

#### 3.2.2 API 配置 — 通用技能 + 租户配置文件

技能逻辑和配置数据分离。`after-sales-api` 是一个通用技能，所有租户共用，唯一的职责是读取当前租户的 API 说明文件。每个租户的 API 说明保存在独立的 MD 文件中，由租户管理员在智能体管理页面配置。

**文件布局**：

```
src/skills/after-sales-api-1.0.0/
  SKILL.md                     ← 通用技能定义（所有租户共用）
  scripts/
    load_api_config.py          ← 读取当前租户的 after-sales-api.md 并返回内容

storage/tenants/{tenant_id}/
  after-sales-api.md            ← 租户的 API 说明文本（管理员编辑此文件）
```

**SKILL.md（通用技能）**：

```markdown
---
name: after-sales-api
description: >
  售后服务外部系统 API 配置加载技能。读取当前租户配置的外部系统 API 说明，
  供 LLM 了解如何调用外部售后系统的接口。使用 http_api 工具发起实际请求。
metadata:
  openclaw:
    emoji: "🔌"
    requires:
      bins: ["python"]
---

# 售后服务外部系统 API 配置

## 如何使用此技能

当你需要调用外部售后系统 API（查询订单、退货、工单等）时：

1. 先执行以下命令加载当前租户的 API 配置：

```bash
python scripts/load_api_config.py
```

2. 脚本会返回当前租户配置的外部系统 API 说明文本（Markdown 格式）
3. 仔细阅读返回的 API 说明，了解：
   - 外部系统的 Base URL 和认证方式
   - 用户身份映射策略（通常为手机号映射）
   - 可用的 API 端点、请求格式和响应格式
4. 根据说明使用 http_api 工具调用外部系统
5. URL 和 headers 中的 ${VAR_NAME} 环境变量会自动替换为实际值

## 注意事项

- 每次对话中首次需要调用外部 API 时，都要先执行脚本获取最新配置
- 调用需要用户身份的 API 时，从系统注入的 [用户身份] 区块获取手机号等信息
- 写操作（创建退货、创建工单等）前，先向用户确认信息再调用
- 如果脚本返回"未配置"，说明该租户尚未配置外部系统，使用内部工单工具代替
```

**租户配置文件默认模板（after-sales-api.md）**：

租户首次开通售后智能体时，自动生成一份默认模板，管理员需根据企业实际情况修改：

```markdown
# 外部系统 API 说明

> ⚠️ 这是默认模板，请根据你企业的实际 API 修改以下内容。
> 修改后立即生效，下次对话时智能体将使用新配置。

## 基本信息

- **系统名称**：企业售后系统（请修改）
- **Base URL**：`${TENANT_AFTER_SALES_BASE_URL}`
- **认证方式**：Bearer Token
- **认证 Header**：`Authorization: Bearer ${TENANT_AFTER_SALES_API_KEY}`
- **通用 Header**：`Content-Type: application/json`

## 用户身份映射

- **映射策略**：手机号映射
- **说明**：通过用户手机号作为第三方系统的用户标识。查询订单、创建退货等操作需要传手机号来识别用户。

## API 端点列表

### 1. 查询订单列表

GET ${TENANT_AFTER_SALES_BASE_URL}/orders?phone={手机号}&page=1&page_size=10

**请求参数**：phone（用户手机号）、page（页码）、page_size（每页数量）

**响应示例**：
```json
{
  "code": 0,
  "data": {
    "list": [{"orderNo": "ORD20260501001", "status": "delivered", "totalAmount": 1299.00}],
    "total": 1
  }
}
```

### 2. 查询订单详情

GET ${TENANT_AFTER_SALES_BASE_URL}/orders/{订单号}

**响应示例**：
```json
{
  "code": 0,
  "data": {
    "orderNo": "ORD20260501001", "status": "delivered", "totalAmount": 1299.00,
    "items": [{"productName": "商品A", "quantity": 2, "unitPrice": 649.50}],
    "createTime": "2026-05-01 10:00:00"
  }
}
```

### 3. 创建退货申请

POST ${TENANT_AFTER_SALES_BASE_URL}/returns

**请求体**：`{"order_id": "订单号", "reason": "退货原因", "items": "涉及商品", "contact_phone": "用户手机号"}`

**响应示例**：`{"code": 0, "data": {"returnNo": "RET20260501001", "status": "pending"}}`

### 4. 查询退货/退款进度

GET ${TENANT_AFTER_SALES_BASE_URL}/returns/{退货单号}

### 5. 创建售后工单

POST ${TENANT_AFTER_SALES_BASE_URL}/tickets

**请求体**：`{"order_id": "订单号(可选)", "category": "分类", "description": "描述", "contact_phone": "手机号", "contact_name": "姓名"}`

### 6. 查询工单详情

GET ${TENANT_AFTER_SALES_BASE_URL}/tickets/{工单号}

## 调用注意事项

1. 环境变量自动替换：${TENANT_AFTER_SALES_BASE_URL} 和 ${TENANT_AFTER_SALES_API_KEY}
2. 用户手机号从 [用户身份] 区块获取
3. 写操作前先向用户确认
```

#### 3.2.3 配置加载机制

**两步加载**：LLM 先加载技能（获取"怎么读配置"的指引），再执行脚本（获取"实际配置内容"）。

```
1. LLM 遇到需要调用外部 API 的场景
   → 调用 use_skill("after-sales-api")
   → SKILL.md 内容注入对话上下文（告诉 LLM 要先执行脚本）

2. LLM 按指引执行脚本
   → skill_execute("python scripts/load_api_config.py")
   → 脚本根据当前 tenant_id 读取 after-sales-api.md
   → 返回 API 说明文本

3. LLM 阅读 API 说明，组装 http_api 调用参数
   → 调用 http_api 执行请求
   → 理解响应，生成自然语言回复
```

**租户管理员配置流程**：
1. 在售后智能体管理页面，找到"外部系统配置"区域
2. 页面显示 MD 编辑器，内容为 `storage/tenants/{tenant_id}/after-sales-api.md`
3. 修改 base_url、API 端点、请求/响应格式等
4. 保存 → 文件直接写入磁盘
5. 下次对话即时生效（无需重启）

> **设计原则**：API 配置文件由 `after-sales-api` skill 的 `load_api_config.py` 脚本按需读取，
> 不需要专门的后端 API 端点。前端通过通用的租户文件管理接口操作配置文件。

#### 3.2.4 LLM 调用流程

```
用户: "帮我查一下订单 ORD20260501001 的状态"
  │
  ▼
Agent 识别意图 → 需要查询外部系统
  │
  ▼
Agent 调用 use_skill("after-sales-api") 加载技能指引:
  → SKILL.md 告诉 Agent：先执行脚本获取 API 配置
  │
  ▼
Agent 调用 skill_execute("python scripts/load_api_config.py"):
  → 脚本读取 storage/tenants/{current_tid}/after-sales-api.md
  → 返回 API 说明文本（base_url、端点列表、响应格式等）
  │
  ▼
Agent 根据 API 说明，调用 http_api 工具:
  {
    "method": "GET",
    "url": "${TENANT_AFTER_SALES_BASE_URL}/orders/ORD20260501001",
    "headers": {
      "Authorization": "Bearer ${TENANT_AFTER_SALES_API_KEY}"
    }
  }
  │
  ▼
http_api 工具执行:
  - ${TENANT_AFTER_SALES_API_KEY} 替换为环境变量中的实际 API Key
  - ${TENANT_AFTER_SALES_BASE_URL} 替换为环境变量中的实际 Base URL
  - 发起 HTTP GET 请求
  - 返回 JSON 响应
  │
  ▼
Agent 理解 JSON 响应，生成自然语言回复:
  "您的订单 ORD20260501001 已签收，包含 2 件商品A，总价 1299 元。"
```

#### 3.2.5 环境变量管理 — 按子智能体隔离的环境变量注入方案

凭据和配置通过 `http_api` 工具内置的环境变量替换机制注入。环境变量存储在 `subagent_env_vars` 表中，**按租户和子智能体隔离**，每个子智能体只获取自己的环境变量。

**设计原则**：

- **简单直接**：管理员创建 `var_name=var_value` 键值对，`var_name` 即为环境变量名，无需额外的映射配置
- **按子智能体隔离**：每个子智能体只获取自己的环境变量，不会看到其他子智能体的变量
- **双模式注入**：无论子智能体以 STANDALONE 模式（`process_message()`）还是委派模式（`execute_as_subagent()`）运行，环境变量都会被正确注入

**环境变量配置示例**（管理后台配置）：

```
子智能体: after-sales
  ┌─────────────────────────────────────┬─────────────────────────────────────┐
  │ 变量名 (var_name)                   │ 变量值 (var_value)                  │
  ├─────────────────────────────────────┼─────────────────────────────────────┤
  │ TENANT_AFTER_SALES_API_KEY          │ sk-xxxxxxxxxxxx                     │
  │ TENANT_AFTER_SALES_BASE_URL         │ https://erp.company.com/api/v1      │
  └─────────────────────────────────────┴─────────────────────────────────────┘
```

**注入机制**（适用于所有子智能体，非售后专用）：

1. **STANDALONE 模式**：Agent 在 `process_message()` 中根据 `tenant_id` 和当前子智能体名称，调用 `SubagentEnvVarDB.get_vars(tenant_id, subagent_name)` 获取该子智能体的环境变量，注入到 `os.environ`
2. **委派模式**：`SubagentExecutor` 在 `execute_as_subagent()` 中，根据传入的 `tenant_id` 和子智能体名称，同样调用 `SubagentEnvVarDB.get_vars()` 获取并注入环境变量
3. `http_api` 工具执行时，`${VAR_NAME}` 自动替换为环境变量值
4. 请求完成后清除所有临时注入的环境变量（通过 `_injected_env_vars` 字典记录已注入的变量名）

**不同子智能体的变量天然隔离**：

```
子智能体: after-sales 的变量:
  TENANT_AFTER_SALES_API_KEY = "sk-xxx"
  TENANT_AFTER_SALES_BASE_URL = "https://..."

子智能体: erp-assistant 的变量:
  TENANT_ERP_API_KEY = "sk-yyy"
  TENANT_ERP_API_SECRET = "secret-zzz"
```

> **注意**：此方案在多 worker 环境下需注意环境变量的进程隔离。如果并发量大，可考虑给 `http_api` 工具增加凭据注入参数（如 `credentials` 字段），避免使用全局环境变量。

### 3.3 子智能体环境变量管理

#### 3.3.1 数据库表设计

> **表名说明**：`subagent_env_vars` 是**系统级环境变量管理表**，不属于某个子智能体的业务数据，因此不以 `bs_` 开头。它按租户和子智能体维度存储环境变量，每个子智能体只能获取自己的变量。

```sql
-- 子智能体环境变量表（系统表，非业务表）
CREATE TABLE IF NOT EXISTS subagent_env_vars (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,                   -- 租户ID
    subagent_name TEXT NOT NULL,               -- 子智能体目录名（如 after-sales）
    var_name TEXT NOT NULL,                    -- 环境变量名（如 TENANT_AFTER_SALES_API_KEY）
    var_value TEXT NOT NULL,                   -- 环境变量值（明文存储）
    description TEXT,                          -- 变量说明（如"售后系统 API Key"）
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- 同一租户的同一子智能体下，变量名唯一
    CONSTRAINT uq_subagent_env_var UNIQUE (tenant_id, subagent_name, var_name)
);

CREATE INDEX IF NOT EXISTS idx_subagent_env_vars_tenant ON subagent_env_vars(tenant_id);
CREATE INDEX IF NOT EXISTS idx_subagent_env_vars_subagent ON subagent_env_vars(tenant_id, subagent_name);
```

**设计说明**：

| 设计决策 | 说明 |
|----------|------|
| `var_name` 即环境变量名 | 管理员直接设置环境变量名，如 `TENANT_AFTER_SALES_API_KEY`，无需额外的映射层 |
| 按子智能体隔离 | `subagent_name` 字段确保每个子智能体只获取自己的变量 |
| 明文存储 | 当前阶段使用明文存储，后续可按需增加加密 |
| 唯一约束 | 同一租户的同一子智能体下变量名不能重复 |

**数据示例**：

| tenant_id | subagent_name | var_name | var_value | description |
|-----------|---------------|----------|-----------|-------------|
| tenant_A | after-sales | TENANT_AFTER_SALES_API_KEY | sk-xxxxxxxx | 售后系统 API Key |
| tenant_A | after-sales | TENANT_AFTER_SALES_BASE_URL | https://erp.company.com/api/v1 | 售后系统 Base URL |
| tenant_A | erp-assistant | TENANT_ERP_API_KEY | sk-yyyyyyyy | ERP 系统 API Key |
| tenant_B | after-sales | TENANT_AFTER_SALES_API_KEY | sk-zzzzzzzz | 售后系统 API Key |

#### 3.3.2 环境变量管理类

```python
# src/db/subagent_env_var.py

class SubagentEnvVarDB:
    """子智能体环境变量管理"""

    @staticmethod
    def create(tenant_id: str, subagent_name: str, var_name: str,
               var_value: str, description: str = None) -> str:
        """创建环境变量"""

    @staticmethod
    def get_vars(tenant_id: str, subagent_name: str) -> Dict[str, str]:
        """获取指定租户和子智能体的所有环境变量，返回 {var_name: var_value}"""

    @staticmethod
    def get_by_id(var_id: int) -> Optional[Dict]:
        """获取单个环境变量"""

    @staticmethod
    def list_by_tenant(tenant_id: str) -> List[Dict]:
        """列出租户所有环境变量（按子智能体分组）"""

    @staticmethod
    def list_by_subagent(tenant_id: str, subagent_name: str) -> List[Dict]:
        """列出指定子智能体的所有环境变量"""

    @staticmethod
    def update(var_id: int, **kwargs) -> bool:
        """更新环境变量"""

    @staticmethod
    def delete(var_id: int) -> bool:
        """删除环境变量"""

    @staticmethod
    def upsert(tenant_id: str, subagent_name: str, var_name: str,
               var_value: str, description: str = None) -> str:
        """创建或更新环境变量（存在则更新，不存在则创建）"""
```

#### 3.3.3 环境变量管理 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/saas/tenant/subagent-env-vars` | 列出当前租户的所有环境变量 |
| GET | `/api/saas/tenant/subagent-env-vars/{subagent_name}` | 列出指定子智能体的环境变量 |
| POST | `/api/saas/tenant/subagent-env-vars` | 创建环境变量 |
| PUT | `/api/saas/tenant/subagent-env-vars/{var_id}` | 更新环境变量 |
| DELETE | `/api/saas/tenant/subagent-env-vars/{var_id}` | 删除环境变量 |

### 3.4 用户身份映射

#### 3.4.1 问题分析

Agent 调用外部售后系统 API 时，面临身份鸿沟：

```
本系统                         第三方企业系统
┌──────────────────┐           ┌──────────────────┐
│ user_id: user_x1 │    →→→    │ 员工工号: EMP00123 │
│ phone: 138xxxx   │           │ 或客户ID: C4567   │
│ name: 张三       │           │ 手机号: 138xxxx   │
└──────────────────┘           └──────────────────┘
```

本系统 `users` 表有 `user_id`、`phone`、`wx_openid` 等字段，但外部系统用不同的标识体系。需要在 Agent 调用 API 前，将本系统用户身份转换为外部系统能识别的用户身份。

#### 3.4.2 现有用户信息盘点

| 信息 | 存储位置 | Agent 是否可见 | 可用于身份映射 |
|------|----------|---------------|---------------|
| `user_id` | `users` 表，Agent `User` 对象 | ✅ 可见 | ❌ 第三方系统不认识 |
| `phone` | `users` 表 | ❌ Agent 只收到 `user_id` + `name` | ✅ 国内企业系统通用标识 |
| `username` | `users` 表 | ❌ 未传递到 Agent | ⚠️ 部分场景可用 |
| `wx_openid` | `users` 表 | ❌ 未传递到 Agent | ⚠️ 企业微信场景可用 |
| `tenant_id` | `users` 表，请求 ContextVar | ✅ 可见 | ✅ 用于租户隔离 |

**当前问题**：Agent 构造 `User` 对象时只填了 `user_id` 和 `name`（`main.py:558-563`），手机号等关键映射字段丢失。

#### 3.4.3 手机号映射（MVP，优先实现）

**核心思路**：国内企业系统（ERP、CRM、售后）几乎都以手机号作为用户唯一标识或查询条件。Agent 调用外部 API 时，将当前用户的手机号作为参数传入。

**实现方式**：

1. **SKILL.md 声明映射策略**（在"用户身份映射"章节）：

```markdown
## 用户身份映射

- **映射策略**：手机号映射
- **说明**：通过用户手机号作为第三方系统的用户标识。
```

2. **Agent 处理消息前，从 DB 查询用户手机号注入上下文**：

```
Agent.process_message(user=User(user_id="user_x1"))
  │
  ├─ 从 users 表查询该用户的 phone（一次 DB 查询）
  │   → phone = "13800138000"
  │
  ├─ 注入到 system prompt 的 [用户身份] 区块：
  │   "当前用户手机号：13800138000，姓名：张三"
  │
  ▼
LLM 读取 skill 中的身份映射说明 + [用户身份] 区块
  → 调用 http_api 时将手机号填入对应参数
```

3. **LLM 自行决定手机号放在哪个参数位置**：SKILL.md 中的 API 描述会提示 LLM 哪些参数需要用户手机号，LLM 从 system prompt 的 [用户身份] 区块读取后填入。

**手机号获取优先级**：

| 来源 | 说明 | 优先级 |
|------|------|--------|
| `users` 表 `phone` 字段 | 注册时绑定，最可靠 | 主方案 |
| 长期记忆 `memory_{user_id}.md` | 从对话中提取的手机号 | 补充 |
| 对话中询问用户 | 以上均无时的兜底 | 最后手段 |

**优势**：
- 零额外基础设施：不需要新建映射表，复用 `users` 表已有字段
- 国内企业系统覆盖率高：绝大多数 ERP/CRM/售后系统支持手机号查询
- LLM 天然理解：手机号是自然语言，LLM 能准确填入 API 参数
- 配置简单：SKILL.md 的"用户身份映射"章节即可声明

**局限**：
- 部分企业系统用员工工号而非手机号做标识 → 需要外部 ID 映射表
- 手机号可能变更（换号），但实际频率很低

#### 3.4.4 外部 ID 映射表（后续迭代）

**核心思路**：为每个租户建立"本系统用户 → 外部系统用户"的精确映射关系。适用于企业系统使用员工工号、客户ID 等非手机号标识的场景。

**数据库表设计**：

> **表名说明**：`user_external_identities` 是**系统级用户身份基础设施表**，不属于某个子智能体的业务数据，因此不以 `bs_` 开头。它存储本系统用户与外部系统用户的映射关系，供所有需要身份映射的子智能体共用。

```sql
-- 用户外部身份映射表（系统表，非业务表，供所有子智能体共用）
CREATE TABLE IF NOT EXISTS user_external_identities (
    id SERIAL PRIMARY KEY,
    mapping_id TEXT UNIQUE NOT NULL,          -- 映射ID: uei_{uuid12}
    tenant_id TEXT NOT NULL,                  -- 租户ID
    user_id TEXT NOT NULL,                    -- 本系统用户ID
    system_name TEXT NOT NULL,                -- 外部系统名称（如 "erp_system_a"）
    external_user_id TEXT NOT NULL,           -- 外部系统的用户ID（如 "EMP00123"）
    external_username TEXT,                   -- 外部系统的用户名（如 "zhangsan"）
    external_phone TEXT,                      -- 外部系统的手机号（用于交叉验证）
    metadata JSON,                            -- 扩展字段（部门、职位等）
    status TEXT NOT NULL DEFAULT 'active',    -- active/inactive
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 同一用户在同一外部系统中只能有一个有效映射
CREATE UNIQUE INDEX IF NOT EXISTS idx_uei_tenant_user_system
    ON user_external_identities(tenant_id, user_id, system_name)
    WHERE status = 'active';
-- 同一外部系统中，external_user_id 不重复
CREATE UNIQUE INDEX IF NOT EXISTS idx_uei_tenant_ext_id
    ON user_external_identities(tenant_id, system_name, external_user_id)
    WHERE status = 'active';
```

**映射关系示例**：

| tenant_id | user_id | system_name | external_user_id | external_username |
|-----------|---------|-------------|------------------|-------------------|
| tenant_A | user_x1 | erp_system | EMP00123 | 张三 |
| tenant_A | user_x1 | crm_system | C4567 | zhangsan |
| tenant_A | user_x2 | erp_system | EMP00456 | 李四 |

**SKILL.md 配置**（"用户身份映射"章节）：

```markdown
## 用户身份映射

- **映射策略**：外部 ID 映射
- **外部系统名称**：erp_system
- **说明**：通过外部系统映射表查找用户在 ERP 系统中的员工工号。
```

**Agent 调用流程**：

```
Agent.process_message(user=User(user_id="user_x1"))
  │
  ├─ 读取 SKILL.md 中"用户身份映射"章节
  │   → 映射策略 = "external_id", 外部系统 = "erp_system"
  │
  ├─ 查询 user_external_identities 表:
  │   WHERE tenant_id = 'tenant_A'
  │     AND user_id = 'user_x1'
  │     AND system_name = 'erp_system'
  │     AND status = 'active'
  │   → external_user_id = "EMP00123"
  │
  ├─ 注入到 system prompt:
  │   "当前用户在外部系统 erp_system 中的ID: EMP00123"
  │
  ▼
LLM 调用 http_api 时使用 EMP00123 作为用户标识
```

**数据录入方式**：

| 方式 | 适用场景 |
|------|----------|
| 管理后台手动录入 | 用户量小，一对一映射 |
| CSV 批量导入 | 企业初始化时批量建立映射 |
| 自动匹配（首次登录时通过手机号匹配） | 外部系统也用手机号时的自动关联 |

#### 3.4.5 身份映射演进路线

```
阶段一（MVP）
  ├─ 手机号映射（strategy: "phone"）
  │   - 从 users 表获取 phone，注入 system prompt
  │   - 零额外基础设施
  │   - 覆盖大多数国内企业系统场景
  │
  ▼
阶段二
  ├─ 新建 user_external_identities 表
  ├─ 外部 ID 映射（strategy: "external_id"）
  │   - 支持员工工号、客户ID 等非手机号标识
  │   - 管理后台录入 / CSV 导入
  │
  ▼
阶段三（远期）
  ├─ OAuth 2.0 / OIDC 用户授权
  │   - 用户首次使用时授权 Agent 访问外部系统
  │   - 平台安全存储 refresh_token
  │   - 完整的审计追踪和权限控制
```

#### 3.4.6 身份信息注入实现细节

无论哪种映射策略，核心实现都是**在 Agent 处理消息前，将用户身份信息注入 system prompt**：

**注入位置**：`agent.py` 中 `process_message()` 方法，构建 system prompt 时。

**注入内容格式**：

```
[用户身份]
姓名：张三
手机号：13800138000
外部系统ID（erp_system）：EMP00123   ← 仅 external_id 策略时有此行

说明：调用外部 API 时，根据 SKILL.md 中"用户身份映射"章节的配置，
使用上述信息作为用户身份标识。
```

**代码改动**：

| 文件 | 改动 | 说明 |
|------|------|------|
| `src/core/agent.py` | `process_message()` 中增加用户身份查询和注入 | 从 DB 查 phone / 从映射表查 external_id，追加到 system prompt |
| `src/models/user.py` | `User` 模型增加 `phone` 字段 | 让 `process_message()` 能传递手机号到 Agent |
| `src/main.py` | 构造 `User` 对象时填入 `phone` | 从 `current_user` dict 中取 phone 传给 User |

---

## 4. 子智能体详细设计

### 4.1 目录结构

```
subagents/after-sales/
  SUBAGENT.md                              ← 子智能体定义

src/skills/after-sales-api-1.0.0/
  SKILL.md                                 ← 通用技能：指引 LLM 读取租户 API 配置
  scripts/
    load_api_config.py                      ← 读取当前租户的 after-sales-api.md 并返回内容

src/skills/after-sales-core-1.0.0/
  SKILL.md                                 ← 售后核心技能（内部工单/退换货 CLI 命令说明）
  scripts/
    after_sales_tool.py                     ← 售后 CLI 脚本（init_tables + 工单/退换货 CRUD）

src/db/
  subagent_env_var.py                      ← 子智能体环境变量管理

storage/tenants/{tenant_id}/
  after-sales-api.md                        ← 租户的 API 说明文本（管理员在智能体管理页面编辑）
```

### 4.2 SUBAGENT.md 定义

```yaml
---
name: 售后服务助手
description: 处理用户的订单查询、退换货、商品使用问题等售后服务
version: 1.0.0
author: system
capabilities:
  - order_query           # 订单查询
  - return_exchange       # 退换货处理
  - product_support       # 商品使用指导
  - ticket_management     # 工单管理
triggers:
  keywords:
    - 订单
    - 退货
    - 换货
    - 退款
    - 售后
    - 维修
    - 工单
    - 物流
tools:
  inherit: true
  additional:
    - http_api               # 调用外部售后系统 API（查询订单、退换货等）
skills:
  allowed:
    - after-sales-core       # 内部工单/退换货 CLI 操作（降级方案）
    - after-sales-api       # 外部系统 API 调用知识（租户定制）
context:
  max_input_tokens: 10000
  max_output_tokens: 4000
---
```

**System Prompt 要点**（写在 `---` 之后的 body 中）：

1. **身份定位**：你是专业的售后服务助手，帮助用户解决订单和商品相关问题
2. **用户信息感知**：自动识别当前用户身份（从长期记忆中获取），无需用户重复提供手机号等基本信息
3. **外部系统调用**：使用 `http_api` 工具调用外部系统 API（订单查询、退换货等），根据注入的 API 配置组装请求
4. **标准化流程**：
   - 订单查询 → 调用外部 API 查询 → 用自然语言总结回复
   - 退换货 → 确认订单和商品 → 了解原因 → 调用外部 API 创建申请 → 跟进进度
   - 商品使用问题 → 了解问题 → 提供指导 → 必要时通过 after-sales-core 技能创建工单
   - 复杂问题 → 通过 after-sales-core 技能创建内部工单 → 转人工处理
5. **安全规则**：不直接展示 API 原始响应，用自然语言总结后回复用户

### 4.3 工具设计

#### 4.3.1 外部系统调用 — 复用 http_api 工具

LLM 根据注入的 API 配置，直接调用 `http_api` 工具与外部售后系统交互：

- **查询订单**：LLM 调用 `http_api(method="GET", url="...", headers={"Authorization": "Bearer ${TENANT_AFTER_SALES_API_KEY}"})`
- **创建退货**：LLM 调用 `http_api(method="POST", url="...", body={...})`
- **查询退货进度**：LLM 调用 `http_api(method="GET", url="...")`

无需为每种操作开发专用工具类。LLM 根据 SKILL.md 中的端点描述和响应示例，自行组装正确的请求参数和理解响应内容。

#### 4.3.2 内部工单/退换货操作 — after-sales-core 技能 CLI

> **设计决策**：内部工单和退换货操作不做成独立工具，而是封装在 `after-sales-core` 技能的 CLI 脚本中。这与 `trade-customer` 技能的 `customer_manager.py` 模式一致——LLM 通过 `use_skill` 加载技能知识，再通过 `skill_execute` 执行脚本命令。

**理由**：
- 工具是给 LLM 直接调用的原子操作，而工单/退换货涉及多步业务逻辑（参数校验、DB 写入、结果格式化），更适合封装在脚本中
- 减少工具数量，降低 LLM 选择工具的复杂度
- 与项目已有的 skill-based 模式一致（`trade-customer`、`competitor-research` 等都用这种方式）

**CLI 命令**（通过 `skill_execute` 调用）：

```bash
# 创建内部工单
python scripts/after_sales_tool.py create-ticket --user-id USER --description "问题" --category return [--order-id ORDER] [--priority normal]

# 查询工单
python scripts/after_sales_tool.py query-ticket --ticket-id ast_xxxx

# 列出用户工单
python scripts/after_sales_tool.py list-tickets --user-id USER [--status open]

# 创建退换货记录
python scripts/after_sales_tool.py create-return --user-id USER --order-id ORDER --type return --reason "原因" [--items JSON]

# 查询退换货记录
python scripts/after_sales_tool.py query-returns --user-id USER [--order-id ORDER]
```

**调用链路**：

```
LLM 识别需要创建内部工单
  → 调用 use_skill("after-sales-core") 加载技能知识
  → 调用 skill_execute("python scripts/after_sales_tool.py create-ticket --user-id ... --description ... --category ...")
  → 脚本执行 DB 操作，返回 JSON 结果
  → LLM 理解结果，生成自然语言回复
```

**脚本特点**：
- 自动初始化数据库表（`init_tables()`）
- 支持子进程独立运行（自动初始化 PostgreSQL 连接池）
- 返回标准 JSON 格式：`{"success": bool, ...}`

### 4.4 用户身份感知与映射

#### 身份感知 — 复用长期记忆系统

用户的基本信息（姓名、偏好等）通过长期记忆系统自动注入 Agent 上下文：

- **长期记忆文件**：`storage/memory/{tenant_id}/memory_{user_id}.md`，记录用户的个人信息、习惯、偏好等（按租户隔离存储）
- **上下文注入**：Agent 在新会话时自动加载用户长期记忆到 system prompt 的 `[用户记忆]` 区块

#### 身份映射 — 手机号注入

手机号通过 DB 查询注入 system prompt 的 `[用户身份]` 区块：

```
长期记忆注入:
  [用户记忆] 区块 → 姓名、偏好、习惯等

手机号注入:
  [用户身份] 区块 → 手机号: 13800138000
                    外部系统ID: EMP00123（如有映射）
```

#### 完整数据流：用户身份 → 外部 API 调用

```
用户发消息 "我要退货"
  │
  ▼
Agent.process_message()
  ├─ 从 users 表查询 phone（一次 DB 查询）
  │   → phone = "13800138000"
  ├─ 注入 [用户身份] 区块到 system prompt
  │   "手机号：13800138000"
  ├─ 加载长期记忆 → [用户记忆] 区块
  │   "姓名：张三，职位：采购经理"
  │
  ▼
LLM 识别需要调用外部 API
  ├─ 调用 use_skill("after-sales-api") → 加载技能指引
  ├─ 执行 skill_execute("python scripts/load_api_config.py")
  │   → 脚本读取 storage/tenants/{tid}/after-sales-api.md
  │   → 返回 API 配置文本
  │
  ▼
LLM 阅读 API 配置 + 用户身份信息，调用 http_api:
  http_api(
    method="POST",
    url="${TENANT_AFTER_SALES_BASE_URL}/returns",
    headers={"Authorization": "Bearer ${TENANT_AFTER_SALES_API_KEY}"},
    body={
      "order_id": "ORD123",
      "reason": "质量问题",
      "contact_phone": "13800138000",  ← LLM 从 [用户身份] 区块获取
      "contact_name": "张三"            ← LLM 从 [用户记忆] 区块获取
    }
  )
  │
  ▼
http_api 工具执行 → 环境变量替换 → 返回 JSON 响应
  │
  ▼
LLM 理解响应，生成回复
```

#### 用户身份信息的获取优先级

| 信息 | 来源 | 获取方式 | 优先级 |
|------|------|----------|--------|
| 手机号 | `users` 表 `phone` 字段 | `process_message()` 中 DB 查询 | 主方案 |
| 手机号 | 长期记忆 `## 个人介绍` | 自动注入的 [用户记忆] 区块 | 补充 |
| 手机号 | 对话中询问用户 | LLM 主动询问 | 兜底 |
| 姓名 | 长期记忆 `## 个人介绍` | 自动注入的 [用户记忆] 区块 | 主方案 |
| 外部系统 ID | `user_external_identities` 表 | `process_message()` 中 DB 查询 | 仅 external_id 策略 |

### 4.5 环境变量注入与 http_api 调用链路

#### 完整处理流程

```
子智能体运行（两种模式触发环境变量注入）:

模式 A: STANDALONE — Agent.process_message() 直接处理
  │
  ├─ 获取 tenant_id（从请求上下文）
  ├─ 获取当前子智能体名称（subagent_name）
  │
  ├─ [1] 环境变量注入（STANDALONE 模式）
  │   从 subagent_env_vars 表读取该租户该子智能体的环境变量:
  │     SubagentEnvVarDB.get_vars(tenant_id, subagent_name)
  │     → {"TENANT_AFTER_SALES_API_KEY": "sk-xxxx",
  │        "TENANT_AFTER_SALES_BASE_URL": "https://erp.company.com/api/v1"}
  │   注入到 os.environ，记录到 _injected_env_vars:
  │     os.environ["TENANT_AFTER_SALES_API_KEY"] = "sk-xxxx"
  │     os.environ["TENANT_AFTER_SALES_BASE_URL"] = "https://erp.company.com/api/v1"
  │
  ├─ [2] 身份解析 + 上下文组装
  │   ...（同上）
  │
  ▼
  LLM 处理对话，调用 http_api 工具...
  │
  ▼
  处理完成后清除注入的环境变量:
    for var_name in _injected_env_vars:
        os.environ.pop(var_name, None)

模式 B: 委派模式 — SubagentExecutor.execute_as_subagent()
  │
  ├─ executor.py 将 tenant_id 传递给子智能体 Agent 构造函数
  │
  ├─ [1] 环境变量注入（委派模式）
  │   子智能体 Agent 在 execute_as_subagent() 中:
  │     SubagentEnvVarDB.get_vars(tenant_id, subagent_name)
  │     → 只加载当前子智能体的环境变量
  │   注入到 os.environ，记录到 _injected_env_vars
  │
  ├─ [2] 处理任务...
  │
  ▼
  处理完成后清除注入的环境变量:
    for var_name in _injected_env_vars:
        os.environ.pop(var_name, None)
```

> **通用性说明**：此环境变量注入机制不是售后子智能体专用的。任何子智能体运行时，都会自动注入其租户配置的、属于该子智能体的环境变量。环境变量按 `subagent_name` 隔离，不同子智能体的变量互不干扰。

### 4.6 无外部系统时的降级策略

当租户未配置外部系统时，售后服务智能体应提供基础功能：

1. **对话式引导**：通过对话收集用户信息（订单号、问题描述等）
2. **创建内部工单**：使用 `use_skill("after-sales-core")` + `skill_execute` 调用 CLI 脚本，将售后请求记录到 `bs_after_sales_tickets` 表
3. **知识库查询**：使用 `knowledge_base_search` 工具查询商品使用指南
4. **人工转接提示**：告知用户联系人工客服

---

## 5. 数据库设计

> **表名规范**：售后子智能体目录名为 `after-sales`，按规范业务表前缀为 `bs_after_sales_`。业务数据表均包含 `tenant_id` 字段实现租户隔离。系统级基础设施表（`subagent_env_vars`、`user_external_identities`）不以 `bs_` 开头。

### 5.1 售后工单表（业务表）

```sql
-- 表名: bs_after_sales_tickets（子智能体 after-sales 的 tickets 业务表）
CREATE TABLE IF NOT EXISTS bs_after_sales_tickets (
    id SERIAL PRIMARY KEY,
    ticket_id TEXT UNIQUE NOT NULL,           -- 工单ID: ast_{uuid12}
    tenant_id TEXT,                           -- 租户ID
    user_id TEXT NOT NULL,                    -- 用户ID
    session_id TEXT,                          -- 会话ID
    order_id TEXT,                            -- 关联订单号（可选）
    category TEXT NOT NULL,                   -- 问题分类: order_issue/return/exchange/repair/usage/other
    status TEXT NOT NULL DEFAULT 'open',      -- open/in_progress/resolved/closed
    priority TEXT NOT NULL DEFAULT 'normal',  -- low/normal/high/urgent
    description TEXT NOT NULL,                -- 问题描述
    resolution TEXT,                          -- 处理结果
    external_ticket_id TEXT,                  -- 外部系统工单号（如有）
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 表名: bs_after_sales_ticket_messages（子智能体 after-sales 的 ticket_messages 业务表）
CREATE TABLE IF NOT EXISTS bs_after_sales_ticket_messages (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT,                           -- 租户ID
    ticket_id TEXT NOT NULL,                  -- 关联的工单ID（应用层校验）
    sender_type TEXT NOT NULL,                -- user/agent/staff（用户/智能体/人工客服）
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 5.2 退换货记录表（业务表）

```sql
-- 表名: bs_after_sales_returns（子智能体 after-sales 的 returns 业务表）
CREATE TABLE IF NOT EXISTS bs_after_sales_returns (
    id SERIAL PRIMARY KEY,
    return_id TEXT UNIQUE NOT NULL,           -- 退货单号: ret_{uuid12}
    tenant_id TEXT,                           -- 租户ID
    user_id TEXT NOT NULL,                    -- 用户ID
    session_id TEXT,                          -- 会话ID
    order_id TEXT NOT NULL,                   -- 订单号
    type TEXT NOT NULL,                       -- return(退货) / exchange(换货)
    reason TEXT NOT NULL,                     -- 原因
    items JSON,                               -- 涉及商品列表
    status TEXT NOT NULL DEFAULT 'pending',   -- pending/approved/rejected/in_progress/completed
    external_return_id TEXT,                  -- 外部系统退货单号
    refund_amount DECIMAL(10,2),              -- 退款金额
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 5.3 用户外部身份映射表（后续迭代，系统表）

> 此表为远期方案预留，MVP 阶段不实现。

表结构见 3.4.4 节。

### 5.4 表初始化

遵循项目规范，业务表在 skill 加载时通过 `init_tables()` 自动创建。`user_external_identities` 表在实际使用时再创建。

---

## 6. 文件变更清单

### 6.1 新增文件

| 文件 | 说明 |
|------|------|
| `subagents/after-sales/SUBAGENT.md` | 子智能体定义 |
| `src/skills/after-sales-api-1.0.0/SKILL.md` | 通用技能：指引 LLM 读取租户 API 配置 |
| `src/skills/after-sales-api-1.0.0/scripts/load_api_config.py` | 读取租户 after-sales-api.md 并返回内容 |
| `src/skills/after-sales-core-1.0.0/SKILL.md` | 售后核心技能定义（CLI 命令说明） |
| `src/skills/after-sales-core-1.0.0/scripts/after_sales_tool.py` | 售后 CLI 脚本（init_tables + 工单/退换货 CRUD） |
| `src/db/subagent_env_var.py` | 子智能体环境变量管理 |
| `src/api/subagent_env_var.py` | 子智能体环境变量管理 API |

### 6.2 修改文件

| 文件 | 改动 | 说明 |
|------|------|------|
| `src/main.py` | 路由注册 + User 构造 | 注册子智能体环境变量管理 API 和配置管理路由；构造 `User` 对象时填入 `phone` 字段 |
| `src/models/user.py` | User 模型 | 增加 `phone` 可选字段 |
| `src/core/agent.py` | `process_message()` + `execute_as_subagent()` | 新增 `[用户身份]` 区块注入 + 子智能体环境变量注入/清理（基于 `SubagentEnvVarDB.get_vars(tenant_id, subagent_name)`，按子智能体隔离注入） |
| `src/core/skill_loader.py` | `_init_skill_tables()` | 增加 after-sales-core 表初始化 |
| `src/db/database.py` | `_init_postgresql()` | 增加 after-sales-core 表初始化 |
| `src/saas/api/agent_instances.py` | 实例创建 | 通用实例管理，不含任何售后专用逻辑 |
| `deploy/db_update.sql` | 增量变更 | 新增 `subagent_env_vars` + `bs_after_sales_*` 表 |

---

## 7. 前端设计

### 7.1 租户管理端 — API 凭据配置页面

```
┌─────────────────────────────────────────────────────┐
│  数字员工管理 > 售后服务助手 > 外部系统配置              │
├─────────────────────────────────────────────────────┤
│                                                     │
│  📡 外部系统连接                                      │
│                                                     │
│  ┌───────────────────────────────────────────────┐  │
│  │  API 基础地址                                  │  │
│  │  [https://erp.company.com/api/v1            ] │  │
│  │                                               │  │
│  │  认证方式  [Bearer Token ▼]                    │  │
│  │                                               │  │
│  │  API Key  [••••••••••••••••]  [测试连接]       │  │
│  │                                               │  │
│  │  [保存]                                       │  │
│  └───────────────────────────────────────────────┘  │
│                                                     │
│  ⚙️ 外部系统 API 配置                                 │
│                                                     │
│  ┌───────────────────────────────────────────────┐  │
│  │  API 调用说明（Markdown 格式）:                  │  │
│  │  ┌─────────────────────────────────────────┐  │  │
│  │  │ # 外部系统 API 说明                       │  │  │
│  │  │                                         │  │  │
│  │  │ ## 基本信息                              │  │  │
│  │  │ - 系统名称: 企业售后系统                   │  │  │
│  │  │ - Base URL: ${TENANT_AFTER_SALES_...}   │  │  │
│  │  │ - 认证方式: Bearer Token                 │  │  │
│  │  │                                         │  │  │
│  │  │ ## API 端点列表                          │  │  │
│  │  │ ### 1. 查询订单列表                      │  │  │
│  │  │ GET ${BASE_URL}/orders?phone=...        │  │  │
│  │  │ ...                                     │  │  │
│  │  └─────────────────────────────────────────┘  │  │
│  │                                               │  │
│  │  [保存配置] [重置为默认模板]                     │  │
│  └───────────────────────────────────────────────┘  │
│                                                     │
│  📊 连接状态                                         │
│  ✅ 上次测试连接成功 (2026-05-19 10:30)               │
│                                                     │
│  👤 用户身份映射                                      │
│                                                     │
│  ┌───────────────────────────────────────────────┐  │
│  │  映射方式  [手机号映射 ▼]                        │  │
│  │                                               │  │
│  │  ℹ️ 手机号映射：使用用户注册时的手机号作为          │  │
│  │     第三方系统的用户标识，无需额外配置。            │  │
│  │                                               │  │
│  │  当前已有手机号的用户: 128/156                    │  │
│  │                                               │  │
│  │  [查看未绑定手机号的用户]                         │  │
│  └───────────────────────────────────────────────┘  │
│                                                     │
└─────────────────────────────────────────────────────┘
```

### 7.2 用户端 — 售后服务对话

无需特殊页面，复用现有的聊天界面。子智能体通过对话提供售后服务，业务数据页面可选：

```
business_pages:
  - id: tickets
    title: 我的工单
    route: /after-sales/tickets
  - id: returns
    title: 退换货记录
    route: /after-sales/returns
```

---

## 8. 安全设计

### 8.1 数据安全

| 安全措施 | 说明 |
|----------|------|
| 环境变量按子智能体隔离 | 每个子智能体只能获取自己的环境变量，防止跨子智能体泄露 |
| 租户隔离 | 环境变量查询必须带 `tenant_id`，防止跨租户访问 |
| 处理完成即清理 | 环境变量注入后通过 `_injected_env_vars` 记录，请求处理完成后立即清除 |
| 响应过滤 | 工具返回结果不包含外部 API 原始响应中的敏感字段 |

### 8.2 操作安全

| 安全措施 | 说明 |
|----------|------|
| 只读操作免确认 | 查询订单、查询进度等只读操作直接执行 |
| 写操作需确认 | 创建退货、创建工单等操作，Agent 先向用户确认再执行 |
| 操作日志 | 所有售后操作记录到 `bs_after_sales_*` 表 |
| 频率限制 | 同一用户的 API 调用频率受 `KeyPool` 信号量控制 |

---

## 9. 开发阶段规划

### Phase 1: 基础框架 + 用户身份映射（预计 3-4 天）

1. 创建售后服务子智能体定义（SUBAGENT.md）✅
2. 创建售后核心技能 CLI 脚本（`after-sales-core` 技能，含工单/退换货 CRUD）✅
3. 创建售后数据表（`bs_after_sales_tickets`、`bs_after_sales_returns`）✅
4. 创建 `subagent_env_vars` 表和环境变量管理 CRUD ✅
5. **用户身份映射（手机号方案）**：✅
   - `User` 模型增加 `phone` 字段
   - `main.py` 构造 `User` 时从 `current_user` 填入 `phone`
   - `agent.py` 的 `process_message()` 中增加 `[用户身份]` 区块注入
6. **子智能体环境变量注入机制**：✅
   - `agent.py` 的 `process_message()` 和 `execute_as_subagent()` 中基于 `SubagentEnvVarDB.get_vars(tenant_id, subagent_name)` 实现按子智能体隔离的环境变量注入
   - 所有子智能体运行时自动注入该租户配置的、属于该子智能体的环境变量
7. 手动测试：在无外部系统时，通过对话创建内部工单；验证 Agent 能感知用户手机号

### Phase 2: 外部系统集成（预计 2-3 天）

1. 创建 `after-sales-api` 通用技能 SKILL.md + `load_api_config.py` 脚本 ✅
2. 创建默认 API 配置模板，租户开通时自动生成 `after-sales-api.md` ✅
3. ~~实现凭据注入到环境变量的机制~~ → 已在 Phase 1 中作为通用机制实现 ✅
4. 实现 API 配置管理（由 `after-sales-api` skill 的 `load_api_config.py` 按需读取，前端直接操作文件）✅
5. 手动测试：配置 mock API，验证 LLM 能通过 use_skill → skill_execute → http_api 完整链路调用外部系统

### Phase 3: 前端 & 管理（预计 2-3 天）

1. 租户管理端 API 凭据配置页面
2. API 端点配置编辑器（JSON 编辑器 + 校验 + 身份映射策略选择）
3. 售后业务数据页面（工单列表、退换货记录）
4. 连接测试功能（使用 http_api 工具测试连接）

### Phase 4: 生产化（预计 1-2 天）

1. 错误处理和降级策略完善
2. 操作审计日志
3. 多 worker 环境变量隔离方案（如改为 http_api 支持直接传 credentials 参数）
4. 文档和部署脚本

### 后续迭代（远期）

1. 新建 `user_external_identities` 表，支持外部 ID 映射（员工工号等非手机号标识）
2. 管理后台用户身份映射管理页面（手动录入 / CSV 导入）
3. OAuth 2.0 / OIDC 用户授权模式

---

## 10. 待讨论事项

1. **渠道用户的身份映射**：当前渠道（企业微信/钉钉/飞书）回调 `user=None`（`channel_routes.py:94-98`），Agent 完全没有用户身份。需要先解决渠道用户到本系统用户的映射（auto_register.py 的弱映射需要加强），才能在 IM 渠道提供售后服务。

2. **手机号缺失时的体验**：部分用户可能未绑定手机号。当 `users.phone` 为空时，Agent 应在对话中主动询问用户手机号，并提示用户在个人设置中绑定手机号。是否需要提供绑定手机号的快捷入口？

3. **API 配置管理方式**：
   - 采用 SKILL.md 方案：租户管理员通过 Markdown 编辑器修改 API 调用知识
   - 公共模板提供通用 REST API 描述，租户根据自己企业的实际 API 修改
   - 优势：复用已有的 skill 管理接口（`/api/saas/skills/`），无需额外开发
   - 后续迭代可增加预置模板选择（如"淘宝/京东/通用 ERP"模板），降低配置门槛

4. **外部 API 超时与重试**：企业内网 API 可能较慢。`http_api` 工具默认超时 30 秒，可通过参数调整。建议写操作不重试（避免重复），读操作可适当超时延长。

5. **多 worker 环境变量隔离**：当前方案通过 `os.environ` 注入环境变量，使用 `_injected_env_vars` 字典记录已注入的变量并在处理完成后清理。多 worker 并发时同一进程内的不同请求可能冲突。后续可考虑给 `http_api` 工具增加直接传 credentials 的参数。

6. **人工转接机制**：当智能体无法解决时，如何转接到人工客服？是否需要对接工单系统的客服分配功能？

7. **售后知识库**：是否需要为每个租户维护商品使用指南的知识库？还是统一使用平台的商品信息？

8. **LLM 调用 http_api 的可靠性**：LLM 可能组装错误的 URL 或 body。需要在 system prompt 中给出清晰的 API 配置和 example_response，并在响应错误时引导 LLM 重试。

---

## 附录 A: 与现有子智能体的复用关系

| 组件 | 复用方式 |
|------|----------|
| 子智能体加载/注册/执行 | 完全复用，按标准格式定义 SUBAGENT.md |
| Skill 系统 | 完全复用，售后核心逻辑和 API 配置读取分别封装为 skill |
| http_api 工具 | 完全复用，无需开发新工具即可调用外部 API |
| skill_execute | 复用，执行 load_api_config.py 脚本读取租户配置 |
| 内部工单/退换货操作 | 封装在 after-sales-core 技能的 CLI 脚本中，非独立工具 |
| 凭据加密 | 复用 `encryption_manager`（后续按需启用） |
| SaaS 订阅控制 | 复用，售后服务作为新的 subagent_type |
| 数据表规范 | 复用，`bs_` 前缀 + `tenant_id` 隔离 |
| 用户手机号 | 复用 `users` 表已有 `phone` 字段 |

## 附录 B: 术语表

| 术语 | 说明 |
|------|------|
| API 配置 (SKILL.md) | 描述外部系统 API 端点的 Markdown 文件，租户可定制 |
| http_api 工具 | 已有的 HTTP 请求工具，支持环境变量替换和多种请求方式 |
| 外部系统 | 企业内部的订单管理、ERP、售后管理系统 |
| 工单 (Ticket) | 用户发起的售后请求记录 |
| 身份映射 (Identity Mapping) | 将本系统用户身份对应到第三方企业系统用户身份的机制 |
| 手机号映射 | MVP 方案，通过用户手机号作为外部系统的用户标识 |
| 外部 ID 映射 | 通过映射表精确关联本系统用户与外部系统用户的标识 |
