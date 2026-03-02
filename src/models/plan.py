"""
执行计划模型

管理任务分解和执行计划
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    """任务状态"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class Task(BaseModel):
    """
    任务模型
    
    表示执行计划中的单个任务
    """
    task_id: str = Field(..., description="任务唯一ID")
    tool_name: str = Field(..., description="工具名称")
    description: str = Field(default="", description="任务描述")
    parameters: Dict[str, Any] = Field(default_factory=dict, description="任务参数")
    dependencies: List[str] = Field(default_factory=list, description="依赖任务ID列表")
    expected_output: str = Field(default="", description="预期输出")
    status: TaskStatus = Field(default=TaskStatus.PENDING, description="任务状态")
    result: Optional[Dict[str, Any]] = Field(None, description="执行结果")
    error: Optional[str] = Field(None, description="错误信息")
    started_at: Optional[datetime] = Field(None, description="开始时间")
    completed_at: Optional[datetime] = Field(None, description="完成时间")
    
    # 子智能体委派支持
    assigned_agent: Optional[str] = Field(None, description="指定执行的子智能体名称")
    execution_id: Optional[str] = Field(None, description="子智能体执行ID")
    
    class Config:
        use_enum_values = True
    
    def is_ready(self, completed_tasks: List[str]) -> bool:
        """
        检查任务是否可以执行（依赖已满足）
        
        Args:
            completed_tasks: 已完成的任务ID列表
        
        Returns:
            是否可以执行
        """
        if self.status != TaskStatus.PENDING:
            return False
        
        for dep in self.dependencies:
            if dep not in completed_tasks:
                return False
        
        return True
    
    def start(self) -> None:
        """标记任务开始"""
        self.status = TaskStatus.RUNNING
        self.started_at = datetime.now()
    
    def complete(self, result: Dict[str, Any]) -> None:
        """
        标记任务完成
        
        Args:
            result: 执行结果
        """
        self.status = TaskStatus.COMPLETED
        self.result = result
        self.completed_at = datetime.now()
    
    def fail(self, error: str) -> None:
        """
        标记任务失败
        
        Args:
            error: 错误信息
        """
        self.status = TaskStatus.FAILED
        self.error = error
        self.completed_at = datetime.now()


class ExecutionPlan(BaseModel):
    """
    执行计划模型
    
    管理任务的执行顺序和状态
    """
    plan_id: str = Field(..., description="计划唯一ID")
    intent: str = Field(..., description="用户意图")
    tasks: List[Task] = Field(default_factory=list, description="任务列表")
    execution_mode: str = Field(default="sequential", description="执行模式: sequential/parallel")
    created_at: datetime = Field(default_factory=datetime.now, description="创建时间")
    updated_at: datetime = Field(default_factory=datetime.now, description="更新时间")
    
    def add_task(self, task: Task) -> None:
        """
        添加任务
        
        Args:
            task: 任务实例
        """
        self.tasks.append(task)
        self.updated_at = datetime.now()
    
    def get_task(self, task_id: str) -> Optional[Task]:
        """
        获取任务
        
        Args:
            task_id: 任务ID
        
        Returns:
            任务实例或None
        """
        for task in self.tasks:
            if task.task_id == task_id:
                return task
        return None
    
    def get_next_task(self) -> Optional[Task]:
        """
        获取下一个可执行的任务
        
        Returns:
            任务实例或None
        """
        completed_tasks = [
            t.task_id for t in self.tasks
            if t.status == TaskStatus.COMPLETED
        ]
        
        for task in self.tasks:
            if task.is_ready(completed_tasks):
                return task
        
        return None
    
    def get_ready_tasks(self) -> List[Task]:
        """
        获取所有可执行的任务（用于并行执行）
        
        Returns:
            任务列表
        """
        completed_tasks = [
            t.task_id for t in self.tasks
            if t.status == TaskStatus.COMPLETED
        ]
        
        return [
            task for task in self.tasks
            if task.is_ready(completed_tasks)
        ]
    
    def is_completed(self) -> bool:
        """
        检查计划是否完成
        
        Returns:
            是否所有任务都已完成
        """
        return all(
            t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED)
            for t in self.tasks
        )
    
    def is_failed(self) -> bool:
        """
        检查计划是否失败
        
        Returns:
            是否有任务失败
        """
        return any(t.status == TaskStatus.FAILED for t in self.tasks)
    
    def get_progress(self) -> Dict[str, int]:
        """
        获取执行进度
        
        Returns:
            进度统计
        """
        status_counts = {
            "total": len(self.tasks),
            "pending": 0,
            "running": 0,
            "completed": 0,
            "failed": 0,
        }
        
        for task in self.tasks:
            status_counts[task.status] = status_counts.get(task.status, 0) + 1
        
        return status_counts
    
    def to_dict(self) -> Dict[str, Any]:
        """
        转换为字典
        
        Returns:
            字典表示
        """
        return {
            "plan_id": self.plan_id,
            "intent": self.intent,
            "tasks": [t.model_dump() for t in self.tasks],
            "execution_mode": self.execution_mode,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }
