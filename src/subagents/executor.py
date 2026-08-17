#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Subagent Executor - 子智能体执行管理器

负责在子线程中启动和管理子智能体的执行，处理任务状态同步。
"""

import asyncio
import os
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from loguru import logger

from src.models.subagent import SubagentConfig, DelegationResponse
from src.subagents.protocol import SubagentTaskRecord, get_task_record_key
from src.core.redis_client import redis_client

if TYPE_CHECKING:
    from src.memory.short_term import ShortTermMemory
    from src.subagents.registry import SubagentRegistry
    from src.core.agent import Agent


class SubagentExecutor:
    """
    子智能体执行管理器
    
    负责:
    1. 在子线程中创建和启动子智能体(Agent实例)
    2. 管理任务状态同步到共享memory
    3. 将子智能体的执行记录同步到主智能体的计划管理器
    4. 超时和取消管理
    
    使用示例:
        executor = SubagentExecutor(session_memory, registry, parent_plan_manager)
        
        # 委托任务
        response = await executor.delegate(
            task_id="task_001",
            subagent_name="code-reviewer",
            task_description="审查代码安全性",
            session_id="session_001"
        )
        
        # 等待结果
        record = await executor.wait_for_result(execution_id, timeout=300)
    """
    
    def __init__(
        self,
        session_memory: 'ShortTermMemory',
        registry: 'SubagentRegistry',
        tool_registry: Optional['ToolRegistry'] = None,
        skill_registry: Optional['SkillRegistry'] = None,
        parent_plan_manager: Optional['PlanManager'] = None,
    ):
        """
        初始化执行管理器

        Args:
            session_memory: 共享的session记忆实例
            registry: Subagent注册表
            tool_registry: 主智能体的工具注册表（不再使用，保留向后兼容）
            skill_registry: 主智能体的技能注册表（不再使用，保留向后兼容）
            parent_plan_manager: 主智能体的计划管理器（用于记录子智能体执行过程）
        """
        self.memory = session_memory
        self.registry = registry
        self.tool_registry = tool_registry
        self.skill_registry = skill_registry
        self.parent_plan_manager = parent_plan_manager
        self._active_executions: Dict[str, asyncio.Task] = {}
        self._subagent_instances: Dict[str, 'Agent'] = {}
        # 并发限制：防止高并发下子智能体数量无上限导致 OOM
        # 默认 max_workers = min(32, cpu_count * 2)，与 ThreadPoolExecutor 有上限的语义一致
        _max_workers = min(32, (os.cpu_count() or 1) * 2)
        self._concurrency_sem = asyncio.Semaphore(_max_workers)
        logger.info(f"[SUBAGENT] Concurrency limit set to {_max_workers}")

    # ==================== Task Record (Redis) ====================

    def _task_record_key(self, execution_id: str) -> str:
        return redis_client.make_key("task_record", execution_id)

    def _save_task_record(self, record: SubagentTaskRecord) -> None:
        """保存任务记录到 Redis Hash"""
        key = self._task_record_key(record.execution_id)
        data = record.model_dump(mode="json")
        redis_client.hset(key, "data", data)
        redis_client.expire(key, 7200)

    def _load_task_record(self, execution_id: str) -> Optional[SubagentTaskRecord]:
        """从 Redis Hash 加载任务记录"""
        key = self._task_record_key(execution_id)
        data = redis_client.hget(key, "data")
        if data:
            try:
                return SubagentTaskRecord.model_validate(data)
            except Exception as e:
                logger.warning(f"[SUBAGENT] Failed to validate task record {execution_id}: {e}")
        return None

    def _delete_task_record(self, execution_id: str) -> None:
        """从 Redis 删除任务记录"""
        key = self._task_record_key(execution_id)
        redis_client.delete(key)

    def _create_execution_id(self) -> str:
        """生成唯一的执行ID"""
        return f"exec_{uuid.uuid4().hex[:12]}"
    
    def _create_task_record(
        self,
        task_id: str,
        execution_id: str,
        subagent_name: str,
        task_description: str,
        task_parameters: Optional[Dict[str, Any]] = None,
    ) -> SubagentTaskRecord:
        """
        创建任务记录并存储到memory
        
        Args:
            task_id: 任务ID
            execution_id: 执行ID
            subagent_name: 子智能体名称
            task_description: 任务描述
            task_parameters: 任务参数
            
        Returns:
            任务记录
        """
        record = SubagentTaskRecord.create(
            task_id=task_id,
            execution_id=execution_id,
            subagent_name=subagent_name,
            task_description=task_description,
            task_parameters=task_parameters,
        )

        # 存储到 Redis
        self._save_task_record(record)

        return record
    
    def _get_task_record(self, execution_id: str) -> Optional[SubagentTaskRecord]:
        """
        从memory获取任务记录
        
        Args:
            execution_id: 执行ID
            
        Returns:
            任务记录，不存在返回None
        """
        # 从 Redis 获取记录
        return self._load_task_record(execution_id)
    
    def _update_task_record(self, record: SubagentTaskRecord) -> None:
        """
        更新memory中的任务记录
        
        Args:
            record: 任务记录
        """
        # 存储到 Redis
        self._save_task_record(record)
    
    async def delegate(
        self,
        task_id: str,
        subagent_name: str,
        task_description: str,
        session_id: str,
        task_parameters: Optional[Dict[str, Any]] = None,
        timeout: int = 7200,
        progress_callback: Optional[callable] = None,
        user_id: Optional[str] = None,
        image_paths: Optional[List[str]] = None,
    ) -> DelegationResponse:
        """
        委托任务给子智能体

        Args:
            task_id: 任务ID
            subagent_name: 子智能体名称
            task_description: 任务描述
            session_id: Session ID
            task_parameters: 任务参数
            timeout: 超时时间（秒）
            progress_callback: 进度回调函数，用于实时传递子智能体执行进度
            user_id: 用户ID（用于设置邮件等工具的用户上下文）
            image_paths: 用户上传图片的完整路径列表（可选，传给多模态子智能体如 video-agent）

        Returns:
            委托响应
        """
        logger.info(f"\n{'='*60}\n[SUBAGENT] Delegate called\n{'='*60}")
        logger.info(f"[SUBAGENT] task_id: {task_id}")
        logger.info(f"[SUBAGENT] subagent_name: {subagent_name}")
        logger.info(f"[SUBAGENT] task_description: {task_description}")
        logger.info(f"[SUBAGENT] session_id: {session_id}")
        if image_paths:
            logger.info(f"[SUBAGENT] image_paths: {image_paths}")

        # 获取配置
        config = self.registry.get(subagent_name)
        if not config:
            logger.error(f"[SUBAGENT] Subagent not found: {subagent_name}")
            return DelegationResponse(
                success=False,
                error=f"Subagent not found: {subagent_name}"
            )

        logger.info(f"[SUBAGENT] Config found: {config.name}")

        # 生成执行ID
        execution_id = self._create_execution_id()
        logger.info(f"[SUBAGENT] Generated execution_id: {execution_id}")

        # 把 image_paths 并入 task_parameters 持久化（便于追溯）
        effective_task_parameters = dict(task_parameters or {})
        if image_paths:
            effective_task_parameters["image_paths"] = image_paths

        # 创建任务记录
        record = self._create_task_record(
            task_id=task_id,
            execution_id=execution_id,
            subagent_name=subagent_name,
            task_description=task_description,
            task_parameters=effective_task_parameters,
        )
        logger.info(f"[SUBAGENT] Task record created: {record.task_id}")

        # 导入Agent类（避免循环导入）
        from src.core.agent import Agent

        # 解析 tenant_id（子智能体线程中 ContextVar 可能不可用，需在主线程提前获取）
        tenant_id = None
        try:
            from src.saas.context import get_current_tenant_id
            tenant_id = get_current_tenant_id()
        except Exception:
            pass

        # 创建子智能体实例
        logger.info(f"[SUBAGENT] Creating subagent Agent instance...")
        subagent_instance = Agent(
            is_master=False,
            subagent_config=config,
            session_id=session_id,
            execution_id=execution_id,
            parent_plan_manager=self.parent_plan_manager,
            tenant_id=tenant_id,
            user_id=user_id,
        )
        self._subagent_instances[execution_id] = subagent_instance
        logger.info(f"[SUBAGENT] Subagent Agent instance created")

        # 设置子智能体邮件工具的 user_id（使工具能从数据库读取用户邮箱配置）
        if user_id:
            for tool_name in ("email_send", "email_read", "email_list_folders", "browser_automation"):
                tool = subagent_instance.tool_registry.get_tool(tool_name)
                if tool and hasattr(tool, 'set_user_id'):
                    tool.set_user_id(user_id)

        # 在子线程启动执行
        async_task = asyncio.create_task(
            self._run_instance(
                subagent_instance, record, timeout, task_description, session_id,
                progress_callback, image_paths=image_paths,
            ),
            name=f"subagent_{subagent_name}_{execution_id}"
        )
        self._active_executions[execution_id] = async_task

        logger.info(f"[SUBAGENT] Started async task: subagent_{subagent_name}_{execution_id}")
        logger.info(f"[SUBAGENT] Active executions: {list(self._active_executions.keys())}")

        return DelegationResponse(
            success=True,
            execution_id=execution_id,
            subagent_name=subagent_name,
        )
    
    async def _run_instance(
        self,
        instance: 'Agent',
        record: SubagentTaskRecord,
        timeout: int,
        task_description: str,
        session_id: str,
        progress_callback: Optional[callable] = None,
        image_paths: Optional[List[str]] = None,
    ) -> None:
        """
        运行子智能体实例

        Args:
            instance: 子智能体实例（Agent）
            record: 任务记录
            timeout: 超时时间
            task_description: 任务描述
            session_id: session ID
            progress_callback: 进度回调函数
            image_paths: 用户上传图片路径列表（可选，传给多模态子智能体）
        """
        logger.info(f"\n{'='*60}\n[SUBAGENT] _run_instance started\n{'='*60}")
        logger.info(f"[SUBAGENT] instance.is_master: {instance.is_master}")
        logger.info(f"[SUBAGENT] instance.subagent_config.name: {instance.subagent_config.name}")
        logger.info(f"[SUBAGENT] record.execution_id: {record.execution_id}")
        logger.info(f"[SUBAGENT] record.task_description: {record.task_description}")

        async with self._concurrency_sem:
            # 并发受限：超出上限的子智能体会在此等待，避免无限制创建协程导致 OOM
            try:
                # 更新状态为运行中
                record.start()
                self._update_task_record(record)
                logger.info(f"[SUBAGENT] Record status updated to: {record.status}")

                # 执行任务（带超时）
                import time
                exec_start_time = time.time()
                logger.info(f"[SUBAGENT] Calling instance.execute_as_subagent() with timeout={timeout}s, execution_id={record.execution_id}...")

                try:
                    result = await asyncio.wait_for(
                        instance.execute_as_subagent(
                            task_description=task_description,
                            parent_session_id=session_id,
                            task_record=record,
                            progress_callback=progress_callback,
                            image_paths=image_paths,
                        ),
                        timeout=timeout
                    )

                    exec_duration = time.time() - exec_start_time
                    logger.info(f"[SUBAGENT] instance.execute_as_subagent() completed, execution_id={record.execution_id}, duration={exec_duration:.2f}s")
                    logger.info(f"[SUBAGENT] Result preview: {str(result)[:200] if result else 'None'}...")

                except asyncio.TimeoutError as e:
                    exec_duration = time.time() - exec_start_time
                    logger.error(f"[SUBAGENT] execute_as_subagent TIMEOUT, execution_id={record.execution_id}, duration={exec_duration:.2f}s, timeout={timeout}s")
                    raise
                except asyncio.CancelledError as e:
                    exec_duration = time.time() - exec_start_time
                    logger.warning(f"[SUBAGENT] execute_as_subagent CANCELLED, execution_id={record.execution_id}, duration={exec_duration:.2f}s")
                    raise
                except Exception as e:
                    exec_duration = time.time() - exec_start_time
                    logger.opt(exception=True).error(f"[SUBAGENT] execute_as_subagent FAILED, execution_id={record.execution_id}, duration={exec_duration:.2f}s, error: {e}")
                    raise

                # 更新结果
                if result:
                    # 检查是否为 clarifying 状态（子智能体需要用户补充信息）
                    if result.get("status") == "clarifying":
                        # CLARIFYING 状态由 execute_as_subagent 内部设置，
                        # 这里只需要记录日志，不需要再调用 record.request_clarification()
                        logger.info(f"[SUBAGENT] Subagent returned clarifying status for execution_id={record.execution_id}")
                    else:
                        record.complete(
                            result=result.get("result"),
                            summary=result.get("summary", "")
                        )
                        if result.get("token_usage"):
                            record.token_usage = result["token_usage"]
                        logger.info(f"[SUBAGENT] Record completed with summary: {record.summary[:200] if record.summary else 'N/A'}")
                else:
                    record.complete(result={}, summary="Task completed")
                    logger.info(f"[SUBAGENT] Record completed with empty result")

            except asyncio.TimeoutError:
                logger.error(f"[SUBAGENT] Execution timed out: {record.execution_id}")
                record.fail(f"Execution timed out after {timeout} seconds")

            except asyncio.CancelledError:
                logger.warning(f"[SUBAGENT] Execution cancelled: {record.execution_id}")
                record.cancel()

            except Exception as e:
                import traceback
                error_trace = traceback.format_exc()
                logger.error(f"[SUBAGENT] Execution failed: {record.execution_id}, error: {e}")
                logger.error(f"[SUBAGENT] Traceback:\n{error_trace}")
                record.fail(str(e))

            finally:
                self._update_task_record(record)
                logger.info(f"[SUBAGENT] Final record status: {record.status}")
                # 清理
                self._active_executions.pop(record.execution_id, None)
                self._subagent_instances.pop(record.execution_id, None)
                logger.info(f"[SUBAGENT] Execution cleanup done")
    
    async def wait_for_result(
        self,
        execution_id: str,
        timeout: float = 300,
        poll_interval: float = 0.5,
    ) -> Optional[SubagentTaskRecord]:
        """
        等待子智能体执行完成
        
        Args:
            execution_id: 执行ID
            timeout: 超时时间
            poll_interval: 轮询间隔
            
        Returns:
            任务记录，超时返回None
        """
        import time
        wait_start = time.time()
        logger.info(f"[SUBAGENT] wait_for_result started, execution_id={execution_id}, timeout={timeout}s")
        
        try:
            poll_count = 0
            while time.time() - wait_start < timeout:
                poll_count += 1
                record = self._get_task_record(execution_id)
                
                if record:
                    current_status = record.status
                    # 每10次轮询记录一次状态（约每5秒）
                    if poll_count % 10 == 0:
                        elapsed = time.time() - wait_start
                        logger.debug(f"[SUBAGENT] wait_for_result polling, execution_id={execution_id}, status={current_status}, elapsed={elapsed:.1f}s")
                    
                    if record.is_terminal():
                        wait_duration = time.time() - wait_start
                        logger.info(f"[SUBAGENT] wait_for_result completed, execution_id={execution_id}, status={current_status}, duration={wait_duration:.2f}s")
                        return record
                else:
                    # 记录未找到
                    if poll_count % 10 == 0:
                        elapsed = time.time() - wait_start
                        logger.warning(f"[SUBAGENT] wait_for_result: record not found, execution_id={execution_id}, elapsed={elapsed:.1f}s")
                
                await asyncio.sleep(poll_interval)
            
            # 超时
            wait_duration = time.time() - wait_start
            logger.error(f"[SUBAGENT] wait_for_result TIMEOUT, execution_id={execution_id}, duration={wait_duration:.2f}s, timeout={timeout}s")
            
        except Exception as e:
            wait_duration = time.time() - wait_start
            logger.opt(exception=True).error(f"[SUBAGENT] wait_for_result ERROR, execution_id={execution_id}, duration={wait_duration:.2f}s, error: {e}")
            raise
        
        logger.warning(f"[SUBAGENT] Wait for result timed out: {execution_id}")
        return None
    
    async def get_progress(self, execution_id: str) -> Optional[Dict[str, Any]]:
        """
        获取执行进度
        
        Args:
            execution_id: 执行ID
            
        Returns:
            进度信息
        """
        record = self._get_task_record(execution_id)
        if not record:
            return None
        
        return {
            "execution_id": execution_id,
            "status": record.status,
            "progress_percent": record.progress_percent,
            "current_step": record.current_step,
            "is_terminal": record.is_terminal(),
        }
    
    async def handle_clarification(
        self,
        execution_id: str,
        answer: str,
    ) -> bool:
        """
        处理澄清请求（re-delegate 模式）
        
        在 re-delegate 模式下，此方法仅更新记录状态（标记已回答）。
        实际的继续执行由主智能体发起新的 delegate 调用完成。
        
        Args:
            execution_id: 原始执行ID
            answer: 澄清答案
            
        Returns:
            是否成功
        """
        record = self._get_task_record(execution_id)
        if not record or not record.is_clarifying():
            return False
        
        record.answer_clarification(answer)
        self._update_task_record(record)
        logger.info(f"[SUBAGENT] Clarification answered for execution_id={execution_id}")
        
        return True
    
    def get_pending_clarification(self, execution_id: str) -> Optional[SubagentTaskRecord]:
        """
        获取待澄清的任务记录
        
        Args:
            execution_id: 执行ID
            
        Returns:
            任务记录，如果不在澄清状态则返回None
        """
        record = self._get_task_record(execution_id)
        if record and record.is_clarifying():
            return record
        return None
    
    async def cancel(self, execution_id: str) -> bool:
        """
        取消执行
        
        Args:
            execution_id: 执行ID
            
        Returns:
            是否成功
        """
        async_task = self._active_executions.get(execution_id)
        if async_task:
            async_task.cancel()
            logger.info(f"Cancelled subagent execution: {execution_id}")
            return True
        
        return False
    
    def get_active_executions(self) -> List[str]:
        """获取所有活跃的执行ID列表"""
        return list(self._active_executions.keys())
    
    async def cancel_all(self) -> None:
        """取消所有活跃的执行"""
        for execution_id in list(self._active_executions.keys()):
            await self.cancel(execution_id)
