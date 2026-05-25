# 客户跟进智能体 — 深度设计方案

> 版本: v1.0 | 创建: 2026-05-25 | 状态: 待审核

## 一、背景与目标

### 1.1 业务背景

企业需要一套 AI 驱动的销售线索管理闭环，从线索导入到转化分析全流程覆盖。核心痛点：
- 线索散落在 Excel、外部 CRM、微信群等各处，缺乏统一管理
- 线索分配靠人工，负载不均，高价值线索可能被忽略
- 跟进缺乏提醒机制，容易遗漏时效性强的客户
- 跟进质量难以量化评估，新人培训缺少参考标准
- 转化数据缺乏分析，无法识别漏斗瓶颈

### 1.2 设计目标

构建一个**自包含的客户跟进智能体**，能够：
1. 通过 Excel 导入/手动录入/外部 API 对接多种方式获取线索
2. 基于规则与负载智能分配线索给销售人员
3. 自动提醒跟进，避免遗漏
4. **AI 电话外呼**：可选自动外呼线索手机/固话，进行初步接触或跟进
5. 跟进记录分析，LLM 评估跟进质量
6. 转化漏斗分析，识别改进点
7. 即使不接入外部系统也能通过 Excel 导入完成功能闭环

### 1.3 与现有 CRM 设计的区别

项目中已有一份 CRM 智能体设计文档（`docs/subagent/crm/crm_subagent_design.md`），聚焦于**客户画像分析**（标签、分层、企业资质）。本智能体聚焦于**销售跟进流程管理**（线索导入→分配→跟进→转化），两者是互补关系而非重复。

---

## 二、两层架构设计

### 第一层：全局可复用基础设施

#### 2.1 业务通知系统扩展（基于现有 `src/services/notification_service.py`）

**现状**：项目已有 `NotificationService`（`src/services/notification_service.py`），支持邮件和 webhook 发送，是无状态的"发后即忘"模式。已在投诉智能体中使用（`complaint_tool.py` 的升级通知）。

**不足**：
- 无持久化：发送后无记录，无法查看历史通知
- 无站内通知：只有邮件/webhook，无前端通知 UI
- 无定时提醒：无法在指定时间触发通知

**扩展方案**：

```
src/services/
  notification_service.py   # 现有，保持不变
  notification_store.py     # 新增：通知持久化（notifications 表 CRUD）
  notification_api.py       # 新增：FastAPI 路由（前端轮询通知）
```

**新增数据库表**：`notifications`（系统级表，不带 bs_ 前缀）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | SERIAL PK | |
| notification_id | TEXT UNIQUE | `notif_<uuid12>` |
| tenant_id | TEXT | 租户隔离 |
| user_id | TEXT NOT NULL | 接收人 |
| type | TEXT | reminder/alert/assignment/system |
| category | TEXT | followup/lead/escalation |
| priority | TEXT | low/normal/high/urgent |
| title | TEXT NOT NULL | 通知标题 |
| content | TEXT | 通知内容 |
| link | TEXT | 深度链接到业务页面 |
| source_type | TEXT | 来源智能体 |
| source_id | TEXT | 关联业务实体 ID |
| status | TEXT | unread/read/dismissed |
| created_at | TIMESTAMP DEFAULT NOW() | |

**扩展接口**：
```python
# 现有（保持不变）
notification_service.send(message: NotificationMessage) -> bool

# 新增：通知持久化
NotificationStore.create(user_id, title, content, type, category, priority, link, source_type, source_id)
NotificationStore.mark_read(notification_id)
NotificationStore.get_unread(user_id, limit)
NotificationStore.get_stats(user_id)
```

**集成方式**：发送通知时同时做两件事：
1. 调用现有 `notification_service.send()` 发送邮件/webhook
2. 调用 `NotificationStore.create()` 持久化到数据库（供前端展示）

**前端**：`NotificationBadge.vue` 组件，显示未读数 + 下拉通知列表

#### 2.2 AI 电话外呼工具（新增 `src/tools/phone/ai_call_tool.py`）

**问题**：用户已实现电话外呼功能（自动 AI 外呼线索手机/固话），需要将其封装为 Agent 工具供智能体调用。

**方案**：新建 `ai_call` 工具，对外呼 API 做 Mock 封装。工具接口先行设计，后端 API 由用户后续实现。

**工具定义**：

```python
# src/tools/phone/ai_call_tool.py
class AICallTool(BaseTool):
    name = "ai_call"
    description = "AI 电话外呼工具，自动拨打线索电话进行初步接触或跟进"
    display_name = "AI外呼"
    category = "phone"
```

**工具参数（InputModel）**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| phone | string | 是 | 被叫号码（手机或固话） |
| lead_id | string | 否 | 关联的线索 ID（用于记录跟进） |
| call_purpose | string | 是 | 外呼目的: first_contact/followup/appointment_reminder/satisfaction_survey |
| script_hint | string | 否 | 给 AI 的话术提示（如"确认客户是否有采购需求"） |
| max_duration | integer | 否 | 最大通话时长（秒），默认 180 |
| callback_url | string | 否 | 通话结果回调 URL |

**工具返回**：

```json
{
    "success": true,
    "call_id": "call_<uuid12>",
    "status": "initiated",        // initiated/ringing/answered/completed/failed/no_answer
    "message": "外呼已发起，等待接听"
}
```

**回调数据（通话完成后）**：

```json
{
    "call_id": "call_<uuid12>",
    "lead_id": "lead_xxxx",
    "status": "completed",
    "duration_seconds": 120,
    "transcript": "通话转写文本...",
    "ai_summary": "AI 总结：客户对产品感兴趣，希望下周面谈",
    "sentiment": "positive",      // positive/neutral/negative
    "next_action_suggested": "安排面谈"
}
```

**Mock 实现**：
- Phase 1 使用 Mock 实现：调用后返回固定的 `{"success": true, "call_id": "call_mock_xxx", "status": "initiated", "message": "Mock: 外呼已模拟发起"}`
- 用户后续实现真实的外呼 API 对接（替换 Mock 为实际 HTTP 调用）
- Mock 期间，智能体的对话流程和 skill 调用链路保持完整，仅实际拨打部分返回模拟结果

**与跟进系统的集成流程**：

```
用户说"帮我外呼这个线索" → Agent 调用 ai_call 工具
    → 外呼 API 拨打线索电话
    → 通话完成后回调
    → 回调触发：自动创建跟进记录（followup_type=ai_call）
    → 回调触发：AI 分析通话内容，更新线索阶段和评分
    → 回调触发：如客户表达意向，创建通知提醒销售人员跟进
```

**多租户隔离**：外呼 API 的密钥和配置通过子智能体环境变量传递（`AI_CALL_API_URL`、`AI_CALL_API_KEY`），各租户可配置不同的外呼服务。

#### 2.3 数据导入辅助工具（新增 `src/utils/data_import.py`）

**问题**：多个 skill 可能需要 Excel 导入逻辑，当前 `trade-customer` 把导入逻辑写在脚本内部不可复用。

**方案**：抽取可复用辅助函数，各 skill 脚本可选使用。

```python
class ExcelImportHelper:
    read_excel(file_path, mapping?) -> List[dict]       # 读取 Excel 并映射列名
    validate_records(records, required_fields) -> (valid, invalid)  # 校验必填字段
    batch_insert(table_name, records, batch_size=100) -> stats      # 批量插入
```

#### 2.4 不需要的基础设施

| 考虑项 | 决定 | 理由 |
|--------|------|------|
| 通用联系人/公司实体 | ❌ 不做 | 各智能体对"客户"定义不同，过早抽象会不当耦合 |
| 管道/漏斗管理原语 | ❌ 不做 | 业务领域概念，不属于基础设施 |
| 数据导出基础设施 | ❌ 不做 | `excel_process` 工具已覆盖 |

---

### 第二层：智能体本身设计

#### 子智能体目录名：`customer-followup`

---

## 三、数据库表设计（5 张表）

所有表名前缀 `bs_customer_followup_`，均含 `tenant_id TEXT` 用于租户隔离。

### 表1：`bs_customer_followup_leads` — 线索主表

```sql
CREATE TABLE IF NOT EXISTS bs_customer_followup_leads (
    id SERIAL PRIMARY KEY,
    lead_id TEXT UNIQUE NOT NULL,
    tenant_id TEXT,
    user_id TEXT,

    -- 线索基本信息
    company_name TEXT,
    contact_name TEXT,
    phone TEXT,
    email TEXT,
    source TEXT,                              -- import/api/manual/referral/website/exhibition
    industry TEXT,
    region TEXT,
    address TEXT,

    -- 线索详情
    product_interest TEXT,
    budget_range TEXT,
    estimated_deal_amount NUMERIC(12,2),
    description TEXT,

    -- 管道状态
    stage TEXT DEFAULT 'new',                 -- new/contacting/qualified/proposal/negotiation/won/lost
    stage_entered_at TIMESTAMP,
    score INTEGER DEFAULT 0,

    -- 分配
    assigned_to TEXT,
    assigned_at TIMESTAMP,
    assignment_rule TEXT,

    -- 状态追踪
    status TEXT DEFAULT 'active',             -- active/converted/lost/recycled
    lost_reason TEXT,
    next_followup_at TIMESTAMP,
    last_followup_at TIMESTAMP,
    followup_count INTEGER DEFAULT 0,

    -- 来源追踪
    import_batch TEXT,
    external_id TEXT,

    -- 标签
    tags TEXT[] DEFAULT '{}',

    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cf_leads_tenant ON bs_customer_followup_leads(tenant_id);
CREATE INDEX IF NOT EXISTS idx_cf_leads_assigned ON bs_customer_followup_leads(tenant_id, assigned_to);
CREATE INDEX IF NOT EXISTS idx_cf_leads_stage ON bs_customer_followup_leads(tenant_id, stage);
CREATE INDEX IF NOT EXISTS idx_cf_leads_status ON bs_customer_followup_leads(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_cf_leads_next_followup ON bs_customer_followup_leads(tenant_id, next_followup_at);
CREATE INDEX IF NOT EXISTS idx_cf_leads_external ON bs_customer_followup_leads(external_id);
CREATE INDEX IF NOT EXISTS idx_cf_leads_tags ON bs_customer_followup_leads USING GIN(tags);
```

