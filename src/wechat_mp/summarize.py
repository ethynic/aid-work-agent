"""公众号文章核心要点总结（WP12 入库内容质量优化；WP13 输入改为 content_md 全文）。

职责边界：只做「content_md 全文（# 标题 + 文本段落 + ![图片描述](CDN地址)）→
LLM → ≤500 字核心要点总结」；不做文档组装/落库（service.py 编排），不做计费落账
（service 调 record_background_llm_usage(source='wechat_mp_summary')）。

模型选择（WP12 定版）：``LLMGateway()`` 默认构造 = 主 provider 默认文本模型——
总结是文本任务，不 pin GLM VL 模型（与 vision.py 固定首选多模态的理由互为镜像）。

可靠性：单轮 user message；``asyncio.wait_for`` 超时 60s（gateway.chat 无
timeout 参数）；失败重试 1 次；两次都失败返回 None，由调用方回退 merged 原文
入库（不丢数据，metadata.summary_fallback=true）。
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

# ------------------------------- 常量 -------------------------------

# 一篇 URL = 一篇 ≤500 字核心要点总结（负责人要求，2026-09-15 agent2 实测反馈）
SUMMARY_MAX_CHARS = 500
# 计费来源标识（record_background_llm_usage 的 source；进 chat_records.session_id）
SUMMARY_SOURCE_TYPE = "wechat_mp_summary"
# 单次调用超时秒数（gateway.chat 无 timeout 参数，asyncio.wait_for 包裹）
SUMMARY_TIMEOUT_SECONDS = 60.0

# 总结指令（WP13 定版文案：图片转述与文字同等重要纳入总结 + 图文去重 + 无标签 +
# 不发挥；措辞可微调语义不变）
SUMMARY_INSTRUCTION = (
    "将以下公众号文章内容提炼为不超过500字的核心要点总结。"
    "图片转述内容与文字内容同等重要，关键信息需纳入总结；"
    "文字内容与图片转述内容如有重复只保留一份；"
    "直接输出总结正文，不要任何前缀、标签或标题行（如\"总结：\"）；"
    "不评价不发挥、不编造未提及的信息。"
)

# 模型偶发输出的标签前缀（指令已禁止，产出侧保守剥一层，思路对齐 vision.clean_description）
_SUMMARY_PREFIX_RE = re.compile(r"^(?:总结|核心要点|要点总结|摘要)[:：]\s*")


# ------------------------------- 指令与产出处理 -------------------------------


def build_summary_messages(merged_text: str, title: str) -> List[Dict[str, Any]]:
    """单轮 user message = 固定指令 + 标题 + content_md 全文（WP13：图片转述以
    Markdown 图片行进入，与文本同等参与总结）。

    标题仅作输入上下文帮助锚定主题；指令已禁止输出任何标签/标题行，
    content_md 亦原样进入（图片描述与文字去重交给模型按指令处理）。
    """
    sections = [SUMMARY_INSTRUCTION]
    if title:
        sections.append(title)
    if merged_text:
        sections.append(merged_text)
    return [{"role": "user", "content": "\n\n".join(sections)}]


def truncate_summary(text: str, max_chars: int = SUMMARY_MAX_CHARS) -> str:
    """超长保护：剥偶发标签前缀后按字符截断（不加省略号，简洁口径）。"""
    cleaned = _SUMMARY_PREFIX_RE.sub("", (text or "").strip(), count=1)
    return cleaned[:max_chars]


def extract_usage(result: Optional[Dict[str, Any]]) -> Dict[str, int]:
    """提取 provider 上报的 token 用量（对齐 vision._extract_usage 口径）。"""
    usage = (result or {}).get("usage") or {}
    return {
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or 0),
    }


# ------------------------------- 总结器 -------------------------------


class ArticleSummarizer:
    """文章总结器（异步；调用方为 service 管道）。

    测试可注入 ``gateway``（带 chat() 的网关替身）隔离真实 LLM。
    """

    def __init__(
        self,
        gateway: Any = None,
        timeout_seconds: float = SUMMARY_TIMEOUT_SECONDS,
        temperature: float = 0.2,
    ):
        self._gateway = gateway
        self._timeout_seconds = timeout_seconds
        self._temperature = temperature

    def _get_gateway(self):
        if self._gateway is None:
            from src.llm.gateway import LLMGateway

            # 默认构造 = 主 provider 默认文本模型（总结是文本任务不 pin VL 模型）
            self._gateway = LLMGateway()
        return self._gateway

    def resolve_model_name(self) -> str:
        """计费用模型名：网关实际使用的模型。

        LLMGateway() 未显式绑模型 → 主 provider 配置默认模型；替身网关无
        provider_name 时按配置主 provider 兜底；解析失败返回 "unknown"
        （单价查不到时计费侧降级 credit_cost=0，不阻断入库）。
        """
        try:
            from src.config.settings import settings

            gateway = self._get_gateway()
            provider = getattr(gateway, "provider_name", None) or settings.llm.provider
            cfg = getattr(settings.llm, provider, None)
            return str(getattr(cfg, "model", "") or "") or str(provider)
        except Exception:  # noqa: BLE001 模型名解析失败不阻断总结流程
            return "unknown"

    async def _summarize_once(
        self, merged_text: str, title: str
    ) -> Tuple[str, str, Dict[str, int]]:
        """单次调用（wait_for 超时包裹）；成功返回 (原始输出, 模型名, usage)，失败上抛。"""
        gateway = self._get_gateway()
        result = await asyncio.wait_for(
            gateway.chat(
                messages=build_summary_messages(merged_text, title),
                temperature=self._temperature,
            ),
            timeout=self._timeout_seconds,
        )
        content = str((result or {}).get("content") or "").strip()
        if not content:
            raise ValueError("总结返回空响应")
        return content, self.resolve_model_name(), extract_usage(result)

    async def summarize(
        self, merged_text: str, title: str
    ) -> Optional[Tuple[str, str, Dict[str, int]]]:
        """初次 + 重试 1 次；成功返回 (截断后总结, 模型名, usage)，两次都失败返回 None。

        日志只记异常类型名（不落响应内容/异常原文，防密钥或正文进日志）。
        """
        last_error: Optional[Exception] = None
        for attempt in (1, 2):
            try:
                content, model, usage = await self._summarize_once(merged_text, title)
                return truncate_summary(content), model, usage
            except Exception as exc:  # noqa: BLE001 单次失败重试，两次失败由调用方回退
                last_error = exc
                logger.bind(module="wechat_mp").warning(
                    "wechat_mp 文章总结第{}次尝试失败: {}", attempt, type(exc).__name__
                )
        logger.bind(module="wechat_mp").warning(
            "wechat_mp 文章总结两次尝试均失败（回退 merged 原文入库）: {}",
            type(last_error).__name__ if last_error else "unknown",
        )
        return None
