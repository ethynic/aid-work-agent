# 主子智能体（Master/SubAgent）设计文档

## 一、文档概述

本文档描述 AID Work Agent 项目中**主智能体（Master Agent）**与**子智能体（SubAgent）**的架构设计，包括：

1. **现有设计复盘**：已实现的架构和机制
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
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Master Agent                                 │
│                   src/core/agent.py                              │
│  is_master=True                                                │
│  - 拥有 delegate_to_subagent 工具                               │
│  - 维护 SubagentRegistry                                        │
│  - 维护 SubagentExecutor                                        │
└─────────────────────────────────────────────────────────────────┘
           │                                    │
           │ delegate_to_subagent               │
           │ (LLM 决策或关键词匹配)              │
           ▼                                    ▼
┌─────────────────────────────────────────────────────────────────┐
│                    SubagentExecutor                              │
│              src/subagents/executor.py                           │
│  - 创建子智能体 Agent 实例                                       │
│  - 在独立线程中异步执行                                          │
│  - 管理任务状态同步                                              │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                   SubAgent Instance                              │
│            Agent(is_master=False, subagent_config=...)            │
│  - 使用子智能体专属的 system_prompt                              │
│  - 工具根据配置可能被过滤                                        │
│  - 无 delegate_to_subagent 工具                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 核心组件

#### 2.2.1 Agent 类（`src/core/agent.py`）

**核心属性**：
| 属性 | 说明 |
|------|------|
| `is_master` | bool，True 为主智能体，False 为子智能体 |
| `subagent_config` | 子智能体配置对象 |
| `subagent_registry` | 主智能体独有，管理所有子智能体注册 |
| `subagent_executor` | 主智能体独有，执行委派任务 |
| `tool_registry` | 工具注册表 |
| `skill_registry` | 技能注册表 |
| `memory` | 短期记忆（session 级别） |
| `plan_manager` | 计划管理器 |

**核心方法**：
| 方法 | 说明 |
|------|------|
| `process_message()` | 主智能体循环：处理用户消息，yield 响应片段 |
| `process_message_sync()` | `process_message` 的同步封装 |
| `execute_as_subagent()` | 子智能体执行入口 |
| `_build_base_system_prompt()` | 构建系统提示词（区分主/子） |
| `_get_tools()` | 获取工具列表（含委派工具，仅主智能体） |

#### 2.2.2 SubagentRegistry 类（`src/subagents/registry.py`）

**职责**：
- 从 `subagents/` 目录加载所有 SUBAGENT.md 配置
- 提供按名称、能力、关键词匹配子智能体
- 生成子智能体描述供 LLM 使用
- 提供委派工具定义（`delegate_to_subagent`）

**关键方法**：
| 方法 | 说明 |
|------|------|
| `get(name)` | 获取指定名称的子智能体配置 |
| `match_by_capability(desc)` | 根据任务描述匹配子智能体 |
| `match_by_file(filename)` | 根据文件类型匹配子智能体 |
| `get_delegation_tool_definition()` | 获取委派工具的 JSON Schema |

#### 2.2.3 SubagentExecutor 类（`src/subagents/executor.py`）

**职责**：
- 在独立线程/协程中创建和启动子智能体 Agent 实例
- 管理任务状态同步（待处理 → 运行中 → 完成/失败）
- 处理超时和取消
- 支持澄清机制（re-delegate 模式）

**关键方法**：
| 方法 | 说明 |
|------|------|
| `delegate()` | 委派任务给子智能体，返回 execution_id |
| `wait_for_result()` | 等待子智能体执行完成 |
| `handle_clarification()` | 处理澄清请求 |
| `cancel()` | 取消执行 |

#### 2.2.4 SUBAGENT.md 配置格式

```yaml
---
name: 外贸获客智能体
description: 外贸获客智能体，帮助企业快速获取海外潜在客户资源
version: 1.0.0

capabilities:
  - lead_matching
  - email_marketing

triggers:
  keywords:
    - 外贸
    - 客户匹配
    - 邮件营销
    ...

tools:
  inherit: true           # 是否继承主智能体全部工具
  additional:
    - content_generate    # 额外添加的工具

skills:
  allowed:
    - pdf
    - email
    - trade-customer

context:
  max_input_tokens: 8000
  max_output_tokens: 4000

system_prompt: |
  ## 外贸专员职责
  ...
---
```

### 2.3 工作流程

