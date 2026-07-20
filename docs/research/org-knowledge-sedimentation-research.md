# 从个人经验到组织能力：AI 智能体组织知识沉淀调研与方案设计

> 关联文档：
> - [ideas.md §调研报告索引](../ideas.md)
> - [AI 智能体行业产品体验提升调研与「工作日报」方案设计](./ai-agent-experience-daily-report-research.md)
> - [企业级智能体平台调研](./enterprise-agent-platform-research.md)
> - [企业知识库 RAG 技术调研](./enterprise-knowledge-base-rag-research.md)
> - [会话内上下文压缩业界方案调研](./context_compression_research.md)
> - [Prompt 版本管理调研](./prompt-version-management-research.md)
>
> 文档索引：[ideas.md §调研报告索引](../ideas.md)
> 创建日期：2026-07-20
> 定位：调研行业领先产品如何把「个人使用经验」沉淀为「组织能力」，并给出可落地的方案设计。
> 适用范围：本调研基于对行业产品的已有公开认知，涉及具体功能边界时，已在文中标注「以官方文档为准」。

---

## 一、调研目的

### 1.1 问题陈述

工作日报解决的是「让企业领导感知 AI 价值」，但**单个员工用得越好，企业其他员工并不会自动受益**。典型表现：

- 员工 A 通过反复试错，总结出了一套「询问外贸客户背景」的高效 Prompt，但员工 B 不知道
- 员工 A 发现某个数字员工 + 某个工具组合可以处理复杂订单问题，但员工 B 重新踩坑
- 员工 A 整理了一份「常见客户拒绝话术应对」清单，但只存在于他的对话历史里
- 租户管理员换一个员工来用，所有经验归零，需要重新摸索

**核心痛点**：当前系统的知识、经验、能力都**停留在个人层**，没有自动沉淀到**租户/组织层**。

### 1.2 调研目标

调研行业领先产品如何把个人使用经验自动沉淀为组织能力，回答以下问题：

1. 沉淀什么？（知识类型与结构）
2. 怎么沉淀？（触发机制、抽取算法）
3. 怎么保证质量？（去重、审核、置信度）
4. 怎么应用回用户？（注入时机、推荐机制）
5. 隐私边界怎么把握？（脱敏、权限、可见性）

### 1.3 调研对象

| 产品 | 类型 | 调研重点 |
|------|------|---------|
| Glean | 企业搜索 + AI Assistant | Personal Graph vs Enterprise Graph，权限感知 |
| Microsoft 365 Copilot | 办公套件 AI | Semantic Index、Personal Profile、组织信号融合 |
| Moveworks | 员工体验 AI Agent | Topic Cluster、知识自动学习、专家识别 |
| Dust.tt | 企业 Assistant 平台 | @assistant 命名空间、Shared Instructions、Workspace 知识 |
| ChatGPT Enterprise / Team | 通用对话 AI | Workspace Memory、Custom GPTs、管理员审核 |
| Claude Projects | 通用对话 AI | Project Knowledge、Custom Instructions |
| Notion AI / Confluence RAG | 协作工具内置 RAG | Workspace 知识、AI Q&A |
| Cursor / GitHub Copilot | 研发 AI | Rules 文件、Org Custom Instructions、Team Convention |
| Guru / Slite | 知识管理 SaaS | Knowledge Triggers、AI Verification、专家驱动 |
| LangChain / LlamaIndex | 开源框架 | Memory 模块、Knowledge Graph 抽取 |

---

## 二、行业产品功能对照

### 2.1 「组织知识沉淀」机制矩阵

| 能力 | Glean | M365 Copilot | Moveworks | Dust.tt | ChatGPT Enterprise | Cursor Rules | Guru | Notion AI | **本项目** |
|------|-------|--------------|-----------|---------|---------------------|--------------|------|-----------|----------|
| 个人记忆 | ✅ Personal Graph | ✅ Personal Profile | ⚠️ | ✅ per-user | ✅ Memory | ⚠️ per-project | ❌ | ⚠️ | ❌ |
| 组织记忆 | ✅ Enterprise Graph | ✅ Semantic Index | ✅ Knowledge Cloud | ✅ Workspace | ✅ Workspace Memory | ✅ .cursor/rules | ✅ Cards | ✅ Workspace | ⚠️ 仅知识库（人工上传） |
| 自动抽取 | ✅ 信号融合 | ✅ Graph 信号 | ✅ Topic Cluster | ⚠️ | ⚠️ Memory 自动 | ❌ 人工写 | ⚠️ AI 提议 | ❌ | ❌ |
| 专家识别 | ✅ 权威信号 | ✅ Organizational Signals | ✅ Expert Routing | ❌ | ❌ | ❌ | ✅ Author | ❌ | ❌ |
| 知识触发 | ✅ Contextual | ⚠️ | ✅ Proactive | ⚠️ | ❌ | ✅ File-glob | ✅ Triggers | ⚠️ | ❌ |
| 自动失效 | ✅ Reindex | ⚠️ | ✅ Auto-Sync | ❌ | ❌ | ❌ | ✅ Verification | ❌ | ❌ |
| 人工审核 | ❌ 自动 | ❌ 自动 | ⚠️ | ✅ Admin | ✅ Admin | ✅ Git PR | ✅ Author | ✅ Editor | ✅ 知识库编辑 |
| 跨用户共享 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ⚠️ 仅上传后共享 |

