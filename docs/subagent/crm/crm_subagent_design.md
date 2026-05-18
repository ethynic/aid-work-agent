# 客户关系智能体（CRM Agent）设计方案

> 版本: v1.0 | 创建: 2026-05-06 | 状态: 待审核

## 一、背景与目标

### 1.1 业务背景

企业需要对其客户进行精细化管理：分析购买行为、售后交互、日志数据，为客户打标签、分层级。对于 B 端客户，还需通过互联网公开信息和社交媒体了解企业资质，生成更精准的标签。

核心痛点：
- 不同企业的客户数据结构不同（字段、格式、来源各异），无法用一套固定 schema 覆盖
- 客户数据散落在多个系统中（ERP、CRM、售后工单、日志平台），缺乏统一分析视角
- 标签体系需要根据行业和业务自定义，不能硬编码
- B 端客户的企业资质信息分散在工商网站、社交媒体等公开渠道，人工收集效率低

### 1.2 目标

构建一个**通用型客户关系分析子智能体**，能够：

1. **灵活接入数据**：支持每个租户自定义数据分类（如购买记录、售后记录、行为日志），每个分类通过文件上传或 API 接入客户数据
2. **智能标签系统**：基于数据分析自动生成标签，支持自定义标签规则
3. **客户分层**：根据标签组合和行为数据自动分层（如高价值客户、流失风险客户）
4. **B 端资质分析**：通过互联网公开信息和社交媒体数据，分析企业资质、经营状况
5. **自然语言交互**：用户通过对话方式查询客户洞察、调整标签规则、导出分析报告

### 1.3 设计原则

- **通用性优先**：数据 schema 不硬编码，由租户自定义
- **渐进增强**：先实现核心分析能力，再扩展高级功能
- **租户隔离**：所有数据严格按 tenant_id 隔离，包括数据源配置、标签体系、分析结果
- **可解释性**：每个标签和分层结论都能追溯数据来源和分析逻辑

---

## 二、整体架构

### 2.1 系统架构图

```
用户（对话交互 / 前端管理页面）
    │
    ▼
CRM 子智能体（SUBAGENT.md + system_prompt）
    │
    ├── 数据源管理 Skill（crm-data-source）
    │     ├── 接入数据分类定义（租户自定义）
    │     ├── 数据导入（Excel/CSV/JSON 上传）
    │     └── 数据查询与管理
    │
    ├── 客户画像 Skill（crm-profiling）
    │     ├── 标签定义与管理
    │     ├── 自动标签引擎（基于规则 + LLM 分析）
    │     ├── 客户分层模型
    │     └── 画像生成与更新
    │
    ├── 企业资质分析 Skill（crm-enrichment）
    │     ├── 工商信息查询（天眼查/企查查 API 或 Web Search）
    │     ├── 社交媒体信息采集（Web Search + Browser）
    │     ├── 经营风险评估
    │     └── 资质标签生成
    │
    └── 使用的现有工具
          ├── WebSearchTool — 互联网公开信息搜索
          ├── ContentGenerateTool — LLM 内容分析
          ├── ExcelReaderTool — 数据文件读取
          ├── KnowledgeBaseTool — 行业知识检索
          └── BrowserAutomationTool — 网页信息采集
```

### 2.2 数据流

```
1. 数据接入阶段：
   租户管理员 → 前端配置数据分类 → 存入 crm_data_categories 表
   租户用户 → 上传数据文件 → 解析并存入 crm_customer_data 表
   或者 → 调用 API 推送数据 → 存入 crm_customer_data 表

2. 数据分析阶段：
   用户对话 → "分析我的客户" →
     读取数据分类配置 →
     加载各分类数据 →
     LLM 分析 + 规则引擎 →
     生成标签 → 存入 crm_customer_tags 表
     生成分层 → 存入 crm_customer_segments 表

3. 资质增强阶段：
   用户对话 → "分析XX公司资质" →
     WebSearchTool 搜索公开信息 →
     BrowserAutomationTool 采集详情 →
     LLM 分析生成资质报告 →
     存入 crm_company_profiles 表
```

---

## 三、数据库设计

### 3.1 核心数据表

所有表名遵循 `bs_crm_` 前缀规范。

#### 3.1.1 数据分类定义表 `bs_crm_data_categories`

每个租户自定义的数据分类（如"购买记录"、"售后工单"、"行为日志"等）。

```sql
CREATE TABLE IF NOT EXISTS bs_crm_data_categories (
    id SERIAL PRIMARY KEY,
    category_id TEXT UNIQUE NOT NULL,       -- 分类ID，如 "cat_purchase_records"
    tenant_id TEXT NOT NULL,                 -- 租户ID
    name TEXT NOT NULL,                      -- 分类名称，如 "购买记录"
    description TEXT,                        -- 分类描述
    field_schema JSONB NOT NULL DEFAULT '{}', -- 字段定义 schema（见下方说明）
    match_rules JSONB DEFAULT '{}',          -- 客户匹配规则（见下方说明）
    created_by TEXT,                         -- 创建者 user_id
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_crm_categories_tenant ON bs_crm_data_categories(tenant_id);
```

**field_schema 格式说明**：

租户定义该分类的数据包含哪些字段，以及字段类型。用于数据导入时的校验和解析。

```json
{
    "fields": [
        {"name": "order_id", "type": "text", "label": "订单编号", "required": true},
        {"name": "customer_name", "type": "text", "label": "客户名称", "required": true},
        {"name": "amount", "type": "number", "label": "订单金额", "required": true},
        {"name": "order_date", "type": "date", "label": "下单日期", "required": false},
        {"name": "product", "type": "text", "label": "产品名称", "required": false},
        {"name": "quantity", "type": "number", "label": "数量", "required": false}
    ],
    "customer_key": "customer_name"    -- 用于匹配客户的唯一标识字段
}
```

**match_rules 格式说明**：

定义如何将该分类的数据关联到客户。支持多个匹配字段组合。

