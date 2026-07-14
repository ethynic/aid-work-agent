# 主子智能体（Master/SubAgent）设计文档

## 一、文档概述

本文档描述 AID Work Agent 项目中**主智能体（Master Agent）**与**子智能体（SubAgent）**的架构设计，包括：

1. **现有设计复盘**：已实现的架构和机制（已对齐代码实际）
2. **新需求**：前端直接指定子智能体模式
3. **设计建议**：结合 OpenClaw、Claude Code 等业界最佳实践的优化建议

---

## 二、现有设计复盘

### 2.1 整体架构

```
┌─────────────────────────────────────────────────────────────────┐
│                         用户请求                                  │
│              (Web / 企业微信 / 钉钉 / 飞书)                       │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                      FastAPI 路由层                              │
│                    src/main.py (chat API)                        │
│         /api/chat (同步)    /api/chat/stream (SSE流式)           │
│                                                              │
│  注意：/api/chat 手动解析 JSON（不使用 ChatRequest）             │
│        /api/chat/stream 使用 ChatRequest 模型                   │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Master Agent (全局单例)                      │
│                   src/core/agent.py :3374                       │
│  master_agent = Agent(is_master=True)                          │
│  导出路径：src/core/__init__.py                                │
│  - 拥有 delegate_to_subagent 工具                               │
│  - 维护 SubagentRegistry（延迟加载：在 __init__ 中调用           │
│    load_from_directory("subagents/")）                          │
│  - 维护 SubagentExecutor                                        │
└─────────────────────────────────────────────────────────────────┘
           │                                    │
           │ delegate_to_subagent               │
           │ (LLM 决策)                         │
           ▼                                    ▼
┌─────────────────────────────────────────────────────────────────┐
│                    SubagentExecutor                              │
│              src/subagents/executor.py                           │
│  - 创建子智能体 Agent 实例                                       │
│  - 在 asyncio.Task 中异步执行（非线程）                            │
│  - 返回 DelegationResponse（含 execution_id）                     │
│  - 管理任务状态同步（通过轮询 wait_for_result）                    │
│  - 支持澄清机制（re-delegate 模式）                              │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                   SubAgent Instance                              │
│            Agent(is_master=False, subagent_config=...)            │
│  - 使用子智能体专属的 system_prompt                              │
│  - 工具根据配置可能被过滤（_filter_tools_by_config）              │
│  - 无 delegate_to_subagent 工具                                  │
│  - 有自己的 ShortTermMemory 实例                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 核心组件

#### 2.2.1 Agent 类（`src/core/agent.py`，约 3377 行）

**核心属性**：

| 属性 | 类型 | 说明 |
|------|------|------|
| `is_master` | `bool` | True 为主智能体，False 为子智能体 |
| `subagent_config` | `Optional[SubagentConfig]` | 子智能体配置（仅子智能体有值） |
| `session_id` | `Optional[str]` | 会话 ID（子智能体模式使用） |
| `execution_id` | `Optional[str]` | 执行 ID（子智能体模式使用） |
| `parent_plan_manager` | `Optional[PlanManager]` | 父智能体的计划管理器（子智能体回写进度用） |
| `llm` | `LLMGateway` | LLM 网关实例（模块级单例） |
| `tool_registry` | `ToolRegistry` | 工具注册表 |
| `tool_executor` | `ToolExecutor` | 工具执行器 |
| `skill_registry` | `SkillRegistry` | 技能注册表 |
| `skill_executor` | `SkillExecutor` | 技能执行器 |
| `memory` | `ShortTermMemory` | 短期记忆（session 级别，deque 滑动窗口） |
| `plan_manager` | `PlanManager` | 计划管理器（持久化为 Markdown 文件） |
| `subagent_registry` | `Optional[SubagentRegistry]` | **主智能体独有**，管理所有子智能体注册 |
| `subagent_executor` | `Optional[SubagentExecutor]` | **主智能体独有**，执行委派任务 |
| `_pending_clarifications` | `Dict[str, Dict]` | 待处理的澄清请求（session_id → 澄清上下文） |
| `_clarification_missing_info` | `List` | 子智能体缺失信息列表 |

**核心方法**：

| 方法 | 签名 | 说明 |
|------|------|------|
| `process_message()` | `async (user_input, session_id, user?, attachments?, cancel_check?) -> AsyncGenerator[dict, None]` | 主入口：处理用户消息，yield 结构化 `AgentEvent` dict。最多 20 轮 LLM 迭代。所有中间状态（progress、tool_start、tool_result、thinking、clarification）和响应文本都通过 yield 输出 |
| `process_message_sync()` | `async (user_input, session_id, user?, attachments?, record_service?, progress_callback?) -> str` | `process_message` 的同步封装，收集所有 `type=response` 事件的 `data` 返回完整字符串。可选 `progress_callback` 接收每个事件用于渠道文件捕获 |
| `execute_as_subagent()` | `async (task_description, parent_session_id, task_record?, progress_callback?) -> Dict` | 子智能体执行入口。内部迭代 `process_message()` 收集事件，返回包含 `result`、`events` 的 dict。若 `is_master=True` 则抛 RuntimeError |
| `_build_base_system_prompt()` | `(include_delegation=True, subagent_constraint="", user=None) -> str` | 构建系统提示词。约 400 行，包含工作流、工具列表、技能规则、委派指南、引导原则等。`not is_master` 时强制 `include_delegation=False` |
| `_build_system_prompt()` | `(user=None) -> str` | 包装方法：master 传 `include_delegation=True`，subagent 传 `include_delegation=False` + `subagent_constraint` |
| `_get_tools()` | `() -> List[Dict]` | 获取工具列表（含 skill 工具 + 委派工具（仅 master 且有子智能体时）） |
| `_register_builtin_tools()` | `() -> None` | 注册 28 个内置工具 + 2 个定时任务工具。master 和 subagent 都调用。subagent 额外调用 `_filter_tools_by_config()` |
| `_filter_tools_by_config()` | `() -> None` | 根据 `subagent_config.tools` 过滤 tool_registry。`inherit: true` 保留全部，否则只保留 `allowed` 列表 |
| `_handle_delegate_to_subagent()` | `(subagent_name, task_description, context_needed?, session_id) -> Dict` | 委派的核心实现：校验子智能体 → 调用 executor.delegate → 等待结果（超时 7200s）→ 处理澄清状态。委派前后 yield `subagent_start`/`subagent_end` 事件 |
| `_handle_use_skill()` | `(skill_name) -> str` | 加载技能内容，返回增强的技能指南 |
| `_handle_skill_execute()` | `(skill_name, command, files?, session_id?, workdir?) -> Dict` | 执行技能脚本命令，处理参数替换、文件解码 |
| `_build_messages()` | `(session_id) -> List[Dict]` | 从 ShortTermMemory 构建消息历史列表 |

**master_agent 全局单例**（`agent.py:3374-3376`）：
```python
master_agent = Agent(is_master=True)
agent = master_agent  # 向后兼容别名
```
在模块导入时创建。通过 `src/core/__init__.py` 导出。

**AGENT_TOOLS 列表**（`agent.py:39-682`）：
定义 28 个工具的 JSON Schema（供 LLM function calling 使用）。**与 ToolRegistry 实现分离**：
- `use_skill`、`delegate_to_subagent`、`create_scheduled_task`、`manage_scheduled_task` 不在 AGENT_TOOLS 中，由 `_get_tools()` 动态追加
- 添加新工具需同时更新 AGENT_TOOLS（schema）和 `_register_builtin_tools()`（实现）

#### 2.2.2 SubagentRegistry 类（`src/subagents/registry.py`）

**全局单例**（`registry.py:385-386`）：
```python
subagent_registry = SubagentRegistry()  # 模块导入时创建，空目录
```
在 Agent.__init__ 中通过 `load_from_directory(Path("subagents"))` 延迟加载。

**职责**：
- 从 `subagents/` 目录加载所有 SUBAGENT.md 配置
- 构建两个反向索引：能力索引、文件模式索引
- 提供按名称、能力（子串匹配）、文件类型匹配子智能体
- 生成子智能体描述供 LLM 使用
- 提供委派工具定义（`delegate_to_subagent`）

**关键方法**：

| 方法 | 说明 |
|------|------|
| `get(name)` | 获取指定名称的子智能体配置 |
| `match_by_capability(desc)` | 根据任务描述匹配（子串匹配，返回第一个命中的） |
| `match_by_file(filename)` | 根据文件类型匹配（fnmatch glob + 回退遍历） |
| `get_delegation_tool_definition(available_subagents?)` | 获取委派工具的 JSON Schema（含可选子智能体列表过滤） |
| `get_descriptions()` | 生成供 LLM 系统提示词使用的子智能体列表描述 |

#### 2.2.3 SubagentExecutor 类（`src/subagents/executor.py`）

**职责**：
- 在 `asyncio.Task` 中异步执行子智能体（**非线程**）
- 管理任务状态同步：PENDING → RUNNING → COMPLETED/FAILED/CANCELLED/CLARIFYING
- `delegate()` 立即返回 `DelegationResponse`，子智能体在后台运行
- `wait_for_result()` 通过轮询（默认 poll_interval=0.5s）等待结果
- 支持澄清机制：子智能体可请求用户澄清（CLARIFYING 状态），主智能体收集用户回答后 re-delegate
- 默认委派超时 7200 秒（2 小时）

**关键方法**：

| 方法 | 返回值 | 说明 |
|------|--------|------|
| `delegate(task_id, subagent_name, task_description, session_id, ...)` | `DelegationResponse` | 委派任务，返回含 execution_id 的响应 |
| `wait_for_result(execution_id, timeout?, poll_interval?)` | `Optional[SubagentTaskRecord]` | 轮询等待子智能体完成 |
| `handle_clarification(execution_id, answer)` | `bool` | 处理用户澄清回答，重置为 RUNNING |
| `get_pending_clarification(execution_id)` | `Optional[SubagentTaskRecord]` | 获取待处理的澄清请求 |
| `cancel(execution_id)` | `bool` | 取消执行（cancel asyncio.Task） |

#### 2.2.4 AgentFactory 类（`src/subagents/factory.py`）— 已存在

**注意：项目中已存在 AgentFactory**，位于 `src/subagents/factory.py`，包含以下方法：

| 方法 | 说明 |
|------|------|
| `create_standalone_agent(agent_name, session_memory, session_id?)` | 创建独立模式的智能体。**当前实现是通用的 master agent**（`is_master=True`），**没有**使用 subagent_config，**没有** mode 参数 |
| `create_subagent(agent_name, session_memory, session_id, execution_id, parent_plan_manager?)` | 创建子智能体（委派模式），`is_master=False`，使用 subagent_config |
| `create_delegation_tool(available_agents?)` | 委托给 SubagentRegistry.get_delegation_tool_definition() |
| `list_available_agents()` | 列出所有已注册的子智能体 |

模块级便捷函数（`factory.py:212`）：
```python
def create_agent(agent_name, subagents_dir="subagents", **kwargs) -> Agent
```

#### 2.2.5 已有的子智能体

**当前只有 1 个子智能体**：`trade-specialist`（外贸获客智能体）

> 注意：设计文档之前版本提到有 hr-expert 和 code-reviewer，但代码中 `subagents/` 目录下只有 `trade-specialist/SUBAGENT.md`。

#### 2.2.6 SUBAGENT.md 配置格式

```yaml
---
# 基本信息
name: 外贸获客智能体
description: 外贸获客智能体，帮助企业快速获取海外潜在客户资源
version: 1.0.0
author: system