### 表2：`bs_customer_followup_records` — 跟进记录表

```sql
CREATE TABLE IF NOT EXISTS bs_customer_followup_records (
    id SERIAL PRIMARY KEY,
    record_id TEXT UNIQUE NOT NULL,
    tenant_id TEXT,
    lead_id TEXT NOT NULL,
    user_id TEXT NOT NULL,

    followup_type TEXT NOT NULL,              -- phone/email/visit/wechat/ai_call/other
    content TEXT NOT NULL,
    followup_at TIMESTAMP DEFAULT NOW(),
    duration_minutes INTEGER,

    outcome TEXT,                             -- positive/neutral/negative/no_response
    next_action TEXT,
    next_followup_at TIMESTAMP,
    quality_score INTEGER,                    -- LLM 评估 1-10

    -- AI 外呼相关字段
    call_id TEXT,                             -- 外呼通话 ID（当 followup_type=ai_call 时）
    call_transcript TEXT,                     -- 通话转写文本
    call_sentiment TEXT,                      -- 通话情感: positive/neutral/negative
    call_summary TEXT,                        -- AI 总结

    attachments JSONB DEFAULT '[]',

    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cf_records_tenant ON bs_customer_followup_records(tenant_id);
CREATE INDEX IF NOT EXISTS idx_cf_records_lead ON bs_customer_followup_records(lead_id);
CREATE INDEX IF NOT EXISTS idx_cf_records_user ON bs_customer_followup_records(tenant_id, user_id);
CREATE INDEX IF NOT EXISTS idx_cf_records_date ON bs_customer_followup_records(tenant_id, followup_at);
```

### 表3：`bs_customer_followup_sales_reps` — 销售人员表

```sql
CREATE TABLE IF NOT EXISTS bs_customer_followup_sales_reps (
    id SERIAL PRIMARY KEY,
    rep_id TEXT UNIQUE NOT NULL,
    tenant_id TEXT,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    department TEXT,
    role TEXT DEFAULT 'sales',                -- sales/manager/director
    active_lead_count INTEGER DEFAULT 0,
    max_leads INTEGER DEFAULT 50,
    is_active BOOLEAN DEFAULT TRUE,
    skills TEXT[] DEFAULT '{}',
    region TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cf_reps_tenant ON bs_customer_followup_sales_reps(tenant_id);
CREATE INDEX IF NOT EXISTS idx_cf_reps_user ON bs_customer_followup_sales_reps(user_id);
CREATE INDEX IF NOT EXISTS idx_cf_reps_active ON bs_customer_followup_sales_reps(tenant_id, is_active);
```

### 表4：`bs_customer_followup_assign_rules` — 分配规则表

```sql
CREATE TABLE IF NOT EXISTS bs_customer_followup_assign_rules (
    id SERIAL PRIMARY KEY,
    rule_id TEXT UNIQUE NOT NULL,
    tenant_id TEXT,
    name TEXT NOT NULL,
    rule_type TEXT NOT NULL,                  -- round_robin/load_balance/region_based/skill_based/manual
    priority INTEGER DEFAULT 0,
    is_active BOOLEAN DEFAULT TRUE,
    conditions JSONB DEFAULT '{}',
    target_rep_ids TEXT[] DEFAULT '{}',
    auto_assign BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cf_rules_tenant ON bs_customer_followup_assign_rules(tenant_id);
```

### 表5：`bs_customer_followup_conversion_funnel` — 转化漏斗事件表（仅追加）

