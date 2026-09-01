#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第三方渠道 verbose 中间消息投递设施（Phase 3）

设计文档：docs/system/agent-intermediate-feedback-design.md §7/§9/§10/§11
开发计划：docs/plans/plan-agent-intermediate-feedback.md「Phase 3：第三方渠道可靠投递」

为什么独立于 src/channels/session.py：session.py 已 2100+ 行且职责是「持久化与
批量写入」；dispatcher 是带独立 asyncio 生命周期的后台发送组件（惰性 task、
容量 1 队列、超时收尾），与配置解析器、metadata 冻结一起形成自洽的 verbose
交付单元，单独成模块便于隔离测试，也避免 session.py 进一步膨胀。
ChannelFactory 实际位置在 src/saas/services/channel_factory.py，本模块只依赖
src/core/verbose_feedback 内核与 src/channels.base 契约，无反向依赖。

组件：
- ChannelVerboseDispatcher：owner 生命周期一个实例；callback 侧 submit() 只做
  校验 + put_nowait（容量 1，满/重复/晚到丢弃），绝不 await adapter；消费 task
  串行调用 ``send_verbose(event, delivery_id)``（由 make_send_verbose 构造，
  内部走 adapter.send_status_message），单次发送 5 秒超时；False/异常/超时只记
  结果不重试。close_and_drain() 在 final 持久化前调用：停止接收并等待在途发送
  真正完成，超时必须 cancel 并 await 实际发送 task 后才返回；cancel_and_await()
  供取消/merged 等异常路径。任何路径不留遗留 task。
- resolve_verbose_feedback_config：配置优先级纯函数（设计 §11）：
  全局 force_disabled > 请求级 > 渠道 config.verbose_feedback >
  旧 wecom_kf waiting_indicator 映射 > 全局 agent.verbose_feedback > 代码默认。
  （2026-09-01 产品决策：system watchdog 删除后，旧配置的 delay 已无运行时
  含义，仅保留 enabled/message 语义，见函数 docstring。）
- build_channel_verbose_metadata_entries：drain 后冻结 verboseMessages
  （按 eventId 去重，恰好五字段 + delivery，设计 §10）。
