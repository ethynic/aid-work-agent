# 定时任务系统设计文档

> 版本：v2.0  
> 日期：2026-03-31  
> 状态：已确认，待实施

---

## 1. 需求概述

### 1.1 核心需求

| # | 需求 | 描述 |
|---|------|------|
| R1 | 定时执行机制 | 用户在 Agent 对话中可以创建定时任务，系统在后台按计划自动执行 |
| R2 | 先试后定 | 定时任务在 Agent 中先执行一次验证，成功后才创建定时任务；失败则提示用户"当前无法完成此类定时任务" |
| R3 | 提示词解析 | 解析用户需求，生成一个能在后台独立执行的完整提示词（不依赖对话上下文） |
| R4 | Agent 执行 | 定时任务由 Agent 执行（复用现有 `process_message_sync` 能力） |
| R5 | 用户绑定 | 定时任务绑定到用户（`user_id`），执行时使用该用户的上下文 |
| R6 | 线程隔离 | 定时任务在独立后台线程中执行，不影响 Web 请求处理线程 |
| R7 | 详细日志 | 记录每次执行的用户、任务、时间、结果、错误等详细信息 |
| R8 | 专属会话 | 每个用户拥有一个固定的定时任务专属会话，执行结果保存到该会话中 |
| R9 | 前端管理页面 | 前端提供"我的定时任务"页面，支持查看/暂停/恢复/取消定时任务 |
| R10 | 支持子智能体委派 | 定时任务不限定主 Agent，也支持委派给子智能体执行 |

### 1.2 典型场景

```
用户：每天早上9点，帮我检查一下邮箱有没有未读邮件，如果有就汇总发给我

Agent 思考：
  1. 识别出"定时任务"意图
  2. 先执行一次（读取未读邮件 + 汇总 + 发送）
  3. 如果成功 → 生成独立可执行提示词 → 创建定时任务
  4. 如果失败 → 告知用户"我目前无法完成此类定时任务"
```

---

## 2. 架构设计

### 2.1 整体架构

```
用户对话（Agent Loop）
    ↓ 用户提出定时任务需求
    ↓
[Agent] 调用 create_scheduled_task 工具
    ↓
[ScheduledTaskTool] 
    ├─ 1. 先试执行：构建独立提示词 → 调用 agent.process_message_sync()
    ├─ 2. 试执行成功 → 保存到数据库 → 注册到 Scheduler
    └─ 3. 试执行失败 → 返回错误，提示用户无法完成
    ↓
[APScheduler] 后台调度线程
    ↓ 到点触发
    ↓
[ScheduledTaskExecutor] 独立执行
    ├─ 构建 User 对象（从 DB 加载）
    ├─ 调用 agent.process_message_sync(prompt, session_id, user)
    ├─ 将结果写入 chat_messages + chat_records
    └─ 记录执行日志到 scheduled_task_logs
```

### 2.2 技术选型

| 组件 | 选型 | 理由 |
|------|------|------|
| 调度引擎 | **APScheduler** | 轻量级、纯 Python、支持 cron 表达式、可与 asyncio 集成 |
| 执行线程 | **BackgroundScheduler**（线程池） | 独立于 uvicorn 的 web 线程，互不干扰 |
| 提示词生成 | **Agent 直接组织** | 由 Agent 在对话中直接生成独立可执行的 `task_prompt`，无需额外工具调用 |
| 执行结果存储 | **用户专属会话** | 每个用户一个固定 `session_id`（`cron_{user_id}`），所有定时任务执行结果写入该会话 |

> **为什么选 APScheduler 而非 Celery？**
> - Celery 需要额外的消息中间件（Redis/RabbitMQ），运维复杂
> - APScheduler 纯内存+SQLite 存储，与项目现有的 SQLite 方案一致
> - 任务量级（企业几十~几百用户）APScheduler 完全胜任
> - 部署简单，无需额外服务

---

## 3. 数据库设计

### 3.1 新增表：`scheduled_tasks`

存储定时任务的配置信息。