```json
{
    "match_by": ["customer_name", "phone"],
    "fuzzy_match": true,
    "match_threshold": 0.8
}
```

#### 3.1.2 客户主数据表 `bs_crm_customers`

从各数据源汇总生成的统一客户主数据。

```sql
CREATE TABLE IF NOT EXISTS bs_crm_customers (
    id SERIAL PRIMARY KEY,
    customer_id TEXT UNIQUE NOT NULL,       -- 客户ID，自动生成
    tenant_id TEXT NOT NULL,                 -- 租户ID
    customer_name TEXT NOT NULL,             -- 客户名称（人名或企业名）
    customer_type TEXT DEFAULT 'individual', -- individual / enterprise
    phone TEXT,                              -- 手机号
    email TEXT,                              -- 邮箱
    company TEXT,                            -- 企业名称（B端客户）
    unified_profile JSONB DEFAULT '{}',      -- 统一画像（各数据源合并后的关键信息）
    lifecycle_stage TEXT,                    -- 生命周期阶段：prospect/active/at_risk/churned/loyal
    segment TEXT,                            -- 分层：vip/high_value/regular/low_value/inactive
    overall_score NUMERIC(5,2),              -- 综合评分 0-100
    source TEXT,                             -- 主要来源：import/api/manual/auto
    tags TEXT[] DEFAULT '{}',                -- 标签列表（数组）
    last_activity_at TIMESTAMPTZ,            -- 最后活跃时间
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_crm_customers_tenant ON bs_crm_customers(tenant_id);
CREATE INDEX IF NOT EXISTS idx_crm_customers_name ON bs_crm_customers(tenant_id, customer_name);
CREATE INDEX IF NOT EXISTS idx_crm_customers_segment ON bs_crm_customers(tenant_id, segment);
CREATE INDEX IF NOT EXISTS idx_crm_customers_tags ON bs_crm_customers USING GIN(tags);
```

#### 3.1.3 客户原始数据表 `bs_crm_customer_data`

各数据分类的原始数据，以 JSONB 存储实现 schema-free。

```sql
CREATE TABLE IF NOT EXISTS bs_crm_customer_data (
    id SERIAL PRIMARY KEY,
    data_id TEXT UNIQUE NOT NULL,            -- 数据记录ID
    tenant_id TEXT NOT NULL,                 -- 租户ID
    category_id TEXT NOT NULL,               -- 关联数据分类
    customer_id TEXT,                        -- 关联客户ID（匹配后填充）
    customer_name TEXT,                      -- 原始客户名称（匹配前用）
    raw_data JSONB NOT NULL,                 -- 原始数据内容（完整字段）
    import_batch TEXT,                       -- 导入批次号
    import_source TEXT,                      -- import/api/manual
    status TEXT DEFAULT 'pending',           -- pending/matched/unmatched/error
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_crm_data_tenant ON bs_crm_customer_data(tenant_id);
CREATE INDEX IF NOT EXISTS idx_crm_data_category ON bs_crm_customer_data(tenant_id, category_id);
CREATE INDEX IF NOT EXISTS idx_crm_data_customer ON bs_crm_customer_data(customer_id);
CREATE INDEX IF NOT EXISTS idx_crm_data_raw ON bs_crm_customer_data USING GIN(raw_data);
```

**设计说明**：

- `raw_data` 使用 JSONB 存储，支持任意 schema，不同数据分类的字段结构各不相同
- `customer_id` 在数据匹配阶段从空值填充，匹配逻辑使用 `match_rules` 定义
- `status` 追踪匹配状态：pending（待匹配）、matched（已匹配）、unmatched（未匹配）、error（异常）
- PostgreSQL 的 JSONB 支持 GIN 索引和 `@>`、`?` 等操作符，可以高效查询

#### 3.1.4 标签定义表 `bs_crm_tag_definitions`

租户自定义的标签体系。

```sql
CREATE TABLE IF NOT EXISTS bs_crm_tag_definitions (
    id SERIAL PRIMARY KEY,
    tag_id TEXT UNIQUE NOT NULL,             -- 标签ID
    tenant_id TEXT NOT NULL,                 -- 租户ID
    tag_name TEXT NOT NULL,                  -- 标签名称
    tag_category TEXT,                       -- 标签分类：behavior/preference/attribute/risk/qualification
    tag_type TEXT DEFAULT 'auto',            -- auto（自动）/ manual（手动）/ rule（规则）
    description TEXT,                        -- 标签说明
    rule_config JSONB,                       -- 规则配置（tag_type=rule 时有效）
    color TEXT,                              -- 前端显示颜色
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_crm_tags_tenant ON bs_crm_tag_definitions(tenant_id);
CREATE INDEX IF NOT EXISTS idx_crm_tags_category ON bs_crm_tag_definitions(tenant_id, tag_category);
```

**rule_config 示例**：

```json
{
    "type": "threshold",
    "field": "total_amount",
    "operator": ">=",
    "value": 10000,
    "description": "累计消费金额 >= 10000 元"
}
```

```json
{
    "type": "frequency",
    "data_category": "purchase_records",
    "field": "order_date",
    "period": "90d",
    "min_count": 3,
    "description": "近90天下单 >= 3 次"
}
```

```json
{
    "type": "keyword",
    "data_category": "after_sales_records",
    "field": "content",
    "keywords": ["投诉", "退货", "差评"],
    "description": "售后记录中包含投诉/退货/差评关键词"
}
```

#### 3.1.5 客户标签关联表 `bs_crm_customer_tags`

```sql
CREATE TABLE IF NOT EXISTS bs_crm_customer_tags (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    tag_id TEXT NOT NULL,
    confidence NUMERIC(3,2) DEFAULT 1.0,    -- 置信度 0-1（LLM 生成的标签 < 1.0）
    source TEXT DEFAULT 'auto',              -- auto/rule/llm/manual
    evidence TEXT,                           -- 标签依据（追溯来源）
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(tenant_id, customer_id, tag_id)
);

CREATE INDEX IF NOT EXISTS idx_crm_ct_tenant ON bs_crm_customer_tags(tenant_id);
CREATE INDEX IF NOT EXISTS idx_crm_ct_customer ON bs_crm_customer_tags(customer_id);
CREATE INDEX IF NOT EXISTS idx_crm_ct_tag ON bs_crm_customer_tags(tag_id);
```

