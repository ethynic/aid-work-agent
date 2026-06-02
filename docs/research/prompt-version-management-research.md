# Prompt 版本管理与生命周期管理 -- 业界调研报告

> 调研日期：2026-05-28
> 调研范围：Langfuse、Dify、Coze Loop (ByteDance)、Microsoft Promptflow、OpenAI 生态

---

## 一、各平台 Prompt 管理架构详解

### 1. Langfuse -- 最成熟的 Prompt 版本管理系统

Langfuse 是目前业界 Prompt 管理最完善的平台，其架构横跨三层：共享类型与验证层（`packages/shared`）、领域服务层（`PromptService`）、两个 API 表面层（tRPC + REST）。

#### 1.1 数据模型

**核心设计决策：每个版本独立一行。** Prompt 表使用复合唯一约束 `(projectId, name, version)` 标识。

```sql
-- Prompt 表核心字段
CREATE TABLE prompts (
    id          UUID PRIMARY KEY,
    project_id  UUID NOT NULL,
    name        TEXT NOT NULL,          -- 项目内唯一，支持 "/" 分隔的文件夹路径
    version     INTEGER NOT NULL,      -- 按 (projectId, name) 自动递增
    type        TEXT DEFAULT 'text',   -- "text" | "chat"
    prompt      JSONB,                 -- text: 字符串; chat: ChatMessage[]
    labels      TEXT[],                -- 如 "production", "staging", "latest"
    tags        TEXT[],                -- 自由格式元数据
    config      JSONB,                 -- 模型参数覆盖 (temperature 等)
    commit_message TEXT,               -- 变更描述，最大 500 字符
    created_at  TIMESTAMP,
    updated_at  TIMESTAMP,
    UNIQUE (project_id, name, version)
);
-- GIN 索引用于 labels/tags 的包含查询
CREATE INDEX idx_prompt_labels ON prompts USING GIN (labels);
```

**辅助表：**

```sql
-- Prompt 依赖关系（用于 prompt composition）
CREATE TABLE prompt_dependencies (
    id           UUID PRIMARY KEY,
    parent_id    UUID REFERENCES prompts(id),  -- 注意：无外键约束，prompt 可独立删除
    child_name   TEXT NOT NULL,
    child_version INTEGER,
    created_at   TIMESTAMP
);

-- 受保护标签注册表（企业安全）
CREATE TABLE prompt_protected_labels (
    id         UUID PRIMARY KEY,
    project_id UUID NOT NULL,
    label      TEXT NOT NULL,
    -- 控制谁可以修改/移除该标签
);
```

#### 1.2 Prompt 类型系统

Zod 联合类型区分两种 prompt 格式：

| 类型 | 内容结构 | 用途 |
|------|---------|------|
| `text` | 简单字符串模板 | 系统指令、格式规则 |
| `chat` | `ChatMessage[]` (role/content 对) | 多轮对话模板 |

`CreatePromptSchema` 基于 `type` 字段的 Zod 联合类型，文本 prompt 的 `type` 可选（默认 `text`），对话 prompt 必须声明。

#### 1.3 标签 (Label) 与版本解析

**标签是环境部署的核心机制：**

- `"latest"` 标签 **自动分配** 给最新版本，用户无法手动操作
- `"production"` 标签是 **约定俗成但不受系统强制** 的标签
- SDK 解析优先级：`version` > `label` > 默认回退到 `"production"` 标签
- 标签验证规则：小写字母数字，可选 `_`、`-`、`.`，最大 36 字符

**受保护标签机制**：企业环境中可通过 `checkHasProtectedLabels` 限制谁可以操作 `production` 等关键标签。

#### 1.4 Prompt 组合 (Composition) -- 跨 Prompt 引用

这是 Langfuse 最具架构意义的特性：

```
@@@langfusePrompt:name=shared-instructions|label=production@@@
@@@langfusePrompt:name=format-rules|version=3@@@
```

- 正则解析：`/@@@langfusePrompt:(.*?)@@@/g`
- 参数用 `|` 分隔，支持 `name=X|version=Y` 或 `name=X|label=Y`
- **深度优先递归解析**，最大嵌套 5 层
- **循环依赖检测**：维护 `Set<string>` 防止循环引用
- 只有 text 类型 prompt 可以作为依赖项