```sql
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT UNIQUE NOT NULL,          -- 任务唯一ID: sched_<uuid>
    user_id TEXT NOT NULL,                 -- 所属用户
    name TEXT NOT NULL,                    -- 任务名称（简短描述）
    description TEXT,                      -- 任务详细描述（用户原始需求）
    task_prompt TEXT NOT NULL,             -- 独立可执行的提示词（LLM生成）
    schedule_type TEXT NOT NULL,           -- 调度类型: daily, weekly, monthly, interval, once
    cron_expression TEXT,                  -- cron 表达式（如 "0 9 * * *" 每天9点）
    interval_seconds INTEGER,              -- 间隔秒数（interval 类型用）
    session_id TEXT,                       -- 关联的会话ID（来源对话）
    status TEXT DEFAULT 'active',          -- 状态: active, paused, completed, cancelled
    max_retries INTEGER DEFAULT 3,         -- 最大重试次数
    retry_count INTEGER DEFAULT 0,         -- 已重试次数
    last_run_at TIMESTAMP,                 -- 上次执行时间
    next_run_at TIMESTAMP,                 -- 下次执行时间
    total_runs INTEGER DEFAULT 0,          -- 总执行次数
    success_count INTEGER DEFAULT 0,       -- 成功次数
    fail_count INTEGER DEFAULT 0,          -- 失败次数
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_user 
    ON scheduled_tasks(user_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_next_run 
    ON scheduled_tasks(next_run_at, status);
```

### 3.2 新增表：`scheduled_task_logs`

存储每次定时任务的执行记录。

```sql
CREATE TABLE IF NOT EXISTS scheduled_task_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    log_id TEXT UNIQUE NOT NULL,           -- 日志唯一ID: slog_<uuid>
    task_id TEXT NOT NULL,                 -- 关联的定时任务
    user_id TEXT NOT NULL,                 -- 执行用户
    session_id TEXT,                       -- 执行时的会话ID
    status TEXT NOT NULL,                  -- 执行状态: success, failed, timeout
    trigger_type TEXT NOT NULL,            -- 触发方式: scheduled, manual
    result_summary TEXT,                   -- 执行结果摘要
    result_detail TEXT,                    -- 执行结果详情（JSON，截断）
    error_message TEXT,                    -- 错误信息
    error_trace TEXT,                      -- 错误堆栈（敏感信息已过滤）
    duration_ms INTEGER DEFAULT 0,         -- 执行耗时（毫秒）
    token_usage INTEGER DEFAULT 0,         -- Token 消耗
    started_at TIMESTAMP NOT NULL,         -- 开始时间
    completed_at TIMESTAMP,                -- 完成时间
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (task_id) REFERENCES scheduled_tasks(task_id),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_scheduled_task_logs_task 
    ON scheduled_task_logs(task_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_scheduled_task_logs_user 
    ON scheduled_task_logs(user_id, created_at DESC);
```

### 3.3 ER 关系

```
users (1) ────── (N) scheduled_tasks (1) ────── (N) scheduled_task_logs
    │
    └── (1) ── cron_{user_id} ── (1) chat_sessions    ← 每用户固定专属会话
                    │
                    └── (N) chat_messages               ← 执行结果消息
```

---

## 4. 模块设计

### 4.1 文件结构

```
src/
├── scheduler/                      # 定时任务调度模块
│   ├── __init__.py
│   ├── manager.py                  # 调度管理器（核心）
│   ├── executor.py                 # 任务执行器（含 cron session 管理）
│   ├── db.py                       # 数据访问层
│   └── models.py                   # Pydantic 模型
├── tools/
│   └── scheduler/
│       ├── __init__.py
│       └── scheduled_task_tool.py  # Agent 工具（create/manage/list）
└── api/
    └── scheduled_task.py           # REST API（CRUD + 手动触发）

frontend/
├── src/
│   ├── components/
│   │   └── ScheduledTasks.vue      # "我的定时任务"管理页面
│   └── api/
│       └── scheduledTask.ts        # 定时任务 API 封装
```

### 4.2 模块依赖

```
src/tools/scheduler/scheduled_task_tool.py
    ↓ 调用
src/scheduler/manager.py (ScheduledTaskManager)
    ↓ 使用
src/scheduler/executor.py (ScheduledTaskExecutor)
    ↓ 调用
src/core/agent.py (master_agent.process_message_sync)
    ↓ 记录日志到
src/scheduler/db.py (ScheduledTaskDB / ScheduledTaskLogDB)
```

---

## 5. 详细设计

### 5.1 Pydantic 模型 (`src/scheduler/models.py`)

