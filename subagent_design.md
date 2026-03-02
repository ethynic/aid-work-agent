# Subagent 架构设计文档

> 版本：v1.1-draft  
> 日期：2026-02-28  
> 状态：设计讨论阶段

---

## 一、设计理念

### 1.1 核心架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                          MasterAgent                                 │
│  - 全局规划与协调                                                    │
│  - 任务分发与结果整合                                                │
│  - 用户交互                                                          │
└───────────────────────────┬─────────────────────────────────────────┘
                            │ 委托任务（子线程）
            ┌───────────────┼───────────────┐
            ▼               ▼               ▼
    ┌───────────────┐ ┌───────────────┐ ┌───────────────┐
    │ SubAgent      │ │ SubAgent      │ │ SubAgent      │
    │ (code-review) │ │ (data-analyst)│ │ (hr-expert)   │
    │               │ │               │ │               │
    │ 专业工具集    │ │ 专业工具集    │ │ 专业工具集    │
    │ 上下文约束    │ │ 上下文约束    │ │ 上下文约束    │
    └───────────────┘ └───────────────┘ └───────────────┘
```

### 1.2 核心原则

1. **Session是用户级别隔离**：不同用户的对话session独立，同一session内主/子智能体共享session上下文
2. **子智能体在子线程运行**：通过子线程执行，不阻塞主智能体工作流，但共享session直接通信
3. **智能体双重角色**：子智能体既可以被委托执行，也可以独立作为主智能体初始化运行

### 1.3 智能体双重角色示例

```
场景A：HR智能体作为子智能体
┌─────────────┐
│ MasterAgent │ ──委托HR任务──> ┌─────────────┐
│ (通用助手)  │                 │ HR SubAgent │
└─────────────┘                 │ (子线程)    │
                                └─────────────┘

场景B：HR智能体作为独立主智能体
┌─────────────┐
│ HR Agent    │ <── 用户直接使用
│ (主智能体)  │
└─────────────┘
    │
    └── 可委派任务给其他子智能体（如 pdf-expert）
```

---

## 二、Subagent 注册机制

### 2.1 文件结构

```
subagents/
├── code-reviewer/
│   ├── SUBAGENT.md       # 定义文件（必需）
│   ├── tools/            # 专属工具（可选）
│   └── prompts/          # 专属提示词模板（可选）
├── data-analyst/
│   └── SUBAGENT.md
└── pdf-expert/
    └── SUBAGENT.md
```

### 2.2 SUBAGENT.md 配置格式

```yaml
---
# 基本信息
name: code-reviewer
description: 代码审查专家，负责代码质量、安全性和性能分析
version: 1.0.0

# 能力标签（用于自动匹配）
capabilities:
  - code_review
  - security_audit
  - performance_analysis
  - bug_detection

# 触发条件
triggers:
  keywords:
    - 代码审查
    - code review
    - 安全检查
    - 性能分析
  file_patterns:
    - "*.py"
    - "*.js"
    - "*.ts"

# 工具白名单（继承主agent的子集）
tools:
  inherit: false          # 是否继承主agent全部工具
  allowed:
    - web_search
    - skill_execute
    - read_file
    - write_file

# 技能访问
skills:
  allowed:
    - pdf        # 可使用的skill
    - code

# 上下文约束
context:
  max_input_tokens: 4000
  max_output_tokens: 2000
  system_prompt: |
    你是一个专业的代码审查助手...
    你的职责是：...
    
# Session配置
session:
  isolated: true          # 是否隔离记忆
  inherit_context: true   # 是否继承父session上下文
  timeout: 300            # 超时时间（秒）
---
```

### 2.3 注册组件设计

```python
# src/subagents/registry.py
class SubagentRegistry:
    """Subagent注册表"""
    
    def __init__(self):
        self._subagents: Dict[str, SubagentConfig] = {}
        self._loader = SubagentLoader()
        
    def discover(self, subagents_dir: str):
        """自动发现并加载所有subagent定义"""
        
    def match_by_capability(self, task_description: str) -> Optional[str]:
        """根据任务描述匹配合适的subagent"""
        
    def match_by_file(self, file_path: str) -> Optional[str]:
        """根据文件类型匹配subagent"""
        
    def get_config(self, name: str) -> SubagentConfig:
        """获取subagent配置"""
