# 竞品研究子智能体 — 深度研究报告

> 版本: v1.0 | 创建: 2026-05-06 | 状态: 待审核

## 一、研究背景

竞品分析（Competitive Intelligence, CI）是企业战略决策的核心环节。传统竞品分析依赖人工收集、整理、分析，耗时长、覆盖面窄、更新滞后。AI 驱动的竞品分析工具已成为市场热点，Crayon、Klue、Contify、Kompyte 等专业平台年收入已达数千万美元级别。

本报告基于对主流竞品分析工具、学术研究和行业实践的综合调研，明确竞品研究子智能体应具备的核心功能、数据来源、分析模型和输出格式，为后续开发提供设计依据。

---

## 二、市场现有工具调研

### 2.1 专业竞品分析平台

| 平台 | 定位 | 核心能力 | 数据来源 | 自动化程度 |
|------|------|----------|----------|-----------|
| **Crayon** | B2B 竞争情报平台 | AI 竞品变化检测与告警；自动生成销售话术和 Battle Card；扫描销售通话录音中的竞品提及 | 竞品网站、新闻、社交媒体、定价页、CRM 数据、通话录音 | 高 — 持续监控 + AI 内容生成 |
| **Klue** | 战斗卡与销售赋能 | 自动生成并持续更新的 Battle Card；竞品优劣势画像；CRM 集成；竞品问答 Chatbot | 竞品网站、新闻、社交媒体、招聘信息、定价页、赢单/输单数据 | 高 — 自动更新 Battle Card + 实时告警 |
| **Contify** | AI 原生市场情报 | 监控 20 万+ 信息源；行业情报自动化；自动生成情报简报和每日快讯；竞争格局映射 | 新闻、新闻稿、企业博客、社交媒体、行业出版物、网站、监管文件 | 极高 — 最广泛的自动化覆盖 |
| **Kompyte** | 销售导向竞品追踪 | 竞品网站变更实时检测；自动 Battle Card；销售团队告警；Chrome 浏览器插件 | 竞品网站（变更检测）、社交媒体、新闻、定价页 | 高 — 实时变更检测 |
| **AlphaSense** | 金融与公开文件深度搜索 | AI 驱动的财务文件搜索；财报电话会议分析；SEC 文件分析；专家网络访问 | SEC 文件、财报、投资者演示、分析师报告、专家访谈 | 中高 — 深度研究而非广度监控 |

### 2.2 数字营销/SEO 分析工具

| 平台 | 核心能力 |
|------|----------|
| **SEMrush** | 关键词差距分析、流量分析、广告支出追踪、外链分析、内容缺口识别 |
| **SimilarWeb** | 流量估算、受众画像、流量来源、互动指标、行业基准对比 |
| **Ahrefs** | 内容差距分析、关键词追踪、竞品外链画像、排名差距识别 |

### 2.3 新兴 AI 原生竞品分析 Agent

| 平台 | 核心能力 |
|------|----------|
| **Jitterbit Competitive Pricing Agent** | 实时竞品价格监控、利润优化建议、收入机会识别 |
| **Relevance AI CI Agent** | 监控竞品、分析市场定位、追踪定价、对比功能、生成销售情报报告 |
| **Beam.ai** | 从数据收集到决策分析的多步骤 Agent 工作流 |

### 2.4 国内市场

| 工具/平台 | 说明 |
|----------|------|
| **墨刀 AI Agent** | 面向产品经理的全生命周期 AI Agent，含竞品分析功能，可自动生成原型和 PRD |
| **腾讯云 AI 分析** | AI 驱动的竞品分析框架 |
| **飞书/钉钉集成方案** | 企业 AI Agent 平台支持多源数据集成 |

### 2.7 关键洞察

从上述工具中可以提炼出以下共性：

1. **数据采集自动化**是基础能力，所有平台都强调持续监控而非一次性分析
2. **Battle Card（战斗卡）** 是最普遍的输出格式，几乎每个平台都支持
3. **销售赋能**是竞品分析的主要应用场景，而非纯战略研究
4. **实时告警**是差异化功能，从季度报告转向实时监控
5. **AI 生成内容**（报告、分析、建议）正在替代人工分析师的重复工作