#### 3.1.6 企业资质信息表 `bs_crm_company_profiles`

B 端客户的企业资质分析结果。

```sql
CREATE TABLE IF NOT EXISTS bs_crm_company_profiles (
    id SERIAL PRIMARY KEY,
    profile_id TEXT UNIQUE NOT NULL,
    tenant_id TEXT NOT NULL,
    customer_id TEXT NOT NULL,               -- 关联客户
    company_name TEXT NOT NULL,
    registration_info JSONB DEFAULT '{}',    -- 工商注册信息
    business_status TEXT,                    -- 经营状态：active/revoked/cancelled/unknown
    risk_level TEXT,                         -- 风险等级：low/medium/high/critical
    risk_factors JSONB DEFAULT '[]',         -- 风险因素列表
    social_media JSONB DEFAULT '{}',         -- 社交媒体信息
    qualification_tags TEXT[] DEFAULT '{}',  -- 资质标签
    credit_score NUMERIC(5,2),               -- 信用评分
    summary TEXT,                            -- LLM 生成的综合摘要
    last_enriched_at TIMESTAMPTZ,            -- 最后更新时间
    data_sources TEXT[] DEFAULT '{}',        -- 数据来源列表
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_crm_profiles_tenant ON bs_crm_company_profiles(tenant_id);
CREATE INDEX IF NOT EXISTS idx_crm_profiles_customer ON bs_crm_company_profiles(customer_id);
CREATE INDEX IF NOT EXISTS idx_crm_profiles_company ON bs_crm_company_profiles(tenant_id, company_name);
```

#### 3.1.7 分层规则表 `bs_crm_segment_rules`

```sql
CREATE TABLE IF NOT EXISTS bs_crm_segment_rules (
    id SERIAL PRIMARY KEY,
    rule_id TEXT UNIQUE NOT NULL,
    tenant_id TEXT NOT NULL,
    segment_name TEXT NOT NULL,              -- 分层名称：vip/high_value/regular/low_value/inactive
    display_name TEXT,                       -- 显示名称：VIP客户/高价值客户/普通客户/低价值客户/不活跃客户
    description TEXT,
    priority INT DEFAULT 0,                  -- 优先级（数字越小优先级越高，匹配时从高到低）
    conditions JSONB NOT NULL DEFAULT '[]',  -- 匹配条件组
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_crm_segments_tenant ON bs_crm_segment_rules(tenant_id);
```

**conditions 格式**：

```json
[
    {
        "type": "tag",
        "tag_ids": ["tag_high_purchase", "tag_frequent_buyer"],
        "match": "all"
    },
    {
        "type": "score",
        "field": "overall_score",
        "operator": ">=",
        "value": 80
    },
    {
        "type": "field",
        "field": "lifecycle_stage",
        "operator": "in",
        "values": ["active", "loyal"]
    }
]
```

### 3.2 表关系图

```
bs_crm_data_categories (数据分类定义)
    │ 1:N
    ▼
bs_crm_customer_data (原始数据)
    │ N:1 (匹配后)
    ▼
bs_crm_customers (客户主数据) ──── N:N ──── bs_crm_tag_definitions (标签定义)
    │                                          │               │
    │ 1:1                                      │               │
    ▼                                          ▼               │
bs_crm_company_profiles (企业资质)   bs_crm_customer_tags (客户标签关联)
    │
    │ 条件引用
    ▼
bs_crm_segment_rules (分层规则) ── 条件引用 ──→ bs_crm_tag_definitions
```

---

## 四、Skill 设计

### 4.1 Skill 总览

| Skill | 版本 | 职责 |
|-------|------|------|
| `crm-data-source` | 1.0.0 | 数据分类管理、数据导入、数据匹配、数据查询 |
| `crm-profiling` | 1.0.0 | 标签管理、自动标签分析、客户分层、画像生成 |
| `crm-enrichment` | 1.0.0 | 企业信息搜索、社交媒体采集、资质评估、资质标签 |

### 4.2 crm-data-source Skill

#### 目录结构

```
src/skills/crm-data-source-1.0.0/
├── _meta.json
├── SKILL.md
├── scripts/
│   └── data_source_manager.py    # 核心业务逻辑脚本
└── logs/
```

#### 命令接口

| 命令 | 参数 | 说明 |
|------|------|------|
| `create-category` | name, description, field_schema, match_rules | 创建数据分类 |
| `update-category` | category_id, name?, description?, field_schema?, match_rules? | 更新数据分类 |
| `list-categories` | - | 列出租户所有数据分类 |
| `delete-category` | category_id | 删除数据分类（及其关联数据） |
| `import-data` | category_id, data (JSON数组), import_batch? | 批量导入数据 |
| `import-from-file` | category_id, file_path | 从文件（Excel/CSV）导入数据 |
| `match-data` | category_id? | 执行数据匹配（将原始数据关联到客户） |
| `list-customers` | page?, page_size?, segment?, tags?, keyword? | 查询客户列表 |
| `get-customer` | customer_id | 获取客户详情（含所有分类数据） |
| `search-customers` | keyword, category_id? | 全文搜索客户 |
| `get-customer-timeline` | customer_id | 获取客户时间线（按时间排列的所有交互） |
| `delete-customer` | customer_id | 删除客户 |
| `stats` | - | 租户级数据统计 |

#### 数据匹配算法

```
1. 对于每条 status=pending 的数据：
   a. 读取 match_rules，获取匹配字段列表（如 customer_name, phone）
   b. 在 bs_crm_customers 中查找：
      - 精确匹配优先：customer_name = raw_data[customer_key]
      - 模糊匹配：使用 PostgreSQL pg_trgm 相似度（如果 fuzzy_match=true）
   c. 如果找到匹配客户：
      - 填充 customer_id，设置 status=matched
      - 合并数据到客户 unified_profile
   d. 如果未找到：
      - 如果 raw_data 中有足够信息，自动创建新客户
      - 否则标记 status=unmatched
```

