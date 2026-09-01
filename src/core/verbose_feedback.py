#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Agent 用户可见中间消息（verbose）统一反馈内核（Phase 1）

设计文档：docs/system/agent-intermediate-feedback-design.md
（§4 事件协议 / §5 唯一解析契约 / §6 内容安全与上下文隔离 / §7 每轮状态 /
  §11 配置 / §12 可观测性）
开发计划：docs/plans/plan-agent-intermediate-feedback.md「Phase 1：统一反馈内核」

本模块是 verbose 的唯一策略解析与事件包装入口，仅供编排层（Agent 主循环 /
process_message_sync / Web 包装入口）调用。全部设施：

- 绝不进入 LLM 上下文：verbose 事件只写事件流，不执行 messages.append；
- 绝不暴露给 LLM：策略来源（Skill metadata.user_feedback / tool.get_user_feedback）
  均为服务端数据，不渲染进 system prompt / tool schema；
- 绝不挂在共享 Agent 实例上：VerboseFeedbackState 由调用方显式创建并传递
  （owner 生命周期一次，所有 cancel/merge 重跑 attempt 复用）；
- best-effort：任何 verbose 失败都不改变工具执行与最终回复。

冻结契约（tests/unit/test_verbose_feedback_contract.py）：
    make_verbose_event(event_id, data, source)      # src/core/agent_events.py
    VerboseFeedbackState().try_emit/mark_response_started/close
    VerboseFeedbackConfig().effective_enabled       # force_disabled 最高优先级
    validate_feedback_text(text) -> str             # 失败整体拒绝返回空串
