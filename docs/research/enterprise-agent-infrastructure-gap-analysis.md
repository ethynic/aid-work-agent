# 企业级 2B 智能体平台基础设施建设差距分析报告

> 调研日期：2026-05-28
> 目标：评估当前系统距离"企业级 2B 智能体平台"的基础设施差距，明确建设优先级和路线图

---

## 一、执行摘要

### 1.1 项目现状

当前系统已构建了一个**功能覆盖面较广的企业 AI 智能体系统**，核心架构完整，包含：

- **LLM 网关**（熔断器 + KeyPool + Failover）—— 评估 A 级，架构成熟
- **智能体循环**（25+ 工具、技能系统、子智能体委托）—— 评估 A- 级
- **SaaS 多租户**（三级权限、Token 配额、订阅计费）—— 评估 B+ 级
- **知识库 RAG**（混合检索、pgvector、文档解析）—— 评估 B+ 级
- **渠道集成**（企业微信/钉钉/飞书）—— 评估 B- 级

### 1.2 核心结论

**距离企业级 2B 平台，需要在 8 大领域补强基础设施。** 按"企业真正买单"的角度排序：

| 优先级 | 领域 | 现状评级 | 目标评级 | 建设周期 | 核心价值 |
|--------|------|---------|---------|---------|---------|
| **P0** | 可观测性与质量保障 | ★☆☆☆☆ | ★★★★☆ | 4-6 周 | 企业不敢用看不见质量的 AI |
| **P0** | Prompt 全生命周期管理 | ★★☆☆☆ | ★★★★☆ | 3-4 周 | 智能体的核心资产需要版本化 |
| **P0** | 知识库能力增强 | ★★★☆☆ | ★★★★☆ | 4-5 周 | RAG 是企业最看重的功能 |
| **P1** | 安全与合规体系 | ★★☆☆☆ | ★★★★☆ | 6-8 周 | 企业采购的硬性门槛 |
| **P1** | 智能体编排引擎 | ★★★☆☆ | ★★★★☆ | 8-12 周 | 决定平台的天花板 |
| **P1** | 开放集成能力 | ★★☆☆☆ | ★★★★☆ | 4-6 周 | 融入企业现有 IT 生态 |
| **P2** | 运维与部署基础设施 | ★★☆☆☆ | ★★★★☆ | 4-6 周 | 私有化部署的基础 |
| **P2** | 开发者生态与体验 | ★☆☆☆☆ | ★★★☆☆ | 6-8 周 | 平台长期竞争力 |

---

## 二、详细差距分析

### 2.1 可观测性与质量保障（P0 —— 最高优先级）

> **方案设计**：[可观测性与质量保障方案设计](../infrastructure/observability-design.md)
> **调研报告**：[AI Agent 平台可观测性设计调研](observability-design-research.md)

#### 为什么是企业第一优先级

企业采购 AI 平台，最大的顾虑不是功能多少，而是**"AI 给出的答案靠谱吗？""出了问题怎么排查？""成本花在哪了？"**。没有可观测性，企业就不敢把 AI 放到关键业务流程中。

#### 现状

| 能力 | 现状 | 评价 |
|------|------|------|
| LLM 调用日志 | JSONL 格式，含 request_id | 基础可用 |
| Token 统计 | 按对话/租户/月汇总 | 基础可用 |
| 错误日志管理 | 前端 ErrorLogs 页面 | 基础可用 |
| **链路追踪** | ❌ 无 | 无法追踪一次请求的完整路径 |
| **回复质量评估** | ❌ 无 | 不知道 AI 回答好不好 |
| **幻觉检测** | ❌ 无 | 无法发现 AI 编造内容 |
| **实时监控仪表盘** | ❌ 静态卡片 | 运维无法实时发现问题 |
| **告警系统** | 仅 LLM 层面有告警冷却 | 不覆盖业务层 |

#### 需要建设的基础设施

**（1）分布式链路追踪**

为每一次用户请求生成唯一 `trace_id`，贯穿整个处理链路：