> 注：⚠️ 表示部分支持或需手动触发；以各产品当前官方文档为准。

### 2.2 行业关键模式

#### 模式 1：双层 Graph（Glean / M365 Copilot）

**做法**：构建「个人 Graph」+「组织 Graph」两层，个人 Graph 描述「这个人是谁、关注什么、最近做什么」，组织 Graph 描述「公司有哪些人、文档、项目、流程」。检索时**双层融合**，确保个人上下文不被组织上下文淹没。

**对项目的启示**：组织知识沉淀不能只做「全局 RAG」，否则用户个性化的需求会被淹没。本项目应保留 `chat_context_summaries`（个人中期记忆）作为个人层，新增组织知识作为第二层。

#### 模式 2：Topic Cluster + 知识自动学习（Moveworks）

**做法**：自动聚类员工对话，识别高频 Topic，从未解决对话中生成「待补充知识」，从已解决对话中（特别是专家员工的回答）抽取「新知识条目」，定期同步到知识库。

**关键机制**：
- **专家识别**：基于员工在某个 Topic 上的成功对话数、回答质量评分
- **置信度阈值**：只有达到阈值的候选知识才会入库
- **管理员审核**：高价值知识经管理员审核后正式发布

**对项目的启示**：本项目有 `chat_records.execution_details`（含工具调用成功率）+ `subagent_id`（数字员工维度），完全可以识别「某员工在某数字员工上的成功模式」。

#### 模式 3：Shared Instructions + 命名空间（Dust.tt / Claude Projects）

**做法**：Workspace / Project 级的「共享指令」是显式的、可版本化的、可审核的。用户个人的偏好仍可叠加，但**组织指令优先级更高**。

**对项目的启示**：组织知识沉淀不应全部走「隐式 RAG」，应该有显式的「租户指令」层（类似 Cursor 的 `.cursor/rules`），由管理员审核发布，作为 system prompt 注入。

#### 模式 4：Knowledge Triggers + AI Verification（Guru / Slite）

**做法**：知识不是被动等待检索，而是**主动触发**。当用户在某个上下文（如打开某个客户详情、写某类邮件）时，相关卡片自动浮出。同时 AI 定期验证知识是否过期。

**对项目的启示**：组织知识应用不应只走「问答时 RAG 检索」，应该在用户开始对话时主动提示「你的同事在类似问题上是这样解决的，要不要参考？」。

#### 模式 5：Rules 文件 + Git 协作（Cursor）

**做法**：团队规则以 Markdown 文件形式存于项目仓库，通过 Git PR 协作维护。规则按文件路径/语言自动激活。

**对项目的启示**：组织知识中「显式规则」部分，可借鉴 Git 协作模式，让租户管理员可以像维护代码一样维护组织规则（版本化、可回滚、可 diff）。本项目已有 `prompt_registry` 表（Prompt 版本管理），可扩展承载组织规则。

---

## 三、现状分析与差距诊断

### 3.1 本项目数据基础（已具备）

| 数据源 | 表 | 用途 |
|--------|-----|------|
| 完整对话内容 | `chat_records.user_message` / `assistant_message` | LLM 抽取的输入 |
| 执行详情 | `chat_records.execution_details`（JSON，含工具调用、子智能体调用、迭代次数、计划步骤） | 识别「成功路径」 |
| 中期记忆 | `chat_context_summaries`（已实现压缩摘要） | 复用为「个人层记忆」 |
| 知识库 | `documents` / `chunks` / `chunks_vec`（含租户隔离） | 组织知识的承载（复用） |
| 子智能体知识源 | `subagent_knowledge_sources`（JSONB） | 已支持给数字员工配置知识源 |
| Prompt 版本管理 | `prompt_registry` / `prompt_versions` | 组织指令的承载（复用） |
| Skill 系统 | `src/skills/<name>-<version>/`（含 `SKILL.md`） | 沉淀的操作流程 |
| Subagent 系统 | `subagents/<name>/SUBAGENT.md` | 数字员工定义 |

**结论**：组织知识沉淀**完全可复用现有基础设施**，无需重新造轮子。

### 3.2 关键差距

| 维度 | 现状 | 差距 |
|------|------|------|
| 知识来源 | 知识库文档全部由人工上传 | 缺少从对话历史自动抽取的管线 |
| 知识层次 | 只有租户级（人工上传） | 缺少个人层、团队层、组织层的区分 |
| 知识应用 | 数字员工启动时 RAG 检索 | 缺少主动触发、上下文感知推荐 |
| 专家识别 | ❌ | 无法识别「谁在某个领域是专家」 |
| 质量保障 | 人工编辑文档 | 缺少置信度、自动失效、A/B 测试 |
| 知识类型 | 文档（非结构化） | 缺少结构化的 SOP / FAQ / 提示词模板 / 术语表 |