```

---

## 三、Session 共享与任务委托

### 3.1 Session架构（用户级别隔离）

```
用户A的Session (session_id: "user_a_001")
│
├── memory: [用户消息, 规划结果, 执行日志...]
├── context: [共享上下文]
│
├── 主线程: MasterAgent
│   ├── Task 1 (email_send) → 直接执行
│   └── Task 2 (code_review) → 委托
│
├── 子线程1: CodeReviewAgent
│   ├── 共享 session memory
│   ├── 执行 code_review 任务
│   └── 结果写回共享memory
│
└── 子线程2: PDFAgent
    ├── 共享 session memory
    └── 执行 pdf_process 任务
```

**关键点**：
- Session是用户级别隔离，不同用户session完全独立
- 同一session内，主/子智能体共享memory和context
- 子智能体在子线程运行，不阻塞主线程
- 子智能体可直接读写session的共享memory

### 3.2 数据模型

```python
# src/models/subagent.py

class SubagentConfig(BaseModel):
    """Subagent配置定义"""
    name: str
    description: str
    capabilities: List[str]
    triggers: Dict[str, Any]
    tools: Dict[str, Any]
    skills: Dict[str, Any]
    context: Dict[str, Any]
    system_prompt: str
    
    # 委派配置（当作为主智能体时可委派给谁）
    delegatable_to: List[str] = []     # 可委派给的子智能体列表
    allow_delegation: bool = True      # 是否允许委派任务

class SubagentExecutionContext(BaseModel):
    """子智能体执行上下文"""
    execution_id: str                  # 执行ID（唯一）
    session_id: str                    # 所属session（与主智能体共享）
    subagent_name: str                 # subagent名称
    status: str                        # pending/running/completed/failed
    
    # 任务
    delegated_task: Task               # 被委托的任务
    parent_agent: str                  # 委托方智能体名称
    
    # 执行
    thread_id: Optional[str]           # 子线程ID
    execution_log: List[Dict]          # 执行日志
    
    # 结果
    result: Optional[Dict]             # 执行结果
    error: Optional[str]               # 错误信息
    
    # 元数据
    created_at: datetime
    completed_at: Optional[datetime]
    token_usage: Dict[str, int]

class DelegationRequest(BaseModel):
    """委托请求"""
    task: Task
    requester: str                     # 请求方智能体名称
    context_filter: Optional[List[str]] # 需要传递的上下文过滤
    
class DelegationResponse(BaseModel):
    """委托响应"""
    success: bool
    execution_id: str
    result: Optional[Dict]
    error: Optional[str]
    execution_summary: str
```

### 3.3 委托流程

```
1. MasterAgent 规划阶段
   └── LLM识别任务需要subagent → Task.assigned_agent = "code-reviewer"

2. 任务执行阶段
   └── PlanExecutor 检测到 assigned_agent
       └── 调用 SubagentManager.delegate()

3. SubagentManager
   ├── 获取或创建 SubagentInstance
   ├── 传递共享的 session memory
   ├── 在子线程启动执行
   └── 等待结果（或继续其他任务）

4. SubagentInstance（子线程）
   ├── 使用受限工具集
   ├── 直接读写共享session memory
   ├── 执行规划-执行循环
   └── 写入结果到共享memory
```

---

## 四、通信机制

### 4.1 通信模型

由于主/子智能体共享同一session，通信机制简化为：

```
┌─────────────────────────────────────────────────────────────────────┐
│                     Shared Session Memory                            │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │  conversation_history: [...]                                 │    │
│  │  task_results: {task_id: result, ...}                       │    │
│  │  pending_clarifications: [...]                               │    │
│  └─────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────┘
         ▲                                              ▲
         │ 读写                                         │ 读写
         │                                              │
┌────────┴────────┐                          ┌─────────┴─────────┐
│   MasterAgent   │  ──委托(task)──>         │   SubAgent        │
│   (主线程)      │                          │   (子线程)        │
│                 │  <──结果(result)──       │                   │
└─────────────────┘                          └───────────────────┘
```

### 4.2 任务状态同步

```python
# src/subagents/protocol.py