#### 1.5 缓存策略 -- Epoch Key 模式

Langfuse 使用精妙的 **epoch key** 双层 Redis 缓存：

1. 每个项目维护一个 epoch token（随机十六进制字符串，TTL 7 天）
2. prompt 变更时，生成新 epoch，旧缓存自动孤立失效
3. **项目级失效**（非 prompt 级），避免 O(n) 扫描开销
4. 缓存命中/未命中通过 OpenTelemetry 指标追踪

#### 1.6 API 设计

| 端点 | 方法 | 用途 |
|------|------|------|
| `/api/public/prompts` | GET, POST | 列出 prompt 元数据（分页）；创建新版本 |
| `/api/public/prompts/:name` | GET, DELETE | 按 name 获取（版本/标签解析）；删除所有版本 |
| `/api/public/prompts/:name/:version` | PATCH | 更新特定版本的标签 |

#### 1.7 事件溯源

每次 prompt 写入触发 `promptChangeEventSourcing`，将事件入队到 BullMQ 的 `EntityChangeQueue`，异步通知下游消费者（webhook、审计日志、分析管道）。

---

### 2. Dify -- 应用级版本管理（非独立 Prompt 版本化）

Dify 采用了不同的策略：**没有独立的 prompt 版本管理系统**。Prompt 作为 App 配置的一部分进行管理。

#### 2.1 版本管理架构

Dify 将应用分为两个架构家族：

**EasyUI 应用**（Chat、Completion、Agent Chat）：
- 配置存储在 `AppModelConfig` 表中
- 每个 App 持有一个 `app_model_config_id` 外键
- AppModelConfig 包含：`model` (JSON)、`pre_prompt`、`chat_prompt_config`、`completion_prompt_config`、`agent_mode` 等

```python
class AppModelConfig(TypeBase):
    __tablename__ = "app_model_configs"
    id: Mapped[str]              # UUID
    app_id: Mapped[str]          # 所属 App
    provider: Mapped[str]        # 模型提供商
    model_id: Mapped[str]        # 模型 ID
    model: Mapped[str]           # JSON: 模型配置
    pre_prompt: Mapped[str]      # 系统提示词
    chat_prompt_config: Mapped[str]    # JSON: 聊天提示词配置
    completion_prompt_config: Mapped[str]  # JSON: 补全提示词配置
    agent_mode: Mapped[str]      # JSON: Agent 模式配置
    prompt_type: Mapped[PromptType]    # "simple" | "advanced"
```

**Workflow 应用**（Advanced Chat、Workflow、Pipeline）：
- Workflow 表实现 Draft/Published 模式

```python
class Workflow(Base):
    __tablename__ = "workflows"
    id: Mapped[str]              # UUID
    tenant_id: Mapped[str]
    app_id: Mapped[str]
    type: Mapped[WorkflowType]   # "workflow" | "chat"
    version: Mapped[str]         # "draft" 或版本号（时间戳字符串）
    graph: Mapped[str]           # JSON: 完整画布配置（nodes + edges）
    features: Mapped[str]        # JSON: 功能开关
    marked_name: Mapped[str]     # 版本标记名称
    marked_comment: Mapped[str]  # 版本标记说明

    VERSION_DRAFT = "draft"      # 每个 App 只有一个 draft 版本
```

#### 2.2 版本发布流程

Dify 的版本管理核心是 **Draft -> Publish 模式**：

1. 每个 App 只有一个 `version="draft"` 的 Workflow
2. 用户在 draft 上编辑
3. 发布时：将 draft 复制为新版本（`version = str(datetime)`）
4. 生产环境使用最新的非 draft 版本
5. 版本列表可查看历史，可回滚

**关键限制**：Dify 不支持独立的 prompt 版本管理、不支持 prompt 组合、不支持 A/B 测试。

---

### 3. Coze Loop (ByteDance) -- Git 风格的 Prompt 版本控制

