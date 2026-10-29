"""定时任务调度循环（Runner 化 v2，设计 §4）。

单循环三步，无进程状态（函数可从任意进程调用，多实例由 DB 行锁与
occurrence_key 条件保证安全）：
① 到期 pending 重提（含首次提交与退避重试，快照原样重建）；
② 恢复驱动（interrupted runner 的 resume 控制，回合键 + 控制账本计数）；
③ 认领新 due（claim_due 原子认领即产生 pending，提交统一走 ①路径）。

提交结果按设计 §4.1 闭合表映射（全部用户可见，无静默路径）：确定性拒绝
→ pause_for_occurrence + task.paused 通知；网络未知/5xx → 退避重试，不当
作确定未接单。通知只在 pause 落库成功（返回 True）时发布——迟到拒绝不发
通知（设计 §4.1，Phase 1 CR 修正）。
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

from loguru import logger

from src.scheduler.db import ScheduledTaskDB
from src.scheduler.notify import publish_task_paused_in_tx

# 非交互指令（设计 §5.3）：认领时随快照固化、提交原样重建，不重新生成；
# Phase 2 已实现根/子执行继承与工具过滤，本文件只负责内容。
NON_INTERACTIVE_AUGMENTATION = (
    "本次为定时自动执行，无法向用户提问；缺少信息时按任务定义中约定的取数"
    "方式与默认行为自主完成；不得请求用户补充。"
)

# 调度侧服务身份（config.yaml agent_runner.peers.scheduler，sources=['scheduled']；
# token 复用同一 .env 的 AGENT_RUNNER_WEB_SERVICE_TOKEN）。
SCHEDULER_SERVICE_ID = "scheduler"

# pause_reason → 用户可见 reason_label（task.paused 通知正文用）。
PAUSE_REASON_LABELS = {
    "credit_blocked": "积分额度不足",
    "profile_forbidden": "数字员工不可用",
    "tenant_unavailable": "租户不可用或已过期",
    "definition_invalid": "任务定义校验未通过",
    "submit_conflict": "提交冲突（幂等键命中不同内容）",
    "submit_rejected": "提交被拒绝",
    "interrupted": "执行中断且自动恢复失败",
}

# 提交/控制确定性错误码 → pause_reason（设计 §4.1 闭合表 + §4.6 受理前拒绝）。
_PAUSE_REASON_BY_CODE = {
    "CREDIT_BLOCKED": "credit_blocked",
    "PROFILE_FORBIDDEN": "profile_forbidden",
    "TENANT_FORBIDDEN": "tenant_unavailable",
    "TENANT_UNAVAILABLE": "tenant_unavailable",
    "TENANT_EXPIRED": "tenant_unavailable",
    "INVALID_REQUEST": "definition_invalid",
}


@dataclass(frozen=True)
class DispatcherConfig:
    """调度循环配置（config.yaml scheduler 节，字段名与设计 §12 一致）。"""

    poll_seconds: float = 15.0
    submit_retry_seconds: int = 60
    recovery_max_attempts: int = 3
    claim_batch_size: int = 20
    pending_batch_size: int = 50
    resume_batch_size: int = 10

    @classmethod
    def from_settings(cls) -> "DispatcherConfig":
        from src.config.settings import settings

        # scheduler 未声明为 Settings 字段，config.yaml 该节经 _AttrDict 兜底
        # 暴露（属性访问）；settings.scheduler 缺失时全取默认值。
        section = getattr(settings, "scheduler", None)

        def _get(name, default):
            value = getattr(section, name, default) if section is not None else default
            return default if value is None else value

        return cls(
            poll_seconds=float(_get("poll_seconds", cls.poll_seconds)),
            submit_retry_seconds=int(_get("submit_retry_seconds", cls.submit_retry_seconds)),
            recovery_max_attempts=int(_get("recovery_max_attempts", cls.recovery_max_attempts)),
        )


class SchedulerRunnerClient:
    """RunnerServiceClient 薄封装：服务身份固定为 scheduler。

    RunnerServiceClient 的服务头取自 config.web_service_id（web 网关为
    'web'）；调度提交与恢复控制必须是 scheduler 身份（设计 §5.2——web 网关
    服务身份被禁止代表 scheduled 行操作）。不改既有 client 语义，仅以只读
    配置视图替换服务身份字段；api_url/凭据与网关同源。
    """

    def __init__(self, config=None, *, token=None):
        from src.services.agent_runner.web_client import RunnerServiceClient

        if config is None:
            from src.config.settings import settings
            config = settings.agent_runner

        class _SchedulerServiceView:
            """仅暴露 RunnerServiceClient.request 用到的两个字段。"""

            def __init__(self, source):
                self.api_url = source.api_url
                self.web_service_id = SCHEDULER_SERVICE_ID

        self._client = RunnerServiceClient(_SchedulerServiceView(config), token=token)

    async def request(self, method, path, *, authorization, body=None, params=None):
        return await self._client.request(method, path, authorization=authorization,
                                          body=body, params=params)


def _is_transient(error: Exception) -> bool:
    """网络未知/服务不可用类：不当作确定性结果（设计 §4.1/§4.6 退避重试）。"""
    from src.services.agent_runner.contracts import RunnerError

    if not isinstance(error, RunnerError):
        return True
    if error.code in ("RUNNER_SERVICE_UNAVAILABLE", "RUNNER_GATEWAY_NOT_CONFIGURED",
                      "RUNNER_SERVICE_INVALID_RESPONSE", "RUNNER_SERVICE_ERROR",
                      "RUNNER_SERVICE_REDIRECT_FORBIDDEN"):
        return True
    return error.status is not None and error.status >= 500


def _pause_reason_for(error) -> Optional[str]:
    """确定性错误码 → pause_reason；未列举确定性 4xx 归入 submit_rejected。"""
    code = getattr(error, "code", "") or ""
    if code.startswith("IDEMPOTENCY"):
        return "submit_conflict"
    reason = _PAUSE_REASON_BY_CODE.get(code)
    if reason is not None:
        return reason
    if getattr(error, "status", 500) < 500:
        return "submit_rejected"
    return None


class ScheduledTaskDispatcher:
    """到期扫描 → 认领/重提 → Runner 提交 → 结果映射的单循环调度器。"""

    def __init__(self, client=None, config: DispatcherConfig = None,
                 db=ScheduledTaskDB):
        self.config = config or DispatcherConfig.from_settings()
        self._client = client or SchedulerRunnerClient()
        self._db = db
        self._stop = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

    # ==================== 生命周期 ====================

    def start(self):
        """启动循环（background_runner 模式：失败不影响宿主进程其他任务）。"""
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self.run_forever())

    async def stop(self):
        self._stop.set()
        if self._task is not None:
            task, self._task = self._task, None
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=30)
            except asyncio.TimeoutError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def run_forever(self):
        logger.info(f"后端日志：定时任务调度循环启动 poll_seconds={self.config.poll_seconds}")
        while not self._stop.is_set():
            try:
                await self.run_once()
            except Exception as e:
                # 单轮异常不终止循环（坏任务已在各步内部隔离，此处兜底）。
                logger.opt(exception=True).error(f"后端日志：定时任务调度轮次异常 {e}")
            try:
                await asyncio.wait_for(self._stop.wait(),
                                       timeout=self.config.poll_seconds)
            except asyncio.TimeoutError:
                pass
        logger.info("后端日志：定时任务调度循环停止")

    async def run_once(self):
        """单轮：①到期 pending 重提 ②恢复驱动 ③认领新 due（认领成功产生的
        pending 由本轮③内立即提交，失败走退避进入下轮①）。

        同步 DAL 经 asyncio.to_thread 执行（评审修正：DB/连接池变慢时不得
        阻塞共享后台事件循环的其他消费者）；HTTP 保持异步。"""
        now = datetime.now()
        pending_rows = await asyncio.to_thread(
            self._db.list_pending, now, self.config.pending_batch_size)
        for row in pending_rows:
            try:
                await self._submit_pending(row)
            except Exception as e:
                logger.opt(exception=True).error(
                    f"后端日志：待提交任务处理异常 task_id={row.get('task_id')} {e}")
        await self._resume_interrupted()
        await self._claim_and_hold(now)

    # ==================== 提交路径（设计 §4.1） ====================

    @staticmethod
    def _build_submit_body(task_row: Dict[str, Any]) -> Dict[str, Any]:
        """从 pending_snapshot 原样重建 RunnerSubmit 字典（重试不重新生成）。

        attachments 为 JSONB 数组直接透传；client_request_id =
        '{task_id}:{occurrence_key}'（响应丢失同键重试返回原 runner）；
        request_data.scheduled.trigger 取 pending_trigger（manual 槽同路径）。"""
        snapshot = task_row.get("pending_snapshot") or {}
        return {
            "source": "scheduled",
            "session": {"kind": "web", "session_id": task_row["session_id"]},
            "client_request_id":
                f"{task_row['task_id']}:{task_row['pending_occurrence_key']}",
            "text": snapshot.get("text") or "",
            "attachments": snapshot.get("attachments") or [],
            "profile_id": snapshot.get("profile_id") or "main",
            "instance_id": snapshot.get("instance_id"),
            "prompt_augmentations": snapshot.get("prompt_augmentations") or [],
            "request_data": {"scheduled": {
                "task_id": task_row["task_id"],
                "trigger": task_row.get("pending_trigger") or "scheduled",
            }},
        }

    async def _submit_pending(self, task_row: Dict[str, Any]) -> None:
        """提交单个 pending 任务并按闭合表映射结果（全部用户可见）。"""
        task_id = task_row["task_id"]
        key = task_row["pending_occurrence_key"]
        body = self._build_submit_body(task_row)
        try:
            await self._client.request("POST", "/v1/runners",
                                       authorization=None, body=body)
        except Exception as error:
            await asyncio.to_thread(self._handle_submit_error, task_row, error)
            return
        # 202 成功（新建或幂等命中）：清 pending（带 occurrence_key 条件，
        # 迟到成功不得清掉他人刚写入的新 pending）。
        await asyncio.to_thread(self._db.clear_pending, task_id, key)
        logger.info(f"后端日志：定时任务提交成功 task_id={task_id}, "
                    f"occurrence_key={key}")

    def _handle_submit_error(self, task_row: Dict[str, Any], error: Exception) -> None:
        task_id = task_row["task_id"]
        key = task_row["pending_occurrence_key"]
        reason = _pause_reason_for(error) if not _is_transient(error) else None
        if reason is None:
            # 5xx/网络未知（含超时）：不当作确定未接单，退避重试。
            self._db.backoff_pending(task_id, key, self.config.submit_retry_seconds)
            logger.warning(f"后端日志：定时任务提交暂时失败，退避重试 "
                           f"task_id={task_id}, occurrence_key={key}, "
                           f"error={getattr(error, 'code', type(error).__name__)}")
            return
        # 确定性拒绝：自动暂停必须走 pause_for_occurrence（occurrence+active
        # 条件）——返回 False 为迟到拒绝（用户已暂停/恢复出新 pending），
        # 不得覆盖状态、不得发布通知。pause 与通知账本同一事务（评审修正：
        # 分两事务时进程在两步之间退出会永久丢通知）。
        if not self._db.pause_for_occurrence_with_notify(
                task_id, key, reason,
                PAUSE_REASON_LABELS.get(reason, reason),
                occurrence_notify_key=key):
            # 迟到拒绝（任务已非 active，或 pending 已被替换）：
            # ①不覆盖状态、不发布通知；②按 key 条件清理残留槽——once 定时
            # 执行先完成置 completed 后，尚未受理的 manual pending 会被接单
            # 守卫永久拒绝且既不清理也不退避（评审 P2 复现），条件清理使
            # key 已换的新 pending 不受影响（clear 带 occurrence_key 条件）。
            stale_cleared = self._db.clear_pending(task_id, key)
            logger.info(f"后端日志：迟到的提交拒绝已忽略 task_id={task_id}, "
                        f"occurrence_key={key}, reason={reason}, "
                        f"stale_pending_cleared={stale_cleared}")
            return
        if reason in ("submit_conflict", "submit_rejected"):
            # 不得静默：409 幂等冲突与未列举 4xx 记 ERROR（设计 §4.1 表）。
            logger.opt(exception=error).error(
                f"后端日志：定时任务提交被确定性拒绝 task_id={task_id}, "
                f"occurrence_key={key}, reason={reason}")

    # ==================== 认领路径（设计 §4.1/§4.2） ====================

    async def _claim_and_hold(self, now: datetime) -> None:
        """扫描到期任务逐个认领（认领即产生 pending），成功后立即提交。"""
        due_rows = await asyncio.to_thread(
            self._db.list_due, now, self.config.claim_batch_size)
        for row in due_rows:
            task_id = row["task_id"]
            try:
                claimed = await asyncio.to_thread(
                    self._db.claim_due,
                    task_id, prompt_augmentations=[NON_INTERACTIVE_AUGMENTATION])
                if claimed is None:
                    continue  # 已被其他实例认领/已暂停/once 过宽限作废
                await self._submit_pending(claimed)
            except Exception as e:
                # 单任务异常隔离：一条坏任务不中断整轮（设计 §4 循环韧性）。
                logger.opt(exception=True).error(
                    f"后端日志：到期任务认领/提交异常 task_id={task_id} {e}")

    # ==================== 恢复驱动（设计 §4.6） ====================

    @staticmethod
    def _resume_control_key(runner_id: str, attempt: int, n: int) -> str:
        """稳定控制请求键：attempt 区分中断回合（恢复成功后再次中断必是新
        attempt，不撞已 consumed 旧控制），n 为本回合已 rejected 计数
        （账本持久事实，重启不归零；同回合重试永远同键幂等）。"""
        return f"sched-resume:{runner_id}:{attempt}:{n}"

    async def _resume_interrupted(self) -> None:
        rows = await asyncio.to_thread(
            self._db.list_interrupted, self.config.resume_batch_size)
        for row in rows:
            try:
                await self._resume_one(row)
            except Exception as e:
                logger.opt(exception=True).error(
                    f"后端日志：恢复驱动单任务异常 task_id={row.get('task_id')} {e}")

    async def _resume_one(self, row: Dict[str, Any]) -> None:
        task_id, runner_id = row["task_id"], row["runner_id"]
        attempt = int(row.get("attempt") or 0)
        # 预算与幂等键序分离（评审 P1）：
        # - 预算（since 过滤的 rejected 数）：用户 resume 后从 0 重新计，
        #   恢复耗尽的任务获得新一轮有界尝试；
        # - 键序（总 rejected 数，不过滤）：永不回退——用户恢复后新提交的
        #   键必然落在旧 rejected 键之外；若用重置后的计数做键，会命中旧
        #   rejected 控制的同键幂等分支（返回旧控制、不创建新控制），
        #   恢复永久卡死（评审沿 ControlRepository 幂等分支复现）。
        n_budget = await asyncio.to_thread(
            self._db.count_rejected_resumes, runner_id, attempt,
            row.get("recovery_reset_at"))
        if n_budget >= self.config.recovery_max_attempts:
            # 尝试耗尽：任务 paused(interrupted) + 通知——pause 与通知同事务，
            # 且带回合保护：事务内复核 runner 仍处于本扫描所见 (runner_id,
            # attempt) 中断态（评审 P1：扫描结果过期时旧决定不得覆盖新状态、
            # 不产生通知）。
            paused = await asyncio.to_thread(
                self._db.pause_interrupted_with_notify,
                task_id, runner_id, attempt,
                PAUSE_REASON_LABELS.get("interrupted", "interrupted"))
            if paused:
                logger.error(f"后端日志：定时任务恢复尝试耗尽已暂停 task_id={task_id}, "
                             f"runner_id={runner_id}, attempt={attempt}")
            return
        n_sequence = await asyncio.to_thread(
            self._db.count_rejected_resumes, runner_id, attempt)
        key = self._resume_control_key(runner_id, attempt, n_sequence)
        body = {"action": "resume", "client_request_id": key,
                "answer": "", "attachments": []}  # 无补充输入（矩阵未放行）
        try:
            await self._client.request(
                "POST", f"/v1/runners/{runner_id}/controls",
                authorization=None, body=body)
        except Exception as error:
            await self._handle_resume_error(row, error)
            return
        # 受理成功（202，含幂等命中既有控制）：等待 worker 领取消费，本回合结束。
        logger.info(f"后端日志：定时任务恢复控制已受理 task_id={task_id}, "
                    f"runner_id={runner_id}, attempt={attempt}, seq={n_sequence}")

    async def _handle_resume_error(self, row: Dict[str, Any], error: Exception) -> None:
        task_id, runner_id = row["task_id"], row["runner_id"]
        attempt = int(row.get("attempt") or 0)
        code = getattr(error, "code", "") or ""
        if _is_transient(error) or code == "RUNNER_RESUME_PENDING":
            # 不消耗次数：网络未知同键下轮重试（带退避）；RESUME_PENDING 是
            # 已有受理待消费的控制（等待 worker 领取），不是失败。退避为
            # 任务级 recovery_retry_at（列表过滤即跳过，不动 runner 状态）。
            await asyncio.to_thread(self._db.defer_recovery,
                                    task_id, self.config.submit_retry_seconds)
            return
        if code in ("RUNNER_NOT_PARKED", "RUNNER_NOT_FOUND", "CONTROL_EXECUTION_CLOSED"):
            # runner 已离开 interrupted（自行恢复/终态/被取消）或已不存在：
            # 状态自解，下轮 list_interrupted 不再返回该行，按暂时等待处理。
            logger.info(f"后端日志：恢复控制遇状态自解错误，跳过 task_id={task_id}, "
                        f"runner_id={runner_id}, code={code}")
            return
        # 受理前确定性拒绝（控制记录创建之前校验，无 rejected 落账，不等待、
        # 不无限重复提交）：402/403 类按原因暂停，其余 paused(interrupted)。
        # 回合保护 + 通知同事务（同耗尽路径）。
        reason = _PAUSE_REASON_BY_CODE.get(code, "interrupted")
        paused = await asyncio.to_thread(
            self._db.pause_interrupted_with_notify,
            task_id, runner_id, attempt,
            PAUSE_REASON_LABELS.get(reason, reason),
            pause_reason=reason)
        if paused:
            logger.opt(exception=error).error(
                f"后端日志：定时任务恢复控制被确定性拒绝 task_id={task_id}, "
                f"runner_id={runner_id}, reason={reason}")
