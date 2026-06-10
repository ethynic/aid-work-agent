# Prompt 全生命周期管理 — 方案设计文档

> 关联文档：[企业级 2B 智能体平台基础设施建设差距分析](../research/enterprise-agent-infrastructure-gap-analysis.md) §2.2
> 关联调研：[Prompt 版本管理与生命周期管理 — 业界调研](../research/prompt-version-management-research.md)
> 设计日期：2026-05-28
> 更新日期：2026-06-08（重构 §八.1.8 为 System Prompt 模板 + 分段变量模式）
> 状态：Phase 2 代码完成，待验证

---

## 一、背景与目标

### 1.1 现状问题

当前系统的子智能体管理存在以下痛点：

| 痛点 | 具体表现 |
|------|---------|
| **无版本控制** | 自定义子智能体的 `SUBAGENT.md` 保存即覆盖，无法回滚 |
| **模板硬编码** | 系统模板（`master_agent.md`、`subagent_base.md`）使用 Python `str.format_map` 简单替换，变量未文档化 |
| **无变更追踪** | 不知道谁在什么时候改了什么，改了之后效果如何 |
| **无效果评估** | 修改 Prompt 后无法量化对比效果 |
| **工具/技能配置门槛高** | 管理员需要了解系统有哪些工具和技能，手动配置绑定关系 |
| **租户无编辑入口** | 租户管理员无法自行编辑定制 Prompt（extra_md），缺少前端 UI |

### 1.2 核心认识：子智能体 = 定义 + Prompt

子智能体本质上由**两部分**组成：

```
┌─────────────────────────────────────────────────┐
│  子智能体（SUBAGENT.md）                         │
│                                                  │
│  ┌─────────────────────────────────────────┐    │
│  │  Part 1: 定义部分（YAML frontmatter）    │    │
│  │  · name / description                   │    │
│  │  · tools（工具绑定）                     │    │
│  │  · skills（技能绑定）                    │    │
│  │  · knowledge_sources（知识库关联）       │    │
│  │  · triggers（触发条件）                  │    │
│  │  · context / llm_provider / reply_style │    │
│  │  → 这部分决定"用什么"，不能随意修改     │    │
│  │  → 工具/技能绑定应由 LLM 智能推荐       │    │
│  └─────────────────────────────────────────┘    │
│                                                  │
│  ┌─────────────────────────────────────────┐    │
│  │  Part 2: System Prompt（Markdown body）  │    │
│  │  · 身份设定、业务流程、行为约束          │    │
│  │  · 这是 LLM 的"工作手册"                │    │
│  │  → 这部分决定"怎么做"，可以迭代优化     │    │
│  │  → 纳入版本管理，支持对比和回滚          │    │
│  └─────────────────────────────────────────┘    │
└─────────────────────────────────────────────────┘
```

**设计决策**：

1. **定义部分（Part 1）**：通过子智能体管理界面修改，工具/技能绑定支持 LLM 智能推荐。修改后直接生效，不走版本管理（结构化配置的变更是原子性的，不需要 diff 对比）
2. **System Prompt 部分（Part 2）**：纳入版本管理，支持编辑 → 提交版本 → 对比 → 回滚的完整闭环
3. **租户定制 Prompt（extra_md）**：独立的第三层，租户管理员可编辑，同样纳入版本管理

### 1.3 设计目标

1. **子智能体统一管理**：平台管理员在一个界面中管理子智能体的完整生命周期（创建、配置、编辑 Prompt、禁用）
2. **System Prompt 版本化**：子智能体的 System Prompt 每次变更生成不可变版本，支持对比和一键回滚
3. **LLM 智能推荐工具/技能**：创建或编辑子智能体时，LLM 根据子智能体用途自动推荐应绑定的工具和技能
4. **知识库关联配置**：子智能体可配置允许检索的知识库 source_type，未配置则检索整个租户知识库
5. **租户定制 Prompt 管理**：租户管理员可在线编辑定制内容，同样纳入版本管理
6. **参数化模板**：继续使用 `str.format_map`（f-string）变量替换，不引入额外模板引擎
7. **渐进式改造**：不破坏现有架构，分阶段引入，每阶段可独立上线

### 1.4 设计原则

- **不替换 Git**：系统级模板（`master_agent.md`、`subagent_base.md`）仍走 Git 版本控制
- **优先覆盖子智能体 Prompt**：先从自定义子智能体的 `system_prompt` 和租户定制 Prompt 开始
- **最小化外部依赖**：不引入 Langfuse 等第三方服务，自建轻量方案
- **兼容现有机制**：保留 `extra_<tenant_id>.md` 租户定制机制和 `SUBAGENT.md` 文件格式，在其基础上扩展

---

## 二、核心概念模型

### 2.1 术语定义

| 术语 | 定义 |
|------|------|
| **Prompt Registry** | Prompt 注册表，每个可版本化的 Prompt 在注册表中有一条记录 |
| **Prompt Version** | Prompt 的一个不可变快照，包含内容、变量定义、模型参数 |
| **Label（标签）** | 指向某个版本的命名指针，如 `production`、`staging`、`experiment-a` |
| **Snippet（片段）** | 可被其他 Prompt 引用的可复用 Prompt 片段（二期） |
| **Draft（草稿）** | 未提交的编辑状态，仅存在于前端，不写入版本表 |
| **Prompt Section（板块）** | DB 子智能体 System Prompt 的结构化分区，固定为 5 个板块（岗位说明、岗位职责、工作流程、回复风格、其他说明），每个板块独立编辑和存储，运行时拼接组装 |

### 2.2 子智能体 Prompt 管理范围

本系统的 Prompt 版本化管理围绕**子智能体**展开，管理范围：

| 类型 | 来源 | 编辑权限 | 安全级别 | 版本化管理范围 |
|------|------|---------|---------|--------------|
| **子智能体 System Prompt** | `SUBAGENT.md` Markdown body | 仅平台管理员 | 🔴 高危 | ✅ 第一优先级 |
| **租户定制 Prompt** | `extra_<tenant_id>.md` | 租户管理员 | 🟡 中等 | ✅ 第一优先级 |
| **系统模板** | `master_agent.md` / `subagent_base.md` | 仅平台管理员 | 🔴 高危 | 🔜 第三阶段 |
| **技能 Prompt** | `SKILL.md` body | 仅平台管理员 | 🔴 高危 | 🔜 第二阶段 |

**子智能体管理的两层分离**：

| 层次 | 内容 | 修改方式 | 版本管理 |
|------|------|---------|---------|
| **定义层** | name、description、tools、skills、knowledge_sources、triggers、context | 子智能体管理界面直接修改（支持 LLM 推荐工具/技能） | 不需要（结构化配置） |
| **Prompt 层** | System Prompt（Markdown body） | Prompt 编辑器修改 → 提交版本 | 需要（支持 diff、回滚） |

**权限规则**：

| 角色 | 可编辑的 Prompt 类型 | 可查看的范围 |
|------|---------------------|-------------|
| 平台管理员 | 所有类型 | 全部租户 |
| 租户管理员 | 仅 `tenant_extra` | 本租户 |
| 普通用户 | 无 | 无 |

**高危 Prompt 的上线约束**（system prompt、系统模板）：

这类 Prompt 影响全局所有租户用户，修改不当会导致所有对话异常。因此必须强制走 **staging 验证流程**：

```
编辑草稿 → 提交为版本 → 标记 staging → 测试验证通过 → 标记 production
                                          ↓
                                     测试验证失败 → 修改后重新提交
```

- **禁止直接标记 production**：高危 Prompt 的新版本必须先标记为 `staging`，经验证后才允许切到 `production`
- **前端强制拦截**：提交 production 标签时，后端校验该版本是否经过 staging 验证，未验证则拒绝
- **验证方式**：在 staging 环境下用测试数据集对话验证，或人工审核确认效果正常

### 2.3 生命周期状态机

#### 普通流程（租户定制 Prompt）

```
  ┌──────────┐   保存编辑   ┌──────────┐   提交发布   ┌───────────┐
  │  空白    │───────────▶│  Draft   │───────────▶│ Committed │
  └──────────┘            └─────┬────┘            └─────┬─────┘
                                │                       │
                                │ 继续编辑              │ 打标签
                                ▼                       ▼
                          ┌──────────┐            ┌───────────┐
                          │  Draft   │            │  Labeled  │
                          │(modified)│            │(staging / │
                          └──────────┘            │production)│
                                                  └───────────┘
```