from enum import Enum
from pydantic import BaseModel
from typing import Any, Dict, Optional
from datetime import datetime

class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CLARIFYING = "clarifying"      # 等待澄清

class SubagentTaskRecord(BaseModel):
    """存储在共享memory中的任务记录"""
    task_id: str
    execution_id: str
    subagent_name: str
    status: TaskStatus
    
    # 进度
    progress_percent: float = 0.0
    current_step: str = ""
    
    # 澄清请求（如果需要）
    clarification_request: Optional[str] = None
    
    # 结果
    result: Optional[Dict] = None
    error: Optional[str] = None
    summary: str = ""
    
    # 时间戳
    started_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime] = None
```

### 4.3 澄清机制

当子智能体需要向用户或主智能体请求澄清时：

```
1. SubAgent 需要澄清
   └── 写入 clarification_request 到共享memory
   └── 更新 status = CLARIFYING

2. MasterAgent 检测到 CLARIFYING 状态
   └── 向用户提问或自主回答
   └── 写入答案到共享memory
   └── 更新 status = RUNNING

3. SubAgent 继续执行
   └── 读取澄清答案
   └── 继续处理
```

### 4.4 执行管理器

```python
# src/subagents/executor.py

class SubagentExecutor:
    """子智能体执行管理器"""
    
    def __init__(self, session_memory: 'SessionMemory'):
        self.memory = session_memory
        self._active_executions: Dict[str, asyncio.Task] = {}
        
    async def delegate(
        self,
        task: Task,
        subagent_name: str,
        registry: 'SubagentRegistry'
    ) -> str:
        """在子线程启动子智能体执行"""
        
        # 1. 创建执行记录
        execution_id = self._create_execution_id()
        record = SubagentTaskRecord(
            task_id=task.task_id,
            execution_id=execution_id,
            subagent_name=subagent_name,
            status=TaskStatus.PENDING,
            started_at=datetime.now()
        )
        self.memory.set(f"subagent_task_{execution_id}", record)
        
        # 2. 获取配置并实例化
        config = registry.get_config(subagent_name)
        instance = SubagentInstance(config, self.memory, record)
        
        # 3. 在子线程启动
        async_task = asyncio.create_task(
            instance.run(task),
            name=f"subagent_{subagent_name}_{execution_id}"
        )
        self._active_executions[execution_id] = async_task
        
        return execution_id
        
    async def wait_for_result(
        self, 
        execution_id: str, 
        timeout: float = 300
    ) -> SubagentTaskRecord:
        """等待子智能体执行完成"""
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            record = self.memory.get(f"subagent_task_{execution_id}")
            if record.status in [TaskStatus.COMPLETED, TaskStatus.FAILED]:
                return record
            await asyncio.sleep(0.5)
            
        raise TimeoutError(f"Subagent execution {execution_id} timed out")
        
    async def cancel(self, execution_id: str):
        """取消正在执行的子智能体"""
        if execution_id in self._active_executions:
            self._active_executions[execution_id].cancel()
```

### 4.5 通信流程示例

```
┌─────────────────┐                              ┌─────────────────┐
│   MasterAgent   │                              │    SubAgent     │
│   (主线程)      │                              │   (子线程)      │
└────────┬────────┘                              └────────┬────────┘
         │                                                │
         │  1. 创建任务记录到共享memory                    │
         │  {task_id, status: PENDING}                   │
         │───────────────────────────────────────────────>│
         │                                                │
         │  2. 更新状态: RUNNING                          │
         │<───────────────────────────────────────────────│
         │                                                │
         │  3. 进度更新 (写入memory)                       │
         │  {progress: 30%, step: "分析代码"}             │
         │<───────────────────────────────────────────────│
         │                                                │
         │  4. 澄清请求 (可选)                            │
         │  {status: CLARIFYING, question: "..."}         │
         │<───────────────────────────────────────────────│
         │                                                │
         │  5. 回复澄清                                   │
         │  (写入答案到memory)                            │
         │───────────────────────────────────────────────>│
         │                                                │
         │  6. 完成任务                                   │
         │  {status: COMPLETED, result: {...}}            │
         │<───────────────────────────────────────────────│
         │                                                │
         ▼                                                ▼