#### 2.3.1 Master Agent 的 process_message 循环

```
1. 检查是否有待处理的澄清回复
   └── 有 → re-delegate 给子智能体（携带补充信息）
   
2. 添加时间戳上下文

3. 处理附件
   └── 匹配 Skill（自动加载）
   └── 保存文件到临时工作目录
   
4. 进入 LLM 迭代循环（最多 20 轮）
   ├── 调用 llm_gateway.chat_with_tools()
   │   └── 传入系统提示词 + 历史消息 + 工具列表
   ├── 如果 LLM 返回工具调用：
   │   ├── delegate_to_subagent → 交给 SubagentExecutor 执行
   │   ├── use_skill → 加载技能上下文
   │   ├── skill_execute → 执行技能命令
   │   ├── create_plan → 调用 PlanManager 创建计划
   │   └── 其他工具 → ToolExecutor 执行
   └── 如果 LLM 返回文本回复 → yield 并退出循环
```

#### 2.3.2 子智能体执行流程（execute_as_subagent）

```
1. 构建消息列表
   └── 仅有系统提示词 + task_description（不使用历史消息）

2. 进入 LLM 迭代循环（最多 20 轮）
   ├── 调用 llm_gateway.chat_with_tools()
   ├── 执行工具调用
   └── 无工具调用时 yield 最终回复

3. 返回结果给 SubagentExecutor
```

### 2.4 系统提示词构建

**主智能体**（`_build_base_system_prompt(include_delegation=True)`）：
- 包含 `delegate_to_subagent` 工具说明
- 列出所有可用子智能体描述
- 包含委派决策逻辑（何时直接委派，何时创建计划）

**子智能体**（`_build_base_system_prompt(include_delegation=False, subagent_constraint=配置.system_prompt)`）：
- 不包含委派工具
- 追加子智能体专属的 system_prompt
- 添加"不能委派"的限制说明

---

## 三、新需求：前端直接指定子智能体模式

### 3.1 需求描述

当前架构中，用户请求统一由 Master Agent 处理，LLM 根据任务内容决定是否委派给子智能体。

**新需求**：前端通过 `subagent` 参数直接指定使用某个子智能体，此时该子智能体应**作为主智能体工作**（拥有完整能力），而不是被 Master Agent 委派执行。

### 3.2 需求分析

| 场景 | 当前行为 | 期望行为 |
|------|----------|----------|
| 用户不指定 subagent | Master Agent 处理，可能委派 | 不变 |
| 用户指定 `subagent=trade-specialist` | Master Agent 委派给 trade-specialist 子智能体 | trade-specialist **直接作为主智能体**处理请求，拥有完整工具和能力 |

**关键区别**：
- **委派模式**：Master Agent 保持控制权，子智能体是执行者
- **直接模式**：子智能体独立工作，拥有 Master Agent 的全部能力

### 3.3 设计方案

#### 3.3.1 路由层改造

**修改文件**：`src/main.py`（ChatRequest 模型和 chat API）

```python
class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    files: Optional[List[Dict[str, Any]]] = None
    user_id: Optional[str] = None
    subagent: Optional[str] = None  # 新增：直接指定子智能体
```

**API 逻辑调整**：

```python
@app.post("/api/chat")
async def chat(request: Request):
    data = await request.json()
    subagent_name = data.get("subagent")  # 获取 subagent 参数
    
    if subagent_name:
        # 直接模式：使用子智能体作为主智能体
        agent = get_or_create_subagent_agent(subagent_name, session_id)
    else:
        # 默认模式：使用 Master Agent
        agent = master_agent
    
    # 后续处理相同
```

#### 3.3.2 Agent 实例管理

**方案：扩展 Agent 类支持两种模式**

```python
class Agent:
    def __init__(
        self,
        is_master: bool = True,
        subagent_config: Optional[SubagentConfig] = None,
        session_id: Optional[str] = None,
        execution_id: Optional[str] = None,
        parent_plan_manager: Optional['PlanManager'] = None,
        # 新增参数
        standalone_mode: bool = False,  # 子智能体独立作为主智能体模式
    ):
        self.standalone_mode = standalone_mode
        
        if standalone_mode:
            # 子智能体独立模式：拥有主智能体全部能力
            self.is_master = True  # 强制为主智能体
            self.subagent_config = subagent_config
            # 工具注册：使用子智能体配置的 allowed 工具
            # 但不限制委派能力（standalone 模式下可以委派给其他子智能体）
            # 系统提示词：使用子智能体的 system_prompt 作为基础
        else:
            # 原有逻辑
            self.is_master = is_master
```

