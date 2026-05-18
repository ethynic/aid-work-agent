# 售后服务通用子智能体 — 设计文档

> 版本: v1.1 | 创建日期: 2026-05-06 | 状态: 待审核
> v1.1 变更：用户身份识别改为复用长期记忆系统，删除 `inject_user_profile` 方案

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
2. **企业系统对接**：每个企业的订单系统、售后系统各不相同，需要一种可插拔的外部系统集成机制
3. **统一对话流程**：无论底层对接哪个企业系统，用户侧的对话体验保持一致

### 1.3 与现有子智能体的对比

| 维度 | 现有子智能体（外贸/合同/旅游） | 售后服务子智能体 |
|------|------|------|
| 数据来源 | LLM 生成 + 内部数据库 | **外部企业系统 API** |
| 系统集成 | 硬编码特定系统（EAS） | **可插拔适配器模式** |
| 用户身份 | 不依赖用户信息 | **通过长期记忆感知用户信息** |
| 多租户定制 | 无 | **每租户不同的 API 适配器** |

---

## 2. 现有基础设施评估

### 2.1 已具备的能力（可直接复用）

| 能力 | 现有组件 | 适用性 |
|------|----------|--------|
| 子智能体定义与加载 | `SubagentLoader` + `SUBAGENT.md` | ✅ 完全适用，售后服务智能体按标准格式定义 |
| 技能系统 | `SkillLoader` + `SkillRegistry` + `SkillExecutor` | ✅ 可将售后流程封装为 skill |
| 用户身份传递 | `agent.process_message(user=User(...))` | ✅ Web 聊天已传递 user_id |
| 用户长期记忆 | `LongTermMemory` + `memory_{user_id}.md`（Phase 3 设计） | ✅ 用户信息通过长期记忆自动注入上下文，售后智能体直接消费 |
| 租户上下文 | `TenantContextMiddleware` + `get_current_tenant_id()` | ✅ 每租户隔离 |
| 凭据加密存储 | `encryption_manager` + Fernet | ✅ 可复用加密机制 |
| 业务数据表规范 | `bs_` 前缀 + `tenant_id` 隔离 | ✅ 售后数据表遵循此规范 |
| SaaS 订阅控制 | `SubscriptionDB.get_allowed_subagent_types()` | ✅ 售后智能体作为子智能体类型注册 |
| 工具系统 | `BaseTool` + `ToolRegistry` + `ToolExecutor` | ✅ 可定义售后专用工具 |
| 渠道接入 | WeCom / DingTalk / Feishu 适配器 | ✅ 用户可通过 IM 直接售后 |
| 定时任务 | `CreateScheduledTaskTool` | ✅ 可用于售后跟进提醒 |

### 2.2 缺失的基础设施（需新建）

| 缺失能力 | 影响 | 解决方案 |
|----------|------|----------|
| **外部 API 适配器机制** | 无法连接不同企业的订单/售后系统 | 新建 `ExternalAdapter` 适配器框架 |
| **租户级 API 凭据管理** | 无法按租户存储外部系统 API Key/Secret | 新建 `tenant_api_credentials` 表 |
| **售后工单数据表** | 无售后工单持久化 | 新建 `bs_after_sales_*` 系列表 |

> **关于用户身份识别**：不做独立方案，复用记忆系统 Phase 3 的长期记忆（`memory_{user_id}.md`）。用户信息（姓名、手机号、偏好等）已由每日自动总结沉淀到长期记忆中，Agent 在新会话时自动加载为上下文。售后智能体无需额外机制即可感知用户身份。

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
  │     （姓名、手机号、偏好、习惯等，由记忆系统 Phase 3 自动注入上下文）
  │
  ├─ [意图识别] ← LLM 判断用户诉求类型
  │
  ├─ [外部系统查询] ← ExternalAdapterRegistry
  │     │
  │     ├─ 适配器 A（企业A的订单系统）
  │     ├─ 适配器 B（企业B的 ERP 系统）
  │     └─ 适配器 C（通用 REST API）
  │
  ├─ [售后工具调用] ← after_sales_tools
  │     ├─ query_order          查询订单
  │     ├─ create_return        创建退货
  │     ├─ query_return_status  查询退货进度
  │     ├─ create_ticket        创建工单
  │     └─ query_ticket         查询工单
  │
  └─ [回复生成] ← LLM 整合查询结果，生成专业回复
