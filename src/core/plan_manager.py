#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
计划管理器

负责：
1. 创建和管理执行计划
2. 将计划持久化到MD文件
3. 跟踪任务完成状态
4. 提供进度报告
"""

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from src.models.plan import ExecutionPlan, Task, TaskStatus
from src.core.redis_client import redis_client


class PlanManager:
    """
    计划管理器
    
    管理用户意图的执行计划，包括：
    - 创建计划
    - 持久化到MD文件
    - 跟踪任务状态
    - 生成进度报告
    """
    
    def __init__(self, plans_dir: Optional[Path] = None):
        """
        初始化计划管理器
        
        Args:
            plans_dir: 计划文件存储目录，默认为项目根目录下的 plans 文件夹
        """
        if plans_dir is None:
            plans_dir = Path(__file__).parent.parent.parent / "plans"
        
        self.plans_dir = plans_dir
        try:
            self.plans_dir.mkdir(parents=True, exist_ok=True)
        except (PermissionError, OSError) as e:
            # 目录不可写时降级：计划主存储在 Redis，MD 文件持久化不可用，但不能炸掉启动链路
            logger.error(f"PlanManager 计划目录不可写: {self.plans_dir} ({e})，MD 持久化将不可用")

        logger.info(f"PlanManager initialized with plans_dir: {self.plans_dir}")

    # ==================== Redis Plan Storage ====================

    def _plan_key(self, session_id: str) -> str:
        return redis_client.make_key("execution_plan", session_id)

    def _save_plan(self, session_id: str, plan: ExecutionPlan) -> None:
        """将计划序列化并保存到 Redis"""
        key = self._plan_key(session_id)
        data = plan.model_dump(mode="json")
        redis_client.set(key, data, ex=3600)

    def _load_plan(self, session_id: str) -> Optional[ExecutionPlan]:
        """从 Redis 加载并反序列化计划"""
        key = self._plan_key(session_id)
        data = redis_client.get(key)
        if data:
            try:
                return ExecutionPlan.model_validate(data)
            except Exception as e:
                logger.warning(f"[PlanManager] Failed to validate plan for {session_id}: {e}")
        return None

    def _delete_plan(self, session_id: str) -> None:
        """从 Redis 删除计划"""
        key = self._plan_key(session_id)
        redis_client.delete(key)
    
    def create_plan(
        self,
        session_id: str,
        user_query: str,
        steps: List[Dict[str, Any]],
        execution_mode: str = "sequential",
        available_tools: Optional[List[str]] = None,
        available_skills: Optional[List[str]] = None,
    ) -> ExecutionPlan:
        """
        创建执行计划
        
        Args:
            session_id: 会话ID
            user_query: 用户原始问题
            steps: 步骤列表，每个步骤包含：
                - step_number: 步骤编号
                - description: 步骤描述
                - tool: 使用的工具
                - parameters: 工具参数
                - expected_output: 预期输出
                - dependencies: 依赖的步骤编号列表
            execution_mode: 执行模式 (sequential/parallel)
            available_tools: 可用工具列表
            available_skills: 可用技能列表
        
        Returns:
            创建的执行计划
        """
        plan_id = f"plan_{uuid.uuid4().hex[:8]}"
        
        # 创建任务列表
        tasks = []
        for step in steps:
            task_id = f"task_{step.get('step_number', len(tasks) + 1)}"
            
            # 获取依赖
            dependencies = []
            for dep_num in step.get("dependencies", []):
                dependencies.append(f"task_{dep_num}")
            
            # 如果是顺序执行且不是第一步，自动添加前一步依赖
            if execution_mode == "sequential" and len(tasks) > 0 and not dependencies:
                dependencies = [tasks[-1].task_id]
            
            task = Task(
                task_id=task_id,
                tool_name=step.get("tool", "unknown"),
                description=step.get("description", ""),
                parameters=step.get("parameters", {}),
                dependencies=dependencies,
                expected_output=step.get("expected_output", ""),
                status=TaskStatus.PENDING,
            )
            tasks.append(task)
        
        # 创建执行计划
        plan = ExecutionPlan(
            plan_id=plan_id,
            intent=user_query,
            tasks=tasks,
            execution_mode=execution_mode,
        )
        
        # 存储到 Redis
        self._save_plan(session_id, plan)
        
        # 持久化到MD文件
        self._save_plan_to_markdown(
            session_id=session_id,
            plan=plan,
            user_query=user_query,
            available_tools=available_tools,
            available_skills=available_skills,
        )
        
        logger.info(f"Created plan {plan_id} for session {session_id} with {len(tasks)} tasks")
        
        return plan
    
    def get_plan(self, session_id: str) -> Optional[ExecutionPlan]:
        """
        获取会话的执行计划
        
        Args:
            session_id: 会话ID
        
        Returns:
            执行计划或None
        """
        return self._load_plan(session_id)
    
    def update_task_status(
        self,
        session_id: str,
        task_id: str,
        status: TaskStatus,
        result: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> bool:
        """
        更新任务状态
        
        Args:
            session_id: 会话ID
            task_id: 任务ID
            status: 新状态
            result: 执行结果
            error: 错误信息
        
        Returns:
            是否更新成功
        """
        plan = self._load_plan(session_id)
        if not plan:
            logger.warning(f"No plan found for session {session_id}")
            return False

        task = plan.get_task(task_id)
        if not task:
            logger.warning(f"No task found with id {task_id}")
            return False

        # 更新状态
        if status == TaskStatus.RUNNING:
            task.start()
        elif status == TaskStatus.COMPLETED:
            task.complete(result or {})
        elif status == TaskStatus.FAILED:
            task.fail(error or "Unknown error")
        else:
            task.status = status

        plan.updated_at = datetime.now()

        # 写回 Redis
        self._save_plan(session_id, plan)

        # 更新MD文件
        self._update_plan_markdown(session_id, plan)

        logger.info(f"Updated task {task_id} status to {status}")

        return True
    
    def mark_task_running(self, session_id: str, task_id: str) -> bool:
        """标记任务开始执行"""
        return self.update_task_status(session_id, task_id, TaskStatus.RUNNING)
    
    def mark_task_completed(
        self,
        session_id: str,
        task_id: str,
        result: Dict[str, Any]
    ) -> bool:
        """标记任务完成"""
        return self.update_task_status(
            session_id, task_id, TaskStatus.COMPLETED, result=result
        )
    
    def mark_task_failed(
        self,
        session_id: str,
        task_id: str,
        error: str
    ) -> bool:
        """标记任务失败"""
        return self.update_task_status(
            session_id, task_id, TaskStatus.FAILED, error=error
        )
    
    def get_next_pending_task(self, session_id: str) -> Optional[Task]:
        """
        获取下一个待执行的任务
        
        Args:
            session_id: 会话ID
        
        Returns:
            下一个可执行的任务或None
        """
        plan = self._load_plan(session_id)
        if not plan:
            return None
        
        return plan.get_next_task()
    
    def get_progress_report(self, session_id: str) -> Dict[str, Any]:
        """
        获取进度报告
        
        Args:
            session_id: 会话ID
        
        Returns:
            进度报告
        """
        plan = self._load_plan(session_id)
        if not plan:
            return {"error": "No plan found"}
        
        progress = plan.get_progress()
        
        return {
            "plan_id": plan.plan_id,
            "intent": plan.intent,
            "total_tasks": progress["total"],
            "completed": progress["completed"],
            "pending": progress["pending"],
            "running": progress["running"],
            "failed": progress["failed"],
            "is_completed": plan.is_completed(),
            "progress_percentage": round(
                progress["completed"] / progress["total"] * 100 
                if progress["total"] > 0 else 0, 
                1
            ),
        }
    
    def _save_plan_to_markdown(
        self,
        session_id: str,
        plan: ExecutionPlan,
        user_query: str,
        available_tools: Optional[List[str]] = None,
        available_skills: Optional[List[str]] = None,
    ) -> None:
        """
        将计划保存为Markdown文件
        
        Args:
            session_id: 会话ID
            plan: 执行计划
            user_query: 用户问题
            available_tools: 可用工具列表
            available_skills: 可用技能列表
        """
        file_path = self.plans_dir / f"{session_id}.md"
        
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        md_content = f"""# 执行计划