#### 数据合并策略

每次匹配到客户后，将该条数据的关键信息合并到 `unified_profile` JSONB 中：

```json
{
    "basic": {"name": "张三", "phone": "138xxxx", "email": "..."},
    "purchase_summary": {"total_amount": 15600, "order_count": 8, "last_order": "2026-04-20"},
    "after_sales_summary": {"ticket_count": 2, "last_ticket": "2026-03-15"},
    "behavior_summary": {"login_count": 45, "last_login": "2026-05-01"},
    "last_updated": "2026-05-06T10:00:00"
}
```

每个数据分类的数据匹配后，会更新对应分类的 summary 字段。合并策略：
- 数值型字段：累加或取最新值
- 文本型字段：取最新值
- 计数型字段：累加
- 时间型字段：取最新值

### 4.3 crm-profiling Skill

#### 目录结构

```
src/skills/crm-profiling-1.0.0/
├── _meta.json
├── SKILL.md
├── scripts/
│   └── profiling_manager.py      # 标签和分层逻辑
└── logs/
```

#### 命令接口

| 命令 | 参数 | 说明 |
|------|------|------|
| `create-tag` | name, category, type, description?, rule_config?, color? | 创建标签 |
| `update-tag` | tag_id, ... | 更新标签 |
| `list-tags` | category? | 列出标签 |
| `delete-tag` | tag_id | 删除标签 |
| `auto-tag` | customer_id?, tag_ids? | 执行自动标签分析 |
| `auto-tag-batch` | tag_ids? | 批量执行自动标签 |
| `manual-tag` | customer_id, tag_id, evidence? | 手动打标签 |
| `remove-tag` | customer_id, tag_id | 移除标签 |
| `create-segment-rule` | segment_name, display_name, conditions, priority | 创建分层规则 |
| `update-segment-rule` | rule_id, ... | 更新分层规则 |
| `list-segment-rules` | - | 列出分层规则 |
| `run-segmentation` | - | 执行分层计算 |
| `get-profile` | customer_id | 获取客户完整画像 |
| `batch-profile` | customer_ids? | 批量生成/更新画像 |
| `get-segment-stats` | - | 获取分层分布统计 |

#### 自动标签分析流程

```
1. 规则型标签（tag_type=rule）：
   - 遍历所有 rule 型标签
   - 读取 rule_config，在客户数据上执行条件判断
   - 命中则打标签，记录 evidence

2. LLM 分析型标签（tag_type=auto）：
   - 收集客户各数据分类的汇总信息
   - 构造分析 prompt（含标签定义、客户数据、已知标签）
   - LLM 返回标签推荐列表 + 置信度 + 依据
   - 置信度 >= 阈值（默认 0.7）的标签自动打上

3. 分析 Prompt 结构：
   [系统指令]
   你是一个客户分析专家。请根据以下客户数据，判断该客户应具备哪些标签。

   [可用标签列表]
   {tag_definitions}

   [客户数据]
   {customer_data_summary}

   [输出格式]
   返回 JSON 数组：
   [{"tag_id": "...", "confidence": 0.9, "evidence": "依据说明"}]
```

#### 客户分层流程

```
1. 按优先级排序分层规则（priority ASC）
2. 对每个客户，从最高优先级规则开始匹配：
   a. 评估 conditions 中的所有条件
   b. 如果所有条件满足，分配该 segment
   c. 跳到下一个客户
3. 未匹配任何规则的客户标记为 'unclassified'
4. 更新 bs_crm_customers.segment 和 overall_score
```

#### 评分模型

```
overall_score = 基础分(40) + 行为分(30) + 价值分(20) + 活跃度分(10)

- 基础分：信息完整度（有手机+邮箱+公司信息得分更高）
- 行为分：基于行为类标签的数量和置信度
- 价值分：基于购买类数据的金额、频率
- 活跃度分：基于最近一次活跃时间距今天数
```

### 4.4 crm-enrichment Skill

#### 目录结构

```
src/skills/crm-enrichment-1.0.0/
├── _meta.json
├── SKILL.md
├── scripts/
│   └── enrichment_manager.py     # 资质分析逻辑
└── logs/
```

#### 命令接口

| 命令 | 参数 | 说明 |
|------|------|------|
| `enrich-company` | customer_id | 对企业客户执行资质分析 |
| `enrich-batch` | customer_ids? | 批量执行资质分析（默认所有企业客户） |
| `search-company-info` | company_name | 搜索企业公开信息（不存储，仅返回） |
| `get-company-profile` | customer_id | 获取企业资质详情 |
| `update-risk-level` | customer_id, risk_level, risk_factors | 手动更新风险等级 |
| `list-risk-customers` | risk_level? | 列出有风险的客户 |

#### 资质分析流程

```
1. 信息采集阶段：
   a. WebSearchTool 搜索 "{company_name} 工商信息"
   b. WebSearchTool 搜索 "{company_name} 经营状况 风险"
   c. WebSearchTool 搜索 "{company_name} 社交媒体 微博 公众号"
   d. BrowserAutomationTool 访问搜索结果中的关键页面，提取详细信息

2. 信息解析阶段：
   a. ContentGenerateTool + 分析 Prompt 提取结构化信息：
      - 注册信息（注册资本、成立时间、法定代表人、经营范围）
      - 经营状态（正常/吊销/注销/异常经营）
      - 风险信息（法律诉讼、行政处罚、经营异常、失信记录）
      - 社交媒体信息（官方微博/公众号/知乎等活跃度）
   b. 生成信用评分和风险等级

3. 标签生成阶段：
   a. 基于采集信息自动生成资质标签：
      - 行业类型、企业规模、经营年限、信用等级
      - 风险标签：有诉讼风险、经营异常、失信企业
      - 正面标签：高新技术企业、上市公司、行业龙头
   b. 标签存入 bs_crm_customer_tags，类型标记为 qualification

4. 存储阶段：
   a. 更新 bs_crm_company_profiles
   b. 更新客户的 qualification_tags
```