```

### 3.2 外部系统适配器设计（核心新增组件）

#### 3.2.1 适配器注册表

每个租户可以配置自己的外部系统适配器。适配器是一个 **JSON 配置 + Python 脚本**的组合，存储在租户的 skill 目录中。

**存储路径**：
```
storage/tenants/{tenant_id}/adapters/
  after_sales/
    adapter.json          ← 适配器配置
    adapter.py            ← 适配器脚本（可选，用于复杂逻辑）
```

**adapter.json 配置格式**：

```json
{
  "name": "企业A售后系统",
  "description": "对接企业A的 ERP 售后模块",
  "version": "1.0.0",
  "type": "after_sales",

  "base_url": "https://erp.company-a.com/api/v1",
  "auth_type": "bearer",
  "credentials_ref": "after_sales_api",

  "apis": {
    "query_order": {
      "method": "GET",
      "path": "/orders/{order_id}",
      "params": {
        "order_id": {"source": "tool_arg", "required": true, "description": "订单号"},
        "phone": {"source": "user_profile", "field": "phone", "description": "用户手机号（用于验证）"}
      },
      "response_mapping": {
        "order_id": "$.data.orderNo",
        "status": "$.data.status",
        "items": "$.data.items[*].{name: productName, qty: quantity, price: unitPrice}",
        "total_amount": "$.data.totalAmount",
        "created_at": "$.data.createTime"
      }
    },
    "list_orders": {
      "method": "GET",
      "path": "/orders",
      "params": {
        "phone": {"source": "user_profile", "field": "phone", "required": true},
        "page": {"source": "tool_arg", "default": 1},
        "page_size": {"source": "tool_arg", "default": 10}
      },
      "response_mapping": {
        "orders": "$.data.list[*].{id: orderNo, status: status, amount: totalAmount, date: createTime}",
        "total": "$.data.total"
      }
    },
    "create_return": {
      "method": "POST",
      "path": "/returns",
      "body": {
        "order_id": {"source": "tool_arg", "required": true},
        "reason": {"source": "tool_arg", "required": true},
        "items": {"source": "tool_arg", "required": true},
        "contact_phone": {"source": "user_profile", "field": "phone"}
      },
      "response_mapping": {
        "return_id": "$.data.returnNo",
        "status": "$.data.status"
      }
    },
    "query_return": {
      "method": "GET",
      "path": "/returns/{return_id}",
      "params": {
        "return_id": {"source": "tool_arg", "required": true}
      },
      "response_mapping": {
        "return_id": "$.data.returnNo",
        "status": "$.data.status",
        "progress": "$.data.progressSteps[*].{step: name, done: completed}",
        "refund_amount": "$.data.refundAmount"
      }
    },
    "create_ticket": {
      "method": "POST",
      "path": "/tickets",
      "body": {
        "order_id": {"source": "tool_arg"},
        "category": {"source": "tool_arg", "required": true},
        "description": {"source": "tool_arg", "required": true},
        "contact_phone": {"source": "user_profile", "field": "phone"},
        "contact_name": {"source": "user_profile", "field": "username"}
      },
      "response_mapping": {
        "ticket_id": "$.data.ticketNo",
        "status": "$.data.status"
      }
    },
    "query_ticket": {
      "method": "GET",
      "path": "/tickets/{ticket_id}",
      "params": {
        "ticket_id": {"source": "tool_arg", "required": true}
      },
      "response_mapping": {
        "ticket_id": "$.data.ticketNo",
        "status": "$.data.status",
        "replies": "$.data.replies[*].{content: content, time: createTime, from: source}"
      }
    }
  }
}
```

#### 3.2.2 参数来源（Source）设计

`params` 和 `body` 中的 `source` 字段定义了参数值的来源：

| Source 值 | 说明 | 示例 |
|-----------|------|------|
| `tool_arg` | 来自工具调用参数（LLM 提供） | `order_id`, `reason` |
| `user_profile` | 来自用户长期记忆（自动注入） | `phone`, `username` |
| `credential` | 来自租户 API 凭据（自动注入） | `api_key`, `token` |
| `static` | 静态值（配置中写死） | `version: "2.0"` |
| `context` | 来自对话上下文（之前工具结果） | `session_id` |

> `user_profile` 的值从 `memory_{user_id}.md` 的 `## 个人介绍` 分类解析。如果长期记忆中没有对应信息，回退到 `UserDB` 查询。