# 能力标签
capabilities:
  - lead_matching
  - email_marketing
  - product_recommendation

# 触发条件
triggers:
  keywords:
    - 外贸
    - 客户匹配
    ...
  # 可选：文件模式匹配
  file_patterns:
    - "*.pdf"

# 工具配置
tools:
  inherit: true           # true=继承全部工具；false=使用 allowed 列表
  # allowed:               # inherit=false 时指定允许的工具名
  #   - web_search
  additional:             # 额外添加的工具（暂未在代码中实现过滤逻辑）
    - content_generate

# 技能访问
skills:
  allowed:
    - pdf
    - email
    - trade-customer

# 上下文约束
context:
  max_input_tokens: 8000
  max_output_tokens: 4000

# 可选：委派配置
delegatable_to:
  - other-agent
allow_delegation: true

# 系统提示词（如果为空，自动使用 Markdown body 作为 system_prompt）
system_prompt: |
  ## 外贸专员职责
  ...
---

# 如果 YAML 中 system_prompt 为空，
# 这里的 Markdown body 会自动作为 system_prompt 使用
```

#### 2.2.7 现有数据模型（`src/models/subagent.py`）

| 模型 | 用途 |
|------|------|
| `SubagentConfig(BaseModel)` | 子智能体配置，含 tools/skills/triggers/capabilities/delegatable_to 等字段 |
| `SubagentTaskStatus(str, Enum)` | 任务状态枚举：PENDING, RUNNING, COMPLETED, FAILED, CLARIFYING, CANCELLED |
| `SubagentExecutionContext(BaseModel)` | 子智能体执行上下文（与 SubagentTaskRecord 功能重叠，定义在 models 层） |
| `DelegationRequest(BaseModel)` | 委派请求模型 |
| `DelegationResponse(BaseModel)` | 委派响应模型（含 success, execution_id, result, error, summary, token_usage） |

`src/subagents/protocol.py` 中的 `SubagentTaskRecord(BaseModel)` 功能与 `SubagentExecutionContext` 高度重叠，是 executor 实际使用的记录模型。

### 2.3 工作流程

#### 2.3.1 Master Agent 的 process_message 循环

```
1. 检查是否有待处理的澄清回复（_pending_clarifications）
   └── 有 → re-delegate 给子智能体（携带原始任务 + 澄清问答）