- **Draft**：前端编辑状态，不写入版本表，仅保存到草稿表
- **Committed**：提交后的不可变版本，自增版本号
- **Labeled**：通过标签（`production`/`staging`/`experiment-*`）标记环境

#### 高危流程（系统模板、子智能体 System Prompt）

```
  ┌──────────┐   保存草稿   ┌──────────┐   提交版本   ┌───────────┐
  │  空白    │───────────▶│  Draft   │───────────▶│ Committed │
  └──────────┘            └──────────┘            └─────┬─────┘
                                                        │
                                          ┌─────────────┤
                                          ▼             │
                                    ┌───────────┐       │
                                    │  staging   │◀──────┤ 回退修改
                                    └─────┬─────┘       │
                                          │ 验证通过     │
                                          ▼             │
                                    ┌───────────┐       │
                                    │ production │───────┘ 验证失败
                                    └───────────┘
```

- **staging 阶段必须经过**：高危 Prompt 的新版本禁止直接标记 `production`
- **验证通过**：管理员在 staging 环境确认效果后，手动将标签切换为 `production`
- **验证失败**：回退到 Draft 重新编辑，或直接回滚到上一个 production 版本

---

## 三、数据模型设计

### 3.1 ER 图

```
┌──────────────────┐       ┌──────────────────────┐
│ prompt_registry   │       │ prompt_versions       │
│──────────────────│       │──────────────────────│
│ id (PK, UUID)    │◀──────│ id (PK, UUID)        │
│ tenant_id        │       │ prompt_id (FK)       │
│ scope            │       │ version (INT)        │
│ scope_id         │       │ content              │
│ prompt_type      │       │ variables (JSONB)    │
│ display_name     │       │ model_config (JSONB) │
│ latest_version   │       │ commit_message       │
│ created_by       │       │ parent_version       │
│ created_at       │       │ created_by           │
│ updated_at       │       │ created_at           │
└──────────────────┘       └──────────────────────┘
         │                          │
         │                          │
         ▼                          ▼
┌──────────────────┐       ┌──────────────────────┐
│ prompt_labels     │       │ prompt_drafts         │
│──────────────────│       │──────────────────────│
│ id (PK, UUID)    │       │ id (PK, UUID)        │
│ prompt_id (FK)   │       │ prompt_id (FK)       │
│ version_id (FK)  │       │ content              │
│ label             │       │ variables (JSONB)    │
│ created_by       │       │ updated_by           │
│ created_at       │       │ updated_at           │
│ updated_at       │       └──────────────────────┘
└──────────────────┘
```

### 3.2 子智能体定义表（Phase 2 新增）

子智能体的元数据定义（name、tools、skills 等）独立存储，与 system_prompt（由 prompt_versions 管理）解耦。

```sql
CREATE TABLE IF NOT EXISTS subagent_definitions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id        TEXT NOT NULL,       -- 唯一标识（如 my-agent）
    name            TEXT NOT NULL,       -- 显示名称
    description     TEXT,                -- 用途描述（50~500 字，LLM 推荐依赖此字段）
    version         TEXT DEFAULT '1.0.0',
    author          TEXT,
    triggers        JSONB DEFAULT '{}',  -- 触发条件
    tools           JSONB DEFAULT '{}',  -- 工具配置 {inherit, additional}
    skills          JSONB DEFAULT '{}',  -- 技能配置 {allowed}
    knowledge_sources JSONB DEFAULT '[]', -- 预留字段，运行时知识库关联由 subagent_knowledge_sources 表管理
    context         JSONB DEFAULT '{}',  -- 上下文约束
    delegatable_to  JSONB DEFAULT '[]',
    allow_delegation BOOLEAN DEFAULT TRUE,
    llm_provider    TEXT,
    reply_style     TEXT,
    business_pages  JSONB,
    status          TEXT DEFAULT 'active',  -- active / disabled
    created_by      TEXT,
    updated_by      TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_subagent_def_agent
    ON subagent_definitions (agent_id);
CREATE INDEX IF NOT EXISTS idx_subagent_def_status
    ON subagent_definitions (status);
```

**与 prompt_registry 的关联**：通过 `scope='subagent'`、`scope_id=<agent_id>` 松散关联（无外键）。system_prompt 的版本管理完全由 prompt_versions 负责。

### 3.3 表结构

#### prompt_registry — Prompt 注册表

```sql
CREATE TABLE IF NOT EXISTS prompt_registry (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     TEXT,                          -- NULL 表示系统级
    scope         TEXT NOT NULL,                 -- 'subagent' | 'skill' | 'system_template' | 'tenant_extra'
    scope_id      TEXT NOT NULL,                 -- 子智能体ID / 技能ID / 模板名 / 'extra:{subagent}:{tenant}'
    prompt_type   TEXT DEFAULT 'normal',         -- 'normal' | 'snippet'
    display_name  TEXT,
    description   TEXT,
    latest_version INTEGER DEFAULT 0,
    created_by    TEXT,
    created_at    TIMESTAMP DEFAULT NOW(),
    updated_by    TEXT,
    updated_at    TIMESTAMP DEFAULT NOW()
);

-- 同一租户内 scope + scope_id 唯一
CREATE UNIQUE INDEX IF NOT EXISTS idx_prompt_registry_scope
    ON prompt_registry(tenant_id, scope, scope_id);
```

#### prompt_versions — Prompt 版本（不可变）

```sql
CREATE TABLE IF NOT EXISTS prompt_versions (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    prompt_id      UUID NOT NULL,
    version        INTEGER NOT NULL,             -- 自增版本号
    content        TEXT NOT NULL,                -- Prompt 内容（f-string 变量用 {variable_name}）
    variables      JSONB,                        -- 变量定义 [{"name": "agent_name", "type": "string", "default": ""}]
    model_config   JSONB,                        -- 模型参数覆盖 {"temperature": 0.7, "max_tokens": 4096}
    commit_message TEXT,                         -- 变更说明
    parent_version INTEGER,                      -- 基于哪个版本
    content_hash   TEXT,                         -- 内容 SHA-256 摘要（用于去重）
    created_by     TEXT,
    created_at     TIMESTAMP DEFAULT NOW(),
    UNIQUE (prompt_id, version)
);

CREATE INDEX IF NOT EXISTS idx_prompt_versions_prompt ON prompt_versions(prompt_id);
CREATE INDEX IF NOT EXISTS idx_prompt_versions_hash ON prompt_versions(content_hash);
```

#### prompt_labels — 标签（环境部署）

```sql
CREATE TABLE IF NOT EXISTS prompt_labels (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    prompt_id     UUID NOT NULL,
    version_id    UUID NOT NULL,
    label         TEXT NOT NULL,                 -- 'production' | 'staging' | 'experiment-*' | 'latest'
    created_by    TEXT,
    created_at    TIMESTAMP DEFAULT NOW(),
    updated_by    TEXT,
    updated_at    TIMESTAMP DEFAULT NOW(),
    UNIQUE (prompt_id, label)                    -- 每个 Prompt 每个标签只能指向一个版本
);

CREATE INDEX IF NOT EXISTS idx_prompt_labels_lookup ON prompt_labels(prompt_id, label);
```

#### prompt_drafts — 草稿

```sql
CREATE TABLE IF NOT EXISTS prompt_drafts (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    prompt_id     UUID NOT NULL UNIQUE,          -- 每个 Prompt 只能有一个草稿
    content       TEXT NOT NULL,
    variables     JSONB,
    base_version  INTEGER,                       -- 基于哪个版本编辑
    updated_by    TEXT,
    updated_at    TIMESTAMP DEFAULT NOW()
);
```

### 3.3 与现有系统的映射关系

| scope | scope_id 示例 | 现有存储 | 说明 |
|-------|-------------|---------|------|
| `subagent` | `order-processing` | `subagents/order-processing/SUBAGENT.md` | 内置子智能体 |
| `tenant_extra` | `extra:order-processing:tenant_abc` | `storage/subagents/order-processing/extra_tenant_abc.md` | 租户定制 |
| `skill` | `baidu-search-1.1.3` | `src/skills/baidu-search-1.1.3/SKILL.md` | 技能 Prompt（二期） |
| `system_template` | `master_agent` | `src/prompts/templates/master_agent.md` | 系统模板（三期） |

---

## 四、模板方案

### 4.1 保持 f-string（str.format_map）

**不做模板引擎升级**。理由：