#### 3.2.3 适配器类型

适配器有两种实现方式，根据企业系统的复杂度选择：

**方式一：声明式适配器（adapter.json）**

适用于标准的 REST API，只需配置 API 端点和字段映射，无需写代码。

- 适配器引擎（`ExternalAdapterEngine`）根据 `adapter.json` 配置自动发起 HTTP 请求
- 支持 JSONPath 响应映射，将外部系统响应转换为统一格式
- 认证方式支持：`bearer`（API Key in Header）、`basic`（用户名密码）、`oauth2`（Token 刷新）、`hmac`（签名认证）

**方式二：脚本式适配器（adapter.json + adapter.py）**

适用于复杂的非标准接口（如 SOAP、需要签名计算、需要多次请求组合等）。

- `adapter.json` 中声明 `"script": "adapter.py"`
- `adapter.py` 是一个 Python 脚本，通过 CLI 接收参数，输出 JSON 结果
- 脚本通过 `skill_execute` 机制运行（复用现有的 `SkillExecutor`）
- 与现有 skill 脚本的运行方式一致

```python
# adapter.py 示例
import sys
import json
import httpx
import hashlib
import time

def main():
    args = json.loads(sys.argv[1])

    if args["action"] == "query_order":
        # 自定义签名逻辑
        timestamp = str(int(time.time()))
        sign = hashlib.md5(f"{args['api_key']}{timestamp}".encode()).hexdigest()

        resp = httpx.get(
            f"{args['base_url']}/orders/{args['order_id']}",
            headers={"X-Api-Key": args["api_key"], "X-Timestamp": timestamp, "X-Sign": sign}
        )
        data = resp.json()

        # 输出统一格式
        print(json.dumps({
            "success": True,
            "data": {
                "order_id": data["orderNo"],
                "status": data["statusCode"],
                "items": data.get("items", [])
            }
        }, ensure_ascii=False))

if __name__ == "__main__":
    main()
```

#### 3.2.4 认证方式支持

| 认证类型 | 配置方式 | 实现 |
|----------|----------|------|
| `bearer` | `{"auth_type": "bearer", "credentials_ref": "xxx"}` | API Key 放入 `Authorization: Bearer {key}` |
| `basic` | `{"auth_type": "basic", "credentials_ref": "xxx"}` | `Authorization: Basic base64(user:pass)` |
| `api_key` | `{"auth_type": "api_key", "header_name": "X-Api-Key", "credentials_ref": "xxx"}` | API Key 放入指定 Header |
| `oauth2` | `{"auth_type": "oauth2", "token_url": "...", "credentials_ref": "xxx"}` | 自动刷新 Token |
| `hmac` | 由脚本适配器自行处理 | 灵活支持各种签名算法 |

### 3.3 租户 API 凭据管理（新增组件）

#### 3.3.1 数据库表设计

```sql
-- 租户外部 API 凭据表
CREATE TABLE IF NOT EXISTS tenant_api_credentials (
    id SERIAL PRIMARY KEY,
    credential_id TEXT UNIQUE NOT NULL,        -- 凭据ID: tac_{uuid12}
    tenant_id TEXT NOT NULL,                   -- 租户ID
    adapter_type TEXT NOT NULL,                -- 适配器类型: after_sales / erp / crm 等
    credential_name TEXT NOT NULL,             -- 凭据名称（如"企业A ERP API"）
    auth_type TEXT NOT NULL DEFAULT 'bearer',  -- 认证方式: bearer/basic/api_key/oauth2
    api_key_encrypted TEXT,                     -- 加密存储的 API Key
    api_secret_encrypted TEXT,                  -- 加密存储的 API Secret
    extra_config JSON,                          -- 额外配置（如 token_url, scope 等）
    status TEXT NOT NULL DEFAULT 'active',      -- active/inactive
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tenant_api_cred_tenant ON tenant_api_credentials(tenant_id);
CREATE INDEX IF NOT EXISTS idx_tenant_api_cred_type ON tenant_api_credentials(adapter_type);
```