2. 添加时间戳上下文到用户输入

3. 处理附件（如有）
   ├── 自动匹配 Skill（skill_registry.match_by_file()）
   ├── 创建临时工作目录（tempfile.mkdtemp）
   ├── 保存文件（base64 解码或从 URL 复制）
   └── 注入文件路径到增强输入

4. 存入 ShortTermMemory

5. 构建消息列表和系统提示词

6. 如果有自动匹配的 Skill → 注入 Skill 内容作为用户消息

7. 进入 LLM 迭代循环（最多 20 轮）
   ├── 调用 llm_gateway.chat_with_tools(system_prompt, messages, tools)
   ├── 如果 LLM 返回文本回复 → 自动完成活跃 Skill → yield → 退出
   └── 如果 LLM 返回工具调用（逐个处理）：
       ├── delegate_to_subagent → _handle_delegate_to_subagent()
       │   └── SubagentExecutor.delegate() → asyncio.Task 后台执行
       │   └── wait_for_result() 轮询等待（超时 7200s）
       │   └── 若 CLARIFYING → 存入 _pending_clarifications
       ├── use_skill → _handle_use_skill()（返回技能指南）
       ├── skill_execute → _handle_skill_execute()（执行脚本，替换参数）
       ├── create_plan → _handle_create_plan()（创建执行计划 Markdown）
       ├── clarify → 返回问题给 LLM（不发送给用户）
       ├── create_scheduled_task → 特殊处理（带上下文）
       ├── manage_scheduled_task → 特殊处理（带上下文）
       └── 其他工具 → tool_executor.execute(tool_name, tool_args)

8. 工具结果追加到消息列表和记忆，继续下一轮
```

#### 2.3.2 子智能体执行流程（execute_as_subagent）

```
1. 校验 is_master=False，否则抛 RuntimeError

2. 构建消息列表
   └── 仅有系统提示词 + task_description（不使用父会话历史消息）

3. 进入 LLM 迭代循环（最多 20 轮）
   ├── 调用 llm_gateway.chat_with_tools()
   ├── 执行工具调用（不支持 delegate_to_subagent）
   │   ├── clarify → 设置 CLARIFYING 状态，提前返回
   │   ├── use_skill → 加载技能上下文
   │   ├── skill_execute → 执行技能脚本
   │   └── 其他工具 → tool_executor.execute()
   └── 无工具调用时收集最终回复

4. 收集 generated_content_list（content_generate 的结果）

5. 返回结果 Dict 给 SubagentExecutor
   └── {result, summary, generated_contents, token_usage}
```

### 2.4 系统提示词构建

**主智能体**（`_build_system_prompt()` → `_build_base_system_prompt(include_delegation=True, user=user)`）：
- 包含 `delegate_to_subagent` 工具说明和使用指南
- 列出所有可用子智能体描述（来自 SubagentRegistry）
- 包含委派决策逻辑（何时直接委派，何时创建计划）
- 包含所有工具列表、技能列表、工作流步骤、浏览器工具规则、定时任务能力、引导原则、工作示例

**子智能体**（`_build_system_prompt()` → `_build_base_system_prompt(include_delegation=False, subagent_constraint=config.system_prompt, user=user)`）：
- 不包含委派工具说明
- 追加子智能体专属的 system_prompt 作为 `subagent_constraint`
- 添加"不能委派"的限制说明

**关键分支逻辑**（`_build_base_system_prompt:1025-1026`）：
```python
if not self.is_master:
    include_delegation = False  # 强制禁止子智能体委派
```

---

## 三、新需求：入口级绑定子智能体

### 3.1 需求描述

当前架构中，用户请求统一由 Master Agent 处理，LLM 根据任务内容决定是否委派给子智能体。

**新需求**：用户从特定入口进入后，**始终由指定的子智能体处理**，不可跳出。这个子智能体直接作为主智能体工作（拥有完整能力），不经过 Master Agent 委派。

**"入口"的两种形式**：
1. **IM 机器人**：企业微信/钉钉/飞书中不同的机器人对应不同的 Agent（如机器人 A = 通用助手，机器人 B = 外贸助手）
2. **前端 URL**：Web 端不同路由对应不同 Agent（如 `/chat` = 通用助手，`/chat/trade-specialist` = 外贸助手）

**核心原则**：
- 入口决定 Agent，**不可在会话中途切换**
- 后端从入口信息确定 Agent，**不信任前端请求体中的 subagent 参数**
- 子智能体内只能做该子智能体定义的事情（通过 system_prompt 和工具过滤约束）

### 3.2 设计方案：入口绑定 Agent

#### 3.2.1 Web 端：URL 路由绑定

**前端增加子智能体专属路由**：

```
现有：/chat                     → master_agent（通用助手）
新增：/chat/:subagent_name      → StandaloneAgent（子智能体独立模式）
```

前端 `vue-router` 改造：
```typescript
// frontend/src/main.ts
routes: [
  { path: '/', name: 'chat', component: ChatContainer },                    // 默认 → master
  { path: '/chat', name: 'chat-master', component: ChatContainer },         // 通用助手
  { path: '/chat/:subagent', name: 'chat-subagent', component: ChatContainer }, // 子智能体
  // ... 其他路由不变
]
```

**ChatContainer 从路由参数获取 subagent**，传入 `useAgent`：
```typescript
// ChatContainer.vue
const route = useRoute()
const subagentName = computed(() => route.params.subagent as string | undefined)

// 发消息时，subagent 从路由参数获取，不从请求体获取
const agent = agentRouter.get_agent(subagentName.value || null, sessionId)
```

**后端不需要在请求体中接收 subagent 参数**。前端通过 URL 路由决定用哪个 Agent，但后端仍需接收 `subagent` 参数以保证 SSE 端点能正确路由（因为 SSE 是 POST 请求，路由参数不在 POST body 中）：

```python
# ChatRequest 保持简洁
class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    files: Optional[List[Dict[str, Any]]] = None
    user_id: Optional[str] = None
    subagent: Optional[str] = None  # 由前端从路由参数提取后传入
```

#### 3.2.2 IM 端：机器人绑定

IM 渠道中，不同的企业微信/钉钉/飞书机器人天然是不同的入口。

**方案：在渠道配置中绑定 Agent**

`configs/config.yaml` 增加每个渠道的 `agent` 配置：

```yaml
channels:
  wecom:
    enabled: true
    corp_id: "..."
    agent_id: "..."           # 通用助手机器人 → 不配 agent 字段 → master_agent
    secret: "..."
    # 无 agent 字段 → 默认使用 master_agent

  wecom_trade:
    enabled: true
    corp_id: "..."
    agent_id: "..."           # 外贸助手机器人 → 绑定 trade-specialist
    secret: "..."
    agent: "trade-specialist" # ← 绑定到子智能体