```python
from datetime import datetime
from enum import Enum
from typing import Optional, List
from pydantic import BaseModel, Field

class ScheduleType(str, Enum):
    DAILY = "daily"        # 每天
    WEEKLY = "weekly"      # 每周
    MONTHLY = "monthly"    # 每月
    INTERVAL = "interval"  # 间隔
    ONCE = "once"          # 一次性

class TaskStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

class ExecutionStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"

class ScheduledTaskCreate(BaseModel):
    """创建定时任务请求（Agent 工具参数）"""
    name: str = Field(..., description="任务名称")
    description: str = Field(..., description="任务详细描述")
    user_input: str = Field(..., description="用户原始输入")
    schedule_type: ScheduleType
    cron_expression: Optional[str] = Field(None, description="cron 表达式")
    time_config: Optional[dict] = Field(None, description="时间配置")
    # time_config 示例:
    # daily: {"hour": 9, "minute": 0}
    # weekly: {"day_of_week": "mon", "hour": 9, "minute": 0}
    # monthly: {"day": 1, "hour": 9, "minute": 0}
    # interval: {"interval_hours": 2}
    # once: {"run_at": "2026-04-01 09:00:00"}

class ScheduledTask(BaseModel):
    """定时任务完整模型"""
    task_id: str
    user_id: str
    name: str
    description: str
    task_prompt: str                    # LLM 生成的独立可执行提示词
    schedule_type: ScheduleType
    cron_expression: Optional[str]
    session_id: Optional[str]
    status: TaskStatus
    max_retries: int = 3
    retry_count: int = 0
    last_run_at: Optional[datetime]
    next_run_at: Optional[datetime]
    total_runs: int = 0
    success_count: int = 0
    fail_count: int = 0
    created_at: datetime
    updated_at: datetime

class ScheduledTaskLog(BaseModel):
    """执行日志模型"""
    log_id: str
    task_id: str
    user_id: str
    session_id: Optional[str]
    status: ExecutionStatus
    trigger_type: str                   # scheduled / manual
    result_summary: Optional[str]
    result_detail: Optional[str]
    error_message: Optional[str]
    error_trace: Optional[str]
    duration_ms: int = 0
    token_usage: int = 0
    started_at: datetime
    completed_at: Optional[datetime]
```

### 5.2 数据访问层 (`src/scheduler/db.py`)

遵循项目现有的 `UserDB` / `SessionDB` 静态方法模式：

```python
class ScheduledTaskDB:
    @staticmethod
    def create(user_id, name, description, task_prompt, 
               schedule_type, cron_expression, session_id=None) -> dict
    
    @staticmethod
    def get_by_id(task_id) -> Optional[dict]
    
    @staticmethod
    def list_by_user(user_id, status=None, limit=50) -> List[dict]
    
    @staticmethod
    def list_active() -> List[dict]     # 获取所有活跃任务（启动时加载）
    
    @staticmethod
    def update_status(task_id, status) -> bool
    
    @staticmethod
    def update_after_run(task_id, success, result_summary=None) -> bool
    
    @staticmethod
    def delete(task_id) -> bool         # 软删除（status -> cancelled）

class ScheduledTaskLogDB:
    @staticmethod
    def create(task_id, user_id, session_id, status, trigger_type, ...) -> dict
    
    @staticmethod
    def list_by_task(task_id, limit=100) -> List[dict]
    
    @staticmethod
    def list_by_user(user_id, limit=50) -> List[dict]
    
    @staticmethod
    def get_stats(task_id) -> dict      # 成功率、平均耗时等统计
```

### 5.3 调度管理器 (`src/scheduler/manager.py`)

**核心职责**：管理 APScheduler 生命周期，提供任务的注册/暂停/恢复/删除接口。

```python
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

class ScheduledTaskManager:
    """定时任务调度管理器"""
    
    def __init__(self):
        self._scheduler = BackgroundScheduler(
            timezone="Asia/Shanghai",
            job_defaults={
                "coalesce": True,        # 错过的任务合并执行（不重复补）
                "max_instances": 1,       # 同一任务不并发
                "misfire_grace_time": 300  # 错过5分钟内仍执行
            }
        )
        self._jobs: Dict[str, str] = {}   # task_id -> apscheduler job_id
    
    def start(self):
        """启动调度器（应用启动时调用）"""
        # 1. 从 DB 加载所有 active 任务
        # 2. 逐个注册到 APScheduler
        # 3. 启动 scheduler
        self._scheduler.start()
    
    def shutdown(self):
        """优雅关闭（应用关闭时调用）"""
        self._scheduler.shutdown(wait=True)
    
    def register_task(self, task: ScheduledTask) -> str:
        """注册定时任务到 APScheduler"""
        # 根据 schedule_type 创建对应的 trigger
        # 添加 job，job_func = self._execute_task
        # 返回 apscheduler job_id
    
    def remove_task(self, task_id: str):
        """移除定时任务"""
    
    def pause_task(self, task_id: str):
        """暂停定时任务"""
    
    def resume_task(self, task_id: str):
        """恢复定时任务"""
    
    async def _execute_task(self, task_id: str):
        """任务执行入口（APScheduler 回调）"""
        # 由 executor 执行具体逻辑
```

### 5.4 任务执行器 (`src/scheduler/executor.py`)

**核心职责**：在后台线程中执行 Agent 任务，记录详细日志。