#### 3.3.2 凭据管理类

```python
# src/db/tenant_api_credential.py

class TenantApiCredentialDB:
    """租户外部 API 凭据管理"""

    @staticmethod
    def create(tenant_id, adapter_type, credential_name, auth_type,
               api_key, api_secret=None, extra_config=None) -> str:
        """创建凭据（加密存储）"""

    @staticmethod
    def get_by_id(credential_id: str) -> Optional[Dict]:
        """获取凭据（解密）"""

    @staticmethod
    def get_by_tenant_and_type(tenant_id: str, adapter_type: str) -> Optional[Dict]:
        """按租户和适配器类型获取凭据（解密）"""

    @staticmethod
    def list_by_tenant(tenant_id: str) -> List[Dict]:
        """列出租户所有凭据（脱敏）"""

    @staticmethod
    def update(credential_id: str, **kwargs) -> bool:
        """更新凭据"""

    @staticmethod
    def delete(credential_id: str) -> bool:
        """删除凭据（软删除）"""
```

#### 3.3.3 凭据管理 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/saas/tenant/api-credentials` | 列出当前租户的 API 凭据（脱敏） |
| POST | `/api/saas/tenant/api-credentials` | 创建 API 凭据 |
| PUT | `/api/saas/tenant/api-credentials/{credential_id}` | 更新 API 凭据 |
| DELETE | `/api/saas/tenant/api-credentials/{credential_id}` | 删除 API 凭据 |

---

## 4. 子智能体详细设计

### 4.1 目录结构

```
subagents/after-sales/
  SUBAGENT.md                              ← 子智能体定义

src/skills/after-sales-core-1.0.0/
  SKILL.md                                 ← 售后核心技能
  scripts/
    adapter_engine.py                       ← 声明式适配器引擎
    after_sales_tool.py                     ← 售后工具脚本

src/tools/after_sales/
  after_sales_query_tool.py                ← 售后查询工具（注册到 ToolRegistry）
  after_sales_action_tool.py              ← 售后操作工具（退换货、工单）

src/db/
  tenant_api_credential.py                 ← 租户 API 凭据（新增）
  after_sales_db.py                        ← 售后数据表（新增）
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
    - after_sales_query
    - after_sales_action
skills:
  allowed:
    - after-sales-core
context:
  max_input_tokens: 10000
  max_output_tokens: 4000
---
```

**System Prompt 要点**（写在 `---` 之后的 body 中）：

1. **身份定位**：你是专业的售后服务助手，帮助用户解决订单和商品相关问题
2. **用户信息感知**：自动识别当前用户身份，无需用户重复提供手机号等基本信息
3. **标准化流程**：
   - 订单查询 → 确认订单 → 查看详情
   - 退换货 → 确认订单和商品 → 了解原因 → 创建申请 → 跟进进度
   - 商品使用问题 → 了解问题 → 提供指导 → 必要时创建工单
   - 复杂问题 → 创建工单 → 转人工处理
4. **安全规则**：不直接展示 API 原始响应，用自然语言总结后回复用户

### 4.3 工具设计

#### 4.3.1 售后查询工具（after_sales_query）

```python
class AfterSalesQueryTool(BaseTool):
    """售后查询工具"""

    name = "after_sales_query"
    description = "查询用户的售后相关信息，包括订单、退换货进度、工单状态等"
    display_name = "售后查询"

    class InputModel(BaseModel):
        query_type: str = Field(
            ...,
            description="查询类型：order_detail(订单详情), order_list(订单列表), return_status(退货进度), ticket_status(工单状态)"
        )
        identifier: str = Field(
            ...,
            description="查询标识：订单号、退货单号或工单号"
        )
```

#### 4.3.2 售后操作工具（after_sales_action）