---

## 三、竞品分析工作流（行业标准）

根据 Contify 五步法和 Competitive Intelligence Alliance 的 CI 循环，标准竞品分析流程为：

```
Step 1: 定向 — 确定分析对象
  ├── 定义关键情报问题（KIQ）
  ├── 识别直接竞品（3-5 个核心竞品）
  ├── 识别间接竞品和新兴竞品
  └── 评估已有信息基础

Step 2: 数据采集 — 多源信息收集
  ├── 外部：网站、新闻、社交媒体、财报、招聘、专利、评价
  ├── 内部：CRM 赢单/输单数据、销售反馈、客户访谈
  └── 持续性：非一次性，需要持续监控

Step 3: 分析处理 — 原始数据转化为可行动情报
  ├── 应用分析框架（SWOT、PESTLE、波特五力等）
  ├── 识别模式、威胁和机会
  └── 回答核心问题：这说明了什么市场趋势？我们错过了什么机会？

Step 4: 报告输出 — 以利益相关者友好的格式交付
  ├── Battle Card、竞品画像、对比矩阵
  ├── 告警通知、情报简报
  └── 集成到工作流工具（企业微信、钉钉、飞书）

Step 5: 行动转化 — 情报驱动决策
  ├── 领导层：战略规划、业务方向
  ├── 市场部：营销优化、差异化定位
  ├── 销售部：Battle Card、异议处理、竞品定位
  └── 产品部：功能优先级、路线图调整

循环往复 → 评估效果 → 优化 KIQ → 改进自动化
```

---

## 四、功能模块设计

基于上述调研，竞品研究子智能体应具备以下功能模块：

### 4.1 功能全景图

```
竞品研究子智能体
│
├── 模块一：竞品管理
│     ├── 竞品列表维护（增删改查）
│     ├── 竞品分类（直接/间接/潜在/替代品）
│     ├── 竞品画像生成（公司概况、产品线、目标市场）
│     └── 竞品关系图谱
│
├── 模块二：信息采集
│     ├── 网站信息采集（官网、产品页、定价页、博客）
│     ├── 新闻与媒体报道监控
│     ├── 社交媒体动态追踪
│     ├── 用户评价与口碑分析（应用商店、评论平台）
│     ├── 招聘信息分析（推测战略方向）
│     └── 财务公开信息收集（上市公司）
│
├── 模块三：分析引擎
│     ├── SWOT 分析（自动生成）
│     ├── 产品功能对比矩阵
│     ├── 定价对比与基准分析
│     ├── 市场定位映射（2×2 矩阵）
│     ├── 用户评价情感分析
│     ├── 波特五力分析
│     └── 趋势检测与机会识别
│
├── 模块四：报告输出
│     ├── Battle Card（战斗卡）生成
│     ├── 竞品深度报告（Word/PDF）
│     ├── 对比分析表格（Excel）
│     ├── 竞品动态快讯（每日/每周摘要）
│     └── 定制化分析报告
│
└── 模块五：持续监控
      ├── 定价变更检测与告警
      ├── 产品更新追踪
      ├── 重大新闻推送
      └── 定期竞品情报简报
```

### 4.2 各模块功能详细说明

#### 模块一：竞品管理

| 功能 | 说明 | 用户交互方式 |
|------|------|-------------|
| 竞品列表维护 | 维护需关注的竞品列表，支持增删改查 | 对话："添加竞品 XXX"、"移除竞品 YYY" |
| 竞品分类 | 按竞争关系分类：直接竞品、间接竞品、潜在进入者、替代品 | 自动建议分类 + 用户确认 |
| 竞品画像 | 生成单个竞品的全景画像（公司概况、产品线、目标客户、差异化定位） | 对话："生成 XXX 的竞品画像" |
| 竞品关系图谱 | 展示竞品之间的关联关系（同母公司、合作伙伴、投资关系） | 前端可视化（未来扩展） |