```

**或者更简单的方案：每个渠道适配器携带 agent 配置**

```python
# src/channels/base.py
class ChannelAdapter(ABC):
    @property
    @abstractmethod
    def channel_type(self) -> str: ...

    @property
    def bound_agent(self) -> Optional[str]:
        """该渠道绑定的子智能体名称，None 表示使用 master_agent"""
        return None
```

各渠道适配器从自己的配置中返回绑定的 agent 名称。

#### 3.2.3 统一入口路由：AgentRouter

所有入口最终都汇聚到 `AgentRouter.get_agent()`：

```
Web URL /chat                    → agent_router.get_agent(None, session_id)           → master_agent
Web URL /chat/trade-specialist   → agent_router.get_agent("trade-specialist", session_id) → StandaloneAgent
企业微信 机器人A（无 agent 绑定） → agent_router.get_agent(None, session_id)           → master_agent
企业微信 机器人B（agent=trade）  → agent_router.get_agent("trade-specialist", session_id) → StandaloneAgent
```

#### 3.2.4 安全保证：入口不可篡改

**Web 端**：
- subagent 参数来自前端路由（URL 路径），由 `ChatContainer` 组件提取后传给 `useAgent`
- 后端接收 `ChatRequest.subagent`，这个值**由前端路由决定，不由用户手动填写**
- 如果安全要求更高，可以在后端校验 subagent 名称是否在 `subagent_registry` 白名单中（当前设计已包含此逻辑——找不到的 subagent 会 fallback 到 master_agent）

**IM 端**：
- 绑定关系写死在配置文件（`config.yaml`）或数据库中，用户完全无法修改
- 渠道回调 URL 固定（如 `/wecom/callback`），机器人 A 和 B 的回调 URL 可以相同（通过 `agent_id` 区分），也可以用不同 URL（如 `/wecom/callback` 和 `/wecom-trade/callback`）
- 最安全的做法是**不同机器人使用不同的回调 URL**，每个 URL 直接硬编码绑定的 agent

**不同机器人使用不同回调 URL 的方案**：

```python
# src/main.py lifespan 中注册不同回调

# 机器人 A：通用助手（默认 master_agent）
# 现有 /wecom/callback 不变，走 agent_router.get_agent(None, ...)

# 机器人 B：外贸助手
@app.post("/wecom-trade/callback")
async def wecom_trade_callback_post(request: Request):
    # ... 解析消息（与现有 wecom 回调相同逻辑）...
    await process_channel_message("wecom", message, bound_agent="trade-specialist")
    return PlainTextResponse("success")
```

### 3.3 Agent 实例管理

#### 3.3.1 引入 AgentMode 枚举 + 扩展现有 AgentFactory

**不新建 `src/core/agent_factory.py`**，而是**扩展现有** `src/subagents/factory.py`，避免两套工厂共存。

```python
from enum import Enum

class AgentMode(Enum):
    MASTER = "master"                    # 主智能体模式
    SUBAGENT = "subagent"               # 子智能体（被委派）模式
    STANDALONE = "standalone"            # 子智能体独立（直接）模式
```

#### 3.3.2 Agent 类改造

```python
class Agent:
    def __init__(
        self,
        is_master: bool = True,
        subagent_config=None,
        session_id: Optional[str] = None,
        execution_id: Optional[str] = None,
        parent_plan_manager=None,
        mode: AgentMode = AgentMode.MASTER,  # 新增
    ):
        self.mode = mode
        self.subagent_config = subagent_config

        if mode == AgentMode.STANDALONE:
            # 子智能体独立模式
            self.is_master = True           # 视为主智能体（获取完整初始化）
            self.subagent_registry = None    # 不维护子智能体注册表
            self.subagent_executor = None    # 不维护执行器
        elif mode == AgentMode.SUBAGENT:
            self.is_master = False
            # 子智能体原有逻辑...
        else:
            # MASTER 模式原有逻辑
            self.is_master = True
            # 初始化 subagent_registry, subagent_executor 等...
```

#### 3.3.3 系统提示词构建调整

**关键问题**：当前 `_build_system_prompt()` 的分支条件是 `if self.is_master`。Standalone 模式 `is_master=True`，会走进 master 分支，但需要的是子智能体的专业约束提示词。

**解决方案**：调整 `_build_system_prompt()` 的判断条件：

```python
def _build_system_prompt(self, user: Optional[User] = None) -> str:
    if self.mode == AgentMode.MASTER:
        # 原有主智能体逻辑
        return self._build_base_system_prompt(include_delegation=True, user=user)
    else:
        # SUBAGENT 和 STANDALONE 都使用子智能体约束
        subagent_constraint = ""
        if self.subagent_config and self.subagent_config.system_prompt:
            subagent_constraint = self.subagent_config.system_prompt
        return self._build_base_system_prompt(
            include_delegation=False,  # 两种模式都禁止委派
            subagent_constraint=subagent_constraint,
            user=user
        )
```

#### 3.3.4 工具列表调整

**Standalone 模式**下禁止委派：

```python
def _get_tools(self) -> List[Dict[str, Any]]:
    tools = list(AGENT_TOOLS)

    if self.skill_registry:
        tools.append(self.skill_registry.get_skill_tool_definition())

    # 仅 MASTER 模式下有委派能力
    # STANDALONE 和 SUBAGENT 模式都禁止委派
    if self.mode == AgentMode.MASTER and self.subagent_registry and len(self.subagent_registry) > 0:
        delegation_tool = self.subagent_registry.get_delegation_tool_definition()
        if delegation_tool:
            tools.append(delegation_tool)

    return tools
```

### 3.4 数据流对比

#### 当前委派模式（保留）

```
用户请求（任何入口）
    │
    ▼
Master Agent (process_message)
    │
    │ LLM 决定委派
    ▼
delegate_to_subagent 工具 → _handle_delegate_to_subagent()
    │
    ▼
SubagentExecutor.delegate() → asyncio.Task（后台执行）
    │
    ▼
SubAgent Instance (execute_as_subagent)
    │   - 独立消息列表（无历史）
    │   - 最多 20 轮 LLM 迭代
    │
    │ wait_for_result() 轮询等待
    ▼
Master Agent 整合结果 → 返回给用户
```

#### 新增入口绑定模式

```
用户请求（从特定入口进入）
    │
    │ 入口类型决定 agent：
    │   Web: /chat/trade-specialist → subagent_name = "trade-specialist"
    │   IM: 机器人 B（配置 agent=trade-specialist） → subagent_name = "trade-specialist"
    ▼
AgentRouter.get_agent(subagent_name, session_id) → 返回 StandaloneAgent 实例
    │   (缓存 key: "{session_id}:{subagent_name}")
    │
    ▼
