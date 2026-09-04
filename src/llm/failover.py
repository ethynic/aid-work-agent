"""
LLM Failover 模块

实现 Provider Chain + 自动故障转移：
- CircuitBreaker: 单 provider 的熔断器
- ProviderSlot: provider 槽位（KeyPool + CircuitBreaker）
- FailoverGateway: 带故障转移的 LLM 网关
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx
from loguru import logger

from .key_pool import KeyPool
from .llm_call_logger import generate_request_id, log_llm_invoke


# ---------------------------------------------------------------------------
# 自定义异常
# ---------------------------------------------------------------------------

class LLMAllProvidersFailedError(Exception):
    """所有 provider 都调用失败"""

    def __init__(self, errors: List[Dict[str, str]]):
        self.errors = errors
        details = "; ".join(f"[{e['provider']}] {e['error']}" for e in errors)
        super().__init__(f"所有 LLM Provider 均失败: {details}")


# 仅主 provider 槽位保留的 kwargs（各 provider 专属的思考控制参数）。
# 透传给备用槽会产生跨 provider 错配：qwen 静默忽略 thinking、zhipu GLM
# 对 thinking 字段 400、enable_thinking/reasoning_effort 语义互不兼容。
PRIMARY_ONLY_KWARGS = ("thinking", "enable_thinking", "reasoning_effort")


# ---------------------------------------------------------------------------
# 错误分类
# ---------------------------------------------------------------------------

def is_retryable_error(error: Exception) -> bool:
    """判断错误是否值得尝试其他 provider

    Provider 层（deepseek/qwen/zhipu）会将 httpx.HTTPStatusError 包成 RuntimeError，
    错误消息里包含 response body 但不含状态码。因此需要对 RuntimeError 做更宽泛的匹配。
    """
    if isinstance(error, asyncio.TimeoutError):
        return True
    if isinstance(error, httpx.ConnectError):
        return True
    if isinstance(error, httpx.HTTPStatusError):
        code = error.response.status_code
        return code >= 500 or code == 429
    if isinstance(error, (TimeoutError, ConnectionError)):
        return True
    if isinstance(error, RuntimeError):
        msg = str(error).lower()
        retryable_keywords = [
            "500", "502", "503", "504",        # 服务端错误
            "429",                              # 限流
            "timeout", "timed out",             # 超时
            "connection", "connect",            # 连接错误
            "invalid",                          # 无效 key（可能是主 provider 配错）
            "authentication", "unauthorized",   # 认证失败（key 错误）
            "forbidden",                        # 禁止访问
        ]
        if any(kw in msg for kw in retryable_keywords):
            return True
    return False


# ---------------------------------------------------------------------------
# CircuitBreaker
# ---------------------------------------------------------------------------

class CircuitBreaker:
    """单 provider 的熔断器

    状态机：closed → open → half_open → closed
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_timeout: float = 60,
        provider_name: str = "",
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.provider_name = provider_name

        self._state = "closed"
        self._failure_count = 0
        self._last_failure_time: float = 0
        self._lock = asyncio.Lock()

    @property
    def state(self) -> str:
        return self._state

    def record_success(self) -> None:
        """记录成功调用，重置状态为 closed"""
        self._failure_count = 0
        old_state = self._state
        self._state = "closed"
        if old_state in ("open", "half_open"):
            logger.info(f"[CircuitBreaker] {self.provider_name} 熔断恢复，重新启用")
            self._on_recovery()

    def record_failure(self) -> None:
        """记录失败调用"""
        self._failure_count += 1
        self._last_failure_time = time.monotonic()

        if self._state == "half_open":
            self._state = "open"
            logger.warning(
                f"[CircuitBreaker] {self.provider_name} 试探失败，继续熔断 "
                f"{self.recovery_timeout}s"
            )
            return

        if self._failure_count >= self.failure_threshold:
            self._state = "open"
            logger.warning(
                f"[CircuitBreaker] {self.provider_name} 连续失败 "
                f"{self._failure_count} 次，熔断 {self.recovery_timeout}s"
            )
            self._on_open()

    def can_attempt(self) -> bool:
        """是否允许尝试调用"""
        if self._state == "closed":
            return True
        if self._state == "open":
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= self.recovery_timeout:
                self._state = "half_open"
                logger.info(
                    f"[CircuitBreaker] {self.provider_name} 进入半开状态，尝试恢复"
                )
                return True
            return False
        # half_open
        return True

    def _on_open(self) -> None:
        """熔断触发回调（子类可覆盖以发送告警）"""
        pass

    def _on_recovery(self) -> None:
        """熔断恢复回调（子类可覆盖以发送告警）"""
        pass