---

## 五、子智能体定义

### 5.1 SUBAGENT.md 设计

```yaml
---
name: 客户关系智能体
description: 分析客户行为数据，智能打标签和分层，B端客户资质分析
version: 1.0.0
author: system
capabilities:
  - customer_data_management
  - customer_profiling
  - customer_segmentation
  - company_enrichment
  - risk_assessment
triggers:
  file_patterns:
    - "*.xlsx"
    - "*.csv"
tools:
  inherit: true
  additional:
    - content_generate
skills:
  allowed:
    - crm-data-source
    - crm-profiling
    - crm-enrichment
    # excel-data-assistant 已迁移为 excel_process 工具
context:
  max_input_tokens: 12000
  max_output_tokens: 4000
business_pages:
  - id: customers
    title: 客户管理
    icon: 👥
    route: /crm/customers
  - id: tags
    title: 标签管理
    icon: 🏷️
    route: /crm/tags
  - id: segments
    title: 客户分层
    icon: 📊
    route: /crm/segments
  - id: data-sources
    title: 数据源
    icon: 📥
    route: /crm/data-sources
  - id: company-profiles
    title: 企业资质
    icon: 🏢
    route: /crm/company-profiles
---
```

### 5.2 system_prompt 核心内容（Markdown body）

```
# 角色
你是一个专业的客户关系分析助手。你帮助企业分析客户数据，生成客户画像和标签，实现精细化客户管理。

# 核心工作流

## 1. 数据接入
当用户首次使用或需要接入新数据时：
- 询问用户需要分析哪类客户数据（购买记录、售后记录、行为日志等）
- 帮助用户定义数据分类的字段结构
- 指导用户上传数据文件或通过对话提供数据
- 调用 crm-data-source 技能完成数据导入和匹配

## 2. 客户分析
当用户需要分析客户时：
- 调用 crm-profiling 技能执行自动标签分析
- 展示分析结果，解释每个标签的依据
- 根据分层规则对客户进行分层
- 生成客户画像摘要

## 3. 企业资质分析（B端客户）
当用户需要了解企业客户资质时：
- 调用 crm-enrichment 技能搜索公开信息
- 综合工商信息、社交媒体、经营状况生成资质报告
- 标记风险等级和风险因素

## 4. 查询与报告
当用户需要查询客户信息时：
- 通过 crm-data-source 技能查询客户数据
- 生成分析报告（可导出 Excel）
- 回答关于客户画像、标签、分层的各类问题

# 约束
- 所有数据分析必须基于租户实际接入的数据，不臆造
- 标签结论必须有数据依据（evidence），可追溯
- B端资质分析基于公开信息，注明信息来源和采集时间
- 敏感信息（手机号、邮箱等）脱敏显示
- 不同租户的数据严格隔离

# 数据分类示例
引导用户创建数据分类时，可参考以下常见类型：
- 购买记录：订单编号、客户名称、产品、金额、日期、数量
- 售后记录：工单编号、客户名称、问题类型、处理结果、日期
- 行为日志：客户名称、行为类型、页面/功能、时间、设备
- 客户信息：姓名、手机、邮箱、公司、职位、来源
```

---

## 六、前端设计

### 6.1 业务页面

子智能体定义了 5 个 business_pages，每个页面对应一个前端路由：

#### 6.1.1 客户管理页面 `/crm/customers`

```
┌─────────────────────────────────────────────────────┐
│  客户管理                          [搜索...] [筛选▼] │
├─────────────────────────────────────────────────────┤
│ 分层筛选: [全部] [VIP] [高价值] [普通] [低价值] [不活跃] │
│ 标签筛选: [标签1] [标签2] [标签3] ...               │
├───────┬──────┬──────┬─────┬──────┬─────┬───────────┤
│ 名称  │ 类型 │ 分层  │ 标签 │ 评分  │ 活跃 │ 操作     │
├───────┼──────┼──────┼─────┼──────┼─────┼───────────┤
│ XX公司│ 企业  │ VIP  │ 🏷🏷🏷│ 92   │ 2天前│ 详情 更多│
│ 张三  │ 个人  │ 高价值│ 🏷🏷 │ 78   │ 5天前│ 详情 更多│
│ ...   │      │      │     │      │     │           │
└───────┴──────┴──────┴─────┴──────┴─────┴───────────┘
共 156 条  [上一页] 1 2 3 ... 8 [下一页]
```

#### 6.1.2 标签管理页面 `/crm/tags`

```
┌─────────────────────────────────────────────────────┐
│  标签管理                          [+ 新建标签]      │
├─────────────────────────────────────────────────────┤
│ 标签分类: [全部] [行为] [偏好] [属性] [风险] [资质]  │
├──────┬──────┬──────┬─────┬──────┬──────────────────┤
│ 标签  │ 分类  │ 类型  │ 客户数│ 规则  │ 操作          │
├──────┼──────┼──────┼─────┼──────┼──────────────────┤
│ 高消费│ 行为  │ 规则  │ 23  │ 金额≥1万│ [编辑] [删除] │
│ 活跃客户│ 行为│ 自动  │ 45  │ LLM分析│ [编辑] [删除]  │
│ ...   │      │      │     │      │                │
└──────┴──────┴──────┴─────┴──────┴──────────────────┘
```

#### 6.1.3 数据源页面 `/crm/data-sources`

