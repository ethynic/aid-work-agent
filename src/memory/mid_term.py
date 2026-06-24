"""会话内上下文压缩服务（Mid-Term Memory）

实现「Summary Buffer」式的会话内上下文压缩：当单 session 的 messages 数组
接近模型 token 上限时，把 HEADER（前几条）+ TAIL（最近 N 条）之间的中间段
调用 LLM 压缩成结构化摘要，原消息物理保留但打上 `compacted=true` 标记。

本文件仅实现 Phase 1+2 同步路径：
  - 触发判断（双阈值）
  - 消息分段（HEADER/COMPRESS/TAIL，TAIL 对齐工具链边界）
  - 工具结果预处理（简单截断策略）
  - 摘要 LLM 调用（独立超时 + 重试，支持切换到 deepseek 等便宜模型）
  - 同步降级路径（_fallback_truncate）
  - 原子事务持久化（_persist_atomically）

异步派发、Redis SETNX 锁、失败计数等 Phase 3+4 内容不在本文件中。

设计文档：docs/infrastructure/memory/context_compression_design.md (v2.0)
"""

import asyncio
import json
import os
import random
import re
import threading
import uuid
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from src.config.settings import settings
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
    """会话内上下文压缩服务

    Phase 1+2：提供同步入口 `compress_now`，串联触发判断、分段、摘要、持久化。
    后续 Phase 3+4 会包装成异步任务（Redis 锁 + 后台派发），但底层调用仍走本类。
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
            redis_client: Redis 客户端（Phase 3+4 用，本阶段可缺省）
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

    def _get_llm_gateway(self):
        """延迟获取 LLM 网关单例，避免 import 时初始化"""
        if self._llm_gateway is not None:
            return self._llm_gateway
        from src.llm.gateway import llm_gateway as _gw
        self._llm_gateway = _gw
        return self._llm_gateway

    # ========== 触发判断 ==========

    def _should_compress(
        self,
        messages: List[Dict[str, Any]],
        model_limit: int,
    ) -> Tuple[bool, str]:
        """双阈值判断：token 主阈值（70%）或消息数兜底阈值（150）任一满足即触发。

        Args:
            messages: 当前会话 messages 数组（含所有角色）
            model_limit: 当前模型的上下文上限（token 数）

        Returns:
            (是否压缩, 触发原因字符串)
            原因格式：'token_threshold(cur/threshold, pct%)' 或 'message_threshold(cur/threshold)'
        """
        # 阈值 1（主阈值）：token 数达到模型上限的指定比例（默认 70%）
        token_count = count_tokens(messages)
        token_threshold = int(model_limit * self._settings.token_threshold_ratio)
        if token_count >= token_threshold:
            pct = token_count * 100 // model_limit if model_limit > 0 else 0
            return True, f"token_threshold({token_count}/{token_threshold}, {pct}%)"

        # 阈值 2（兜底）：消息条数达到阈值（默认 150）
        msg_threshold = self._settings.message_count_threshold
        if len(messages) >= msg_threshold:
            return True, f"message_threshold({len(messages)}/{msg_threshold})"

        return False, ""

    # ========== 分段 ==========

    def _split_messages(
        self,
        messages: List[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
        """分 HEADER / COMPRESS / TAIL 三段。

        - HEADER：前 header_keep 条，保留原文
        - TAIL：最近 tail_keep 条，按工具链边界对齐；TAIL 末尾必到 messages 末尾，无需向后扩展
        - COMPRESS：中间所有消息，进入摘要 LLM

        孤儿 tool 处理（P0-4）：如果 TAIL 开头是 tool 消息，向前回溯寻找配对的
        assistant(tool_calls)。若一直回溯到 HEADER 边界仍找不到配对（说明这是孤儿
        tool，前面没有对应的 tool_calls），则将这些孤儿 tool 从 messages 中移除
        并记录 warning，重新分段。避免孤儿 tool 落入 COMPRESS 区末尾导致摘要 LLM 报错。

        如果消息总数太少（≤ header_keep + tail_keep），COMPRESS 为空。

        Returns:
            (header, compress, tail) 三段
        """
        header_keep = self._settings.header_keep
        tail_keep = self._settings.tail_keep

        # 预处理：移除整段 messages 中所有「孤儿 tool」。
        # 孤儿 tool 定义：在 messages 中向前回溯，最近一个非 tool 消息不是带 tool_calls 的 assistant。
        # 这种 tool 没有对应的 tool_calls，LLM 会拒绝（"messages must contain tool_calls"）。
        messages = self._drop_orphan_tool_messages(messages)
        total = len(messages)

        # 总数太少，全部保留，COMPRESS 区为空
        if total <= header_keep + tail_keep:
            return list(messages), [], []

        # ---------- HEADER ----------
        header = messages[:header_keep]

        # ---------- TAIL 边界对齐 ----------
        # TAIL 末尾固定到 messages 末尾（tail_keep 条），无需向后扩展。
        # （设计文档 §3.2 原本允许 TAIL 向后扩展到工具链闭合，但当前实现 TAIL 末尾 == total，
        #  不存在「后续还有 tool 结果在 TAIL 外」的场景。若未来 tail 不固定到末尾，需重新实现。）
        tail_start_idx = total - tail_keep

        # 向前对齐：如果 TAIL 第一条是 tool，向前回溯把对应的 assistant(tool_calls) 拉进 TAIL。
        # 由于 _drop_orphan_tool_messages 已保证每个 tool 前面必能找到 assistant(tool_calls)，
        # 这里回溯最多到 header_keep 边界，一定能找到配对（否则该 tool 已被丢弃）。
        while tail_start_idx > header_keep:
            cur = messages[tail_start_idx]
            role = cur.get("role")
            if role == "tool":
                tail_start_idx -= 1
                continue
            if role == "assistant" and cur.get("tool_calls"):
                break
            break

        tail = messages[tail_start_idx:total]
        compress = messages[header_keep:tail_start_idx]
        return header, compress, tail

    @staticmethod
    def _drop_orphan_tool_messages(
        messages: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """移除孤儿 tool 消息（P0-4）。

        遍历 messages，对每条 role=tool 的消息向前回溯：
        - 若最近一个非 tool 消息是 assistant(tool_calls) → 配对成功，保留
        - 否则（user、无 tool_calls 的 assistant，或回溯到头部）→ 孤儿 tool，移除

        连续多个 tool 结果可能对应同一次 tool_calls（multi-tool 场景），所以回溯时
        允许跨过其他 tool 消息。

        Returns:
            清理后的 messages（可能与入参相同，也可能是新列表）
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
                if prev_role == "assistant" and messages[j].get("tool_calls"):
                    paired = True
                break
            if not paired:
                orphan_indices.add(i)

        if not orphan_indices:
            return messages

        logger.warning(
            f"orphan tool message dropped: count={len(orphan_indices)}, "
            f"total_msgs={len(messages)}, indices={sorted(orphan_indices)}"
        )
        return [m for i, m in enumerate(messages) if i not in orphan_indices]

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

