"""定时任务 Pydantic 模型"""

from enum import Enum
from pydantic import BaseModel, Field


class ScheduleType(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    INTERVAL = "interval"
    ONCE = "once"


class TaskStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ExecutionStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"


class TriggerType(str, Enum):
    SCHEDULED = "scheduled"
    MANUAL = "manual"


class ScheduledTaskCreate(BaseModel):
    """Agent 工具创建定时任务的参数"""
    name: str = Field(..., description="任务名称")
    description: str = Field(..., description="任务详细描述")
    task_prompt: str = Field(..., description="独立可执行的提示词")
    schedule_type: ScheduleType
    time_config: dict[str, any] | None = Field(None, description="时间配置")


class ScheduledTask(BaseModel):
    """定时任务完整模型"""
    task_id: str
    user_id: str
    name: str
    description: str
    task_prompt: str
    schedule_type: str
    cron_expression: str | None = None
    interval_seconds: int | None = None
    session_id: str | None = None
    status: str = "active"
    max_retries: int = 3
    retry_count: int = 0
    last_run_at: str | None = None
    next_run_at: str | None = None
    total_runs: int = 0
    success_count: int = 0
    fail_count: int = 0
    created_at: str | None = None
    updated_at: str | None = None


class ScheduledTaskLog(BaseModel):
    """执行日志模型"""
    log_id: str
    task_id: str
    user_id: str
    session_id: str | None = None
    status: str
    trigger_type: str
    result_summary: str | None = None
    result_detail: str | None = None
    error_message: str | None = None
    error_trace: str | None = None
    duration_ms: int = 0
    token_usage: int = 0
    started_at: str | None = None
    completed_at: str | None = None
    created_at: str | None = None