Coze Loop 是字节跳动开源的 AI Agent 优化平台，采用 **Git 风格** 的 prompt 版本管理，是最接近软件开发版本控制范式的实现。

#### 3.1 数据模型

```go
// 核心实体
type Prompt struct {
    ID           int64
    SpaceID      int64          // 工作空间 ID
    PromptKey    string         // 唯一标识符
    PromptBasic  *PromptBasic   // 基础信息
    PromptDraft  *PromptDraft   // 草稿版本
    PromptCommit *PromptCommit  // 提交版本
}

type PromptBasic struct {
    PromptType        PromptType  // "normal" | "snippet"
    DisplayName       string
    Description       string
    LatestVersion     string      // 最新提交版本号
    CreatedBy         string
    UpdatedBy         string
}

type PromptDraft struct {
    PromptDetail *PromptDetail
    DraftInfo    *DraftInfo       // 包含 BaseVersion、IsModified 等
}

type PromptCommit struct {
    PromptDetail *PromptDetail
    CommitInfo   *CommitInfo      // 包含 Version、BaseVersion、Description 等
}

type CommitInfo struct {
    Version     string        // 提交版本号
    BaseVersion string        // 基于哪个版本
    Description string        // 提交描述（类似 commit message）
    CommittedBy string
    CommittedAt time.Time
}

// Label 系统（类似 Git tag）
type PromptLabel struct {
    ID        int64
    SpaceID   int64
    LabelKey  string         // 标签名称
    CreatedBy string
}
```

#### 3.2 版本解析优先级

Coze Loop 支持两种版本查询方式，通过 `MParseCommitVersion` 统一处理：

1. **版本号查询**：`version="v1.2.3"`
2. **标签查询**：`label="production"`
3. **空版本号**：自动获取 `LatestVersion`（最新提交版本）

标签与版本的映射存储在独立表中，支持批量查询和缓存。

#### 3.3 Prompt 模板引擎

Coze Loop 支持 **四种模板引擎**：

```go
const (
    TemplateTypeNormal          = "normal"          // {{variable}} 简单替换
    TemplateTypeJinja2          = "jinja2"          // Jinja2 模板引擎
    TemplateTypeGoTemplate      = "go_template"     // Go 模板引擎
    TemplateTYpeCustomTemplateM = "custom_template_m"
)
```

变量类型系统非常丰富：

```go
const (
    VariableTypeString       = "string"
    VariableTypePlaceholder  = "placeholder"   // 插槽式消息注入
    VariableTypeBoolean      = "boolean"
    VariableTypeInteger      = "integer"
    VariableTypeFloat        = "float"
    VariableTypeObject       = "object"
    VariableTypeArrayString  = "array<string>"
    VariableTypeMultiPart    = "multi_part"    // 多模态内容
)
```

#### 3.4 Snippet（代码片段）系统 -- Prompt 组合

类似 Langfuse 的 Composition，但实现更完善：

- PromptBasic 支持两种类型：`normal`（完整 prompt）和 `snippet`（可复用片段）
- PromptTemplate 有 `HasSnippets` 标记和 `Snippets` 列表
- 支持最大 2 层嵌套的 snippet 展开
- Snippet 引用在内容中通过特殊语法标记
- 验证：snippet 引用必须指向 `PromptTypeSnippet` 类型的 prompt

#### 3.5 评估系统

Coze Loop 有独立的评估模块 (`backend/modules/evaluation/`)，支持：

- **评估器类型**：Prompt 评估器、Code 评估器、Agent 评估器、自定义 RPC 评估器
- **评估集** (`EvaluationSet`)：带版本的数据集管理
- **实验管理** (`Experiment`)：包含运行、结果聚合、洞察分析
- **评估目标**：支持内置 Coze Bot、Coze Workflow、Loop Prompt、自定义 RPC、Volcengine Agent

---

### 4. Microsoft Promptflow -- DAG 流水线中的变体管理

Promptflow 采用 **YAML 文件 + Git** 的版本管理方式，核心是 flow 级别的变体系统。

#### 4.1 变体 (Variant) 系统

变体是 LLM 节点的不同配置版本：

