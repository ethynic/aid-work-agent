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
from src.core.cache_utils import CacheKeys

# 全局单例
_executor = ScheduledTaskExecutor()
# 分布式锁 key（走 make_key 自动拼接 REDIS_KEY_PREFIX，避免裸键）
_SCHEDULER_LOCK_KEY = redis_client.make_key(CacheKeys.SCHEDULER_LOCK, "manager")


class ScheduledTaskManager:
    """定时任务调度管理器"""

    def __init__(self):
        self._scheduler: Optional[BackgroundScheduler] = None
        self._jobs: Dict[str, str] = {}  # task_id -> apscheduler job_id
        self._running = False
        self._lock_value: Optional[str] = None
        # reconcile 对账缓存：task_id -> 最近一次注册时的签名 tuple
        # 签名按 (schedule_type, cron, interval, status, updated_at) 计算，
        # 仅当签名变化时才重注册，避免每 30s 重置 interval 计时
        self._reconcile_seen: Dict[str, tuple] = {}

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
                logger.opt(exception=True).error(f"后端日志：注册定时任务失败 task_id={task['task_id']}, {e}")

        self._scheduler.start()
        self._running = True

        # 注册系统级定时任务：每日记忆总结
        self._register_system_jobs()

        logger.info(f"后端日志：定时任务调度器已启动，已注册 {registered_count} 个活跃任务")

    def _register_system_jobs(self):
        """注册系统级定时任务"""
        try:
            from src.config.settings import settings
        except Exception as e:
            logger.error(f"后端日志：_register_system_jobs 加载 settings 失败: {e}")
            return

        try:
            if settings.memory.long_term.enabled:
                cron_expr = settings.memory.long_term.summary_cron or "0 2 * * *"
                parts = cron_expr.split()
                trigger = CronTrigger(
                    minute=parts[0] if len(parts) > 0 else "0",
                    hour=parts[1] if len(parts) > 1 else "2",
                    day=parts[2] if len(parts) > 2 else "*",
                    month=parts[3] if len(parts) > 3 else "*",
                    day_of_week=parts[4] if len(parts) > 4 else "*",
                    timezone="Asia/Shanghai",
                )
                self._scheduler.add_job(
                    self._run_memory_summarizer,
                    trigger,
                    id="job_system_memory_summarizer",
                    name="Daily Memory Summarizer",
                    max_instances=1,
                )
                logger.info(f"后端日志：已注册每日记忆总结任务 (cron={cron_expr})")
        except Exception as e:
            logger.error(f"后端日志：注册记忆总结任务失败: {e}")

        # Phase 8：上下文压缩后台扫描（§2.5 补漏机制）
        try:
            mt = settings.memory.mid_term
            if mt.enabled and mt.background_scan_enabled:
                self._scheduler.add_job(
                    self._run_compression_scan,
                    IntervalTrigger(seconds=mt.background_scan_interval_sec),
                    id="job_system_compression_scan",
                    name="Context Compression Scan",
                    max_instances=1,
                    coalesce=True,
                )
                logger.info(
                    f"后端日志：已注册上下文压缩扫描任务 "
                    f"(interval={mt.background_scan_interval_sec}s, batch={mt.background_scan_batch_size})"
                )
        except Exception as e:
            logger.error(f"后端日志：注册压缩扫描任务失败: {e}")

        # ===== D11：reconcile 对账（每 30s 扫 scheduled_tasks 同步 APScheduler）=====
        try:
            self._scheduler.add_job(
                self._reconcile,
                IntervalTrigger(seconds=30),
                id="job_system_reconcile",
                name="Scheduled Tasks Reconcile",
                max_instances=1,
                coalesce=True,
            )
            logger.info("后端日志：已注册定时任务对账任务 (interval=30s)")
        except Exception as e:
            logger.error(f"后端日志：注册对账任务失败: {e}")

        # ===== D15：memory_cleanup（同步，interval）— 迁自 main.py _memory_cleanup_loop =====
        try:
            self._scheduler.add_job(
                self._run_memory_cleanup,
                IntervalTrigger(seconds=settings.memory.cleanup_interval),
                id="job_system_memory_cleanup",
                name="Memory Cleanup",
                max_instances=1,
                coalesce=True,
            )
            logger.info(
                f"后端日志：已注册会话记忆清理任务 (interval={settings.memory.cleanup_interval}s)"
            )
        except Exception as e:
            logger.error(f"后端日志：注册会话记忆清理任务失败: {e}")

        # ===== D15：dedup_cleanup（同步，cron 03:00）— 迁自 main.py _dedup_cleanup_loop =====
        try:
            self._scheduler.add_job(
                self._run_dedup_cleanup,
                CronTrigger(hour=3, minute=0, timezone="Asia/Shanghai"),
                id="job_system_dedup_cleanup",
                name="Channel Dedup Cleanup",
                max_instances=1,
                coalesce=True,
            )
            logger.info("后端日志：已注册渠道去重清理任务 (cron=03:00)")
        except Exception as e:
            logger.error(f"后端日志：注册渠道去重清理任务失败: {e}")

        # ===== D14：wecom_kf_timeout（async，interval 60s）=====
        try:
            self._scheduler.add_job(
                self._run_wecom_kf_timeout,
                IntervalTrigger(seconds=60),
                id="job_system_wecom_kf_timeout",
                name="WeCom KF Timeout Check",
                max_instances=1,
                coalesce=True,
            )
            logger.info("后端日志：已注册微信客服人工会话超时检查任务 (interval=60s)")
        except Exception as e:
            logger.error(f"后端日志：注册微信客服超时检查任务失败: {e}")

        # ===== D10：S1 发布调度钩子（未实现时 ImportError 跳过）=====
        try:
            from src.social_media.publishing.dispatcher import PublishJobDispatcher  # noqa: F401
            self._scheduler.add_job(
                self._run_publish_dispatch,
                IntervalTrigger(seconds=60),
                id="job_system_publish_dispatch",
                name="Social Publish Dispatcher",
                max_instances=1,
                coalesce=True,
            )
            logger.info("后端日志：已注册社媒发布调度任务 (interval=60s)")
        except ImportError:
            logger.debug("后端日志：PublishJobDispatcher 尚未实现，跳过（S1 未落地）")
        except Exception as e:
            logger.error(f"后端日志：注册发布调度任务失败: {e}")

        # ===== 工作成果复盘任务（每日 02:30）=====
        # 设计文档 docs/system/work-outcome-record-design.md §6
        # 复盘昨天有对话但无文件型成果的会话，用小模型提取 action/decision/other 成果
        try:
            self._scheduler.add_job(
                self._run_work_outcome_review,
                CronTrigger(hour=2, minute=30, timezone="Asia/Shanghai"),
                id="job_system_work_outcome_review",
                name="Work Outcome Review",
                max_instances=1,
                coalesce=True,
            )
            logger.info("后端日志：已注册工作成果复盘任务 (cron=02:30)")
        except Exception as e:
            logger.error(f"后端日志：注册工作成果复盘任务失败: {e}")

        # ===== 视频生成状态轮询（每 30s，只在 background_runner 进程跑）=====
        # 设计文档 docs/system/content-production/mvp-design.md §10
        # 扫描 PENDING/RUNNING 的万相 card，SUCCEEDED 下载成片（含烧录 AI 标识）+ 注册 file_id
        try:
            self._scheduler.add_job(
                self._run_video_gen_poll,
                IntervalTrigger(seconds=settings.video_gen.wanx.poll_interval_seconds),
                id="job_system_video_gen_poll",
                name="Video Generation Status Poller",
                max_instances=1,
                coalesce=True,
            )
            logger.info(f"后端日志：已注册视频生成轮询任务 (interval={settings.video_gen.wanx.poll_interval_seconds}s)")
        except Exception as e:
            logger.error(f"后端日志：注册视频生成轮询任务失败: {e}")

        # ===== skill_ws 临时工作目录残留清理（每日 03:30）=====
        # 正常路径由 agent.process_message 结束时删除工作目录；
        # 本任务兜底清理进程崩溃 / 异常退出遗留的 skill_ws_* 目录
        try:
            self._scheduler.add_job(
                self._run_skill_ws_cleanup,
                CronTrigger(hour=3, minute=30, timezone="Asia/Shanghai"),
                id="job_system_skill_ws_cleanup",
                name="Skill Workspace Cleanup",
                max_instances=1,
                coalesce=True,
            )
            logger.info("后端日志：已注册技能工作目录清理任务 (cron=03:30)")
        except Exception as e:
            logger.error(f"后端日志：注册技能工作目录清理任务失败: {e}")

        # ===== 用户行为审计日志清理（每日 04:00）=====
        # 设计文档 docs/system/user-behavior-audit-log-design.md §6.2：
        # 按 behavior_log.retention_days（默认 180 天）分批删除过期日志，每批 1 万行避免长锁
        try:
            self._scheduler.add_job(
                self._run_behavior_log_cleanup,
                CronTrigger(hour=4, minute=0, timezone="Asia/Shanghai"),
                id="job_system_behavior_log_cleanup",
                name="User Behavior Log Cleanup",
                max_instances=1,
                coalesce=True,
            )
            logger.info("后端日志：已注册用户行为日志清理任务 (cron=04:00)")
        except Exception as e:
            logger.error(f"后端日志：注册用户行为日志清理任务失败: {e}")

        # ===== weixin_marketing 调度闭环（R42 门控：enabled=false 零注册；R44 四类 tick）=====
        # tick 函数与 APScheduler 解耦（src/weixin_marketing/dispatch.py，可直接注入
        # now/batch/config 调用）；interval 从 weixin_marketing 配置节读取（热改 yaml
        # 需重启生效——APScheduler job 注册仅在调度器启动时执行一次）
        try:
            from src.weixin_marketing import dispatch as wxm_dispatch
            from src.weixin_marketing.config import get_weixin_marketing_config

            wxm_cfg = get_weixin_marketing_config()
            if wxm_cfg.enabled:
                self._scheduler.add_job(
                    wxm_dispatch.time_scan_tick,
                    IntervalTrigger(seconds=wxm_cfg.time_scan_interval_seconds),
                    id="job_system_weixin_marketing_time_scan",
                    name="Weixin Marketing Time Scan",
                    max_instances=1,
                    coalesce=True,
                )
                self._scheduler.add_job(
                    wxm_dispatch.run_dispatch_tick,
                    IntervalTrigger(seconds=wxm_cfg.dispatch_interval_seconds),
                    id="job_system_weixin_marketing_dispatch",
                    name="Weixin Marketing Run Dispatch",
                    max_instances=1,
                    coalesce=True,
                )
                self._scheduler.add_job(
                    wxm_dispatch.permits_sweep_tick,
                    IntervalTrigger(seconds=wxm_cfg.permits_sweep_interval_seconds),
                    id="job_system_weixin_marketing_sweep",
                    name="Weixin Marketing Permits Sweep",
                    max_instances=1,
                    coalesce=True,
                )
                self._scheduler.add_job(
                    wxm_dispatch.runs_reclaim_tick,
                    IntervalTrigger(seconds=wxm_cfg.runs_reclaim_interval_seconds),
                    id="job_system_weixin_marketing_reclaim",
                    name="Weixin Marketing Runs Reclaim",
                    max_instances=1,
                    coalesce=True,
                )
                # P4-A：过期无引用素材清理（R57；retention_days/间隔可配）
                self._scheduler.add_job(
                    wxm_dispatch.assets_cleanup_tick,
                    IntervalTrigger(seconds=wxm_cfg.assets_cleanup_interval_seconds),
                    id="job_system_weixin_marketing_assets_cleanup",
                    name="Weixin Marketing Assets Cleanup",
                    max_instances=1,
                    coalesce=True,
                )
                # P4-B：事件匹配 worker（R57；受 event_triggers_enabled 细分门控）
                self._scheduler.add_job(
                    wxm_dispatch.event_match_tick,
                    IntervalTrigger(seconds=wxm_cfg.event_match_interval_seconds),
                    id="job_system_weixin_marketing_event_match",
                    name="Weixin Marketing Event Match",
                    max_instances=1,
                    coalesce=True,
                )
                # P5 R59③：payloads/occurrences 保留期清理（enabled 门控；retention 可配）
                self._scheduler.add_job(
                    wxm_dispatch.retention_cleanup_tick,
                    IntervalTrigger(seconds=wxm_cfg.retention_cleanup_interval_seconds),
                    id="job_system_weixin_marketing_retention_cleanup",
                    name="Weixin Marketing Retention Cleanup",
                    max_instances=1,
                    coalesce=True,
                )
                # P5 R59③：磁盘孤儿素材扫描（只读告警；assets_orphan_scan_enabled 默认关）
                self._scheduler.add_job(
                    wxm_dispatch.assets_orphan_scan_tick,
                    IntervalTrigger(seconds=wxm_cfg.assets_orphan_scan_interval_seconds),
                    id="job_system_weixin_marketing_assets_orphan_scan",
                    name="Weixin Marketing Assets Orphan Scan",
                    max_instances=1,
                    coalesce=True,
                )
                logger.info(
                    f"后端日志：已注册 weixin_marketing 调度闭环任务 "
                    f"(time_scan={wxm_cfg.time_scan_interval_seconds}s, "
                    f"dispatch={wxm_cfg.dispatch_interval_seconds}s, "
                    f"sweep={wxm_cfg.permits_sweep_interval_seconds}s, "
                    f"reclaim={wxm_cfg.runs_reclaim_interval_seconds}s, "
                    f"assets_cleanup={wxm_cfg.assets_cleanup_interval_seconds}s, "
                    f"event_match={wxm_cfg.event_match_interval_seconds}s, "
                    f"retention_cleanup={wxm_cfg.retention_cleanup_interval_seconds}s, "
                    f"assets_orphan_scan={wxm_cfg.assets_orphan_scan_interval_seconds}s)"
                )
            else:
                logger.debug("后端日志：weixin_marketing 未启用，跳过调度 tick 注册（R42 零注册）")
        except Exception as e:
            logger.error(f"后端日志：注册 weixin_marketing 调度任务失败: {e}")

    def _run_memory_summarizer(self):
        """执行每日记忆总结（APScheduler 回调）"""
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            from src.memory.memory_summarizer import run_memory_summarization
            stats = loop.run_until_complete(run_memory_summarization())
            logger.info(f"后端日志：每日记忆总结完成: {stats}")
        except Exception as e:
            logger.opt(exception=True).error(f"后端日志：每日记忆总结失败: {e}")
        finally:
            try:
                loop.close()
            except Exception:
                pass

    def _run_compression_scan(self):
        """Phase 8：上下文压缩后台扫描（APScheduler 回调，后台线程执行）"""
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            from src.memory.mid_term import run_background_compression_scan
            stats = loop.run_until_complete(run_background_compression_scan())
            logger.info(f"后端日志：上下文压缩扫描完成: {stats}")
        except Exception as e:
            logger.opt(exception=True).error(f"后端日志：上下文压缩扫描失败: {e}")
        finally:
            try:
                loop.close()
            except Exception:
                pass

    # ===== D11：reconcile 对账（核心）=====
    def _reconcile(self):
        """每 30s 扫描 scheduled_tasks，把 DB 状态同步到 APScheduler。

        - active 任务：按签名（schedule_type/cron/interval/status/updated_at）变化重注册
        - paused 任务：从 APScheduler 移除
        - DB 已删除/取消的任务：从 APScheduler 移除
        - manual_trigger_at 非空：立即执行一次再清空

        幂等：按 updated_at 判变化，未变化不重注册（避免重置 interval 计时）。
        """
        try:
            rows = ScheduledTaskDB.list_all_for_reconcile()
            db_ids = set()
            for t in rows:
                tid = t["task_id"]
                db_ids.add(tid)
                # 单任务隔离：一条畸形任务（坏 cron / scheduler 异常）不得中断整轮对账，
                # 否则会每 30s 卡住所有其他任务的注册/移除/触发。
                try:
                    sig = (
                        t.get("schedule_type"),
                        t.get("cron_expression"),
                        t.get("interval_seconds"),
                        t.get("status"),
                        str(t.get("updated_at")),
                    )
                    if t.get("status") == "active":
                        if self._reconcile_seen.get(tid) != sig:
                            self._register_job(t)  # replace_existing=True 幂等
                            self._reconcile_seen[tid] = sig
                    else:
                        # paused 等非 active 状态：从调度器移除
                        if tid in self._jobs:
                            self.remove_task(tid)
                        self._reconcile_seen.pop(tid, None)

                    # 手动触发：非空则立即执行一次，再清空标记
                    if t.get("manual_trigger_at"):
                        self._fire_once(tid, trigger_type="manual")
                        try:
                            ScheduledTaskDB.clear_manual_trigger(tid)
                        except Exception as e:
                            logger.opt(exception=True).error(f"后端日志：清除手动触发标记失败 task_id={tid}, {e}")
                except Exception as e:
                    logger.opt(exception=True).error(
                        f"后端日志：reconcile 处理单任务失败 task_id={tid}（跳过该任务，继续对账）: {e}",
                    )

            # 清理 DB 中已不存在的任务（被 cancel/delete）
            for tid in list(self._jobs.keys()):
                if tid not in db_ids:
                    self.remove_task(tid)
                    self._reconcile_seen.pop(tid, None)
        except Exception as e:
            logger.opt(exception=True).error(f"后端日志：reconcile 对账失败: {e}")

    def _fire_once(self, task_id: str, trigger_type: str = "manual"):
        """立即执行一次任务（独立线程 + 新 event loop）。

        抽自 trigger_task 的异步执行分支，供 reconcile 手动触发复用。
        """
        import threading

        def _run():
            loop = asyncio.new_event_loop()
            try:
                asyncio.set_event_loop(loop)
                loop.run_until_complete(_executor.execute(task_id, trigger_type=trigger_type))
            except Exception as e:
                logger.opt(exception=True).error(
                    f"后端日志：_fire_once 执行失败 task_id={task_id}, trigger={trigger_type}, {e}",
                )
            finally:
                try:
                    loop.close()
                except Exception:
                    pass

        threading.Thread(target=_run, daemon=True).start()

    # ===== D15：memory_cleanup 回调（同步直调）=====
    def _run_memory_cleanup(self):
        """清理过期会话短期记忆（APScheduler 回调，后台线程执行）。

        迁自 main.py _memory_cleanup_loop 循环体。cleanup_expired 是同步方法。
        """
        try:
            from src.core.agent import master_agent
            cleaned = master_agent.memory.cleanup_expired()
            if cleaned > 0:
                logger.debug(f"后端日志：Memory cleanup cleaned {cleaned} expired sessions")
        except Exception as e:
            logger.opt(exception=True).error(f"后端日志：会话记忆清理异常: {e}")

    # ===== D15：dedup_cleanup 回调（同步，保留 psycopg2 降级）=====
    def _run_dedup_cleanup(self):
        """清理过期渠道消息去重记录（APScheduler 回调）。

        迁自 main.py _dedup_cleanup_loop 循环体。保留 psycopg2.OperationalError 降级。
        """
        try:
            import psycopg2
            from src.channels.idempotency import MessageDeduplicator
            dedup = MessageDeduplicator(ttl_seconds=300)
            cleaned = dedup.cleanup_expired()
            if cleaned > 0:
                logger.info(f"后端日志：Channel dedup cleanup cleaned {cleaned} expired records")
        except psycopg2.OperationalError as e:
            logger.warning(
                f"后端日志：Channel dedup cleanup error (DB connection issue, will retry next cycle): {e}"
            )
        except Exception as e:
            logger.opt(exception=True).error(f"后端日志：Channel dedup cleanup error: {e}")

    # ===== skill_ws 残留清理回调（同步）=====
    def _run_skill_ws_cleanup(self):
        """清理租户 temp 下超过 3 天的 skill_ws_* 残留目录（APScheduler 回调）。

        正常路径由 agent.process_message 结束时删除工作目录；
        本任务兜底处理进程崩溃 / 异常退出遗留的目录。
        """
        try:
            import shutil
            import time
            from pathlib import Path

            from src.core.storage import get_tenants_storage_root

            tenants_root = Path(get_tenants_storage_root())
            if not tenants_root.exists():
                return 0

            max_age_seconds = 3 * 24 * 3600  # 3 天
            now = time.time()
            cleaned = 0
            for ws_dir in tenants_root.glob("*/temp/skill_ws_*"):
                try:
                    if not ws_dir.is_dir():
                        continue
                    # 目录 mtime 随最后写入更新；超过 3 天视为无活跃使用的残留
                    if now - ws_dir.stat().st_mtime <= max_age_seconds:
                        continue
                    shutil.rmtree(ws_dir, ignore_errors=True)
                    cleaned += 1
                    logger.info(f"后端日志：清理过期技能工作目录 {ws_dir}")
                except Exception as e:
                    logger.warning(f"后端日志：清理技能工作目录失败 {ws_dir}: {e}")
            if cleaned > 0:
                logger.info(f"后端日志：skill_ws 残留清理完成，共清理 {cleaned} 个目录")
            return cleaned
        except Exception as e:
            logger.opt(exception=True).error(f"后端日志：skill_ws 残留清理任务异常: {e}")
            return 0

    # ===== 用户行为日志清理回调（同步，分批删除避免长锁）=====
    def _run_behavior_log_cleanup(self):
        """清理过期的用户行为审计日志（APScheduler 回调）

        按 behavior_log.retention_days（默认 180 天）删除 user_behavior_logs 过期行，
        每批 1 万行循环删除，避免长事务长锁。结果（删除行数、耗时）记 logger.info。
        """
        from src.db.database import get_db_connection
        from src.config.settings import settings

        started_at = datetime.now()
        total_deleted = 0
        batch_size = 10000
        try:
            retention_days = settings.behavior_log.retention_days
            while True:
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        """
                        DELETE FROM user_behavior_logs
                        WHERE id IN (
                            SELECT id FROM user_behavior_logs
                            WHERE created_at < NOW() - (%s * INTERVAL '1 day')
                            LIMIT %s
                        )
                        """,
                        (retention_days, batch_size),
                    )
                    deleted = cursor.rowcount
                    conn.commit()
                total_deleted += deleted
                if deleted < batch_size:
                    break
            elapsed = (datetime.now() - started_at).total_seconds()
            logger.info(
                f"后端日志：用户行为日志清理完成，删除 {total_deleted} 行"
                f"（保留 {retention_days} 天），耗时 {elapsed:.1f}s"
            )
        except Exception as e:
            logger.opt(exception=True).error(
                f"后端日志：用户行为日志清理失败（已删除 {total_deleted} 行）: {e}"
            )

    # ===== D14：wecom_kf_timeout 回调（async tick + 新 event loop）=====
    def _run_wecom_kf_timeout(self):
        """检查微信客服人工会话超时（APScheduler 回调，后台线程执行）。

        迁自 main.py _wecom_kf_timeout_check_loop 循环体，抽取为单次 tick。
        新建 event loop run_until_complete（同 _run_memory_summarizer 模式）。
        """
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._wecom_kf_tick())
        except Exception as e:
            logger.opt(exception=True).error(f"后端日志：微信客服超时检查异常: {e}")
        finally:
            try:
                loop.close()
            except Exception:
                pass

    async def _wecom_kf_tick(self):
        """微信客服人工会话超时检查单次 tick（迁自 main.py _wecom_kf_timeout_check_loop 循环体）。

        保留原业务逻辑和异常隔离，仅去掉 while True + asyncio.sleep。
        """
        from datetime import datetime

        from src.channels.session import channel_session_manager
        from src.db.database import get_db_connection

        default_timeout_minutes = 8
        try:
            # 查询所有 wecom_kf 且 service_state=3 的会话
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT session_id, tenant_id, channel_chat_id, channel_user_id,
                           last_message_at, metadata
                    FROM channel_sessions
                    WHERE channel_type = 'wecom_kf'
                      AND metadata::text LIKE '%"service_state"%3%'
                      AND metadata::text NOT LIKE '%"exit_human_timeout_failed_at"%'
                """)
                rows = cursor.fetchall()

            if not rows:
                return

            now = datetime.now()
            for row in rows:
                try:
                    session = dict(row)
                    session_id = session["session_id"]
                    tenant_id = session["tenant_id"]
                    open_kfid = session.get("channel_chat_id", "")
                    external_userid = session["channel_user_id"]
                    last_message_at_val = session.get("last_message_at")

                    if not last_message_at_val or not open_kfid or not tenant_id:
                        continue

                    # last_message_at 在 PostgreSQL 中是 TIMESTAMP，psycopg2 通常返回 datetime 对象；
                    # 极少数情况（旧数据/手动写入）可能是字符串，需兼容处理
                    if hasattr(last_message_at_val, "strftime"):
                        last_msg_time = last_message_at_val
                    else:
                        last_msg_str = str(last_message_at_val)
                        if "." in last_msg_str:
                            last_msg_str = last_msg_str.split(".")[0]
                        last_msg_time = datetime.strptime(last_msg_str, "%Y-%m-%d %H:%M:%S")
                    elapsed_minutes = (now - last_msg_time).total_seconds() / 60

                    # 从租户渠道配置获取超时时间
                    from src.saas.db.channel_config_db import ChannelConfigDB
                    configs = ChannelConfigDB.list_by_tenant(tenant_id, "wecom_kf")
                    timeout_minutes = default_timeout_minutes
                    for cfg in configs:
                        kf_accounts = cfg.get("config", {}).get("kf_account", [])
                        for kf in kf_accounts:
                            if kf.get("open_kfid") == open_kfid:
                                timeout_minutes = kf.get("exit_human_timeout_minutes", default_timeout_minutes)
                                break

                    if elapsed_minutes < timeout_minutes:
                        continue

                    logger.info(
                        f"[wecom_kf] 人工会话超时: session_id={session_id}, "
                        f"elapsed={elapsed_minutes:.1f}min, threshold={timeout_minutes}min"
                    )

                    # 创建 adapter
                    from src.saas.services.channel_factory import ChannelFactory
                    adapter, _, _ = await ChannelFactory.create_from_tenant_config(
                        tenant_id, "wecom_kf"
                    )
                    if adapter is None:
                        logger.warning(
                            f"[wecom_kf] 超时检查：无法创建 adapter: tenant_id={tenant_id}"
                        )
                        continue

                    # 先查询微信侧实际状态，防止员工已结束对话但本地状态未更新
                    remote_state = await adapter.api_client.get_service_state(
                        open_kfid, external_userid
                    )
                    remote_service_state = remote_state.get("service_state")
                    logger.info(
                        f"[wecom_kf] 超时检查远程状态: session_id={session_id}, "
                        f"local_state=3, remote_state={remote_service_state}"
                    )

                    if remote_service_state == 4:
                        channel_session_manager.update_session(
                            session_id=session_id,
                            metadata={"service_state": 4},
                        )
                        logger.info(
                            f"[wecom_kf] 远程已结束，跳过超时处理: session_id={session_id}"
                        )
                        await adapter.close()
                        continue

                    if remote_service_state != 3:
                        channel_session_manager.update_session(
                            session_id=session_id,
                            metadata={"service_state": remote_service_state},
                        )
                        logger.info(
                            f"[wecom_kf] 远程状态已变更({remote_service_state})，跳过超时处理: "
                            f"session_id={session_id}"
                        )
                        await adapter.close()
                        continue

                    # 远程仍是人工状态，执行超时退出（结束会话）
                    adapter.current_open_kfid = open_kfid
                    result = await adapter.end_human_service(open_kfid, external_userid)
                    if result:
                        channel_session_manager.update_session(
                            session_id=session_id,
                            metadata={"service_state": 4},
                        )
                        await adapter.send_text(
                            f"人工服务已超时（超过{timeout_minutes}分钟无新消息），"
                            f"本次会话已结束。如有新问题，请重新发送消息。",
                            external_userid,
                        )
                        logger.info(
                            f"[wecom_kf] 超时结束人工会话成功: session_id={session_id}"
                        )
                    else:
                        channel_session_manager.update_session(
                            session_id=session_id,
                            metadata={
                                "service_state": 3,
                                "exit_human_timeout_failed_at": now.strftime("%Y-%m-%d %H:%M:%S"),
                            },
                        )
                        logger.error(
                            f"[wecom_kf] 超时结束人工会话失败，保留人工状态避免远程不一致: "
                            f"session_id={session_id}"
                        )
                    await adapter.close()

                except Exception as e:
                    logger.opt(exception=True).error(
                        f"[wecom_kf] 超时检查处理单个会话异常: "
                        f"session_id={session.get('session_id', 'unknown')}: {e}",
                    )
        except Exception as e:
            try:
                raw_sql = cursor.query.decode() if cursor and cursor.query else None
            except Exception:
                raw_sql = None
            logger.opt(exception=True).error(
                f"[wecom_kf] 超时检查异常: {e} | raw_sql={raw_sql}",
            )

    # ===== D10：S1 发布调度回调（占位，随 S1 落地）=====
    def _run_publish_dispatch(self):
        """社媒发布调度（APScheduler 回调）。S1 未落地时不会被注册。"""
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            from src.social_media.publishing.dispatcher import PublishJobDispatcher
            dispatcher = PublishJobDispatcher()
            stats = loop.run_until_complete(dispatcher.dispatch_once())
            logger.info(f"后端日志：社媒发布调度完成: {stats}")
        except Exception as e:
            logger.opt(exception=True).error(f"后端日志：社媒发布调度失败: {e}")
        finally:
            try:
                loop.close()
            except Exception:
                pass

    # ===== 工作成果复盘回调（async，每日 02:30）=====
    def _run_work_outcome_review(self):
        """执行工作成果复盘任务（APScheduler 回调，后台线程执行）。

        参考设计文档 docs/system/work-outcome-record-design.md §6。
        使用 asyncio.new_event_loop + run_until_complete 调用 async 入口
        （同 _run_memory_summarizer 模式），复盘昨天（自然日）的会话。
        """
        from datetime import date, timedelta

        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            from src.reports.work_outcome_review import run_daily_review
            batch_id = loop.run_until_complete(
                run_daily_review(date.today() - timedelta(days=1))
            )
            logger.info(f"后端日志：工作成果复盘完成 batch_id={batch_id}")
        except Exception as e:
            logger.opt(exception=True).error(f"后端日志：工作成果复盘失败: {e}")
        finally:
            try:
                loop.close()
            except Exception:
                pass

    # ===== 视频生成状态轮询回调（async，每 30s）=====
    def _run_video_gen_poll(self):
        """视频生成状态轮询（APScheduler 回调，后台线程执行）。

        参考设计文档 docs/system/content-production/mvp-design.md §10。
        新建 event loop run_until_complete（同 _run_memory_summarizer 模式）。
        只在 background_runner 进程跑（调度器有 manager 级 Redis 锁）。
        """
        # 临时 tlog：标记 background_runner 触发视频轮询
        try:
            from src.core.temp_logger import tlog
            tlog("video-agent-阶段三", "background_runner._run_video_gen_poll 触发")
        except Exception:
            pass

        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._video_gen_poll_tick())
        except Exception as e:
            logger.opt(exception=True).error(f"后端日志：视频生成轮询异常: {e}")
            try:
                from src.core.temp_logger import tlog
                tlog("video-agent-阶段三", "background_runner 轮询异常 err={err}", err=str(e), level="ERROR")
            except Exception:
                pass
        finally:
            try:
                loop.close()
            except Exception:
                pass

    async def _video_gen_poll_tick(self):
        """视频生成轮询单次 tick。"""
        from src.video_gen.service import VideoGenService
        try:
            svc = VideoGenService()
            n = await svc.poll_pending_cards()
            # 临时 tlog：标记 tick 执行结果（含 0 也要记，便于排查是否真的在跑）
            try:
                from src.core.temp_logger import tlog
                tlog("video-agent-阶段三", "background_runner._video_gen_poll_tick 完成 processed={n}", n=n)
            except Exception:
                pass
            if n > 0:
                logger.info(f"后端日志：视频生成轮询处理 {n} 条 card")
        except Exception as e:
            logger.opt(exception=True).error(f"后端日志：视频生成轮询 tick 异常: {e}")
            try:
                from src.core.temp_logger import tlog
                tlog("video-agent-阶段三", "background_runner tick 异常 err={err}", err=str(e), level="ERROR")
            except Exception:
                pass

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
            logger.opt(exception=True).error(f"后端日志：移除定时任务失败 task_id={task_id}, {e}")
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
            logger.opt(exception=True).error(f"后端日志：定时任务执行异常 task_id={task_id}, {e}")
        finally:
            try:
                loop.close()
            except Exception:
                pass


# 全局单例
scheduled_task_manager = ScheduledTaskManager()