1. **当前方案已满足需求**：系统模板只有 ~10 个变量，全部是纯字符串插值（`{variable_name}`），不需要条件、循环或过滤器
2. **零学习成本**：企业管理员编辑 Prompt 时直接写 `{变量名}`，无需学习任何模板语法
3. **零安全风险**：`_SafeDict` 已处理未定义变量，不存在模板注入问题
4. **与 LangChain 一致**：LangChain 的 `PromptTemplate` 默认也是 f-string，只在极少数场景才切换到 Mustache/Jinja2

现有渲染器 (`src/prompts/renderer.py`) 的 `_SafeDict + format_map` 方案保持不变。

### 4.2 变量文档化

虽然不换引擎，但需要将模板中可用的变量**文档化**，方便编辑者知道可以用哪些变量：

| 变量名 | 类型 | 适用范围 | 说明 |
|--------|------|---------|------|
| `{available_tools_list}` | string | master / subagent | 可用工具列表 |
| `{skill_descriptions}` | string | master / subagent | 技能描述 |
| `{tool_usage_guides}` | string | master / subagent | 工具使用指南 |
| `{subagent_matching_hint}` | string | master | 子智能体匹配提示 |
| `{subagent_descriptions}` | string | master | 子智能体描述列表 |
| `{delegation_guide}` | string | master | 委托指南 |
| `{subagent_constraint_section}` | string | subagent | 子智能体角色约束 |
| `{long_term_memory}` | string | master / subagent | 用户长期记忆 |
| `{user_info_section}` | string | master / subagent | 用户信息 |
| `{reply_style_section}` | string | master / subagent | 回复风格 |

变量文档存储在 `prompt_versions.variables` 字段中，前端编辑器可展示可用变量列表供参考。

---

## 五、System Prompt 组装架构与优化方向

> 本节记录当前 system prompt 的组装机制和已识别的优化点，作为 Prompt 管理的架构基础。

### 5.1 当前组装流程

子智能体的完整 system prompt 由以下三部分叠加组成：

```
┌─────────────────────────────────────────────────┐
│  ① subagent_base.md — 通用 Agent 工作说明       │
│  · 核心工作流程（分析→计划→执行→汇报）           │
│  · 技能使用规则                                  │
│  · 工具使用指南（各工具的 usage_guide）           │
│  · 指导原则、回复规范                            │
│  · 重要限制（不能委派）                          │
├─────────────────────────────────────────────────┤
│  ② SUBAGENT.md body — 子智能体专属业务流程      │
│  · 身份设定（如旅游顾问）                        │
│  · 业务阶段管理（如聊需求→出行程→报价）          │
│  · 搜索原则、沟通技巧、行为约束                  │
│  · 通过 {subagent_constraint_section} 注入       │
├─────────────────────────────────────────────────┤
│  ③ 租户定制 extra.md — 租户级覆盖               │
│  · 覆盖默认行为约束                              │
│  · 租户专属话术/流程                             │
│  · 拼接在②末尾的「租户定制需求」栏目下           │
├─────────────────────────────────────────────────┤
│  ④ 运行时变量 — 动态上下文                      │
│  · {available_tools_list} / {skill_descriptions} │
│  · {user_info_section} / {long_term_memory}      │
│  · {reply_style_section}                         │
│  · 由 _build_base_system_prompt() 动态计算注入   │
└─────────────────────────────────────────────────┘
```

**为什么通用模板（①）必须保留**：子智能体本质上也是一个 Agent，运行在 agent loop 中。通用模板告诉它"怎么作为一个 Agent 工作"（分析需求、调用工具、汇报结果），而 SUBAGENT.md 告诉它"怎么作为旅游顾问处理业务"。两者是叠加关系，不是替代关系。

### 5.2 当前已识别的问题

#### 问题一：通用指导原则与业务约束存在矛盾 ✅ 已修复

| 位置 | 内容 | 冲突点 |
|------|------|--------|
| `subagent_base.md` 指导原则 | "透明化：让用户知道你在做什么" | 与旅游顾问行为约束 #9 "绝不暴露内部工作过程" 直接矛盾 |

**已修正**：通用模板中的"透明化"已改为"**专业沟通**：告知用户结论和结果，不暴露内部工具调用和检索过程"。

#### 问题二：工具描述存在两层冗余 ✅ 已优化

| 层级 | 位置 | 内容 |
|------|------|------|
| tools 参数 | API 调用 `tools` 字段 | 工具名 + 参数 schema（JSON Schema） |
| system prompt | `{{{tool_usage_guides}}}` 区块 | 4 个工具的详细 usage_guide（file_write、browser_automation、content_generate、http_api） |

**分析**：两层都有存在的必要。`tools` 参数告诉 LLM 工具的参数结构，但无法传达"正确使用姿势"（如 `file_write` 的三种模式、`content_generate` 好坏 prompt 示例）。`usage_guide` 补充了这些关键上下文。

**优化方向**：可以在 `usage_guide` 中去掉与 JSON Schema 重复的参数说明，只保留"用法模式"和"示例"，减少 token 消耗。✅ 已完成：http_api 的 usage_guide 已精简，去掉了重复的参数说明部分。

#### 问题三：{available_tools_list} 与 tools 参数重复 ✅ 已评估

`{available_tools_list}` 在 system prompt 中列出工具名列表（如 `` `email_send`, `read_file` ``），但 LLM 通过 `tools` 参数已经能看到所有工具。

**评估结论**：`{available_tools_list}` 确有冗余，但 token 消耗极低（约 20-50 token），去掉的收益不足以抵消修改模板和 agent.py 的风险，决定保留。

### 5.3 Prompt 组装架构图

```
                    Agent._build_system_prompt(user)
                               │
                               ▼
                    ┌─── AgentMode 判断 ───┐
                    │                      │
               MASTER 模式            SUBAGENT/STANDALONE 模式
                    │                      │
                    ▼                      ▼
           master_agent.md          subagent_base.md
                    │                      │
                    │                      ├── SUBAGENT.md body ──┐
                    │                      │   (subagent_         │
                    │                      │    constraint_       │
                    │                      │    section)          │
                    │                      │                      │
                    │                      └── extra_{tenant}.md ─┘
                    │                             (追加到末尾)
                    │
                    ▼
              f-string 渲染（_SafeDict + format_map）
                    │
                    ▼
              完整 system_prompt 字符串
```

### 5.4 优化计划

| 优先级 | 优化项 | 改动范围 | 影响 | 状态 |
|--------|--------|---------|------|------|
| P0 | 修正"透明化"矛盾 → 改为"专业沟通" | `subagent_base.md`、`master_agent.md` | 消除 LLM 行为不一致 | ✅ 已完成 |
| P1 | 精简 `usage_guide`，去掉与 JSON Schema 重复的参数说明 | 各工具的 `usage_guide` 属性 | 减少 token 消耗 | ✅ 已完成（http_api） |
| P2 | 评估是否去掉 `{available_tools_list}` | 模板文件 + agent.py | 减少 token 消耗 | ✅ 已评估，保留 |
| P3 | 通用工作流章节按需注入（某些子智能体不需要 create_plan 说明） | 模板拆分 | 精准匹配业务需求 | 🔜 后续迭代 |

---

## 六、运行时 Prompt 解析流程

### 6.1 当前流程（保持不变的部分）

```
用户请求 → Agent._build_system_prompt(user)
  → PromptManager.render("master_agent.md", variables) 或
  → PromptManager.render("subagent_base.md", variables)
  → 返回完整的 system_prompt
```

### 6.2 新增 Prompt Resolver 层

在模板渲染之前，新增一个 Prompt Resolver 层，负责从数据库解析当前应使用的 Prompt 版本。

#### 6.2.1 通用 Prompt 解析（`PromptResolver.resolve()`）

用于 skill、system_template、tenant_extra 等非子智能体场景：

```
PromptResolver.resolve(scope, scope_id, tenant_id)
  → 查找 prompt_registry 记录
  → 查找 prompt_labels 中 "production" 标签指向的版本
  → 返回 prompt_version.content 或 None
```

#### 6.2.2 子智能体解析（DB 优先，文件系统兜底）

子智能体的定义和 system_prompt 解析遵循 **DB 优先**策略：

```
子智能体查找顺序:
  1. registry 缓存（启动时从文件系统全量加载 + DB 全量覆盖）
  2. DB 按需加载（AgentFactory._load_single_from_db）
     - 查 subagent_definitions（agent_id = agent_id, status = active）
     - 如不存在 → 返回 None
     - 如存在 → 调用 prompt_resolver.resolve(scope="subagent", scope_id=agent_id)
     - 如 prompt 也无 → 返回 None
     - 都有 → 注册到 registry 并返回
  3. 都没有 → 返回 None（加载失败）
```