```python
class AfterSalesActionTool(BaseTool):
    """售后操作工具"""

    name = "after_sales_action"
    description = "执行售后操作，包括创建退换货申请、创建工单等"
    display_name = "售后操作"

    class InputModel(BaseModel):
        action_type: str = Field(
            ...,
            description="操作类型：create_return(创建退货), create_exchange(创建换货), create_ticket(创建工单)"
        )
        order_id: Optional[str] = Field(None, description="关联的订单号")
        reason: Optional[str] = Field(None, description="退换货原因")
        items: Optional[str] = Field(None, description="涉及的商品（JSON 字符串）")
        description: Optional[str] = Field(None, description="问题描述（工单用）")
        category: Optional[str] = Field(None, description="问题分类")
```

### 4.4 用户身份感知 — 复用长期记忆系统

#### 设计思路

售后服务智能体**不做独立的用户信息注入机制**。用户身份识别完全复用记忆系统 Phase 3 的长期记忆设计：

- **长期记忆文件**：`storage/memory/memory_{user_id}.md`，记录用户的个人信息、习惯、偏好等
- **信息来源**：每日自动总结从当天会话中提取用户特征（姓名、手机号、职位、习惯等）
- **上下文注入**：Agent 在新会话时自动加载用户长期记忆到 system prompt 的 `[用户记忆]` 区块

这意味着售后服务智能体不需要做任何特殊处理——当长期记忆系统上线后，所有子智能体（包括售后服务）都能自动感知用户信息。

#### 长期记忆中与售后服务相关的信息

长期记忆的 `## 个人介绍` 分类会沉淀用户的姓名、手机号等信息。这些信息在售后服务场景中会被适配器引擎使用：

```
# 用户记忆（memory_user_xxxx.md）

## 个人介绍

- 姓名：张三
- 手机号：13800138000
- 职位：采购经理
- 公司：XX科技

## 工作习惯

- 常用快递：顺丰
- 偏好简短的回复
```

当售后工具调用外部 API 时，适配器引擎的参数来源 `source: "user_profile"` 将从长期记忆中提取用户手机号等信息，自动填充到 API 请求参数中（如 `contact_phone`、`contact_name`）。

#### 长期记忆 → 适配器参数 的数据流

```
用户发消息 "我要退货"
  │
  ▼
Agent.process_message()
  ├─ 加载长期记忆 → system prompt 中注入 [用户记忆] 区块
  │   （包含 "手机号：13800138000"）
  │
  ▼
LLM 决定调用 after_sales_action(create_return, order_id="ORD123", reason="质量问题")
  │
  ▼
AfterSalesActionTool.execute()
  ├─ 从 system prompt 上下文中提取用户信息
  │   （或从 LongTermMemory.get_memory(user_id) 读取）
  │
  ▼
ExternalAdapterEngine.execute(
    action="create_return",
    tool_args={"order_id": "ORD123", "reason": "质量问题"},
    user_profile={"phone": "13800138000", "name": "张三"}  ← 来自长期记忆
  )
  │
  ▼
外部 API 调用: POST /returns
  body: {
    "order_id": "ORD123",
    "reason": "质量问题",
    "contact_phone": "13800138000",  ← 自动填充
    "contact_name": "张三"            ← 自动填充
  }
```

#### user_profile 数据来源的两种方式

| 方式 | 说明 | 优先级 |
|------|------|--------|
| **长期记忆解析** | 从 `memory_{user_id}.md` 的 `## 个人介绍` 分类解析结构化字段 | 主方案 |
| **UserDB 回退** | 如果长期记忆中没有手机号等信息，从 `users` 表回退查询 | 兜底 |

适配器引擎中 `user_profile` 参数解析逻辑：

```python
def _get_user_profile(self, user_id: str) -> dict:
    """获取用户信息用于适配器参数填充"""
    profile = {}

    # 1. 尝试从长期记忆解析
    try:
        from src.memory.long_term import LongTermMemory
        ltm = LongTermMemory()
        sections = ltm.get_memory_sections(user_id)
        if "个人介绍" in sections:
            for item in sections["个人介绍"]:
                if "手机号" in item or "电话" in item:
                    profile["phone"] = item.split("：")[-1].strip()
                if "姓名" in item:
                    profile["name"] = item.split("：")[-1].strip()
    except Exception:
        pass

    # 2. 回退到 UserDB
    if not profile.get("phone") or not profile.get("name"):
        from src.db.models import UserDB
        user = UserDB.get_by_id(user_id)
        if user:
            profile.setdefault("phone", user.get("phone", ""))
            profile.setdefault("name", user.get("username", ""))

    return profile
```