```yaml
nodes:
- name: summarize_text
  use_variants: true

node_variants:
  summarize_text:
    default_variant_id: variant_0
    variants:
      variant_0:
        node:
          type: llm
          source:
            type: code
            path: summarize_text.jinja2
          inputs:
            temperature: '0.2'
            text: ${fetch_text_content.output}
          connection: open_ai_connection
      variant_1:
        node:
          type: llm
          source:
            type: code
            path: summarize_text__variant_1.jinja2
          inputs:
            temperature: '0.7'
            text: ${fetch_text_content.output}
          connection: open_ai_connection
```

**关键设计**：
- 每个 LLM 节点可以有多个 variant
- 每个 variant 可以有不同的 prompt 模板文件和参数
- 通过 `--variant '${node_name.variant_name}'` 指定运行哪个变体
- YAML 文件通过 Git 进行版本控制

#### 4.2 评估框架

Promptflow 的 `promptflow-evals` 包提供完整的评估管线：

```python
from promptflow.evals.evaluate import evaluate
from promptflow.evals.evaluators import RelevanceEvaluator, CoherenceEvaluator

result = evaluate(
    data="test_data.jsonl",       # JSONL 格式测试数据
    evaluators={
        "coherence": CoherenceEvaluator(model_config=config),
        "relevance": RelevanceEvaluator(model_config=config),
    },
    evaluator_config={
        "coherence": {
            "answer": "${data.answer}",
            "question": "${data.question}"
        },
        "relevance": {
            "answer": "${data.answer}",
            "context": "${data.context}",
            "question": "${data.question}"
        }
    }
)
```

**内置评估指标**：

| 评估器 | 指标 | 说明 |
|--------|------|------|
| `RelevanceEvaluator` | relevance | 回答与问题的相关性 |
| `CoherenceEvaluator` | coherence | 回答的逻辑连贯性 |
| `FluencyEvaluator` | fluency | 回答的语言流畅性 |
| `GroundednessEvaluator` | groundedness | 回答是否基于提供的上下文 |
| `SimilarityEvaluator` | similarity | 与参考答案的相似度 |
| `F1ScoreEvaluator` | f1_score | F1 分数 |
| `BleuEvaluator` | bleu | BLEU 分数 |
| `RougeEvaluator` | rouge | ROUGE 分数 |
| 内容安全评估器 | violence/self_harm/sexual/hate | 安全性评分 |

**评估管线架构**：

1. 加载 JSONL 测试数据
2. 可选：通过 target 函数生成输出
3. 对每个评估器执行批量运行
4. 收集所有结果到 DataFrame
5. 聚合指标：计算均值、缺陷率等
6. 输出：`{rows: [...], metrics: {...}, studio_url: "..."}`

**聚合策略**：
- 通用指标：计算 `mean(numeric_only=True)`
- 内容安全指标：计算缺陷率（分数 >= 阈值的比例）
- 标签指标：计算 True 比例作为缺陷率

---

### 5. OpenAI 生态 -- Prompt 管理工具与服务

OpenAI 本身没有官方的 prompt 版本管理产品，但社区和生态中形成了清晰的实践模式。

#### 5.1 OpenAI 社区讨论的关键诉求

- 版本控制（保存、分支、回滚）
- 协作（多人编辑、评审）
- 实验（A/B 测试、参数对比）
- 可复用的 prompt 配置（消息、工具定义、模型配置的组合）

#### 5.2 Braintrust 的 Prompt 版本管理模式（2026 年标杆）

Braintrust 是 2026 年业界评价最高的 prompt 版本管理平台（94/100），其核心设计：

1. **内容可寻址 ID**：每个 prompt 变更生成唯一版本 ID（如 `5878bd218351fb8e`），相同内容 = 相同 ID
2. **环境部署**：dev/staging/production 环境关联不同版本，代码按环境名加载
3. **Playground**：PM 和工程师在同一界面测试、对比版本
4. **双向 Git 同步**：`braintrust pull` 下载为代码，Git PR 评审后 `braintrust push` 上传
5. **Trace 回溯**：生产 trace 关联到具体 prompt 版本，一键在 playground 复现
6. **A/B 测试**：为每个变体创建独立环境，路由流量后对比指标