```
用户消息 → [渠道适配] → [Agent Loop]
  → [LLM 调用] (span: llm_call)
  → [工具调用] (span: tool_call)
  → [知识库检索] (span: knowledge_retrieval)
  → [子智能体委托] (span: subagent_delegation)
← [响应构建] → [渠道回复]
```

每个 span 记录：`trace_id`, `span_id`, `parent_span_id`, `start_time`, `end_time`, `status`, `input_summary`, `output_summary`, `token_usage`, `error_info`

推荐方案：**自建轻量 Trace 系统**（PostgreSQL 存储 + 前端可视化），不引入 OpenTelemetry 全家桶（过重）。

**（2）回复质量自动评估**

每次对话结束后，异步触发质量评估 Pipeline：

- **相关性**：回复是否与用户问题相关（可用 LLM-as-Judge）
- **准确性**：引用知识库的内容是否正确（可对比原始文档片段）
- **完整性**：是否完整回答了用户的问题
- **安全性**：是否泄露了敏感信息

评估结果写入 `quality_scores` 表，用于：
- 前端质量仪表盘（按天/周/月趋势）
- 低分回复自动告警
- 按智能体维度的质量对比

**（3）实时监控仪表盘**

替换现有的静态卡片，构建 WebSocket 推送的实时仪表盘：

- **概览**：当前活跃会话数、QPS、平均响应时间、错误率
- **Token 消耗**：实时速率、按模型分布、成本估算
- **质量趋势**：满意度评分趋势、低分回复比例
- **工具调用**：各工具调用频次、成功率、平均耗时
- **渠道状态**：各渠道连接状态、消息积压量

**（4）结构化告警**

统一告警框架，支持多通道通知：

```python
class AlertRule:
    metric: str          # "error_rate", "response_time_p99", "quality_score"
    threshold: float
    window: str          # "5m", "1h"
    channels: list       # ["wecom", "email", "webhook"]
    cooldown: int        # 秒
```

---

### 2.2 Prompt 全生命周期管理（P0）

#### 为什么重要

Prompt 是智能体的核心资产。企业客户的实际需求是：
- 改了 Prompt 效果变差了，需要**回滚到上一个版本**
- 同一个智能体在不同客户场景下需要**不同的 Prompt 变体**
- 团队协作时需要知道**谁改了什么、为什么改**
- 上线新版本前需要**灰度测试**效果

#### 现状

| 能力 | 现状 | 评价 |
|------|------|------|
| Prompt 存储 | 文件系统（SUBAGENT.md / SKILL.md） | 无法版本化 |
| Prompt 编辑 | 前端文本编辑器 | 基础可用 |
| AI 完善 Prompt | 已实现 | 好功能 |
| **版本控制** | ❌ 无（依赖 Git） | 无法在运行时回滚 |
| **A/B 测试** | ❌ 无 | 无法对比不同 Prompt 效果 |
| **变量模板** | ❌ 无 | 无法参数化 Prompt |
| **效果评估** | ❌ 无 | 不知道改了之后效果如何 |

#### 需要建设的基础设施

**（1）Prompt 版本管理**

```sql
CREATE TABLE prompt_versions (
    id SERIAL PRIMARY KEY,
    agent_id TEXT NOT NULL,
    prompt_type TEXT NOT NULL,      -- 'system_prompt', 'skill_prompt', 'tool_guide'
    version INTEGER NOT NULL,
    content TEXT NOT NULL,
    variables JSONB,                -- 模板变量定义
    created_by TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    is_active BOOLEAN DEFAULT FALSE,
    change_note TEXT,               -- 变更说明
    parent_version INTEGER,         -- 从哪个版本 fork
    UNIQUE(agent_id, prompt_type, version)
);
```

核心能力：
- 每次编辑 Prompt 自动创建新版本（不可变）
- 一键回滚到任意历史版本
- 版本对比（diff）
- 变更说明强制填写

**（2）Prompt 模板引擎**

将当前的硬编码 Prompt 模板改为参数化模板：

```python
# 当前：硬拼接
system_prompt = f"你是{agent_name}。" + skills_desc + tools_guide

# 目标：模板引擎
template = PromptTemplate("""
你是 {{ agent_name }}，{{ agent_description }}。

## 可用工具
{% for tool in tools %}
- {{ tool.name }}: {{ tool.description }}
{% endfor %}

## 约束
{{ constraints | default("无特殊约束") }}
""")
```