#### 3.3.3 系统提示词构建调整

**Standalone 模式下**：

```python
def _build_base_system_prompt(self, ...):
    if self.standalone_mode:
        # 子智能体独立模式：
        # 1. 不包含委派指南（避免自我委派或循环委派）
        # 2. 使用子智能体的 system_prompt 作为专业约束
        # 3. 包含完整工具列表（基于 allowed 配置）
        return self._build_base_system_prompt(
            include_delegation=False,  # 禁用委派
            subagent_constraint=self.subagent_config.system_prompt,
            user=user
        )
    else:
        # 原逻辑
        ...
```

#### 3.3.4 防止循环委派

当子智能体处于 standalone_mode 时：
1. **禁止调用 `delegate_to_subagent`**
2. 系统提示词中明确说明这一限制
3. 工具列表中不包含委派工具

```python
def _get_tools(self) -> List[Dict[str, Any]]:
    tools = list(AGENT_TOOLS)
    
    # 添加技能工具
    if self.skill_registry:
        tools.append(self.skill_registry.get_skill_tool_definition())
    
    # 仅在非 standalone 模式下添加委派工具
    if self.is_master and not self.standalone_mode and self.subagent_registry:
        delegation_tool = self.subagent_registry.get_delegation_tool_definition()
        if delegation_tool:
            tools.append(delegation_tool)
    
    return tools
```

### 3.4 数据流对比

#### 当前委派模式

```
用户请求
    │
    ▼
Master Agent (process_message)
    │
    │ LLM 决定委派
    ▼
delegate_to_subagent 工具
    │
    ▼
SubagentExecutor.delegate()
    │
    ▼
SubAgent Instance (execute_as_subagent)
    │
    │ 结果返回
    ▼
Master Agent 整合结果
    │
    ▼
返回给用户
```

#### 新增直接模式

```
用户请求 (subagent=trade-specialist)
    │
    ▼
路由层检测到 subagent 参数
    │
    ▼
创建/获取 StandaloneAgent(subagent_name="trade-specialist")
    │
    ▼
Agent(standalone_mode=True)
    │
    │ 直接作为主智能体处理
    ▼
process_message() 循环
    │
    │ （禁止委派）
    ▼
返回结果给用户
```

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
| Agent 独立性 | 子智能体依赖 Master 发起 | 增强子智能体的独立性，支持独立运行 |
| 消息协议 | 通过 memory 共享 | 定义清晰的 SubAgentProtocol，统一消息格式 |

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

#### 建议 2：分离 AgentFactory

```python
# src/core/agent_factory.py
class AgentFactory:
    @staticmethod
    def create_master_agent() -> Agent:
        """创建主智能体"""
        return Agent(is_master=True)
    
    @staticmethod
    def create_subagent(name: str, session_id: str) -> Optional[Agent]:
        """创建子智能体（委派模式）"""
        config = subagent_registry.get(name)
        if not config:
            return None
        return Agent(
            is_master=False,
            subagent_config=config,
            session_id=session_id,
        )
    
    @staticmethod
    def create_standalone_agent(name: str, session_id: str) -> Optional[Agent]:
        """创建子智能体（独立模式，直接作为主智能体）"""
        config = subagent_registry.get(name)
        if not config:
            return None
        return Agent(
            is_master=True,  # 视为主智能体
            subagent_config=config,
            session_id=session_id,
            mode=AgentMode.STANDALONE,
        )
```

#### 建议 3：统一入口点

```python
# src/core/agent_router.py
class AgentRouter:
    def __init__(self):
        self.master_agent = AgentFactory.create_master_agent()
        self._standalone_agents: Dict[str, Agent] = {}  # 缓存独立模式的子智能体
    
    def get_agent(self, subagent_name: Optional[str], session_id: str) -> Agent:
        if not subagent_name:
            return self.master_agent
        
        cache_key = f"{session_id}:{subagent_name}"
        if cache_key not in self._standalone_agents:
            agent = AgentFactory.create_standalone_agent(subagent_name, session_id)
            if agent:
                self._standalone_agents[cache_key] = agent
            else:
                return self.master_agent  # 回退到主智能体
        
        return self._standalone_agents[cache_key]
    
    def release_standalone_agent(self, session_id: str, subagent_name: str):
        """释放独立模式的子智能体实例"""
        cache_key = f"{session_id}:{subagent_name}"
        self._standalone_agents.pop(cache_key, None)
```

