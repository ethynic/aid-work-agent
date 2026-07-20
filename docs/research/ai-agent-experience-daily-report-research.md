# AI 智能体行业产品体验提升调研与「工作日报」方案设计

> 关联文档：
> - [ideas.md §调研报告索引](../ideas.md)
> - [企业级智能体平台调研](./enterprise-agent-platform-research.md)
> - [AI Agent 可观测性调研](./observability-design-research.md)
> - [租户积分充值与计费设计](../system/saas/tenant-credit-billing-design.md)
>
> 文档索引：[ideas.md §调研报告索引](../ideas.md)
> 创建日期：2026-07-20
> 定位：以「工作日报」为切入点，系统调研行业领先产品在「提升用户使用体验感、证明 AI 价值、推动续费」方面的做法，并给出可落地的方案设计。

---

## 一、调研目的

本项目是企业员工智能代理系统（B2B SaaS），租户企业领导付费的核心理由是「员工真的在用、用了真的有效果」。但现状下，**「AI 价值」对企业领导是不可见的**：

- 员工每天用了多少次、用了什么数字员工、解决了什么问题 → 领导看不到
- 哪些员工用得好、哪些员工从没用过 → 领导看不到
- 这个月付的积分费用花在哪些场景上、ROI 如何 → 领导只能看到积分消耗，看不到产出

用户的初步想法是「数字员工模拟真人员工写工作日报」。这是一个非常好的切入点，但只覆盖了「个人复盘」一侧；要让企业领导真正感知到价值，还需要「团队/租户级用量洞察」一侧。本调研在用户原始想法基础上做系统性扩展。

**调研对象**（基于公开知识与产品文档）：

| 产品 | 类型 | 调研重点 |
|------|------|---------|
| Microsoft 365 Copilot Dashboard | 办公套件 AI 助手 | 团队/个人采纳度分析、节省时间量化 |
| ChatGPT (Team/Enterprise) | 通用对话 AI | 管理员用量面板、Workspace 分析 |
| Claude (Team/Enterprise) | 通用对话 AI | Projects、Memory、对话总结 |
| Glean Assistant | 企业搜索 + AI 助手 | Usage Analytics、热门查询、AI 价值量化 |
| Coze / 扣子 | AI Agent 平台 | 数据中心、Bot 用量、对话洞察 |
| Dify | 开源 LLM 应用平台 | 应用级 Analytics、消息满意度 |
| 飞书智能伙伴 | 办公套件 AI | 工作日报、AI 周报、自动总结 |
| Cursor / GitHub Copilot | 研发 AI | 个人 Activity、Weekly Summary、团队采纳率 |
| Notion AI | 协作 AI | 项目级 AI 使用统计 |

> 说明：本调研基于对上述产品公开功能与行业实践的认知，不依赖实时网络抓取。涉及具体功能边界时，已在文中标注「以官方文档为准」。

---

## 二、行业产品功能对照

### 2.1 「价值证明」相关功能矩阵

| 能力 | M365 Copilot | ChatGPT Team | Glean | Coze | Dify | 飞书智能伙伴 | Cursor | **本项目** |
|------|--------------|--------------|-------|------|------|-------------|--------|----------|
| 个人使用统计 | ✅ Viva Insights | ⚠️ 仅对话历史 | ✅ My Insights | ⚠️ | ❌ | ✅ 工作日报 | ✅ Daily/Weekly | ❌ |
| 团队用量面板 | ✅ Copilot Dashboard | ✅ Workspace Analytics | ✅ Admin Console | ✅ 数据中心 | ✅ App Analytics | ✅ 管理后台 | ✅ Team Insights | ⚠️ 仅积分消耗 |
| AI 自动生成日报 | ⚠️ 偏统计 | ❌ | ⚠️ | ❌ | ❌ | ✅ 工作日报/AI 周报 | ✅ Weekly Summary | ❌ |
| 节省时间量化 | ✅ Time Saved | ❌ | ✅ Search Time Saved | ❌ | ❌ | ⚠️ | ⚠️ | ❌ |
| 采纳率分析 | ✅ Adoption Rate | ✅ 活跃成员 | ✅ Active Users | ⚠️ | ⚠️ | ⚠️ | ✅ | ❌ |
| 热门场景识别 | ✅ Scenarios | ❌ | ✅ Top Queries | ✅ 热门 Bot | ✅ 热门问题 | ⚠️ | ❌ | ❌ |
| 多渠道推送 | ⚠️ Viva Insights 邮件 | ❌ | ⚠️ | ❌ | ❌ | ✅ 飞书内推送 | ⚠️ IDE 内 | ❌ |
| 对话级复盘 | ❌ | ⚠️ 对话历史 | ❌ | ⚠️ | ⚠️ | ✅ 会议纪要 | ⚠️ | ❌ |