---

## 二、通用模式与最佳实践

### 1. Prompt 模板引擎对比

| 模板引擎 | 适用场景 | 复杂度 | 代表平台 |
|----------|---------|--------|---------|
| **简单替换** (`{{var}}`) | 变量少、无逻辑 | 低 | Coze Loop (normal)、Langfuse |
| **Jinja2** | 条件/循环/过滤器 | 中 | Promptflow、Coze Loop |
| **Go Template** | Go 生态项目 | 中 | Coze Loop |
| **Mustache/Handlebars** | 无逻辑模板 | 低 | 少数项目 |
| **自定义 DSL** | 特殊需求 | 高 | Langfuse (`@@@...@@@`) |

**推荐**：对于企业级平台，采用 **Jinja2 作为主引擎**（表达力强、生态成熟），同时支持简单替换作为降级方案。

### 2. 版本管理模型对比

| 模型 | 代表平台 | 优点 | 缺点 |
|------|---------|------|------|
| **每版本独立行** | Langfuse | 简单、不可变、GIN 索引高效 | 行数膨胀 |
| **Draft/Commit** | Coze Loop | Git 风格直观 | 需要草稿管理逻辑 |
| **YAML + Git** | Promptflow | 与代码同步 | 缺少运行时版本管理 |
| **App 级配置快照** | Dify | 与应用耦合 | 不独立、不灵活 |
| **内容可寻址** | Braintrust | 确定性、可缓存 | 实现复杂 |

**推荐**：采用 **Draft/Commit 模型**（最接近开发者习惯），结合 **Label 系统**（环境部署）。

### 3. A/B 测试框架模式

#### 3.1 基于环境的路由（Braintrust 模式）

```
用户请求 → 路由层（按实验分组）→ 环境A (variant-a) / 环境B (variant-b)
→ 收集 metrics → 对比 → 提升 winner 到 production
```

#### 3.2 基于标签的流量分割（Langfuse 模式）

```
SDK 调用 get_prompt("name", label="experiment-a")
SDK 调用 get_prompt("name", label="experiment-b")
→ 按用户 hash 或随机分配 label
```

#### 3.3 基于变体的批量对比（Promptflow 模式）

```
pf run create --flow my_flow --variant '${node.variant_0}' --data test.jsonl
pf run create --flow my_flow --variant '${node.variant_1}' --data test.jsonl
→ 对比两个 run 的 metrics
```

### 4. 评估管线架构

#### 4.1 LLM-as-a-Judge 模式（Langfuse 实现）

```
                    ┌──────────────────┐
                    │  EvalTemplate    │
                    │  (prompt + vars) │
                    └────────┬─────────┘
                             │
┌──────────────┐    ┌────────▼─────────┐    ┌──────────────────┐
│  Trigger     │───▶│  Variable Mapping │───▶│  Compile Prompt  │
│  (trace/event│    │  (extract data)   │    │  ({{var}} replace)│
│   /dataset)  │    └──────────────────┘    └────────┬─────────┘
└──────────────┘                                      │
                                              ┌───────▼──────────┐
                                              │  Call LLM Judge   │
                                              │  (with schema)    │
                                              └───────┬──────────┘
                                                      │
                                              ┌───────▼──────────┐
                                              │  Validate Output  │
                                              │  (typed schema)   │
                                              └───────┬──────────┘
                                                      │
                                              ┌───────▼──────────┐
                                              │  Score Event      │
                                              │  (persist + trace)│
                                              └──────────────────┘
```

**关键设计模式**：
- **双重队列**：trace 评估 vs observation 评估分开队列，防止高吞吐饿死
- **依赖注入**：`EvalExecutionDeps` 接口解耦 DB/S3/Queue/LLM 四个关注点
- **负配置缓存**：无评估器配置的项目跳过 DB 查询（Redis 10 分钟 TTL）
- **保护机制**：阻塞（模型配置无效）、延迟（等 trace 完整）、采样（概率评估）

#### 4.2 评估指标分类