```sql
CREATE TABLE IF NOT EXISTS bs_customer_followup_conversion_funnel (
    id SERIAL PRIMARY KEY,
    funnel_id TEXT UNIQUE NOT NULL,
    tenant_id TEXT,
    lead_id TEXT NOT NULL,
    from_stage TEXT,
    to_stage TEXT NOT NULL,
    changed_at TIMESTAMP DEFAULT NOW(),
    changed_by TEXT,
    days_in_previous_stage INTEGER,
    note TEXT
);

CREATE INDEX IF NOT EXISTS idx_cf_funnel_tenant ON bs_customer_followup_conversion_funnel(tenant_id);
CREATE INDEX IF NOT EXISTS idx_cf_funnel_lead ON bs_customer_followup_conversion_funnel(lead_id);
CREATE INDEX IF NOT EXISTS idx_cf_funnel_stage ON bs_customer_followup_conversion_funnel(tenant_id, to_stage);
CREATE INDEX IF NOT EXISTS idx_cf_funnel_date ON bs_customer_followup_conversion_funnel(tenant_id, changed_at);
```

**漏斗表设计要点**：仅追加，不更新。每次阶段变更插入一条记录，可重建完整漏斗历史并计算时间维度的转化指标。

---

## 四、Skill 设计（3 个 skill）

### Skill 1：`lead-management` v1.0.0

目录：`src/skills/lead-management-1.0.0/`
脚本：`scripts/lead_manager.py`
参考实现：`src/skills/trade-customer-1.0.0/scripts/customer_manager.py`

| 命令 | 参数 | 说明 |
|------|------|------|
| `init_tables` | — | 创建所有 5 张表 |
| `import-leads` | file_path, mapping?, batch? | Excel 导入线索，支持列映射 |
| `add-lead` | lead JSON | 手动添加单条线索 |
| `list-leads` | user_id, stage?, status?, assigned_to?, keyword?, page?, page_size? | 列表查询 |
| `get-lead` | lead_id | 线索详情（含跟进历史） |
| `update-lead` | lead_id, fields JSON | 更新线索字段 |
| `update-stage` | lead_id, stage, note? | 阶段变更（插入漏斗记录） |
| `delete-lead` | lead_id | 软删除（status→lost） |
| `assign-lead` | lead_id, assigned_to?, rule? | 分配线索 |
| `batch-assign` | lead_ids?, rule?, unassigned_only? | 批量分配 |
| `stats` | user_id, date_range? | 线索统计 |
| `export-leads` | stage?, status?, format? | 导出 Excel |

### Skill 2：`followup-tracking` v1.0.0

目录：`src/skills/followup-tracking-1.0.0/`
脚本：`scripts/followup_manager.py`

| 命令 | 参数 | 说明 |
|------|------|------|
| `init_tables` | — | 确保跟进相关表存在 |
| `add-record` | lead_id, type, content, outcome?, next_action?, next_followup_at? | 创建跟进记录 |
| `list-records` | lead_id?, user_id?, date_from?, date_to?, limit? | 跟进记录列表 |
| `get-record` | record_id | 跟进详情 |
| `evaluate-quality` | record_id | LLM 评估跟进质量 |
| `batch-evaluate` | user_id?, date_range? | 批量质量评估 |
| `get-reminders` | user_id, due_before? | 待跟进提醒 |
| `get-overdue` | tenant_id | 逾期跟进（经理视图） |
| `reminder-stats` | user_id | 提醒统计 |
| `record-ai-call` | lead_id, call_id, transcript, sentiment, summary, duration? | 记录 AI 外呼结果（自动创建跟进记录） |
| `batch-ai-call-results` | results JSON | 批量记录外呼结果 |

### Skill 3：`conversion-analysis` v1.0.0

目录：`src/skills/conversion-analysis-1.0.0/`
脚本：`scripts/conversion_analyzer.py`

| 命令 | 参数 | 说明 |
|------|------|------|
| `funnel-stats` | date_from?, date_to? | 漏斗统计（各阶段数、转化率） |
| `stage-duration` | stage?, date_range? | 各阶段平均停留时间 |
| `win-loss-analysis` | date_range? | 赢单/输单分析和原因 |
| `rep-performance` | user_id?, date_range? | 销售绩效指标 |
| `source-analysis` | date_range? | 按来源分析转化率 |
| `generate-report` | report_type, date_range? | 生成综合分析报告 |

---

## 五、SUBAGENT.md 配置

文件：`subagents/customer-followup/SUBAGENT.md`

