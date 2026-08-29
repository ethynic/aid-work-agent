"""定时任务执行器"""

import asyncio
import time
from datetime import datetime
from typing import Optional, Dict, Any
from loguru import logger

from src.scheduler.db import ScheduledTaskDB, ScheduledTaskLogDB
from src.db.models import UserDB, SessionDB, MessageDB
from src.saas.context import (
    set_tenant_context,
    get_current_tenant_id,
    get_current_user_id,
)


class TenantContextConflictError(RuntimeError):
    """可信租户来源互相冲突；固定文案避免在错误链路泄漏租户标识。"""


def _resolve_exec_tenant_id(
    user_dict: Optional[Dict[str, Any]], fallback_tenant_id: Optional[str]
) -> Optional[str]:
    """解析定时任务执行租户（execute 与 dry_run 共用同一优先级规则）。

    - 用户行租户（users.tenant_id）优先；None/缺字段时回落调用链提供的
      可信兜底（任务行租户 / 请求上下文租户）。
    - 空串是明确的公共租户身份，不能被 fallback 覆盖。
    - 用户租户与 fallback 都非空且不等时抛 TenantContextConflictError，
      由调用方 fail-closed（不调 Agent、不建成功记录、不重试）。
    - fallback 为空串视同未指定（任务行 "''=公共用户或遗留未回填" 存在
      遗留数据），不参与冲突校验，用户行非空租户直接生效。
    """
    if user_dict is not None and "tenant_id" in user_dict:
        user_tenant_id = user_dict.get("tenant_id")
        if user_tenant_id is not None:
            if (
                fallback_tenant_id
                and user_tenant_id != fallback_tenant_id
            ):
                raise TenantContextConflictError("定时任务租户上下文冲突")
            return user_tenant_id
    return fallback_tenant_id


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
        # 任务行租户：回填与创建时落库；''=公共用户或遗留未回填
        task_tenant_fallback = task.get("tenant_id")
        task_tenant_id = task_tenant_fallback or ""

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
                completed_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                tenant_id=task_tenant_id
            )
            ScheduledTaskDB.update_after_run(task_id, success=False, result_summary=error_msg)
            return {"success": False, "error": error_msg}

        logger.info(f"后端日志：定时任务开始执行 task_id={task_id}, user_id={user_id}, "
                    f"task_name={task_name}, trigger={trigger_type}")

        # 3. 执行任务
        try:
            user_obj = None
            # 用户行租户优先、任务行租户兜底；冲突时抛错走 fail-closed 分支。
            exec_tenant_id = _resolve_exec_tenant_id(user_dict, task_tenant_fallback)
            try:
                from src.models.user import User
                # 执行身份携带解析后的租户（None=未知，''=公共用户）。
                user_obj = User(
                    user_id=user_dict["user_id"],
                    name=user_dict.get("username") or user_dict.get("phone") or user_id,
                    tenant_id=exec_tenant_id,
                    phone=user_dict.get("phone", ""),
                    role=user_dict.get("role", "employee")
                )
            except Exception:
                user_obj = None

            # 执行租户设入请求级 ContextVar，供 agent 内部（skills/订阅/计费/
            # trace 等）按租户解析。finally 恢复进入前上下文而非无条件清空：
            # 本方法可能在请求协程内被嵌套 await（如手动触发），clear 会把整个
            # 请求的租户上下文一并清掉；后台线程进入前为 (None, None)，
            # 恢复等价于 clear，行为不变。
            _prev_tenant = get_current_tenant_id()
            _prev_user = get_current_user_id()
            set_tenant_context(exec_tenant_id, user_id)
            try:
                result = await self.agent.process_message_sync(
                    user_input=task_prompt,
                    session_id=cron_session_id,
                    user=user_obj
                )
            finally:
                set_tenant_context(_prev_tenant, _prev_user)

            duration_ms = int((time.time() - start_time) * 1000)
            completed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            result_summary = result[:500] if result else "（无输出）"

            # 4. 记录成功日志（租户随任务属主落库，保证日志可按租户过滤）
            log = ScheduledTaskLogDB.create(
                task_id=task_id, user_id=user_id, session_id=cron_session_id,
                status="success", trigger_type=trigger_type,
                result_summary=result_summary, duration_ms=duration_ms,
                started_at=started_at, completed_at=completed_at,
                tenant_id=task_tenant_id
            )
            ScheduledTaskDB.update_after_run(task_id, success=True, result_summary=result_summary)

            logger.info(f"后端日志：定时任务执行完成 task_id={task_id}, status=success, "
                        f"duration={duration_ms}ms")

            return {"success": True, "result": result, "log_id": log.get("log_id") if log else ""}

        except asyncio.CancelledError:
            logger.warning(f"后端日志：定时任务被取消 task_id={task_id}")
            return {"success": False, "error": "任务被取消"}

        except TenantContextConflictError:
            # 租户冲突 fail-closed：不调用 Agent、不重试，只落失败日志并返回。
            # 安全异常不得走 logger.opt(exception=True)：Loguru diagnose 可能把
            # traceback frame locals（含双方租户值）写进日志，造成租户标识泄漏。
            duration_ms = int((time.time() - start_time) * 1000)
            completed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            logger.error("后端日志：定时任务租户上下文冲突，已拒绝执行")
            log = ScheduledTaskLogDB.create(
                task_id=task_id, user_id=user_id, session_id=cron_session_id,
                status="failed", trigger_type=trigger_type,
                error_message="定时任务租户上下文冲突", duration_ms=duration_ms,
                started_at=started_at, completed_at=completed_at,
                tenant_id=task_tenant_id,
            )
            ScheduledTaskDB.update_after_run(
                task_id, success=False, result_summary="失败: 定时任务租户上下文冲突"
            )
            return {
                "success": False,
                "error": "定时任务租户上下文冲突",
                "log_id": log.get("log_id") if log else "",
            }

        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            completed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            error_msg = str(e)
            error_trace = ""

            import traceback
            error_trace = traceback.format_exc()

            logger.opt(exception=True).error(f"后端日志：定时任务执行失败 task_id={task_id}, error={error_msg}")

            # 5. 记录失败日志（租户随任务属主落库）
            log = ScheduledTaskLogDB.create(
                task_id=task_id, user_id=user_id, session_id=cron_session_id,
                status="failed", trigger_type=trigger_type,
                error_message=error_msg, error_trace=error_trace,
                duration_ms=duration_ms, started_at=started_at, completed_at=completed_at,
                tenant_id=task_tenant_id
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
                      user_input: str = "",
                      tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """
        试执行（创建定时任务前的验证）

        tenant_id：调用方（工具层）已解析的可信租户（''=公共用户，None=未知），
        与 users 行租户走同一解析与冲突规则，用于构建执行身份。

        Returns: {"success": bool, "result": str, "error": str}
        """
        started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        start_time = time.time()
        cron_session_id = self._get_cron_session_id(user_id)

        logger.info(f"后端日志：定时任务试执行开始 user_id={user_id}")

        try:
            user_dict = self._build_user(user_id)
            user_obj = None
            # 与正式执行同一租户解析与冲突规则（用户行优先、空串公共租户
            # 不被覆盖、双非空不等抛冲突）。
            exec_tenant_id = _resolve_exec_tenant_id(user_dict, tenant_id)
            if user_dict:
                try:
                    from src.models.user import User
                    user_obj = User(
                        user_id=user_dict["user_id"],
                        name=user_dict.get("username") or user_dict.get("phone") or user_id,
                        tenant_id=exec_tenant_id,
                        phone=user_dict.get("phone", ""),
                        role=user_dict.get("role", "employee")
                    )
                except Exception:
                    user_obj = None

            # 与正式执行相同：设入请求级租户上下文，finally 恢复进入前值
            # 而非 clear——本方法会被创建工具在请求协程内 await，clear 会把
            # 整个请求的租户上下文清掉，后续任务落库将拿到空租户。
            _prev_tenant = get_current_tenant_id()
            _prev_user = get_current_user_id()
            set_tenant_context(exec_tenant_id, user_id)
            try:
                result = await self.agent.process_message_sync(
                    user_input=task_prompt,
                    session_id=cron_session_id,
                    user=user_obj
                )
            finally:
                set_tenant_context(_prev_tenant, _prev_user)

            duration_ms = int((time.time() - start_time) * 1000)
            logger.info(f"后端日志：定时任务试执行成功 user_id={user_id}, duration={duration_ms}ms")

            return {"success": True, "result": result}

        except TenantContextConflictError:
            # 不附 traceback/locals，避免冲突租户标识进入日志；不调用 Agent。
            logger.error("后端日志：定时任务试执行租户上下文冲突，已拒绝执行")
            return {
                "success": False,
                "result": "",
                "error": "定时任务租户上下文冲突",
            }
        except Exception as e:
            error_msg = str(e)
            logger.opt(exception=True).error(f"后端日志：定时任务试执行失败 user_id={user_id}, error={error_msg}")
            return {"success": False, "result": "", "error": error_msg}
