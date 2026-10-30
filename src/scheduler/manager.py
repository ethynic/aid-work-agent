"""定时任务调度管理器（系统级 job 专用）

v2 Runner 化重建（设计 §4）：用户定时任务不再经 APScheduler 注册/对账，
改为 dispatch.py 到期循环 + 数据库原子认领（Phase 3 落地）。本模块保留
Redis 锁 + 幽灵锁重试接管 + BackgroundScheduler 与全部系统级 job。
"""

import asyncio
import os
import threading
import time
from datetime import datetime
from typing import Optional

from loguru import logger
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from src.core.redis_client import redis_client
from src.core.cache_utils import CacheKeys

# 分布式锁 key（走 make_key 自动拼接 REDIS_KEY_PREFIX，避免裸键）
_SCHEDULER_LOCK_KEY = redis_client.make_key(CacheKeys.SCHEDULER_LOCK, "manager")
# 锁重试接管间隔（强杀残留幽灵锁场景：TTL 300s 过期后重试即可抢到）
_LOCK_RETRY_SECONDS = 30


def _ensure_any_session_scenario_registered() -> bool:
    """任一启用场景描述器注册成功即返回 True（B1.2 九处之八，设计 §4.2/§9.1 B1.3）。

    场景注册点在此显式列举（组合根；通用层 session_tasks 不反向 import 场景实现）。
    B1.3 起新场景（boss_conversation）追加为并列分支：任一注册成功即注册 worker。
    逐场景独立 try/ensure（不短路），保证多场景同时启用时各自注册（决策 worker
    按场景键取描述器，漏注册的场景任务无法被处理）；单场景注册失败不阻断其余场景。
    """
    registered = False
    try:
        from src.weixin_conversation import registration as wxconv_registration

        registered = wxconv_registration.ensure_registered() or registered
    except Exception as e:  # noqa: BLE001 单场景注册失败不阻断其余场景注册
        logger.error(f"后端日志：weixin_conversation 场景注册失败: {e}")
    try:
        from src.boss_conversation import registration as boss_registration

        registered = boss_registration.ensure_registered() or registered
    except Exception as e:  # noqa: BLE001 单场景注册失败不阻断其余场景注册
        logger.error(f"后端日志：boss_conversation 场景注册失败: {e}")
    return registered