#### 建议 4：动态工具集

当前实现一次性返回所有工具，可以考虑按阶段/上下文动态提供：

```python
def _get_tools(self, context: Optional[Dict] = None) -> List[Dict[str, Any]]:
    """获取工具列表，可根据上下文动态调整"""
    tools = list(AGENT_TOOLS)
    
    # 基础工具始终可用
    if self.skill_registry:
        tools.append(self.skill_registry.get_skill_tool_definition())
    
    # 仅主智能体（非 standalone）有委派能力
    if self.is_master and self.mode != AgentMode.STANDALONE:
        if self.subagent_registry:
            delegation_tool = self.subagent_registry.get_delegation_tool_definition()
            if delegation_tool:
                tools.append(delegation_tool)
    
    # 根据上下文过滤工具（可选）
    if context:
        if context.get("phase") == "planning":
            # 计划阶段，提供 plan 相关工具
            pass
        elif context.get("phase") == "execution":
            # 执行阶段，提供执行工具
            pass
    
    return tools
```

#### 建议 5：SubAgent Protocol 规范化

定义清晰的子智能体通信协议：

```python
# src/subagents/protocol.py
from typing import Optional, Dict, Any, Literal
from pydantic import BaseModel

class SubAgentMessage(BaseModel):
    """子智能体消息协议"""
    type: Literal["task", "result", "error", "clarification", "progress"]
    execution_id: str
    content: str
    metadata: Optional[Dict[str, Any]] = None

class SubAgentTask(BaseModel):
    """子智能体任务描述"""
    task_id: str
    subagent_name: str
    description: str
    context: Optional[Dict[str, Any]] = None
    timeout: int = 7200

class SubAgentResult(BaseModel):
    """子智能体执行结果"""
    execution_id: str
    success: bool
    result: Optional[Any] = None
    summary: str
    error: Optional[str] = None
    token_usage: Optional[Dict] = None
```

#### 建议 6：会话隔离增强

当前 standalone 模式的子智能体应维护独立的会话状态：

```python
class StandaloneAgentSession:
    """独立模式子智能体的会话管理"""
    def __init__(self, agent: Agent, session_id: str):
        self.agent = agent
        self.session_id = session_id
        self.created_at = datetime.now()
        self.last_active = datetime.now()
        self.history: List[Dict] = []  # 独立的对话历史
    
    def add_to_history(self, role: str, content: str):
        self.history.append({"role": role, "content": content})
        self.last_active = datetime.now()
    
    def get_context(self) -> List[Dict]:
        return self.history
```

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
│                      src/core/agent_router.py                            │
│  ┌─────────────────┐    ┌──────────────────────────────────────────────┐  │
│  │ get_agent()     │    │ 逻辑：                                        │  │
│  │                 │    │ 1. subagent=None → MasterAgent               │  │
│  │ session_id      │    │ 2. subagent=xxx → StandaloneAgent            │  │
│  │ subagent_name   │    │    (创建/获取缓存的独立模式子智能体实例)        │  │
│  └─────────────────┘    └──────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────┘
            │                               │
            ▼                               ▼
┌─────────────────────────┐    ┌─────────────────────────────────────────────┐
│     MasterAgent         │    │        StandaloneAgent                     │
│  Agent(mode=MASTER)      │    │  Agent(mode=STANDALONE)                    │
│                          │    │                                             │
│ - 完整工具集              │    │ - 独立处理请求                               │
│ - 有 delegate 能力        │    │ - 子智能体 system_prompt                    │
│ - 维护 SubagentRegistry  │    │ - 基于 allowed 工具列表                     │
│                          │    │ - 禁止委派（禁止 delegate_to_subagent）      │
└─────────────────────────┘    └─────────────────────────────────────────────┘
            │                               │
            │ 委派                           │ 不委派
            ▼                               ▼
┌─────────────────────────┐    ┌─────────────────────────────────────────────┐
│  SubagentExecutor       │    │              直接处理                        │
│                         │    │         process_message()                   │
│ - 创建 SubAgent Instance │    │                                             │
│ - 异步执行               │    │                                             │
│ - 返回结果给 Master      │    └─────────────────────────────────────────────┘
└─────────────────────────┘
            │
            ▼
