#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Subagent Protocol - 任务记录与状态同步

定义子智能体任务在共享memory中的记录格式，用于主/子智能体之间的状态同步。
"""

from datetime import datetime
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field

from src.models.subagent import SubagentTaskStatus


class SubagentTaskRecord(BaseModel):
    """
    存储在共享memory中的任务记录
    
    主/子智能体通过读写此记录来同步任务状态和结果。
    """
    # 基本信息
    task_id: str = Field(..., description="任务ID")
    execution_id: str = Field(..., description="执行ID")
    subagent_name: str = Field(..., description="子智能体名称")
    status: SubagentTaskStatus = Field(
        default=SubagentTaskStatus.PENDING,
        description="任务状态"
    )
    
    # 任务描述
    task_description: str = Field(default="", description="任务描述")
    task_parameters: Dict[str, Any] = Field(default_factory=dict, description="任务参数")
    
    # 进度
    progress_percent: float = Field(default=0.0, description="进度百分比")
    current_step: str = Field(default="", description="当前步骤")
    
    # 澄清请求
    clarification_request: Optional[str] = Field(default=None, description="澄清问题")
    clarification_answer: Optional[str] = Field(default=None, description="澄清答案")
    
    # 结果
    result: Optional[Dict[str, Any]] = Field(default=None, description="执行结果")
    error: Optional[str] = Field(default=None, description="错误信息")
    summary: str = Field(default="", description="执行摘要")
    
    # 时间戳
    started_at: Optional[datetime] = Field(default=None, description="开始时间")
    updated_at: datetime = Field(default_factory=datetime.now, description="更新时间")
    completed_at: Optional[datetime] = Field(default=None, description="完成时间")
    
    # Token统计
    token_usage: Dict[str, int] = Field(default_factory=dict, description="Token使用统计")
    
    class Config:
        use_enum_values = True
    
    @classmethod
    def create(
        cls,
        task_id: str,
        execution_id: str,
        subagent_name: str,
        task_description: str = "",
        task_parameters: Optional[Dict[str, Any]] = None,
    ) -> "SubagentTaskRecord":
        """
        创建新的任务记录
        
        Args:
            task_id: 任务ID
            execution_id: 执行ID
            subagent_name: 子智能体名称
            task_description: 任务描述
            task_parameters: 任务参数
            
        Returns:
            任务记录实例
        """
        return cls(
            task_id=task_id,
            execution_id=execution_id,
            subagent_name=subagent_name,
            task_description=task_description,
            task_parameters=task_parameters or {},
            started_at=datetime.now(),
        )
    
    def start(self) -> None:
        """标记任务开始"""
        self.status = SubagentTaskStatus.RUNNING
        self.started_at = datetime.now()
        self.updated_at = datetime.now()
    
    def update_progress(self, percent: float, step: str = "") -> None:
        """
        更新进度
        
        Args:
            percent: 进度百分比
            step: 当前步骤描述
        """
        self.progress_percent = min(100.0, max(0.0, percent))
        self.current_step = step
        self.updated_at = datetime.now()
    
    def request_clarification(self, question: str) -> None:
        """
        请求澄清
        
        Args:
            question: 澄清问题
        """
        self.status = SubagentTaskStatus.CLARIFYING
        self.clarification_request = question
        self.updated_at = datetime.now()
    
    def answer_clarification(self, answer: str) -> None:
        """
        回答澄清
        
        Args:
            answer: 澄清答案
        """
        self.clarification_answer = answer
        self.status = SubagentTaskStatus.RUNNING
        self.updated_at = datetime.now()
    
    def complete(self, result: Dict[str, Any], summary: str = "") -> None:
        """
        标记任务完成
        
        Args:
            result: 执行结果
            summary: 执行摘要
        """
        self.status = SubagentTaskStatus.COMPLETED
        self.result = result
        self.summary = summary
        self.progress_percent = 100.0
        self.completed_at = datetime.now()
        self.updated_at = datetime.now()
    
    def fail(self, error: str) -> None:
        """
        标记任务失败
        
        Args:
            error: 错误信息
        """
        self.status = SubagentTaskStatus.FAILED
        self.error = error
        self.completed_at = datetime.now()
        self.updated_at = datetime.now()
    
    def cancel(self) -> None:
        """取消任务"""
        self.status = SubagentTaskStatus.CANCELLED
        self.completed_at = datetime.now()
        self.updated_at = datetime.now()
    
    def is_terminal(self) -> bool:
        """检查是否为终态"""
        return self.status in [
            SubagentTaskStatus.COMPLETED,
            SubagentTaskStatus.FAILED,
            SubagentTaskStatus.CANCELLED
        ]
    
    def is_clarifying(self) -> bool:
        """检查是否在等待澄清"""
        return self.status == SubagentTaskStatus.CLARIFYING
    
    def get_memory_key(self) -> str:
        """获取在memory中的存储key"""
        return f"subagent_task_{self.execution_id}"
    
    def to_summary(self) -> str:
        """生成执行摘要文本"""
        lines = [
            f"任务: {self.task_description}",
            f"状态: {self.status}",
        ]
        
        if self.progress_percent > 0:
            lines.append(f"进度: {self.progress_percent:.0f}%")
        
        if self.current_step:
            lines.append(f"当前步骤: {self.current_step}")
        
        if self.summary:
            lines.append(f"摘要: {self.summary}")
        
        if self.error:
            lines.append(f"错误: {self.error}")
        
        return "\n".join(lines)


def get_task_record_key(execution_id: str) -> str:
    """
    获取任务记录在memory中的key
    
    Args:
        execution_id: 执行ID
        
    Returns:
        memory key
    """
    return f"subagent_task_{execution_id}"