> 注：⚠️ 表示部分支持或需手动触发；以各产品当前官方文档为准。

### 2.2 行业关键趋势

1. **「采纳度分析」（Adoption Analytics）成为企业 AI 产品的标配**。M365 Copilot Dashboard 是这一类的标杆：让管理员看到「AI 真的被用了、用了多少、省了多少时间」。本项目目前只有「积分消耗」这一项，缺失「采纳度」和「价值量化」两端。

2. **AI 自动生成日报/周报是新一波趋势**。飞书智能伙伴的「工作日报」、Cursor 的「Weekly Summary」、Notion AI 的「项目总结」都在让 AI 自己把零散的对话/操作聚合成结构化叙事。这比纯统计图表更有「人感」，也更容易被领导理解。

3. **双视角（个人 + 团队）缺一不可**。只做个人日报，企业领导看不到团队全景；只做团队面板，员工感受不到 AI 的陪伴感。M365 Copilot Dashboard 同时提供「My Insights」和「Team Insights」就是典型案例。

4. **主动推送优于被动查询**。Glean、Viva Insights 都把关键洞察通过邮件/IM 主动推送给管理员，而不是等管理员登录后台。企业领导很忙，不会每天登录 SaaS 后台。

5. **节省时间 / 价值量化是续费的最强说服力**。M365、Glean 都在量化「AI 帮你节省了多少小时」，这个数字直接对应「如果不续费，这些时间又要还回去」，是最强的续费驱动。

---

## 三、现状分析与差距诊断

### 3.1 本项目数据基础（已具备）

`chat_records` 表已经记录了非常丰富的数据，完全可支撑日报功能：

| 字段 | 用途 |
|------|------|
| `tenant_id` / `user_id` | 租户隔离、用户维度聚合 |
| `session_id` / `subagent_id`（关联 `chat_sessions`） | 会话维度、数字员工维度聚合 |
| `user_message` / `assistant_message` | 完整对话内容，LLM 摘要的输入 |
| `execution_details`（JSON） | 工具调用、子智能体调用、迭代次数、执行计划 |
| `prompt_tokens` / `completion_tokens` / `credit_cost` | 成本维度 |
| `duration_ms` / `status` / `error_message` | 性能与质量维度 |
| `source_type` | 渠道分布（chat / wecom_kf / dingtalk / feishu / wecom_personal_rpa） |
| `created_at` | 时间维度（按日/周/月聚合） |

**结论**：数据基础完备，无需新增埋点，只需在现有数据上做聚合 + LLM 摘要。

### 3.2 本项目现有相关能力

- **平台管理员**：`PlatformTokenUsage.vue` + `admin_reports.py` 已提供「租户数 / 本月 Token / 今日对话数」等顶层指标
- **租户管理员**：`TenantTokenUsage.vue` + `TenantDashboard.vue` 已提供租户级用量
- **积分计费**：`chat_records.credit_cost` 已记录每条对话的积分消耗，`tenant_recharges` 表已记录充值

### 3.3 关键差距

| 维度 | 现状 | 差距 |
|------|------|------|
| 个人复盘 | ❌ 无 | 员工无法回顾「我今天和 AI 聊了什么、解决了什么」 |
| 团队洞察 | ⚠️ 仅积分消耗 | 管理员看不到「谁在用、用得好不好、产生什么价值」 |
| AI 价值证明 | ❌ 无 | 企业领导无法量化「AI 帮团队省了多少时间」 |
| 主动触达 | ❌ 无 | 所有数据都需要管理员主动登录后台才能看到 |
| 工作内容化 | ❌ 无 | 对话内容散落在 `chat_records`，未被结构化成「工作成果」 |

---

## 四、工作日报功能方案设计

### 4.1 设计目标与原则

**目标**：让企业领导明确感知「员工在用 AI、AI 在产生价值」，从而愿意持续付费。

**原则**：

1. **双视角同步建设**：个人日报（给员工用）+ 团队日报（给管理员/领导用），两者数据源一致但呈现视角不同
2. **AI 生成 + 模板化展示**：用 LLM 把零散对话聚合成结构化叙事，关键指标用图表固化，避免「纯 AI 文本」不可控
3. **数据驱动 + 故事化叙述**：数字证明价值，故事让人共情，两者缺一不可
4. **主动推送 + 被动查询**：日报默认主动推送到企微/邮件，同时提供「日报中心」供历史查询
5. **隐私可控**：团队日报只暴露工作内容摘要，不暴露对话原文；管理员可下钻但不能旁路租户隔离
6. **成本可控**：日报生成走小模型（如 `qwen-turbo`）+ 缓存，避免给主链路加成本

### 4.2 双视角总览

```
┌─────────────────────────────────────────────────────────────┐
│                    数据源：chat_records                       │
│         （按 tenant_id + user_id + created_at 聚合）          │
└─────────────────────────────────────────────────────────────┘
                              │
              ┌───────────────┴───────────────┐
              ▼                               ▼
   ┌──────────────────┐            ┌──────────────────┐
   │   个人日报         │            │   团队日报         │
   │  （员工视角）      │            │ （管理员/领导视角） │
   └──────────────────┘            └──────────────────┘
              │                               │
              ▼                               ▼
   - 今日对话 N 条                  - 活跃员工 N 人（占比）
   - 涉及数字员工 M 个               - Top 3 活跃员工
   - 主要工作内容（LLM 摘要）         - Top 3 数字员工
   - 节省时间估算                    - 团队主要工作成果（LLM 摘要）
   - 明日建议                        - 节省时间估算
                                    - 未使用员工提醒
                                    - 续费建议
```

### 4.3 个人日报（员工视角）

#### 4.3.1 内容结构

| 模块 | 内容 | 数据来源 |
|------|------|---------|
| 今日概览 | 对话数 / 涉及数字员工 / 消耗积分 / 预估节省时间 | `chat_records` 聚合 |
| 工作内容摘要 | LLM 生成：「今天你和 AI 主要做了这几件事…」每件事 1-2 句 | LLM 摘要 `user_message` + `assistant_message` |
| 数字员工使用 | 每个数字员工的对话次数、典型问题 | 按 `subagent_id` 分组 |
| 工具使用 Top | 调用最多的工具（如「邮件发送 5 次」「客户查询 3 次」） | `execution_details.tool_executions` |
| 渠道分布 | Web / 企微 / 钉钉 各占多少 | `source_type` 分组 |
| 高光时刻 | 最有价值的一条对话（LLM 判断） | LLM 选 1 条 |
| 明日建议 | LLM 建议（如「明天可以试试 XX 数字员工处理 Y」） | LLM 生成 |

#### 4.3.2 节省时间估算（关键价值指标）

**估算公式**（参考 M365 Copilot Dashboard 的方法）：