| 类别 | 指标 | 数据类型 | 聚合方式 |
|------|------|---------|---------|
| **LLM 评判** | relevance, coherence, groundedness, fluency | numeric (1-5) | mean |
| **内容安全** | violence, self_harm, sexual, hate | numeric (1-5) | defect_rate |
| **文本相似** | BLEU, ROUGE, F1, GLEU, METEOR | numeric | mean |
| **标签检测** | protected_material, xpia | boolean | defect_rate |
| **自定义** | 用户定义的分类/数值 | categorical/numeric | mean or defect_rate |

### 5. Prompt 注册与发现

#### 5.1 文件夹路径组织（Langfuse 模式）

```
prompts/
├── customer-service/
│   ├── greeting
│   ├── faq-response
│   └── complaint-handler
├── translation/
│   ├── en-to-zh
│   └── zh-to-en
└── shared/
    ├── format-rules
    └── safety-guidelines
```

- 用 `/` 分隔的路径编码文件夹层级
- SQL `LIKE` 条件匹配路径前缀
- UI 支持文件夹导航、批量操作

#### 5.2 标签系统（Coze Loop 模式）

- Label 类似 Git tag，指向特定版本
- 独立表存储 label -> version 映射
- 支持批量查询和缓存

### 6. 生命周期状态机

综合各平台实践，prompt 生命周期遵循以下状态机：

```
  ┌─────────┐   Save    ┌─────────┐   Commit   ┌──────────┐
  │  New     │──────────▶│  Draft  │───────────▶│ Committed│
  └─────────┘           └────┬────┘            └────┬─────┘
                             │                       │
                             │ Edit                  │ Tag
                             ▼                       ▼
                        ┌─────────┐            ┌──────────┐
                        │  Draft  │            │ Labeled  │
                        │(modified)│            │(staging/ │
                        └─────────┘            │production)│
                                               └──────────┘
```

---

## 三、对企业级 AI Agent 平台的适配建议

### 推荐的数据模型

综合 Langfuse 的每版本独立行 + Coze Loop 的 Draft/Commit + Langfuse 的 Label 系统：

```sql
-- Prompt 基础信息
CREATE TABLE prompt_registry (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     TEXT NOT NULL,
    prompt_key    TEXT NOT NULL,            -- 租户内唯一标识
    display_name  TEXT,
    description   TEXT,
    prompt_type   TEXT DEFAULT 'normal',    -- "normal" | "snippet"
    template_type TEXT DEFAULT 'jinja2',    -- "simple" | "jinja2"
    latest_version TEXT,
    created_by    TEXT,
    updated_by    TEXT,
    created_at    TIMESTAMP DEFAULT NOW(),
    updated_at    TIMESTAMP DEFAULT NOW(),
    UNIQUE (tenant_id, prompt_key)
);

-- Prompt 版本（每个版本独立一行，不可变）
CREATE TABLE prompt_versions (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    prompt_id      UUID NOT NULL REFERENCES prompt_registry(id),
    version        TEXT NOT NULL,             -- 语义化版本号，如 "v1.0.0"
    base_version   TEXT,                      -- 基于哪个版本
    content        TEXT NOT NULL,             -- prompt 模板内容
    model_config   JSONB,                     -- 模型参数覆盖
    tools          JSONB,                     -- 工具定义
    commit_message TEXT,                      -- 变更描述
    created_by     TEXT,
    created_at     TIMESTAMP DEFAULT NOW(),
    UNIQUE (prompt_id, version)
);

-- 标签系统（环境部署）
CREATE TABLE prompt_labels (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    prompt_id     UUID NOT NULL REFERENCES prompt_registry(id),
    version_id    UUID NOT NULL REFERENCES prompt_versions(id),
    label         TEXT NOT NULL,             -- "production", "staging", "experiment-a"
    created_by    TEXT,
    created_at    TIMESTAMP DEFAULT NOW(),
    updated_at    TIMESTAMP DEFAULT NOW(),
    UNIQUE (prompt_id, label)                -- 每个 prompt 每个标签只有一个版本
);

-- 依赖关系（prompt composition）
CREATE TABLE prompt_dependencies (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    parent_id       UUID NOT NULL REFERENCES prompt_versions(id),
    child_prompt_id UUID NOT NULL REFERENCES prompt_registry(id),
    child_version   TEXT,                     -- null 表示使用 label 解析
    child_label     TEXT,
    created_at      TIMESTAMP DEFAULT NOW()
);

-- 索引
CREATE INDEX idx_prompt_versions_prompt ON prompt_versions(prompt_id);
CREATE INDEX idx_prompt_labels_prompt_label ON prompt_labels(prompt_id, label);
```