┌─────────────────────────┐
│   SubAgent Instance     │
│  Agent(mode=SUBAGENT)   │
│                        │
│ - 使用 subagent config  │
│ - 无委派能力            │
│ - 专属 system_prompt    │
└─────────────────────────┘
```

### 5.2 关键改动清单

| 序号 | 文件 | 改动内容 |
|------|------|----------|
| 1 | `src/core/agent.py` | 新增 `AgentMode` 枚举；新增 `standalone_mode` 参数；调整 `_get_tools()` 逻辑 |
| 2 | `src/core/agent_router.py` | 新增 `AgentRouter` 类，统一管理 Agent 实例 |
| 3 | `src/main.py` | `ChatRequest` 新增 `subagent` 字段；路由逻辑调用 `AgentRouter` |
| 4 | `src/models/subagent.py` | 可选：新增 `AgentMode` 相关配置 |

### 5.3 核心代码变更

#### 5.3.1 Agent 类改造

```python
from enum import Enum

class AgentMode(Enum):
    MASTER = "master"
    SUBAGENT = "subagent"
    STANDALONE = "standalone"

class Agent:
    def __init__(
        self,
        is_master: bool = True,
        subagent_config: Optional[SubagentConfig] = None,
        session_id: Optional[str] = None,
        execution_id: Optional[str] = None,
        parent_plan_manager: Optional['PlanManager'] = None,
        mode: AgentMode = AgentMode.MASTER,
    ):
        self.is_master = is_master
        self.mode = mode
        self.subagent_config = subagent_config
        # ...
        
        # Standalone 模式下，强制 is_master=True 以获取完整能力
        if mode == AgentMode.STANDALONE:
            self.is_master = True
    
    def _get_tools(self) -> List[Dict[str, Any]]:
        tools = list(AGENT_TOOLS)
        
        if self.skill_registry:
            tools.append(self.skill_registry.get_skill_tool_definition())
        
        # 仅在 MASTER 模式下（有委派能力）添加委派工具
        # STANDALONE 和 SUBAGENT 模式都禁止委派
        if self.is_master and self.mode == AgentMode.MASTER:
            if self.subagent_registry and len(self.subagent_registry) > 0:
                delegation_tool = self.subagent_registry.get_delegation_tool_definition()
                if delegation_tool:
                    tools.append(delegation_tool)
        
        return tools
    
    def _build_system_prompt(self, user: Optional[User] = None) -> str:
        if self.mode == AgentMode.STANDALONE:
            # Standalone 模式：使用子智能体配置，但禁止委派
            subagent_constraint = ""
            if self.subagent_config and self.subagent_config.system_prompt:
                subagent_constraint = self.subagent_config.system_prompt
            return self._build_base_system_prompt(
                include_delegation=False,  # 禁用委派
                subagent_constraint=subagent_constraint,
                user=user
            )
        elif self.mode == AgentMode.SUBAGENT:
            # 子智能体模式：使用配置，无委派能力
            subagent_constraint = ""
            if self.subagent_config and self.subagent_config.system_prompt:
                subagent_constraint = self.subagent_config.system_prompt
            return self._build_base_system_prompt(
                include_delegation=False,
                subagent_constraint=subagent_constraint,
                user=user
            )
        else:
            # 主智能体模式
            return self._build_base_system_prompt(include_delegation=True, user=user)
```

#### 5.3.2 AgentRouter 类

```python
# src/core/agent_router.py
from typing import Optional, Dict

class AgentRouter:
    def __init__(self):
        from src.core.agent import Agent
        self.master_agent = Agent(mode=AgentMode.MASTER)
        self._standalone_cache: Dict[str, Agent] = {}
    
    def get_agent(
        self,
        subagent_name: Optional[str],
        session_id: str,
    ) -> 'Agent':
        if not subagent_name:
            return self.master_agent
        
        cache_key = f"{session_id}:{subagent_name}"
        if cache_key not in self._standalone_cache:
            from src.core.agent_factory import AgentFactory
            agent = AgentFactory.create_standalone_agent(subagent_name, session_id)
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
```

#### 5.3.3 AgentFactory 类

```python
# src/core/agent_factory.py
from typing import Optional
from src.core.agent import Agent, AgentMode
from src.subagents.registry import subagent_registry