```

### 4.6 独立运行模式

当子智能体作为独立主智能体运行时：

```python
# 初始化HR智能体作为独立主智能体
hr_agent = Agent(
    config=registry.get_config("hr-expert"),
    mode="standalone"  # 独立模式
)

# HR智能体可委派任务给其配置的子智能体
# delegatable_to: ["pdf-expert", "email-sender"]
```

---

## 五、核心组件架构

### 5.1 目录结构

```
src/subagents/
├── __init__.py
├── registry.py          # SubagentRegistry - 注册管理
├── loader.py            # SubagentLoader - 配置加载
├── executor.py          # SubagentExecutor - 子线程执行管理
├── instance.py          # SubagentInstance - 运行实例
├── protocol.py          # 任务记录与状态定义
└── config.py            # SubagentConfig 模型

src/models/
└── subagent.py          # 数据模型

subagents/               # Subagent定义目录
├── code-reviewer/
│   └── SUBAGENT.md
├── data-analyst/
│   └── SUBAGENT.md
├── hr-expert/
│   └── SUBAGENT.md
└── ...
```

### 5.2 关键类设计

#### SubagentInstance（统一智能体实例）

```python
# src/subagents/instance.py

class SubagentInstance:
    """智能体实例（可作主智能体或子智能体）"""
    
    def __init__(
        self,
        config: SubagentConfig,
        session_memory: 'SessionMemory',
        execution_context: Optional[SubagentExecutionContext] = None
    ):
        self.config = config
        self.memory = session_memory
        self.execution_context = execution_context  # None表示独立主智能体模式
        
        # 受限的组件（根据配置）
        self.llm = llm_gateway
        self.tool_registry = self._build_tool_registry()
        self.skill_registry = self._build_skill_registry()
        
        # 委派能力（如果配置了delegatable_to）
        self.subagent_executor = None
        if config.allow_delegation and config.delegatable_to:
            self.subagent_executor = SubagentExecutor(session_memory)
        
    @property
    def is_subagent_mode(self) -> bool:
        """是否为子智能体模式"""
        return self.execution_context is not None
        
    async def run(self, task: Optional[Task] = None):
        """执行任务"""
        
        if self.is_subagent_mode:
            # 子智能体模式：执行委托的任务
            await self._run_delegated_task(task)
        else:
            # 独立主智能体模式：处理用户交互循环
            await self._run_standalone_loop()
            
    async def _run_delegated_task(self, task: Task):
        """执行委托的任务"""
        
        # 1. 更新状态为RUNNING
        self._update_status(TaskStatus.RUNNING)
        
        # 2. 构建上下文（从共享memory读取）
        messages = self._build_messages(task)
        
        # 3. 执行agent循环
        try:
            while not self._is_complete():
                response = await self.llm.chat(
                    messages=messages,
                    tools=self._get_tool_definitions(),
                    system_prompt=self.config.system_prompt
                )
                
                # 处理工具调用
                if response.tool_calls:
                    for tool_call in response.tool_calls:
                        result = await self._execute_tool(tool_call)
                        messages.append({"tool_result": result})
                        self._update_progress()
                        
                # 检查澄清需求
                if self._needs_clarification(response):
                    answer = await self._request_clarification(response)
                    messages.append({"user": answer})
                    
            # 4. 写入结果到共享memory
            self._write_result(success=True, result=self._get_result())
            
        except Exception as e:
            self._write_result(success=False, error=str(e))
            
    async def _run_standalone_loop(self):
        """独立主智能体模式：处理用户消息"""
        
        while True:
            # 获取用户输入
            user_input = await self._get_user_input()
            
            # 处理消息（可委派给子智能体）
            response = await self._process_with_delegation(user_input)
            
            # 返回响应
            await self._send_response(response)
            
    async def _process_with_delegation(self, user_input: str) -> str:
        """处理消息，支持委派给子智能体"""
        
        # 构建消息
        messages = self.memory.get_conversation_history()
        messages.append({"role": "user", "content": user_input})
        
        # 调用LLM
        response = await self.llm.chat(
            messages=messages,
            tools=self._get_tool_definitions(include_delegation_tools=True),
            system_prompt=self.config.system_prompt
        )
        
        # 处理工具调用（包括委派工具）
        if response.tool_calls:
            for tool_call in response.tool_calls:
                if tool_call.name == "delegate_to_subagent":
                    # 委派给子智能体
                    result = await self._handle_delegation(tool_call)
                else:
                    result = await self._execute_tool(tool_call)
                messages.append({"tool_result": result})
                
        return self._extract_response(messages)
        
    def _update_status(self, status: TaskStatus):
        """更新任务状态到共享memory"""
        if self.execution_context:
            key = f"subagent_task_{self.execution_context.execution_id}"
            record = self.memory.get(key)
            record.status = status
            record.updated_at = datetime.now()
            self.memory.set(key, record)
            
    def _write_result(self, success: bool, result: Dict = None, error: str = None):
        """写入结果到共享memory"""
        if self.execution_context:
            key = f"subagent_task_{self.execution_context.execution_id}"
            record = self.memory.get(key)
            record.status = TaskStatus.COMPLETED if success else TaskStatus.FAILED
            record.result = result
            record.error = error
            record.completed_at = datetime.now()
            self.memory.set(key, record)
