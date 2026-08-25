"""定时任务执行器"""

import asyncio
import time
from datetime import datetime
from typing import Optional, Dict, Any
from loguru import logger

from src.scheduler.db import ScheduledTaskDB, ScheduledTaskLogDB
from src.db.models import UserDB, SessionDB, MessageDB


class ScheduledTaskExecutor:
    """定时任务执行器"""

    def __init__(self):
        # 延迟导入避免循环依赖
        self._agent = None

    @property
    def agent(self):
        if self._agent is None:
            from src.core.agent import master_agent
            self._agent = master_agent
        return self._agent

    def _get_cron_session_id(self, user_id: str) -> str:
        """获取用户专属的定时任务会话ID"""
        session_id = f"cron_{user_id}"
        session = SessionDB.get_by_id(session_id)
        if not session:
            # 需要直接插入自定义 session_id
            self._create_session_with_id(
                session_id=session_id,
                user_id=user_id,
                title="定时任务执行记录",
                context_data={"type": "scheduled_task", "auto_created": True}
            )
        return session_id

    def _create_session_with_id(self, session_id: str, user_id: str,
                                 title: str = None, context_data: dict = None) -> Optional[Dict[str, Any]]:
        """创建指定 session_id 的会话"""
        import json
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    INSERT INTO chat_sessions (session_id, user_id, title, context_data)
                    VALUES (%s, %s, %s, %s)
                """, (session_id, user_id, title or "新会话",
                      json.dumps(context_data) if context_data else None))
                conn.commit()
                logger.info(f"后端日志：定时任务专属会话创建 session_id={session_id}, user_id={user_id}")
                return SessionDB.get_by_id(session_id)
            except Exception as e:
                logger.opt(exception=True).error(f"后端日志：创建定时任务专属会话失败 {e}")
                return None

    def _build_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        """从 DB 加载用户信息"""
        return UserDB.get_by_id(user_id)

    def _save_result_to_session(self, session_id: str, user_input: str,
                                 assistant_response: str, metadata: dict = None):
        """将执行结果保存到会话消息"""
        try:
            MessageDB.create(session_id, "user", user_input)
            MessageDB.create(session_id, "assistant", assistant_response, metadata=metadata)
        except Exception as e:
            logger.opt(exception=True).error(f"后端日志：保存定时任务执行结果到会话失败 session_id={session_id}, {e}")

    async def execute(self, task_id: str, trigger_type: str = "scheduled") -> Dict[str, Any]:
        """
        执行定时任务

        Returns: {"success": bool, "result": str, "error": str, "log_id": str}
        """
        # 1. 加载任务配置
        task = ScheduledTaskDB.get_by_id(task_id)
        if not task:
            logger.error(f"后端日志：定时任务不存在 task_id={task_id}")
            return {"success": False, "error": f"任务不存在: {task_id}"}

        if task["status"] != "active":
            logger.warning(f"后端日志：定时任务未激活 task_id={task_id}, status={task['status']}")
            return {"success": False, "error": f"任务状态异常: {task['status']}"}

        user_id = task["user_id"]
        task_prompt = task["task_prompt"]
        task_name = task["name"]
        max_retries = task["max_retries"]
        retry_count = task["retry_count"]

        started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        start_time = time.time()

        # 2. 获取用户和 cron session
        user_dict = self._build_user(user_id)
        cron_session_id = self._get_cron_session_id(user_id)

        if not user_dict:
            error_msg = f"用户不存在: {user_id}"
            logger.error(f"后端日志：定时任务执行失败 task_id={task_id}, {error_msg}")
            ScheduledTaskLogDB.create(
                task_id=task_id, user_id=user_id, session_id=cron_session_id,
                status="failed", trigger_type=trigger_type,
                error_message=error_msg, started_at=started_at,
                completed_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            )
            ScheduledTaskDB.update_after_run(task_id, success=False, result_summary=error_msg)
            return {"success": False, "error": error_msg}

        logger.info(f"后端日志：定时任务开始执行 task_id={task_id}, user_id={user_id}, "
                    f"task_name={task_name}, trigger={trigger_type}")

        # 3. 执行任务
        try:
            user_obj = None
            try:
                from src.models.user import User
                user_obj = User(
                    user_id=user_dict["user_id"],
                    username=user_dict.get("username", ""),
                    phone=user_dict.get("phone", ""),
                    role=user_dict.get("role", "employee")
                )
            except Exception:
                user_obj = None

            result = await self.agent.process_message_sync(
                user_input=task_prompt,
                session_id=cron_session_id,
                user=user_obj
            )

            duration_ms = int((time.time() - start_time) * 1000)
            completed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            result_summary = result[:500] if result else "（无输出）"

            # 4. 记录成功日志
            log = ScheduledTaskLogDB.create(
                task_id=task_id, user_id=user_id, session_id=cron_session_id,
                status="success", trigger_type=trigger_type,
                result_summary=result_summary, duration_ms=duration_ms,
                started_at=started_at, completed_at=completed_at
            )
            ScheduledTaskDB.update_after_run(task_id, success=True, result_summary=result_summary)

            logger.info(f"后端日志：定时任务执行完成 task_id={task_id}, status=success, "
                        f"duration={duration_ms}ms")

            return {"success": True, "result": result, "log_id": log.get("log_id") if log else ""}

        except asyncio.CancelledError:
            logger.warning(f"后端日志：定时任务被取消 task_id={task_id}")
            return {"success": False, "error": "任务被取消"}

        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            completed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            error_msg = str(e)
            error_trace = ""

            import traceback
            error_trace = traceback.format_exc()

            logger.opt(exception=True).error(f"后端日志：定时任务执行失败 task_id={task_id}, error={error_msg}")

            # 5. 记录失败日志
            log = ScheduledTaskLogDB.create(
                task_id=task_id, user_id=user_id, session_id=cron_session_id,
                status="failed", trigger_type=trigger_type,
                error_message=error_msg, error_trace=error_trace,
                duration_ms=duration_ms, started_at=started_at, completed_at=completed_at
            )
            ScheduledTaskDB.update_after_run(task_id, success=False, result_summary=f"失败: {error_msg[:200]}")

            # 6. 重试逻辑
            if retry_count < max_retries:
                new_retry_count = retry_count + 1
                # 更新重试计数（通过原生 SQL）
                from src.db.database import get_db_connection
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        UPDATE scheduled_tasks SET retry_count = %s WHERE task_id = %s
                    """, (new_retry_count, task_id))
                    conn.commit()

                logger.info(f"后端日志：定时任务将在60秒后重试 task_id={task_id}, "
                            f"retry={new_retry_count}/{max_retries}")
                # 延迟60秒后重试
                import threading
                def _retry():
                    time.sleep(60)
                    try:
                        loop = asyncio.new_event_loop()
                        loop.run_until_complete(self.execute(task_id, trigger_type))
                    finally:
                        loop.close()
                threading.Thread(target=_retry, daemon=True).start()

            return {"success": False, "error": error_msg,
                    "log_id": log.get("log_id") if log else ""}

    async def dry_run(self, user_id: str, task_prompt: str,
                      user_input: str = "") -> Dict[str, Any]:
        """
        试执行（创建定时任务前的验证）

        Returns: {"success": bool, "result": str, "error": str}
        """
        started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        start_time = time.time()
        cron_session_id = self._get_cron_session_id(user_id)

        logger.info(f"后端日志：定时任务试执行开始 user_id={user_id}")

        try:
            user_dict = self._build_user(user_id)
            user_obj = None
            if user_dict:
                try:
                    from src.models.user import User
                    user_obj = User(
                        user_id=user_dict["user_id"],
                        username=user_dict.get("username", ""),
                        phone=user_dict.get("phone", ""),
                        role=user_dict.get("role", "employee")
                    )
                except Exception:
                    user_obj = None

            result = await self.agent.process_message_sync(
                user_input=task_prompt,
                session_id=cron_session_id,
                user=user_obj
            )

            duration_ms = int((time.time() - start_time) * 1000)
            logger.info(f"后端日志：定时任务试执行成功 user_id={user_id}, duration={duration_ms}ms")

            return {"success": True, "result": result}

        except Exception as e:
            error_msg = str(e)
            logger.opt(exception=True).error(f"后端日志：定时任务试执行失败 user_id={user_id}, error={error_msg}")
            return {"success": False, "result": "", "error": error_msg}