```
┌─────────────────────────────────────────────────────┐
│  数据源管理                        [+ 新建数据分类]   │
├─────────────────────────────────────────────────────┤
│ ┌─────────────────────────────────────────────────┐ │
│ │ 📦 购买记录                1,250 条数据          │ │
│ │ 字段: 订单编号, 客户名称, 金额, 日期, 产品       │ │
│ │ 匹配率: 89%  │ [上传数据] [查看数据] [编辑] [删除]│ │
│ └─────────────────────────────────────────────────┘ │
│ ┌─────────────────────────────────────────────────┐ │
│ │ 📦 售后记录                  320 条数据          │ │
│ │ 字段: 工单编号, 客户名称, 问题类型, 处理结果     │ │
│ │ 匹配率: 92%  │ [上传数据] [查看数据] [编辑] [删除]│ │
│ └─────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────┘
```

#### 6.1.4 客户分层页面 `/crm/segments`

```
┌─────────────────────────────────────────────────────┐
│  客户分层                        [编辑规则] [重新计算]│
├─────────────────────────────────────────────────────┤
│                                                     │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐          │
│  │ VIP      │  │ 高价值    │  │ 普通      │         │
│  │ 12 客户  │  │ 35 客户   │  │ 68 客户   │         │
│  │ 7.7%     │  │ 22.4%    │  │ 43.6%    │          │
│  └──────────┘  └──────────┘  └──────────┘          │
│                                                     │
│  ┌──────────┐  ┌──────────┐                         │
│  │ 低价值    │  │ 不活跃    │                        │
│  │ 28 客户  │  │ 13 客户   │                        │
│  │ 17.9%    │  │ 8.3%     │                         │
│  └──────────┘  └──────────┘                         │
│                                                     │
│  [分层规则列表]                                     │
│  优先级 1: VIP ← 条件: 评分>=80 且 标签含"高消费"   │
│  优先级 2: 高价值 ← 条件: 评分>=60 且 活跃          │
│  ...                                                │
└─────────────────────────────────────────────────────┘
```

#### 6.1.5 企业资质页面 `/crm/company-profiles`

```
┌─────────────────────────────────────────────────────┐
│  企业资质                          [批量分析]        │
├─────────────────────────────────────────────────────┤
│ 风险筛选: [全部] [低风险] [中风险] [高风险] [严重]   │
├──────┬──────┬──────┬──────┬──────────┬─────────────┤
│ 企业 │ 风险 │ 信用 │ 资质标签│ 最后更新  │ 操作       │
├──────┼──────┼──────┼──────┼──────────┼─────────────┤
│ XX公司│ 低  │ 85   │ 高新技术│ 3天前    │ [详情][更新]│
│ YY公司│ 高  │ 32   │ 有诉讼 │ 1周前    │ [详情][更新]│
└──────┴──────┴──────┴──────┴──────────┴─────────────┘
```

### 6.2 前端 API 设计

| 方法 | 路径 | 说明 |
|------|------|------|
| **数据源管理** | | |
| GET | `/api/crm/categories` | 列出数据分类 |
| POST | `/api/crm/categories` | 创建数据分类 |
| PUT | `/api/crm/categories/{category_id}` | 更新数据分类 |
| DELETE | `/api/crm/categories/{category_id}` | 删除数据分类 |
| POST | `/api/crm/categories/{category_id}/import` | 上传文件导入数据 |
| GET | `/api/crm/categories/{category_id}/data` | 查看分类下的数据 |
| POST | `/api/crm/categories/{category_id}/match` | 执行数据匹配 |
| **客户管理** | | |
| GET | `/api/crm/customers` | 客户列表（支持筛选） |
| GET | `/api/crm/customers/{customer_id}` | 客户详情 |
| GET | `/api/crm/customers/{customer_id}/timeline` | 客户时间线 |
| DELETE | `/api/crm/customers/{customer_id}` | 删除客户 |
| **标签管理** | | |
| GET | `/api/crm/tags` | 标签列表 |
| POST | `/api/crm/tags` | 创建标签 |
| PUT | `/api/crm/tags/{tag_id}` | 更新标签 |
| DELETE | `/api/crm/tags/{tag_id}` | 删除标签 |
| POST | `/api/crm/tags/auto-apply` | 执行自动标签 |
| **分层管理** | | |
| GET | `/api/crm/segments` | 分层规则列表 + 分布统计 |
| POST | `/api/crm/segments` | 创建分层规则 |
| PUT | `/api/crm/segments/{rule_id}` | 更新分层规则 |
| DELETE | `/api/crm/segments/{rule_id}` | 删除分层规则 |
| POST | `/api/crm/segments/run` | 执行分层计算 |
| **企业资质** | | |
| GET | `/api/crm/company-profiles` | 企业资质列表 |
| GET | `/api/crm/company-profiles/{customer_id}` | 企业资质详情 |
| POST | `/api/crm/company-profiles/{customer_id}/enrich` | 触发资质分析 |
| POST | `/api/crm/company-profiles/batch-enrich` | 批量资质分析 |
| **统计** | | |
| GET | `/api/crm/stats` | CRM 数据总览统计 |

---

## 七、需要新增的基础设施组件

### 7.1 已具备的基础能力

| 能力 | 现有组件 | 是否满足 |
|------|----------|----------|
| 子智能体框架 | SUBAGENT.md + loader + executor | ✅ 满足 |
| 工具调用 | ToolRegistry + ToolExecutor | ✅ 满足 |
| Skill 执行 | SkillLoader + SkillExecutor（CLI 脚本） | ✅ 满足 |
| Web 搜索 | WebSearchTool（Tavily） | ✅ 满足 |
| 文件解析 | ExcelReaderTool, FileReaderTool | ✅ 满足 |
| LLM 内容生成 | ContentGenerateTool | ✅ 满足 |
| 知识库 RAG | KnowledgeBaseTool | ✅ 满足 |
| 浏览器自动化 | BrowserAutomationTool | ✅ 满足 |
| 多租户隔离 | TenantContextMiddleware + ContextVar | ✅ 满足 |
| 前端页面路由 | business_pages 配置 | ✅ 满足 |
| 数据库表自动创建 | init_tables() 机制 | ✅ 满足 |

### 7.2 需要新增的组件

#### 7.2.1 Skill CLI 脚本模板改进