**核心原则**：DB 优先于文件系统。DB 中有定义 + system_prompt 的子智能体覆盖文件系统版本；DB 中没有的保留文件系统版本；两者都没有则加载失败。

### 6.3 降级策略

#### 子智能体的加载策略（DB 优先）

```
Agent._build_system_prompt(user):
  if config.from_db == True:
    # 来自 DB → system_prompt 已在 load_from_db() 时解析并写入 config
    subagent_constraint = config.system_prompt
  else:
    # 来自文件系统 → 使用 SUBAGENT.md 中的 system_prompt，不查 DB
    subagent_constraint = config.system_prompt
```

**关键**：`_build_system_prompt()` 不再独立调用 `prompt_resolver.resolve()`。DB 来源的 Config 在 `load_from_db()` 阶段已解析好 system_prompt；文件系统来源的 Config 直接使用 SUBAGENT.md 中的内容。

#### SubagentRegistry 的加载策略（DB 优先，文件系统兜底）

**启动时全量加载**：

```
SubagentRegistry.load_from_db():
  definitions = SubagentDefinitionDB.list_active()
  for row in definitions:
    prompt_content = prompt_resolver.resolve(scope="subagent", scope_id=row["agent_id"])
    if not prompt_content:
      logger.warning(f"跳过 {row['agent_id']}：有定义但无 system_prompt，由文件系统兜底")
      continue  # ← 缺 prompt 则跳过，保留文件系统版本
    config = SubagentConfig(..., system_prompt=prompt_content, from_db=True)
    self._configs[config.name] = config  # DB 版本覆盖文件系统版本
```

**运行时按需加载**：

```
AgentFactory._load_single_from_db(registry, agent_id):
  row = SubagentDefinitionDB.get_by_agent_id(agent_id)
  if not row → return None
  prompt_content = prompt_resolver.resolve(scope="subagent", scope_id=agent_id)
  if not prompt_content → return None
  config = SubagentConfig(..., from_db=True)
  registry._configs[config.name] = config  # 注册到内存，后续请求直接命中缓存
  return config
```

调用方（`create_standalone_subagent`、`_delegate_to_subagent`、`DelegateTool.execute`）查找顺序：registry 缓存 → DB 按需加载 → 返回 None。

#### 非 DB 场景的降级链（通用 Prompt）

```
PromptResolver.resolve()
  ├── 查找 prompt_labels("production") → 找到 → 使用该版本
  ├── 查找 prompt_labels("latest")     → 找到 → 使用该版本
  ├── prompt_registry 存在但无标签      → 使用 latest_version
  └── prompt_registry 不存在            → 返回 None（调用方自行降级）
```

### 6.4 缓存设计

采用 Redis 二级缓存 + 版本号失效：

```python
class PromptCache:
    """
    缓存结构：
    - prompt:content:{prompt_id}:{version} → content (TTL 10min)
    - prompt:label:{prompt_id}:{label} → version (TTL 5min)
    """

    def get_content(self, prompt_id: str, version: int) -> str | None:
        """获取版本内容"""
        ...

    def set_content(self, prompt_id: str, version: int, content: str):
        """缓存版本内容"""
        ...

    def invalidate_label(self, prompt_id: str, label: str):
        """标签变更时失效"""
        ...

    def invalidate_prompt(self, prompt_id: str):
        """Prompt 整体失效（删除时）"""
        ...
```

多 Worker 一致性：标签/版本写入时主动刷新 Redis 缓存，其他 Worker 下次读取获取最新版本。

---

## 七、API 设计

### 7.1 Prompt 管理 API

**前缀**：`/api/admin/prompts`（平台管理员）和 `/api/prompts`（租户管理员）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 列出 Prompt（分页、按 scope 筛选） |
| GET | `/{prompt_id}` | 获取 Prompt 详情（含当前 production 版本） |
| POST | `/` | 创建新 Prompt 注册 |

### 7.2 版本管理 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/{prompt_id}/versions` | 列出所有版本（分页） |
| GET | `/{prompt_id}/versions/{version}` | 获取特定版本内容 |
| POST | `/{prompt_id}/versions` | 创建新版本（commit） |
| GET | `/{prompt_id}/versions/diff?v1=X&v2=Y` | 两个版本 diff 对比 |

### 7.3 草稿 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/{prompt_id}/draft` | 获取当前草稿 |
| PUT | `/{prompt_id}/draft` | 保存草稿 |
| DELETE | `/{prompt_id}/draft` | 丢弃草稿 |
| POST | `/{prompt_id}/draft/commit` | 提交草稿为新版本 |

### 7.4 标签 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/{prompt_id}/labels` | 列出所有标签 |
| PUT | `/{prompt_id}/labels/{label}` | 设置标签指向的版本 |
| DELETE | `/{prompt_id}/labels/{label}` | 删除标签 |
| POST | `/{prompt_id}/rollback` | 将 production 标签回滚到指定版本 |

### 7.5 评估 API（二期）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/{prompt_id}/evaluate` | 对指定版本运行评估 |
| GET | `/{prompt_id}/evaluations` | 获取评估结果列表 |
| GET | `/{prompt_id}/evaluations/{eval_id}` | 获取评估详情 |

---

## 八、与现有系统的集成方案

### 8.1 子智能体管理（第一优先级）

#### 8.1.1 设计思路

**新增独立的"智能体管理"页面**，与旧的 `/portal/subagents`（DigitalEmployeeManager）完全解耦。旧的数字员工页面保持不动。

**核心变化**：子智能体定义（name、tools、skills 等）从文件系统迁移到数据库（`subagent_definitions` 表），system_prompt 的版本管理复用已有的 `prompt_versions` 系统。

**新页面** `/portal/agent-definitions`（AgentDefinitionManager.vue）：

1. **列表页**：展示数据库中的子智能体定义，每个卡片展示当前 System Prompt 版本号、状态
2. **编辑页面两区分离**：
   - **定义区**（左半部分）：name、description、tools、skills、knowledge_sources 等结构化配置。工具/技能绑定支持 LLM 智能推荐按钮
   - **Prompt 区**（右半部分）：System Prompt 的 Markdown 编辑器 + 版本历史面板 + 版本对比
3. **运行时集成**：`SubagentRegistry` 新增 `load_from_db()` 方法（DB 优先覆盖文件系统版本），`AgentFactory` 新增 `_load_single_from_db()` 按需加载

**与旧系统的关系**：

| 维度 | 旧（/portal/subagents） | 新（/portal/agent-definitions） |
|------|------------------------|-------------------------------|
| 数据源 | 文件系统（SUBAGENT.md） | PostgreSQL（subagent_definitions） |
| 加载优先级 | 文件系统唯一来源 | **DB 优先**，DB 有定义+prompt 则覆盖文件系统版本 |
| DB-only 智能体 | 不支持 | 按需加载支持（文件系统中无需存在） |
| Prompt 版本 | 无 | 完整版本管理（提交/对比/回滚） |

#### 8.1.2 LLM 智能推荐工具/技能

**问题**：管理员在创建/编辑子智能体时，需要手动配置 tools 和 skills，但对系统提供的工具和技能不熟悉，容易遗漏或误配。

**方案**：在编辑界面的工具/技能配置区域增加"智能推荐"按钮：

```
管理员填写子智能体名称和用途描述
    │
    ▼
点击"智能推荐"按钮
    │
    ▼
后端调用 LLM，输入：
  - 子智能体的 name、description
  - 系统当前可用的工具列表（ID + 名称 + 描述）
  - 系统当前可用的技能列表（ID + 名称 + 描述）
    │
    ▼
LLM 返回推荐的 tools 和 skills 配置
    │
    ▼
前端展示推荐结果，管理员确认或调整后保存
```

**description 字数约束**：description 字段要求 50~500 字。低于 50 字时 LLM 推荐效果差（信息不足无法判断用途），前端在字数不足时禁用"智能推荐"按钮并提示"描述不足 50 字，无法推荐"。

**后端 API**：

```
POST /api/admin/agent-definitions/meta/suggest-config
Body: { "name": "售后服务助手", "description": "..." }
  → 后端校验 description 长度，< 50 字返回 400 错误
Response: { "tools": {"inherit": true, "additional": ["http_api"]}, "skills": {"allowed": ["after-sales-core"]} }
```