```python
class ScheduledTaskExecutor:
    """定时任务执行器"""
    
    def __init__(self):
        from src.core.agent import master_agent
        self.agent = master_agent
    
    def _get_cron_session_id(self, user_id: str) -> str:
        """
        获取用户专属的定时任务会话ID
        
        规则：session_id = "cron_{user_id}"（固定值，每个用户唯一）
        如果会话不存在则自动创建，确保每个用户始终有一个专属会话。
        注意：需扩展 SessionDB.create() 以支持自定义 session_id 参数。
        """
        session_id = f"cron_{user_id}"
        session = SessionDB.get_by_id(session_id)
        if not session:
            SessionDB.create(
                session_id=session_id,          # 新增：支持传入自定义 session_id
                user_id=user_id,
                title="定时任务执行记录",
                context_data={"type": "scheduled_task", "auto_created": True}
            )
        return session_id
    
    async def execute(self, task_id: str, trigger_type: str = "scheduled") -> dict:
        """
        执行定时任务
        
        流程：
        1. 从 DB 加载任务配置
        2. 构建独立执行上下文（加载 User，使用用户专属 cron session）
        3. 调用 agent.process_message_sync(task_prompt, cron_session_id, user)
        4. 将执行结果保存到用户专属 cron session 的消息中
        5. 记录执行日志到 scheduled_task_logs
        6. 更新任务统计（total_runs, success_count/fail_count, last_run_at）
        7. 失败时判断是否需要重试
        """
    
    async def dry_run(self, user_id: str, task_prompt: str, user_input: str) -> dict:
        """
        试执行（创建定时任务前的验证）
        
        流程：
        1. 使用用户专属 cron session
        2. 调用 agent.process_message_sync(task_prompt, cron_session_id, user)
        3. 试执行结果也保存到 cron session 中（用户可查看验证过程）
        4. 返回 {success, result, error}
        """
    
    def _build_user(self, user_id: str) -> User:
        """从 DB 加载用户信息构建 User 对象"""
        user_dict = UserDB.get_by_id(user_id)
        return User.from_dict(user_dict)
    
    def _save_result_to_session(self, session_id: str, user_input: str, 
                                  assistant_response: str, result_detail: dict):
        """将执行结果保存到会话消息和记录"""
        MessageDB.create(session_id, "user", user_input)
        MessageDB.create(session_id, "assistant", assistant_response, metadata=result_detail)
```

### 5.5 Agent 工具定义 (`src/tools/scheduler/scheduled_task_tool.py`)

为 Agent 新增的工具，定义在 `AGENT_TOOLS` 列表中：

#### 5.5.1 `create_scheduled_task` 工具

```python
{
    "name": "create_scheduled_task",
    "description": "为用户创建定时执行的任务。创建前会先执行一次验证，只有验证通过才会创建定时任务。支持每天、每周、每月、间隔执行等模式。",
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "任务名称，简短描述（如：每日邮件检查）"
            },
            "description": {
                "type": "string",
                "description": "任务的详细描述"
            },
            "task_prompt": {
                "type": "string",
                "description": "独立可执行的提示词，不依赖对话上下文。应包含完整的任务指令、所有必要信息。例如：'检查邮箱中未读邮件，如有未读邮件，汇总邮件列表并发送到 user@company.com'"
            },
            "schedule_type": {
                "type": "string",
                "enum": ["daily", "weekly", "monthly", "interval", "once"],
                "description": "调度类型"
            },
            "time_config": {
                "type": "object",
                "description": "时间配置。daily: {hour, minute}; weekly: {day_of_week, hour, minute}; monthly: {day, hour, minute}; interval: {interval_hours}; once: {run_at}"
            }
        },
        "required": ["name", "description", "task_prompt", "schedule_type", "time_config"]
    }
}
```

#### 5.5.2 `manage_scheduled_task` 工具

```python
{
    "name": "manage_scheduled_task",
    "description": "管理用户的定时任务：查看列表、暂停、恢复、取消。",
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "pause", "resume", "cancel", "view_logs"],
                "description": "操作类型"
            },
            "task_id": {
                "type": "string",
                "description": "任务ID（list 操作不需要）"
            }
        },
        "required": ["action"]
    }
}
```

### 5.6 Agent 中工具的处理逻辑

在 `src/core/agent.py` 的 `process_message` 循环中添加对 `create_scheduled_task` 和 `manage_scheduled_task` 的处理：

```python
# 在 tool_calls 处理循环中添加（类似现有的 create_plan、delegate_to_subagent）：

if tool_name == "create_scheduled_task":
    task_result = await self._handle_create_scheduled_task(
        args=tool_args,
        user=user,
        session_id=session_id,
        progress_callback=progress_callback,
    )
    # 返回结果，包含是否创建成功、任务ID、下次执行时间等

if tool_name == "manage_scheduled_task":
    task_result = await self._handle_manage_scheduled_task(
        args=tool_args,
        user=user,
        session_id=session_id,
    )
```