class AgentFactory:
    @staticmethod
    def create_master_agent() -> Agent:
        return Agent(mode=AgentMode.MASTER)
    
    @staticmethod
    def create_subagent(name: str, session_id: str) -> Optional[Agent]:
        config = subagent_registry.get(name)
        if not config:
            return None
        return Agent(
            is_master=False,
            mode=AgentMode.SUBAGENT,
            subagent_config=config,
            session_id=session_id,
        )
    
    @staticmethod
    def create_standalone_agent(name: str, session_id: str) -> Optional[Agent]:
        config = subagent_registry.get(name)
        if not config:
            return None
        return Agent(
            is_master=True,  # 视为主智能体
            mode=AgentMode.STANDALONE,
            subagent_config=config,
            session_id=session_id,
        )
```

#### 5.3.4 路由层改动

```python
# src/main.py

# 全局 AgentRouter
agent_router = AgentRouter()

class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    files: Optional[List[Dict[str, Any]]] = None
    user_id: Optional[str] = None
    subagent: Optional[str] = None  # 新增

@app.post("/api/chat")
async def chat(request: Request):
    try:
        data = await request.json()
        user_input = data.get("message", "")
        user_id = data.get("user_id", "web_user")
        session_id = data.get("session_id")
        subagent_name = data.get("subagent")  # 获取 subagent 参数
        
        # 选择 Agent
        agent = agent_router.get_agent(subagent_name, session_id)
        
        # 处理消息
        response_text = await agent.process_message_sync(
            user_input=user_input,
            session_id=session_id,
        )
        
        return JSONResponse({
            "success": True,
            "response": response_text,
            "session_id": session_id,
        })
    except Exception as e:
        logger.error(f"Failed to process chat request: {e}", exc_info=True)
        return JSONResponse({
            "success": False,
            "error": str(e),
        }, status_code=500)
```

---

## 六、安全考虑

### 6.1 子智能体能力限制

| 模式 | delegate_to_subagent | 工具限制 | 技能限制 |
|------|---------------------|----------|----------|
| MASTER | ✅ 可用 | 全部 | 全部 |
| STANDALONE | ❌ 禁用 | 基于 allowed | 基于 allowed |
| SUBAGENT | ❌ 禁用 | 基于 allowed | 基于 allowed |

### 6.2 会话隔离

- 每个 standalone 模式的子智能体有独立的对话历史
- 会话结束时清理对应的缓存
- 不同时共享 memory（除非显式配置）

---

## 七、待确认问题

1. **Standalone 模式下是否允许委派给其他子智能体？**
   - 当前设计：**禁止**（出于安全考虑）
   - 如果需要允许，需额外设计委派链管理

2. **Standalone 模式的超时策略？**
   - 建议与 Master Agent 相同（最多 20 轮迭代）

3. **是否需要支持多级委派？（A → B → C）**
   - 当前设计：**不支持**（最多一级委派）
   - 如需支持，需扩展 SubAgentProtocol

4. **前端如何知道请求被哪个 Agent 处理？**
   - 建议响应中增加 `agent_type` 字段

---

## 八、实施计划

### Phase 1：基础改造（核心功能）

1. 新增 `AgentMode` 枚举
2. 改造 `Agent` 类支持 `standalone_mode`
3. 新增 `AgentFactory` 类
4. 调整工具列表逻辑（禁用 STANDALONE 的委派）

### Phase 2：路由集成

1. 新增 `AgentRouter` 类
2. 修改 `ChatRequest` 模型
3. 修改 chat API 调用逻辑

### Phase 3：测试与优化

1. 单元测试覆盖
2. 集成测试
3. 性能优化（缓存策略）

---

## 九、附录

### A. 相关文件清单

| 文件路径 | 说明 |
|----------|------|
| `src/core/agent.py` | Agent 核心类 |
| `src/core/agent_factory.py` | Agent 工厂类（新增） |
| `src/core/agent_router.py` | Agent 路由类（新增） |
| `src/subagents/registry.py` | 子智能体注册表 |
| `src/subagents/executor.py` | 子智能体执行器 |
| `src/subagents/protocol.py` | 子智能体通信协议 |
| `src/main.py` | FastAPI 入口 |
| `subagents/*/SUBAGENT.md` | 子智能体配置 |

### B. 术语表

| 术语 | 说明 |
|------|------|
| Master Agent | 主智能体，负责整体协调和委派 |
| SubAgent | 子智能体，负责执行专业任务 |
| STANDALONE | 子智能体独立模式，作为主智能体直接处理请求 |
| Delegate | 委派，Master Agent 将任务交给 SubAgent 执行 |