```yaml
---
name: 客户跟进智能体
description: 智能线索分配与跟进管理专家，支持线索导入、智能分配、自动提醒、跟进分析和转化漏斗
version: 1.0.0
author: system
capabilities:
  - lead_import
  - lead_assignment
  - followup_reminder
  - followup_analysis
  - conversion_analysis
  - ai_call_outbound
triggers:
  file_patterns:
    - "*.xlsx"
    - "*.csv"
  keywords:
    - 线索
    - 客户跟进
    - 销售线索
    - 跟进提醒
    - 转化率
    - 销售漏斗
    - 跟进记录
    - 分配线索
    - 导入线索
    - 外呼
    - 电话跟进
    - AI外呼
tools:
  inherit: true
  additional:
    - content_generate
    - ai_call
skills:
  allowed:
    - lead-management
    - followup-tracking
    - conversion-analysis
context:
  max_input_tokens: 10000
  max_output_tokens: 4000
business_pages:
  - id: leads
    title: 线索管理
    icon: 🎯
    route: /customer-followup/leads
  - id: followup-records
    title: 跟进记录
    icon: 📝
    route: /customer-followup/followup-records
  - id: funnel
    title: 转化漏斗
    icon: 📊
    route: /customer-followup/funnel
  - id: sales-reps
    title: 销售人员
    icon: 👥
    route: /customer-followup/sales-reps
---
```

Markdown body 内容将包括：角色定义、核心工作流程（导入→分配→跟进→分析）、每个 skill 的精确 `skill_execute` 命令格式、阶段枚举定义、禁止事项。

---

## 六、API 端点设计

文件：`src/api/customer_followup.py`
前缀：`/api/followup`
参考实现：`src/api/customer.py`

### 线索管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/followup/leads` | 线索列表（分页、按 stage/status/assigned_to/keyword 筛选） |
| GET | `/api/followup/leads/{lead_id}` | 线索详情（含跟进历史） |
| POST | `/api/followup/leads` | 手动创建线索 |
| PUT | `/api/followup/leads/{lead_id}` | 更新线索字段 |
| POST | `/api/followup/leads/{lead_id}/stage` | 阶段变更 |
| DELETE | `/api/followup/leads/{lead_id}` | 软删除 |
| POST | `/api/followup/leads/import` | Excel 导入 |
| GET | `/api/followup/leads/export` | Excel 导出 |
| POST | `/api/followup/leads/{lead_id}/assign` | 分配线索给销售人员 |

### 跟进记录

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/followup/records` | 跟进记录列表 |
| POST | `/api/followup/records` | 创建跟进记录 |
| GET | `/api/followup/records/{record_id}` | 跟进详情 |
| POST | `/api/followup/records/{record_id}/evaluate` | 触发质量评估 |

### 销售与分配

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/followup/reps` | 销售人员列表 |
| POST | `/api/followup/reps` | 添加销售人员 |
| PUT | `/api/followup/reps/{rep_id}` | 更新销售人员 |
| GET | `/api/followup/reps/{rep_id}/performance` | 销售绩效 |
| GET | `/api/followup/assign-rules` | 分配规则列表 |
| POST | `/api/followup/assign-rules` | 创建分配规则 |
| PUT | `/api/followup/assign-rules/{rule_id}` | 更新分配规则 |

### 分析与漏斗

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/followup/funnel` | 漏斗统计 |
| GET | `/api/followup/conversion` | 转化率 |
| GET | `/api/followup/rep-performance` | 销售绩效仪表板 |
| GET | `/api/followup/source-analysis` | 来源分析 |
| GET | `/api/followup/overdue` | 逾期跟进（经理视图） |
| GET | `/api/followup/dashboard` | 仪表板概览 |

### AI 外呼

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/followup/leads/{lead_id}/call` | 发起 AI 外呼（调用 ai_call 工具） |
| POST | `/api/followup/leads/batch-call` | 批量外呼（选择多个线索批量拨打） |
| POST | `/api/followup/call-callback` | 外呼结果回调（外呼系统调用，记录结果） |
| GET | `/api/followup/call-records` | 外呼记录列表（followup_type=ai_call 的跟进记录） |

---

## 七、前端页面设计

| 页面 | 路由 | 组件 | 核心功能 |
|------|------|------|----------|
| 线索管理 | `/customer-followup/leads` | `LeadManager.vue` | 统计卡片 + 筛选 + 数据表 + 详情侧面板 + 导入/导出 |
| 跟进记录 | `/customer-followup/followup-records` | `FollowupRecords.vue` | 按线索/人员/日期筛选 + 质量评分显示 |
| 转化漏斗 | `/customer-followup/funnel` | `ConversionFunnel.vue` | 漏斗可视化 + 赢单/输单分析 + 来源分析 + 绩效排行 |
| 销售人员 | `/customer-followup/sales-reps` | `SalesRepManager.vue` | 销售卡片 + 分配规则管理 + 规则测试 |

### 线索管理页面布局