---

## 四、组织知识沉淀方案设计

### 4.1 设计目标与原则

**目标**：让少数典型用户的密集使用经验，自动沉淀为整个租户的能力，换用户来用也能受益。

**原则**：

1. **三层知识架构**：个人层（短期）-> 团队层（中期）-> 组织层（长期），层次分明、可见性递增、审核强度递增
2. **抽取与应用分离**：抽取管线夜间离线跑（不阻塞主链路），应用走实时 RAG + Prompt 注入
3. **结构化优于非结构化**：抽取出的知识尽量结构化（SOP / FAQ / 提示词模板 / 术语表），而非长文档
4. **显式规则优先于隐式 RAG**：组织级规则、品牌规范、合规要求走显式 Prompt 注入，不走 RAG（确保确定性）
5. **质量优先于数量**：宁可少入库，不可入库低质量知识（避免污染 RAG）
6. **隐私可控**：脱敏入库、原文不外泄、专家识别只暴露「领域权威」不暴露具体对话

### 4.2 三层知识架构

```
┌──────────────────────────────────────────────────────────────┐
│                   组织知识（L3 - 租户级）                       │
│   显式规则 + 品牌/合规 + 跨数字员工共享 SOP + 术语表            │
│   可见性：全员可用｜审核：管理员审核｜TTL：永久（带版本）       │
└──────────────────────────────────────────────────────────────┘
                              ▲ 沉淀（专家识别 + 管理员审核）
┌──────────────────────────────────────────────────────────────┐
│                   团队知识（L2 - 数字员工级）                  │
│   该数字员工的高频 FAQ + 操作技巧 + 典型案例 + 失败案例         │
│   可见性：租户内全员（使用该数字员工时）｜审核：自动 + 抽检     │
└──────────────────────────────────────────────────────────────┘
                              ▲ 沉淀（自动抽取 + 置信度过滤）
┌──────────────────────────────────────────────────────────────┐
│                   个人知识（L1 - 用户级）                      │
│   个人偏好 + 个人历史 + 个人上下文摘要                          │
│   可见性：仅本人｜审核：无（自动）｜TTL：30 天滚动             │
└──────────────────────────────────────────────────────────────┘
                              ▲ 沉淀（已有 chat_context_summaries）
```

### 4.3 知识类型与结构

组织知识按**类型**而非「文档」组织，便于检索、应用、失效。

| 知识类型 | 结构 | 应用方式 | 示例 |
|---------|------|---------|------|
| **组织规则** | Markdown 文本 + 触发条件 | 显式 Prompt 注入（system prompt） | 「客户名出现的文档必须脱敏」「金额超过 10w 需走审批」 |
| **术语表** | 术语 -> 解释 + 别名 | RAG 检索 + 实体识别替换 | 「EAS = 企业应用系统」「SO = 销售订单」 |
| **SOP** | 标题 + 步骤数组 + 触发条件 + 适用数字员工 | Skill 化（推荐使用）或 RAG 检索 | 「处理客户投诉的 5 步流程」 |
| **FAQ** | 问题 + 最佳答案 + 关联数字员工 + 置信度 | RAG 检索 + 主动推荐 | 「如何查询订单物流状态？」 |
| **提示词模板** | 模板 + 变量 + 使用场景 + 效果评分 | 数字员工启动时推荐 / 用户主动调用 | 「外贸客户背景调研模板」 |
| **专家案例** | 对话摘要 + 关键决策点 + 结果 | RAG 检索（脱敏） | 「某员工处理某类客诉的完整对话」 |
| **失败案例** | 错误模式 + 纠正方法 + 触发条件 | 检测到类似模式时主动提醒 | 「不要直接给客户报底价」 |

### 4.4 沉淀管线设计（核心）