class ScheduledTaskManager:
    """定时任务调度管理器"""

    def __init__(self):
        self._scheduler: Optional[BackgroundScheduler] = None
        self._running = False
        self._lock_value: Optional[str] = None
        # 锁重试接管线程（强杀残留幽灵锁场景）；None=未启动，启动成功后自然退出
        self._retry_thread: Optional[threading.Thread] = None

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
                # 锁被占：可能为强杀残留的幽灵锁（TTL 300s 过期后可接管），起后台
                # 线程重试接管，不直接放弃（否则锁过期后无人再尝试，调度器永久停摆）
                logger.info(
                    f"后端日志：定时任务调度器锁被占（可能为强杀残留幽灵锁），"
                    f"后台每 {_LOCK_RETRY_SECONDS}s 重试接管 lock_owner={self._lock_value}"
                )
                self._start_acquire_retry_thread()
                return
        except Exception as e:
            logger.warning(f"后端日志：分布式锁获取失败，继续启动调度器: {e}")
            self._lock_value = None

        self._start_scheduler_locked()

    def _start_scheduler_locked(self):
        """抢锁成功后的启动主体：构造调度器 → 启动 → 注册系统任务。

        用户定时任务不再在此加载（v2 由 dispatch.py 到期循环驱动，Phase 3）。
        """
        self._scheduler = BackgroundScheduler(
            timezone="Asia/Shanghai",
            job_defaults={
                "coalesce": True,
                "max_instances": 1,
                "misfire_grace_time": 300
            }
        )

        self._scheduler.start()
        self._running = True

        # 注册系统级定时任务：每日记忆总结
        self._register_system_jobs()

        logger.info("后端日志：定时任务调度器已启动（仅系统级 job；用户任务由 dispatch 循环驱动）")

    def _start_acquire_retry_thread(self):
        """启动锁重试接管线程（已有存活线程则不重复起，防双跑）。"""
        if self._retry_thread is not None and self._retry_thread.is_alive():
            return
        self._retry_thread = threading.Thread(
            target=self._acquire_retry_loop,
            args=(self._lock_value,),
            daemon=True,
            name="scheduler-lock-retry",
        )
        self._retry_thread.start()

    def _acquire_retry_loop(self, lock_value: str):
        """后台重试接管调度锁（幽灵锁残留场景）：锁 TTL 过期后 acquire 成功即补启动。

        while not self._running 保证：补启动成功（或调度器已被其他途径启动）后
        线程自然退出，不会在调度器运行期间重复抢锁导致双跑。
        """
        while not self._running:
            time.sleep(_LOCK_RETRY_SECONDS)
            try:
                if redis_client.acquire_lock(_SCHEDULER_LOCK_KEY, lock_value, ex=300):
                    self._lock_value = lock_value
                    self._start_scheduler_locked()
                    return
            except Exception as e:  # noqa: BLE001 单轮异常不终止重试
                logger.warning(f"后端日志：定时任务调度器锁重试接管失败（继续等待）: {e}")

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

        # 防滥用策略扫描（Phase 1 观察闭环，设计 §5.2）
        try:
            aa = settings.anti_abuse
            if aa.scanner_enabled:
                self._scheduler.add_job(
                    self._run_abuse_scan,
                    IntervalTrigger(seconds=aa.scanner_interval_seconds),
                    id="job_system_abuse_scan",
                    name="Anti-Abuse Policy Scan",
                    max_instances=1,
                    coalesce=True,
                )
                logger.info(
                    f"后端日志：已注册防滥用策略扫描任务 (interval={aa.scanner_interval_seconds}s)"
                )
        except Exception as e:
            logger.error(f"后端日志：注册防滥用策略扫描任务失败: {e}")

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

        # ===== storage/tmp 技能草稿清理（每日 03:45，保留 7 天）=====
        # SKILL_TMP_DIR：技能脚本（travel-quote/spec2quote 等）的草稿目录，
        # 交付件会 cp 到租户目录，草稿本身无引用，按 mtime 兜底清理
        try:
            self._scheduler.add_job(
                self._run_skill_tmp_cleanup,
                CronTrigger(hour=3, minute=45, timezone="Asia/Shanghai"),
                id="job_system_skill_tmp_cleanup",
                name="Skill Tmp Cleanup",
                max_instances=1,
                coalesce=True,
            )
            logger.info("后端日志：已注册技能草稿目录清理任务 (cron=03:45)")
        except Exception as e:
            logger.error(f"后端日志：注册技能草稿目录清理任务失败: {e}")

        # ===== 日志文件清理（每日 00:30，保留 15 天）=====
        # cleanup_old_logs 此前仅启动时执行一次，长期不重启的容器日志分片会持续累积，
        # 改由调度器每日兜底（清理逻辑多 worker 幂等，见 src/core/log_retention.py）
        try:
            self._scheduler.add_job(
                self._run_log_file_cleanup,
                CronTrigger(hour=0, minute=30, timezone="Asia/Shanghai"),
                id="job_system_log_file_cleanup",
                name="Log File Cleanup",
                max_instances=1,
                coalesce=True,
            )
            logger.info("后端日志：已注册日志文件清理任务 (cron=00:30)")
        except Exception as e:
            logger.error(f"后端日志：注册日志文件清理任务失败: {e}")

        # ===== log_error 数据库错误日志清理（每日 00:30，保留 15 天）=====
        # log_error 表按 timestamp 走 idx_log_error_timestamp 索引分批删除，避免长锁
        try:
            self._scheduler.add_job(
                self._run_log_error_cleanup,
                CronTrigger(hour=0, minute=30, timezone="Asia/Shanghai"),
                id="job_system_log_error_cleanup",
                name="DB Error Log Cleanup",
                max_instances=1,
                coalesce=True,
            )
            logger.info("后端日志：已注册 log_error 数据库错误日志清理任务 (cron=00:30)")
        except Exception as e:
            logger.error(f"后端日志：注册 log_error 数据库错误日志清理任务失败: {e}")

        # ===== log/temp 临时主题日志清理（每周六 04:50 全量清空）=====
        # tlog 文件名无日期，不命中 log_retention 的日期清理模式，按周全删兜底；
        # 调试规范要求 bug 修复后删除 tlog 调用，残留文件由本任务定期回收
        try:
            self._scheduler.add_job(
                self._run_temp_log_cleanup,
                CronTrigger(day_of_week="sat", hour=4, minute=50, timezone="Asia/Shanghai"),
                id="job_system_temp_log_cleanup",
                name="Temp Log Cleanup",
                max_instances=1,
                coalesce=True,
            )
            logger.info("后端日志：已注册 log/temp 临时日志清理任务 (cron=周六 04:50)")
        except Exception as e:
            logger.error(f"后端日志：注册 log/temp 临时日志清理任务失败: {e}")

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

        # ===== 已删除租户夜间清理（每日 04:30）=====
        # 1) status=deactivated 且 updated_at 超 tenant_purge retention 天数的租户 -> 物理删除核心数据
        # 2) 动态扫描所有含 tenant_id / to_tenant_id 列的表，分批删除孤儿数据（租户行已不存在）
        # 3) 清理 storage/tenants/ 下租户行已不存在的附件目录
        try:
            self._scheduler.add_job(
                self._run_tenant_purge,
                CronTrigger(hour=4, minute=30, timezone="Asia/Shanghai"),
                id="job_system_tenant_purge",
                name="Tenant Purge",
                max_instances=1,
                coalesce=True,
            )
            logger.info("后端日志：已注册已删除租户夜间清理任务 (cron=04:30)")
        except Exception as e:
            logger.error(f"后端日志：注册已删除租户夜间清理任务失败: {e}")

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

        # ===== Redis 夜间巡检（每日 00:30；REDIS_INSPECTION_ENABLED 门控，默认关）=====
        # 仅生产本地容器实例启用；测试环境为腾讯云托管 Redis（云监控覆盖），无需自巡检
        try:
            if settings.redis.enabled and settings.redis.inspection_enabled:
                self._scheduler.add_job(
                    self._run_redis_inspection,
                    CronTrigger(hour=0, minute=30, timezone="Asia/Shanghai"),
                    id="job_system_redis_inspection",
                    name="Redis Inspection",
                    max_instances=1,
                    coalesce=True,
                )
                logger.info("后端日志：已注册 Redis 夜间巡检任务 (cron=00:30)")
            else:
                logger.debug("后端日志：Redis 巡检未启用（REDIS_INSPECTION_ENABLED），跳过注册")
        except Exception as e:
            logger.error(f"后端日志：注册 Redis 巡检任务失败: {e}")

        # ===== 端侧会话任务决策 worker（C3；session_tasks.enabled + 任一启用场景描述器）=====
        # tick 为同步入口（内部自带事件循环跑模型调用），与 APScheduler 解耦可直接注入调用；
        # 适配器/决策钩子注册仅在调度器启动时执行（ensure_registered，本函数上方调用），
        # tick 不重复注册——进程内 registry 不会自发丢失（reset 仅测试场景使用）。
        # CR 阻断 11：决策 worker 与控制请求 worker 独立注册、独立记录启动失败——
        # 决策注册失败不得拖垮安全控制 worker（后者是阻断迁移的唯一消费者）。
        try:
            from src.session_tasks.config import get_session_tasks_config

            st_cfg = get_session_tasks_config()
            if st_cfg.enabled:
                if _ensure_any_session_scenario_registered():
                    from src.session_tasks.decisions import run_decision_tick

                    self._scheduler.add_job(
                        run_decision_tick,
                        IntervalTrigger(seconds=st_cfg.decision_tick_seconds),
                        id="job_system_session_task_decisions",
                        name="Session Task Decision Worker",
                        max_instances=1,
                        coalesce=True,
                    )
                    logger.info(
                        f"后端日志：已注册 session_tasks 决策 worker (tick={st_cfg.decision_tick_seconds}s, "
                        f"max_decisions_per_tenant={st_cfg.max_decisions_per_tenant})"
                    )
                else:
                    logger.debug("后端日志：session_tasks 未启用或无启用场景，跳过决策 worker 注册")
        except Exception as e:
            logger.error(f"后端日志：注册 session_tasks 决策 worker 失败: {e}")

        # ===== 控制请求处理器（B1.2，设计 §5.5.5；仅 session_tasks.enabled 门控，
        # 不依赖场景注册；独立 try 域——CR 阻断 11）=====
        try:
            from src.session_tasks.config import get_session_tasks_config as _st_cfg_for_control

            if _st_cfg_for_control().enabled:
                from src.session_tasks.control_requests import run_control_request_tick

                self._scheduler.add_job(
                    run_control_request_tick,
                    IntervalTrigger(seconds=5),
                    id="job_system_session_task_control_requests",
                    name="Session Task Control Request Processor",
                    max_instances=1,
                    coalesce=True,
                )
                logger.info("后端日志：已注册 session_tasks 控制请求处理器 (tick=5s)")
        except Exception as e:
            logger.error(f"后端日志：注册 session_tasks 控制请求处理器失败: {e}")

        # ===== BOSS 沟通日志投影（B2，设计 §5.6；boss_conversation.enabled 热读
        # 门控，默认关闭零注册；独立 try 域不拖垮其他 worker）=====
        try:
            from src.boss_conversation.config import scenario_enabled_gate as _boss_gate

            if _boss_gate():
                from src.boss_conversation.projection import run_projection_tick

                self._scheduler.add_job(
                    run_projection_tick,
                    IntervalTrigger(seconds=30),
                    id="job_system_boss_comm_log_projection",
                    name="Boss Comm Log Projection",
                    max_instances=1,
                    coalesce=True,
                )
                logger.info("后端日志：已注册 boss_conversation 沟通日志投影 (tick=30s)")
            else:
                logger.debug("后端日志：boss_conversation 未启用，跳过投影 job 注册")
        except Exception as e:
            logger.error(f"后端日志：注册 boss_conversation 投影 job 失败: {e}")

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

    def _run_abuse_scan(self):
        """防滥用策略扫描（APScheduler 回调，同步 DB 操作直接执行）"""
        try:
            from src.services.anti_abuse.scanner import run_abuse_scan_once
            stats = run_abuse_scan_once()
            if stats.get("events") or stats.get("notified"):
                logger.info(f"后端日志：防滥用策略扫描完成: {stats}")
        except Exception as e:
            logger.opt(exception=True).error(f"后端日志：防滥用策略扫描失败: {e}")

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

    # ===== 已删除租户夜间清理回调（同步）=====
    def _run_tenant_purge(self):
        """物理删除超期已删除租户 + 清理孤儿数据/附件目录（APScheduler 回调）。

        实现见 src/saas/services/tenant_purge.py；保留天数固定 7 天。
        """
        try:
            from src.saas.services.tenant_purge import (
                cleanup_orphan_storage_dirs,
                purge_expired_deleted_tenants,
                purge_orphan_data,
            )

            purged = purge_expired_deleted_tenants(days=7)
            orphans = purge_orphan_data()
            dirs = cleanup_orphan_storage_dirs()
            logger.info(
                f"后端日志：已删除租户夜间清理完成: purged_tenants={len(purged)}, "
                f"orphan_tables={len(orphans)}, removed_dirs={len(dirs)}"
            )
        except Exception as e:
            logger.opt(exception=True).error(f"后端日志：已删除租户夜间清理异常: {e}")

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

    # ===== storage/tmp 技能草稿清理回调（同步，mtime 7 天兜底）=====
    def _run_skill_tmp_cleanup(self):
        """清理 storage/tmp 下超过 7 天的技能草稿文件与工作目录（APScheduler 回调）。

        SKILL_TMP_DIR 由 skill_executor 注入给技能脚本做临时草稿；
        交付件经 cp_tool 复制到租户目录，草稿本身不被任何表/Redis 引用，
        按 mtime 兜底清理（与 skill_ws_cleanup 同策略：mtime 随最后写入更新）。
        """
        try:
            import shutil
            import time
            from pathlib import Path

            tmp_root = Path("storage/tmp")
            if not tmp_root.is_dir():
                return 0

            max_age_seconds = 7 * 24 * 3600  # 7 天
            now = time.time()
            cleaned = 0
            for entry in tmp_root.iterdir():
                if entry == tmp_root:
                    continue
                try:
                    # 目录 mtime 随最后写入更新；超过 7 天视为无活跃使用的残留
                    if now - entry.stat().st_mtime <= max_age_seconds:
                        continue
                    if entry.is_dir():
                        shutil.rmtree(entry, ignore_errors=True)
                    else:
                        entry.unlink()
                    cleaned += 1
                    logger.info(f"后端日志：清理过期技能草稿 {entry}")
                except Exception as e:
                    logger.warning(f"后端日志：清理技能草稿失败 {entry}: {e}")
            if cleaned > 0:
                logger.info(f"后端日志：storage/tmp 技能草稿清理完成，共清理 {cleaned} 项")
            return cleaned
        except Exception as e:
            logger.opt(exception=True).error(f"后端日志：storage/tmp 技能草稿清理任务异常: {e}")
            return 0

    # ===== 日志文件清理回调（同步）=====
    def _run_log_file_cleanup(self):
        """清理 log/ 下超过保留期（15 天）的日志分片文件（APScheduler 回调）。"""
        try:
            from src.core.log_retention import cleanup_old_logs

            deleted = cleanup_old_logs()
            if deleted:
                logger.info(f"后端日志：日志文件清理完成，删除 {deleted} 个过期分片")
            return deleted
        except Exception as e:
            logger.opt(exception=True).error(f"后端日志：日志文件清理任务异常: {e}")
            return 0

    # ===== log_error 数据库错误日志清理回调（同步，分批删除避免长锁）=====
    def _run_log_error_cleanup(self, retention_days: int = 15):
        """删除 log_error 表超过保留期（默认 15 天）的记录（APScheduler 回调）。

        分批删除（每批 1 万行）避免长事务长锁；平台管理员手动清理端点
        （src/api/admin_error_logs.py）保留 30 天口径不变。
        """
        from src.db.database import get_db_connection

        started_at = datetime.now()
        total_deleted = 0
        batch_size = 10000
        try:
            while True:
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        """
                        DELETE FROM log_error
                        WHERE id IN (
                            SELECT id FROM log_error
                            WHERE timestamp < NOW() - (%s * INTERVAL '1 day')
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
            if total_deleted:
                logger.info(
                    f"后端日志：log_error 错误日志清理完成，删除 {total_deleted} 行"
                    f"（保留 {retention_days} 天），耗时 {elapsed:.1f}s"
                )
            return total_deleted
        except Exception as e:
            logger.opt(exception=True).error(
                f"后端日志：log_error 错误日志清理失败（已删除 {total_deleted} 行）: {e}"
            )
            return total_deleted

    # ===== log/temp 临时主题日志清理回调（每周六全量清空）=====
    def _run_temp_log_cleanup(self):
        """清空 log/temp/ 下全部 tlog 临时日志（APScheduler 回调，每周六凌晨）。

        tlog 每次写入都是独立 open/append/close，删除后进行中的调试写入
        会自动重建文件继续记，不影响排查。
        """
        try:
            import shutil
            from pathlib import Path

            temp_root = Path("log/temp")
            if not temp_root.is_dir():
                return 0

            cleaned = 0
            for entry in temp_root.iterdir():
                try:
                    if entry.is_dir():
                        shutil.rmtree(entry, ignore_errors=True)
                    else:
                        entry.unlink()
                    cleaned += 1
                except Exception as e:
                    logger.warning(f"后端日志：清理临时日志失败 {entry}: {e}")
            if cleaned:
                logger.info(f"后端日志：log/temp 临时日志清理完成，共清理 {cleaned} 项")
            return cleaned
        except Exception as e:
            logger.opt(exception=True).error(f"后端日志：log/temp 临时日志清理任务异常: {e}")
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
                        # 不发超时通知：state=3 人工接待期机器人 send_msg 被微信拒收（95018），
                        # state=4 已结束后同样拒收，该文案无法送达；客户重新发消息会自动
                        # 进入新会话并走智能助手接待流程
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

    # ===== Redis 夜间巡检回调（同步）=====
    def _run_redis_inspection(self):
        """Redis 夜间巡检（APScheduler 回调）。巡检逻辑见 src/core/redis_inspection.py。"""
        try:
            from src.core.redis_inspection import run_redis_inspection
            run_redis_inspection()
        except Exception as e:
            logger.opt(exception=True).error(f"后端日志：Redis 夜间巡检任务异常: {e}")

    def shutdown(self):
        """优雅关闭"""
        if not self._running:
            return

        if self._scheduler:
            self._scheduler.shutdown(wait=True)
            self._scheduler = None
        self._running = False

        # 释放分布式锁
        if self._lock_value:
            try:
                redis_client.release_lock(_SCHEDULER_LOCK_KEY, self._lock_value)
            except Exception:
                pass
            self._lock_value = None

        logger.info("后端日志：定时任务调度器已关闭")


# 全局单例
scheduled_task_manager = ScheduledTaskManager()