"""

import asyncio
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Dict, Optional

from loguru import logger

from src.core.agent_events import make_verbose_event

# ============== 常量 ==============

# policy 文案违规时的降级文案（设计 §6.3，用户可见契约，逐字冻结）。
# 2026-09-01 产品决策：system watchdog 已删除，无系统兜底；本文案仅在
# policy 命中但模板违规时作为整体替换的降级文案（仍属 policy 决定的提示）。
DEFAULT_FALLBACK_MESSAGE = "正在处理你的请求，复杂任务可能需要一点时间，请耐心等待。"

# delegate_to_subagent 固定父级开始文案（设计 §5 解析顺序 2，审核文案）
DELEGATE_START_MESSAGE = "正在交由专业数字员工处理，请耐心等待"

# 文案长度上限（设计 §4/§6.1：1~60 字）
# 注意（Phase 4 裁决）：config 的 max_text_chars 字段当前仅作配置透传/展示，
# 运行期校验固定使用本模块常量 MAX_FEEDBACK_CHARS——validate_feedback_text 是
# 无 config 依赖的冻结契约（tests/unit/test_verbose_feedback_contract.py），
# 接线 config 会改变契约签名且 60 字上限无差异化诉求，故不做双源接线。
MAX_FEEDBACK_CHARS = 60

# 句末标点（单句判定用；出现即视为句子边界）
_SENTENCE_TERMINALS = set("。！？!?；;")

# 敏感键名（小写归一后子串匹配；设计 §6.1「敏感键」黑名单）
_SENSITIVE_KEYS = (
    "password", "passwd", "pwd",
    "api_key", "apikey", "api-key",
    "secret", "token", "access_token",
    "authorization", "cookie",
    "credential", "private_key", "client_secret",
)

# Windows 盘符路径（C:\... / C:/...）
_WINDOWS_PATH_RE = re.compile(r"[A-Za-z]:[\\/]")
# Unix 多段路径（/usr/local/bin 这类至少两段），避免误伤「24/7」单斜杠
_UNIX_PATH_RE = re.compile(r"/[\w.\-]+/[\w.\-]+")
# URL（https://...）
_URL_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://")
# HTML 标签（<b>、</span>、<br/> 等）与 HTML 实体（&nbsp;）
_HTML_TAG_RE = re.compile(r"</?[A-Za-z][^>]*>")
_HTML_ENTITY_RE = re.compile(r"&[A-Za-z]+;")
# JSON 片段：花括号或 "key": 形态
_JSON_KEY_RE = re.compile(r'["\']\s*:')

# 常见命令词（按词边界匹配，防误伤普通中文/英文叙述）
_COMMAND_WORDS = (
    "rm", "sudo", "apt-get", "chmod", "chown", "kill", "pkill",
    "curl", "wget", "scp", "ssh", "docker", "kubectl",
    "bash", "zsh", "pip", "pip3", "npm", "npx", "make",
    "grep", "awk", "sed", "cat", "ls", "cp", "mv", "dd",
)
_COMMAND_WORD_RES = tuple(
    re.compile(rf"(?<![\w-]){re.escape(word)}(?![\w-])") for word in _COMMAND_WORDS
)
# shell 元字符 / 命令替换
_SHELL_METACHARS = ("&&", "||", "|", "$(", "`", ">>", "&>")

# 数字 ETA：数字 + 时间单位，设计 §6.1 禁止。
# 拆成中文/英文两条：中文单位后不能接 \b（汉字间无词边界），英文单位需要 \b 防
# 误伤（如 beta 中的 eta 不会命中，但那是敏感词场景；这里防的是单位词误配）。
_ETA_CN_RE = re.compile(
    r"[0-9０-９]+(?:\.[0-9０-９]+)?\s*(?:个\s*)?"
    r"(?:秒|分钟|分|小时|时|天|周|月|日|年)"
)
_ETA_EN_RE = re.compile(
    r"[0-9０-９]+(?:\.[0-9０-９]+)?\s*"
    r"(?:seconds?|secs?|mins?|minutes?|hours?|hrs?|days?)\b",
    re.IGNORECASE,
)
# 百分比字符（半角/全角）
_PERCENT_CHARS = "%\uff05"


def new_verbose_event_id() -> str:
    """生成本轮唯一的 verbose eventId（``verbose_`` + uuid4 hex）。

    「本轮唯一」由调用方保证（设计 §4）：工厂不做去重，调用点不得传常量 id。
    """
    return f"verbose_{uuid.uuid4().hex}"


# ============== 文本校验（设计 §6.1，契约 4） ==============


def validate_feedback_text(text: Any) -> str:
    """白名单式校验用户可见反馈文案；合法原样返回，任何违规整体返回空串。

    规则（设计 §6.1）：1~60 字、单句、无换行；不含代码块、HTML、JSON、路径、
    命令、敏感键、数字 ETA 或百分比。失败不做局部清洗——调用方改用
    system fallback，避免「清洗后的半句病句/泄漏」直达用户。
    """
    if not isinstance(text, str):
        return ""
    if not text or not text.strip():
        return ""
    if len(text) > MAX_FEEDBACK_CHARS:
        return ""
    # 换行 / 制表符（单行约束）
    if any(ch in text for ch in ("\n", "\r", "\t")):
        return ""
    # 单句：句末标点最多一个，且必须位于末尾
    terminals = [ch for ch in text if ch in _SENTENCE_TERMINALS]
    if len(terminals) > 1 or (len(terminals) == 1 and text[-1] not in _SENTENCE_TERMINALS):
        return ""
    # 代码块 / 行内代码
    if "`" in text:
        return ""
    # HTML 标签 / 实体
    if _HTML_TAG_RE.search(text) or _HTML_ENTITY_RE.search(text):
        return ""
    # 百分比
    if any(ch in text for ch in _PERCENT_CHARS):
        return ""
    # 路径 / URL
    if _WINDOWS_PATH_RE.search(text) or _UNIX_PATH_RE.search(text) or _URL_RE.search(text):
        return ""
    # 命令：shell 元字符或命令词
    if any(meta in text for meta in _SHELL_METACHARS):
        return ""
    if any(pattern.search(text) for pattern in _COMMAND_WORD_RES):
        return ""
    # JSON 片段
    if "{" in text or "}" in text or _JSON_KEY_RE.search(text):
        return ""
    # 数字 ETA
    if _ETA_CN_RE.search(text) or _ETA_EN_RE.search(text):
        return ""
    # 敏感键（子串匹配，大小写不敏感）
    lowered = text.lower()
    if any(key in lowered for key in _SENSITIVE_KEYS):
        return ""
    return text


# ============== 策略结构与解析（设计 §5，唯一解析契约） ==============


@dataclass(frozen=True)
class LongRunningFeedback:
    """长任务策略解析结果：可信的等待提示文案。

    start_message 为空串表示「命中长任务但模板文案未通过校验」，
    调用方必须降级使用本轮配置的 fallback_message（整体替换，不局部清洗）。
    """

    start_message: str


def resolve_feedback_policy(
    tool_name: str,
    tool_args: dict,
    agent: Any,
) -> Optional[LongRunningFeedback]:
    """唯一长任务策略解析入口（设计 §5），解析顺序固定：

    1. ``skill_execute``：按 ``tool_args["skill"]`` 查当前 Agent skill_registry
       中该 Skill 的 ``metadata.user_feedback``（long_running 开启即命中；
       start_message 校验失败时返回空串文案，由调用方降级 fallback 文案）；
    2. ``delegate_to_subagent``：固定视为长任务，使用审核文案；
    3. 普通工具：调用可选 ``tool.get_user_feedback(tool_args)``（返回 None 即短任务）；
    4. 未命中返回 None，该 tool call 不产生业务提示。

    多个 tool call 并行时由调用方按顺序逐个解析，取第一个命中策略，不拼接。
    本函数只读不写，无副作用，异常安全（工具钩子异常视同短任务）。
    """
    if not tool_name or not isinstance(tool_name, str):
        return None
    args = tool_args if isinstance(tool_args, dict) else {}

    if tool_name == "skill_execute":
        return _resolve_skill_feedback(args, agent)
    if tool_name == "delegate_to_subagent":
        text = validate_feedback_text(DELEGATE_START_MESSAGE)
        return LongRunningFeedback(start_message=text) if text else None
    return _resolve_tool_feedback(tool_name, args, agent)


def _resolve_skill_feedback(tool_args: dict, agent: Any) -> Optional[LongRunningFeedback]:
    """skill_execute → metadata.user_feedback（编排侧 registry 字段，非 Skill 正文）。"""
    skill_name = str(tool_args.get("skill") or "").strip()
    if not skill_name:
        return None
    registry = getattr(agent, "skill_registry", None)
    if registry is None:
        return None
    feedback: Any = None
    getter = getattr(registry, "get_user_feedback", None)
    if callable(getter):
        try:
            feedback = getter(skill_name)
        except Exception as e:
            logger.opt(exception=True).warning(
                f"[VERBOSE] skill_registry.get_user_feedback failed: {e}"
            )
            feedback = None
    else:
        # 兜底：直接读 Skill.metadata（旧版 registry 无专用访问器时）
        skill = registry.get(skill_name)
        metadata = getattr(skill, "metadata", None) or {}
        feedback = metadata.get("user_feedback") if isinstance(metadata, dict) else None
    if not isinstance(feedback, dict) or not feedback.get("long_running"):
        return None
    start_message = feedback.get("start_message")
    text = validate_feedback_text(start_message) if isinstance(start_message, str) else ""
    # 命中长任务但文案违规：返回空串，调用方降级 fallback 文案（整体拒绝）
    return LongRunningFeedback(start_message=text)


def _find_tool(tool_name: str, agent: Any) -> Any:
    """按名称查找工具实例：先 ToolRegistry（普通工具），再控制工具集（虚拟工具）。"""
    tool_registry = getattr(agent, "tool_registry", None)
    if tool_registry is not None:
        try:
            tool = tool_registry.get_tool(tool_name)
        except Exception:
            tool = None
        if tool is not None:
            return tool
    controls = getattr(agent, "_tool_controls", None)
    if controls is not None:
        try:
            return controls.get(tool_name)
        except Exception:
            return None
    return None


def _resolve_tool_feedback(tool_name: str, tool_args: dict, agent: Any) -> Optional[LongRunningFeedback]:
    """普通工具 → 可选 get_user_feedback 钩子（返回 None/非约定类型即短任务）。"""
    tool = _find_tool(tool_name, agent)
    getter = getattr(tool, "get_user_feedback", None) if tool is not None else None
    if not callable(getter):
        return None
    try:
        feedback = getter(tool_args)
    except Exception as e:
        # 工具钩子异常视同短任务：verbose 是 best-effort，不得影响工具执行
        logger.opt(exception=True).warning(
            f"[VERBOSE] get_user_feedback failed for tool '{tool_name}': {e}"
        )
        return None
    if isinstance(feedback, LongRunningFeedback):
        text = validate_feedback_text(feedback.start_message)
        # 文案违规时返回空串，调用方降级 fallback 文案
        return LongRunningFeedback(start_message=text)
    return None


# ============== 每轮状态（设计 §7，契约 2） ==============


@dataclass
class VerboseFeedbackState:
    """owner 生命周期的 verbose 状态机：PENDING → EMITTED → CLOSED。

    必须由调用方（owner 开始时）显式创建并传递：渠道侧所有 cancel/merge 重跑
    attempt 复用同一实例，累计仍最多一条；严禁挂在共享 Agent 实例字段上
    （并发请求共用同一 Agent，实例属性会互相覆盖）。
    """

    event: Optional[Dict[str, Any]] = None
    emitted_at: Optional[float] = None
    response_started: bool = False
    closed: bool = False

    def try_emit(self, event: Dict[str, Any]) -> bool:
        """PENDING → EMITTED 成功一次；其后（EMITTED/response_started/CLOSED）一律拒绝。"""
        if self.event is not None or self.response_started or self.closed:
            return False
        self.event = event
        self.emitted_at = time.time()
        return True

    def mark_response_started(self) -> None:
        """response 开始后拒绝再发（成功标准 5）；与 try_emit 相互独立生效。"""
        self.response_started = True

    def close(self) -> None:
        """进入 CLOSED；幂等（异常/取消路径可能多次收尾），CLOSED 后拒绝再发。"""
        self.closed = True


# ============== 配置（设计 §11，契约 3） ==============


@dataclass(frozen=True)
class VerboseFeedbackConfig:
    """本轮冻结的 verbose 配置（owner 开始时确定，运行期不可改写）。

    默认开启（2026-09-01 产品决策：全局默认启用，替代原"首版灰度前关闭"策略）；
    ``force_disabled`` 是最高优先级全局 kill switch，任何请求级/渠道级配置都
    不能覆盖。

    2026-09-01 产品决策：system watchdog（8 秒兜底）已删除，verbose 仅由策略
    （Skill metadata / Tool get_user_feedback / delegate 固定文案）产生；
    ``fallback_message`` 保留，仅作 policy 文案违规时的整体降级模板。
    """

    enabled: bool = True
    force_disabled: bool = False
    max_per_turn: int = 1
    max_text_chars: int = 60
    delivery_timeout_seconds: float = 5.0
    fallback_message: str = DEFAULT_FALLBACK_MESSAGE

    @property
    def effective_enabled(self) -> bool:
        """最终生效开关：force_disabled 优先级高于 enabled（紧急回滚唯一手段）。"""
        return self.enabled and not self.force_disabled


def default_feedback_config() -> VerboseFeedbackConfig:
    """从全局配置（settings.agent.verbose_feedback）构造本轮 VerboseFeedbackConfig。

    settings 未声明 / 读取失败时返回代码默认值（2026-09-01 起默认启用），绝不抛异常。
    """
    try:
        from src.config.settings import settings
        agent_cfg = getattr(settings, "agent", None)
        vf = getattr(agent_cfg, "verbose_feedback", None)
    except Exception as e:
        logger.debug(f"[VERBOSE] 读取全局 verbose_feedback 配置失败，使用代码默认值: {e}")
        return VerboseFeedbackConfig()
    if vf is None:
        return VerboseFeedbackConfig()

    def _float(name: str, default: float) -> float:
        value = getattr(vf, name, default)
        try:
            return float(value) if value is not None else default
        except (TypeError, ValueError):
            return default

    def _int(name: str, default: int) -> int:
        value = getattr(vf, name, default)
        try:
            return int(value) if value is not None else default
        except (TypeError, ValueError):
            return default

    fallback = getattr(vf, "fallback_message", None)
    return VerboseFeedbackConfig(
        enabled=bool(getattr(vf, "enabled", False)),
        force_disabled=bool(getattr(vf, "force_disabled", False)),
        max_per_turn=_int("max_per_turn", 1),
        max_text_chars=_int("max_text_chars", 60),
        delivery_timeout_seconds=_float("delivery_timeout_seconds", 5.0),
        fallback_message=fallback if isinstance(fallback, str) and fallback else DEFAULT_FALLBACK_MESSAGE,
    )


def fallback_text_for(config: VerboseFeedbackConfig) -> str:
    """解析 policy 文案违规时降级可用的兜底文案：配置文案违规时回退到冻结默认值。"""
    text = validate_feedback_text(config.fallback_message)
    return text or DEFAULT_FALLBACK_MESSAGE


def build_policy_verbose_event(
    agent: Any,
    valid_tool_calls: list,
    config: VerboseFeedbackConfig,
    state: VerboseFeedbackState,
) -> Optional[Dict[str, Any]]:
    """Agent 主循环 policy 注入点（Phase 1 唯一注入逻辑，agent.py 只做接线 yield）。

    调用时机固定：valid_tool_calls 规范化完成后、首个 tool_start yield 之前
    （设计 §5）。行为：

    1. 按顺序对每个有效 tool call 调 resolve_feedback_policy，取第一个长任务
       策略（并行 tool calls 不拼接多条文案）；
    2. 策略文案违规（start_message 为空串）时整体降级 fallback 文案
       （仍属 policy 决定的提示，不局部清洗）；
    3. state.try_emit 成功才返回事件（状态机已发射 / response 已开始 / 已关闭
       时拒绝，每轮最多一条）；
    4. 全程 best-effort：任何异常只记日志并返回 None，绝不影响工具执行。

    Returns:
        可直接 yield 的 verbose 事件 dict；未命中 / 被状态机拒绝 / 异常时 None。
    """
    try:
        feedback = None
        for probe in valid_tool_calls or []:
            feedback = resolve_feedback_policy(probe.get("name"), probe.get("arguments"), agent)
            if feedback is not None:
                break
        if feedback is None:
            return None
        raw_text = feedback.start_message or fallback_text_for(config)
        checked_text = validate_feedback_text(raw_text) or fallback_text_for(config)
        event = make_verbose_event(new_verbose_event_id(), checked_text, "policy")
        # 状态机守卫：已发射 / response 已开始 / 已关闭时拒绝
        return event if state.try_emit(event) else None
    except Exception as e:
        logger.opt(exception=True).warning(f"[VERBOSE] policy 注入失败（best-effort 忽略）: {e}")
        return None


def prepare_turn_feedback(
    progress_callback: Any,
    feedback_state: Optional[VerboseFeedbackState],
    verbose_config: Optional[VerboseFeedbackConfig],
) -> tuple:
    """process_message_sync 的 wrapper 门控（设计 §6.3）。

    仅当存在用户投递 callback（progress_callback 非 None）且配置生效时启用
    verbose 包装；scheduler 等无用户表面的调用完全不受影响。owner 级 state 优先
    复用调用方显式传入的实例（cancel/merge 重跑共享，累计仍最多一条），
    未传入时新建一次，严禁挂在共享 Agent 实例字段上。

    Returns:
        (enabled, config, state)：enabled=False 时 config 为 None、state 原样返回。
    """
    if progress_callback is None:
        return False, None, feedback_state
    cfg = (
        verbose_config
        if isinstance(verbose_config, VerboseFeedbackConfig)
        else default_feedback_config()
    )
    if not cfg.effective_enabled:
        return False, None, feedback_state
    state = feedback_state if feedback_state is not None else VerboseFeedbackState()
    return True, cfg, state


# ============== 可观测性（设计 §12） ==============


class VerboseFeedbackObserver:
    """verbose 观测钩子基类：只记录元信息，绝不记录文案正文。

    至少覆盖：是否触发、source、文本长度、time-to-first、抑制原因。
    渠道 dispatcher（Phase 3）可子类化回写 delivery outcome。
    """

    def on_verbose_emitted(
        self, *, source: str, text_length: int, time_to_first_seconds: float
    ) -> None:
        logger.info(
            f"[VERBOSE] emitted source={source} text_length={text_length} "
            f"time_to_first={time_to_first_seconds:.2f}s"
        )

    def on_verbose_suppressed(self, *, reason: str) -> None:
        logger.info(f"[VERBOSE] suppressed reason={reason}")


def notify_emitted(
    observer: Optional[VerboseFeedbackObserver],
    *,
    source: str,
    text_length: int,
    time_to_first_seconds: float,
) -> None:
    """安全回调 on_verbose_emitted：observer 异常只记日志，不影响主流程。"""
    if observer is None:
        return
    try:
        observer.on_verbose_emitted(
            source=source,
            text_length=text_length,
            time_to_first_seconds=time_to_first_seconds,
        )
    except Exception as e:
        logger.debug(f"[VERBOSE] observer.on_verbose_emitted failed: {e}")


def notify_suppressed(observer: Optional[VerboseFeedbackObserver], *, reason: str) -> None:
    """安全回调 on_verbose_suppressed：observer 异常只记日志，不影响主流程。"""
    if observer is None:
        return
    try:
        observer.on_verbose_suppressed(reason=reason)
    except Exception as e:
        logger.debug(f"[VERBOSE] observer.on_verbose_suppressed failed: {e}")


# ============== 事件包装器（设计 §6.3） ==============

# 用户可见的终止事件：到达即视为本轮结束（EMITTED → CLOSED，状态机图）。
# browser_human_required 属用户可见交互终态：其后 response_started 置位，
# state 与 dispatcher 都不再放行/投递任何后续 verbose（policy 发射守卫）。
_TERMINAL_EVENT_TYPES = frozenset({
    "response", "clarification", "browser_human_required",
    "complete", "error", "cancelled",
})


class _ProducerDone:
    """producer 正常结束哨兵。"""


class _ProducerFailed:
    """producer 异常包装（队列需传递异常对象而非在 put 时抛出）。"""

    __slots__ = ("error",)

    def __init__(self, error: BaseException):
        self.error = error


_PRODUCER_DONE = _ProducerDone()


async def iter_with_verbose_feedback(
    source_gen: AsyncIterator[Dict[str, Any]],
    *,
    surface: str,
    config: VerboseFeedbackConfig,
    state: VerboseFeedbackState,
    observer: Optional[VerboseFeedbackObserver] = None,
) -> AsyncIterator[Dict[str, Any]]:
    """包装原始 Agent 事件 async generator：verbose 事件的统一过滤/观测出口。

    确定性要求（设计 §6.3，2026-09-01 产品决策后）：
    - 无系统兜底：wrapper 自身不产生任何 verbose，只透传；verbose 唯一来源是
      策略（agent 主循环 policy 注入点在 tool_start 前 yield 并 try_emit）；
    - producer task 消费原始事件写入内部 queue，消费端逐条透传，绝不取消 producer；
    - 「非 state.event 本体的 verbose 一律丢弃」：迟到的重复 / 外部伪造事件在
      此被防御性拦截，确保「每轮最多一条」不依赖 producer 自觉；
    - 终态事件（response / clarification / browser_human_required 等）到达即
      mark_response_started，其后 state 拒绝再发；
    - 正常 / 异常 / 取消路径都必须 cancel 并 await producer，不留遗留 task；
    - 配置未生效时纯透传（不启动 producer task）。

    Args:
        source_gen: 原始 Agent 事件 async generator（process_message / impl）。
        surface: 用户表面，仅允许 ``"web" | "channel"``（日志与 Phase 3 dispatcher 用）。
        config: 本轮冻结配置；``effective_enabled=False`` 时纯透传。
        state: 本轮共享状态（policy 注入方与 wrapper 必须同一实例）。
        observer: 可选观测钩子（不记录文案正文）。
    """
    if surface not in ("web", "channel"):
        raise ValueError(f"surface 仅允许 web|channel，实际: {surface!r}")

    if not config.effective_enabled:
        # 关闭时零开销透传：与原始 generator 行为完全一致，不产生任何 task
        async for event in source_gen:
            yield event
        return

    queue: asyncio.Queue = asyncio.Queue()
    start_monotonic = time.monotonic()

    async def _producer() -> None:
        try:
            async for event in source_gen:
                await queue.put(event)
        except asyncio.CancelledError:
            raise
        except BaseException as e:  # noqa: BLE001 - 需把任意异常传回消费端
            await queue.put(_ProducerFailed(e))
        finally:
            await queue.put(_PRODUCER_DONE)

    producer = asyncio.create_task(_producer())
    try:
        while True:
            item = await queue.get()

            if item is _PRODUCER_DONE:
                break
            if isinstance(item, _ProducerFailed):
                raise item.error

            event = item
            event_type = event.get("type")
            if event_type == "verbose" and event is not state.event:
                # 防御：未在本轮 state 注册的 verbose（迟到重复/外部伪造）一律丢弃，
                # 确保「每轮最多一条」不依赖 producer 自觉
                notify_suppressed(observer, reason="duplicate_verbose_dropped")
                continue
            if event_type == "verbose":
                # policy verbose 透传：emitted 观测统一由 wrapper 记录（设计 §12）
                notify_emitted(
                    observer,
                    source=str(event.get("source", "")),
                    text_length=len(event.get("data") or ""),
                    time_to_first_seconds=time.monotonic() - start_monotonic,
                )
            if event_type in _TERMINAL_EVENT_TYPES:
                state.mark_response_started()
            yield event
    finally:
        # 正常 / 异常 / 取消统一收尾：cancel 并 await producer，不留遗留 task
        producer.cancel()
        try:
            await producer
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug(f"[VERBOSE] producer cleanup suppressed: {e}")