```
┌─────────────────────────────────────────────────────────────┐
│  数据源：chat_records + execution_details + summaries        │
│  过滤：近 N 天 + 租户内 + status=completed + 有 assistant_msg │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
        ┌────────────────────────────────────────┐
        │  Step 1: 候选识别（规则 + Embedding）   │
        │  - 高频问题聚类（同 subagent 下相似问题）│
        │  - 成功对话识别（工具调用成功 + 迭代少）  │
        │  - 失败对话识别（多次重试 / 用户否定）   │
        │  - 专家识别（某用户在某 subagent 高产出）│
        └────────────────────────────────────────┘
                            │
                            ▼
        ┌────────────────────────────────────────┐
        │  Step 2: LLM 结构化抽取                 │
        │  - 抽取 FAQ / SOP / 提示词模板 / 术语   │
        │  - 抽取失败模式                         │
        │  - 脱敏（人名/客户名/金额）             │
        │  - 标注置信度（0-1）                    │
        └────────────────────────────────────────┘
                            │
                            ▼
        ┌────────────────────────────────────────┐
        │  Step 3: 去重与合并                     │
        │  - Embedding 相似度（>0.92 视为重复）   │
        │  - 与现有知识合并（频次累加 + 答案择优）│
        │  - 跨用户去重（同一 FAQ 多人提出）      │
        └────────────────────────────────────────┘
                            │
                            ▼
        ┌────────────────────────────────────────┐
        │  Step 4: 质量评分                       │
        │  - 使用频次（30 天）                    │
        │  - 成功率（来源对话的成功率）           │
        │  - 用户反馈（如有）                     │
        │  - 专家权重（来源用户是否专家）         │
        │  - 综合分 < 阈值则丢弃                  │
        └────────────────────────────────────────┘
                            │
                            ▼
        ┌────────────────────────────────────────┐
        │  Step 5: 入库（按类型分流）             │
        │  - L3 规则 -> 待管理员审核              │
        │  - L3 术语表 -> 待管理员审核            │
        │  - L2 FAQ/SOP/案例 -> 自动入库（高置信）│
        │  - L1 个人偏好 -> 自动入库              │
        └────────────────────────────────────────┘
                            │
                            ▼
        ┌────────────────────────────────────────┐
        │  Step 6: 应用（实时）                   │
        │  - RAG 检索（对话发起时）              │
        │  - Prompt 注入（system prompt 拼装）    │
        │  - 主动推荐（识别到类似上下文）         │
        │  - Skill 推荐（高频操作流程化）         │
        └────────────────────────────────────────┘
```

### 4.5 专家识别机制

**专家识别**是组织知识沉淀的关键--只从「专家员工」的成功对话中抽取，避免被低质量对话污染。

**专家评分公式**（基于 30 天滚动窗口）：

```
专家分 = 0.4 × 标准化对话数
       + 0.3 × 标准化成功率
       + 0.2 × 标准化效率（迭代次数倒数）
       + 0.1 × 标准化工具调用成功率

其中：
- 标准化 = (用户值 - 全员最小) / (全员最大 - 全员最小)
- 成功率 = status=completed 的对话数 / 总对话数
- 效率 = 1 / avg(agent_iterations)
- 工具调用成功率 = success tool_executions / total tool_executions
```

**专家阈值**：专家分 > 0.7 视为该 subagent 的专家。

**应用**：
- 抽取管线只从专家对话中抽取 L3 知识（L2 可放宽）
- 团队日报「Top 3 活跃员工」可标注「领域专家」徽章
- 用户提问时，若识别到该领域专家，可建议「咨询 @某某」

### 4.6 应用机制

#### 4.6.1 被动应用（RAG 检索，对话发起时）

```
用户提问 "如何查询订单 EAS-12345 的物流"
    │
    ▼
1. 实体识别：抽取 "订单查询" / "物流状态" / "EAS-12345"
    │
    ▼
2. 知识检索（三层并行）：
   - L1 个人：检索该用户历史摘要
   - L2 团队：检索该 subagent 的 FAQ / SOP
   - L3 组织：检索术语表 / 规则
    │
    ▼
3. 优先级排序：
   - L3 规则 > L2 SOP > L2 FAQ > L1 个人 > L3 术语
    │
    ▼
4. 注入 system prompt + RAG context
```

#### 4.6.2 主动推荐（识别到类似上下文）

```
场景：员工 B 提问 "外贸客户背景调研怎么做"
    │
    ▼
1. 检索 L2 SOP / 专家案例
    │
    ▼
2. 命中「员工 A 高频使用的客户调研模板」
    │
    ▼
3. 主动推荐（不强制）：
   "💡 你的同事在类似问题上常用以下模板，要不要参考？
    [客户背景调研模板 v3] - 由 @张三 验证，使用 12 次，成功率 92%"
    │
    ▼
4. 用户点击 -> 自动注入到对话上下文
```

#### 4.6.3 Skill 化沉淀（高频操作流程化）

当某操作模式被识别为高频（如「客户背景调研」被 5+ 用户、20+ 次执行），系统**自动生成 Skill 草案**：

```
src/skills/auto/customer-background-research-1.0.0/
└── SKILL.md   # 由 LLM 生成草案，管理员审核后生效
```

草案进入 `subagent_definitions.skills.allowed` 白名单前需管理员审核。

#### 4.6.4 显式规则注入（确定性优先）

组织级规则（合规、品牌、安全）走**显式 Prompt 注入**，不走 RAG：

```
# 数字员工 system prompt 拼装顺序
1. 基础 system prompt（来自 SUBAGENT.md）
2. + 组织规则（来自 prompt_registry，租户管理员审核）
3. + 数字员工知识源 RAG 检索结果
4. + 用户个人上下文摘要
5. + 对话历史
```

### 4.7 数据模型（新增表）