**现状**：`customer_manager.py` 是一个独立的 CLI 脚本，通过 `skill_execute` 以子进程方式调用。脚本需要自行初始化数据库连接、解析 JSON 参数、处理错误。

**需要改进**：
- 提供一个公共的 `BaseSkillScript` 基类，封装数据库连接初始化、JSON 参数解析、tenant_id 注入、日志记录等通用逻辑
- 各 Skill 脚本继承基类，只需关注业务逻辑

```python
# src/tools/skill/base_script.py（新增）
class BaseSkillScript:
    """Skill CLI 脚本基类"""

    def __init__(self):
        self.tenant_id = os.environ.get("TENANT_ID", "")
        self._init_db()

    def _init_db(self):
        """自动初始化数据库连接池"""

    def parse_args(self) -> dict:
        """解析 CLI JSON 参数（复用 parse_json_safe）"""

    def run(self):
        """入口：解析命令 → 分发 → 输出 JSON 结果"""
        command = sys.argv[1]
        args = self.parse_args()
        result = getattr(self, f"cmd_{command}")(args)
        print(json.dumps(result, ensure_ascii=False))

    def log_execution(self, command, args, result):
        """写入 logs/ 目录的审计日志"""
```

**影响范围**：`src/tools/skill/` 目录。不影响现有 skill 执行流程，只是新增一个可选的基类。

#### 7.2.2 skill_execute 的 tenant_id 传递

**现状**：`SkillExecutor` 执行脚本时不传递 tenant_id，脚本无法感知当前租户。

**需要改进**：
- `skill_execute` 调用脚本时，通过环境变量传递 `TENANT_ID`
- 来源：`get_current_tenant_id()`（从 ContextVar 获取）

```python
# src/core/skill_executor.py 修改
env = {
    **os.environ,
    "TENANT_ID": get_current_tenant_id() or "",
    "USER_ID": user_id or "",
}
process = subprocess.run(cmd, capture_output=True, text=True, env=env)
```

**影响范围**：`src/core/skill_executor.py`，改动量小，向后兼容。

#### 7.2.3 前端文件上传与解析 API

**现状**：`/api/documents/upload` 已支持文件上传到知识库，但不支持将解析后的数据存入业务表。

**需要新增**：
- `/api/crm/categories/{category_id}/import` 端点
- 接收上传的 Excel/CSV 文件
- 根据 field_schema 解析文件内容
- 调用 data_source_manager.py 的 import-data 命令

```python
# src/api/crm.py（新增）
@router.post("/categories/{category_id}/import")
async def import_category_data(
    category_id: str,
    file: UploadFile,
    tenant_id: str = Depends(get_current_tenant_id)
):
    # 1. 读取分类定义，获取 field_schema
    # 2. 根据文件类型选择解析器（Excel/CSV）
    # 3. 解析文件内容为 JSON 数组
    # 4. 调用 data_source_manager import-data
    # 5. 返回导入结果（成功数、失败数、匹配数）
```

**影响范围**：新增 `src/api/crm.py`，在 `src/main.py` 中注册路由。

#### 7.2.4 JSONB 查询工具

**现状**：系统没有通用的 JSONB 查询构建器，各脚本需要手写 JSONB 查询 SQL。

**需要新增**：
- `src/db/jsonb_query.py`：封装常用 JSONB 查询操作

```python
# src/db/jsonb_query.py（新增）
class JsonbQuery:
    """PostgreSQL JSONB 查询构建器"""

    @staticmethod
    def path_extract(column: str, path: str) -> str:
        """提取 JSONB 路径值"""
        return f"{column}->>'{path}'"

    @staticmethod
    def contains(column: str, key: str, value: Any) -> tuple:
        """JSONB 包含查询"""
        return f"{column} @> %s::jsonb", json.dumps({key: value})

    @staticmethod
    def array_contains(column: str, value: str) -> str:
        """数组包含查询（tags 字段）"""
        return f"{value} = ANY({column})"

    @staticmethod
    def search_text(column: str, keyword: str) -> str:
        """JSONB 内文本搜索"""
        return f"{column}::text ILIKE %s"
```

**影响范围**：新增文件，不影响现有代码。

#### 7.2.5 异步任务执行机制（资质分析批量处理）

**现状**：Skill 通过 `skill_execute` 同步执行，耗时操作会阻塞对话。

**需要改进**：
- 长时间运行的分析任务（如批量标签分析、批量资质查询）应异步执行
- 利用现有的 `src/tools/scheduler/` 定时任务机制，或新增轻量级异步任务队列

**方案 A（推荐）**：利用现有的 `ScheduledTaskTool` 基础设施，将批量分析包装为一次性定时任务

**方案 B**：新增简单的 asyncio 后台任务管理器

```python
# src/core/background_task.py（新增）
import asyncio
from typing import Dict, Callable

class BackgroundTaskManager:
    """轻量级异步后台任务管理器"""

    def __init__(self):
        self._tasks: Dict[str, asyncio.Task] = {}

    def submit(self, task_id: str, coro: Coroutine) -> str:
        """提交后台任务"""
        self._tasks[task_id] = asyncio.create_task(coro)
        return task_id

    def get_status(self, task_id: str) -> dict:
        """获取任务状态"""
        task = self._tasks.get(task_id)
        if not task:
            return {"status": "not_found"}
        if task.done():
            return {"status": "completed", "result": task.result()}
        return {"status": "running"}

    def cleanup(self):
        """清理已完成的任务"""
        ...
```

**影响范围**：新增文件，独立使用。

---

## 八、开发计划

### Phase 1: 核心数据层（预计 3-4 天）

| 任务 | 涉及文件 | 依赖 |
|------|----------|------|
| 1.1 基础设施改进：BaseSkillScript 基类 | `src/tools/skill/base_script.py`（新增） | 无 |
| 1.2 基础设施改进：skill_execute 传递 tenant_id | `src/core/skill_executor.py` | 无 |
| 1.3 crm-data-source skill 开发 | `src/skills/crm-data-source-1.0.0/`（新增） | 1.1, 1.2 |
| 1.4 数据源 API 开发 | `src/api/crm.py`（新增）, `src/main.py` | 1.3 |
| 1.5 前端数据源页面 | `frontend/src/...` | 1.4 |

