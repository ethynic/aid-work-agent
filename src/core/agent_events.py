"""Agent 事件模块 — 统一的事件格式和辅助函数"""

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


def make_event(event_type: str, **kwargs) -> Dict[str, Any]:
    """创建标准 Agent 事件"""
    event = {"type": event_type, "timestamp": int(time.time() * 1000)}
    event.update(kwargs)
    return event


def make_image_event(
    images: List[Dict[str, Any]],
    placement: str = "after_text",
) -> Dict[str, Any]:
    """构造 images SSE 事件（Phase 2 P2.1）。

    用于 Agent 主循环在工具结果中识别到 ImageRef 后推送图片到前端。
    与 response / tool_result 等事件并列，前端独立处理。

    Args:
        images: ImageRef 字典列表（已经是 model_dump() 后的纯 dict，便于 JSON 序列化）
        placement: 图片在前端消息中的展示位置
            - ``after_text``（默认）：文本下方画廊（最常见）
            - ``before_text``：文本上方（如景点封面图先看图再看介绍）
            - ``inline``：文本流中行内（Phase 2 仍按 after_text 渲染，Phase 3 实现精确行内）

    Returns:
        SSE 事件 dict，结构::

            {
                "type": "images",
                "timestamp": int,
                "images": [...],
                "placement": "after_text" | "before_text" | "inline",
            }
    """
    return make_event("images", images=list(images or []), placement=placement)


@dataclass
class ContextCompressedEvent:
    """上下文压缩完成事件（Phase 7 §7.1）。

    由 Agent._run_compression_phase 在 compress_now 成功后构造，转发给
    TraceCollector，在 trace 页面展示为紫色 span，让运维一眼看到「本次对话
    触发了一次压缩」。

    字段说明：
    - summary_id: 新创建的摘要 ID（csum_xxx）
    - compressed_message_count: 被压缩的原消息数
    - original_token_count / compressed_token_count: 压缩前后 token 数
    - compression_ratio: 压缩比（compressed / original）
    - fallback_used: 是否走了降级路径（LLM 调用失败时为 True）
    - trigger_reason: 触发原因（force / token_threshold(...) / message_threshold(...)）
    - llm_provider / llm_model: 实际调用的摘要 LLM
    - duration_ms: compress_now 总耗时（含 LLM 调用）
    """
    # 注意：事件流转通过 make_event 字典，不直接序列化 dataclass。
    # 此类作为字段约束文档 + 类型提示，构造时调用 to_dict() 转字典。
    event_type: str = "context_compressed"
    summary_id: str = ""
    compressed_message_count: int = 0
    original_token_count: int = 0
    compressed_token_count: int = 0
    compression_ratio: float = 0.0
    fallback_used: bool = False
    trigger_reason: str = ""
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None
    duration_ms: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """转为通过 SSE / trace 流转发的事件字典。

        顶层带 `type` 和 `timestamp` 字段，与 TraceCollector.on_event
        约定的事件格式保持一致。
        """
        return {
            "type": self.event_type,
            "timestamp": int(time.time() * 1000),
            "summary_id": self.summary_id,
            "compressed_message_count": self.compressed_message_count,
            "original_token_count": self.original_token_count,
            "compressed_token_count": self.compressed_token_count,
            "compression_ratio": self.compression_ratio,
            "fallback_used": self.fallback_used,
            "trigger_reason": self.trigger_reason,
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model,
            "duration_ms": self.duration_ms,
        }