#### `_handle_create_scheduled_task` 方法核心流程

```python
async def _handle_create_scheduled_task(self, args, user, session_id, progress_callback):
    # 1. 参数解析
    name = args["name"]
    task_prompt = args["task_prompt"]
    schedule_type = args["schedule_type"]
    time_config = args.get("time_config", {})
    
    # 2. 生成 cron 表达式
    cron_expression = self._generate_cron_expression(schedule_type, time_config)
    
    # 3. 先试执行（dry run）
    await progress_callback({"type": "progress", "data": "正在验证任务是否可以执行..."})
    
    executor = ScheduledTaskExecutor()
    dry_run_result = await executor.dry_run(
        user_id=user.user_id,
        task_prompt=task_prompt,
        user_input=args.get("description", ""),
    )
    
    if not dry_run_result["success"]:
        return {
            "success": False,
            "error": "任务验证失败，无法创建定时任务",
            "debug": dry_run_result.get("error", "未知错误"),
            "message": "抱歉，我目前无法完成此类定时任务。"
        }
    
    # 4. 验证成功，创建定时任务
    await progress_callback({"type": "progress", "data": "任务验证通过，正在创建定时任务..."})
    
    task = ScheduledTaskDB.create(
        user_id=user.user_id,
        name=name,
        description=args.get("description", ""),
        task_prompt=task_prompt,
        schedule_type=schedule_type,
        cron_expression=cron_expression,
        session_id=session_id,
    )
    
    # 5. 注册到调度器
    scheduled_task_manager.register_task(ScheduledTask(**task))
    
    return {
        "success": True,
        "task_id": task["task_id"],
        "name": name,
        "schedule_description": self._format_schedule_description(schedule_type, time_config),
        "next_run_at": task.get("next_run_at"),
        "dry_run_result_preview": dry_run_result.get("result", "")[:200],
    }
```

### 5.7 系统提示词增强

在 `_build_system_prompt` 中添加定时任务相关指令：

```markdown
## 定时任务能力

你具备为用户创建定时执行任务的能力。当用户的需求包含以下特征时，应考虑创建定时任务：
- "每天/每周/每月" + 某个操作
- "定期/定时" + 某个操作  
- "每隔X小时" + 某个操作
- "在XX时间" + 某个操作

创建定时任务时，你必须：
1. 使用 `create_scheduled_task` 工具
2. 生成一个 **独立可执行的提示词（task_prompt）**，该提示词必须：
   - 不依赖当前对话上下文
   - 包含所有必要的信息（收件人、文件路径、操作步骤等）
   - 描述清晰，让 Agent 可以仅凭此提示词完成任务
   - 可以包含委派子智能体的指令（如需要领域专业能力）
3. 系统会先验证执行一次，成功后才会创建定时任务
4. **支持委派子智能体**：如果任务需要专业能力，可以在 task_prompt 中指示主 Agent 委派给对应的子智能体执行

如果用户询问已创建的定时任务，使用 `manage_scheduled_task` 工具查看。
```

---

## 6. 执行流程详解

### 6.1 创建定时任务完整流程

```
用户输入: "每天早上9点帮我检查邮箱未读邮件，汇总后发到 zhangsan@company.com"
    │
    ▼
[Agent 识别定时任务意图]
    │
    ▼
[Agent 调用 create_scheduled_task 工具]
    │  参数：
    │  - name: "每日邮件检查与汇总"
    │  - description: "每天检查邮箱未读邮件并汇总发送"
    │  - task_prompt: "请执行以下任务：1. 使用 email_read 工具读取未读邮件（folder=INBOX, unseen_only=True, limit=20）；2. 如果有未读邮件，将邮件列表汇总为文本；3. 使用 email_send 工具发送汇总到 zhangsan@company.com，主题为'每日未读邮件汇总 - {日期}'；4. 如果没有未读邮件，则发送一封简短通知'今日暂无未读邮件'。"
    │  - schedule_type: "daily"
    │  - time_config: {"hour": 9, "minute": 0}
    │
    ▼
[ScheduledTaskTool._handle_create_scheduled_task]
    │
    ├─ 生成 cron 表达式: "0 9 * * *"
    │
    ▼
[ScheduledTaskExecutor.dry_run]  ← 先试执行
    │  创建临时 session
    │  调用 agent.process_message_sync(task_prompt, session_id, user)
    │
    ├─ 成功 → 返回 {success: true, result: "..."}
    │    │
    │    ▼
    │  [ScheduledTaskDB.create]  ← 保存到数据库
    │  [ScheduledTaskManager.register_task]  ← 注册到 APScheduler
    │  [Agent 回复用户] "定时任务已创建，每天早上9点执行。首次验证执行结果：..."
    │
    └─ 失败 → 返回 {success: false, error: "..."}
         │
         ▼
       [Agent 回复用户] "抱歉，我目前无法完成此类定时任务。原因：..."
```