### 推荐的 API 设计

```
POST   /api/prompts                              -- 创建 prompt
GET    /api/prompts                              -- 列出（分页、搜索、文件夹过滤）
GET    /api/prompts/{key}                        -- 获取（按 label 或 version 解析）
DELETE /api/prompts/{key}                        -- 删除所有版本

POST   /api/prompts/{key}/versions               -- 创建新版本（commit）
GET    /api/prompts/{key}/versions               -- 列出所有版本
GET    /api/prompts/{key}/versions/{version}     -- 获取特定版本

PATCH  /api/prompts/{key}/labels/{label}         -- 更新标签指向的版本
DELETE /api/prompts/{key}/labels/{label}         -- 删除标签

POST   /api/prompts/{key}/draft                  -- 保存草稿
GET    /api/prompts/{key}/draft                  -- 获取草稿
POST   /api/prompts/{key}/draft/commit           -- 将草稿提交为新版本

POST   /api/prompts/{key}/evaluate               -- 对指定版本运行评估
GET    /api/prompts/{key}/evaluations            -- 获取评估结果
```

### 推荐的评估架构

```
Dataset (JSONL) ──┐
                  │    ┌──────────────┐    ┌──────────────┐
Prompt Version ───┼───▶│ Evaluation   │───▶│ Score Storage│
(variant A)       │    │ Pipeline     │    │ (DB + Redis) │
                  │    └──────┬───────┘    └──────┬───────┘
Prompt Version ───┘           │                   │
(variant B)              ┌────▼─────┐        ┌────▼────┐
                         │ LLM Judge │        │ Metrics │
                         │ (G-Eval)  │        │ Compare │
                         └──────────┘         └─────────┘
```

**评估指标建议**：
- 主指标：relevance（相关性）、groundedness（事实性）、safety（安全性）
- 辅助指标：coherence（连贯性）、fluency（流畅性）、latency（延迟）
- 业务指标：用户满意度、任务完成率

---

## 四、参考资源

| 资源 | 链接 |
|------|------|
| Langfuse Prompt Management 源码 | [github.com/langfuse/langfuse](https://github.com/langfuse/langfuse) -- `packages/shared/src/server/services/PromptService/` |
| Langfuse PostgreSQL Schema | [schema.prisma](https://github.com/langfuse/langfuse/blob/main/packages/shared/prisma/schema.prisma) |
| Dify 架构 | [github.com/langgenius/dify](https://github.com/langgenius/dify) -- `api/models/workflow.py`, `api/models/model.py` |
| Coze Loop Prompt 模块 | [github.com/coze-dev/coze-loop](https://github.com/coze-dev/coze-loop) -- `backend/modules/prompt/` |
| Coze Loop 评估模块 | [github.com/coze-dev/coze-loop](https://github.com/coze-dev/coze-loop) -- `backend/modules/evaluation/` |
| Microsoft Promptflow | [github.com/microsoft/promptflow](https://github.com/microsoft/promptflow) -- `src/promptflow-evals/` |
| Braintrust Prompt 版本管理 | [braintrust.dev](https://www.braintrust.dev/articles/best-prompt-versioning-tools-2025) |
| LaunchDarkly Prompt 版本管理指南 | [launchdarkly.com](https://launchdarkly.com/blog/prompt-versioning-and-management/) |
| LLM-as-a-Judge 综合指南 | [evidentlyai.com](https://evidentlyai.com/llm-guide/llm-as-a-judge) |
| Arize AI LLM Judge 实践 | [arize.com](https://arize.com/llm-as-a-judge/) |
