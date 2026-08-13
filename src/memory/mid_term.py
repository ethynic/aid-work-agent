"""会话内上下文压缩服务（Mid-Term Memory）

实现「Summary Buffer」式的会话内上下文压缩：当单 session 的 messages 数组
接近模型 token 上限时，把 HEADER（前几条）+ TAIL（最近 N 条）之间的中间段
调用 LLM 压缩成结构化摘要，原消息物理保留但打上 `compacted=true` 标记。

v3.2 架构原则（执行顺序优化）：
  - 拆分为 `check_threshold`（O(1) 阈值检查）+ `compress_now`（执行压缩）两个职责清晰的方法
  - 调用方先 check_threshold（不拉 messages），未达阈值时跳过 compress_now 完整 IO
  - 保留 `compress_session` 作为兼容入口，内部串联 check_threshold + compress_now
  - check_threshold 用 _eval_threshold 纯函数做双阈值判断；
    缓存=0 时不回退 count_tokens（让消息数阈值兜底）

v3.1 架构原则：
  - 内部自动从 DB 解析 tenant_id/user_id/subagent_id/context_token_count
  - 内部自动从 settings.llm 读取 model_limit
  - 不依赖 Agent / Channel / 工具系统（避免循环依赖）
  - 同步执行：调用方直接 await，等待压缩完成

设计文档：docs/infrastructure/memory/context_compression_design.md (v3.2)
"""

import asyncio
import json
import os
import random
import re
import threading
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from src.config.settings import settings
from src.core.cache_utils import CacheKeys, delete_cached_pattern
from src.db.database import get_db_connection
from src.db.models import ContextSummaryDB, generate_summary_id


# ============== 脱敏（P2-2）==============

# 敏感信息正则模式（与 .claude/rules/backend_dev.md SENSITIVE_PATTERNS 保持一致）
SENSITIVE_PATTERNS = [
    r'password["\s:=]+\S+',
    r'api[_-]?key["\s:=]+\S+',
    r'token["\s:=]+\S+',
    r'secret["\s:=]+\S+',
]


def sanitize_text(text: str) -> str:
    """脱敏文本中的敏感信息（password/api_key/token/secret 后的值替换为 ***）。

    用于构造摘要 LLM prompt 前对 message content 脱敏，避免把敏感信息发给第三方 LLM。
    """
    if not text:
        return text
    for pattern in SENSITIVE_PATTERNS:
        text = re.sub(
            pattern,
            lambda m: re.split(r'[:=]', m.group(0), maxsplit=1)[0] + '=***',
            text,
            flags=re.IGNORECASE,
        )
    return text


# ============== token 计数 ==============

# CJK 统一汉字范围（U+4E00 ~ U+9FFF）
_CJK_START = 0x4E00
_CJK_END = 0x9FFF


def _count_text_tokens_impl(text: str) -> int:
    """区分中英文的 token 估算：
    - 中文：1 字 ≈ 1.5 token（CJK 汉字往往 1 字 1-2 token，取折中）
    - 其他字符（英文/数字/符号）：4 字符 ≈ 1 token
    """
    if not text:
        return 0
    chinese_chars = sum(1 for c in text if _CJK_START <= ord(c) <= _CJK_END)
    other_chars = len(text) - chinese_chars
    return int(chinese_chars * 1.5 + other_chars / 4)


def count_tokens(messages: List[Dict[str, Any]]) -> int:
    """粗略估算 messages 数组的 token 数。

    TODO(Phase 7): 替换为精确 tokenizer（tiktoken / 分词器）。
    当前实现区分中英文：中文 1 字 ≈ 1.5 token，其他 4 字符 ≈ 1 token。
    仅用于触发阈值判断，不参与计费。
    """
    total = 0
    for msg in messages:
        content = msg.get("content") or ""
        if isinstance(content, (dict, list)):
            content = json.dumps(content, ensure_ascii=False)
        total += _count_text_tokens_impl(str(content))
        tool_calls = msg.get("tool_calls")
        if tool_calls:
            try:
                tc_str = json.dumps(tool_calls, ensure_ascii=False)
                total += _count_text_tokens_impl(tc_str)
            except Exception:
                pass
    return total


def count_text_tokens(text: str) -> int:
    """估算单段文本的 token 数（区分中英文）。"""
    return _count_text_tokens_impl(text)


# ============== 数据类（v3.1 Phase 3）==============


@dataclass
class CompressionResult:
    """compress_session 的返回值（v3.1 Phase 3）。

    设计文档 §5.1 CompressionResult：summary_id、压缩消息数、token 前后值、
    压缩比、降级标记、实际调用的 provider/model。
    """

    summary_id: str
    compressed_message_count: int
    original_token_count: int
    compressed_token_count: int
    compression_ratio: float
    fallback_used: bool
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None
    # 触发压缩的原因（force / token_threshold(...) / message_threshold(...)）
    # 由 check_threshold 返回的 reason 透传（force 时填 "force"）
    trigger_reason: str = ""


@dataclass
class SessionMeta:
    """从 DB 解析的 session 元数据（v3.1 Phase 3）。

    `_resolve_session_meta` 返回此结构，供 `compress_session` 内部使用。
    所有字段在 session 不存在时为空值（不抛异常，让上层判断）。
    """

    session_id: str
    source_type: str
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    subagent_id: Optional[str] = None
    context_token_count: int = 0


# ============== 模型上下文上限映射（v3.1 Phase 3）==============

# 现役主力模型的上下文上限（token 数）。_get_model_limit 会优先查这里，
# 找不到时回退到 _DEFAULT_MODEL_LIMIT 并打 warning。
# 只收录项目实际使用的 model_code（见 configs/config.yaml 的 llm 配置）。
# 数据来源：各 provider 官方文档（截至 2026-06）。
_MODEL_CONTEXT_LIMITS: Dict[str, int] = {
    # DeepSeek（主 provider）。官方标称均为 1M，按 5 折取值（API 实际可用 ~128K-200K，
    # 且长上下文性能在 150K 以下更稳定，保守打折避免阈值偏晚）
    "deepseek-v4-pro": 512_000,
    "deepseek-v4-flash": 512_000,
}

# 未知模型回退到的保守值（与现役主力模型对齐）
_DEFAULT_MODEL_LIMIT = 512_000


# ============== 摘要 LLM 直连（P0-3）==============

# 各 provider 的默认 endpoint 和 API key 环境变量名
_PROVIDER_DEFAULTS = {
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "api_key_env": "DEEPSEEK_API_KEYS",  # 项目使用复数形式，逗号分隔
        "default_model": "deepseek-chat",
    },
    "qwen": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key_env": "QWEN_API_KEYS",
        "default_model": "qwen-plus",
    },
    "zhipu": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "api_key_env": "ZHIPU_API_KEYS",
        "default_model": "glm-4-flash",
    },
}


def _get_provider_api_key(provider: str) -> Optional[str]:
    """从环境变量读取指定 provider 的第一个有效 API key。"""
    cfg = _PROVIDER_DEFAULTS.get(provider)
    if not cfg:
        return None
    raw = os.getenv(cfg["api_key_env"], "").strip()
    if not raw:
        return None
    # 项目约定多 key 用逗号分隔，这里取第一个
    first = raw.split(",")[0].strip()
    return first or None