# ---------------------------------------------------------------------------
# ProviderSlot
# ---------------------------------------------------------------------------

@dataclass
class ProviderSlot:
    """一个 provider 的完整槽位"""
    provider_name: str
    key_pool: KeyPool
    circuit_breaker: CircuitBreaker

    def is_available(self) -> bool:
        """熔断器允许 且 key_pool 已配置"""
        return self.circuit_breaker.can_attempt()


# ---------------------------------------------------------------------------
# FailoverGateway
# ---------------------------------------------------------------------------

def _build_provider(provider_name: str, api_key: str, model: Optional[str] = None) -> Any:
    """构建 Provider 实例（从 gateway 导入，避免循环引用）"""
    from .gateway import _build_provider as _gw_build
    return _gw_build(provider_name, api_key, model=model)


class FailoverGateway:
    """带故障转移的 LLM 网关"""

    def __init__(
        self,
        primary_name: str,
        fallback_names: Optional[List[str]] = None,
        failover_cfg=None,
        model_codes: Optional[Dict[str, str]] = None,
    ):
        from src.config.settings import settings

        self._settings = settings
        self._primary_name = primary_name
        self._fallback_names = fallback_names or []
        self._failover_cfg = failover_cfg or settings.llm.failover
        # 子智能体级别的 model_code 覆盖（failover 链上每个 provider 单独覆盖）
        self._model_codes: Dict[str, str] = model_codes or {}

        cb_cfg = self._failover_cfg.circuit_breaker
        self._slots: List[ProviderSlot] = []

        seen_providers = set()
        for name in [primary_name] + self._fallback_names:
            # 去重兜底：主 provider 与备用链同名（或备用链内部重复）时只保留首个，
            # 避免同一 provider 注册两个独立 KeyPool/熔断器（熔断状态不共享，白耗一次切换）
            if name in seen_providers:
                logger.warning(f"[Failover] Provider [{name}] 在链中重复，跳过重复槽位")
                continue
            seen_providers.add(name)

            cfg = self._get_provider_cfg(name)
            if cfg is None:
                logger.warning(f"[Failover] Provider [{name}] 未配置，跳过")
                continue

            keys = cfg.get_effective_keys()
            if not keys:
                logger.warning(f"[Failover] Provider [{name}] 未配置 API Key，跳过")
                continue

            cb = CircuitBreaker(
                failure_threshold=cb_cfg.failure_threshold,
                recovery_timeout=cb_cfg.recovery_timeout,
                provider_name=name,
            )

            slot = ProviderSlot(
                provider_name=name,
                key_pool=KeyPool(
                    keys=keys,
                    max_concurrent_per_key=cfg.max_concurrent_per_key,
                    queue_timeout=cfg.queue_timeout,
                ),
                circuit_breaker=cb,
            )

            # 绑定告警回调
            slot_name = name
            is_primary = name == primary_name
            cb._on_open = lambda s=slot_name, p=is_primary: asyncio.ensure_future(
                self._handle_circuit_open(s, p)
            )
            cb._on_recovery = lambda s=slot_name: asyncio.ensure_future(
                self._handle_circuit_recovery(s)
            )
            self._slots.append(slot)
            logger.info(f"[Failover] Provider [{name}] 已注册，Keys: {len(keys)}")

        if not self._slots:
            raise ValueError("没有可用的 LLM Provider（均未配置 API Key）")
        if len(self._slots) == 1:
            logger.warning("[Failover] 仅有 1 个可用 Provider，failover 无实际效果")

        # 告警去重：{provider_name: last_alert_time}
        self._alert_times: Dict[str, float] = {}

        # 最近一次 failover 事件
        self._last_failover: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    def _get_provider_cfg(self, name: str) -> Any:
        """获取 provider 配置对象"""
        mapping = {
            "qwen": self._settings.llm.qwen,
            "zhipu": self._settings.llm.zhipu,
            "deepseek": self._settings.llm.deepseek,
            "moonshot": self._settings.llm.moonshot,
        }
        return mapping.get(name)

    def _get_provider_chain(self) -> List[ProviderSlot]:
        """返回可用的 provider 链"""
        return [s for s in self._slots if s.is_available()]

    async def _call_slot(
        self, slot: ProviderSlot, fn_name: str, model_override: Optional[str] = None, **kwargs
    ) -> Any:
        """从 slot 的 key_pool 获取 key，构建 provider 后执行调用

        Args:
            model_override: 显式模型覆盖（仅对主 provider 槽位传入），优先于 _model_codes
        """
        async with slot.key_pool.acquire() as api_key:
            model = model_override or self._model_codes.get(slot.provider_name)
            provider = _build_provider(slot.provider_name, api_key, model=model)
            method = getattr(provider, fn_name)
            return await method(**kwargs)

    async def _stream_slot(
        self, slot: ProviderSlot, fn_name: str, model_override: Optional[str] = None, **kwargs
    ) -> AsyncGenerator[str, None]:
        """流式调用"""
        async with slot.key_pool.acquire() as api_key:
            model = model_override or self._model_codes.get(slot.provider_name)
            provider = _build_provider(slot.provider_name, api_key, model=model)
            method = getattr(provider, fn_name)
            async for chunk in method(**kwargs):
                yield chunk

    def _log_failover_event(
        self,
        request_id: str,
        from_provider: str,
        to_provider: str,
        reason: str,
        duration_ms: float,
    ):
        """记录 failover 事件到 JSONL 日志"""
        self._last_failover = {
            "from": from_provider,
            "to": to_provider,
            "reason": reason,
            "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        try:
            log_llm_invoke(
                request_id=request_id,
                provider=from_provider,
                model="",
                request_params={"type": "failover"},
                response_data={
                    "type": "failover",
                    "from_provider": from_provider,
                    "to_provider": to_provider,
                    "reason": reason,
                },
                duration_ms=duration_ms,
            )
        except Exception:
            pass

    async def _send_alert(self, level: str, title: str, content: str):
        """发送告警通知"""
        if not self._failover_cfg.alert.enabled:
            return
        try:
            from src.services.notification_service import (
                NotificationChannel,
                NotificationMessage,
                notification_service,
            )

            channel_map = {
                "webhook": NotificationChannel.WEBHOOK,
                "email": NotificationChannel.EMAIL,
            }
            channel = channel_map.get(
                self._failover_cfg.alert.channel, NotificationChannel.WEBHOOK
            )
            msg = NotificationMessage(
                title=title,
                content=content,
                urgency=level,
                recipient="",
                channel=channel,
                metadata={"source": "llm-failover"},
            )
            await notification_service.send(msg)
        except Exception as e:
            logger.error(f"[Failover] 告警发送失败: {e}")

    def _check_alert_cooldown(self, provider_name: str) -> bool:
        """检查告警冷却，返回 True 表示可以发送"""
        now = time.monotonic()
        last = self._alert_times.get(provider_name, 0)
        if now - last < self._failover_cfg.alert.cooldown:
            return False
        self._alert_times[provider_name] = now
        return True

    async def _handle_circuit_open(self, provider_name: str, is_primary: bool):
        """熔断触发时发送告警"""
        if not self._check_alert_cooldown(provider_name):
            return

        level = "high" if is_primary else "medium"
        title = f"[{level.upper()}] LLM 服务熔断 - {provider_name}"
        active_providers = [
            s.provider_name for s in self._slots
            if s.circuit_breaker.state == "closed"
        ]
        content = (
            f"LLM 提供者 {provider_name} 连续失败触发熔断。\n"
            f"当前可用 Provider: {', '.join(active_providers) or '无'}\n"
            f"系统{'仍可用' if active_providers else '不可用'}。"
        )
        await self._send_alert(level, title, content)

        if not active_providers:
            if self._check_alert_cooldown("__all__"):
                await self._send_alert(
                    "critical",
                    "[CRITICAL] 所有 LLM 服务不可用",
                    "所有 LLM Provider 均已熔断，系统无法响应任何 AI 请求。",
                )

    async def _handle_circuit_recovery(self, provider_name: str):
        """熔断恢复时发送通知"""
        if self._check_alert_cooldown(f"{provider_name}_recovery"):
            await self._send_alert(
                "low",
                f"[LOW] LLM 服务恢复 - {provider_name}",
                f"LLM 提供者 {provider_name} 已恢复正常。",
            )

    # ------------------------------------------------------------------
    # 核心调用逻辑
    # ------------------------------------------------------------------

    async def call_with_failover(
        self, fn_name: str, request_id: Optional[str] = None, **kwargs
    ) -> Any:
        """按优先级尝试各 provider（非流式）

        显式 model kwarg 仅作用于主 provider 槽位（调用方指定的模型是针对主 provider 的），
        且从 kwargs 中移除——否则各 provider 的 request_body.update(kwargs) 会把该模型串
        原样发给备用 provider，产生 qwen/deepseek-v4-flash 这类跨 provider 模型错配。
        备用槽位一律使用各自的 _model_codes 覆盖或 settings.llm.{provider}.model。
        思考控制参数（PRIMARY_ONLY_KWARGS）同样仅主槽保留。
        """
        if request_id is None:
            request_id = generate_request_id()

        explicit_model = kwargs.pop("model", None)
        # 思考控制参数仅主 provider 槽位保留（PRIMARY_ONLY_KWARGS），备用槽剥离走各自默认
        primary_thinking = {k: kwargs.pop(k) for k in PRIMARY_ONLY_KWARGS if k in kwargs}
        chain = self._get_provider_chain()
        errors: List[Dict[str, str]] = []

        for i, slot in enumerate(chain):
            start = time.monotonic()
            try:
                slot_kwargs = dict(kwargs)
                if slot.provider_name == self._primary_name:
                    slot_kwargs.update(primary_thinking)
                result = await self._call_slot(
                    slot, fn_name,
                    model_override=explicit_model if slot.provider_name == self._primary_name else None,
                    **slot_kwargs,
                )
                slot.circuit_breaker.record_success()
                return result
            except Exception as e:
                duration_ms = (time.monotonic() - start) * 1000
                error_msg = f"{type(e).__name__}: {e}"
                errors.append({
                    "provider": slot.provider_name,
                    "error": error_msg,
                })

                if not is_retryable_error(e):
                    logger.error(
                        f"[Failover] Provider [{slot.provider_name}] 不可重试错误: {e}"
                    )
                    raise

                slot.circuit_breaker.record_failure()
                logger.warning(
                    f"[Failover] {slot.provider_name} → 下一个, "
                    f"reason: {error_msg}, attempt {i + 1}/{len(chain)}"
                )

                # 记录 failover 事件
                next_provider = chain[i + 1].provider_name if i + 1 < len(chain) else "none"
                self._log_failover_event(
                    request_id, slot.provider_name, next_provider,
                    error_msg, duration_ms,
                )

        raise LLMAllProvidersFailedError(errors)

    async def stream_with_failover(
        self, fn_name: str, request_id: Optional[str] = None, **kwargs
    ) -> AsyncGenerator[str, None]:
        """流式调用 failover（仅连接阶段做 failover）

        显式 model kwarg 处理同 call_with_failover：仅作用于主 provider 槽位。
        """
        if request_id is None:
            request_id = generate_request_id()

        explicit_model = kwargs.pop("model", None)
        # 思考控制参数处理同 call_with_failover：仅主 provider 槽位保留
        primary_thinking = {k: kwargs.pop(k) for k in PRIMARY_ONLY_KWARGS if k in kwargs}
        chain = self._get_provider_chain()
        errors: List[Dict[str, str]] = []

        for i, slot in enumerate(chain):
            start = time.monotonic()
            try:
                slot_kwargs = dict(kwargs)
                if slot.provider_name == self._primary_name:
                    slot_kwargs.update(primary_thinking)
                # 尝试建立流连接并获取第一个 chunk
                stream = self._stream_slot(
                    slot, fn_name,
                    model_override=explicit_model if slot.provider_name == self._primary_name else None,
                    **slot_kwargs,
                )
                stream_iter = stream.__aiter__()
                first_chunk = await stream_iter.__anext__()

                slot.circuit_breaker.record_success()

                # 返回生成器：先 yield 第一个 chunk，再继续流
                yield first_chunk
                async for chunk in stream_iter:
                    yield chunk
                return

            except StopAsyncIteration:
                # 空流，视为成功
                slot.circuit_breaker.record_success()
                return
            except Exception as e:
                duration_ms = (time.monotonic() - start) * 1000
                error_msg = f"{type(e).__name__}: {e}"
                errors.append({
                    "provider": slot.provider_name,
                    "error": error_msg,
                })

                if not is_retryable_error(e):
                    logger.error(
                        f"[Failover] Provider [{slot.provider_name}] 流式不可重试错误: {e}"
                    )
                    raise

                slot.circuit_breaker.record_failure()
                logger.warning(
                    f"[Failover] {slot.provider_name} → 下一个 (stream), "
                    f"reason: {error_msg}, attempt {i + 1}/{len(chain)}"
                )

                next_provider = chain[i + 1].provider_name if i + 1 < len(chain) else "none"
                self._log_failover_event(
                    request_id, slot.provider_name, next_provider,
                    error_msg, duration_ms,
                )

        raise LLMAllProvidersFailedError(errors)

    # ------------------------------------------------------------------
    # 公共接口（与 LLMGateway 完全兼容）
    # ------------------------------------------------------------------

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 16384,
        **kwargs,
    ) -> Dict[str, Any]:
        return await self.call_with_failover(
            "chat",
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )

    async def stream_chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 16384,
        **kwargs,
    ) -> AsyncGenerator[str, None]:
        async for chunk in self.stream_with_failover(
            "stream_chat",
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        ):
            yield chunk

    async def chat_with_tools(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        tool_choice: str = "auto",
        system_prompt: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        if system_prompt:
            messages = [{"role": "system", "content": system_prompt}] + messages
        return await self.chat(
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            **kwargs,
        )

    # ------------------------------------------------------------------
    # 监控接口
    # ------------------------------------------------------------------

    def get_primary_name(self) -> str:
        return self._primary_name

    def get_model_name(self) -> str:
        """获取主 provider 的模型名称。

        优先返回子智能体配置的 model_code 覆盖，未配置时返回全局默认。
        """
        # 优先使用子智能体配置的 model_code 覆盖
        override = self._model_codes.get(self._primary_name)
        if override:
            return override
        mapping = {
            "qwen": self._settings.llm.qwen.model,
            "zhipu": self._settings.llm.zhipu.model,
            "deepseek": self._settings.llm.deepseek.model,
        }
        return mapping.get(self._primary_name, "unknown")

    def health_status(self) -> Dict[str, Any]:
        """返回所有 provider 的健康状态"""
        result: Dict[str, Any] = {
            "primary": None,
            "fallbacks": [],
            "last_failover": self._last_failover,
        }

        for i, slot in enumerate(self._slots):
            info = {
                "provider": slot.provider_name,
                "circuit_breaker": slot.circuit_breaker.state,
                "key_pool": slot.key_pool.stats(),
            }
            if i == 0:
                result["primary"] = info
            else:
                result["fallbacks"].append(info)

        return result
