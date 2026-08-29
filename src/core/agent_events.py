"""Agent 事件模块 — 统一的事件格式和辅助函数"""

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


def make_event(event_type: str, **kwargs) -> Dict[str, Any]:
    """创建标准 Agent 事件"""
    event = {"type": event_type, "timestamp": int(time.time() * 1000)}
    event.update(kwargs)
    return event


def extract_downloadable_file(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """从成功的工具结果事件中提取可下载文件，不依赖工具名称。"""
    if event.get("type") != "tool_result" or event.get("success") is not True:
        return None
    result = event.get("result")
    if not isinstance(result, dict):
        return None
    if not result.get("file_id") or result.get("visible") is False:
        return None
    return {
        "file_id": result["file_id"],
        "file_name": (
            result.get("download_file_name")
            or result.get("file_name")
            or "未命名文件"
        ),
        "file_size": result.get("file_size", 0),
        "download_url": result.get("download_url", ""),
        "mime_type": result.get("mime_type", ""),
    }


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


# ============== 工具参数脱敏 ==============

# 视为敏感、值需脱敏为 *** 的参数键名（统一小写 + 下划线归一后匹配）
SENSITIVE_ARG_KEYS = frozenset({
    "password", "passwd", "pwd",
    "api_key", "apikey",
    "token", "access_token",
    "secret", "secret_key",
    "authorization", "cookie", "cookies",
    "credential", "credentials",
    "private_key", "client_secret",
})

# 单字符串值超过该长度截断，避免文档正文等大段内容写入事件持久化链
MAX_ARG_VALUE_LENGTH = 200

# 嵌套深度上限，防止极端深层结构撑爆序列化
MAX_ARG_NEST_DEPTH = 8


def _is_sensitive_arg_key(key: str) -> bool:
    return key.lower().replace("-", "_") in SENSITIVE_ARG_KEYS


def mask_tool_args(args: Any, _depth: int = 0) -> Any:
    """递归脱敏工具参数副本，供 SSE / 会话 metadata / trace 使用。

    工具参数可能包含凭据（token/password/api_key）或文档正文、个人信息。
    原始参数只进入 LLM 上下文与工具执行；事件链路统一使用本函数脱敏后的
    副本，避免敏感信息落库。

    - 敏感键名（password/token/api_key/secret 等）的值替换为 ``***``
    - 超长字符串截断为前 MAX_ARG_VALUE_LENGTH 字符并附省略标记
    - 嵌套深度超过 MAX_ARG_NEST_DEPTH 时整体替换为省略标记

    返回新结构，不影响用于实际工具执行的原始参数。
    """
    if _depth > MAX_ARG_NEST_DEPTH:
        return "…(嵌套过深)"
    if isinstance(args, dict):
        return {
            key: ("***" if _is_sensitive_arg_key(key) else mask_tool_args(value, _depth + 1))
            for key, value in args.items()
        }
    if isinstance(args, list):
        return [mask_tool_args(item, _depth + 1) for item in args]
    if isinstance(args, str):
        if len(args) <= MAX_ARG_VALUE_LENGTH:
            return args
        return f"{args[:MAX_ARG_VALUE_LENGTH]}…(已截断，共 {len(args)} 字符)"
    return args


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


# ===== 工具结果内容处理纯函数 =====
# 以下三个函数均为与领域无关、无 I/O 的纯逻辑（输入 → 输出），自 agent.py 迁入；
# agent.py 保留 re-export，既有 import 路径与行为完全不变。


def _truncate_tool_content(
    content: str,
    max_chars: int = 8000,
    head_chars: int = 6000,
    threshold: int = 12000,
) -> str:
    """超长工具结果截断：>threshold 字符时保留 head_chars + (max_chars-head_chars) 尾部 + 省略标记。

    保头保尾策略：头部常含 summary/status，尾部常含关键数据（如 JSON 末尾、文件结尾），
    中间省略并明确标注，LLM 能感知结果被截断并主动缩小检索范围或告知用户。
    """
    if len(content) <= threshold:
        return content
    tail_chars = max_chars - head_chars
    return (
        content[:head_chars]
        + f"\n...[已截断：共 {len(content)} 字符，省略 {len(content) - head_chars - tail_chars} 字符]...\n"
        + content[-tail_chars:]
    )


def _extract_image_refs_from_tool_result(result: Any) -> List[Dict[str, Any]]:
    """从工具返回结果中提取所有 ImageRef dict。

    识别规则（约定优于类型约束，不修改 BaseTool）：
    - ``result["images"]``：list[dict]，每个 dict 含 file_id（顶层批量图）
    - ``result["cover_image"]``：单个 dict 或 None（顶层封面图）
    - ``result["results"]``：list，遍历每项的 ``cover_image`` 字段
      （attraction_search 这类返回列表的工具用此模式）

    Args:
        result: 工具 execute 返回值（通常是 dict，也可能是其他类型）

    Returns:
        ImageRef dict 列表（不含 None / 缺 file_id 的项）
    """
    refs: List[Dict[str, Any]] = []
    if not isinstance(result, dict):
        return refs

    # 顶层 images 键
    images_val = result.get("images")
    if isinstance(images_val, list):
        for img in images_val:
            if isinstance(img, dict) and img.get("file_id"):
                refs.append(img)

    # 顶层 cover_image 键
    cover = result.get("cover_image")
    if isinstance(cover, dict) and cover.get("file_id"):
        refs.append(cover)

    # results 列表中的 cover_image（attraction_search 模式）
    results_val = result.get("results")
    if isinstance(results_val, list):
        for item in results_val:
            if isinstance(item, dict):
                nested_cover = item.get("cover_image")
                if isinstance(nested_cover, dict) and nested_cover.get("file_id"):
                    refs.append(nested_cover)

    return refs


def _normalize_image_placement(refs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """给每个 ImageRef dict 补 placement 默认值（``after_text``）。

    Phase 2 不实现 inline 智能定位：``placement="inline"`` 也保留原值，
    前端 P2.7 会兜底按 after_text 渲染。空字符串/None 统一改为 after_text。
    """
    for ref in refs:
        if not ref.get("placement"):
            ref["placement"] = "after_text"
    return refs