```

#### Agent工厂

```python
# src/subagents/factory.py

class AgentFactory:
    """智能体工厂：统一创建主/子智能体"""
    
    def __init__(self, registry: SubagentRegistry):
        self.registry = registry
        
    def create_standalone_agent(
        self, 
        agent_name: str,
        session_memory: 'SessionMemory'
    ) -> SubagentInstance:
        """创建独立主智能体"""
        config = self.registry.get_config(agent_name)
        return SubagentInstance(
            config=config,
            session_memory=session_memory,
            execution_context=None  # 独立模式
        )
        
    def create_subagent(
        self,
        agent_name: str,
        session_memory: 'SessionMemory',
        execution_context: SubagentExecutionContext
    ) -> SubagentInstance:
        """创建子智能体（委托模式）"""
        config = self.registry.get_config(agent_name)
        return SubagentInstance(
            config=config,
            session_memory=session_memory,
            execution_context=execution_context
        )
```

---

## 六、与现有系统的集成

| 现有组件 | 集成方式 |
|---------|---------|
| `PlanManager` | Task模型增加 `assigned_agent` 字段 |
| `ShortTermMemory` | 主/子智能体共享同一session的memory实例 |
| `SkillRegistry` | Subagent 可访问受限的 skill 子集 |
| `ToolRegistry` | Subagent 使用工具白名单子集 |
| `SandboxManager` | Subagent 继承沙盒能力 |

### 6.1 Task模型扩展

```python
# src/models/plan.py 扩展

class Task(BaseModel):
    task_id: str
    tool_name: str
    description: str
    parameters: Dict[str, Any]
    dependencies: List[str]
    expected_output: str
    status: TaskStatus
    result: Optional[Dict]
    error: Optional[str]
    
    # 新增：委派支持
    assigned_agent: Optional[str] = None    # 指定执行的子智能体
    execution_id: Optional[str] = None      # 子智能体执行ID
```

### 6.2 委派工具定义

```python
# 添加到AGENT_TOOLS

DELEGATE_TOOL = {
    "name": "delegate_to_subagent",
    "description": "将任务委托给专业的子智能体执行",
    "input_schema": {
        "type": "object",
        "properties": {
            "subagent_name": {
                "type": "string",
                "description": "子智能体名称，如 code-reviewer, hr-expert"
            },
            "task_description": {
                "type": "string",
                "description": "任务描述"
            },
            "context_needed": {
                "type": "array",
                "items": {"type": "string"},
                "description": "需要传递的上下文关键词"
            }
        },
        "required": ["subagent_name", "task_description"]
    }
}
```

---

## 七、待讨论问题

### 7.1 并行委托

**问题**：多个子智能体任务是否支持并行执行？

**建议**：支持，通过 `asyncio.gather()` 在不同子线程并行执行

```python
# 并行执行示例
results = await asyncio.gather(
    executor.delegate(task1, "code-reviewer", registry),
    executor.delegate(task2, "pdf-expert", registry),
)
```

### 7.2 嵌套委托

**问题**：子智能体能否再委托给其他子智能体？

**建议**：支持，通过 `delegatable_to` 配置控制

```yaml
# hr-expert 的 SUBAGENT.md
delegatable_to:
  - pdf-expert      # HR可委派PDF任务
  - email-sender    # HR可委派邮件任务