输出格式（严格遵守）：

## 用户与背景
- 用户的身份、任务背景、长期约束

## 关键事实与决策
- 已确认的事实、已做的决策（含决策原因）

## 已完成的任务
- 已经做完的事情，含产出物路径（重要！）

## 进行中的事项
- 尚未完成的任务、待跟进的待办

## 关键文件与资源
- 涉及的文件路径、订单号、客户 ID 等（必须保留原文）

## 用户偏好
- 沟通风格、格式偏好等

要求：
1. 每个字段只写必要条目，不要扩写
2. 文件路径、ID、URL 必须保留原文，不要改写
3. 总长度不超过 {max_tokens} tokens

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

        三步一个事务：
          ① INSERT chat_context_summaries（status='active'），summary_version 由
             INSERT...SELECT COALESCE(MAX(version),0)+1 单 SQL 计算（P1-3，避免竞态）
          ② UPDATE 旧 active summary → status='superseded'
          ③ UPDATE chat_messages SET compacted=true, compacted_by=summary_id WHERE id IN (...)
        任一步失败 → 整个事务回滚，对外抛异常。

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

                # ④ UPDATE chat_messages.compacted = true
                if compressed_message_ids:
                    cursor.execute(
                        f"""
                        UPDATE chat_messages
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

    # ========== 同步入口 ==========

    async def compress_now(
        self,
        session_id: str,
        source_type: str,
        tenant_id: Optional[str],
        user_id: Optional[str],
        subagent_id: Optional[str],
        model_limit: int,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """同步执行上下文压缩（Phase 1+2 提供给 Phase 6 集成用的入口，也是测试入口）。

        串联：触发判断 → 分段 → 预处理 → 摘要 LLM（失败则降级）→ 原子事务持久化。

        Args:
            session_id: 会话 ID
            source_type: 来源类型
            tenant_id / user_id / subagent_id: 租户/用户/子智能体
            model_limit: 模型上下文上限（token 数）
            messages: 当前 messages 数组，缺省时从 DB 读取（本阶段不实现，必须传入）

        Returns:
            dict: {
                "compressed": bool, 是否实际执行了压缩,
                "reason": str, 触发原因或跳过原因,
                "summary_id": Optional[str], 新 summary_id（未压缩时为 None）,
                "fallback_used": bool, 是否走了降级路径,
                "compressed_message_count": int,
                "original_token_count": int,
                "compressed_token_count": int,
            }
        """
        if messages is None:
            # Phase 6 集成时由 agent 传入；本阶段不实现自动从 DB 重建逻辑
            return {
                "compressed": False,
                "reason": "no messages provided",
                "summary_id": None,
                "fallback_used": False,
                "compressed_message_count": 0,
                "original_token_count": 0,
                "compressed_token_count": 0,
            }

        # 1) 触发判断
        should, reason = self._should_compress(messages, model_limit)
        if not should:
            return {
                "compressed": False,
                "reason": reason or "below threshold",
                "summary_id": None,
                "fallback_used": False,
                "compressed_message_count": 0,
                "original_token_count": count_tokens(messages),
                "compressed_token_count": count_tokens(messages),
            }

        # 2) 分段（含孤儿 tool 清理）
        header, compress, tail = self._split_messages(messages)
        if not compress:
            # 中间段为空，无需压缩
            return {
                "compressed": False,
                "reason": "compress_section_empty",
                "summary_id": None,
                "fallback_used": False,
                "compressed_message_count": 0,
                "original_token_count": count_tokens(messages),
                "compressed_token_count": count_tokens(messages),
            }

        # 3) 预处理 COMPRESS 区
        processed_compress = self._preprocess_tool_results(compress)

        # 4) 摘要 LLM（失败 → 降级）
        existing_summary = self.get_active_summary(
            session_id, source_type, tenant_id=tenant_id
        )
        summary_text = await self._call_summary_llm(existing_summary, processed_compress)
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

        # 5) 收集被压缩消息的 BIGINT id
        compressed_ids: List[int] = []
        for m in compress:
            mid = m.get("id")
            if mid is not None:
                try:
                    compressed_ids.append(int(mid))
                except (TypeError, ValueError):
                    continue

        # 6) 计算 token 数
        original_token_count = count_tokens(messages)
        compressed_token_count = count_tokens(header) + count_text_tokens(summary_text) + count_tokens(tail)

        # 7) 原子事务持久化
        summary_id = self._persist_atomically(
            session_id=session_id,
            source_type=source_type,
            tenant_id=tenant_id,
            user_id=user_id,
            subagent_id=subagent_id,
            summary_text=summary_text,
            compressed_message_ids=compressed_ids,
            original_token_count=original_token_count,
            compressed_token_count=compressed_token_count,
            fallback_used=fallback_used,
            llm_tokens_used=llm_tokens_used,
        )

        return {
            "compressed": True,
            "reason": reason,
            "summary_id": summary_id,
            "fallback_used": fallback_used,
            "compressed_message_count": len(compressed_ids),
            "original_token_count": original_token_count,
            "compressed_token_count": compressed_token_count,
        }

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