#### 对记忆系统 Phase 3 的补充建议

为了更好地支持售后等业务场景的自动化用户识别，建议在长期记忆的每日总结 Prompt 中增加以下提取规则：

```
提取规则补充：
7. 用户的联系方式（手机号、邮箱、微信号等），用于业务场景自动填充
8. 用户的常用地址（如果有提到），用于物流和售后场景
```

同时在 `## 个人介绍` 分类的条目格式中，建议使用结构化的键值对格式，便于程序化解析：

```markdown
## 个人介绍

- 姓名：张三
- 手机号：13800138000
- 邮箱：zhangsan@company.com
- 职位：采购经理
- 部门：供应链管理部
- 公司：XX科技有限公司
- 常用地址：北京市朝阳区XX路XX号
```

### 4.5 适配器引擎（核心组件）

#### 4.5.1 ExternalAdapterEngine

```python
# src/skills/after-sales-core-1.0.0/scripts/adapter_engine.py

class ExternalAdapterEngine:
    """外部系统适配器引擎

    职责：
    1. 加载租户的 adapter.json 配置
    2. 根据 action 查找对应的 API 定义
    3. 组装请求参数（合并 tool_arg / user_profile / credential）
    4. 发起 HTTP 请求
    5. 解析响应（JSONPath 映射）
    6. 返回统一格式的结果
    """

    def __init__(self, tenant_id: str, adapter_type: str = "after_sales"):
        self.tenant_id = tenant_id
        self.adapter_type = adapter_type
        self.config = self._load_config()
        self.credentials = self._load_credentials()

    def execute(self, action: str, tool_args: dict, user_profile: dict) -> dict:
        """执行一个适配器动作"""
        api_def = self.config["apis"][action]

        # 1. 组装参数
        params = self._resolve_params(api_def, tool_args, user_profile)

        # 2. 发起请求
        response = self._make_request(api_def, params)

        # 3. 映射响应
        result = self._map_response(api_def.get("response_mapping"), response)

        return {"success": True, "data": result}

    def _load_config(self) -> dict:
        """加载 adapter.json"""
        config_path = f"storage/tenants/{self.tenant_id}/adapters/{self.adapter_type}/adapter.json"
        # 支持回退到平台默认配置
        if not os.path.exists(config_path):
            config_path = f"adapters/{self.adapter_type}/adapter.json"  # 内置默认
        with open(config_path) as f:
            return json.load(f)

    def _load_credentials(self) -> dict:
        """从 DB 加载解密后的凭据"""
        cred = TenantApiCredentialDB.get_by_tenant_and_type(
            self.tenant_id, self.adapter_type
        )
        if cred:
            return {
                "api_key": cred.get("api_key"),
                "api_secret": cred.get("api_secret"),
            }
        return {}

    def _resolve_params(self, api_def, tool_args, user_profile) -> dict:
        """解析参数来源，组装最终参数"""
        resolved = {}
        source_map = {
            "tool_arg": tool_args,
            "user_profile": user_profile,
            "credential": self.credentials,
        }
        # ... 遍历 api_def 的 params/body，根据 source 解析值
        return resolved

    def _make_request(self, api_def, params) -> dict:
        """发起 HTTP 请求"""
        # 根据 auth_type 设置认证头
        # 根据 method 调用 GET/POST
        # 返回 JSON 响应

    def _map_response(self, mapping: dict, response: dict) -> dict:
        """用 JSONPath 映射响应到统一格式"""
        # ... JSONPath 解析
```

#### 4.5.2 工具调用链路