支持：条件渲染、循环、默认值、变量注入。

**（3）Prompt A/B 测试**

```sql
CREATE TABLE prompt_experiments (
    id SERIAL PRIMARY KEY,
    agent_id TEXT NOT NULL,
    name TEXT NOT NULL,
    variants JSONB NOT NULL,        -- [{"version": 3, "weight": 0.5}, {"version": 4, "weight": 0.5}]
    status TEXT DEFAULT 'running',  -- 'draft', 'running', 'completed'
    started_at TIMESTAMP,
    ended_at TIMESTAMP,
    result JSONB                    -- 实验结果统计
);
```

核心能力：
- 按 50/50 或其他比例分配流量到不同 Prompt 版本
- 自动收集各版本的质量评分和用户反馈
- 实验结束后自动选出胜出版本

**（4）Prompt 市场/模板库**

- 预置高质量 Prompt 模板（按行业/角色分类）
- 企业可以上传/分享自己的 Prompt 模板
- 从模板一键创建新智能体

---

### 2.3 知识库能力增强（P0）

#### 为什么重要

知识库是企业最常用的 AI 功能。当前系统已有基础 RAG 实现（混合检索 + pgvector），但与行业标杆（FastGPT、Dify）相比，缺少几个关键能力。

#### 现状

| 能力 | 现状 | 评价 |
|------|------|------|
| 文档解析 | 支持 PDF/Word/Excel/PPT | 良好 |
| 向量检索 | pgvector HNSW | 良好 |
| 混合检索 | 向量 0.7 + FTS 0.3 RRF 融合 | 良好 |
| 租户隔离 | 有 | 良好 |
| **Rerank 重排** | ❌ 无 | 检索精度有提升空间 |
| **知识库权限** | 仅租户级隔离 | 缺少文档级权限 |
| **自动更新/同步** | ❌ 无 | 文档变更需手动重新上传 |
| **检索效果评估** | ❌ 无 | 不知道检索准不准 |
| **多轮对话记忆检索** | ❌ 无 | 长对话中上下文丢失 |
| **结构化数据问答** | ❌ 无 | 无法问答 Excel/数据库数据 |

#### 需要建设的基础设施

**（1）Rerank 重排序**

在当前混合检索之后，增加 Rerank 层提升精度：

```
用户查询 → 向量检索 Top-20 + FTS Top-20 → RRF 融合 Top-15 → Rerank Top-5 → 注入 LLM 上下文
```

可选方案：
- **轻量方案**：使用 LLM 做 point-wise 重排（调用成本低，效果可控）
- **标准方案**：接入 BGE-Reranker / Cohere Rerank API（效果好，有外部依赖）
- **推荐**：先实现 LLM-based Rerank（无外部依赖），后续按需接入专用 Rerank 模型

**（2）文档级权限控制**

```sql
ALTER TABLE documents ADD COLUMN access_level TEXT DEFAULT 'tenant';  -- 'tenant', 'department', 'user'
ALTER TABLE documents ADD COLUMN allowed_departments TEXT[];  -- 部门级权限
ALTER TABLE documents ADD COLUMN allowed_users TEXT[];        -- 用户级权限
```

检索时根据当前用户的部门/角色过滤文档。

**（3）知识库健康度检测**

定期自动评估知识库质量：
- 覆盖率：常见问题是否都能检索到相关文档
- 准确率：检索到的文档是否真正相关
- 时效性：文档是否过期（基于文档日期字段或内容分析）

**（4）结构化数据问答（Text-to-SQL）**

让用户通过自然语言查询结构化数据（Excel 表格、数据库表）：
- 自动识别查询意图（知识库问答 vs 数据查询）
- 数据查询走 Text-to-SQL 路径
- 结果以表格/图表形式展示

**（5）文档自动同步**

支持配置外部数据源，定期同步：
- 文件系统目录
- 企业网盘（企业微信文档、飞书文档）
- Web 页面（定期爬取）
- 数据库视图

---

### 2.4 安全与合规体系（P1）