```

### 7.3 上下文传递

**问题**：如何控制传递给子智能体的上下文范围？

**建议**：
- 默认传递完整session memory
- 可通过 `context_filter` 过滤敏感信息
- 子智能体只能访问其被授权的工具/技能

### 7.4 错误恢复

**问题**：子智能体失败后的处理策略？

**建议**：
- 支持重试次数配置
- 失败后主智能体可选择接管或向用户报告
- 超时后自动取消子线程

### 7.5 智能体发现

**问题**：主智能体如何知道有哪些可用的子智能体？

**建议**：
- 系统提示词中注入可用子智能体列表及其能力描述
- 动态匹配：根据任务描述自动推荐合适的子智能体

---

## 八、实现计划

### Phase 1：基础框架
- [ ] 创建数据模型 (`src/models/subagent.py`)
- [ ] 实现 `SubagentLoader` 和 `SubagentRegistry`
- [ ] 实现 `SubagentConfig` 解析（含 `delegatable_to` 配置）

### Phase 2：核心运行时
- [ ] 实现 `SubagentInstance`（支持双重模式）
- [ ] 实现 `SubagentExecutor`（子线程管理）
- [ ] 实现工具/技能过滤机制
- [ ] 实现任务状态同步到共享memory

### Phase 3：委派与通信
- [ ] 实现任务记录协议 (`protocol.py`)
- [ ] 实现澄清机制
- [ ] 实现 `delegate_to_subagent` 工具
- [ ] 实现 `AgentFactory`

### Phase 4：集成与测试
- [ ] 扩展 `Task` 模型（增加 `assigned_agent`）
- [ ] 集成到 `MasterAgent`
- [ ] 编写测试用例
- [ ] 创建示例 subagent 定义（hr-expert, code-reviewer 等）

### Phase 5：独立运行支持
- [ ] 实现独立主智能体启动入口
- [ ] 实现子智能体发现与注入到系统提示词
- [ ] 端到端测试（独立模式 + 委派模式）

---

## 九、使用示例

### 9.1 创建独立HR智能体应用

```python
# main_hr_agent.py
from src.subagents.factory import AgentFactory
from src.subagents.registry import SubagentRegistry
from src.core.memory import SessionMemory

# 加载配置
registry = SubagentRegistry()
registry.discover("subagents/")

# 创建HR智能体（独立主智能体模式）
factory = AgentFactory(registry)
session = SessionMemory(session_id="hr_session_001")

hr_agent = factory.create_standalone_agent(
    agent_name="hr-expert",
    session_memory=session
)

# 启动交互循环
await hr_agent.run()
```

### 9.2 主智能体委派任务给子智能体

```python
# 主智能体处理用户请求
user_input = "帮我审查这段代码的安全性"

# LLM可能返回工具调用：
tool_call = {
    "name": "delegate_to_subagent",
    "arguments": {
        "subagent_name": "code-reviewer",
        "task_description": "审查代码安全性，重点关注SQL注入、XSS等漏洞",
        "context_needed": ["user_code"]
    }
}

# 主智能体执行委派
result = await subagent_executor.delegate(
    task=Task(
        task_id="review_001",
        description="代码安全审查",
        assigned_agent="code-reviewer"
    ),
    subagent_name="code-reviewer",
    registry=registry
)
```

### 9.3 HR智能体委派任务给PDF专家

```python
# HR智能体收到请求："帮我分析这份PDF简历"
# hr-expert 配置了 delegatable_to: ["pdf-expert"]

# HR智能体可以委派给pdf-expert
result = await hr_agent._handle_delegation({
    "subagent_name": "pdf-expert",
    "task_description": "提取PDF简历中的关键信息"
})
```

---

## 九、参考资料

- 现有架构：`src/core/agent.py` (MasterAgent)
- 技能系统：`src/core/skill_registry.py`, `src/core/skill_loader.py`
- 工具系统：`src/tools/registry.py`, `src/tools/base.py`
- 规划系统：`src/core/plan_manager.py`, `src/models/plan.py`