```
Agent.process_message()
  → LLM 决定调用 after_sales_query 工具
    → AfterSalesQueryTool.execute(query_type="order_detail", identifier="ORD123")
      → 从 tenant_id 获取适配器配置
      → ExternalAdapterEngine(tenant_id).execute(
            action="query_order",
            tool_args={"order_id": "ORD123"},
            user_profile={"phone": "13800138000", "username": "张三"}
        )
        → 读取 adapter.json
        → 从 DB 读取 API 凭据
        → 组装 HTTP 请求
        → 调用外部 API
        → 映射响应
      → 返回统一格式结果
    → 工具结果返回 Agent
  → LLM 基于结果生成回复
```

### 4.6 无适配器时的降级策略

当租户未配置外部系统适配器时，售后服务智能体应提供基础功能：

1. **对话式引导**：通过对话收集用户信息（订单号、问题描述等）
2. **创建内部工单**：将售后请求记录到 `bs_after_sales_tickets` 表
3. **知识库查询**：使用 `knowledge_base_search` 工具查询商品使用指南
4. **人工转接提示**：告知用户联系人工客服

---

## 5. 数据库设计

### 5.1 售后工单表

```sql
-- 售后工单（无外部系统时的内部工单）
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

-- 工单沟通记录
CREATE TABLE IF NOT EXISTS bs_after_sales_ticket_messages (
    id SERIAL PRIMARY KEY,
    ticket_id TEXT NOT NULL REFERENCES bs_after_sales_tickets(ticket_id),
    sender_type TEXT NOT NULL,                -- user/agent/staff（用户/智能体/人工客服）
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 5.2 退换货记录表

```sql
-- 退换货记录（内部记录，同步到外部系统）
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

### 5.3 表初始化

遵循项目规范，在 skill 加载时通过 `init_tables()` 自动创建。

---

## 6. 文件变更清单

### 6.1 新增文件

| 文件 | 说明 |
|------|------|
| `subagents/after-sales/SUBAGENT.md` | 子智能体定义 |
| `src/skills/after-sales-core-1.0.0/SKILL.md` | 售后核心技能定义 |
| `src/skills/after-sales-core-1.0.0/scripts/adapter_engine.py` | 适配器引擎 |
| `src/skills/after-sales-core-1.0.0/scripts/after_sales_tool.py` | 售后工具脚本（CLI 入口） |
| `src/tools/after_sales/after_sales_query_tool.py` | 售后查询工具 |
| `src/tools/after_sales/after_sales_action_tool.py` | 售后操作工具 |
| `src/db/tenant_api_credential.py` | 租户 API 凭据管理 |
| `src/db/after_sales_db.py` | 售后数据表操作 |
| `src/api/tenant_api_credential.py` | 租户 API 凭据管理 API |

### 6.2 修改文件

| 文件 | 改动 | 说明 |
|------|------|------|
| `src/core/agent.py` | `_register_builtin_tools()` | 注册售后工具 |
| `src/main.py` | 路由注册 | 注册 API 凭据管理路由 |
| `deploy/init-postgres.sql` | 表结构 | 增加 `tenant_api_credentials` 表 |
| `deploy/db_update.sql` | 增量变更 | 记录新增表和字段 |
| `src/saas/models/enums.py` | 枚举 | 增加售后相关枚举值 |

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
│  ⚙️ 高级配置（适配器）                                 │
│                                                     │
│  ┌───────────────────────────────────────────────┐  │
│  │  适配器类型: [声明式（JSON配置）▼]               │  │
│  │                                               │  │
│  │  API 端点配置（JSON）:                          │  │
│  │  ┌─────────────────────────────────────────┐  │  │
│  │  │ {                                       │  │  │
│  │  │   "apis": {                             │  │  │
│  │  │     "query_order": {                    │  │  │
│  │  │       "method": "GET",                  │  │  │
│  │  │       "path": "/orders/{order_id}"      │  │  │
│  │  │     }                                   │  │  │
│  │  │   }                                     │  │  │
│  │  │ }                                       │  │  │
│  │  └─────────────────────────────────────────┘  │  │
│  │                                               │  │
│  │  [保存配置] [重置为默认]                         │  │
│  └───────────────────────────────────────────────┘  │
│                                                     │
│  📊 连接状态                                         │
│  ✅ 上次测试连接成功 (2026-05-06 10:30)               │
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
| API Key 加密存储 | 使用 `encryption_manager` Fernet 加密，数据库中不存明文 |
| 凭据脱敏返回 | 列表接口返回 `****` 替代真实密钥 |
| 租户隔离 | 凭据查询必须带 `tenant_id`，防止跨租户访问 |
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

