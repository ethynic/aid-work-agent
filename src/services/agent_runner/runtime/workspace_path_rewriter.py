"""跨轮 workspace 旧路径改写。

上一轮 execution 终态提交后其 skill 工作区被 cleanup_workspaces() 删除，但旧路径
字符串已持久化进会话历史（attachments 的 Full path 行、工具结果 JSON），下一轮
LLM 仍会看到并复用，导致 image_render 等工具首调失败一次才自纠。

本模块在 context_assembler 加载历史后、进入 LLM 前，对历史消息中的
`workspaces/{hex64}/skill_ws_` 形态绝对路径改写：
- 命中反查索引（ws_file_idx:{sha256(path)}）→ 替换为 file_id（副本仍在 TTL 内可用）
- 未命中 → 替换为失效标记，阻断模型继续引用

只改写发给 LLM 的上下文投影，不改 DB 历史原文（trace/审计依赖原文）。
"""

import asyncio
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from src.core.cache_utils import CacheKeys, get_cached, set_cached

# 快筛：廉价 str.search，未命中零 Redis 开销（分隔符兼容 / 与 \，防御性覆盖）
_FAST = re.compile(r'workspaces[/\\][0-9a-f]{64}[/\\]skill_ws_')

# 完整路径提取：绝对路径开头 + workspaces/{hex64}/skill_ws_ + 到边界字符止。
# 排除空白、引号/反引号、ASCII 与常见中文标点（files_context 的反引号包裹与
# JSON 字符串的 \" 边界天然被排除）
_PATH = re.compile(
    r'(?:[A-Za-z]:[\\/]|/)[^\s"\'`<>(){}\[\]，。；：！？（）【】《》「」、]*'
    r'workspaces[/\\][0-9a-f]{64}[/\\]skill_ws_'
    r'[^\s"\'`<>(){}\[\]，。；：！？（）【】《》「」、]*'
)

# 失效标记中文件名的危险字符过滤（保证替换值不含 JSON 破坏字符）
_UNSAFE_NAME = re.compile(r'["\\\x00-\x1f]')

_INDEX_TTL = 86400  # 与 ImageRegistry.DEFAULT_TTL 对齐：file_id 副本过期后索引同步失效


def is_workspace_path(item: str) -> bool:
    """判定字符串是否为 runner 工作区产物绝对路径（供工具报错文案复用）。"""
    return bool(_FAST.search(item)) and bool(_PATH.search(item))


def index_workspace_file(path: str, file_id: str, name: str, tenant_id: str) -> None:
    """登记 workspace 产物 path -> file_id 反查索引（注册 file_id 时调用）。

    失败仅 warning 不阻断注册主流程（改写侧降级为失效标记，方向安全）。
    """
    try:
        set_cached(
            CacheKeys.WORKSPACE_FILE_INDEX,
            hashlib.sha256(str(path).encode()).hexdigest(),
            value=json.dumps({
                "file_id": file_id,
                "name": name,
                "tenant_id": tenant_id,
                "registered_at": datetime.now().isoformat(),
            }, ensure_ascii=False),
            ttl=_INDEX_TTL,
        )
    except Exception:
        logger.opt(exception=True).warning("[workspace_path_rewriter] 反查索引写入失败")


def _stale_marker(path: str) -> str:
    name = _UNSAFE_NAME.sub('', Path(path).name)
    return (
        f"[已失效:临时文件 {name} 随上一轮工作区清理删除,请勿引用该路径;"
        f"图片可改用会话中工具结果里的 file_id 或请用户重新上传]"
    )


def _lookup_mapping(paths: List[str], tenant_id: str) -> Dict[str, str]:
    """批量反查 path -> file_id；Redis 异常时全部按未命中处理。"""
    mapping: Dict[str, str] = {}
    for path in paths:
        try:
            raw = get_cached(
                CacheKeys.WORKSPACE_FILE_INDEX,
                hashlib.sha256(path.encode()).hexdigest(),
            )
            if not raw:
                continue
            data = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(data, dict) or not data.get("file_id"):
                continue
            if data.get("tenant_id") and data["tenant_id"] != tenant_id:
                continue  # 纵深防御：tenant 不一致按未命中降级
            mapping[path] = data["file_id"]
        except Exception:
            logger.opt(exception=True).warning(
                "[workspace_path_rewriter] 反查索引读取失败，按未命中降级")
    return mapping


def _rewrite_text(text: str, tenant_id: str) -> Optional[str]:
    """改写单段文本；无 workspace 路径返回 None（避免无谓 copy）。"""
    if not _FAST.search(text):
        return None
    paths = list(dict.fromkeys(_PATH.findall(text)))
    if not paths:
        return None
    mapping = _lookup_mapping(paths, tenant_id)
    changed = False
    for path in paths:
        replacement = mapping.get(path) or _stale_marker(path)
        if replacement != path:
            text = text.replace(path, replacement)
            changed = True
    return text if changed else None


def _rewrite_content(content: Any, tenant_id: str) -> Any:
    """改写消息 content（str 或 parts list）；未变化原样返回（含 None 哨兵）。"""
    if isinstance(content, str):
        new_text = _rewrite_text(content, tenant_id)
        return content if new_text is None else new_text
    if isinstance(content, list):
        changed = False
        new_parts = list(content)
        for i, part in enumerate(content):
            if isinstance(part, dict) and part.get("type") == "text" \
                    and isinstance(part.get("text"), str):
                new_text = _rewrite_text(part["text"], tenant_id)
                if new_text is not None:
                    new_parts[i] = {**part, "text": new_text}
                    changed = True
        return new_parts if changed else content
    return content


def rewrite_history_workspace_paths_sync(messages: List[Dict[str, Any]],
                                         tenant_id: str) -> List[Dict[str, Any]]:
    """改写同步核心（含同步 Redis 反查，调用方须在 to_thread 中执行）。"""
    if not messages:
        return messages
    result = None  # 惰性建新 list：全部未命中时原样返回，零 copy
    for i, message in enumerate(messages):
        content = message.get("content")
        if not isinstance(content, (str, list)):
            continue
        new_content = _rewrite_content(content, tenant_id)
        if new_content is content or new_content == content:
            if result is not None:
                result.append(message)
            continue
        if result is None:
            result = list(messages[:i])
        new_message = dict(message)
        new_message["content"] = new_content
        result.append(new_message)
    return result if result is not None else messages


async def rewrite_history_workspace_paths(messages: List[Dict[str, Any]],
                                          tenant_id: str) -> List[Dict[str, Any]]:
    """改写历史消息中的跨轮 workspace 旧路径（异步入口，同步核心走线程池）。

    调用时序要求：必须在 context_assembler 的 get_context 之后、append
    incoming/resumed_tool 之前调用，保证当轮有效路径不被误改。

    改写语义：返回新 list，被改写的消息 dict 与 content parts 必须 copy
    （ShortTermMemory.get_context 返回内存缓存共享引用，禁止就地修改）。
    """
    return await asyncio.to_thread(rewrite_history_workspace_paths_sync, messages, tenant_id)