Agent(mode=STANDALONE, is_master=True)
    │   - 使用子智能体 system_prompt（只能做定义范围内的事）
    │   - 工具按 subagent_config 过滤（只能用允许的工具）
    │   - 独立的 ShortTermMemory（有会话记忆）
    │   - 禁止委派（无法跳出）
    │
    ▼
process_message() 完整循环 → 直接返回给用户
```

### 3.5 约束保证：子智能体"只能做定义范围内的事"

"限定在子智能体里面"由三层机制共同保证：

| 层 | 机制 | 保证 |
|---|------|------|
| **1. 入口层** | URL / 机器人回调绑定 | 用户从指定入口进入，后端固定使用对应 Agent，会话中途不可切换 |
| **2. 提示词层** | subagent_config.system_prompt | 告诉 LLM 它的角色和职责边界，LLM 只会在该范围内行动 |
| **3. 工具层** | _filter_tools_by_config() | 子智能体只能调用 subagent_config 中允许的工具，其他工具物理上不可用 |

入口层是硬约束（不可绕过），提示词层是软约束（LLM 可能偶尔偏离但概率极低），工具层是物理约束（LLM 想调也调不了）。三层结合可以可靠地限定子智能体的行为范围。

### 3.6 默认子智能体自动路由

当租户仅订阅了一个可访问的子智能体时，`_resolve_default_subagent` 函数会自动将用户请求路由到该子智能体，跳过主智能体对话界面，直接进入子智能体交互。

| 租户情况 | 用户情况 | 结果 |
|---------|---------|------|
| 订阅 1 个智能体 | 租户管理员 | 自动路由到该智能体 |
| 订阅 1 个智能体 | 普通用户（已授权） | 自动路由到该智能体 |
| 订阅 1 个智能体 | 普通用户（未授权） | 走主智能体（用户无权，后续会拒绝） |
| 订阅 N 个智能体 (N>1) | 租户管理员 | 走主智能体（原有逻辑） |
| 订阅 N 个智能体 (N>1) | 普通用户（只授权 1 个） | 自动路由到该智能体 |
| demo 租户 | 任意 | 不走自动路由（内置智能体多） |
| 非租户/演示模式 | 任意 | 不走自动路由 |

---

## 四、设计建议：结合业界最佳实践

### 4.1 OpenClaw 设计参考

**OpenClaw** 是一个开源的 AI Agent 框架，其核心设计原则：

1. **Agent 作为最小执行单元**：每个 Agent 独立运行，有自己的工具集和状态
2. **两层架构**：Orchestrator（编排器）+ Worker（工作者）
3. **基于消息的通信**：Agent 之间通过结构化消息通信

**可借鉴点**：

| 设计 | 当前实现 | 建议改进 |
|------|----------|----------|
| Agent 独立性 | 子智能体依赖 Master 发起 | 增强子智能体的独立性，支持独立运行（本设计已覆盖） |
| 消息协议 | 通过 memory 共享 | 已有 `SubagentTaskRecord` + `DelegationResponse`，可在其基础上规范化 |

### 4.2 Claude Code 设计参考

**Claude Code** 是 Anthropic 的命令行 Agent，其核心设计：

1. **工具为王**：精心设计的工具定义，LLM 主要通过工具交互
2. **安全沙箱**：文件操作有严格的路径限制
3. **透明执行**：LLM 知道每一步在做什么

**可借鉴点**：

| 设计 | 当前实现 | 建议改进 |
|------|----------|----------|
| 工具定义 | 一次性返回所有工具 | 按阶段/上下文动态提供工具（减少 LLM 混淆） |
| 执行透明度 | 进度回调，但不够结构化 | 增加更详细的执行步骤描述 |
| 错误恢复 | 简单重试 | 增加任务级别的错误恢复策略 |

### 4.3 具体设计建议

#### 建议 1：引入 AgentMode 枚举

```python
from enum import Enum

class AgentMode(Enum):
    MASTER = "master"                    # 主智能体模式
    SUBAGENT = "subagent"               # 子智能体（被委派）模式
    STANDALONE = "standalone"            # 子智能体独立（直接）模式
```

#### 建议 2：扩展现有 AgentFactory（不新建）

在 `src/subagents/factory.py` 的**现有** `AgentFactory` 类上增加方法，而非新建 `src/core/agent_factory.py`：

```python
# 在 src/subagents/factory.py 现有 AgentFactory 中增加
class AgentFactory:
    # ... 保留现有 create_standalone_agent / create_subagent 等 ...

    @staticmethod
    def create_standalone_subagent(
        name: str,
        session_id: str,
    ) -> Optional['Agent']:
        """创建子智能体（独立模式，直接作为主智能体）

        与现有 create_standalone_agent() 的区别：
        - 使用 subagent_config（专业约束）
        - 使用 AgentMode.STANDALONE
        """
        config = subagent_registry.get(name)
        if not config:
            return None
        return Agent(
            is_master=True,
            mode=AgentMode.STANDALONE,
            subagent_config=config,
            session_id=session_id,
        )
```

#### 建议 3：统一入口点 AgentRouter

```python
# src/core/agent_router.py（新文件）
from typing import Optional, Dict
from loguru import logger
from src.core.agent import Agent, AgentMode, master_agent  # 使用现有单例

class AgentRouter:
    def __init__(self):
        # 使用现有 master_agent 全局单例，避免重复初始化
        self.master_agent = master_agent
        self._standalone_cache: Dict[str, Agent] = {}

    def get_agent(
        self,
        subagent_name: Optional[str],
        session_id: str,
    ) -> Agent:
        if not subagent_name:
            return self.master_agent

        cache_key = f"{session_id}:{subagent_name}"
        if cache_key not in self._standalone_cache:
            from src.subagents.factory import AgentFactory
            agent = AgentFactory.create_standalone_subagent(subagent_name, session_id)
            if not agent:
                logger.warning(f"[AgentRouter] Subagent '{subagent_name}' not found, fallback to master")
                return self.master_agent
            self._standalone_cache[cache_key] = agent

        return self._standalone_cache[cache_key]

    def release_session(self, session_id: str):
        """释放会话相关的所有独立模式子智能体"""
        keys_to_remove = [k for k in self._standalone_cache if k.startswith(f"{session_id}:")]
        for k in keys_to_remove:
            del self._standalone_cache[k]

    def cleanup_expired(self, max_age_seconds: int = 3600):
        """清理过期的独立模式子智能体实例

        建议由 APScheduler 定期调用，避免缓存无限增长。
        需要在 Agent 实例上记录 last_active 时间戳。
        """
        # 实现略：遍历缓存，移除超过 max_age_seconds 的实例