```
节省时间 = Σ (每条对话的预估手工耗时 - 实际 AI 处理耗时)

其中：
- 预估手工耗时 = f(工具调用类型)
  - 邮件起草：8 分钟/封
  - 文档生成：15 分钟/篇
  - 客户查询：5 分钟/次
  - 数据导出：10 分钟/次
  - 通用问答：3 分钟/次
  - 其他：5 分钟/次
- 实际 AI 处理耗时 = duration_ms / 1000 / 60（分钟）
```

> 系数可在 `configs/config.yaml` 配置，便于按客户反馈调整。初期建议保守，避免数字虚高反噬可信度。

#### 4.3.3 展示形态

- **站内入口**：对话页面顶部「今日工作日报」卡片，点击进入详情页 `/t/{tenant_id}/daily-report`
- **日报中心**：历史日报列表，支持按日期/数字员工筛选
- **推送渠道**：
  - 每日 18:00 自动生成（定时任务）
  - 企微/钉钉/飞书应用消息推送（用户可开关）
  - 邮件推送（可选，附件 PDF）
  - 站内消息中心

### 4.4 团队日报（管理员/领导视角）

#### 4.4.1 内容结构

| 模块 | 内容 | 数据来源 |
|------|------|---------|
| 团队概览 | 活跃员工数 / 员工总数（活跃率）/ 总对话数 / 总节省时间 / 总消耗积分 | `chat_records` + `users` |
| 活跃度排行 | Top 5 活跃员工（对话数 + 节省时间） | 聚合 |
| 数字员工排行 | Top 3 数字员工（使用次数 + 满意度） | 按 `subagent_id` 聚合 |
| 团队工作成果 | LLM 生成：「今天团队主要完成了..」3-5 条要点 | LLM 摘要全员日报 |
| 渠道分布 | 各渠道对话占比、各渠道活跃用户 | `source_type` 聚合 |
| 异常提醒 | 长时间未使用的员工、错误率高的对话、积分消耗异常 | 多维度检测 |
| 续费建议 | 当前积分消耗速率、预计到期时间、建议充值额度 | `tenant_recharges` + 消耗趋势 |
| 高光案例 | 团队中最有价值的一条对话（脱敏） | LLM 选 1 条 |

#### 4.4.2 隐私边界

| 数据项 | 团队日报是否暴露 | 备注 |
|--------|----------------|------|
| 员工姓名 | ✅ | 管理员有权知道 |
| 对话次数 / 积分消耗 | ✅ | 已有 |
| 工作内容摘要（LLM 生成） | ✅ 脱敏后 | 隐去具体客户名、金额等敏感信息 |
| 对话原文 | ❌ | 不暴露，仅在管理员「下钻」时按权限查看（已有 RBAC） |
| 未使用员工列表 | ✅ | 仅显示「近 7 天未使用」，不显示具体内容 |

#### 4.4.3 展示形态

- **站内入口**：租户管理后台「日报中心」菜单
- **管理员日报页面**：`/t/{tenant_id}/admin/daily-report`
- **领导汇报模式**：一键导出 PDF/Word，包含图表 + 文字摘要，适合作为周会汇报材料
- **推送渠道**：
  - 每日 19:00 推送给租户管理员（企微/邮件）
  - 每周一 9:00 推送周报给租户管理员 + 平台管理员
  - 月度报告（每月 1 日）推送给租户管理员

### 4.5 关键交互设计

#### 4.5.1 日报生成时机