### 6.2 定时执行流程

```
[APScheduler 触发] (每天 09:00)
    │
    ▼
[ScheduledTaskManager._execute_task]
    │
    ▼
[ScheduledTaskExecutor.execute]
    │
    ├─ 从 DB 加载任务配置（task_prompt, user_id）
    ├─ 构建 User 对象（从 users 表加载）
    ├─ 获取用户专属 cron session（session_id = "cron_{user_id}"，不存在则自动创建）
    │
    ▼
[master_agent.process_message_sync(task_prompt, cron_session_id, user)]
    │  ← 在独立线程中执行，不影响 web 线程
    │  ← task_prompt 中可包含委派子智能体指令，Agent 会自动委派执行
    │
    ├─ 成功
    │   ├─ 保存结果到 cron session 的 chat_messages（用户可在前端查看）
    │   ├─ 记录 chat_record
    │   ├─ 记录 scheduled_task_log (status=success)
    │   └─ 更新 scheduled_task (total_runs++, success_count++)
    │
    └─ 失败
        ├─ 记录 scheduled_task_log (status=failed, error_message, error_trace)
        ├─ 更新 scheduled_task (total_runs++, fail_count++)
        └─ 如果 retry_count < max_retries → 短暂延迟后重试
```

---

## 7. REST API 设计

### 7.1 API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/scheduled-tasks` | 获取当前用户的定时任务列表 |
| GET | `/api/scheduled-tasks/{task_id}` | 获取任务详情 |
| POST | `/api/scheduled-tasks/{task_id}/pause` | 暂停任务 |
| POST | `/api/scheduled-tasks/{task_id}/resume` | 恢复任务 |
| DELETE | `/api/scheduled-tasks/{task_id}` | 取消任务 |
| POST | `/api/scheduled-tasks/{task_id}/run` | 手动触发执行一次 |
| GET | `/api/scheduled-tasks/{task_id}/logs` | 获取任务执行日志 |
| GET | `/api/scheduled-tasks/logs` | 获取当前用户的所有执行日志 |

### 7.2 响应格式

遵循项目的 `success/error/debug` 规范：

```json
{
    "success": true,
    "data": {
        "task_id": "sched_abc123",
        "name": "每日邮件检查",
        "status": "active",
        "next_run_at": "2026-04-01 09:00:00",
        "total_runs": 5,
        "success_count": 4,
        "fail_count": 1
    }
}
```

---

## 8. 前端"我的定时任务"页面

### 8.1 页面路由

在 `frontend/src/main.ts` 中新增路由：

```typescript
import ScheduledTasks from './components/ScheduledTasks.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'chat',
      component: () => import('./components/ChatContainer.vue')
    },
    {
      path: '/customer-info',
      name: 'customer-info',
      component: CustomerInfo
    },
    {
      path: '/scheduled-tasks',
      name: 'scheduled-tasks',
      component: ScheduledTasks
    }
  ]
})
```

### 8.2 页面布局

`frontend/src/components/ScheduledTasks.vue` 页面结构：

```
┌─────────────────────────────────────────────────────┐
│  我的定时任务                              [刷新]    │
├─────────────────────────────────────────────────────┤
│                                                     │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐   │
│  │ 活跃任务 │ │ 已暂停   │ │ 总执行  │ │ 成功率   │   │
│  │   5     │ │   2     │ │  128    │ │  96.1%  │   │
│  └─────────┘ └─────────┘ └─────────┘ └─────────┘   │
│                                                     │
│  ┌─────────────────────────────────────────────┐    │
│  │ 任务名称        │ 调度      │ 状态 │ 操作   │    │
│  ├─────────────────┼───────────┼──────┼────────┤    │
│  │ 每日邮件检查     │ 每天 9:00 │ 活跃 │ ⏸ 🗑 📋│    │
│  │ 周报生成        │ 每周一 18:00│ 活跃 │ ⏸ 🗑 📋│   │
│  │ 竞品监控        │ 每2小时   │ 暂停 │ ▶ 🗑 📋│    │
│  └─────────────────┴───────────┴──────┴────────┘    │
│                                                     │
│  ▼ 点击 [📋] 展开执行日志                            │
│  ┌─────────────────────────────────────────────┐    │
│  │ 执行时间          │ 状态   │ 耗时   │ 操作   │    │
│  │ 2026-03-31 09:00  │ ✅成功  │ 12.5s │ 查看结果│    │
│  │ 2026-03-30 09:00  │ ✅成功  │ 11.2s │ 查看结果│    │
│  │ 2026-03-29 09:00  │ ❌失败  │  3.1s │ 查看错误│    │
│  └─────────────────┴─────────┴───────┴────────┘    │
│                                                     │
│  ▼ 点击 [查看结果] → 跳转到用户专属 cron 会话         │
│    路由: /?session_id=cron_{user_id}                │
│    在 chat 页面中加载 cron session 的消息记录          │
└─────────────────────────────────────────────────────┘
```

