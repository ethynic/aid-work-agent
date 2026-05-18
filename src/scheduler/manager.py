"""定时任务调度管理器"""

import asyncio
import os
import threading
import traceback
from datetime import datetime
from typing import Optional, Dict

from loguru import logger
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from src.scheduler.db import ScheduledTaskDB
from src.scheduler.executor import ScheduledTaskExecutor
from src.core.redis_client import redis_client

# 全局单例
_executor = ScheduledTaskExecutor()
# 分布式锁 key
_SCHEDULER_LOCK_KEY = "scheduled_task_manager:lock"


class ScheduledTaskManager:
    """定时任务调度管理器"""

    def __init__(self):
        self._scheduler: Optional[BackgroundScheduler] = None
        self._jobs: Dict[str, str] = {}  # task_id -> apscheduler job_id
        self._running = False
        self._lock_value: Optional[str] = None

    def start(self):
        """启动调度器（应用启动时调用）"""
        if self._running:
            logger.warning("后端日志：定时任务调度器已在运行中")
            return

        # 多 worker 环境下使用 Redis 分布式锁避免重复启动
        try:
            self._lock_value = f"{os.getpid()}:{threading.current_thread().ident}"
            acquired = redis_client.acquire_lock(_SCHEDULER_LOCK_KEY, self._lock_value, ex=300)
            if not acquired:
                logger.info("后端日志：定时任务调度器已在其他 worker 中运行，当前 worker 跳过启动")
                return
        except Exception as e:
            logger.warning(f"后端日志：分布式锁获取失败，继续启动调度器: {e}")
            self._lock_value = None

        self._scheduler = BackgroundScheduler(
            timezone="Asia/Shanghai",
            job_defaults={
                "coalesce": True,
                "max_instances": 1,
                "misfire_grace_time": 300
            }
        )

        # 从 DB 加载所有 active 任务并注册
        active_tasks = ScheduledTaskDB.list_active()
        registered_count = 0
        for task in active_tasks:
            try:
                self._register_job(task)
                registered_count += 1
            except Exception as e:
                logger.error(f"后端日志：注册定时任务失败 task_id={task['task_id']}, {e}", exc_info=True)

        self._scheduler.start()
        self._running = True
        logger.info(f"后端日志：定时任务调度器已启动，已注册 {registered_count} 个活跃任务")

    def shutdown(self):
        """优雅关闭"""
        if not self._running:
            return

        if self._scheduler:
            self._scheduler.shutdown(wait=True)
            self._scheduler = None
        self._running = False
        self._jobs.clear()

        # 释放分布式锁
        if self._lock_value:
            try:
                redis_client.release_lock(_SCHEDULER_LOCK_KEY, self._lock_value)
            except Exception:
                pass
            self._lock_value = None

        logger.info("后端日志：定时任务调度器已关闭")

    def register_task(self, task: dict) -> Optional[str]:
        """注册定时任务到 APScheduler，返回 apscheduler job_id"""
        return self._register_job(task)

    def remove_task(self, task_id: str) -> bool:
        """移除定时任务"""
        job_id = self._jobs.get(task_id)
        if not job_id or not self._scheduler:
            return False

        try:
            self._scheduler.remove_job(job_id)
            self._jobs.pop(task_id, None)
            logger.info(f"后端日志：定时任务已移除 task_id={task_id}")
            return True
        except Exception as e:
            logger.error(f"后端日志：移除定时任务失败 task_id={task_id}, {e}", exc_info=True)
            return False

    def pause_task(self, task_id: str) -> bool:
        """暂停定时任务"""
        job_id = self._jobs.get(task_id)
        if not job_id or not self._scheduler:
            return False

        try:
            self._scheduler.pause_job(job_id)
            ScheduledTaskDB.update_status(task_id, "paused")
            logger.info(f"后端日志：定时任务已暂停 task_id={task_id}")
            return True
        except Exception as e:
            logger.error(f"后端日志：暂停定时任务失败 task_id={task_id}, {e}", exc_info=True)
            return False

    def resume_task(self, task_id: str) -> bool:
        """恢复定时任务"""
        job_id = self._jobs.get(task_id)
        if not job_id or not self._scheduler:
            return False

        try:
            self._scheduler.resume_job(job_id)
            ScheduledTaskDB.update_status(task_id, "active")
            logger.info(f"后端日志：定时任务已恢复 task_id={task_id}")
            return True
        except Exception as e:
            logger.error(f"后端日志：恢复定时任务失败 task_id={task_id}, {e}", exc_info=True)
            return False

    def trigger_task(self, task_id: str) -> bool:
        """手动触发执行一次"""
        try:
            job_id = self._jobs.get(task_id)
            if job_id and self._scheduler:
                self._scheduler.modify_job(job_id, next_run_time=datetime.now())
            else:
                # 没有注册的 job，直接异步执行
                import threading
                def _run():
                    try:
                        loop = asyncio.new_event_loop()
                        loop.run_until_complete(_executor.execute(task_id, trigger_type="manual"))
                    finally:
                        loop.close()
                threading.Thread(target=_run, daemon=True).start()
            return True
        except Exception as e:
            logger.error(f"后端日志：手动触发定时任务失败 task_id={task_id}, {e}", exc_info=True)
            return False

    def _register_job(self, task: dict) -> Optional[str]:
        """注册单个任务到 APScheduler"""
        task_id = task["task_id"]
        schedule_type = task["schedule_type"]
        cron_expression = task.get("cron_expression")
        interval_seconds = task.get("interval_seconds")

        # 移除旧 job（如果存在）
        if task_id in self._jobs:
            try:
                self._scheduler.remove_job(self._jobs[task_id])
            except Exception:
                pass
            self._jobs.pop(task_id, None)

        # 构建 trigger
        if schedule_type == "once":
            # 一次性任务：使用 cron_expression 中存储的 datetime
            if cron_expression:
                try:
                    run_time = datetime.strptime(cron_expression, "%Y-%m-%d %H:%M:%S")
                    trigger = DateTrigger(run_date=run_time)
                except ValueError:
                    trigger = CronTrigger.from_crontab(cron_expression, timezone="Asia/Shanghai")
            else:
                logger.warning(f"后端日志：一次性任务缺少执行时间 task_id={task_id}")
                return None
        elif schedule_type == "interval" and interval_seconds:
            trigger = IntervalTrigger(seconds=interval_seconds)
        elif cron_expression:
            trigger = CronTrigger.from_crontab(cron_expression, timezone="Asia/Shanghai")
        else:
            logger.error(f"后端日志：定时任务缺少调度配置 task_id={task_id}")
            return None

        # 添加 job
        job = self._scheduler.add_job(
            self._execute_task,
            trigger=trigger,
            args=[task_id],
            id=f"job_{task_id}",
            name=task.get("name", task_id),
            replace_existing=True,
            max_instances=1,
        )

        job_id = job.id
        self._jobs[task_id] = job_id
        logger.info(f"后端日志：定时任务已注册 task_id={task_id}, job_id={job_id}, "
                    f"schedule_type={schedule_type}")
        return job_id

    def _execute_task(self, task_id: str):
        """任务执行入口（APScheduler 回调，在后台线程中执行）"""
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(_executor.execute(task_id, trigger_type="scheduled"))
        except Exception as e:
            logger.error(f"后端日志：定时任务执行异常 task_id={task_id}, {e}", exc_info=True)
        finally:
            try:
                loop.close()
            except Exception:
                pass


# 全局单例
scheduled_task_manager = ScheduledTaskManager()