### Phase 1: 基础框架（预计 3-5 天）

1. 创建售后服务子智能体定义（SUBAGENT.md）
2. 实现 `SubagentConfig.inject_user_profile` 字段和用户信息注入
3. 创建 `after_sales_query` 和 `after_sales_action` 工具（基础框架，不含外部 API 调用）
4. 创建售后数据表（`bs_after_sales_tickets`、`bs_after_sales_returns`）
5. 创建 `tenant_api_credentials` 表和凭据管理 CRUD
6. 手动测试：在无外部系统时，通过对话创建内部工单

### Phase 2: 适配器引擎（预计 3-5 天）

1. 实现 `ExternalAdapterEngine` 声明式适配器
2. 实现参数解析和 JSONPath 响应映射
3. 工具与适配器引擎集成
4. 创建示例适配器配置（用于测试）
5. 手动测试：配置测试 API，验证查询和操作流程

### Phase 3: 前端 & 管理（预计 2-3 天）

1. 租户管理端 API 凭据配置页面
2. 售后业务数据页面（工单列表、退换货记录）
3. 连接测试功能
4. 适配器配置编辑器（JSON 编辑器 + 校验）

### Phase 4: 生产化（预计 2-3 天）

1. 脚本式适配器支持
2. 错误处理和降级策略完善
3. 操作审计日志
4. 压力测试和性能优化
5. 文档和部署脚本

---

## 10. 待讨论事项

1. **用户信息注入粒度**：是否所有子智能体都需要 `inject_user_profile`？还是只对特定子智能体生效？建议：按需配置，默认不注入。

2. **适配器配置管理方式**：
   - 方案 A：租户管理端 UI 编辑 JSON 配置（灵活但门槛高）
   - 方案 B：预置几种常见 ERP/电商系统的适配器模板，租户选择模板后填入地址和密钥（易用但覆盖有限）
   - 建议：先实现方案 A（JSON 编辑），后续迭代增加方案 B（模板选择）

3. **外部 API 超时与重试**：企业内网 API 可能较慢，需要合理的超时配置和重试策略。建议默认超时 10 秒，不重试（避免重复操作）。

4. **多租户适配器隔离**：不同租户的 `adapter.json` 和 `adapter.py` 互相隔离，需要沙箱执行脚本来防止安全问题。

5. **人工转接机制**：当智能体无法解决时，如何转接到人工客服？是否需要对接工单系统的客服分配功能？

6. **售后知识库**：是否需要为每个租户维护商品使用指南的知识库？还是统一使用平台的商品信息？

7. **消息渠道的用户身份绑定**：当前渠道（企业微信/钉钉/飞书）回调不携带应用 `user_id`，需要解决渠道用户与应用用户的映射关系。

---

## 附录 A: 与现有子智能体的复用关系

| 组件 | 复用方式 |
|------|----------|
| 子智能体加载/注册/执行 | 完全复用，按标准格式定义 SUBAGENT.md |
| Skill 系统 | 完全复用，售后核心逻辑封装为 skill |
| Tool 系统 | 扩展，新增售后专用工具 |
| 凭据加密 | 复用 `encryption_manager` |
| SaaS 订阅控制 | 复用，售后服务作为新的 subagent_type |
| 数据表规范 | 复用，`bs_` 前缀 + `tenant_id` 隔离 |

## 附录 B: 术语表

| 术语 | 说明 |
|------|------|
| 适配器 (Adapter) | 连接外部企业系统的可配置中间件 |
| 声明式适配器 | 通过 JSON 配置描述 API 接口，无需编码 |
| 脚本式适配器 | 通过 Python 脚本实现复杂接口逻辑 |
| 外部系统 | 企业内部的订单管理、ERP、售后管理系统 |
| 工单 (Ticket) | 用户发起的售后请求记录 |