```
┌───────────────────────────────────────────────────────┐
│ 统计卡片：[新线索 12] [联系中 45] [已成交 8] [总活跃 65]│
├───────────────────────────────────────────────────────┤
│ 筛选栏：[阶段▼] [状态▼] [分配给▼] [🔍搜索] [导入][导出]│
├───────────────────────────────────────────────────────┤
│ 公司名 │联系人│阶段  │分配给│下次跟进│评分│创建时间│操作│
│ XX公司 │张三  │联系中│李四  │明天    │ 78 │5-25   │...│
│ YY科技 │王五  │新线索│—     │—      │ 45 │5-24   │...│
├───────────────────────────────────────────────────────┤
│ 分页：< 1 2 3 4 5 >                     共 65 条     │
└───────────────────────────────────────────────────────┘
```

### 转化漏斗页面布局

```
┌───────────────────────────────────────────────────────┐
│ 日期范围：[2026-05-01] ~ [2026-05-25]                 │
├───────────────────────────────────────────────────────┤
│                                                       │
│ 新线索 ████████████████████████████  120 (100%)        │
│ 联系中 ██████████████████████        90  (75%)         │
│ 已qualified ████████████████         60  (50%)         │
│ 方案中 ██████████                    40  (33%)         │
│ 谈判中 ████████                      30  (25%)         │
│ 成交   ████                          15  (12.5%)       │
│                                                       │
├───────────────────────┬───────────────────────────────┤
│ 赢单/输单分析          │ 来源转化率                    │
│ 成交 15 / 流失 25     │ 展会 20% API导入 15% ...      │
├───────────────────────┴───────────────────────────────┤
│ 销售绩效排行                                          │
│ 1. 张三 - 成交5笔 / 转化率 25%                       │
│ 2. 李四 - 成交4笔 / 转化率 20%                       │
└───────────────────────────────────────────────────────┘
```

### 路由注册

在 `frontend/src/main.ts` 中，参照 `trade-specialist` 模式添加路由组：

```typescript
// 非租户模式
{
  path: '/customer-followup',
  component: () => import('./components/BaseBusinessLayout.vue'),
  children: [
    { path: 'leads', component: () => import('./components/followup/LeadManager.vue') },
    { path: 'followup-records', component: () => import('./components/followup/FollowupRecords.vue') },
    { path: 'funnel', component: () => import('./components/followup/ConversionFunnel.vue') },
    { path: 'sales-reps', component: () => import('./components/followup/SalesRepManager.vue') },
  ]
}
// 租户模式同结构，前缀 /t/:tenant_id/customer-followup
```

前端 API 模块：`frontend/src/api/followup.ts`

---

## 八、提醒系统设计

### 两层策略

**Phase 1 — 基于现有调度器（零基础设施改动）**：
- 通过 `create_scheduled_task` 工具创建定时 LLM 任务（每小时）
- 任务 prompt 让 Agent 调用 `followup-tracking get-reminders`
- 发现逾期跟进后通过对话消息提醒用户
- 优点：利用现有基础设施，无需开发新组件

**Phase 2 — 通知持久化（基于现有 NotificationService 扩展）**：
- 后台定时扫描 `next_followup_at <= NOW()` 的线索
- 调用现有 `notification_service.send()` 发送邮件/webhook
- 调用新增 `NotificationStore.create()` 持久化通知到数据库
- 前端 `NotificationBadge.vue` 实时显示未读通知数
- 优点：复用现有邮件发送能力，新增站内通知展示

---

## 九、外部系统集成

### 集成方式

智能体是自包含的，但可选连接外部系统：

1. **线索获取**：外部 CRM 通过 `http_api` 工具推送线索，或 Agent 主动拉取。数据始终先流入 `bs_customer_followup_leads`
2. **状态同步**：可选回调外部系统报告阶段变更
3. **AI 外呼**：通过 `ai_call` 工具调用外呼 API，用户已实现外呼功能，当前封装为 Mock 工具
4. **无硬依赖**：所有核心功能无需外部系统。外部连接通过对话配置（Agent 引导用户设置 API 端点）

### AI 外呼集成详情

**外呼触发方式**：
1. **对话触发**：用户在对话中说"帮我打电话给这个线索" → Agent 调用 `ai_call` 工具
2. **批量外呼**：用户选择多个线索 → 点击"批量外呼" → API 批量调用 `ai_call`
3. **自动外呼**（高级）：新线索分配后自动触发首次外呼（通过分配规则配置 `auto_call: true`）

**外呼结果处理流程**：
```
外呼 API 完成通话
    → 回调 POST /api/followup/call-callback
    → 写入跟进记录（followup_type=ai_call, call_transcript, call_sentiment, call_summary）
    → 更新线索的 last_followup_at、followup_count
    → AI 分析通话内容，自动建议：
      - 如客户有意向 → 建议升级阶段（contacting→qualified）
      - 如客户拒绝 → 标记 outcome=negative，建议回收或降级
      - 如无人接听 → 安排重试，设置 next_followup_at
    → 通知对应的销售人员查看外呼结果
```