async def _call_summary_llm_direct(
    provider: str,
    model: str,
    messages_for_llm: List[Dict[str, Any]],
    temperature: float,
    max_tokens: int,
    timeout: float,
) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """直接调用指定 provider 的 OpenAI 兼容 chat completions endpoint。

    绕过 llm_gateway 的主 provider 路由，用于摘要 LLM 切换到 deepseek 等便宜模型。

    Args:
        provider: 提供者（deepseek / qwen / zhipu）
        model: 模型名
        messages_for_llm: OpenAI 格式消息
        temperature: 温度
        max_tokens: 最大输出 token
        timeout: 超时秒数

    Returns:
        (content, usage)：成功时 content 为字符串（可能为空），usage 为 dict 或 None；
        网络层异常时抛出。
    """
    import httpx

    cfg = _PROVIDER_DEFAULTS.get(provider)
    if not cfg:
        raise ValueError(f"unsupported summary_llm provider: {provider}")
    api_key = _get_provider_api_key(provider)
    if not api_key:
        raise ValueError(f"summary_llm provider [{provider}] API key not configured")
    base_url = os.getenv(f"{provider.upper()}_BASE_URL") or cfg["base_url"]
    api_url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": model,
        "messages": messages_for_llm,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(api_url, headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()
    # OpenAI 兼容响应
    choices = data.get("choices") or []
    content: Optional[str] = None
    if choices:
        msg = choices[0].get("message") or {}
        content = msg.get("content")
    usage = data.get("usage")
    return content, usage


# ============== 服务 ==============

class ContextCompressionService:
    """会话内上下文压缩服务（v3.1 同步模式，业务无关模块）。

    对外公开方法（v3.2.1 P1-6）：
    - `compress_session(session_id, source_type, *, force=False)`：触发压缩
    - `get_active_summary(session_id, source_type)`：读取当前 active 摘要文本
    - `get_session_tenant_id(session_id, source_type)`：解析 session 归属租户
      （供 API 层做租户隔离校验，避免直接调用内部 _resolve_session_meta）

    其他内部方法（
    `_eval_threshold` / `_split_messages` / `_call_summary_llm` /
    `_persist_atomically` / `_fallback_truncate`）属于实现细节，不应被外部调用。

    v3.2.1 关键设计：
    - 同步 await：调用方等待压缩完成，本轮 LLM 调用直接享受新上下文
    - 入参收敛：tenant_id/user_id/subagent_id 由 `_resolve_session_meta` 内部解析
    - 模型上限自动读取：`_get_model_limit` 从 `settings.llm` 读 model_code
    - token 缓存优先：`_eval_threshold` 优先读 session.context_token_count
      字段（v3.1），为 0 时让消息数阈值兜底；消息数 ≥ 80 时再做精确 token 重算
    - DB 调用全部走 `asyncio.to_thread`：避免阻塞 FastAPI 事件循环（v3.2.1 P0-2）
    """

    def __init__(
        self,
        settings_cfg: Optional[Any] = None,
        redis_client: Optional[Any] = None,
        llm_gateway: Optional[Any] = None,
    ):
        """初始化压缩服务

        Args:
            settings_cfg: 中期记忆配置（settings.memory.mid_term），缺省自动取
            redis_client: Redis 客户端（保留参数，v3.1 未使用）
            llm_gateway: LLM 网关（用于摘要调用的 fallback），缺省时延迟到首次调用时获取
        """
        self._settings = settings_cfg or settings.memory.mid_term
        self._redis = redis_client
        self._llm_gateway = llm_gateway
        # 摘要 LLM 提供者/模型（配置值，实际调用可能 fallback 到主 gateway）
        self._summary_provider_cfg = self._settings.summary_llm.provider
        self._summary_model_cfg = self._settings.summary_llm.model
        self._summary_timeout = self._settings.summary_llm.timeout_sec
        self._summary_retry = self._settings.summary_llm_retry
        # 实际调用的 provider/model（初始化时未定，首次调用后确定）
        self._actual_provider: Optional[str] = None
        self._actual_model: Optional[str] = None
        # v3.1: 模型上下文上限覆盖点（仅测试用）。生产代码默认 None，每次解析
        # settings.llm 拿最新 model_code（P1-2：避免 failover 后缓存陈旧）。
        # 测试可通过直接赋值注入指定 limit，绕过 settings 解析。
        self._model_limit_cache: Optional[int] = None

    def _get_llm_gateway(self):
        """延迟获取 LLM 网关单例，避免 import 时初始化"""
        if self._llm_gateway is not None:
            return self._llm_gateway
        from src.llm.gateway import llm_gateway as _gw
        self._llm_gateway = _gw
        return self._llm_gateway

    # ========== session 元数据 / 模型上限解析（v3.1 Phase 3）==========

    async def _resolve_session_meta(
        self,
        session_id: str,
        source_type: str,
    ) -> SessionMeta:
        """从 DB 解析 session 元数据（v3.1 Phase 3；v3.2.1 真正非阻塞）。

        - source_type='chat' → 查 chat_sessions 表（SessionDB.get_by_id）
        - 其他 source_type（wecom_kf/dingtalk/feishu/wecom_personal_rpa）
          → 查 channel_sessions 表（ChannelSessionManager.get_session_by_id）

        v3.2.1（P0-2）：底层 DB 调用是同步阻塞的（psycopg2），用 asyncio.to_thread
        转入线程池执行，避免阻塞 FastAPI 事件循环。FastAPI 单 worker 高并发下，
        PG 单行查询的几 ms 不再阻塞其他协程。

        Args:
            session_id: 会话 ID
            source_type: 来源类型

        Returns:
            SessionMeta。session 不存在时所有可选字段为空，context_token_count=0，
            不抛异常，让上层（compress_session）根据 session_id 是否为空字符串判断。
        """
        return await asyncio.to_thread(
            self._resolve_session_meta_sync, session_id, source_type
        )

    def _resolve_session_meta_sync(
        self,
        session_id: str,
        source_type: str,
    ) -> SessionMeta:
        """_resolve_session_meta 的同步实现（v3.2.1 P0-2 拆出）。

        在 asyncio.to_thread 中执行；不要直接在 async 调用栈中调用本方法。
        """
        # 延迟 import 避免循环依赖
        from src.db.models import SessionDB
        from src.channels.session import channel_session_manager

        try:
            if source_type == "chat":
                row = SessionDB.get_by_id(session_id)
            else:
                # 复用模块级单例，避免每次新建 ChannelSessionManager（P1-4）
                row = channel_session_manager.get_session_by_id(session_id)
        except Exception as e:
            logger.warning(
                f"ContextCompression _resolve_session_meta failed: "
                f"sid={session_id}, source={source_type}, err={e}"
            )
            return SessionMeta(
                session_id=session_id,
                source_type=source_type,
                tenant_id=None,
                user_id=None,
                subagent_id=None,
                context_token_count=0,
            )

        if not row:
            # session 不存在：返回空 meta（不抛异常，让上层判断）
            return SessionMeta(
                session_id=session_id,
                source_type=source_type,
                tenant_id=None,
                user_id=None,
                subagent_id=None,
                context_token_count=0,
            )

        # channel_sessions 的 user_id 字段可能为空字符串
        tenant_id = row.get("tenant_id") or None
        user_id = row.get("user_id") or None
        subagent_id = row.get("subagent_id") or None
        # context_token_count 字段可能不存在（存量数据未升级）或为 None
        try:
            cached = row.get("context_token_count")
            context_token_count = int(cached) if cached else 0
        except (TypeError, ValueError):
            context_token_count = 0

        return SessionMeta(
            session_id=session_id,
            source_type=source_type,
            tenant_id=tenant_id,
            user_id=user_id,
            subagent_id=subagent_id,
            context_token_count=context_token_count,
        )

    def _get_model_limit(self) -> int:
        """从 settings.llm 读取当前主模型的上下文上限（v3.1 Phase 3）。

        取值优先级：
        0. 测试注入：若 self._model_limit_cache 非 None（测试通过赋值注入），直接返回
        1. 主 provider 对应的 model（settings.llm.<provider>.model）
        2. _MODEL_CONTEXT_LIMITS 映射表
        3. _DEFAULT_MODEL_LIMIT（512K）+ logger.warning

        生产路径不缓存（每次解析）：解析成本可忽略，避免 provider failover 后
        缓存陈旧（P1-2）。

        Returns:
            模型上下文 token 上限
        """
        # 测试注入优先
        if self._model_limit_cache is not None:
            return self._model_limit_cache

        # 取主 provider 配置的 model_code
        provider = settings.llm.provider
        provider_cfg = getattr(settings.llm, provider, None)
        model_code = ""
        if provider_cfg is not None:
            model_code = (provider_cfg.model or "").strip()

        limit = _MODEL_CONTEXT_LIMITS.get(model_code)
        if limit is None:
            limit = _DEFAULT_MODEL_LIMIT
            logger.warning(
                f"ContextCompression unknown model_code [{model_code}] "
                f"(provider={provider}), fallback to default limit={limit}"
            )
        return limit

    async def _load_messages(
        self,
        session_id: str,
        source_type: str,
        tenant_id: Optional[str],
    ) -> List[Dict[str, Any]]:
        """从 DB 加载 messages（v3.1 Phase 3）。

        - source_type='chat' → MessageDB.list_by_session（默认过滤 compacted=true）
        - 其他 source_type → ChannelSessionManager.get_messages（默认过滤 compacted=true）

        返回的 messages 包含 id 字段（用于压缩时记录 compressed_message_ids）。
        """
        from src.db.models import MessageDB
        from src.channels.session import channel_session_manager

        try:
            if source_type == "chat":
                # limit 给个大值，避免默认 100 截断；实际由 _eval_threshold / 精确回退判断
                messages = MessageDB.list_by_session(session_id, limit=10000)
            else:
                # 复用模块级单例，避免每次新建 ChannelSessionManager（P1-4）
                messages = channel_session_manager.get_messages(session_id, limit=10000)
        except Exception as e:
            logger.warning(
                f"ContextCompression _load_messages failed: "
                f"sid={session_id}, source={source_type}, err={e}"
            )
            return []

        # tool_calls 还原：DB 层把 tool_calls 存在 metadata 里（写入见 session.py:826-827），
        # 但压缩后续逻辑（_drop_orphan_tool_messages / _split_messages TAIL 对齐）检查的是
        # 顶层 tool_calls 字段。这里统一还原，与主流程 short_term.py:149 / agent.py:1227 一致。
        # 不还原会导致所有 tool 消息被判为孤儿删除，COMPRESS 区内容流失，压缩死循环。
        for m in messages:
            if m.get("role") != "assistant" or m.get("tool_calls"):
                continue
            meta = m.get("metadata")
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except Exception:
                    meta = {}
            if isinstance(meta, dict) and meta.get("tool_calls"):
                m["tool_calls"] = meta["tool_calls"]
        return messages

    # ========== 触发判断 ==========

    def _eval_threshold(
        self,
        cached_tokens: int,
        msg_count: int,
        model_limit: int,
    ) -> Tuple[bool, str]:
        """纯函数双阈值判断（v3.2 新增；v3.2.1 P1-2 起成为唯一阈值判断函数）。

        - 不依赖 messages 数组，只用 cached_tokens（session 表缓存）和 msg_count（COUNT 查询）
        - token 阈值优先：cached_tokens >= model_limit × 70% → 压缩
        - 缓存 = 0（新 session 首轮 / Agent 异常未写入）时不做 token 判断，
          等下一轮 LLM 写入缓存后再判；极端长会话由消息数阈值兜底
        - 消息数阈值兜底：msg_count >= message_count_threshold（默认 200）→ 压缩
        - 调用方应在 check_threshold 中先做 msg_count 查询（O(log n)），再调用本函数

        Args:
            cached_tokens: session.context_token_count 缓存值（>0 时用于 token 判断）
            msg_count: chat_messages / channel_messages 表的 COUNT 查询结果
            model_limit: 当前模型的上下文上限（token 数）

        Returns:
            (是否压缩, 触发原因字符串)；不压缩时 reason 为空字符串
        """
        # token 阈值优先（用缓存值，不调 count_tokens）
        # 缓存 = 0 时不做 token 判断，让消息数阈值兜底
        if cached_tokens > 0:
            token_threshold = int(model_limit * self._settings.token_threshold_ratio)
            if cached_tokens >= token_threshold:
                pct = cached_tokens * 100 // model_limit if model_limit > 0 else 0
                return True, f"token_threshold({cached_tokens}/{token_threshold}, {pct}%, cached=True)"

        # 消息数阈值兜底
        msg_threshold = self._settings.message_count_threshold
        if msg_count >= msg_threshold:
            return True, f"message_threshold({msg_count}/{msg_threshold})"

        return False, ""

    # ========== 分段 ==========

    def _split_messages(
        self,
        messages: List[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
        """分 HEADER / COMPRESS / TAIL 三段。

        - HEADER：前 header_keep 条（边界对齐到完整对话单元），保留原文
        - TAIL：最近 tail_keep 条（边界对齐到完整对话单元），保留原文
        - COMPRESS：中间所有消息，进入摘要 LLM

        **边界对齐原则（v3.2.2 治本修复）**：HEADER 终点和 TAIL 起点都必须落在
        「安全切断点」（_is_safe_split_point）上，即一个完整对话单元的边界。
        对话单元 = user → [assistant(tool_calls) → tool...] → assistant(最终回复)，
        绝不在 assistant(tool_calls) 与 tool 之间切断，否则会产生孤儿 tool
        （配对的 assistant 被压走、tool 留下），导致 COMPRESS 区内容流失、压缩死循环。

        孤儿 tool 兜底（P0-4）：历史压缩可能已遗留孤儿 tool（配对 assistant 已被压走），
        这些 tool 由 _drop_orphan_tool_messages 降级为 assistant 文本保留（不删除），
        避免占着消息数却进不了 COMPRESS 区。

        如果消息总数太少（≤ header_keep + tail_keep），COMPRESS 区为空。

        Returns:
            (header, compress, tail) 三段
        """
        header_keep = self._settings.header_keep
        tail_keep = self._settings.tail_keep

        # 预处理：降级孤儿 tool（配对 assistant 已被历史压缩的 tool）为 assistant 文本
        messages = self._drop_orphan_tool_messages(messages)
        total = len(messages)

        # 总数太少，全部保留，COMPRESS 区为空
        if total <= header_keep + tail_keep:
            return list(messages), [], []

        is_safe = ContextCompressionService._is_safe_split_point

        # ---------- HEADER 终点对齐 ----------
        # header_end_idx 从 header_keep 向后找，直到落在安全切断点。
        # 即 messages[header_end_idx] 是 user 或 assistant(无 tool_calls)。
        # 这样 HEADER 区的末尾必是一个完整单元的结束，不会把 assistant(tool_calls)
        # 留在 HEADER 而 tool 结果落到 COMPRESS。
        header_end_idx = header_keep
        while header_end_idx < total and not is_safe(messages[header_end_idx]):
            header_end_idx += 1
        header = messages[:header_end_idx]

        # ---------- TAIL 起点对齐 ----------
        # tail_start_idx 从 total - tail_keep 向前找，直到落在安全切断点。
        # 即 messages[tail_start_idx] 是 user 或 assistant(无 tool_calls)。
        # 这样 TAIL 区的开头必是一个完整单元的开始，不会把 tool 结果留在 TAIL
        # 而对应的 assistant(tool_calls) 落到 COMPRESS。
        tail_start_idx = total - tail_keep
        # 不能侵入 HEADER 区
        min_tail_start = header_end_idx
        while tail_start_idx > min_tail_start and not is_safe(messages[tail_start_idx]):
            tail_start_idx -= 1

        tail = messages[tail_start_idx:total]
        compress = messages[header_end_idx:tail_start_idx]
        return header, compress, tail

    @staticmethod
    def _extract_tool_calls(msg: Dict[str, Any]) -> list:
        """读取 assistant 的 tool_calls，兼容顶层和 metadata 两种存储。

        DB 层把 tool_calls 存在 metadata 里（写入见 session.py:826-827），
        _load_messages 会还原到顶层，但为防御其他数据源（如直接构造的测试数据、
        未走 _load_messages 的路径），这里兜底同时检查两处。与 short_term.py:149 一致。
        """
        tc = msg.get("tool_calls")
        if tc:
            return tc
        meta = msg.get("metadata")
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                return []
        if isinstance(meta, dict):
            return meta.get("tool_calls") or []
        return []

    @staticmethod
    def _is_safe_split_point(msg: Optional[Dict[str, Any]]) -> bool:
        """判断该位置是否为安全的分段切断点（一个完整对话单元的边界）。

        一个完整对话单元：user → [assistant(tool_calls) → tool...] → assistant(最终回复)。
        安全切断点 = 上一个单元已结束、下一个单元尚未开始的边界位置：
        - user 消息（新对话轮的开始）✓
        - assistant 无 tool_calls（上一轮的结束）✓
        - tool 消息（单元中间，切断会导致孤儿 tool）✗
        - assistant 有 tool_calls（后面还有 tool 结果，切断会断链）✗

        HEADER 终点和 TAIL 起点都必须落在安全切断点上，才能保证：
        1. COMPRESS 区和 TAIL 区都不会出现孤儿 tool（配对的 assistant(tc) 和 tool 要么
           同在一段、要么同在另一段，不被切断）
        2. 任何一段单独送给摘要 LLM 或主对话 LLM 都是完整的消息序列
        """
        if not msg:
            return True
        role = msg.get("role")
        if role == "user":
            return True
        if role == "assistant":
            return not ContextCompressionService._extract_tool_calls(msg)
        return False

    @staticmethod
    def _drop_orphan_tool_messages(
        messages: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """处理孤儿 tool 消息（P0-4）。

        遍历 messages，对每条 role=tool 的消息向前回溯：
        - 若最近一个非 tool 消息是 assistant(tool_calls) → 配对成功，保留
        - 否则（user、无 tool_calls 的 assistant，或回溯到头部）→ 孤儿 tool

        孤儿 tool 的处理（v3.2.2 修复死循环）：**不删除**，而是降级为 assistant 文本
        消息保留在 messages 中。原因：
        1. 删除会导致 COMPRESS 区内容流失（孤儿 tool 本该被压缩），消息数永远降不下来
           → 死循环触发压缩（生产实证：2 小时触发 13 次）
        2. 孤儿 tool 的产生往往是因为配对的 assistant(tool_calls) 已被历史压缩，
           这些 tool 内容仍有摘要价值，应参与摘要而非丢弃
        3. 摘要 LLM 把所有消息格式化为纯文本（_format_messages_for_prompt），
           不要求 tool 消息配对，降级为 assistant 文本不影响摘要质量
        4. 降级为 assistant（而非保留 role=tool）可避免 _split_messages 的 TAIL 对齐
           逻辑把它们误吞进 TAIL（while 循环遇 role=tool 会无限向前回溯）

        连续多个 tool 结果可能对应同一次 tool_calls（multi-tool 场景），所以回溯时
        允许跨过其他 tool 消息。

        Returns:
            处理后的 messages（孤儿 tool 已降级为 assistant，数量不变）
        """
        if not messages:
            return messages
        orphan_indices: set = set()
        for i, msg in enumerate(messages):
            if msg.get("role") != "tool":
                continue
            # 向前回溯寻找最近一个非 tool 消息
            j = i - 1
            paired = False
            while j >= 0:
                prev_role = messages[j].get("role")
                if prev_role == "tool":
                    j -= 1
                    continue
                # 找到非 tool 消息：判断是否为带 tool_calls 的 assistant
                # （兼容 tool_calls 存在 metadata 的存储，见 _extract_tool_calls）
                if prev_role == "assistant" and ContextCompressionService._extract_tool_calls(
                    messages[j]
                ):
                    paired = True
                break
            if not paired:
                orphan_indices.add(i)

        if not orphan_indices:
            return messages

        logger.warning(
            f"orphan tool message downgraded to assistant: count={len(orphan_indices)}, "
            f"total_msgs={len(messages)}, indices={sorted(orphan_indices)}"
        )
        # 降级孤儿 tool：role 改为 assistant，保留 content 和 id（id 用于压缩记录 compressed_ids）
        result = []
        for i, m in enumerate(messages):
            if i in orphan_indices:
                m = dict(m)
                # 读取工具名（兼容 metadata 为 dict / JSON 字符串 / None）
                tool_name = ""
                meta = m.get("metadata")
                if isinstance(meta, str):
                    try:
                        meta = json.loads(meta)
                    except Exception:
                        meta = {}
                if isinstance(meta, dict):
                    tool_name = meta.get("tool_name") or ""
                m["role"] = "assistant"
                if tool_name:
                    m["content"] = f"[工具结果:{tool_name}] {m.get('content') or ''}"
                m.pop("tool_calls", None)
            result.append(m)
        return result

    # ========== 工具结果预处理 ==========

    def _preprocess_tool_results(
        self,
        compress_section: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """对 COMPRESS 区内的 tool 结果做差异化预处理，降低 token 占用。

        Phase 1+2 简化实现：所有 tool 消息 content 截断到 large_tool_result_truncate_chars。
        Phase 5 会按工具类型应用更精细的策略（truncate/summarize/keep/drop）。
        """
        truncate_chars = self._settings.large_tool_result_truncate_chars
        result: List[Dict[str, Any]] = []
        for msg in compress_section:
            if msg.get("role") != "tool":
                result.append(msg)
                continue
            content = msg.get("content")
            if content is None:
                result.append(msg)
                continue
            text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
            if len(text) <= truncate_chars:
                result.append(msg)
                continue
            truncated = text[:truncate_chars] + "\n[...原文已存档，可重新调用工具获取...]"
            new_msg = dict(msg)
            new_msg["content"] = truncated
            result.append(new_msg)
        return result

    # ========== 摘要 LLM 调用 ==========

    def _build_summary_prompt(
        self,
        existing_summary: Optional[str],
        new_messages: List[Dict[str, Any]],
    ) -> str:
        """构造结构化摘要 prompt（设计 §3.4）。

        注意：调用方在传入 new_messages 前应先调用 _sanitize_messages_for_prompt
        对敏感字段脱敏（P2-2）。
        """
        max_tokens = self._settings.summary_max_tokens
        existing_block = (
            sanitize_text(existing_summary.strip()) if existing_summary else "(无，本次为首次摘要)"
        )
        new_block = self._format_messages_for_prompt(new_messages)
        return f"""你是对话摘要助手。请把以下对话历史压缩成结构化摘要，保留长期有效的信息。

压缩原则（核心）：
- 以「任务/事件」为单位归并：用户围绕同一件事的多轮对话（反复澄清、
  补充信息、试错）应归并成一条结论，而非逐轮流水账记录。
- 保留「用户最终需求」+「模型最终方案」：一个完整的任务压成一对结论
  （要什么 / 怎么解决的）。中间的澄清、试错、工具调用过程可丢弃。
- 工具调用过程不保留，但工具产出的关键事实（查到的订单号、客户信息等）必须按原文保留。
- **文件路径不可省略（最高优先级）**：对话中 cp 工具返回的 file_path / download_url
  是用户后续下载文件的唯一入口。即使为控制长度省略其他内容，每个生成过文件的任务
  都必须在「已完成的任务」里内联保留其 download_url 原文，格式：
  「已生成XX文档（下载：download_url原文）」。路径丢失 = 用户找不到文件。

输出格式（严格遵守）：

## 用户与背景
- 用户的身份、任务背景、长期约束

## 关键事实与决策
- 已确认的事实、已做的决策（含决策原因）

## 已完成的任务
- 已经做完的事情。每个生成/交付了文件的任务，**必须**在同一行内联标注下载路径，
  格式：「任务描述（下载：<download_url原文>）」。不可遗漏，不可仅写"已生成"而不带路径。

## 进行中的事项
- 尚未完成的任务、待跟进的待办

## 关键文件与资源
- 涉及的文件路径、订单号、客户 ID 等（必须保留原文）

## 用户偏好
- 沟通风格、格式偏好等

要求：
1. 每个字段只写必要条目，不要扩写
2. 文件路径、ID、URL 必须保留原文，不要改写
3. **cp 工具返回的 download_url 必须内联到对应任务条目，不得省略**（用户靠它下载文件）
4. 总长度不超过 {max_tokens} tokens

已有摘要（如有，请合并增量信息）：
{existing_block}

需要总结的新对话：
{new_block}
"""

    def _format_messages_for_prompt(self, messages: List[Dict[str, Any]]) -> str:
        """把 messages 列表格式化为 prompt 中的文本块（P2-2：已做脱敏）"""
        lines: List[str] = []
        for idx, msg in enumerate(messages):
            role = msg.get("role", "unknown")
            content = msg.get("content") or ""
            if isinstance(content, (dict, list)):
                content = json.dumps(content, ensure_ascii=False)
            content = sanitize_text(str(content))
            if role == "tool":
                tool_name = (msg.get("metadata") or {}).get("tool_name") or "tool"
                lines.append(f"[{idx}] tool({tool_name}): {content}")
            elif role == "assistant" and msg.get("tool_calls"):
                calls_desc = "; ".join(
                    f"{tc.get('function', {}).get('name', '')}({tc.get('function', {}).get('arguments', '')})"
                    for tc in msg.get("tool_calls") or []
                )
                calls_desc = sanitize_text(calls_desc)
                lines.append(f"[{idx}] assistant(tool_calls=[{calls_desc}]): {content}")
            else:
                lines.append(f"[{idx}] {role}: {content}")
        return "\n".join(lines)

    async def _call_summary_llm(
        self,
        existing_summary: Optional[str],
        new_messages: List[Dict[str, Any]],
        *,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[str]:
        """调用摘要 LLM，独立超时 + 重试 M 次。失败返回 None。

        优先级（P0-3）：
          1. 若 summary_llm.provider 配置了独立 API key（环境变量 *_API_KEYS），
             直接调用对应 provider 的 OpenAI 兼容 endpoint（绕过主 gateway）
          2. 否则 fallback 到主 llm_gateway.chat()，并记录 warning
        DB 记录的 llm_provider/llm_model 是实际调用的（不是配置值）。

        Args:
            existing_summary: 既有的 active 摘要（增量合并用）
            new_messages: COMPRESS 区消息（已预处理）
            tenant_id: 租户 ID（v3.2.2 P1 修复：从 SessionMeta 透传，
                       background_runner 调度场景无 HTTP 上下文，需显式传入
                       才能让 record_background_llm_usage 把计费归属到租户）
            user_id: 用户 ID（同上）

        Returns:
            摘要文本；重试耗尽仍失败时返回 None

        Note:
            外层 asyncio.wait_for 超时设为 summary_llm.timeout_sec * 2，留出
            gateway/provider 内部超时空间（避免双层超时冲突，P1-7）。
        """
        prompt = self._build_summary_prompt(existing_summary, new_messages)
        messages_for_llm = [
            {"role": "user", "content": prompt},
        ]
        provider_cfg = self._summary_provider_cfg
        model_cfg = self._summary_model_cfg
        has_direct_key = _get_provider_api_key(provider_cfg) is not None
        max_attempts = self._summary_retry + 1  # 首次 + 重试
        # 外层超时留 2x 余量，给 provider 内部超时留空间（P1-7）
        outer_timeout = self._summary_timeout * 2

        last_error: Optional[Exception] = None
        for attempt in range(1, max_attempts + 1):
            try:
                if has_direct_key:
                    # 直连路径（P0-3）：用独立 provider 调用
                    content, _usage = await asyncio.wait_for(
                        _call_summary_llm_direct(
                            provider=provider_cfg,
                            model=model_cfg,
                            messages_for_llm=messages_for_llm,
                            temperature=0.3,
                            max_tokens=self._settings.summary_max_tokens,
                            timeout=float(self._summary_timeout),
                        ),
                        timeout=outer_timeout,
                    )
                    self._actual_provider = provider_cfg
                    self._actual_model = model_cfg
                    # 累加 LLM 用量到当前 SessionRecordService（对话内后台 LLM 调用计费）
                    # v3.2.2 P1 修复：background_runner 调度场景透传 tenant_id/user_id，
                    # 让兜底落库路径能归属租户
                    from src.services.session_record import record_background_llm_usage
                    record_background_llm_usage(
                        _usage,
                        tenant_id=tenant_id,
                        user_id=user_id,
                        source="mid_term_summary",
                        user_message="上下文压缩扫描摘要",
                    )
                else:
                    # fallback 到主 gateway（仅首次记录 warning）
                    if attempt == 1:
                        logger.warning(
                            f"summary_llm provider [{provider_cfg}] not configured, "
                            f"fallback to main gateway"
                        )
                    gateway = self._get_llm_gateway()
                    result = await asyncio.wait_for(
                        gateway.chat(
                            messages=messages_for_llm,
                            temperature=0.3,
                            max_tokens=self._settings.summary_max_tokens,
                        ),
                        timeout=outer_timeout,
                    )
                    content = (result or {}).get("content")
                    # fallback 到主 gateway，provider/model 以 gateway 实际为准
                    self._actual_provider = getattr(gateway, "provider_name", None) or "main"
                    self._actual_model = model_cfg
                    # 累加 LLM 用量到当前 SessionRecordService（对话内后台 LLM 调用计费）
                    # v3.2.2 P1 修复：background_runner 调度场景透传 tenant_id/user_id
                    from src.services.session_record import record_background_llm_usage
                    record_background_llm_usage(
                        (result or {}).get("usage"),
                        tenant_id=tenant_id,
                        user_id=user_id,
                        source="mid_term_summary",
                        user_message="上下文压缩扫描摘要",
                    )

                content = (content or "").strip()
                if content:
                    logger.info(
                        f"ContextCompression summary LLM ok, attempt={attempt}, "
                        f"len={len(content)}, provider={self._actual_provider}"
                    )
                    return content
                # 空 content 也视为失败，进入重试
                last_error = ValueError("empty content from summary LLM")
                logger.warning(f"ContextCompression summary LLM empty content, attempt={attempt}")
            except asyncio.TimeoutError:
                last_error = TimeoutError(f"summary LLM timeout after {outer_timeout}s")
                logger.warning(
                    f"ContextCompression summary LLM timeout, attempt={attempt}/{max_attempts}"
                )
            except Exception as e:
                last_error = e
                logger.warning(
                    f"ContextCompression summary LLM error, attempt={attempt}/{max_attempts}, "
                    f"err={type(e).__name__}: {e}"
                )
            # 重试间隔：线性退避 + 抖动（P1-6）
            if attempt < max_attempts:
                delay = 1.0 * attempt + random.random()
                await asyncio.sleep(delay)

        logger.error(
            f"ContextCompression summary LLM exhausted retries, last_error={last_error}"
        )
        return None

    # ========== 同步降级路径 ==========

    def _fallback_truncate(self, compress_section: List[Dict[str, Any]]) -> str:
        """降级路径：摘要 LLM 失败时，硬截断中间段，提取关键骨架。

        策略（设计 §6.2）：
        - 所有 user 消息原文（截断到 500 字符）
        - 所有 assistant 最终回复原文（截断到 500 字符）
        - 所有工具调用名 + 参数（不含结果）

        拼成结构化但简短的伪摘要，不调 LLM。
        """
        max_item_chars = 500
        user_items: List[str] = []
        assistant_items: List[str] = []
        tool_items: List[str] = []

        for msg in compress_section:
            role = msg.get("role")
            content = msg.get("content") or ""
            if isinstance(content, (dict, list)):
                content = json.dumps(content, ensure_ascii=False)
            content_str = str(content)

            if role == "user":
                text = content_str[:max_item_chars]
                user_items.append(text)
            elif role == "assistant":
                tool_calls = msg.get("tool_calls") or []
                if tool_calls:
                    for tc in tool_calls:
                        fn = tc.get("function", {}) or {}
                        name = fn.get("name", "")
                        args = fn.get("arguments", "")
                        if isinstance(args, (dict, list)):
                            args = json.dumps(args, ensure_ascii=False)
                        args_text = str(args)[:max_item_chars]
                        tool_items.append(f"{name}({args_text})")
                else:
                    text = content_str[:max_item_chars]
                    assistant_items.append(text)
            elif role == "tool":
                # 降级路径不保留 tool 结果，仅记录出现过的工具
                pass

        parts: List[str] = ["[降级摘要（LLM 调用失败，硬截断骨架）]"]
        if user_items:
            parts.append("## 用户消息")
            for idx, t in enumerate(user_items, 1):
                parts.append(f"{idx}. {t}")
        if assistant_items:
            parts.append("## 助手回复")
            for idx, t in enumerate(assistant_items, 1):
                parts.append(f"{idx}. {t}")
        if tool_items:
            parts.append("## 调用的工具")
            for idx, t in enumerate(tool_items, 1):
                parts.append(f"{idx}. {t}")
        return "\n".join(parts)

    # ========== 原子事务持久化 ==========

    def _persist_atomically(
        self,
        session_id: str,
        source_type: str,
        tenant_id: Optional[str],
        user_id: Optional[str],
        subagent_id: Optional[str],
        summary_text: str,
        compressed_message_ids: List[int],
        original_token_count: int,
        compressed_token_count: int,
        fallback_used: bool = False,
        llm_tokens_used: Optional[int] = None,
    ) -> str:
        """原子事务持久化（缺一不可）。

        四步一个事务：
          ① SELECT 旧 active summary（用于 ③ 步骤 superseded）
          ② INSERT chat_context_summaries（status='active'），summary_version 由
             INSERT...SELECT COALESCE(MAX(version),0)+1 单 SQL 计算（P1-3，避免竞态）
          ③ UPDATE 旧 active summary → status='superseded'
          ④ UPDATE compacted=true（按 source_type 分流）：
             - source_type='chat' → UPDATE chat_messages
             - 其他 source_type（wecom_kf/dingtalk/feishu/wecom_personal_rpa）
               → UPDATE channel_messages
        任一步失败 → 整个事务回滚，对外抛异常。

        v3.2.1 P0 修复：第④步必须按 source_type 分流到对应消息表。之前硬编码
        chat_messages，导致第三方渠道（channel_messages）的 compacted 标记永远
        写不进去 → 下次请求 list_by_session 不过滤这些消息 → 又注入 active
        summary → 形成无限重复压缩 + 上下文永不缩小 + summary 越来越多的 P0 bug。

        P0-1 配合：DB 层有 UNIQUE 部分索引 (session_id, source_type) WHERE status='active'，
        即使并发产生两条 active 也会被唯一约束拒绝，保证最终至多一条。

        Args:
            session_id: 会话 ID
            source_type: 来源类型
            tenant_id / user_id / subagent_id: 租户/用户/子智能体
            summary_text: 摘要文本
            compressed_message_ids: 被压缩的原消息 BIGINT id 列表
            original_token_count: 压缩前 token 数
            compressed_token_count: 压缩后 token 数（摘要 + TAIL）
            fallback_used: 是否降级路径
            llm_tokens_used: 摘要 LLM 消耗（降级时为 0）

        Returns:
            新创建的 summary_id

        Raises:
            Exception: 三步任一失败时抛出，事务已回滚
        """
        summary_id = generate_summary_id()
        compressed_count = len(compressed_message_ids)
        if original_token_count > 0:
            ratio = compressed_token_count / original_token_count
        else:
            ratio = 0.0
        placeholder = "%s"
        # 实际调用的 provider/model（DB 记录的是真实值，不是配置值，P0-3）
        actual_provider = self._actual_provider or self._summary_provider_cfg
        actual_model = self._actual_model or self._summary_model_cfg

        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                # ① 查询现有 active summary（用于 ③ 步骤 superseded）
                cursor.execute(
                    f"""
                    SELECT summary_id FROM chat_context_summaries
                    WHERE session_id = {placeholder}
                      AND source_type = {placeholder}
                      AND status = 'active'
                    """,
                    (session_id, source_type),
                )
                old_active_rows = cursor.fetchall() or []

                # ② INSERT 新 summary（status='active'）+ summary_version 单 SQL 计算（P1-3）
                cursor.execute(
                    f"""
                    INSERT INTO chat_context_summaries (
                        summary_id, session_id, source_type, tenant_id, user_id, subagent_id,
                        summary_text, summary_version,
                        compressed_message_ids, compressed_message_count,
                        original_token_count, compressed_token_count, compression_ratio,
                        llm_provider, llm_model, llm_tokens_used,
                        fallback_used, status
                    ) SELECT
                        {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder},
                        {placeholder},
                        COALESCE((SELECT MAX(summary_version) FROM chat_context_summaries
                                 WHERE session_id = {placeholder} AND source_type = {placeholder}), 0) + 1,
                        {placeholder}, {placeholder},
                        {placeholder}, {placeholder}, {placeholder},
                        {placeholder}, {placeholder}, {placeholder},
                        {placeholder}, {placeholder}
                    """,
                    (
                        summary_id, session_id, source_type, tenant_id, user_id, subagent_id,
                        summary_text,
                        session_id, source_type,
                        list(compressed_message_ids), compressed_count,
                        original_token_count, compressed_token_count, ratio,
                        actual_provider, actual_model, llm_tokens_used,
                        fallback_used, "active",
                    ),
                )

                # ③ 旧 active summary → 'superseded'
                for old_row in old_active_rows:
                    old_id = old_row.get("summary_id")
                    if not old_id:
                        continue
                    cursor.execute(
                        f"""
                        UPDATE chat_context_summaries
                        SET status = 'superseded',
                            superseded_at = CURRENT_TIMESTAMP
                        WHERE summary_id = {placeholder} AND status = 'active'
                        """,
                        (old_id,),
                    )

                # ④ UPDATE compacted=true（按 source_type 分流到对应消息表）
                # v3.2.1 P0 修复：第三方渠道消息在 channel_messages 表，不能硬编码 chat_messages，
                # 否则 compacted 标记永远写不进去 → 无限重复压缩（详见 _persist_atomically docstring）。
                # table_name 来自白名单 if/else，非用户输入，用 f-string 拼接是安全的（PG 也不支持表名参数化）。
                if compressed_message_ids:
                    if source_type == "chat":
                        table_name = "chat_messages"
                    else:
                        table_name = "channel_messages"
                    cursor.execute(
                        f"""
                        UPDATE {table_name}
                        SET compacted = TRUE,
                            compacted_by = {placeholder}
                        WHERE id = ANY({placeholder}::bigint[])
                        """,
                        (summary_id, list(compressed_message_ids)),
                    )
                    affected = cursor.rowcount
                    if affected != compressed_count:
                        logger.warning(
                            f"ContextCompression persist: compacted affected rows {affected} "
                            f"!= expected {compressed_count} (some messages may have been deleted)"
                        )

                conn.commit()
                # 压缩后立即失效该会话的消息列表缓存（list_by_session 缓存 60s）。
                # 仅 chat_messages 走缓存（channel_messages 的 get_messages 不缓存），
                # 但这里无条件清理一次也安全、开销极小，避免本轮 memory rebuild 读到压缩前的旧列表。
                try:
                    delete_cached_pattern(CacheKeys.SESSION_MSGS, session_id, "")
                except Exception as cache_err:
                    logger.warning(
                        f"ContextCompression invalidate SESSION_MSGS cache failed: "
                        f"sid={session_id}, err={cache_err}"
                    )
                logger.info(
                    f"ContextCompression persisted summary_id={summary_id}, "
                    f"compacted={compressed_count} msgs, "
                    f"old_active_superseded={len(old_active_rows)}, fallback={fallback_used}"
                )
                return summary_id
            except Exception as e:
                try:
                    conn.rollback()
                except Exception as rollback_err:
                    logger.error(f"ContextCompression rollback failed: {rollback_err}")
                logger.exception(
                    f"ContextCompression _persist_atomically failed, session={session_id}, "
                    f"source={source_type}: {e}"
                )
                raise

    # ========== 对外入口（v3.2：拆分为 check_threshold + compress_now）==========

    async def check_threshold(
        self,
        session_id: str,
        source_type: str,
    ) -> Tuple[bool, str, "SessionMeta"]:
        """快速阈值检查（v3.2 新增）。

        只做三件事：
        1. 读 session 表（O(1) 单行查询，含 context_token_count 缓存字段）
        2. 读消息条数 COUNT（走索引，不拉数据）
        3. 双阈值判断（_eval_threshold）

        **不**加载完整 messages 列表，避免完整 DB IO。

        Args:
            session_id: 会话 ID
            source_type: 'chat' / 'wecom_kf' / 'dingtalk' / 'feishu' / 'wecom_personal_rpa'

        Returns:
            (should_compress, reason, session_meta)
            - should_compress=False 时是快路径，调用方跳过 compress_now
            - reason 为空字符串表示未触发；否则是 'token_threshold(...)' 或 'message_threshold(...)'
            - session_meta 是 _resolve_session_meta 的结果，传给 compress_now 复用避免重复查询

        **边界权衡**：
        - 缓存 context_token_count = 0（新 session 首轮 / 存量数据 / Agent 异常未写入）时，
          **不**做 token 判断（不回退到 count_tokens 全量计算，因为新执行顺序下 memory
          尚未重建，拉取 messages 等于完整 IO，违背 check_threshold 的快路径初衷）。
          等下一轮 LLM 写入缓存后再判；极端长会话由消息数阈值（默认 200）兜底。
        """
        # 1) 读 session 元数据（含 context_token_count）—— v3.2.1 P0-2：已 to_thread
        meta = await self._resolve_session_meta(session_id, source_type)

        # 2) 消息数 COUNT 查询（走索引，不拉数据）
        # v3.2.1（P0-2）：用 asyncio.to_thread 包裹，避免阻塞事件循环
        try:
            msg_count = await self._count_messages(session_id, source_type)
        except Exception as e:
            logger.warning(
                f"ContextCompression check_threshold count_msgs failed: "
                f"sid={session_id}, source={source_type}, err={e}（降级到 0，让 token 缓存兜底）"
            )
            msg_count = 0

        # 3) 双阈值判断（token 优先 + 消息数兜底，缓存=0 时跳过 token 判断）
        model_limit = self._get_model_limit()
        should, reason = self._eval_threshold(
            meta.context_token_count, msg_count, model_limit
        )

        return should, reason, meta

    async def _count_messages(
        self, session_id: str, source_type: str
    ) -> int:
        """COUNT 消息数（v3.2.1 P0-2：用 asyncio.to_thread 包裹同步 DB 调用）。

        - source_type='chat' → MessageDB.count_messages_by_session
        - 其他 source_type → ChannelSessionManager.count_messages_by_session

        默认过滤 compacted=true（与 _load_messages 保持一致），让消息数兜底阈值
        基于实际活跃消息数（与 v3.2 行为保持一致）。
        """
        if source_type == "chat":
            from src.db.models import MessageDB
            return await asyncio.to_thread(
                MessageDB.count_messages_by_session, session_id
            )
        from src.channels.session import channel_session_manager
        return await asyncio.to_thread(
            channel_session_manager.count_messages_by_session, session_id
        )

    async def compress_now(
        self,
        session_id: str,
        source_type: str,
        session_meta: Optional["SessionMeta"] = None,
        *,
        force: bool = False,
        trigger_reason: str = "",
    ) -> Optional[CompressionResult]:
        """执行压缩（v3.2 新增；v3.2.1 新增 trigger_reason 参数）。

        **不再做阈值检查**（假设调用方已通过 check_threshold 判断），直接进入：
        加载 messages → 分段 → 预处理 → 摘要 LLM → 持久化。

        Args:
            session_id: 会话 ID
            source_type: 来源类型
            session_meta: 由 check_threshold 返回的 meta，避免重复查询；
                          None 时内部重新调 _resolve_session_meta（兼容旧调用方）
            force: True 时即使阈值未过也强制压缩（运维触发 / Phase 8 调度任务）。
                   此时 trigger_reason 记为 "force"。
            trigger_reason: 调用方（如 check_threshold + 主流程）传入的精确触发原因
                            （'token_threshold(...)' / 'message_threshold(...)'）。
                            优先级：force=True 时强制 "force"；
                            否则用 trigger_reason；为空时回退到 "threshold_passed"
                            （兼容旧调用方）。

        Returns:
            CompressionResult（含 summary_id / token 数 / 压缩比 / 降级标记）；
            COMPRESS 区为空时返回 None（极少见，仅当消息数 <= header_keep + tail_keep）；
            压缩失败时抛异常（事务已回滚，调用方决定是否重试）。
        """
        # 1) session 元数据：复用 check_threshold 传入的，或重新解析
        if session_meta is not None:
            meta = session_meta
        else:
            meta = await self._resolve_session_meta(session_id, source_type)

        # 2) 加载 messages（默认过滤 compacted=true）
        messages = await self._load_messages(session_id, source_type, meta.tenant_id)

        # 3) trigger_reason 优先级：force > 显式传入 > 通用回退
        # v3.2.1（P0-1）：主流程透传 check_threshold 的精确 reason，
        # 避免「压缩触发」在生产监控里全部坍缩成 "threshold_passed" 丢失精确性
        if force:
            final_reason = "force"
        else:
            final_reason = trigger_reason or "threshold_passed"

        # 4) 分段（含孤儿 tool 清理）
        header, compress, tail = self._split_messages(messages)
        if not compress:
            logger.debug(
                f"ContextCompression skipped: compress_section_empty, "
                f"sid={session_id}, total_msgs={len(messages)}"
            )
            return None

        # 5) 预处理 COMPRESS 区
        processed_compress = self._preprocess_tool_results(compress)

        # 6) 摘要 LLM（失败 → 降级）
        existing_summary = self.get_active_summary(session_id, source_type)
        # v3.2.2 P1 修复：透传 SessionMeta 的 tenant_id/user_id，让 background_runner
        # 调度场景（无 HTTP 上下文）的计费能归属到具体租户
        summary_text = await self._call_summary_llm(
            existing_summary,
            processed_compress,
            tenant_id=meta.tenant_id,
            user_id=meta.user_id,
        )
        fallback_used = False
        llm_tokens_used: Optional[int] = None
        if summary_text is None:
            summary_text = self._fallback_truncate(compress)
            fallback_used = True
            llm_tokens_used = 0
            logger.warning(
                f"ContextCompression fallback to truncate, session={session_id}, source={source_type}"
            )
        else:
            # 估算 LLM 消耗（粗略：prompt + output）
            llm_tokens_used = count_text_tokens(
                self._build_summary_prompt(existing_summary, processed_compress)
            ) + count_text_tokens(summary_text)

        # 7) 收集被压缩消息的 BIGINT id
        compressed_ids: List[int] = []
        for m in compress:
            mid = m.get("id")
            if mid is not None:
                try:
                    compressed_ids.append(int(mid))
                except (TypeError, ValueError):
                    continue

        # 8) 计算 token 数
        original_token_count = count_tokens(messages)
        compressed_token_count = count_tokens(header) + count_text_tokens(summary_text) + count_tokens(tail)
        ratio = (
            compressed_token_count / original_token_count
            if original_token_count > 0
            else 0.0
        )

        # 9) 原子事务持久化
        summary_id = self._persist_atomically(
            session_id=session_id,
            source_type=source_type,
            tenant_id=meta.tenant_id,
            user_id=meta.user_id,
            subagent_id=meta.subagent_id,
            summary_text=summary_text,
            compressed_message_ids=compressed_ids,
            original_token_count=original_token_count,
            compressed_token_count=compressed_token_count,
            fallback_used=fallback_used,
            llm_tokens_used=llm_tokens_used,
        )

        logger.info(
            f"ContextCompression done: sid={session_id}, source={source_type}, "
            f"summary_id={summary_id}, reason={final_reason}, "
            f"compacted={len(compressed_ids)} msgs, "
            f"token {original_token_count}->{compressed_token_count}, "
            f"fallback={fallback_used}"
        )

        return CompressionResult(
            summary_id=summary_id,
            compressed_message_count=len(compressed_ids),
            original_token_count=original_token_count,
            compressed_token_count=compressed_token_count,
            compression_ratio=ratio,
            fallback_used=fallback_used,
            llm_provider=self._actual_provider or self._summary_provider_cfg,
            llm_model=self._actual_model or self._summary_model_cfg,
            trigger_reason=final_reason,
        )

    async def compress_session(
        self,
        session_id: str,
        source_type: str,
        *,
        force: bool = False,
    ) -> Optional[CompressionResult]:
        """兼容入口（v3.1 Phase 3，v3.2 内部串联 check_threshold + compress_now）。

        保留此方法以兼容既有调用方和测试。新调用方推荐直接使用
        check_threshold + compress_now 组合，以便在 check_threshold 未通过时
        跳过 compress_now 的完整 DB IO（v3.2 执行顺序优化）。

        Args:
            session_id: 会话 ID
            source_type: 来源类型
            force: True 时跳过阈值检查直接压缩（运维触发 / Phase 8 调度任务）。
                   force=True 时不调用 check_threshold，直接进入 compress_now。

        Returns:
            CompressionResult；未达阈值且 force=False 时返回 None；
            压缩失败时抛异常（事务已回滚）。
        """
        if force:
            # force 模式：完全跳过 check_threshold（不做 COUNT/单行查询），直接进入压缩
            return await self.compress_now(session_id, source_type, None, force=True)

        should, reason, meta = await self.check_threshold(session_id, source_type)
        if not should:
            logger.debug(
                f"ContextCompression skipped: sid={session_id}, source={source_type}, "
                f"reason={reason or 'below threshold'}"
            )
            return None
        # v3.2.1（P0-1）：把 check_threshold 的精确 reason 透传给 compress_now，
        # 不再事后覆盖 result.trigger_reason（保持两条路径行为一致）
        return await self.compress_now(
            session_id, source_type, meta, force=False, trigger_reason=reason
        )

    def get_active_summary(
        self,
        session_id: str,
        source_type: str,
        tenant_id: Optional[str] = None,
    ) -> Optional[str]:
        """读取当前 active 摘要文本（给 _build_messages 用）。

        Args:
            session_id: 会话 ID
            source_type: 来源类型
            tenant_id: 租户 ID（租户隔离）。非 SaaS 场景可传 None。
        """
        try:
            row = ContextSummaryDB.get_active_by_session(
                session_id, source_type, tenant_id=tenant_id
            )
            if not row:
                return None
            return row.get("summary_text")
        except Exception as e:
            logger.error(
                f"ContextCompression get_active_summary failed, session={session_id}, "
                f"source={source_type}, tenant={tenant_id}: {e}"
            )
            return None

    async def get_session_tenant_id(
        self,
        session_id: str,
        source_type: str,
    ) -> Optional[str]:
        """解析 session 归属租户（v3.2.1 P1-6）。

        公共方法，供 API 层（context_compression_routes.manual_compress）做
        压缩前的租户隔离校验，避免 API 层直接调用内部 _resolve_session_meta。

        内部无缓存：每次都查 DB（轻量单行查询）。Phase 7 §7.1 的可观测性
        summary 路由在 rollback 后未失效此缓存（因为根本没有缓存），所以
        本方法的返回值始终反映 DB 当前状态。

        Args:
            session_id: 会话 ID
            source_type: 来源类型（chat / wecom_kf / dingtalk / feishu /
                         wecom_personal_rpa）

        Returns:
            session 归属租户的 tenant_id；session 不存在或解析失败时返回 None。
        """
        meta = await self._resolve_session_meta(session_id, source_type)
        return meta.tenant_id

    async def scan_over_threshold_sessions(
        self, token_threshold: int, batch_size: int = 50
    ) -> List[Tuple[str, str]]:
        """扫描 context_token_count 超阈值的 session（Phase 8 §2.5）。

        一条 SQL 扫 chat_sessions + channel_sessions 两张表，返回
        [(session_id, source_type), ...]。只扫 token_count > 0 的
        （缓存=0 的判不准，留给主流程同步压缩）。

        查询异常容错返回空列表（定时任务不因 DB 抖动崩溃）。
        """
        return await asyncio.to_thread(
            self._scan_over_threshold_sessions_sync, token_threshold, batch_size
        )

    def _scan_over_threshold_sessions_sync(
        self, token_threshold: int, batch_size: int = 50
    ) -> List[Tuple[str, str]]:
        """scan_over_threshold_sessions 的同步实现（run in thread）。"""
        placeholder = "%s"
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    f"""
                    SELECT session_id, 'chat' AS source_type FROM chat_sessions
                    WHERE context_token_count > {placeholder} AND context_token_count > 0
                    UNION ALL
                    SELECT session_id, channel_type AS source_type FROM channel_sessions
                    WHERE context_token_count > {placeholder} AND context_token_count > 0
                    LIMIT {placeholder}
                    """,
                    (token_threshold, token_threshold, batch_size),
                )
                rows = cursor.fetchall() or []
                return [(row["session_id"], row["source_type"]) for row in rows]
        except Exception as e:
            logger.warning(
                f"ContextCompression scan_over_threshold_sessions failed, "
                f"threshold={token_threshold}, batch={batch_size}: {e}"
            )
            return []


# ============== 单例（P1-4：线程安全）==============

_compression_service: Optional[ContextCompressionService] = None
_compression_service_lock = threading.Lock()


def get_compression_service() -> ContextCompressionService:
    """获取 ContextCompressionService 单例（线程安全，P1-4）。"""
    global _compression_service
    if _compression_service is None:
        with _compression_service_lock:
            # double-check：进入锁后再判断一次，避免多线程同时通过外层检查
            if _compression_service is None:
                _compression_service = ContextCompressionService()
    return _compression_service


async def run_background_compression_scan() -> Dict[str, int]:
    """Phase 8 定时任务入口：扫描超阈值 session 并补压缩。

    读 settings.memory.mid_term 配置；disabled 时直接返回零统计。
    单个 session 异常被隔离（记日志继续下一个），不让一个 session 挂掉整轮扫描。

    Returns:
        {"scanned": N, "compressed": N, "failed": N, "skipped": N}
    """
    cfg = settings.memory.mid_term
    if not (cfg.enabled and cfg.background_scan_enabled):
        return {"scanned": 0, "compressed": 0, "failed": 0, "skipped": 0}

    service = get_compression_service()
    model_limit = service._get_model_limit()
    threshold = int(model_limit * cfg.token_threshold_ratio)

    sessions = await service.scan_over_threshold_sessions(
        threshold, cfg.background_scan_batch_size
    )

    compressed = 0
    skipped = 0
    failed = 0
    for sid, source in sessions:
        try:
            result = await service.compress_session(sid, source, force=False)
            if result is not None:
                compressed += 1
            else:
                skipped += 1
        except Exception as e:
            failed += 1
            logger.warning(
                f"ContextCompression background scan compress failed, "
                f"sid={sid}, source={source}: {e}"
            )

    return {
        "scanned": len(sessions),
        "compressed": compressed,
        "failed": failed,
        "skipped": skipped,
    }