**现有基础**：`POST /api/admin/subagents/{agent_id}/ai-enhance` 已实现类似的 LLM 增强功能，可以复用其模式。

#### 8.1.3 知识库关联配置（Phase 3） ✅ 已实现

**问题**：当前 `knowledge_base_search` 工具搜索整个租户下的所有知识库文档。不同子智能体关注的知识范围不同（如旅游顾问关注景点和酒店，售后助手关注产品文档），LLM 调用检索工具时可能检索到不相关的内容。

**架构决策**：知识库关联是**租户级别**的配置（per-tenant per-agent），而非平台级定义。原因：每个租户的知识库分类不同（名称、数量），Portal 管理页面无租户上下文，无法获知租户有哪些知识库。因此配置入口放在 `TenantMgmt.vue` 的 agents tab，与"环境变量"和"API 配置"按钮同级。

**数据模型**：

```sql
-- 租户级知识库关联表（Phase 3.2 新增）
CREATE TABLE subagent_knowledge_sources (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    subagent_name TEXT NOT NULL,
    sources JSONB NOT NULL DEFAULT '[]',
    -- sources 格式: [{"source_type": "attraction_resource", "display_name": "景点资源数据"}]
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(tenant_id, subagent_name)
);
```

**数据源**：租户的知识库分类由 `knowledge_categories` 表维护（每条记录含 `source_type` + `display_name` + `document_count`）。前端通过 `GET /knowledge/categories`（带 `X-Tenant-Id`）获取可选列表。

**运行时行为**：

```
_build_system_prompt() → _load_knowledge_sources(tenant_id, subagent_name)
  → 查询 subagent_knowledge_sources 表
  → 如果非空:
    在 system prompt 末尾追加:
      ## 可用知识库
      你可以通过 knowledge_base_search 工具检索以下知识库：
      - attraction_resource（景点资源数据）
      - hotel_resource（酒店住宿数据）
      调用时必须传入正确的 source_type 参数。
  → 如果为空（未配置）:
    不注入约束，LLM 按需决定是否传 source_type
```

**后端 API**：

```
GET  /api/saas/tenant/subagent-knowledge/:subagent_name  → 获取关联
PUT  /api/saas/tenant/subagent-knowledge/:subagent_name  → 设置关联（全量覆盖）
DELETE /api/saas/tenant/subagent-knowledge/:subagent_name → 删除关联
```

**前端配置入口**：`TenantMgmt.vue` agents tab，每个已授权 agent 旁的"知识库"按钮 → 弹窗复选框列表。

#### 8.1.4 工具/技能元数据 API（Phase 3）

**问题**：管理员在配置子智能体的工具和技能时，不知道系统有哪些可用的工具/技能，也不了解各工具/技能的作用。当前前端只展示 ID 列表，没有名称和说明。

**方案**：新增元数据查询 API，返回系统中所有工具和技能的 `id`、`display_name`、`description`，前端以可选择的列表形式展示。

**后端 API**：

```
GET /api/admin/agent-definitions/meta/tools
Response: {
    "tools": [
        {"id": "web_search", "name": "网络搜索", "description": "在网络上搜索实时信息"},
        {"id": "knowledge_base_search", "name": "知识库搜索", "description": "搜索租户知识库中的相关内容"},
        {"id": "email_send", "name": "发送邮件", "description": "发送电子邮件"},
        ...
    ]
}

GET /api/admin/agent-definitions/meta/skills
Response: {
    "skills": [
        {"id": "trade-customer-1.0.0", "name": "外贸客户管理", "description": "管理外贸客户信息和跟进记录"},
        {"id": "travel-quote-1.0.0", "name": "行程报价生成", "description": "生成旅游行程和报价方案"},
        ...
    ]
}

GET /api/admin/agent-definitions/meta/source-types
Response: {
    "source_types": [
        {"source_type": "file", "description": "上传的文档资料"},
        {"source_type": "attraction_resource", "description": "景点资源数据"},
        {"source_type": "hotel_resource", "description": "酒店住宿数据"}
    ]
}
```

**数据来源**：
- **工具**：`ToolRegistry` 已有每个工具的 `name`、`display_name`、`description`，直接聚合返回
- **技能**：`SkillRegistry` 已有每个技能的 `name`、`description`，直接聚合返回
- **source_type**：从 `documents` 表 `SELECT DISTINCT source_type` 查询，加上预定义的说明

**前端展示**：工具和技能选择器从"手动输入 ID"改为"下拉选择 + 描述说明"，用户可以看到每个选项的名称和用途。

#### 8.1.5 子智能体运行时集成

**改动点**：
1. `src/models/subagent.py` — `SubagentConfig` 新增 `from_db: bool = False` 字段
2. `src/subagents/registry.py` — 新增 `load_from_db()` 方法（DB 优先，覆盖文件系统版本）
3. `src/core/agent.py` — `_build_system_prompt()` 改为根据 `from_db` 标记决定行为
4. `src/subagents/factory.py` — 新增 `_load_single_from_db()` 按需加载单个子智能体

**策略**：DB 优先——DB 中有定义 + system_prompt 的子智能体直接覆盖文件系统版本。DB 中无定义或无 system_prompt 的，保留文件系统版本。

**加载流程**（启动时）：
1. `SubagentRegistry()` 构造时从 `subagents/` 目录全量加载文件系统子智能体
2. `load_from_db()` 从数据库全量加载，覆盖同名的文件系统版本
3. 文件系统中有但 DB 中没有的子智能体保持不变

**按需加载**（运行时）：
当 registry 中找不到子智能体时（如 DB-only 智能体未被启动时全量加载到），`AgentFactory._load_single_from_db()` 从 DB 单独加载并注册到 registry。调用方覆盖 `create_standalone_subagent`、`_delegate_to_subagent`、`DelegateTool.execute` 三个入口。

```python
# registry.py — 启动时全量加载
def load_from_db(self):
    definitions = SubagentDefinitionDB.list_active()
    for row in definitions:
        prompt_content = prompt_resolver.resolve(
            scope="subagent", scope_id=row["agent_id"],
        )
        if not prompt_content:
            logger.warning(
                f"子智能体 {row['agent_id']} 有定义但无 system_prompt，"
                "跳过 DB 加载，由文件系统兜底"
            )
            continue

        config = SubagentConfig(
            name=row["name"], dir_name=row["agent_id"],
            description=row.get("description", ""),
            tools=row.get("tools", {}),
            skills=row.get("skills", {}),
            knowledge_sources=row.get("knowledge_sources", []),
            context=row.get("context", {}),
            system_prompt=prompt_content,  # 已从 DB 解析
            from_db=True,  # 标记来自数据库
            # ...其他字段从 DB row 映射
        )
        self._configs[config.name] = config  # DB 版本覆盖文件系统版本

# factory.py — 运行时按需加载
@staticmethod
def _load_single_from_db(registry, agent_id):
    row = SubagentDefinitionDB.get_by_agent_id(agent_id)
    if not row:
        return None
    prompt_content = prompt_resolver.resolve(scope="subagent", scope_id=agent_id)
    if not prompt_content:
        return None
    config = SubagentConfig(..., from_db=True)
    registry._configs[config.name] = config
    registry._build_indices()
    return config
```

**`_build_system_prompt()` 改造**（Phase 2 实现，需调整 Phase 1 的逻辑）：

Phase 1 已实现的 `_build_system_prompt()` 中有独立的 `prompt_resolver.resolve()` 调用。Phase 2 需将其改为根据 `from_db` 标记决定行为：

```python
# agent.py _build_system_prompt() 改造
subagent_constraint = ""
if self.subagent_config:
    if self.subagent_config.from_db:
        # 来自 DB → system_prompt 已在 load_from_db() 时解析好
        subagent_constraint = self.subagent_config.system_prompt
    else:
        # 来自文件系统 → 直接用 SUBAGENT.md 中的内容，不查 DB
        subagent_constraint = self.subagent_config.system_prompt
```

**核心变化**：不再在 `_build_system_prompt()` 中独立调用 `prompt_resolver.resolve()`。DB 解析工作在 `load_from_db()` 阶段完成，运行时只根据 `from_db` 标记读取已解析的内容。

#### 8.1.6 回复风格配置（Phase 3）

**现状**：