#### 模块二：信息采集

| 功能 | 数据来源 | 采集方式 | 适用工具 |
|------|----------|----------|----------|
| 网站信息采集 | 竞品官网、产品页、定价页、博客 | Web Search + Browser Automation | WebSearchTool, BrowserAutomationTool |
| 新闻媒体报道 | 行业新闻、公关稿、分析师报告 | Web Search + RSS | WebSearchTool |
| 社交媒体动态 | 微信公众号、微博、LinkedIn、X/Twitter | Web Search | WebSearchTool |
| 用户评价口碑 | 应用商店、知乎、小红书、G2、Capterra | Web Search + Browser | WebSearchTool, BrowserAutomationTool |
| 招聘信息 | 招聘网站 JD 分析 | Web Search | WebSearchTool |
| 财务公开信息 | 上市公司年报、财报、招股书 | Web Search + 文件解析 | WebSearchTool, ExcelReaderTool |

**采集原则**：
- 优先使用项目已有的 `WebSearchTool` 和 `BrowserAutomationTool`
- 不做持续爬虫，采用"按需采集 + 定期刷新"模式
- 每次采集记录来源 URL 和采集时间，确保可溯源

#### 模块三：分析引擎

| 分析类型 | 说明 | 输出格式 |
|----------|------|----------|
| **SWOT 分析** | 基于采集数据自动生成竞品的 S/W/O/T 四维分析 | 结构化文本（表格） |
| **功能对比矩阵** | 逐功能点对比我方与竞品的产品能力 | 对比表格 |
| **定价对比** | 竞品定价策略对比、性价比分析 | 对比表格 + 柱状图描述 |
| **市场定位映射** | 在价格-功能/高端-性价比等维度上的二维定位图 | 2×2 矩阵描述 |
| **情感分析** | 用户评价的正负面情感分布、高频关键词 | 情感分布统计 |
| **波特五力** | 行业竞争态势分析 | 结构化文本 |
| **趋势分析** | 竞品近期动态趋势、战略方向推断 | 趋势摘要 |

**分析原则**：
- 所有分析必须基于采集到的实际数据，不编造信息
- 每条结论标注数据来源
- 不确定的结论明确标注置信度

#### 模块四：报告输出

| 报告类型 | 说明 | 输出格式 |
|----------|------|----------|
| **Battle Card（战斗卡）** | 单页竞品速览，供销售团队使用。包含：竞品概述、核心优劣势、我方差异化优势、常见异议应对话术 | 结构化卡片 |
| **竞品深度报告** | 全面深入的单竞品分析报告（10-15 页），涵盖公司背景、产品分析、市场策略、SWOT、建议 | Markdown / Word |
| **对比分析表** | 多竞品并排对比（功能、价格、口碑等维度） | 表格 / Excel |
| **竞品动态快讯** | 每日/每周竞品重要动态摘要 | 简短文本 |
| **定制化报告** | 用户指定分析维度和竞品范围的自定义报告 | 按需格式 |

#### 模块五：持续监控

| 监控类型 | 说明 | 触发方式 |
|----------|------|----------|
| 定价变更 | 竞品定价页面变化检测 | 定时任务 |
| 产品更新 | 新功能发布、版本更新 | 定时任务 |
| 重大新闻 | 融资、并购、高管变动、战略发布 | 定时任务 |
| 定期简报 | 每周自动生成竞品情报摘要 | 定时任务 |

---

## 五、技术架构设计

### 5.1 与现有系统集成

```
用户（企业微信/钉钉/飞书/Web）
    │
    ▼
主智能体 (Agent)
    │
    ├── delegate_to_subagent("competitor-research")
    │     │
    │     ▼
    │   竞品研究子智能体
    │     │
    │     ├── Skill: competitor-research（核心技能）
    │     │     ├── 竞品管理（CRUD + 画像生成）
    │     │     ├── 信息采集（多源数据收集）
    │     │     ├── 分析引擎（SWOT/对比/情感分析）
    │     │     ├── 报告生成（Battle Card/深度报告/快讯）
    │     │     └── 持续监控（定时任务 + 告警）
    │     │
    │     └── 使用的工具
    │           ├── WebSearchTool — 互联网搜索
    │           ├── BrowserAutomationTool — 网页内容采集
    │           ├── ExcelReaderTool — 数据文件读取
    │           ├── ContentGenerateTool — LLM 长文生成
    │           └── KnowledgeBaseTool — 行业知识检索
    │
    └── 定时任务调度
          ├── 每周竞品情报简报
          ├── 定价变更检测
          └── 重大新闻推送
```