```sql
-- 组织知识条目表（统一承载 L2/L3 结构化知识）
CREATE TABLE IF NOT EXISTS org_knowledge_entries (
    id SERIAL PRIMARY KEY,
    entry_id TEXT UNIQUE NOT NULL,           -- okn_xxxxxxxx 格式
    tenant_id TEXT NOT NULL,
    layer TEXT NOT NULL,                     -- 'team' / 'org'（L2/L3）
    knowledge_type TEXT NOT NULL,            -- 'rule' / 'term' / 'sop' / 'faq' / 'prompt_template' / 'expert_case' / 'failure_case'
    subagent_id TEXT,                        -- L2 时必填，L3 时为 NULL（跨数字员工）

    -- 内容
    title TEXT NOT NULL,
    content JSONB NOT NULL,                  -- 按 knowledge_type 不同结构
    summary TEXT,                            -- 一句话摘要（用于检索展示）

    -- 来源
    source_user_ids TEXT[],                  -- 抽取自哪些用户（脱敏后只存 ID）
    source_record_ids BIGINT[],              -- 抽取自哪些 chat_records（可追溯）
    expert_source BOOLEAN DEFAULT FALSE,     -- 是否来自专家对话

    -- 质量与状态
    confidence NUMERIC(3,2) DEFAULT 0.0,     -- 0.00-1.00，抽取时打分
    usage_count_30d INTEGER DEFAULT 0,       -- 近 30 天使用次数
    success_count_30d INTEGER DEFAULT 0,     -- 近 30 天应用后成功次数
    status TEXT DEFAULT 'pending',           -- 'pending' / 'published' / 'rejected' / 'archived'
    rejected_reason TEXT,

    -- 向量（用于语义检索）
    embedding_model TEXT,
    embedding vector(1024),                  -- 复用 pgvector

    -- 元数据
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    published_at TIMESTAMP,
    expires_at TIMESTAMP,                    -- 自动失效时间（NULL = 永久）

    UNIQUE(tenant_id, layer, knowledge_type, title)
);

CREATE INDEX IF NOT EXISTS idx_org_knowledge_tenant_layer ON org_knowledge_entries(tenant_id, layer, status);
CREATE INDEX IF NOT EXISTS idx_org_knowledge_subagent ON org_knowledge_entries(subagent_id, status) WHERE layer = 'team';
CREATE INDEX IF NOT EXISTS idx_org_knowledge_type ON org_knowledge_entries(tenant_id, knowledge_type, status);

-- 组织知识向量索引
CREATE INDEX IF NOT EXISTS idx_org_knowledge_embedding ON org_knowledge_entries
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- 专家识别表（按 subagent 维度）
CREATE TABLE IF NOT EXISTS org_expert_profiles (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    subagent_id TEXT NOT NULL,               -- NULL 表示综合专家
    expert_score NUMERIC(4,3) DEFAULT 0.0,   -- 0.000-1.000
    conversation_count_30d INTEGER DEFAULT 0,
    success_rate NUMERIC(3,2) DEFAULT 0.0,
    avg_iterations NUMERIC(3,1) DEFAULT 0.0,
    tool_success_rate NUMERIC(3,2) DEFAULT 0.0,
    is_expert BOOLEAN DEFAULT FALSE,
    computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(tenant_id, user_id, subagent_id)
);

-- 知识应用日志（用于质量评估和 A/B）
CREATE TABLE IF NOT EXISTS org_knowledge_applications (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    entry_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    record_id TEXT,                          -- 关联 chat_records
    applied_via TEXT NOT NULL,               -- 'rag' / 'prompt_injection' / 'proactive_recommend' / 'skill'
    user_accepted BOOLEAN,                   -- 主动推荐时是否接受
    outcome TEXT,                            -- 'success' / 'failure' / 'neutral' / NULL（未评估）
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_org_knowledge_apps_entry ON org_knowledge_applications(entry_id, applied_at DESC);
CREATE INDEX IF NOT EXISTS idx_org_knowledge_apps_user ON org_knowledge_applications(tenant_id, user_id, applied_at DESC);
```

### 4.8 LLM 抽取 Prompt 设计

#### 4.8.1 FAQ 抽取 Prompt

```
请从以下对话记录中抽取「高频问题 + 最佳答案」类型的 FAQ。

【对话记录】（共 {n} 条，来自专家员工 {expert_name}）
{每条对话的 user_message + assistant_message（截断到 500 字）}

【抽取规则】
1. 只抽取「问题清晰、答案明确、可复用」的对话
2. 问题用一句话表达，答案用 2-3 句话总结（不要照抄原文）
3. 脱敏：客户姓名 -> 某客户；金额 -> 某金额；内部系统名保留
4. 标注置信度（0-1）：答案完整度高、问题表述清晰 -> 高；模糊或带猜测 -> 低

【输出 JSON 数组】
[
  {
    "question": "...",
    "answer": "...",
    "subagent_id": "...",
    "confidence": 0.85,
    "source_record_ids": [123, 456]
  }
]

【约束】
- 不抽取只对特定客户/特定订单有效的对话
- 不抽取一次性的临时问题
- 至少 2 条相似对话才抽取为 FAQ
```

#### 4.8.2 SOP 抽取 Prompt

```
请从以下对话记录中抽取「标准操作流程」类型的 SOP。

【对话记录】
{...}

【抽取规则】
1. 识别「多步骤操作」对话（如：查询订单 -> 通知客户 -> 记录备注）
2. 步骤用动词开头（"查询" / "通知" / "记录"）
3. 标注触发条件（"当 ... 时"）
4. 标注适用数字员工

【输出 JSON 数组】
[
  {
    "title": "...",
    "trigger": "当客户询问订单物流状态时",
    "steps": ["查询 EAS 订单状态", "调用物流查询工具", "通知客户预计到达时间", "在客户档案记录本次沟通"],
    "applicable_subagents": ["trade-specialist"],
    "confidence": 0.9,
    "source_record_ids": [...]
  }
]
```

### 4.9 质量保障机制

| 机制 | 说明 |
|------|------|
| **置信度阈值** | LLM 抽取时打分 < 0.7 直接丢弃 |
| **频次阈值** | 单条知识需 2+ 用户或 5+ 次对话支撑 |
| **专家权重** | 来自专家对话的知识，置信度 × 1.2 |
| **去重** | Embedding 相似度 > 0.92 视为重复，合并而非新增 |
| **管理员审核** | L3 知识（规则、术语）必须管理员审核 |
| **抽检机制** | L2 知识自动入库，但每天抽样 5% 给管理员审核 |
| **自动失效** | 30 天未应用 + 30 天未提及 -> 标记 stale；60 天 -> archived |
| **A/B 测试** | 高置信度新知识可灰度应用（10% 用户），观察成功率提升 |
| **用户反馈** | 应用知识后用户可点「有用 / 无用」，反馈计入评分 |

### 4.10 隐私边界

| 数据项 | L1 个人 | L2 团队 | L3 组织 |
|--------|---------|---------|---------|
| 原始对话 | ✅ 仅本人 | ❌ 不暴露 | ❌ 不暴露 |
| 工作摘要 | ✅ | ✅ 脱敏后 | ✅ 脱敏后 |
| 客户姓名/金额 | ✅ | ❌ 必脱敏 | ❌ 必脱敏 |
| 专家身份 | ❌ | ✅ 可暴露 | ✅ 可暴露 |
| 使用频次 | ✅ | ✅ 聚合后 | ✅ 聚合后 |
| 失败案例 | ✅ | ✅ 脱敏后 | ✅ 脱敏后 |

**关键约束**：
- 抽取管线在写入 `org_knowledge_entries` 前，**必须**经过脱敏正则 + LLM 复核两道关卡
- 跨租户绝对隔离：`tenant_id` 是硬约束，所有查询必带
- 管理员可下钻查看某条知识的来源对话（已脱敏），但不能看原文（需用户授权）

### 4.11 技术实现路径

#### 4.11.1 模块划分

```
src/org_knowledge/                           # 新增模块
├── __init__.py
├── extractor/                               # 抽取管线
│   ├── candidate_detector.py                # 候选识别（聚类 + 专家识别）
│   ├── faq_extractor.py                     # FAQ 抽取
│   ├── sop_extractor.py                     # SOP 抽取
│   ├── term_extractor.py                    # 术语抽取
│   ├── prompt_template_extractor.py         # 提示词模板抽取
│   └── failure_case_extractor.py            # 失败案例抽取
├── quality/                                 # 质量保障
│   ├── dedup.py                             # 去重（Embedding 相似度）
│   ├── scorer.py                            # 质量评分
│   ├── sanitizer.py                         # 脱敏
│   └── expert_identifier.py                 # 专家识别
├── store/                                   # 存储
│   ├── entry_db.py                          # org_knowledge_entries CRUD
│   ├── expert_db.py                         # org_expert_profiles CRUD
│   └── application_db.py                    # org_knowledge_applications CRUD
├── retriever/                               # 应用层
│   ├── hybrid_retriever.py                  # 三层并行检索
│   ├── prompt_injector.py                   # 显式规则注入
│   └── proactive_recommender.py             # 主动推荐
├── skill_generator.py                       # Skill 草案生成
└── db.py                                    # 表初始化

src/api/
└── org_knowledge.py                         # 管理员审核 API

src/scheduler/jobs/
└── org_knowledge_extraction_job.py          # 夜间抽取任务（凌晨 2:00）
└── org_knowledge_expiry_job.py              # 自动失效任务（每天 3:00）

frontend/src/components/org-knowledge/
├── OrgKnowledgeReview.vue                   # 管理员审核页面
├── OrgKnowledgeBrowser.vue                  # 组织知识浏览器
├── ExpertProfiles.vue                        # 专家画像
└── ProactiveRecommendation.vue              # 主动推荐卡片
```

#### 4.11.2 与现有架构的集成点

| 现有模块 | 集成方式 |
|---------|---------|
| `src/knowledge/retriever/hybrid_retriever.py` | 扩展：增加 `org_knowledge_entries` 作为检索源 |
| `src/core/agent.py` 的 system prompt 拼装 | 注入 L3 组织规则（来自 `prompt_registry`） |
| `src/core/skill_loader.py` | 加载 `src/skills/auto/` 自动生成的 Skill 草案 |
| `subagent_definitions.skills.allowed` | 自动生成的 Skill 进入白名单前需管理员审核 |
| `chat_records` 抽取链路 | 复用现有数据，只读 |
| `chat_context_summaries` | 复用为 L1 个人层记忆 |
| `prompt_registry` | 承载 L3 组织规则（已有版本管理） |