> **会话ID**: `{session_id}`  
> **计划ID**: `{plan.plan_id}`  
> **创建时间**: {now}  
> **执行模式**: {plan.execution_mode}

---

## 用户需求

{user_query}

---

## 可用资源

### 可用工具
{self._format_list(available_tools or [])}

### 可用技能
{self._format_list(available_skills or [])}

---

## 执行步骤

| 序号 | 步骤描述 | 工具 | 状态 | 结果 |
|------|----------|------|------|------|
{self._format_tasks_table(plan.tasks)}

---

## 详细任务信息

{self._format_tasks_detail(plan.tasks)}

---

## 执行进度

- **总任务数**: {len(plan.tasks)}
- **已完成**: {sum(1 for t in plan.tasks if t.status == TaskStatus.COMPLETED)}
- **进行中**: {sum(1 for t in plan.tasks if t.status == TaskStatus.RUNNING)}
- **待执行**: {sum(1 for t in plan.tasks if t.status == TaskStatus.PENDING)}
- **失败**: {sum(1 for t in plan.tasks if t.status == TaskStatus.FAILED)}

---

## 更新日志

| 时间 | 事件 |
|------|------|
| {now} | 计划创建 |
"""
        try:
            file_path.write_text(md_content, encoding="utf-8")
            logger.info(f"Saved plan to {file_path}")
        except (PermissionError, OSError) as e:
            logger.error(f"计划 MD 文件写入失败: {file_path} ({e})")
    
    def _update_plan_markdown(
        self,
        session_id: str,
        plan: ExecutionPlan,
    ) -> None:
        """
        更新计划Markdown文件
        
        Args:
            session_id: 会话ID
            plan: 执行计划
        """
        file_path = self.plans_dir / f"{session_id}.md"
        
        if not file_path.exists():
            logger.warning(f"Plan file not found: {file_path}")
            return
        
        # 读取现有内容
        content = file_path.read_text(encoding="utf-8")
        
        # 更新任务表格
        tasks_table = self._format_tasks_table(plan.tasks)
        content = self._replace_section(
            content, 
            "| 序号 |", 
            tasks_table,
            start_marker="| 序号 |",
            end_marker="---\n\n## 详细任务信息"
        )
        
        # 更新详细任务信息
        tasks_detail = self._format_tasks_detail(plan.tasks)
        content = self._replace_section(
            content,
            "## 详细任务信息",
            tasks_detail,
            start_marker="## 详细任务信息\n\n",
            end_marker="\n---\n\n## 执行进度"
        )
        
        # 更新执行进度
        progress_section = f"""- **总任务数**: {len(plan.tasks)}