**Mock 模式说明**：
- 工具调用返回模拟的 `{"success": true, "call_id": "call_mock_xxx", "status": "initiated"}`
- 不实际拨打电话，但完整的调用链路（工具调用 → 参数校验 → 返回格式）保持一致
- 用户后续替换 `ai_call_tool.py` 中的 Mock 实现为真实 HTTP 调用即可，无需修改其他代码

### 使用的现有工具

| 工具 | 用途 |
|------|------|
| `excel_process` | 读取上传的 Excel 文件用于线索导入 |
| `email_send` | 发送跟进提醒邮件 |
| `content_generate` | 跟进质量评估、跟进建议生成、分析报告 |
| `http_api` | 可选连接外部 CRM/线索系统 |
| `create_scheduled_task` | 创建定时提醒任务 |
| `web_search` | 线索背景调研（丰富线索数据） |
| `ai_call` | AI 电话外呼，自动拨打线索电话进行初步接触或跟进 |

---

## 十、分阶段实施计划

### Phase 1：核心线索管理 — 已完成 (2026-05-25)

| 任务 | 关键文件 | 状态 |
|------|----------|------|
| 1.1 创建 lead-management skill + init_tables | `src/skills/lead-management-1.0.0/*` (新) | ✅ 已完成 |
| 1.2 实现 lead_manager.py（add/list/get/update/delete/stage/stats） | 其内 `scripts/lead_manager.py` | ✅ 已完成 |
| 1.3 实现 Excel 导入（import-leads） | 同上 | ✅ 已完成 |
| 1.4 创建 API 路由 | `src/api/customer_followup.py` (新) | ✅ 已完成 |
| 1.5 注册路由到 main.py | `src/main.py` (改) | ✅ 已完成 |
| 1.6 创建 SUBAGENT.md | `subagents/customer-followup/SUBAGENT.md` (新) | ✅ 已完成 |
| 1.7 前端 LeadManager.vue | `frontend/src/components/followup/LeadManager.vue` (新) | ✅ 已完成 |
| 1.8 前端路由 + API 模块 | `frontend/src/main.ts` (改), `frontend/src/api/followup.ts` (新) | ✅ 已完成 |
| 1.9 DB 变更脚本 | `deploy/db_update.sql` (改) | ✅ 已完成 |

**交付标准**：线索 CRUD + Excel 导入 + 阶段流转 + 多租户隔离 + 前端线索页

### Phase 2：分配、跟进与 AI 外呼 — 已完成 (2026-05-25)

| 任务 | 关键文件 | 状态 |
|------|----------|------|
| 2.1 实现销售人员表 + CRUD | 扩展 `lead_manager.py` | ✅ 已完成 |
| 2.2 实现分配规则表 + 负载均衡分配 | 扩展 `lead_manager.py` | ✅ 已完成 |
| 2.3 创建 followup-tracking skill | `src/skills/followup-tracking-1.0.0/*` (新) | ✅ 已完成 |
| 2.4 实现 followup_manager.py | 其内 `scripts/followup_manager.py` | ✅ 已完成 |
| 2.5 实现质量评估（调用 content_generate） | 同上 | ✅ 已完成 |
| 2.6 创建 AI 外呼工具（Mock 实现） | `src/tools/phone/ai_call_tool.py` (新) | ✅ 已完成 |
| 2.7 注册 ai_call 工具到 Agent | `src/core/agent.py` (改) | ✅ 已完成 |
| 2.8 扩展 API（跟进记录 + 销售 + 分配 + 外呼） | 扩展 `src/api/customer_followup.py` | ✅ 已完成 |
| 2.9 前端 FollowupRecords.vue + SalesRepManager.vue | 新 Vue 组件 | ✅ 已完成 |
| 2.10 单元测试（38 项全部通过） | `tests/unit/test_lead_manager_phase2.py` 等 | ✅ 已完成 |

**交付标准**：线索分配 + 负载均衡 + 跟进记录 + 质量评估 + AI 外呼(Mock) + 定时提醒 + 前端

### Phase 3：分析与报告（2-3 天）

| 任务 | 关键文件 | 依赖 |
|------|----------|------|
| 3.1 创建 conversion-analysis skill | `src/skills/conversion-analysis-1.0.0/*` (新) | P1 |
| 3.2 实现 conversion_analyzer.py | 其内 `scripts/conversion_analyzer.py` | 3.1 |
| 3.3 扩展 API（漏斗 + 转化 + 绩效） | 扩展 `src/api/customer_followup.py` | 3.2 |
| 3.4 前端 ConversionFunnel.vue | 新 Vue 组件 | 3.3 |

**交付标准**：漏斗可视化 + 转化率分析 + 销售绩效 + 来源分析

### Phase 4：通知持久化与导入辅助（2-3 天）

| 任务 | 关键文件 | 依赖 |
|------|----------|------|
| 4.1 创建 notifications 表 | `deploy/db_update.sql` (改) | — |
| 4.2 实现 NotificationStore（通知持久化层） | `src/services/notification_store.py` (新) | 4.1 |
| 4.3 创建通知 API | `src/services/notification_api.py` (新) | 4.2 |
| 4.4 注册通知 API 到 main.py | `src/main.py` (改) | 4.3 |
| 4.5 集成：followup-tracking 创建通知（调用现有 notification_service + 新 NotificationStore） | 改 `followup_manager.py` | 4.2 |
| 4.6 前端 NotificationBadge.vue | 新 Vue 组件 | 4.3 |
| 4.7 数据导入辅助工具（可选） | `src/utils/data_import.py` (新) | — |

**交付标准**：逾期提醒作为站内通知 + 邮件提醒 + 前端通知徽章

---

## 十一、文件变更汇总

### 新增文件

| 文件 | Phase | 状态 | 说明 |
|------|-------|------|------|
| `subagents/customer-followup/SUBAGENT.md` | P1 | ✅ 已完成 | 子智能体定义 |
| `src/skills/lead-management-1.0.0/SKILL.md` | P1 | ✅ 已完成 | 线索管理 skill |
| `src/skills/lead-management-1.0.0/scripts/lead_manager.py` | P1 | ✅ 已完成 | 线索管理 CLI |
| `src/skills/followup-tracking-1.0.0/SKILL.md` | P2 | ✅ 已完成 | 跟进跟踪 skill |
| `src/skills/followup-tracking-1.0.0/scripts/followup_manager.py` | P2 | ✅ 已完成 | 跟进管理 CLI |
| `src/tools/phone/ai_call_tool.py` | P2 | ✅ 已完成 | AI 外呼工具（Mock 实现，用户后续替换为真实 API） |
| `src/skills/conversion-analysis-1.0.0/SKILL.md` | P3 | 待开发 | 转化分析 skill |
| `src/skills/conversion-analysis-1.0.0/scripts/conversion_analyzer.py` | P3 | 待开发 | 转化分析 CLI |
| `src/api/customer_followup.py` | P1 | ✅ 已完成 | FastAPI 路由 |
| `src/services/notification_store.py` | P4 | 待开发 | 通知持久化层（基于现有 notification_service 扩展） |
| `src/services/notification_api.py` | P4 | 通知 API（前端轮询） |
| `src/utils/data_import.py` | P4 | 导入辅助（可选） |
| `frontend/src/api/followup.ts` | P1 | 前端 API |
| `frontend/src/components/followup/LeadManager.vue` | P1 | 线索管理页 |
| `frontend/src/components/followup/FollowupRecords.vue` | P2 | ✅ 已完成 | 跟进记录页 |
| `frontend/src/components/followup/SalesRepManager.vue` | P2 | ✅ 已完成 | 销售人员页 |
| `frontend/src/components/followup/ConversionFunnel.vue` | P3 | 转化漏斗页 |
| `frontend/src/components/NotificationBadge.vue` | P4 | 通知组件 |

### 修改文件

| 文件 | Phase | 说明 |
|------|-------|------|
| `src/main.py` | P1, P4 | 注册 customer_followup 和 notification_api 路由 |
| `src/core/agent.py` | P2 | ✅ 已完成 | 注册 ai_call 工具到 `_register_builtin_tools()` |
| `frontend/src/main.ts` | P1, P2 | 添加 customer-followup 业务页路由（含 P2 新增 followup-records、sales-reps 路由） |
| `deploy/init-postgres.sql` | P1, P4 | 添加 bs_customer_followup_* 和 notifications 表 |
| `deploy/db_update.sql` | P1, P4 | 增量建表语句 |

---

## 十二、关键参考文件

| 用途 | 文件路径 |
|------|----------|
| Skill CLI 脚本模板 | `src/skills/trade-customer-1.0.0/scripts/customer_manager.py` |
| API 路由模板 | `src/api/customer.py` |
| SUBAGENT.md 模板 | `subagents/trade-specialist/SUBAGENT.md` |
| 调度器机制 | `src/scheduler/manager.py` + `src/scheduler/executor.py` |
| Skill 执行器 | `src/core/skill_executor.py` |
| 业务页面前端模板 | `frontend/src/components/CustomerInfo.vue` |
| DB 变更规范 | `.claude/rules/database_dev.md` |
| 前端页面布局规范 | `.claude/rules/frontend_dev.md` |