### 8.3 核心功能

| 功能 | 说明 |
|------|------|
| 任务列表 | 展示用户所有定时任务（活跃/暂停/已完成/已取消） |
| 统计概览 | 活跃任务数、总执行次数、成功率 |
| 暂停/恢复 | 通过 REST API 暂停/恢复任务 |
| 取消任务 | 软删除任务（状态改为 cancelled） |
| 执行日志 | 展开查看每次执行的详细日志（时间、状态、耗时、错误） |
| 查看结果 | 跳转到用户专属 cron session，查看 Agent 的执行对话记录 |

### 8.4 查看执行结果

点击"查看结果"按钮后，跳转到 chat 页面并加载用户专属 cron session：

```typescript
// 跳转到 cron session 查看执行结果
function viewResult(userId: string) {
  router.push({
    path: '/',
    query: { session_id: `cron_${userId}` }
  })
}
```

在 `ChatContainer.vue` 中，如果 URL 包含 `session_id=cron_{user_id}` 参数，则自动加载该 session 的消息记录并标记为"定时任务执行记录"。

### 8.5 API 调用

```typescript
// frontend/src/api/scheduledTask.ts

export interface ScheduledTask {
  task_id: string
  user_id: string
  name: string
  description: string
  schedule_type: string
  cron_expression: string
  status: string
  total_runs: number
  success_count: number
  fail_count: number
  last_run_at: string
  next_run_at: string
  created_at: string
}

export interface TaskLog {
  log_id: string
  task_id: string
  status: string
  trigger_type: string
  result_summary: string
  error_message: string
  duration_ms: number
  started_at: string
  completed_at: string
}

// 获取用户的定时任务列表
export async function listScheduledTasks(userId: string) { ... }

// 暂停任务
export async function pauseTask(taskId: string) { ... }

// 恢复任务
export async function resumeTask(taskId: string) { ... }

// 取消任务
export async function cancelTask(taskId: string) { ... }

// 获取任务执行日志
export async function getTaskLogs(taskId: string) { ... }

// 手动触发执行
export async function triggerTask(taskId: string) { ... }
```

### 8.6 入口方式

用户可以通过以下方式访问"我的定时任务"页面：

1. **菜单侧边栏**：在 `MenuSidebar.vue` 中添加"我的定时任务"入口按钮
2. **定时任务创建成功后**：Agent 回复中包含跳转链接
3. **直接 URL**：`/scheduled-tasks?user_id=xxx`

在 `configs/config.yaml` 中添加：

```yaml
# 定时任务配置
scheduler:
  enabled: true                    # 是否启用定时任务功能
  timezone: "Asia/Shanghai"        # 时区
  max_tasks_per_user: 20           # 每个用户最大定时任务数
  default_max_retries: 3           # 默认最大重试次数
  execution_timeout: 300           # 单次执行超时（秒）
  coalesce_missed: true            # 错过的任务合并执行
  misfire_grace_time: 300          # 错过执行的最大宽限时间（秒）
```

---

## 9. 配置设计

在 `configs/config.yaml` 中添加：

```yaml
# 定时任务配置
scheduler:
  enabled: true                    # 是否启用定时任务功能
  timezone: "Asia/Shanghai"        # 时区
  max_tasks_per_user: 20           # 每个用户最大定时任务数
  default_max_retries: 3           # 默认最大重试次数
  execution_timeout: 300           # 单次执行超时（秒）
  coalesce_missed: true            # 错过的任务合并执行
  misfire_grace_time: 300          # 错过执行的最大宽限时间（秒）
```

---

## 10. 日志设计

### 9.1 日志格式

使用 loguru 记录结构化日志：