- **已完成**: {sum(1 for t in plan.tasks if t.status == TaskStatus.COMPLETED)}
- **进行中**: {sum(1 for t in plan.tasks if t.status == TaskStatus.RUNNING)}
- **待执行**: {sum(1 for t in plan.tasks if t.status == TaskStatus.PENDING)}
- **失败**: {sum(1 for t in plan.tasks if t.status == TaskStatus.FAILED)}"""
        content = self._replace_section(
            content,
            "## 执行进度\n\n",
            progress_section,
            start_marker="## 执行进度\n\n",
            end_marker="\n---\n\n## 更新日志"
        )
        
        # 添加更新日志条目
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        running_task = next(
            (t for t in plan.tasks if t.status == TaskStatus.RUNNING), None
        )
        completed_task = next(
            (t for t in plan.tasks if t.status == TaskStatus.COMPLETED), None
        )
        failed_task = next(
            (t for t in plan.tasks if t.status == TaskStatus.FAILED), None
        )
        
        if completed_task:
            event = f"任务 {completed_task.task_id} 完成"
        elif running_task:
            event = f"任务 {running_task.task_id} 开始执行"
        elif failed_task:
            event = f"任务 {failed_task.task_id} 失败: {failed_task.error}"
        else:
            event = "状态更新"
        
        # 在更新日志表格末尾添加新行
        new_log_entry = f"| {now} | {event} |\n"
        content = content.replace(
            "| {now} | 计划创建 |",
            f"| {now} | 计划创建 |\n{new_log_entry}"
        )
        
        try:
            file_path.write_text(content, encoding="utf-8")
            logger.info(f"Updated plan file: {file_path}")
        except (PermissionError, OSError) as e:
            logger.error(f"计划 MD 文件写入失败: {file_path} ({e})")
    
    def _format_list(self, items: List[str]) -> str:
        """格式化列表"""
        if not items:
            return "_无_"
        return "\n".join(f"- `{item}`" for item in items)
    
    def _format_tasks_table(self, tasks: List[Task]) -> str:
        """格式化任务表格"""
        rows = []
        for i, task in enumerate(tasks, 1):
            status_emoji = {
                TaskStatus.PENDING: "⏳",
                TaskStatus.RUNNING: "🔄",
                TaskStatus.COMPLETED: "✅",
                TaskStatus.FAILED: "❌",
            }.get(task.status, "❓")
            
            description = task.description or task.tool_name
            result_preview = ""
            if task.result:
                result_preview = str(task.result)[:50] + "..."
            elif task.error:
                result_preview = f"错误: {task.error[:30]}..."
            
            rows.append(
                f"| {i} | {description} | `{task.tool_name}` | {status_emoji} {task.status} | {result_preview} |"
            )
        
        return "\n".join(rows)
    
    def _format_tasks_detail(self, tasks: List[Task]) -> str:
        """格式化详细任务信息"""
        sections = []
        
        for i, task in enumerate(tasks, 1):
            description = task.description or task.tool_name
            expected = task.expected_output or "无"
            
            status_emoji = {
                TaskStatus.PENDING: "⏳",
                TaskStatus.RUNNING: "🔄",
                TaskStatus.COMPLETED: "✅",
                TaskStatus.FAILED: "❌",
            }.get(task.status, "❓")
            
            section = f"""### 任务 {i}: {description}