"""

import asyncio
from typing import Any, Awaitable, Callable, Dict, List, Optional

from loguru import logger

from src.channels.base import StatusDeliveryResult
from src.core.verbose_feedback import (
    VerboseFeedbackConfig,
    VerboseFeedbackState,
    default_feedback_config,
)

# send_verbose 闭包契约（make_send_verbose 构造）：
#   async (event: dict, delivery_id: str) -> StatusDeliveryResult
SendVerboseFn = Callable[[Dict[str, Any], str], Awaitable[StatusDeliveryResult]]

# 旧 waiting_indicator 的兼容默认话术（与 src/channels/wecom_kf/prompts.py 一致；
# 拷贝常量避免渠道模块反向依赖：本模块只读配置，不 import 渠道实现）。
# 注：旧配置的 delay_seconds 默认值常量已随 system watchdog 删除（delay 无运行时含义）。
_LEGACY_WAITING_DEFAULT_MESSAGE = "我正在处理您的问题，可能需要几分钟，请稍等下。"


# ============== 唯一投递 ID（设计 §9.4） ==============


def final_delivery_id(event_id: str) -> str:
    """final 出站投递 ID：``{event_id}:final``。"""
    return f"{event_id or 'event'}:final"


def verbose_delivery_id(event: Dict[str, Any]) -> str:
    """verbose 出站投递 ID：``{event_id}:verbose:1``（MVP 每轮最多一条，固定序号 1）。

    企微个人 RPA 的 outbox 幂等键由 request_id 派生；verbose 与 final 必须使用
    不同 delivery_id，否则后发消息会被 outbox 去重吞掉（设计 §3.3 陷阱 B）。
    """
    event_id = str((event or {}).get("eventId") or "verbose")
    return f"{event_id}:verbose:1"


# ============== 配置优先级解析器（设计 §11，纯函数） ==============


def resolve_verbose_feedback_config(
    request_override: Optional[VerboseFeedbackConfig] = None,
    channel_cfg: Optional[Dict[str, Any]] = None,
    legacy_waiting_indicator: Optional[Dict[str, Any]] = None,
    global_cfg: Optional[VerboseFeedbackConfig] = None,
) -> VerboseFeedbackConfig:
    """解析本轮 verbose 配置，优先级固定（设计 §11）：

    1. 全局 ``force_disabled=true``：kill switch，立即返回关闭配置，
       任何请求级/渠道级/旧 waiting_indicator 都不能覆盖；
    2. 请求级显式 ``request_override``（VerboseFeedbackConfig，测试/内部调用）；
    3. 渠道级 ``channel_cfg``（tenant config.verbose_feedback，键：
       enabled / fallback_message）；
    4. 旧 wecom_kf ``legacy_waiting_indicator`` 映射（键：enabled /
       delay_seconds / message）。2026-09-01 产品决策删除 system watchdog 后，
       delay 已无运行时含义，最终语义固定为：
       - ``enabled`` 缺省或 false → 视为该渠道级显式关闭；
       - ``delay_seconds``（或 ``initial_delay_seconds``）<= 0 → 显式关闭
         （沿用旧 ``_get_waiting_indicator_cfg`` 语义）；
       - delay 非法值（非数字/缺失）→ 不再回退默认启用/禁用，直接视为启用
         （delay 已不参与任何运行时行为）；
       - ``message`` 映射为 ``fallback_message``（watchdog 删除后仅作 policy
         文案违规时的降级模板），空白回退旧默认话术；
    5. 全局 ``global_cfg``（缺省读 settings.agent.verbose_feedback）；
    6. 代码默认（2026-09-01 起随全局模型默认启用；渠道/旧配置显式关闭仍可盖过）。

    渠道/旧配置只声明 enabled/message 子集，其余字段（delivery_timeout、
    max_per_turn 等）继承全局/默认值。
    """
    base = global_cfg if isinstance(global_cfg, VerboseFeedbackConfig) else default_feedback_config()

    # 1. 全局 kill switch：最高优先级，覆盖一切
    if base.force_disabled:
        return VerboseFeedbackConfig(
            enabled=False,
            force_disabled=True,
            max_per_turn=base.max_per_turn,
            max_text_chars=base.max_text_chars,
            delivery_timeout_seconds=base.delivery_timeout_seconds,
            fallback_message=base.fallback_message,
        )

    # 2. 请求级显式配置
    if isinstance(request_override, VerboseFeedbackConfig):
        return request_override

    def _explicit_off() -> VerboseFeedbackConfig:
        # 渠道/旧配置显式声明"不启用"（enabled=false 或 delay<=0）时，
        # 视为该渠道级的显式关闭：必须盖过全局默认开启，否则默认值改为
        # enabled=true 后，显式关闭的存量租户会被全局穿透（行为倒退）。
        return VerboseFeedbackConfig(
            enabled=False,
            force_disabled=False,
            max_per_turn=base.max_per_turn,
            max_text_chars=base.max_text_chars,
            delivery_timeout_seconds=base.delivery_timeout_seconds,
            fallback_message=base.fallback_message,
        )

    # 3/4. 渠道级、旧 waiting_indicator
    for source in (channel_cfg, legacy_waiting_indicator):
        if not isinstance(source, dict) or not source:
            continue
        enabled = bool(source.get("enabled", False))
        if not enabled:
            # 与旧 _get_waiting_indicator_cfg 语义一致：未勾选 enabled 不启用
            return _explicit_off()
        # delay 仅保留"<=0 视为显式关闭"的兼容判断；非法值视为启用（无运行时含义）
        raw_delay = source.get("initial_delay_seconds")
        if raw_delay is None:
            raw_delay = source.get("delay_seconds")
        try:
            if float(raw_delay) <= 0:
                return _explicit_off()
        except (TypeError, ValueError):
            pass
        message = source.get("fallback_message")
        if message is None:
            message = source.get("message")
        if not (isinstance(message, str) and message.strip()):
            message = _LEGACY_WAITING_DEFAULT_MESSAGE
        return VerboseFeedbackConfig(
            enabled=True,
            force_disabled=False,
            max_per_turn=base.max_per_turn,
            max_text_chars=base.max_text_chars,
            delivery_timeout_seconds=base.delivery_timeout_seconds,
            fallback_message=message,
        )

    # 5/6. 全局 / 代码默认
    return base


# ============== metadata 冻结（设计 §10） ==============

# metadata.verboseMessages 条目恰好六字段（协议五字段 + delivery），与 Web 端
# 契约（TestWebVerboseMetadataDeliveryContract）同构；原因等可观测信息走日志，
# 不进 metadata，避免破坏历史消息结构。
_VERBOSE_ENTRY_FIELDS = ("eventId", "data", "source", "timestamp", "delivery")


def build_channel_verbose_metadata_entries(
    events: Optional[List[Dict[str, Any]]],
    dispatcher: Optional["ChannelVerboseDispatcher"] = None,
) -> List[Dict[str, Any]]:
    """把本轮 verbose 事件冻结为 assistant metadata.verboseMessages 条目。

    - 按 eventId 去重保留首条（设计 §10 合并规则 3）；
    - delivery 取 dispatcher 冻结结果：仅 ``sent`` 表示 adapter 判定真发送成功；
      failed/timeout/suppressed_* / 未尝试（suppressed_unsupported）如实记录；
    - 本轮无 verbose 时返回空列表（调用方保持 metadata 无该键，历史结构不变）。
    """
    entries: List[Dict[str, Any]] = []
    seen = set()
    for event in events or []:
        if not isinstance(event, dict):
            continue
        event_id = event.get("eventId")
        if not event_id or event_id in seen:
            continue
        seen.add(event_id)
        entries.append({
            "eventId": event_id,
            "data": event.get("data", ""),
            "source": event.get("source", ""),
            "timestamp": event.get("timestamp", 0),
            "delivery": "sent",
        })
    if entries and dispatcher is not None:
        outcome = dispatcher.outcome or "suppressed_unsupported"
        entries[0]["delivery"] = outcome
    for entry in entries:
        assert set(entry.keys()) == set(_VERBOSE_ENTRY_FIELDS)
    return entries


# ============== dispatcher ==============


class ChannelVerboseDispatcher:
    """owner 生命周期的 verbose 投递调度器（设计 §9.2 确定性算法）。

    生命周期：process_and_persist（owner）创建一次；惰性启动消费 task（首个
    verbose 事件才建）；cancel/merge 重跑 attempt 复用同一实例与同一
    VerboseFeedbackState，已 emitted/sent 状态不重置；merged follower 不进入
    owner processor，因此永远不会创建 task。

    与状态机联动：submit 只接受 ``state.event`` 本体事件；投递结果经 _freeze
    一次性写入 ``outcome``，冻结后晚到事件不再改写。
    """

    def __init__(
        self,
        *,
        send_verbose: SendVerboseFn,
        state: VerboseFeedbackState,
        timeout_seconds: float = 5.0,
    ):
        self._send_verbose = send_verbose
        self._state = state
        self._timeout_seconds = max(0.05, float(timeout_seconds))
        # 容量 1（设计 §9.2 步骤 1）：重复事件在 submit 侧即被丢弃
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=1)
        self._task: Optional[asyncio.Task] = None
        self._accepting = True
        self._outcome: Optional[str] = None
        self._outcome_reason = ""
        self._sent_event_id: Optional[str] = None
        self._attempts = 0
        self._last_drop_reason = ""

    # ---------- callback 侧（绝不 await adapter） ----------

    @property
    def outcome(self) -> Optional[str]:
        """冻结后的投递结果：sent/failed/timeout/suppressed_*；未冻结为 None。"""
        return self._outcome

    @property
    def outcome_reason(self) -> str:
        return self._outcome_reason

    @property
    def sent_event_id(self) -> Optional[str]:
        return self._sent_event_id

    @property
    def attempts(self) -> int:
        return self._attempts

    @property
    def worker_task(self) -> Optional[asyncio.Task]:
        return self._task

    def submit(self, event: Dict[str, Any]) -> bool:
        """Agent callback 侧入口：只校验 + put_nowait，立即返回（设计 §9.2 步骤 2）。

        丢弃场景（返回 False，记录原因）：已停止接收 / 结果已冻结 / 非本轮
        state 注册的本体事件（晚到重复或伪造）/ response 已开始 / 已关闭 /
        队列满（上一条尚未投递）。
        """
        if not self._accepting:
            self._last_drop_reason = "not_accepting"
            return False
        if self._outcome is not None:
            self._last_drop_reason = "outcome_frozen"
            return False
        if event is not self._state.event:
            self._last_drop_reason = "not_registered_event"
            return False
        if self._state.response_started:
            self._last_drop_reason = "response_started"
            return False
        if self._state.closed:
            self._last_drop_reason = "state_closed"
            return False
        self._ensure_worker()
        try:
            self._queue.put_nowait(event)
            return True
        except asyncio.QueueFull:
            self._last_drop_reason = "queue_full"
            logger.warning(
                "[VERBOSE] dispatcher queue full，丢弃重复/积压事件: "
                f"eventId={event.get('eventId')}"
            )
            return False

    def _ensure_worker(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(
                self._run(), name="channel-verbose-dispatcher"
            )

    # ---------- 消费循环（串行发送） ----------

    async def _run(self) -> None:
        while True:
            if self._queue.empty() and not self._accepting:
                return
            event = await self._queue.get()
            if not isinstance(event, dict):
                return
            await self._deliver(event)
            if self._outcome is not None:
                # 结果已冻结（每轮最多一条，MVP）：立即退出，避免 worker 停在
                # queue.get() 上导致 close_and_drain 空等整个超时窗口、final 被
                # 无谓延迟（CR 复核修复：成功投递后 drain 实测空等 timeout+1s）。
                return

    async def _deliver(self, event: Dict[str, Any]) -> None:
        if self._outcome is not None:
            return
        self._attempts += 1
        delivery_id = verbose_delivery_id(event)
        try:
            result = await asyncio.wait_for(
                self._send_verbose(event, delivery_id),
                timeout=self._timeout_seconds,
            )
        except asyncio.TimeoutError:
            self._freeze("timeout", f"send exceeded {self._timeout_seconds:.1f}s")
            return
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - best-effort：异常只记结果不重试
            self._freeze("failed", f"send_verbose exception: {e}")
            return
        if isinstance(result, StatusDeliveryResult):
            if result.is_sent:
                self._freeze("sent", result.reason, event_id=event.get("eventId"))
            else:
                self._freeze(result.status, result.reason)
            return
        # 防御：send_verbose 返回 bool（不规范实现）时按真值收敛
        self._freeze(
            "sent" if result else "failed",
            "legacy bool return" if result else "adapter returned falsy",
            event_id=event.get("eventId") if result else None,
        )

    def _freeze(
        self,
        status: str,
        reason: str = "",
        event_id: Optional[str] = None,
    ) -> None:
        """一次性冻结投递结果；冻结后晚到事件/重放不再改写（设计 §9.2 步骤 8）。"""
        if self._outcome is not None:
            return
        self._outcome = status
        self._outcome_reason = reason or ""
        if status == "sent":
            self._sent_event_id = event_id
        logger.info(
            f"[VERBOSE] dispatcher outcome={status} reason={reason or '-'} "
            f"eventId={event_id or '-'} attempts={self._attempts}"
        )

    # ---------- 收尾 ----------

    async def close_and_drain(self, timeout: Optional[float] = None) -> None:
        """final 持久化前调用（设计 §9.2 步骤 5）：停止接收，等待在途发送完成。

        超时必须 cancel 并 await 实际发送 task 后才返回——不能只停止等待而留下
        晚到发送与 DB batch 竞态。
        """
        self._accepting = False
        task = self._task
        if task is None or task.done():
            self._task = None
            if self._outcome is None:
                self._freeze(
                    "suppressed_unsupported",
                    self._last_drop_reason or "no delivery attempted",
                )
            return
        wait = float(timeout) if timeout is not None else self._timeout_seconds + 1.0
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=max(0.0, wait))
        except asyncio.TimeoutError:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[VERBOSE] dispatcher worker cleanup error: {e}")
            if self._outcome is None:
                self._freeze("failed", "drain timed out before delivery")
        except asyncio.CancelledError:
            # drain 自身被取消：仍需收尾 worker，不留遗留 task
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            raise
        finally:
            self._task = None
        if self._outcome is None:
            self._freeze(
                "suppressed_unsupported",
                self._last_drop_reason or "closed without delivery",
            )

    async def cancel_and_await(self) -> None:
        """取消 / merged / 异常路径收尾：停止接收并 cancel+await worker，不留 task。"""
        self._accepting = False
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[VERBOSE] dispatcher worker cancel cleanup error: {e}")
        if self._outcome is None:
            self._freeze("failed", "cancelled before delivery")