#### 为什么重要

安全是企业采购的**硬性门槛**，不是加分项。特别是：
- 大型企业必须有 SSO（Active Directory / LDAP / OIDC 集成）
- 金融/政务/医疗行业需要等保认证
- 数据安全法要求完整的数据操作审计

#### 现状

| 能力 | 现状 | 评价 |
|------|------|------|
| 密码认证 | bcrypt 哈希 | 基础安全 |
| SMS 验证 | 有实现 | 可用 |
| 租户隔离 | 数据库级 tenant_id | 基础隔离 |
| 渠道签名验证 | 企业微信完整 | 渠道安全 |
| **SSO/OIDC 集成** | ❌ 无 | **最大短板** |
| **RBAC 细粒度权限** | ❌ 仅 3 个硬编码角色 | 无法满足企业需求 |
| **API Key 管理** | ❌ 无 | 无法管理第三方接入 |
| **操作审计日志** | ❌ 无 | 无法追溯安全事件 |
| **数据加密存储** | ❌ API Key 明文存储 | 安全风险 |
| **内容安全过滤** | ❌ 无 | 无法过滤敏感内容 |

#### 需要建设的基础设施

**（1）统一认证框架**

```python
# 认证策略模式
class AuthStrategy(ABC):
    @abstractmethod
    async def authenticate(self, request) -> User:
        pass

class PasswordAuth(AuthStrategy): ...     # 现有密码登录
class SMSAuth(AuthStrategy): ...          # 现有短信登录
class OIDCAuth(AuthStrategy): ...         # 新增：OIDC/SSO
class LDAPAuth(AuthStrategy): ...         # 新增：LDAP/AD
class APIKeyAuth(AuthStrategy): ...       # 新增：API Key
```

核心要求：
- 支持 OIDC（企业微信、钉钉、飞书的 OAuth2 登录）
- 支持 LDAP/Active Directory（大型企业必备）
- 支持 API Key 认证（开发者接入）
- 统一的 Session/Token 管理

**（2）RBAC 权限模型**

```sql
CREATE TABLE roles (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT,
    name TEXT NOT NULL,
    description TEXT,
    is_system BOOLEAN DEFAULT FALSE,  -- 系统内置角色不可删除
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE permissions (
    id SERIAL PRIMARY KEY,
    resource TEXT NOT NULL,   -- 'agent', 'knowledge', 'tool', 'user', 'settings'
    action TEXT NOT NULL,     -- 'read', 'write', 'delete', 'manage'
    scope TEXT DEFAULT 'own'  -- 'own', 'department', 'tenant', 'platform'
);

CREATE TABLE role_permissions (
    role_id INTEGER REFERENCES roles(id),
    permission_id INTEGER REFERENCES permissions(id),
    PRIMARY KEY (role_id, permission_id)
);

CREATE TABLE user_roles (
    user_id TEXT NOT NULL,
    role_id INTEGER REFERENCES roles(id),
    scope_id TEXT,            -- 部门/团队 ID，用于 department 级权限
    PRIMARY KEY (user_id, role_id)
);
```

预置角色：
- `platform_admin`：平台管理员（跨租户）
- `tenant_admin`：租户管理员（租户内全部权限）
- `department_admin`：部门管理员（部门内用户管理）
- `knowledge_manager`：知识库管理员（知识库读写）
- `agent_user`：普通用户（只能使用授权的智能体）

**（3）操作审计日志**

```sql
CREATE TABLE audit_logs (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    action TEXT NOT NULL,         -- 'login', 'create_agent', 'edit_prompt', 'delete_knowledge', ...
    resource_type TEXT NOT NULL,  -- 'agent', 'knowledge', 'user', 'settings'
    resource_id TEXT,
    details JSONB,                -- 变更详情（before/after）
    ip_address TEXT,
    user_agent TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_audit_logs_tenant_time ON audit_logs(tenant_id, created_at DESC);
CREATE INDEX idx_audit_logs_user ON audit_logs(user_id, created_at DESC);
```

需要审计的操作：
- 用户登录/登出
- 智能体创建/编辑/删除/Prompt 修改
- 知识库文档上传/删除
- 用户管理操作
- 系统配置变更
- 数据导出