- **数据库**：`subagent_definitions` 表已有 `reply_style TEXT` 字段
- **模型**：`SubagentConfig` 已有 `reply_style: Optional[str]` 字段
- **运行时**：`agent.py _resolve_reply_style()` 已读取 `subagent_config.reply_style`（优先级 2）
- **风格数据**：`reply_styles` 表存储系统级和租户级风格，`StyleManager` 管理缓存和降级
- **API**：`GET /api/saas/reply-styles` 已返回风格列表

**缺失**：`AgentDefinitionManager.vue` 定义区无回复风格选择器，管理员无法在智能体定义中配置回复风格。

**方案**：在定义区增加回复风格下拉选择器，调用现有 API 获取可选风格列表。

```
定义区 — 回复风格配置区：
┌─────────────────────────────────────────┐
│  回复风格：[▼ 拟人风格 — 以真人同事口吻回复 ] │
│                                         │
│  选项来源：GET /api/saas/reply-styles    │
│  · 系统级风格（tenant_id=system）        │
│  · 租户自定义风格（当前租户）             │
│  · "默认"选项（空值，使用全局配置）       │
└─────────────────────────────────────────┘
```

**后端无改动**，`reply_style` 字段的读写已在 Phase 2 实现。只需前端增加：
1. 调用 `GET /api/saas/reply-styles` 获取风格列表
2. 下拉选择器展示风格名称和描述
3. 选中值保存到智能体定义的 `reply_style` 字段

#### 8.1.7 业务页面配置（business_pages）（Phase 3）

**现状**：

- **数据库**：`subagent_definitions` 表已有 `business_pages JSONB` 字段
- **模型**：`SubagentConfig` 已有 `business_pages: Optional[List[Dict[str, Any]]]` 字段
- **YAML 来源**：`SUBAGENT.md` frontmatter 中定义 `business_pages` 数组
- **API**：`CreateDefinitionRequest` / `UpdateDefinitionRequest` 已支持 `business_pages` 读写
- **运行时**：`MenuSidebar.vue` 已根据 `business_pages` 渲染侧边栏菜单
- **注册表**：`get_all_subagents_with_type()` 已包含 `business_pages`

**缺失**：`AgentDefinitionManager.vue` 定义区无 business_pages 编辑区，管理员无法配置智能体的业务页面。

**方案**：在定义区增加业务页面列表编辑功能，支持增删改页面条目。

**数据结构**：

```json
// subagent_definitions.business_pages 字段
[
    {"id": "vehicles", "title": "车辆价格", "icon": "🚐", "route": "/travel-consultant/vehicles"},
    {"id": "attractions", "title": "景点门票", "icon": "🏔️️", "route": "/travel-consultant/attractions"}
]
```

**前端交互**：

```
定义区 — 业务页面配置区：
┌──────────────────────────────────────────────────┐
│  业务页面：                              [+ 添加] │
│                                                  │
│  🚐 车辆价格 → /travel-consultant/vehicles  [编辑][删除] │
│  🏔️ 景点门票 → /travel-consultant/attractions [编辑][删除] │
│  🏨 酒店住宿 → /travel-consultant/hotels    [编辑][删除] │
│                                                  │
│  添加/编辑弹窗（BaseModal）：                     │
│  ┌─────────────────────────────────────┐         │
│  │  页面ID：[vehicles          ] *     │         │
│  │  页面名称：[车辆价格        ] *     │         │
│  │  图标：[🚐                ]         │         │
│  │  路由：[/travel-consultant/vehicles] *│        │
│  │                    [取消]  [保存]   │         │
│  └─────────────────────────────────────┘         │
└──────────────────────────────────────────────────┘
```

**后端无改动**，`business_pages` 字段的读写已在 Phase 2 实现。只需前端增加编辑区。

#### 8.1.8 System Prompt 模板 + 分段变量（Phase 3）

**问题**：当前 DB 定义的子智能体，其 system_prompt 存储在 `prompt_versions` 表中作为一整块文本。管理员面对巨大的 textarea 编辑，内容难以管理和优化。不同类型的信息（身份、流程、约束）混在一起，局部修改困难。

**方案**：`prompt_versions.content` 存储的是**模板**（含 `{section_key}` 变量占位符），`subagent_prompt_sections` 存储的是**变量值**。运行时从 DB 实时读取模板和变量值，通过 `render_template()` 渲染为完整 system_prompt。

**核心设计**：

- **模板**：`prompt_versions.content` 中的 system_prompt 包含 `{变量名}` 占位符（复用现有 `render_template()` + `_SafeDict`）。模板本身有版本管理。
- **分段变量**：`subagent_prompt_sections` 表存储每个 `{变量名}` 的实际内容值。变量数量可变（不硬编码 5 个），由模板决定。
- **运行时实时渲染**：`_build_system_prompt()` 每次从 DB/Redis 读取模板 + 变量值 → 渲染 → 返回。修改分段值后无需重启。

**数据模型**：

```sql
-- 分段变量值表（每个 {section_key} 对应一行）
CREATE TABLE IF NOT EXISTS subagent_prompt_sections (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id    TEXT NOT NULL,
    section_key TEXT NOT NULL,
    content     TEXT DEFAULT '',
    updated_by  TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(agent_id, section_key)
);
```

**模板示例**：

```markdown
## 角色描述

{role_description}

## 岗位职责

{responsibilities}

## 工作流程

{workflow}

## 回复风格

{reply_style}

## 其他说明

{other_notes}
```

管理员可以自由增减 `{变量名}`，系统自动检测模板中的变量并在前端生成对应的编辑区。

**运行时渲染流程**：

```
Agent._build_system_prompt(user)
  │
  ├── subagent_config.from_db == True
  │     │
  │     ▼
  │   _resolve_db_subagent_prompt()
  │     │
  │     ├── prompt_resolver.resolve() → 获取模板（Redis 缓存）
  │     ├── SubagentPromptSectionDB.get_sections_map() → 获取变量值（Redis 缓存）
  │     └── render_template(template, section_map) → 渲染为完整文本
  │
  ├── subagent_config.from_db == False（文件系统）
  │     直接使用 config.system_prompt（一整块文本，不变）
  │
  └── 后续逻辑不变：加载知识库约束 → 加载 extra_md → 渲染模板
```

**缓存与实时生效**：

- 模板走现有 `prompt_resolver.resolve()` 的缓存（TTL 600s）
- 变量值走 `prompt_sections:{agent_id}` 缓存（TTL 300s）
- 编辑保存变量值后主动刷新缓存，实现**实时生效**
- 编辑模板后通过 prompt_resolver 的缓存失效机制自动刷新

**后端 API**：

```
# 变量值 CRUD
GET    /api/admin/agent-definitions/{agent_id}/sections/keys     → 从模板解析变量名列表
GET    /api/admin/agent-definitions/{agent_id}/sections          → 获取所有变量值
PUT    /api/admin/agent-definitions/{agent_id}/sections/{key}    → 保存变量值

# AI 智能优化
POST   /api/admin/agent-definitions/{agent_id}/sections/{key}/optimize → LLM 优化变量内容
```

**前端编辑界面**：

`AgentDefinitionManager.vue` 右侧 Prompt 区分为两部分：

1. **模板编辑区**：textarea 编辑含 `{变量名}` 的模板文本，支持版本管理（保存草稿/提交新版本/版本历史/对比/回滚）
2. **变量值编辑区**：根据模板中解析出的 `{变量名}` 动态生成编辑区，每个变量一个 textarea + AI 优化按钮，从上到下按模板中的出现顺序排列

```
┌─ Prompt 区 ──────────────────────────────────────────────────┐
│  模板编辑（含版本历史）          │  版本历史                    │
│  ┌──────────────────────────┐  │  ┌──────────────────┐      │
│  │ ## 角色描述               │  │  │ V3 prod          │      │
│  │ {role_description}       │  │  │ V2               │      │
│  │ ## 岗位职责               │  │  │ V1               │      │
│  │ {responsibilities}        │  │  │ [对比] [回滚]     │      │
│  │ ...                       │  │  └──────────────────┘      │
│  │ [保存草稿] [提交新版本]    │  │                             │
│  └──────────────────────────┘  │                             │
│  变量值编辑（从模板解析）       │                             │
│  ┌──────────────────────────┐  │                             │
│  │ role_description  [AI优化]│  │                             │
│  │ ┌──────────────────────┐ │  │                             │
│  │ │ 你是一名行程规划师... │ │  │                             │
│  │ └──────────────────────┘ │  │                             │
│  │ responsibilities [AI优化] │  │                             │
│  │ ┌──────────────────────┐ │  │                             │
│  │ │ 1. 帮客户规划行程... │ │  │                             │
│  │ └──────────────────────┘ │  │                             │
│  │ [全部保存]                │  │                             │
│  └──────────────────────────┘  │                             │
└──────────────────────────────────────────────────────────────┘
```