### 5.2 数据存储设计

#### 5.2.1 竞品基础信息表

```sql
CREATE TABLE IF NOT EXISTS bs_competitor_research_competitors (
    id SERIAL PRIMARY KEY,
    competitor_id TEXT UNIQUE NOT NULL,     -- 竞品唯一标识
    tenant_id TEXT,                          -- 租户 ID
    user_id TEXT NOT NULL,                   -- 创建人
    name TEXT NOT NULL,                      -- 竞品名称
    company TEXT,                            -- 所属公司
    website TEXT,                            -- 官网 URL
    category TEXT DEFAULT 'direct',          -- 分类：direct/indirect/potential/substitute
    description TEXT,                        -- 竞品描述
    status TEXT DEFAULT 'active',            -- 状态：active/archived
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

#### 5.2.2 竞品情报记录表

```sql
CREATE TABLE IF NOT EXISTS bs_competitor_research_intel (
    id SERIAL PRIMARY KEY,
    intel_id TEXT UNIQUE NOT NULL,           -- 情报唯一标识
    tenant_id TEXT,
    competitor_id TEXT NOT NULL REFERENCES bs_competitor_research_competitors(competitor_id),
    intel_type TEXT NOT NULL,                -- 情报类型：news/pricing/product/hiring/financial/social/review
    title TEXT,                              -- 标题
    content TEXT,                            -- 内容
    source_url TEXT,                         -- 来源 URL
    source_name TEXT,                        -- 来源名称
    collected_at TIMESTAMP,                  -- 采集时间
    analysis_result TEXT,                    -- 分析结果（JSON）
    importance TEXT DEFAULT 'normal',        -- 重要程度：high/normal/low
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

#### 5.2.3 分析报告表

```sql
CREATE TABLE IF NOT EXISTS bs_competitor_research_reports (
    id SERIAL PRIMARY KEY,
    report_id TEXT UNIQUE NOT NULL,
    tenant_id TEXT,
    user_id TEXT NOT NULL,
    report_type TEXT NOT NULL,               -- 报告类型：battle_card/deep_dive/comparison/briefing/custom
    title TEXT NOT NULL,                     -- 报告标题
    content TEXT NOT NULL,                   -- 报告内容（Markdown）
    competitor_ids TEXT,                     -- 关联的竞品 ID（逗号分隔）
    status TEXT DEFAULT 'draft',             -- 状态：draft/published/archived
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 5.3 技能拆分建议

竞品研究作为一个 Skill 还是多个 Skill？

**推荐方案：单个 Skill（competitor-research）**

理由：
- 竞品管理的各个功能之间强关联（管理竞品 → 采集信息 → 分析 → 生成报告）
- 用户交互是统一的对话式，不需要在不同 Skill 之间切换
- 单个 Skill 的 SKILL.md 可以包含完整的系统提示词指导大模型完成全流程

如果未来功能膨胀，可拆分为：
- `competitor-manage`：竞品管理 + 信息采集
- `competitor-analysis`：深度分析 + 报告生成

---

## 六、用户交互设计

### 6.1 核心对话场景

| 场景 | 用户输入示例 | 智能体行为 |
|------|-------------|-----------|
| 添加竞品 | "帮我跟踪 XX 公司的产品" | 搜索 XX 公司信息 → 生成初始画像 → 存入竞品列表 |
| 竞品画像 | "生成 XX 的竞品画像" | 多源采集 → 生成全景画像 |
| SWOT 分析 | "分析 XX 公司的 SWOT" | 采集数据 → 生成 SWOT 分析 |
| 功能对比 | "对比我们和 XX 的产品功能" | 采集双方产品信息 → 生成对比矩阵 |
| 定价分析 | "XX 的定价策略是怎样的" | 采集定价信息 → 生成定价对比 |
| 生成 Battle Card | "给我一份 XX 的战斗卡" | 综合已有情报 → 生成 Battle Card |
| 竞品动态 | "XX 最近有什么新动态" | 搜索最新信息 → 生成动态快讯 |
| 深度报告 | "出一份 XX 的深度竞品报告" | 全流程执行 → 生成完整报告 |
| 监控告警 | "如果 XX 降价了通知我" | 设定监控规则 → 定时检测 → 推送告警 |

### 6.2 多轮对话示例

```
用户：帮我分析一下飞书这个竞品

智能体：好的，我来为你收集飞书的相关信息。
[调用 WebSearchTool 搜索飞书产品信息]
[调用 WebSearchTool 搜索飞书最新动态]
[调用 WebSearchTool 搜索飞书用户评价]

已完成信息收集，以下是飞书概览：
- 所属公司：字节跳动
- 定位：企业协作平台（IM + 文档 + 会议 + 项目管理）
- 目标客户：中大型企业，尤其是互联网/科技公司
- 核心功能：即时通讯、在线文档、视频会议、OKR、审批流程
- 定价模式：免费版 + 商业版（人均 360 元/年起）+ 旗舰版

你想要进一步了解哪方面？我可以：
1. 生成 SWOT 分析
2. 与钉钉/企业微信做功能对比
3. 生成 Battle Card
4. 分析用户口碑
5. 生成完整的深度报告

用户：和钉钉做个功能对比吧

智能体：好的，我来对比飞书和钉钉的核心功能...
[采集钉钉产品信息]
[生成功能对比矩阵]

以下是飞书 vs 钉钉的核心功能对比：

| 功能维度 | 飞书 | 钉钉 |
|---------|------|------|
| 即时通讯 | ★★★★★ 界面简洁，消息搜索强 | ★★★★ 功能完善，但界面较重 |
| 文档协作 | ★★★★★ 原生在线文档，体验流畅 | ★★★☆ 集成第三方，体验一般 |
| ... | ... | ... |

需要我导出为表格，或者进一步分析吗？
```

---

## 七、与其他子智能体的关系

### 7.1 与 CRM 子智能体的协同

| 协同场景 | 说明 |
|----------|------|
| 竞争态势与客户关联 | CRM 子智能体的客户流失分析中发现竞品因素时，可触发竞品研究子智能体进行针对性分析 |
| Battle Card 赋能销售 | 竞品研究生成的 Battle Card 可供 CRM 子智能体在客户跟进时引用 |
| 赢单/输单反馈 | CRM 的赢单/输单数据可为竞品分析提供内部视角 |

### 7.2 与知识库的关系

| 协同场景 | 说明 |
|----------|------|
| 行业知识支撑 | 知识库中的行业报告、分析文章可为竞品分析提供背景知识 |
| 分析结果沉淀 | 竞品分析报告可入库，作为后续对话的知识来源 |

---

## 八、实施优先级

### Phase 1：MVP（核心功能，2-3 周）

| 功能 | 说明 | 优先级 |
|------|------|--------|
| 竞品管理 | 添加/删除/查看竞品列表 | P0 |
| 竞品信息采集 | 基于搜索的多源信息收集 | P0 |
| SWOT 分析 | 自动生成单个竞品的 SWOT | P0 |
| 功能对比矩阵 | 两个产品的功能并排对比 | P0 |
| Battle Card 生成 | 单页竞品速览 | P0 |
| 竞品深度报告 | 完整的竞品分析报告 | P1 |

### Phase 2：增强功能（2 周）

| 功能 | 说明 | 优先级 |
|------|------|--------|
| 定价对比分析 | 竞品定价策略对比 | P1 |
| 用户评价情感分析 | 从评价中提取正负面情感 | P1 |
| 竞品动态快讯 | 最近动态摘要 | P1 |
| 市场定位映射 | 2×2 定位图分析 | P2 |
| 波特五力分析 | 行业竞争态势 | P2 |

### Phase 3：持续监控与自动化（2 周）

| 功能 | 说明 | 优先级 |
|------|------|--------|
| 定价变更检测 | 竞品定价页面变化检测 | P2 |
| 重大新闻告警 | 竞品重大事件推送 | P2 |
| 定期情报简报 | 每周自动生成竞品动态摘要 | P2 |
| 定制化报告模板 | 用户自定义报告格式 | P3 |

### Phase 4：高级功能（按需）

| 功能 | 说明 | 优先级 |
|------|------|--------|
| 招聘信息分析 | 从 JD 推测竞品战略方向 | P3 |
| 财务数据分析 | 上市公司财报分析 | P3 |
| 趋势预测 | 基于历史数据预测竞品动向 | P3 |
| 竞品关系图谱 | 展示竞品之间的投资/合作/竞争关系 | P3 |

---

## 九、设计原则

1. **数据驱动**：所有分析结论必须基于实际采集到的数据，不编造、不推测无依据的结论
2. **可溯源**：每条情报和分析结论都标注来源 URL 和采集时间
3. **渐进增强**：先实现核心分析能力（信息采集 + 报告生成），再扩展监控和自动化
4. **租户隔离**：竞品列表、情报数据、分析报告严格按 tenant_id 隔离
5. **复用现有工具**：优先使用项目已有的 WebSearchTool、BrowserAutomationTool、ContentGenerateTool 等
6. **对话式交互**：所有功能通过自然语言对话触发，不需要学习特殊命令
7. **结构化输出**：分析结果以表格、矩阵等结构化格式呈现，便于阅读和导出

---

## 十、风险与注意事项

| 风险 | 说明 | 缓解措施 |
|------|------|----------|
| 数据准确性 | 互联网信息可能过时或不准确 | 标注信息来源和时间，对关键数据交叉验证 |
| 采集频率 | 过于频繁的搜索可能被搜索引擎限制 | 采用合理间隔，优先利用已有情报 |
| 分析深度 | LLM 分析可能停留在表面 | 通过多轮采集 + 结构化分析框架提升深度 |
| 数据量 | 竞品情报数据可能快速增长 | 设置数据保留策略，定期归档旧数据 |
| 租户差异 | 不同行业的竞品分析需求差异大 | 保持通用框架，通过 prompt 引导适应不同场景 |

---

## 参考来源

- [Crayon — 竞争情报平台](https://www.crayon.co/)
- [Crayon 2025 竞争情报现状报告](https://www.crayon.co/state-of-competitive-intelligence)
- [Klue — 如何用 AI 做竞品分析](https://klue.com/blog/how-to-do-competitive-analysis-with-ai)
- [Contify — 五步构建竞争情报流程](https://www.contify.com/resources/blog/competitive-intelligence-process/)
- [Competitive Intelligence Alliance — CI 循环](https://www.competitiveintelligencealliance.io/the-competitive-intelligence-cycle/)
- [Contify — 最佳竞争情报工具](https://www.contify.com/resources/blog/best-competitive-intelligence-tools/)
- [SuperAGI — AI 竞品分析工具对比](https://web.superagi.com/comparing-the-best-ai-competitor-analysis-tools-ahrefs-search-atlas-and-crayon-in-2025/)
- [Noimosai — 2026 五大竞品分析自主 AI Agent](https://noimosai.com/en/blog/5-best-autonomous-ai-agents-for-competitor-analysis-in-2026-automate-your-market-intelligence)
- [Autobound — 15 大竞争情报工具 2026](https://www.autobound.ai/blog/top-15-competitive-intelligence-tools-2026)
- [Jitterbit — 竞品定价 AI Agent](https://www.jitterbit.com/ai/jitterbit-competitive-pricing-agent/)
- [ArXiv — Agent 设计模式目录](https://arxiv.org/html/2405.10467v4)