**（4）内容安全**

- 输入过滤：敏感词检测、PII（个人隐私信息）检测
- 输出过滤：防止泄露企业敏感信息、防止生成有害内容
- 日志脱敏：存储的对话日志自动脱敏处理

**（5）数据加密**

- API Key、渠道密钥等敏感配置使用 AES-256 加密存储
- 数据库连接字符串加密
- 支持 KMS（密钥管理服务）集成

---

### 2.5 智能体编排引擎（P1）

#### 为什么重要

当前系统的智能体定义方式是 **"Prompt + 工具选择"** 模式（通过 SUBAGENT.md 文件），这在简单场景下足够，但面对复杂企业流程时暴露以下问题：

- 无法定义**条件分支**（如"如果客户级别是 VIP，转人工；否则自动处理"）
- 无法定义**循环/重试**（如"如果搜索无结果，换一个关键词重试"）
- 无法定义**并行执行**（如"同时查询库存和价格"）
- 无法定义**人工审批节点**（如"金额 > 1万需要主管审批"）
- 无法**可视化调试**（不知道智能体执行到了哪一步）

#### 行业趋势分析

| 平台 | 编排模式 | 复杂度 |
|------|---------|--------|
| Dify | 可视化 DAG 工作流 | 高 |
| Coze 2.0 | Expert Agent + 插件 | 中高 |
| 百度千帆 | 三种模式（自主规划/工作流/多智能体） | 高 |
| FastGPT | 简化工作流 | 中 |
| **本项目** | Prompt + 工具选择 | **低** |

#### 建设策略：渐进式增强，不追求一步到位

**阶段一：增强当前 Prompt 模式（4 周）**

不引入可视化 DAG，而是增强现有架构：

1. **结构化工作流定义**：在 SUBAGENT.md 中支持 YAML 定义流程步骤

```yaml
# subagents/order-processing/SUBAGENT.md
---
name: 订单处理智能体
workflow:
  steps:
    - id: classify
      type: llm
      prompt: "判断客户意图：查询订单/下单/退换货/投诉"
      output: intent

    - id: handle_query
      type: tool
      tool: search_order
      condition: "intent == '查询订单'"
      inputs:
        order_id: "{{ extract_from_message('order_id') }}"

    - id: handle_complaint
      type: delegate
      subagent: complaint-handling
      condition: "intent == '投诉'"

    - id: approval
      type: human_approval
      condition: "order_amount > 10000"
      message: "订单金额超过1万，需要主管审批"
      timeout: 3600

    - id: fallback
      type: llm
      prompt: "无法处理的请求，礼貌告知客户并转人工"
---
```

2. **WorkflowExecutor**：解析 YAML 定义，按步骤顺序执行，支持条件分支和人工审批

3. **执行状态追踪**：每步执行结果记录到数据库，前端可查看执行进度

**阶段二：可视化编排器（8-12 周）**

在阶段一基础上，构建前端可视化编辑器：
- 拖拽节点到画布
- 节点类型：LLM 节点、工具节点、条件节点、人工审批节点、子智能体节点
- 节点间连线定义数据流
- 前端使用 Vue Flow（基于 Vue 3 的流程图库）

**阶段三：高级编排能力（长期）**

- 并行执行节点
- 循环节点（带退出条件）
- 子工作流嵌套
- 错误恢复和断点续执行

---

### 2.6 开放集成能力（P1）

#### 为什么重要

企业 AI 平台不是孤岛，必须融入企业现有的 IT 生态：
- 与企业 OA 系统对接（审批、通知）
- 与企业 CRM/ERP 对接（数据查询、操作）
- 与企业 BI 系统对接（报表、数据可视化）
- 开发者需要 API 和 SDK 进行二次开发

#### 现状

| 能力 | 现状 | 评价 |
|------|------|------|
| REST API | 有基础 API | 可用 |
| SSE 流式 | 已实现 | 良好 |
| 渠道 Webhook | 企业微信完整 | 良好 |
| **OpenAPI 文档** | ❌ 无 | 开发者无法发现 API |
| **SDK** | ❌ 无 | 开发者需要自己封装 |
| **嵌入组件** | ❌ 无 | 无法嵌入到第三方页面 |
| **MCP 协议** | ❌ 无 | 无法接入 MCP 工具生态 |
| **Webhook 出站** | ❌ 无 | 无法主动推送事件 |