**LLM 智能优化**：

每个变量编辑区提供"AI 优化"按钮。点击后后端调用 LLM 基于变量名、子智能体名称和用途、当前内容进行优化。管理员确认后覆盖原内容。

**设计决策**：

- **模板与变量分离**：模板是"结构"，变量是"内容"。结构变化走版本管理，内容变化实时生效
- **变量数量可变**：不硬编码 5 个板块，管理员在模板中自由添加 `{new_section}` 变量
- **与 prompt_versions 共存**：`prompt_versions.content` 存模板（含占位符），版本管理不变
- **独立于 extra_md**：extra_md 是租户级定制，仍由 Phase 4 实现
- **文件系统子智能体不受影响**：`from_db=False` 的子智能体继续用整块 system_prompt

**与现有子智能体创建/编辑流程的关系**：

- **创建子智能体**时，输入初始模板（含 `{变量名}` 占位符），提交为 V1
- **编辑模板**时，修改模板文本，提交新版本
- **编辑变量值**时，修改 `subagent_prompt_sections` 表，实时生效
- **`SubagentRegistry.load_from_db()`** 和 **`AgentFactory._load_single_from_db()`** 的 `system_prompt` 字段存模板内容，运行时通过 `_resolve_db_subagent_prompt()` 实时渲染

> 改造目标不变：将 extra_md 从文件系统迁移到数据库，纳入版本管理，并在租户前台提供编辑界面。

**当前逻辑**：

- 存储：`storage/subagents/<dir_name>/extra_<tenant_id>.md`（文件系统）
- 后端 API：`/api/v1/subagents/{name}/extra`（GET/PUT/DELETE）已实现
- 前端 UI：**不存在**，租户无法自行编辑定制内容
- 运行时加载：`Agent._load_extra_md()` 从文件读取

**改造目标**：将 extra_md 完全从文件系统迁移到数据库，纳入版本管理，并在租户前台提供编辑界面。

**改造后的数据流**：

```
租户管理员在租户前台编辑 extra_md
    │
    ▼
前端调用 PUT /api/prompts/{prompt_id}/draft → 保存草稿
前端调用 POST /api/prompts/{prompt_id}/draft/commit → 提交新版本
    │
    ▼
prompt_registry 记录：scope=tenant_extra, scope_id=extra:{dir_name}:{tenant_id}
prompt_versions 记录：content=编辑内容, version=N
prompt_labels 记录：label=production, version_id=最新提交
    │
    ▼
Agent._load_extra_md() 改造为从 PromptResolver 获取
```

**scope_id 映射**：`extra:{subagent_dir_name}:{tenant_id}`

**后端改造**：

```python
# agent.py _load_extra_md() 改造
async def _load_extra_md(self, dir_name=None, tenant_id=None):
    dir_name = dir_name or self.subagent_config.dir_name
    tenant_id = tenant_id or self._init_tenant_id or get_current_tenant_id()

    if not tenant_id or not dir_name:
        return None

    # 从数据库获取 production 版本
    resolved = await prompt_resolver.resolve(
        scope="tenant_extra",
        scope_id=f"extra:{dir_name}:{tenant_id}",
        tenant_id=tenant_id
    )
    return resolved.content if resolved else None
```

**现有 API 迁移**：`src/api/subagent_extra.py` 的 GET/PUT/DELETE 接口改为调用 PromptRegistryService，文件系统逻辑全部移除。

### 8.3 前端改造

#### 8.3.1 平台管理后台：独立智能体管理页面

**新建页面**：`AgentDefinitionManager.vue`，路由 `/portal/agent-definitions`

**完全独立于旧的 DigitalEmployeeManager.vue**（`/portal/subagents`），不从旧页面改造。

```
┌────────────────────────────────────────────────────────────────┐
│  子智能体编辑 — {name}                               [保存] │
├────────────────────────────────────────────────────────────────┤
│                                                                │
│  ┌─ 定义区（YAML 配置）───────────────────────────────────┐  │
│  │  名称：[售后服务助手          ]                         │  │
│  │  描述：[处理售后...           ]                         │  │
│  │                                                        │  │
│  │  工具配置：                    [🤖 智能推荐]            │  │
│  │  ☑ 继承默认工具  额外工具：[http_api ▼] [+添加]        │  │
│  │                                                        │  │
│  │  技能配置：                    [🤖 智能推荐]            │  │
│  │  已选技能：[after-sales-core] [+添加]                   │  │
│  │                                                        │  │
│  │  知识库关联：                                           │  │
│  │  ☑ file — 上传的文档资料                               │  │
│  │  ☐ attraction_resource — 景点资源数据                  │  │
│  │  [查看可用 source_type]                                │  │
│  │                                                        │  │
│  │  回复风格：[▼ 拟人风格 — 以真人同事口吻回复 ]            │  │
│  │                                                        │  │
│  │  业务页面：                                  [+ 添加]   │  │
│  │  🚐 车辆价格 → /travel-consultant/vehicles  [编辑][删除] │  │
│  │  🏔️ 景点门票 → /travel-consultant/attractions [编辑][删除] │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                                │
│  ┌─ Prompt 区（System Prompt）───────────────────────────┐  │
│  │  ┌─────────────────────┐ ┌─────────────────────────┐  │  │
│  │  │  Markdown 编辑器     │ │  版本历史面板            │  │  │
│  │  │                     │ │  V3 (production) 👈当前  │  │  │
│  │  │  你是一个售后服务    │ │  V2  2026-06-01 admin   │  │  │
│  │  │  助手。你的任务是... │ │  V1  2026-05-28 admin   │  │  │
│  │  │                     │ │                         │  │  │
│  │  │                     │ │  [版本对比] [回滚]       │  │  │
│  │  └─────────────────────┘ └─────────────────────────┘  │  │
│  │                                                        │  │
│  │  变更说明：[优化退换货流程说明       ]                  │  │
│  │  [保存草稿]  [提交新版本]                              │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                                │
└────────────────────────────────────────────────────────────────┘
```

**交互流程**：

1. **定义区修改**：直接修改 name、description、tools、skills 等配置，点击"保存"即时生效
2. **Prompt 区修改**：编辑 System Prompt → 自动保存草稿 → 填写变更说明 → 提交新版本
3. **智能推荐**：在工具/技能配置区点击"智能推荐"，LLM 根据子智能体用途自动推荐配置
4. **版本管理**：右侧版本历史面板展示版本时间线，支持 diff 对比和一键回滚

#### 8.3.2 租户前台：定制 Prompt 编辑器

在租户前台 `/t/{tenant_id}` 的数字员工管理页面中，每个已启用的子智能体卡片增加「**定制提示词**」入口。

**编辑界面功能**：

1. **Markdown 编辑器**：编辑租户定制 Prompt 内容（对应 extra_md 内容）
2. **版本历史面板**：展示版本时间线（版本号、提交时间、提交人、变更说明）
3. **版本对比视图**：两个版本并排 diff 展示
4. **回滚按钮**：一键回滚到任意历史版本
5. **草稿自动保存**：编辑内容自动保存为草稿，避免丢失
6. **变更说明**：提交新版本时要求填写变更说明

**路由设计**：

```
/t/{tenant_id}/agent/{subagent_name}/prompt    → 租户定制 Prompt 编辑页面
```

#### 8.3.3 高危 Prompt 编辑流程（强制约束）

编辑子智能体 System Prompt 时，影响全局所有租户，必须强制走 staging 验证：

1. **编辑**：在 Markdown 编辑器中修改内容，自动保存为草稿
2. **提交到 staging**：点击"提交测试版本"按钮，系统创建新版本并标记为 `staging`。前端展示醒目提示：
   > ⚠️ 此修改将影响所有租户的对话效果。请务必在 staging 环境验证后再上线。
3. **staging 验证**：管理员在测试环境中使用 staging 版本进行对话测试
4. **上线 production**：验证通过后，点击"确认上线"按钮。**前端二次确认弹窗**
5. **上线失败回滚**：上线后发现异常，可一键回滚到上一个 production 版本

**后端强制校验**：