**验收标准**：
- [ ] 租户可自定义数据分类（字段 schema）
- [ ] 支持上传 Excel/CSV 文件导入数据
- [ ] 数据匹配逻辑正常工作
- [ ] 多租户数据隔离验证通过

### Phase 2: 标签与分层（预计 3-4 天）

| 任务 | 涉及文件 | 依赖 |
|------|----------|------|
| 2.1 crm-profiling skill 开发 | `src/skills/crm-profiling-1.0.0/`（新增） | Phase 1 |
| 2.2 标签管理 API | `src/api/crm.py` 扩展 | 2.1 |
| 2.3 分层规则 API | `src/api/crm.py` 扩展 | 2.1 |
| 2.4 前端标签管理页面 | `frontend/src/...` | 2.2 |
| 2.5 前端客户分层页面 | `frontend/src/...` | 2.3 |

**验收标准**：
- [ ] 规则型标签自动执行正常
- [ ] LLM 分析型标签生成准确
- [ ] 客户分层规则正确匹配
- [ ] 标签和分层有数据依据可追溯

### Phase 3: 企业资质分析（预计 2-3 天）

| 任务 | 涉及文件 | 依赖 |
|------|----------|------|
| 3.1 crm-enrichment skill 开发 | `src/skills/crm-enrichment-1.0.0/`（新增） | Phase 1 |
| 3.2 资质分析 API | `src/api/crm.py` 扩展 | 3.1 |
| 3.3 前端企业资质页面 | `frontend/src/...` | 3.2 |

**验收标准**：
- [ ] 企业公开信息搜索正常
- [ ] 风险评估结论准确
- [ ] 资质标签自动生成
- [ ] 信息来源可追溯

### Phase 4: 子智能体整合与前端完善（预计 2-3 天）

| 任务 | 涉及文件 | 依赖 |
|------|----------|------|
| 4.1 SUBAGENT.md 编写 | `subagents/crm-agent/SUBAGENT.md`（新增） | Phase 1-3 |
| 4.2 前端客户管理页面 | `frontend/src/...` | Phase 2, 3 |
| 4.3 客户详情页面 | `frontend/src/...` | 4.2 |
| 4.4 端到端测试 | `tests/` | 4.1 |

**验收标准**：
- [ ] 子智能体对话流程完整
- [ ] 前端所有页面功能正常
- [ ] 端到端测试通过

---

## 九、风险与待讨论事项

### 9.1 风险

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| JSONB 查询性能 | 大数据量下 JSONB 字段查询可能慢 | 对常用查询路径建 GIN 索引；复杂查询回退到应用层 |
| LLM 标签分析成本 | 批量标签分析需要多次 LLM 调用 | 控制单次分析客户数；使用 smaller model 做初步筛选 |
| 网页采集稳定性 | 目标网站结构变化导致采集失败 | 优先使用 WebSearchTool 的结构化结果；Browser 仅作补充 |
| 数据匹配准确率 | 模糊匹配可能误匹配 | 提供匹配审核机制；低置信度匹配标记为需人工确认 |
| 批量分析耗时 | 批量标签/资质分析可能超时 | 使用异步任务；前端轮询进度 |

### 9.2 待讨论事项

1. **企业信息数据源**：是使用天眼查/企查查等付费 API，还是仅依赖 WebSearchTool + Browser 的免费采集？前者数据准确但需要付费，后者免费但不稳定。

2. **数据导入上限**：单次导入数据量是否需要限制？建议单次最大 10000 条，超过分批处理。

3. **标签分析频率**：自动标签是实时触发还是定时批量执行？建议新数据导入后自动触发规则型标签，LLM 型标签定时执行（如每日一次）。

4. **评分模型是否需要可配置**：当前评分公式硬编码，是否需要支持租户自定义权重？

5. **数据导出格式**：分析报告的导出格式需求？Excel/PDF/Word？

6. **客户数据隐私**：存储的客户手机号、邮箱等敏感信息是否需要加密？根据项目规范"敏感信息必须加密"的要求，可能需要增加字段级加密。

---

## 十、文件变更汇总

### 新增文件

| 文件 | Phase | 说明 |
|------|-------|------|
| `subagents/crm-agent/SUBAGENT.md` | P4 | 子智能体定义 |
| `src/skills/crm-data-source-1.0.0/` | P1 | 数据源管理 Skill（SKILL.md + scripts/） |
| `src/skills/crm-profiling-1.0.0/` | P2 | 标签与分层 Skill |
| `src/skills/crm-enrichment-1.0.0/` | P3 | 企业资质分析 Skill |
| `src/api/crm.py` | P1 | CRM API 路由 |
| `src/tools/skill/base_script.py` | P1 | Skill 脚本基类 |
| `src/db/jsonb_query.py` | P1 | JSONB 查询工具 |
| `src/core/background_task.py` | P2 | 异步任务管理器 |
| `frontend/src/api/crm.ts` | P1 | 前端 CRM API |
| `frontend/src/components/crm/` | P1-4 | 前端 CRM 页面组件 |

### 修改文件

| 文件 | Phase | 说明 |
|------|-------|------|
| `src/core/skill_executor.py` | P1 | 传递 tenant_id 环境变量 |
| `src/main.py` | P1 | 注册 CRM API 路由 |
| `src/config/settings.py` | P1 | 新增 CRM 相关配置 |
| `configs/config.yaml` | P1 | 新增 CRM 配置项 |
| `deploy/init-postgres.sql` | P1 | 新增 bs_crm_* 表定义 |
| `deploy/db_update.sql` | P1 | 增量建表语句 |
| `src/subagents/__init__.py` 或配置 | P4 | 注册新子智能体 |
| `configs/config.yaml` skills 配置 | P4 | 将 CRM skills 加入白名单 |