#### 需要建设的基础设施

**（1）OpenAPI 文档自动生成**

FastAPI 已支持自动 OpenAPI 文档，需要：
- 完善每个 API 的描述和参数文档
- 增加认证说明
- 提供在线调试界面（Swagger UI）

**（2）嵌入组件（Chat Widget）**

提供给企业客户的可嵌入聊天组件：

```html
<script src="https://your-domain.com/widget.js"
  data-agent-id="order-processing"
  data-tenant-id="company-a"
  data-theme="blue">
</script>
```

功能：
- 浮动气泡 / 侧边栏 / 全屏模式
- 自定义颜色/Logo/欢迎语
- SSO 免登录
- 对话历史持久化

**（3）Webhook 出站事件**

```python
# 可配置的事件推送
WEBHOOK_EVENTS = [
    "conversation.created",
    "conversation.completed",
    "message.created",
    "tool.executed",
    "approval.required",
    "error.occurred",
]
```

企业可以订阅这些事件，触发自己的业务流程（如 ERP 创建工单、OA 发起审批）。

**（4）MCP（Model Context Protocol）协议支持**

MCP 是 2025-2026 年的行业新标准，允许智能体通过统一协议访问外部工具和数据源：
- 实现 MCP Server：将当前系统的工具暴露为 MCP 资源
- 实现 MCP Client：让当前智能体可以访问外部 MCP 服务器
- 这是未来工具生态的标准接口

---

### 2.7 运维与部署基础设施（P2）

#### 现状

| 能力 | 现状 | 评价 |
|------|------|------|
| Docker 部署 | 有 Dockerfile | 基础可用 |
| Gunicorn 多 Worker | 已配置 | 良好 |
| 数据库迁移 | 基于文件哈希的幂等迁移 | 可用 |
| **Kubernetes** | ❌ 无 | 无法弹性伸缩 |
| **配置中心** | ❌ 本地文件 | 无法集中管理 |
| **灰度发布** | ❌ 无 | 上线风险高 |
| **健康检查** | 部分有 | 不完整 |
| **备份恢复** | ❌ 无 | 数据丢失风险 |

#### 需要建设的基础设施

**（1）Kubernetes 部署方案**

```yaml
# 关键组件
- API Server: FastAPI (Deployment, HPA 自动伸缩)
- Worker: Agent 异步任务 (Deployment)
- Redis: StatefulSet 或云服务
- PostgreSQL: StatefulSet 或云服务
- 前端: Nginx (Deployment)
```

**（2）配置中心**

将 `configs/config.yaml` 和 `.env` 迁移到配置中心：
- 轻量方案：PostgreSQL 存储配置 + Redis 缓存 + API 热更新
- 重量方案：Nacos / Apollo 配置中心

**（3）健康检查完善**

```
/health          → 整体状态（API + DB + Redis + LLM）
/health/db       → 数据库连接池状态
/health/redis    → Redis 连接状态
/health/llm      → LLM Provider 状态（使用现有的 health_status()）
/health/channels → 各渠道连接状态
```

**（4）数据备份**

- PostgreSQL 自动备份（pg_dump 定时任务）
- 对象存储备份（知识库文件、记忆文件）
- 备份恢复测试流程

---

### 2.8 开发者生态与体验（P2）

#### 为什么重要

要让内部人员轻松自定义个性智能体，需要**降低创建智能体的门槛**：
- 非技术人员应该能通过 UI 创建基本智能体（已部分实现）
- 技术人员应该能通过代码快速创建复杂智能体
- 两者之间应该有平滑的过渡

#### 需要建设的基础设施

**（1）智能体模板市场**

预置场景模板，一键创建：
- 客服智能体模板
- 销售助手模板
- HR 助手模板
- IT 运维助手模板
- 法务助手模板

每个模板包含：预设 Prompt、推荐工具组合、示例知识库、推荐技能。