| 时机 | 触发方式 | 说明 |
|------|---------|------|
| 实时日报 | 用户主动点击「查看今日日报」 | 缓存 + 增量更新，避免重复生成 |
| 定时日报 | 每日 18:00 自动生成 | 定时任务（建议用 `arq` 异步队列，参考 [#38](../tech-stack-optimization/background-tasks-externalization.md)） |
| 次日补生成 | 次日 9:00 补全昨日漏掉的日报 | 处理跨天对话 |
| 手动重生 | 管理员/用户点击「重新生成」 | 强制刷新，扣少量积分 |

#### 4.5.2 日报数据模型（新增表）

```sql
-- 工作日报表（个人 + 团队共用，按 scope 区分）
CREATE TABLE IF NOT EXISTS work_daily_reports (
    id SERIAL PRIMARY KEY,
    report_id TEXT UNIQUE NOT NULL,           -- wdr_xxxxxxxx 格式
    tenant_id TEXT NOT NULL,
    scope TEXT NOT NULL,                       -- 'personal' / 'team'
    target_user_id TEXT,                       -- scope=personal 时必填，team 时为 NULL
    report_date DATE NOT NULL,                 -- 日报日期（按本地时区）

    -- 统计指标（JSON）
    metrics JSONB NOT NULL,                    -- 对话数/积分/节省时间/活跃员工数等

    -- LLM 生成内容
    summary_text TEXT,                         -- 工作内容摘要
    highlights JSONB,                          -- 高光时刻数组
    suggestions JSONB,                         -- 建议/提醒数组

    -- 元数据
    model TEXT,                                -- 生成所用模型
    token_cost INTEGER DEFAULT 0,              -- 生成消耗 token
    generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    regenerated_count INTEGER DEFAULT 0,       -- 重生次数

    UNIQUE(tenant_id, scope, target_user_id, report_date)
);

CREATE INDEX IF NOT EXISTS idx_work_daily_reports_tenant_date ON work_daily_reports(tenant_id, report_date DESC);
CREATE INDEX IF NOT EXISTS idx_work_daily_reports_user_date ON work_daily_reports(target_user_id, report_date DESC) WHERE scope = 'personal';
```

#### 4.5.3 推送配置

```sql
-- 日报推送配置表（每用户一行，UPSERT）
CREATE TABLE IF NOT EXISTS work_report_preferences (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    personal_report_enabled BOOLEAN DEFAULT TRUE,      -- 个人日报开关
    personal_push_channels TEXT[],                     -- ['in_app', 'wecom', 'email']
    personal_push_time TIME DEFAULT '18:00',           -- 推送时间
    team_report_enabled BOOLEAN DEFAULT FALSE,         -- 团队日报开关（仅管理员可见）
    team_push_channels TEXT[],
    team_push_time TIME DEFAULT '19:00',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(tenant_id, user_id)
);
```

### 4.6 LLM 摘要 Prompt 设计

#### 4.6.1 个人日报摘要 Prompt（核心）

```
你是数字员工的日报助手，请根据以下用户今天的对话记录，生成一份简洁的工作日报。

【用户信息】
- 姓名：{user_name}
- 部门：{department}
- 日期：{date}

【今日对话记录】（共 {n} 条）
{每条对话的 user_message（截断到 200 字）+ 关键工具调用}

【请按以下结构输出】
1. 工作内容摘要（3-5 条要点，每条 1-2 句，按工作主题归类，不要按对话顺序罗列）
2. 高光时刻（选 1 条最有价值的对话，说明价值点）
3. 明日建议（1-2 条，基于今日工作内容给出可执行建议）

【约束】
- 客户姓名、金额、内部系统名等敏感信息用「某客户」「某金额」替代
- 不编造未在对话中出现的内容
- 使用第一人称「你」称呼用户
- 总字数控制在 300-500 字
```

#### 4.6.2 团队日报摘要 Prompt

```
你是团队 AI 使用日报助手，请根据以下团队成员今日的个人日报，生成团队日报。

【团队信息】
- 租户：{tenant_name}
- 日期：{date}
- 团队规模：{total} 人，今日活跃：{active} 人

【成员个人日报摘要】
{每个活跃成员的 summary_text（已脱敏）}

【请按以下结构输出】
1. 团队工作成果（5-8 条要点，按业务主题归类，不要按员工罗列）
2. 协作亮点（如多人解决同类问题、跨数字员工协作等）
3. 改进建议（如「部分员工尚未使用 XX 数字员工，建议推广」）
4. 续费建议（基于使用密度，给出「保持/扩容/缩减」建议）

【约束】
- 不点名批评任何员工，对未使用员工用「部分成员」表达
- 总字数控制在 500-800 字
```

### 4.7 技术实现路径

#### 4.7.1 模块划分

```
src/reports/                              # 新增模块
├── __init__.py
├── aggregator.py                         # 数据聚合器（从 chat_records 聚合）
├── summarizer.py                         # LLM 摘要生成
├── time_saver.py                         # 节省时间估算
├── generator.py                          # 日报生成主流程
├── pusher.py                             # 多渠道推送
├── exporter.py                           # PDF/Word 导出
└── db.py                                 # work_daily_reports 表 CRUD

src/api/
└── work_reports.py                       # 新增 API 路由

src/scheduler/jobs/
└── daily_report_job.py                   # 定时任务（每日 18:00 / 19:00）

frontend/src/components/reports/
├── PersonalDailyReport.vue               # 个人日报页面
├── TeamDailyReport.vue                   # 团队日报页面
├── ReportCenter.vue                      # 日报中心（历史列表）
└── ReportPushSettings.vue                # 推送配置
```

#### 4.7.2 关键 API

```
GET  /api/reports/personal/today          # 获取今日个人日报（自动生成或返回缓存）
GET  /api/reports/personal/{date}         # 获取指定日期个人日报
POST /api/reports/personal/regenerate     # 重新生成（扣积分）
GET  /api/reports/personal/list           # 历史日报列表

GET  /api/reports/team/today              # 获取今日团队日报（管理员）
GET  /api/reports/team/{date}             # 获取指定日期团队日报
GET  /api/reports/team/export             # 导出 PDF/Word

GET  /api/reports/preferences             # 获取推送配置
PUT  /api/reports/preferences             # 更新推送配置

POST /api/reports/team/daily-push         # 手动触发推送（平台管理员代推）
```

#### 4.7.3 缓存策略

| 缓存对象 | 存储 | TTL | 失效时机 |
|---------|------|-----|---------|
| 日报正文 | `work_daily_reports` 表 | 永久（按日期查询） | 用户/管理员手动重生 |
| 当日聚合统计 | Redis `report:stats:{tenant}:{user}:{date}` | 10 分钟 | 当日有新对话时自动失效 |
| LLM 摘要结果 | `work_daily_reports.summary_text` | 永久 | 手动重生 |

#### 4.7.4 成本控制

- **模型选择**：摘要生成使用 `qwen-turbo` 或 `glm-4-flash`（成本低、速度快）
- **Token 控制**：每条对话 `user_message` 截断到 200 字，每日上限 50 条对话进入摘要
- **缓存复用**：同一日多次查询只生成一次
- **积分消耗**：日报生成不消耗用户积分（由平台承担），但「重新生成」扣 50 积分防止滥用

### 4.8 实施分阶段建议

| 阶段 | 内容 | 工作量 | 价值 |
|------|------|--------|------|
| **Phase 1** | 个人日报（站内查看 + 基础统计 + LLM 摘要） | 5-7 人天 | 让员工感受到 AI 陪伴，培养使用习惯 |
| **Phase 2** | 团队日报（管理员视角 + 数据看板 + LLM 摘要） | 5-7 人天 | 让管理员看到团队采纳度，推动内部推广 |
| **Phase 3** | 主动推送（企微/钉钉/飞书/邮件）+ 推送配置 | 4-5 人天 | 触达领导，不依赖管理员主动登录 |
| **Phase 4** | PDF/Word 导出 + 领导汇报模式 | 3-4 人天 | 让管理员能直接拿去汇报，扩大影响 |
| **Phase 5** | 节省时间量化 + 续费建议 + 月报/周报 | 4-5 人天 | 直接驱动续费决策 |

**MVP 建议**：Phase 1 + Phase 2 同步上线，是「双视角」的最小可用版本，能让员工和管理员都感受到价值。Phase 3-5 按优先级推进。

---

## 五、与其他方案的关联

### 5.1 与可观测性（[#1](../ideas.md)）的关系

可观测性关注**系统侧**质量（Trace、延迟、错误率），日报关注**用户侧**价值（工作成果、节省时间）。两者数据源都是 `chat_records` + `execution_details`，但聚合维度和呈现视角不同。**日报可以复用可观测性 Phase 1 已建设的 TraceCollector 数据**，无需重复埋点。

### 5.2 与租户计费（[#42](../ideas.md)）的关系

日报中的「积分消耗」「续费建议」直接复用 `tenant_recharges` + `chat_records.credit_cost`。续费建议模块是计费系统的自然延伸，把「快没钱了」从冷冰冰的告警变成「基于使用密度的智能建议」。

### 5.3 与多会话后台流式（[#43](../ideas.md)）的关系

用户在多会话切换时，日报入口应放在「全局」位置（如侧边栏底部），不随会话切换而消失。

### 5.4 与后台任务外置（[#38](../tech-stack-optimization/background-tasks-externalization.md)）的关系

日报定时生成任务（每日 18:00 / 19:00）是典型的「需要跨 Gunicorn worker 一致执行」的后台任务，建议与 #38 一起用 `arq` 实现，避免多 worker 重复生成。

---

## 六、风险与对策

| 风险 | 严重度 | 对策 |
|------|--------|------|
| LLM 摘要出现幻觉（编造未发生的对话） | 高 | Prompt 严格约束「不编造」+ 摘要后用规则校验关键事实（如对话数、工具调用次数）匹配 |
| 摘要泄露敏感信息（客户名、金额） | 高 | Prompt 脱敏 + 后处理正则替换 + 管理员下钻走 RBAC |
| 节省时间估算虚高反噬可信度 | 中 | 系数保守配置 + 显示「估算值」字样 + 允许租户自定义系数 |
| 日报推送打扰员工 | 中 | 默认关闭推送、用户可开关、可设时间窗 |
| 团队日报引发员工「被监控」焦虑 | 中 | 明确说明暴露的是工作内容摘要而非原文 + 团队日报默认只对管理员可见 |
| 跨天对话归错日期 | 低 | 按 `created_at` 归属，次日 9:00 补生成机制兜底 |
| 生成成本失控 | 低 | 用小模型 + 缓存 + 每日生成上限 |

---

## 七、后续展望

1. **AI 周报 / 月报**：在日报基础上聚合，更适合领导汇报节奏
2. **跨租户 benchmark**：在脱敏前提下，让租户看到自己 vs 同行业的 AI 采纳度分位
3. **员工 AI 等级**：根据使用密度和质量，给员工打 AI 使用等级（如「AI 探索者」「AI 达人」「AI 专家」），gamification 推动使用
4. **数字员工效能榜**：基于日报数据反向评估数字员工的价值，为优化数字员工提供数据支撑
5. **智能续约提醒**：基于使用密度趋势，提前 30 天给平台管理员推送「某租户续约风险高，建议主动联系」

---

## 八、结论

用户的「工作日报」想法切中 B2B SaaS 续费的核心痛点——**让付费方看到价值**。建议在用户原始想法（个人日报）基础上扩展为「双视角」方案：

- **个人日报**：让员工有陪伴感、培养使用习惯
- **团队日报**：让管理员/领导看到团队采纳度、量化 AI 价值、推动续费

数据基础已完备（`chat_records` + `execution_details`），无需新增埋点；技术路径清晰（聚合 + LLM 摘要 + 多渠道推送）；实施可分 5 个 Phase 推进，MVP（Phase 1 + 2）10-14 人天可上线。

**关键提醒**：日报不是孤立的「报表功能」，而是企业 AI 产品的「价值证明闭环」——把不可见的 AI 价值，变成领导看得见的工作成果，最终转化为续费意愿。建议作为 2026 Q3 的重点功能推进。