```

#### 建议 4：动态工具集（后续优化）

当前实现一次性返回所有工具，可以考虑按阶段/上下文动态提供：

```python
def _get_tools(self, context: Optional[Dict] = None) -> List[Dict[str, Any]]:
    """获取工具列表，可根据上下文动态调整"""
    tools = list(AGENT_TOOLS)

    if self.skill_registry:
        tools.append(self.skill_registry.get_skill_tool_definition())

    # 仅 MASTER 模式有委派能力
    if self.mode == AgentMode.MASTER:
        if self.subagent_registry and len(self.subagent_registry) > 0:
            delegation_tool = self.subagent_registry.get_delegation_tool_definition()
            if delegation_tool:
                tools.append(delegation_tool)

    return tools
```

#### 建议 5：Protocol 复用（不重复定义）

**不建议**新建 `SubAgentMessage`、`SubAgentTask`、`SubAgentResult` 模型，因为：
- `SubagentTaskRecord`（`protocol.py`）已覆盖任务记录需求
- `DelegationRequest`/`DelegationResponse`（`subagent.py`）已覆盖委派通信需求
- `SubagentExecutionContext`（`subagent.py`）与 `SubagentTaskRecord` 功能重叠，应统一

建议后续清理 `SubagentExecutionContext`（models 层），统一使用 `SubagentTaskRecord`（protocol 层），但**不在本次改造中做**。

#### 建议 6：会话隔离

不需要新建 `StandaloneAgentSession` 类。Standalone 模式下：
- Agent 实例自带独立的 `ShortTermMemory`（构造时创建新实例）
- Agent 实例缓存按 `{session_id}:{subagent_name}` 隔离
- 会话结束调用 `agent_router.release_session(session_id)` 清理

---

## 五、完整设计：整合新需求 + 建议

### 5.1 架构图

```
┌──────────────────────────────────────────────────────────────────────────┐
│                              用户请求                                      │
│                   (subagent 参数可选)                                      │
└──────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                           AgentRouter                                     │
│                      src/core/agent_router.py（新增）                      │
│  ┌─────────────────┐    ┌──────────────────────────────────────────────┐  │
│  │ get_agent()     │    │ 逻辑：                                        │  │
│  │                 │    │ 1. subagent=None → 现有 master_agent 单例     │  │
│  │ session_id      │    │ 2. subagent=xxx → StandaloneAgent            │  │
│  │ subagent_name   │    │    (创建/获取缓存的独立模式子智能体实例)        │  │
│  └─────────────────┘    └──────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────┘
            │                               │
            ▼                               ▼
┌─────────────────────────┐    ┌─────────────────────────────────────────────┐
│     MasterAgent         │    │        StandaloneAgent                     │
│  Agent(mode=MASTER)      │    │  Agent(mode=STANDALONE)                    │
│  (现有全局单例)           │    │  (按 session:subagent 缓存)                │
│                          │    │                                             │
│ - 完整工具集              │    │ - 使用子智能体 system_prompt                │
│ - 有 delegate 能力        │    │ - 独立 ShortTermMemory（有会话记忆）        │
│ - 维护 SubagentRegistry  │    │ - 工具按 subagent_config 过滤               │
│                          │    │ - 禁止委派（无 delegate_to_subagent）       │
└─────────────────────────┘    └─────────────────────────────────────────────┘
            │                               │
            │ 委派（LLM 决策）               │ 直接处理（process_message）
            ▼                               │ （完整 20 轮循环）
┌─────────────────────────┐                 │
│  SubagentExecutor       │                 │
│  asyncio.Task           │                 │
│  - 创建 SubAgent        │                 │
│  - 后台执行              │                 │
│  - 轮询等待结果          │                 │
└─────────────────────────┘                 │
            │                               │
            ▼                               ▼
┌─────────────────────────┐    ┌─────────────────────────────────────────────┐
│   SubAgent Instance     │    │           返回结果给用户                      │
│  Agent(mode=SUBAGENT)   │    │    (SSE stream 或 sync JSON)                 │
│  - 无历史记忆            │    │    响应含 agent_type 字段                     │
│  - 无委派能力            │    └─────────────────────────────────────────────┘
│  - 专属 system_prompt    │
└─────────────────────────┘
```

### 5.2 关键改动清单

| 序号 | 文件 | 改动类型 | 改动内容 |
|------|------|----------|----------|
| 1 | `src/core/agent.py` | 修改 | 新增 `AgentMode` 枚举；`__init__` 增加 `mode` 参数；调整初始化分支（STANDALONE 不创建 subagent_registry/executor）；调整 `_get_tools()` 使用 `mode` 判断；调整 `_build_system_prompt()` 使用 `mode` 判断 |
| 2 | `src/core/agent_router.py` | **新增** | `AgentRouter` 类：管理 master_agent 单例 + standalone 缓存 + 过期清理 |
| 3 | `src/subagents/factory.py` | 修改 | `AgentFactory` 增加 `create_standalone_subagent()` 方法（使用 subagent_config + AgentMode.STANDALONE） |
| 4 | `src/main.py` | 修改 | `ChatRequest` 新增 `subagent` 字段；`/api/chat` 和 `/api/chat/stream` 两个端点都通过 `agent_router.get_agent()` 选择 agent；响应增加 `agent_type`；`master_agent` 引用替换为 `agent_router` |
| 5 | `src/core/__init__.py` | 修改 | 导出 `AgentMode` |
| 6 | `src/subagents/executor.py` | 修改（可选） | `_create_subagent` 时传入 `mode=AgentMode.SUBAGENT` |
| 7 | `tests/` | **新增** | `test_agent_router.py`、`test_standalone_mode.py` |

### 5.3 核心代码变更

#### 5.3.1 AgentMode 枚举 + Agent 类改造

```python
# src/core/agent.py（文件顶部，AGENT_TOOLS 列表之前）
from enum import Enum

class AgentMode(Enum):
    MASTER = "master"
    SUBAGENT = "subagent"
    STANDALONE = "standalone"