- **任务ID**: `{task.task_id}`
- **工具**: `{task.tool_name}`
- **状态**: {status_emoji} {task.status}
- **参数**:
```json
{json.dumps(task.parameters, ensure_ascii=False, indent=2)}
```
- **预期输出**: {expected}
- **依赖**: {', '.join(task.dependencies) if task.dependencies else '无'}
"""
            
            if task.result:
                section += f"""- **执行结果**:
```json
{json.dumps(task.result, ensure_ascii=False, indent=2)}
```
"""
            
            if task.error:
                section += f"- **错误信息**: {task.error}\n"
            
            if task.started_at:
                section += f"- **开始时间**: {task.started_at.strftime('%H:%M:%S')}\n"
            
            if task.completed_at:
                section += f"- **完成时间**: {task.completed_at.strftime('%H:%M:%S')}\n"
            
            sections.append(section)
        
        return "\n".join(sections)
    
    def _replace_section(
        self,
        content: str,
        section_name: str,
        new_content: str,
        start_marker: str,
        end_marker: str,
    ) -> str:
        """替换内容中的某个部分"""
        start_idx = content.find(start_marker)
        end_idx = content.find(end_marker)
        
        if start_idx != -1 and end_idx != -1:
            content = content[:start_idx] + new_content + "\n" + content[end_idx:]
        
        return content
    
    def is_simple_task(self, session_id: str) -> bool:
        """
        判断是否为简单任务（只需调用一次工具）
        
        Args:
            session_id: 会话ID
        
        Returns:
            是否为简单任务
        """
        plan = self._load_plan(session_id)
        if not plan:
            return True
        
        return len(plan.tasks) == 1
    
    def get_plan_summary(self, session_id: str) -> str:
        """
        获取计划摘要（用于展示给用户）
        
        Args:
            session_id: 会话ID
        
        Returns:
            计划摘要文本
        """
        plan = self._load_plan(session_id)
        if not plan:
            return "暂无执行计划"
        
        progress = plan.get_progress()
        
        lines = [
            f"📋 **执行计划** (`{plan.plan_id}`)",
            f"",
            f"**目标**: {plan.intent}",
            f"",
            f"**步骤**:",
        ]
        
        for i, task in enumerate(plan.tasks, 1):
            status_emoji = {
                TaskStatus.PENDING: "⏳",
                TaskStatus.RUNNING: "🔄",
                TaskStatus.COMPLETED: "✅",
                TaskStatus.FAILED: "❌",
            }.get(task.status, "❓")
            
            description = task.description or task.tool_name
            lines.append(f"  {i}. {status_emoji} {description}")
        
        lines.extend([
            f"",
            f"**进度**: {progress['completed']}/{progress['total']} 完成 "
            f"({round(progress['completed']/progress['total']*100 if progress['total'] > 0 else 0)}%)",
        ])
        
        return "\n".join(lines)