**（2）低代码工具连接器**

让非技术人员也能配置工具：
- 数据库连接器（填写连接信息，自动生成查询工具）
- API 连接器（填写 OpenAPI 文档 URL，自动生成调用工具）
- 文件连接器（配置文件路径，自动生成读写工具）

**（3）调试和测试工具**

- **对话模拟器**：在前端模拟用户输入，查看智能体完整执行过程
- **Prompt 调试器**：查看完整的 system prompt 和 messages
- **检索调试器**：输入查询，查看检索到的文档片段和排序
- **A/B 对比**：同一输入，对比两个版本的输出

**（4）开发者 SDK**

```python
# 理想的开发者体验
from aid_sdk import Agent, Tool, Skill

@Tool(name="查询库存", description="查询商品库存")
async def check_inventory(product_id: str) -> dict:
    return await db.query("SELECT stock FROM products WHERE id = %s", product_id)

agent = Agent(
    name="电商助手",
    tools=[check_inventory],
    knowledge_base=["products", "policies"],
    prompt="你是电商客服助手，帮助客户查询商品信息和处理退换货。"
)

agent.deploy()  # 一键部署
```

---

## 三、建设路线图

### 第一阶段：核心能力补强（8-10 周）

> 目标：让企业"敢用"

| 周次 | 建设内容 | 交付物 |
|------|---------|--------|
| W1-W3 | **可观测性基础设施** | 链路追踪系统 + 质量评估 Pipeline + 实时仪表盘 |
| W3-W5 | **Prompt 版本管理** | 版本存储 + 前端版本对比/回滚 + 变量模板引擎 |
| W5-W7 | **知识库增强** | Rerank 重排 + 文档权限 + 知识库健康度 |
| W7-W10 | **安全框架** | 统一认证(OIDC) + RBAC + 审计日志 + 内容安全 |

### 第二阶段：平台能力提升（8-12 周）

> 目标：让企业"好用"

| 周次 | 建设内容 | 交付物 |
|------|---------|--------|
| W1-W4 | **智能体编排（阶段一）** | YAML 工作流定义 + WorkflowExecutor + 状态追踪 |
| W4-W8 | **开放集成** | 嵌入组件 + Webhook 出站 + OpenAPI 文档 |
| W8-W12 | **智能体编排（阶段二）** | 可视化编辑器前端 + 后端 DAG 引擎 |

### 第三阶段：生态建设（持续）

> 目标：让企业"离不开"

| 周次 | 建设内容 |
|------|---------|
| 持续 | MCP 协议支持 |
| 持续 | 智能体模板市场 |
| 持续 | 低代码工具连接器 |
| 持续 | 开发者 SDK |
| 持续 | K8s 部署方案 |
| 持续 | 国际化 |

---

## 四、各模块详细评估矩阵

### 4.1 后端模块评估

| 模块 | 代码量 | 完整度 | 质量 | 扩展性 | 企业就绪 | 评级 |
|------|--------|--------|------|--------|---------|------|
| LLM 网关 | ~1400 行 | 92% | ★★★★ | ★★★★★ | ★★★★★ | **A** |
| 智能体核心 | ~2900 行 | 95% | ★★★★ | ★★★★ | ★★★ | **A-** |
| 工具系统 | ~500 行框架 | 90% | ★★★★ | ★★★★★ | ★★★★ | **A-** |
| 技能系统 | ~1900 行 | 90% | ★★★★ | ★★★★★ | ★★★ | **A-** |
| 子智能体 | ~1000 行 | 85% | ★★★★ | ★★★★★ | ★★★ | **B+** |
| 记忆系统 | ~1000 行 | 88% | ★★★★ | ★★★ | ★★★ | **B+** |
| 知识库 | ~1100 行 | 88% | ★★★★ | ★★★★ | ★★★ | **B+** |
| 数据库 | ~2400 行 | 85% | ★★★★ | ★★★ | ★★★★ | **B+** |
| SaaS 系统 | ~3000 行 | 80% | ★★★★ | ★★★★ | ★★★ | **B+** |
| 渠道系统 | ~2000 行 | 65% | ★★★ | ★★★★ | ★★★ | **B-** |
| 安全认证 | 分散 | 55% | ★★★ | ★★★ | ★★ | **C+** |