# Agent.__init__ 改造
class Agent:
    def __init__(
        self,
        is_master: bool = True,
        subagent_config=None,
        session_id: Optional[str] = None,
        execution_id: Optional[str] = None,
        parent_plan_manager=None,
        mode: AgentMode = AgentMode.MASTER,  # 新增
    ):
        self.mode = mode
        self.is_master = is_master
        self.subagent_config = subagent_config
        self.session_id = session_id
        self.execution_id = execution_id
        self.parent_plan_manager = parent_plan_manager

        # ... 公共初始化（llm, tool_registry, tool_executor, memory, skill_registry 等）...

        if mode == AgentMode.STANDALONE:
            # 独立模式：不创建子智能体注册表和执行器
            self.subagent_registry = None
            self.subagent_executor = None
            self._register_builtin_tools()
            if subagent_config:
                self._filter_tools_by_config()
        elif is_master:
            # MASTER 模式（原有逻辑）
            self.subagent_registry = SubagentRegistry(subagents_dir=Path("subagents"))
            self._register_builtin_tools()
            self.subagent_executor = SubagentExecutor(...)
        else:
            # SUBAGENT 模式（原有逻辑）
            self.subagent_registry = None
            self.subagent_executor = None
            self._register_builtin_tools()
            self._filter_tools_by_config()

    def _get_tools(self) -> List[Dict[str, Any]]:
        tools = list(AGENT_TOOLS)

        if self.skill_registry:
            tools.append(self.skill_registry.get_skill_tool_definition())

        # 仅 MASTER 模式有委派能力
        if self.mode == AgentMode.MASTER and self.subagent_registry and len(self.subagent_registry) > 0:
            delegation_tool = self.subagent_registry.get_delegation_tool_definition()
            if delegation_tool:
                tools.append(delegation_tool)

        return tools

    def _build_system_prompt(self, user: Optional[User] = None) -> str:
        if self.mode == AgentMode.MASTER:
            return self._build_base_system_prompt(include_delegation=True, user=user)
        else:
            # SUBAGENT 和 STANDALONE 都使用子智能体专业约束
            subagent_constraint = ""
            if self.subagent_config and self.subagent_config.system_prompt:
                subagent_constraint = self.subagent_config.system_prompt
            return self._build_base_system_prompt(
                include_delegation=False,
                subagent_constraint=subagent_constraint,
                user=user
            )
```

#### 5.3.2 AgentRouter 类（新文件）

```python
# src/core/agent_router.py
import time
from typing import Optional, Dict
from loguru import logger
from src.core.agent import Agent, AgentMode, master_agent

class AgentRouter:
    def __init__(self):
        # 使用现有全局单例，避免重复初始化（见下方"为何复用 master_agent"说明）
        self.master_agent = master_agent
        self._standalone_cache: Dict[str, Agent] = {}

    def get_agent(
        self,
        subagent_name: Optional[str],
        session_id: str,
    ) -> Agent:
        if not subagent_name:
            return self.master_agent

        cache_key = f"{session_id}:{subagent_name}"
        if cache_key not in self._standalone_cache:
            from src.subagents.factory import AgentFactory
            agent = AgentFactory.create_standalone_subagent(subagent_name, session_id)
            if not agent:
                logger.warning(
                    f"[AgentRouter] Subagent '{subagent_name}' not found, "
                    f"fallback to master"
                )
                return self.master_agent
            agent._created_at = time.time()  # 记录创建时间
            self._standalone_cache[cache_key] = agent
            logger.info(f"[AgentRouter] Created standalone agent: {cache_key}")

        return self._standalone_cache[cache_key]

    def release_session(self, session_id: str):
        """释放会话相关的所有独立模式子智能体"""
        keys_to_remove = [
            k for k in self._standalone_cache
            if k.startswith(f"{session_id}:")
        ]
        for k in keys_to_remove:
            del self._standalone_cache[k]
            logger.info(f"[AgentRouter] Released standalone agent: {k}")

    def cleanup_expired(self, max_age_seconds: int = 3600):
        """清理过期的独立模式子智能体实例"""
        now = time.time()
        expired_keys = [
            k for k, agent in self._standalone_cache.items()
            if now - getattr(agent, '_created_at', now) > max_age_seconds
        ]
        for k in expired_keys:
            del self._standalone_cache[k]
            logger.info(f"[AgentRouter] Cleaned up expired standalone agent: {k}")

# 全局路由实例
agent_router = AgentRouter()
```

**为何复用 master_agent 全局单例（而非每个会话创建新实例）？**

核心原因：**Agent 实例本身是无状态的，会话隔离由 ShortTermMemory 内部的 session_id 字典保证。**

```
master_agent（单个实例）
  ├── subagent_registry          ← 所有会话共享（只读，不会变）
  ├── subagent_executor          ← 所有会话共享（每次委派创建独立 Task）
  ├── tool_registry              ← 所有会话共享（只读，不会变）
  ├── _pending_clarifications    ← Dict[session_id, Dict]，按 session_id 隔离
  └── memory (ShortTermMemory)   ← 内部结构：
        self._cache: Dict[str, deque]
          ├── "web_user_abc123"  → deque([msg1, msg2, ...])   ← 会话 A 的上下文
          ├── "web_user_def456"  → deque([msg1, msg2, ...])   ← 会话 B 的上下文
          └── "wecom_zhangsan"   → deque([msg1, ...])         ← 会话 C 的上下文
```

- `ShortTermMemory.add()`、`get_context()`、`clear()` 全部以 `session_id` 为 key 操作，不同会话的数据存在不同的 deque 中，**互不干扰**
- `_pending_clarifications` 同样是按 `session_id` 索引的字典
- 如果每个会话创建一个 master_agent，每个实例都会重复执行：加载 `subagents/` 目录（磁盘 IO）、注册 28+ 工具、创建空的 memory——这些全是无意义的重复开销

**Standalone Agent 为何需要缓存（每个 session:subagent 一个实例）？**

与 master_agent 不同，standalone agent 的情况特殊：
- 每个 standalone agent 绑定了不同的 `subagent_config`（不同的 system_prompt、不同的工具过滤规则）
- 需要注入特定的专业领域约束，无法复用通用 master_agent
- 但同一个用户在同一会话中连续对话，应该复用同一个 standalone 实例（保持其内部的 skill session 等状态）
- 因此按 `{session_id}:{subagent_name}` 缓存

**对比总结**：

| 维度 | master_agent | standalone_agent |
|------|-------------|-----------------|
| 实例数量 | 1 个全局单例 | 按 `{session_id}:{subagent_name}` 缓存 |
| 会话隔离方式 | memory 内部 `Dict[session_id, deque]` | 每个 Agent 实例自带独立 memory |
| 能否复用 | 所有会话共享同一实例 | 同一 session + 同一 subagent 复用 |
| 初始化开销 | 较大（加载子智能体配置 + 注册工具） | 较小（无 subagent_registry） |
| 为何不复用 | N/A（已是单例） | 不同 subagent 有不同 system_prompt |

#### 5.3.3 AgentFactory 扩展（修改现有文件）

```python
# src/subagents/factory.py — 在现有 AgentFactory 类中增加方法

class AgentFactory:
    # ... 保留现有所有方法不变 ...

    @staticmethod
    def create_standalone_subagent(
        name: str,
        session_id: str,
    ) -> Optional['Agent']:
        """创建子智能体独立模式（直接作为主智能体处理请求）

        与 create_standalone_agent() 的区别：
        - 使用 subagent_config（注入专业 system_prompt）
        - 使用 AgentMode.STANDALONE（影响提示词和工具）
        - 工具按 subagent_config.tools 过滤
        """
        config = subagent_registry.get(name)
        if not config:
            return None

        from src.core.agent import Agent, AgentMode
        return Agent(
            is_master=True,
            mode=AgentMode.STANDALONE,
            subagent_config=config,
            session_id=session_id,
        )
```

#### 5.3.4 路由层改动

```python
# src/main.py

# 替换全局 master_agent 引用
from src.core.agent_router import agent_router

class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    files: Optional[List[Dict[str, Any]]] = None
    user_id: Optional[str] = None
    subagent: Optional[str] = None  # 新增

# /api/chat 改动（核心逻辑）
@app.post("/api/chat")
async def chat(request: Request):
    data = await request.json()
    subagent_name = data.get("subagent")

    agent = agent_router.get_agent(subagent_name, session_id)

    response_text = await agent.process_message_sync(
        user_input=user_input,
        session_id=session_id,
        user=agent_user,
    )
    return JSONResponse({
        "success": True,
        "response": response_text,
        "session_id": session_id,
        "agent_type": agent.mode.value,
    })

# /api/chat/stream 改动（核心逻辑，在线程内部）
async def chat_stream(http_request: Request, request: ChatRequest):
    agent = agent_router.get_agent(request.subagent, session_id)

    def run_agent():
        # ...
        # 替换 master_agent.process_message() 为 agent.process_message()
        # 替换 master_agent.llm.get_model_name() 为 agent.llm.get_model_name()
        # 替换 master_agent.tool_registry 为 agent.tool_registry
        # ...

    yield sse_event({"type": "connected", "session_id": session_id, "agent_type": agent.mode.value})
```

---

## 六、安全考虑

### 6.1 子智能体能力限制

| 模式 | delegate_to_subagent | 工具限制 | 技能限制 | 会话记忆 |
|------|---------------------|----------|----------|---------|
| MASTER | 可用 | 全部（28 个 + 动态工具） | 全部 | session 级别 |
| STANDALONE | **禁用** | 按 subagent_config 过滤 | 按 skills.allowed | **独立**（自带 ShortTermMemory） |
| SUBAGENT | **禁用** | 按 subagent_config 过滤 | 按 skills.allowed | **无**（每次任务独立） |

### 6.2 会话隔离

- 每个 standalone 模式的子智能体是独立的 Agent 实例，自带独立的 ShortTermMemory
- 缓存按 `{session_id}:{subagent_name}` 隔离
- 会话结束调用 `agent_router.release_session(session_id)` 清理
- 定期调用 `agent_router.cleanup_expired()` 清理长时间未活动的实例

### 6.3 参数校验

- `subagent_name` 必须在 `subagent_registry` 中存在，否则回退到 master_agent
- 前端不可随意指定任意 subagent 名称（白名单机制）

---

## 七、待确认问题

1. **Standalone 模式下是否允许委派给其他子智能体？**
   - 当前设计：**禁止**（出于安全考虑和简化实现）
   - 如果需要允许，需额外设计委派链管理和循环检测

2. **Standalone 模式的超时策略？**
   - 建议与 Master Agent 相同（最多 20 轮迭代）
   - 但因为没有委派开销，实际上会更快

3. **是否需要支持多级委派？（A → B → C）**
   - 当前设计：**不支持**（最多一级委派）
   - 如需支持，需扩展 SubAgentProtocol 增加委派深度追踪

4. **前端如何知道请求被哪个 Agent 处理？**
   - 响应中增加 `agent_type` 字段（已在本设计中包含）
   - SSE 初始 `connected` 事件中也包含 `agent_type`

5. **Standalone agent 缓存的过期清理策略？**
   - 建议 1 小时 TTL，由 APScheduler 定期调用 `cleanup_expired()`
   - 或在 lifespan shutdown 时统一清理

6. **是否保留现有 `create_standalone_agent()` 方法？**
   - 现有方法创建的是通用 master agent（无 subagent_config）
   - 新方法 `create_standalone_subagent()` 创建带专业约束的
   - 建议：保留现有方法，新增方法，两者职责不同

---

## 八、实施计划

### Phase 1：核心改造

1. 在 `src/core/agent.py` 新增 `AgentMode` 枚举
2. `Agent.__init__` 增加 `mode` 参数，调整初始化分支
3. 调整 `_get_tools()` 使用 `self.mode` 判断
4. 调整 `_build_system_prompt()` 使用 `self.mode` 判断
5. `src/core/__init__.py` 导出 `AgentMode`

### Phase 2：路由层

1. 新增 `src/core/agent_router.py`（AgentRouter + 全局实例）
2. 扩展 `src/subagents/factory.py` 增加 `create_standalone_subagent()`
3. `ChatRequest` 新增 `subagent` 字段
4. `/api/chat` 和 `/api/chat/stream` 通过 AgentRouter 选择 agent
5. 响应增加 `agent_type` 字段

### Phase 3：测试与优化

1. 单元测试：AgentMode 各模式的工具列表、提示词构建
2. 集成测试：AgentRouter 路由、缓存、回退逻辑
3. SSE 端点测试：streaming 模式下的 agent 切换
4. 缓存清理测试：`release_session()` 和 `cleanup_expired()`

---

## 九、附录

### A. 相关文件清单

| 文件路径 | 说明 | 改动类型 |
|----------|------|----------|
| `src/core/agent.py` | Agent 核心类 | 修改 |
| `src/core/__init__.py` | Core 模块导出 | 修改 |
| `src/core/agent_router.py` | Agent 路由类 | **新增** |
| `src/subagents/factory.py` | Agent 工厂类（已存在） | 修改 |
| `src/subagents/registry.py` | 子智能体注册表 | 不变 |
| `src/subagents/executor.py` | 子智能体执行器 | 可选修改 |
| `src/subagents/protocol.py` | 子智能体通信协议 | 不变 |
| `src/main.py` | FastAPI 入口 | 修改 |
| `subagents/*/SUBAGENT.md` | 子智能体配置 | 不变 |

### B. 术语表

| 术语 | 说明 |
|------|------|
| Master Agent | 主智能体，负责整体协调和委派（全局单例 `master_agent`） |
| SubAgent | 子智能体，通过 Master 委派执行专业任务 |
| Standalone Agent | 子智能体独立模式，绕过 Master 直接处理用户请求 |
| AgentMode | 枚举类型，标识 Agent 的工作模式 |
| AgentRouter | 路由器，根据请求参数选择使用哪个 Agent 实例 |
| AgentFactory | 工厂类，负责创建不同模式的 Agent 实例 |
| Delegate | 委派，Master Agent 将任务交给 SubAgent 执行 |

### C. 与现有 AgentFactory 的关系

```
AgentFactory（src/subagents/factory.py — 已存在）
├── create_standalone_agent()      → 通用 master agent（无 subagent_config，保留）
├── create_standalone_subagent()   → 带专业约束的独立模式 agent（新增）
├── create_subagent()              → 委派模式子智能体（已存在）
├── create_agent()                 → 模块级便捷函数（已存在）
├── list_available_agents()        → 列出可用子智能体（已存在）
└── create_delegation_tool()       → 创建委派工具定义（已存在）
```