```python
async def set_label(prompt_id: str, label: str, version_id: str):
    prompt = get_prompt(prompt_id)

    # 高危 Prompt：禁止直接标记 production
    if label == "production" and prompt.scope in ("system_template", "subagent"):
        staging_label = get_label(prompt_id, "staging")
        if not staging_label or staging_label.version_id != version_id:
            raise HTTPException(
                status_code=400,
                detail="系统级 Prompt 必须先标记为 staging 并验证后才能上线 production"
            )

    # 设置标签
    ...
```

#### 8.3.4 与现有组件的关系

| 现有组件 | 改造内容 |
|---------|---------|
| `DigitalEmployeeManager.vue` | 编辑页面改造为两区分离（定义区 + Prompt 区），增加智能推荐按钮和版本管理面板 |
| `src/api/admin_subagent.py` | 新增 `POST /suggest-config` 智能推荐 API |
| `src/api/subagent_extra.py` | API 实现改为调用 PromptRegistryService |
| 新增：`PromptVersionHistory.vue` | 版本历史面板（可复用组件） |
| 新增：`PromptDiffView.vue` | 版本对比视图（可复用组件） |
| 新增：`TenantPromptEditor.vue` | 租户前台 Prompt 编辑器（Markdown 编辑 + 版本管理） |

### 8.4 文件系统同步

当通过数据库 API 修改 Prompt 时，需要同步写入文件系统（保证多 Worker 加载的一致性）：

```python
async def commit_version(prompt_id: str, commit_message: str):
    # 1. 写入数据库
    version = create_version_in_db(prompt_id, content, commit_message)

    # 2. 同步写入文件系统
    if scope == "subagent":
        write_subagent_md(scope_id, content)
    elif scope == "tenant_extra":
        write_extra_md(...)

    # 3. 刷新缓存
    prompt_cache.invalidate_label(prompt_id, "production")
```

---

## 九、A/B 测试设计（二期）

### 9.1 实验模型

```sql
CREATE TABLE IF NOT EXISTS prompt_experiments (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     TEXT,
    prompt_id     UUID NOT NULL,
    name          TEXT NOT NULL,
    status        TEXT DEFAULT 'draft',         -- 'draft' | 'running' | 'completed' | 'cancelled'
    variants      JSONB NOT NULL,               -- [{"version": 3, "label": "control"}, {"version": 5, "label": "treatment"}]
    traffic_config JSONB,                        -- {"type": "random", "weights": [0.5, 0.5]}
    started_at    TIMESTAMP,
    ended_at      TIMESTAMP,
    result        JSONB,                         -- 实验结果统计
    created_by    TEXT,
    created_at    TIMESTAMP DEFAULT NOW()
);
```

### 9.2 流量分割

```python
class ExperimentRouter:
    """实验路由器 — 决定当前请求使用哪个 Prompt 版本"""

    def resolve_version(self, prompt_id: str, user_id: str) -> int:
        """
        基于用户 ID 的确定性哈希分割：
        - 同一用户始终看到同一版本
        - 按 weights 比例分配
        """
        experiment = self._get_running_experiment(prompt_id)
        if not experiment:
            return self._get_production_version(prompt_id)

        hash_value = int(hashlib.md5(f"{prompt_id}:{user_id}".encode()).hexdigest(), 16)
        bucket = hash_value % 100

        cumulative = 0
        for variant, weight in zip(experiment.variants, experiment.traffic_weights):
            cumulative += weight * 100
            if bucket < cumulative:
                return variant["version"]

        return experiment.variants[0]["version"]
```

### 9.3 指标收集

实验期间自动收集以下指标：

| 指标 | 来源 | 说明 |
|------|------|------|
| 对话满意度 | 用户反馈 / 会话评分 | 用户主观评价 |
| 平均对话轮数 | 会话消息数 | 越少越好（效率） |
| 工具调用成功率 | 工具执行日志 | 工具调用失败率 |
| 任务完成率 | 会话状态 | 是否达到预期目标 |
| 平均响应时间 | 链路追踪 | LLM 调用耗时 |
| Token 消耗 | LLM 调用日志 | 成本指标 |

---

## 十、效果评估 Pipeline 设计（二期）

### 10.1 评估架构

```
评估数据集 (JSONL)
    │
    ▼
┌────────────────────────────┐
│  评估触发器                │
│  - 手动触发                │
│  - 版本提交时自动触发       │
│  - 定时批量评估            │
└────────────┬───────────────┘
             │
             ▼
┌────────────────────────────┐
│  评估 Pipeline             │
│  1. 加载测试数据集          │
│  2. 对每条数据生成回复      │
│  3. 调用 LLM-as-Judge 评分 │
│  4. 汇总指标               │
└────────────┬───────────────┘
             │
             ▼
┌────────────────────────────┐
│  评估结果存储               │
│  - 评分明细                 │
│  - 聚合指标                 │
│  - 版本对比                 │
└────────────────────────────┘
```

### 10.2 评估指标

使用 LLM-as-Judge 方案，对每个对话回复评分：

| 维度 | 指标 | 评分范围 | Prompt 要点 |
|------|------|---------|------------|
| 相关性 | relevance | 1-5 | 回复是否与问题相关 |
| 准确性 | accuracy | 1-5 | 引用内容是否正确 |
| 完整性 | completeness | 1-5 | 是否完整回答了问题 |
| 安全性 | safety | 1-5 | 是否包含敏感/有害内容 |
| 简洁性 | conciseness | 1-5 | 是否简洁不冗余 |

### 10.3 评估数据集

```sql
CREATE TABLE IF NOT EXISTS prompt_eval_datasets (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   TEXT,
    name        TEXT NOT NULL,
    description TEXT,
    data        JSONB NOT NULL,              -- [{"question": "...", "expected_answer": "...", "context": "..."}]
    created_by  TEXT,
    created_at  TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS prompt_eval_results (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    prompt_id   UUID NOT NULL,
    version     INTEGER NOT NULL,
    dataset_id  UUID NOT NULL,
    scores      JSONB NOT NULL,              -- {"relevance": 4.2, "accuracy": 3.8, ...}
    details     JSONB,                       -- 每条数据的评分明细
    created_at  TIMESTAMP DEFAULT NOW()
);
```

---

## 十一、实施计划

> 详细开发计划见 [prompt-lifecycle-dev-plan.md](./prompt-lifecycle-dev-plan.md)

### 阶段一：Prompt 内容优化 + 版本管理基础（2 周）

> 目标：修正现有 Prompt 矛盾 + 建立版本管理数据库和服务层

### 阶段二：子智能体管理增强 + LLM 智能推荐（2 周）

> 目标：改造子智能体编辑页面为两区分离，增加 LLM 智能推荐工具/技能功能

### 阶段三：知识库关联配置 + 工具技能列表展示 + 回复风格 + business_pages（1.5 周）

> 目标：完善智能体定义配置——知识库关联（租户级）、回复风格、业务页面配置；前端展示可用工具/技能清单供选择
> 
> **已完成**：3.1 移除 capabilities、3.2 知识库关联配置（租户级表 + TenantMgmt 配置入口 + 运行时注入）

### 阶段四：extra_md 迁移 + 租户前台编辑器（2-3 周）

> 目标：租户定制 Prompt 从文件系统迁移到数据库 + 租户前台编辑器

---

## 十二、风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| 数据库与文件系统不一致 | 多 Worker 加载到不同 Prompt | 写入时同步双写 + 定期一致性检查 |
| 版本数量膨胀 | 数据库存储压力 | 设置版本保留策略（默认保留最近 100 个版本） |
| 缓存不一致 | 切换版本后仍使用旧版本 | 标签变更时主动失效缓存 + 短 TTL |
| 评估成本 | LLM-as-Judge 调用产生 Token 费用 | 支持采样评估（默认评估 10% 对话） |

---

## 十三、技术选型总结

| 组件 | 选型 | 理由 |
|------|------|------|
| 模板引擎 | f-string（`str.format_map`） | 现有方案，零额外依赖，零学习成本 |
| 版本存储 | PostgreSQL | 与现有架构一致，支持 JSONB |
| 缓存 | Redis (现有) | 与现有 RedisClient 复用 |
| Diff 算法 | `difflib.unified_diff` | Python 标准库，无需额外依赖 |
| 内容哈希 | SHA-256 | 版本去重 + 缓存 key |
| A/B 流量分割 | 一致性哈希 (MD5) | 确定性分割，同一用户同一版本 |
| 评估引擎 | LLM-as-Judge | 复用现有 LLM Gateway，无外部依赖 |