### 4.2 前端评估

| 模块 | 页面数 | 完整度 | 企业就绪 | 评级 |
|------|--------|--------|---------|------|
| 聊天界面 | 1 | 95% | ★★★★ | A- |
| SaaS 管理后台 | 8 | 80% | ★★★ | B+ |
| 业务数据页面 | 10+ | 85% | ★★★ | B+ |
| 智能体管理 | 1 | 75% | ★★★ | B |
| 监控仪表盘 | 3 | 50% | ★★ | B- |
| 可视化编排 | 0 | 0% | — | N/A |
| 权限管理 | 0 | 0% | — | N/A |

### 4.3 基础设施评估

| 能力 | 现状 | 目标 | 差距 |
|------|------|------|------|
| 链路追踪 | 无 | OpenTelemetry 级别 | 需从零构建 |
| 质量评估 | 无 | 自动评估 + 人工标注 | 需从零构建 |
| Prompt 版本管理 | Git 文件管理 | 运行时版本控制 + 回滚 | 需新建 |
| RBAC | 3 个硬编码角色 | 自定义角色 + 权限矩阵 | 需重建 |
| SSO/OIDC | 无 | 企业微信/钉钉/AD SSO | 需从零构建 |
| 审计日志 | 仅错误日志 | 全操作审计 | 需从零构建 |
| K8s 部署 | Docker 单机 | K8s + HPA | 需新建 |
| API 文档 | 无 | Swagger UI | 需完善 |
| 嵌入组件 | 无 | Chat Widget | 需从零构建 |
| MCP 协议 | 无 | Server + Client | 需从零构建 |

---

## 五、风险与注意事项

### 5.1 技术风险

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| agent.py 过大（2900 行） | 维护困难，新人上手慢 | 优先拆分为 loop/tools/skills/delegation 模块 |
| 长期记忆基于文件系统 | 高并发写入冲突 | 迁移到 PostgreSQL 或对象存储 |
| 无 ORM 抽象 | SQL 硬编码，表结构变更困难 | 引入 SQLAlchemy 或保持现状但规范 SQL 管理 |
| 前端无状态管理库 | 组件间状态同步困难 | 引入 Pinia |

### 5.2 建设优先级决策依据

**为什么可观测性排第一？**
- 企业 CTO/CIO 在采购评估时，最关心的是"AI 回答质量如何衡量"
- 没有质量度量，所有其他功能都是空中楼阁
- 可观测性是安全、运维、优化的基础

**为什么编排引擎不是 P0？**
- 当前 Prompt + 工具选择模式已经覆盖 80% 的场景
- 编排引擎是锦上添花，不是雪中送炭
- 企业更关心"AI 靠不靠谱"而不是"AI 能不能做复杂流程"

**为什么安全是 P1 而不是 P0？**
- 当前系统的租户隔离已经满足基本安全需求
- SSO/RBAC 是大型企业的需求，中小型企业不一定需要
- 建议按客户需求驱动，不提前过度建设

### 5.3 资源估算

| 阶段 | 人力 | 周期 | 核心产出 |
|------|------|------|---------|
| 第一阶段 | 2-3 人 | 8-10 周 | 可观测 + Prompt 管理 + 知识库增强 + 安全 |
| 第二阶段 | 3-4 人 | 8-12 周 | 编排引擎 + 开放集成 + 可视化编辑器 |
| 第三阶段 | 持续投入 | 持续 | 生态建设 |

---

## 六、总结

当前系统在**智能体核心能力**（LLM 网关、工具系统、技能系统）上已经达到了较高的水平，架构设计成熟（特别是 LLM Gateway 的熔断器 + Failover 设计）。

但要成为企业真正愿意付费的 2B 平台，最大的差距不在"功能多不多"，而在**"企业敢不敢用"**——这需要可观测性、Prompt 管理、安全合规这些看不见的基础设施。

建议按 **"先让企业敢用 → 再让企业好用 → 最后让企业离不开"** 的三阶段路线推进建设。