#### 4.11.3 定时任务

| 任务 | 时间 | 说明 |
|------|------|------|
| 候选识别 + 抽取 | 每天 02:00 | 处理前 24 小时对话 |
| 质量评分 + 入库 | 每天 02:30 | 紧接抽取 |
| 专家识别更新 | 每天 03:00 | 滚动 30 天窗口 |
| 知识失效检查 | 每天 03:30 | 30/60 天规则 |
| Skill 草案生成 | 每周日 04:00 | 高频操作流程化 |

> 建议与 [#38 后台任务外置](../tech-stack-optimization/background-tasks-externalization.md) 一起用 `arq` 实现。

#### 4.11.4 关键 API

```
# 管理员审核
GET  /api/org-knowledge/pending              # 待审核列表（L3）
POST /api/org-knowledge/{entry_id}/approve    # 审核通过
POST /api/org-knowledge/{entry_id}/reject     # 驳回
GET  /api/org-knowledge/list                  # 全部知识列表（支持筛选）

# 知识浏览（管理员 + 普通用户）
GET  /api/org-knowledge/search                # 语义检索
GET  /api/org-knowledge/by-subagent/{id}      # 按数字员工筛选
GET  /api/org-knowledge/experts               # 专家画像列表

# 应用反馈（普通用户）
POST /api/org-knowledge/{entry_id}/feedback   # 有用 / 无用

# Skill 草案
GET  /api/org-knowledge/skill-drafts          # 待审核 Skill 草案
POST /api/org-knowledge/skill-drafts/{id}/publish  # 发布为正式 Skill
```

### 4.12 实施分阶段建议

| 阶段 | 内容 | 工作量 | 价值 |
|------|------|--------|------|
| **Phase 1** | 数据基础 + 专家识别 + L2 FAQ 自动抽取 | 7-9 人天 | 让「专家的答案」自动惠及全员，单点见效 |
| **Phase 2** | L2 SOP 抽取 + 主动推荐机制 | 6-8 人天 | 让「最佳实践」被主动推荐，培养使用习惯 |
| **Phase 3** | L3 规则 + 术语表 + 管理员审核面板 | 5-7 人天 | 让管理员显式沉淀组织级指令 |
| **Phase 4** | Skill 草案自动生成 + 失效机制 + A/B | 6-8 人天 | 让高频操作流程化，让知识自维护 |
| **Phase 5** | 跨数字员工知识迁移 + 个人偏好学习 | 4-6 人天 | 让一个数字员工的经验迁移到其他 |

**MVP 建议**：Phase 1 是核心--专家识别 + FAQ 自动抽取，最直接体现「少数人密集使用，全员受益」。10 人天内可见效。

---

## 五、与其他方案的关联

### 5.1 与工作日报（[前一篇](./ai-agent-experience-daily-report-research.md)）的关系

工作日报是「**价值证明**」（让领导看到），组织知识沉淀是「**价值积累**」（让团队受益）。两者数据源都是 `chat_records`，可以共享：

- 工作日报的「高光时刻」-> 组织知识的「专家案例」候选
- 工作日报的「明日建议」-> 组织知识的「SOP」候选
- 团队日报的「Top 活跃员工」-> 专家识别的输入

### 5.2 与知识库能力增强（[#5](../ideas.md)）的关系

知识库 Phase 1-2 关注「人工上传的文档如何检索更好」，组织知识沉淀关注「**对话中的知识如何自动入库**」。两者共用 `documents` / `chunks` / `chunks_vec` 基础设施，但入库路径不同：

- 人工上传：编辑器/上传 -> 解析 -> 分块 -> 入库
- 自动抽取：对话历史 -> LLM 抽取 -> 脱敏 -> 入库（作为新的 document 或结构化 entry）

建议：**Phase 1 优先走结构化 `org_knowledge_entries` 表**（而非塞进 `documents`），避免污染人工上传的知识库。后续再考虑融合。

### 5.3 与可观测性（[#1](../ideas.md)）的关系

组织知识沉淀需要质量评估，可观测性的 LLM 质量评估模块可复用：
- 知识应用后对话的成功率 -> 复用可观测性的对话质量评分
- 专家识别的成功率 -> 复用可观测性的工具调用成功率统计

### 5.4 与 Prompt 版本管理（[调研](./prompt-version-management-research.md)）的关系

L3 组织规则直接复用 `prompt_registry` 表，已有版本管理、审核流程、A/B 灰度能力。无需重新建设。

### 5.5 与 Skill 系统（[架构](../.claude/rules/architecture.md)）的关系

自动生成的 Skill 草案进入 `src/skills/auto/`，由管理员审核后加入 `subagent_definitions.skills.allowed` 白名单。**关键约束**：自动生成的 Skill **不能自动生效**，必须经管理员审核（避免幻觉 Skill 污染生产环境）。

---

## 六、风险与对策

| 风险 | 严重度 | 对策 |
|------|--------|------|
| LLM 抽取幻觉（编造不存在的知识） | 高 | 置信度阈值 + 来源对话可追溯 + 管理员抽检 |
| 抽取的知识泄露敏感信息 | 高 | 双层脱敏（正则 + LLM 复核）+ 入库前最终校验 |
| 低质量知识污染 RAG | 高 | 质量评分 + 自动失效 + 用户反馈反向修正 |
| 专家识别偏差（遗漏真正专家） | 中 | 滚动 30 天窗口 + 多维度评分 + 管理员可手动标记 |
| 用户被「监控」焦虑 | 中 | 明确告知抽取范围 + 个人对话原文不暴露 + 退出机制 |
| 知识陈旧导致错误建议 | 中 | 自动失效 + 用户反馈触发复审 + 高频知识优先复审 |
| 自动生成 Skill 质量参差 | 中 | 强制管理员审核 + 试用期（7 天内高频回滚） |
| 跨数字员工知识迁移错误 | 中 | Phase 5 才做，先在单一数字员工内验证 |
| 抽取任务成本失控 | 低 | 小模型 + 增量抽取（只处理新对话）+ 失败重试上限 |
| 多 worker 重复抽取 | 中 | Redis 分布式锁 + 与 #38 一起用 arq |

---

## 七、后续展望

1. **跨租户匿名 benchmark**：脱敏后让租户看到「同类企业的 AI 采纳度分位」，激发良性竞争
2. **AI 教练**：基于组织知识，主动给员工推荐「今日学习任务」（如「你的同事 X 在 Y 场景有更好做法，看看？」）
3. **知识图谱可视化**：把组织知识按「实体-关系」可视化，让管理员一眼看到知识盲区
4. **跨数字员工能力迁移**：员工 A 在「外贸智能体」上的经验，部分迁移到「CRM 智能体」（需严格验证）
5. **租户知识资产化**：租户退出时，可导出全部组织知识作为「数字资产」交给企业

---

## 八、结论

用户的诉求--**让少数典型用户的密集使用经验自动沉淀为整个租户的能力**--是 B2B SaaS 从「工具」升级为「组织资产」的关键一步。行业领先产品（Glean、M365 Copilot、Moveworks）的共同模式是：

1. **双层 Graph**：个人 + 组织，层次分明
2. **专家识别**：只从专家对话中抽取，避免污染
3. **结构化抽取**：FAQ / SOP / 术语 / 规则，而非长文档
4. **主动推荐 + 显式注入**：知识不只是被检索，更要主动浮出
5. **质量保障 + 自动失效**：宁可少入库，不可入库低质量

本项目数据基础完备（`chat_records` + `execution_details` + `chat_context_summaries` + 知识库基础设施），技术路径清晰（抽取管线 + 三层检索 + Prompt 注入 + Skill 化），实施可分 5 个 Phase 推进，MVP（Phase 1：专家识别 + FAQ 抽取）7-9 人天可上线。

**关键提醒**：组织知识沉淀是「**飞轮效应**」的起点--使用越多 -> 沉淀越多 -> 新用户体验越好 -> 使用更多。一旦飞轮转起来，租户切换成本会显著上升，**这是最强的续费护城河**。建议作为 2026 Q3-Q4 的重点功能推进，与工作日报形成「价值证明 + 价值积累」双轮驱动。

---

## 附录 A：行业产品关键功能速查

### Glean（ glean.com ）
- Personal Graph：员工个人知识图谱（基于其搜索/访问/对话行为）
- Enterprise Graph：组织级知识图谱（文档 + 人 + 项目 + 流程）
- 权限感知：检索结果按用户权限过滤
- Authority Signals：基于作者权威度排序

### Microsoft 365 Copilot
- Semantic Index：企业级语义索引（M365 Graph 上的语义层）
- Personal Profile：员工个人画像（基于 Exchange/SharePoint 行为）
- Organizational Signals：组织信号（如「这个文档是 HR 发布的，权威度高」）

### Moveworks（ moveworks.com ）
- Topic Cluster：自动聚类员工问题
- Knowledge Auto-Sync：从已解决对话中自动更新知识库
- Expert Routing：识别专家并路由问题
- Proactive Knowledge：主动推送知识

### Dust.tt（ dust.tt ）
- @assistant 命名空间：每个 assistant 绑定特定知识源
- Shared Instructions：团队共享指令
- Workspace + User 双层：Workspace 级 + 个人级 assistant 分离

### Cursor Rules
- `.cursor/rules/` 目录：项目级规则文件
- 自动激活：按文件路径 / 语言 / 工作区
- Git 协作：通过 PR 维护

### Guru / Slite
- Knowledge Cards：结构化知识卡片
- Knowledge Triggers：基于上下文主动触发
- AI Verification：AI 验证知识是否过期
- Author Authority：作者权威度评分

> 以上速查基于公开知识，具体功能边界以各产品官方文档为准。