```python
# 任务创建
logger.info("后端日志：定时任务创建", extra={
    "task_id": "sched_abc123",
    "user_id": "user_xyz789",
    "task_name": "每日邮件检查",
    "schedule_type": "daily",
    "cron_expression": "0 9 * * *",
    "dry_run_success": True,
})

# 任务执行开始
logger.info("后端日志：定时任务开始执行", extra={
    "task_id": "sched_abc123",
    "user_id": "user_xyz789",
    "task_name": "每日邮件检查",
    "trigger_type": "scheduled",
    "session_id": "session_new123",
})

# 任务执行完成
logger.info("后端日志：定时任务执行完成", extra={
    "task_id": "sched_abc123",
    "user_id": "user_xyz789",
    "status": "success",
    "duration_ms": 12500,
    "result_preview": "检查到3封未读邮件...",
})

# 任务执行失败
logger.error("后端日志：定时任务执行失败", extra={
    "task_id": "sched_abc123",
    "user_id": "user_xyz789",
    "error": "Connection timeout",
    "retry_count": 1,
    "max_retries": 3,
}, exc_info=True)
```

### 9.2 日志文件

定时任务日志单独输出到文件（不与 web 日志混在一起）：

```python
# 在 src/config/logging.py 中添加
logger.add(
    "log/scheduler/scheduler_{time:YYYY-MM-DD}.log",
    rotation="00:00",
    retention="30 days",
    filter=lambda record: "定时任务" in record.get("extra", {}).get("task_id", ""),
)
```

---

## 11. 应用启动集成

在 `src/main.py` 的 `lifespan` 函数中集成：

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # ===== 启动 =====
    init_database()
    
    # 初始化定时任务调度器
    from src.scheduler.manager import scheduled_task_manager
    scheduled_task_manager.start()
    logger.info("定时任务调度器已启动")
    
    yield
    
    # ===== 关闭 =====
    scheduled_task_manager.shutdown()
    logger.info("定时任务调度器已关闭")
```

---

## 12. 安全与限制

| 限制项 | 值 | 说明 |
|--------|-----|------|
| 每用户最大任务数 | 5 | 防止资源滥用 |
| 单次执行超时 | 1800秒 | 防止长时间占用 |
| 同一任务最大并发 | 1 | 避免重复执行 |
| 提示词最大长度 | 2000字符 | 限制 LLM 输入 |
| 执行日志保留 | 30天 | 日志轮转 |
| 重试次数 | 3次 | 失败后不再自动重试 |
| 重试间隔 | 60秒 | 重试前的等待时间 |

---

## 13. 新增依赖

```
# requirements.txt 新增
APScheduler>=3.10.0
```

APScheduler 是纯 Python 包，无其他系统级依赖。

---

## 14. 测试计划

### 14.1 单元测试

| 测试用例 | 说明 |
|----------|------|
| `test_cron_expression_generation` | 验证 cron 表达式生成逻辑 |
| `test_dry_run_success` | 试执行成功场景 |
| `test_dry_run_failure` | 试执行失败场景 |
| `test_task_crud` | 定时任务 CRUD |
| `test_task_execution` | 任务执行记录 |
| `test_schedule_registration` | APScheduler 注册 |
| `test_max_tasks_limit` | 超过最大任务数限制 |
| `test_concurrent_prevention` | 同一任务不并发 |

### 14.2 集成测试

| 测试用例 | 说明 |
|----------|------|
| `test_create_and_execute_daily_task` | 创建每日任务并验证执行 |
| `test_pause_and_resume_task` | 暂停/恢复流程 |
| `test_retry_on_failure` | 失败重试机制 |
| `test_timeout_handling` | 超时处理 |

---

## 15. 实施计划

| 阶段 | 内容 | 预估工时 |
|------|------|----------|
| **P1** | 数据库表 + DAL + Pydantic 模型 | 2h |
| **P2** | ScheduledTaskManager + ScheduledTaskExecutor（含用户专属 cron session） | 3h |
| **P3** | Agent 工具 + process_message 集成 + 系统提示词（含子智能体委派支持） | 3h |
| **P4** | REST API | 2h |
| **P5** | 应用启动集成 + 配置 | 1h |
| **P6** | 前端"我的定时任务"页面 + 路由 + API 对接 | 3h |
| **P7** | 前端 cron session 查看集成（ChatContainer 加载 cron session） | 1h |
| **P8** | 单元测试 + 集成测试 | 3h |
| **P9** | 日志完善 + 文档更新 | 1h |
| **总计** | | **~19h** |

---

## 16. 待确认事项（已全部确认）

| # | 事项 | 决议 |
|---|------|------|
| 1 | 提示词生成方式 | **已确认**：由 Agent 直接在对话中组织 `task_prompt` |
| 2 | 执行结果存储 | **已确认**：保存到用户专属的 cron session（`cron_{user_id}`），前端提供查看入口 |
| 3 | 任务编辑 | **已确认**：V1 暂不支持编辑，需取消后重新创建。前端管理页面提供暂停/恢复/取消功能 |
| 4 | 子智能体委派 | **已确认**：不限定主 Agent 执行，支持在 `task_prompt` 中指示委派给子智能体 |
