"""
大内容落盘管理器

把工具（http_api/pdf/ocr 等）产生的大文本内容落盘到临时文件，返回截断预览 +
落盘路径，供 LLM 通过 read/grep 按需回读被截断部分，消除「信息黑洞」。

设计依据：docs/tools/large-content-retrieval-design.md 第 3 节。

核心原则：
1. 本函数不判断内容是否够大——调用方自己判断 >5000 字符才调用，避免小响应也落盘。
2. 落盘目录用 tempfile.gettempdir() 下的子目录，与 read 工具路径白名单兼容
   （read 已允许整个临时目录），无需改 read。
3. 启动时调 cleanup_stale_spill_files 清理老文件，依赖 OS tmp 清理兜底。

本模块只依赖标准库 + loguru + _helpers.truncate_text，不拉起 src 的 db/config/agent
链，避免循环导入或启动开销。
"""

import json
import os
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger

from src.tools._helpers import truncate_text

__all__ = ["spill_large_content", "cleanup_stale_spill_files", "SPILL_DIR_NAME"]

# 预览默认长度（与 truncate_text 配套，调用方可覆盖）
PREVIEW_LIMIT = 5000

# 落盘子目录名（位于系统临时目录下）
SPILL_DIR_NAME = "aid_agent_spill"


def _get_spill_dir() -> Path:
    """获取落盘目录（不存在则创建）。"""
    spill_dir = Path(tempfile.gettempdir()) / SPILL_DIR_NAME
    spill_dir.mkdir(parents=True, exist_ok=True)
    return spill_dir


def spill_large_content(
    content: str,
    *,
    prefix: str = "tool_response_",
    suffix: str = ".txt",
    meta: Optional[dict] = None,
) -> dict:
    """将大文本内容落盘到临时文件，返回供 LLM 回读的结构。

    本函数不做大小判断（调用方判断 >5000 字符后才调用）。落盘后返回截断预览 +
    绝对路径，调用方据 `file_path` + `truncated` 标记决定是否提示 LLM 回读。

    Args:
        content: 要落盘的完整文本内容。
        prefix: 文件名前缀，用于标识来源工具（如 ``httpapi_response_``），便于排查。
        suffix: 文件名后缀（含扩展名，如 ``.json``、``.txt``）。
        meta: 可选元信息（如原 URL、OCR 页数），写入同名 ``.meta.json``，
            供 grep 旁路参考，不进返回值。为空则不写 meta 文件。

    Returns:
        ``{"file_path": <绝对路径>, "full_size": <字符数>,
        "preview": <前 5000 字符>, "truncated": True}``。
        其中 ``truncated`` 反映 preview 是否被截断（内容确实 >5000 字符时为 True）。
    """
    if content is None:
        content = ""

    full_size = len(content)

    # 截断预览（前 PREVIEW_LIMIT 字符）
    preview, truncated = truncate_text(content, PREVIEW_LIMIT)

    # 文件名：{prefix}{YYYYMMDDHHmmss}_{8位uuid}{suffix}
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    short_uuid = uuid.uuid4().hex[:8]
    filename = f"{prefix}{timestamp}_{short_uuid}{suffix}"

    spill_dir = _get_spill_dir()
    file_path = spill_dir / filename

    # 落盘完整内容（注意：用 encoding 保留 unicode，errors 不替换，避免静默损坏；
    # 极端情况下编码失败应抛错让调用方知道，而不是落一个乱码文件）
    try:
        file_path.write_text(content, encoding="utf-8")
    except Exception:
        # 兜底：极少数 surrogate 字符无法编码时，用 replace 防止整个调用挂掉
        logger.warning(f"落盘内容 UTF-8 编码失败，启用 replace 兜底: {file_path}")
        file_path.write_text(content, encoding="utf-8", errors="replace")

    # 写 meta（同名 .meta.json）
    if meta:
        meta_path = file_path.with_name(file_path.name + ".meta.json")
        try:
            meta_path.write_text(
                json.dumps(meta, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            # meta 写失败不影响主流程，只记日志
            logger.warning(f"写 meta 文件失败（不影响落盘）: {e}")

    logger.debug(
        f"大内容已落盘: {file_path} (full_size={full_size}, truncated={truncated})"
    )

    return {
        "file_path": str(file_path.resolve()),
        "full_size": full_size,
        "preview": preview,
        # 反映 preview 是否被截断（truncate_text 真实返回值）。
        # 调用方一般在内容 >PREVIEW_LIMIT 时才调本函数，此时 truncated 多为 True；
        # 但若调用方对小内容也调用，truncated 会是 False，preview 即完整内容。
        "truncated": truncated,
    }


def cleanup_stale_spill_files(max_age_hours: int = 24) -> int:
    """清理 spill 目录下过期的落盘文件。

    遍历 ``aid_agent_spill`` 目录，删除 mtime 超过 ``max_age_hours`` 的文件。
    用于应用启动时调用，防止临时文件无限增长。

    注意：清理范围严格限定在 spill 子目录内，不会触碰系统临时目录的其他文件。
    meta 文件（``*.meta.json``）与其主文件可能单独过期，一并清理。

    Args:
        max_age_hours: 文件最大存活小时数，超过则删除。默认 24 小时。

    Returns:
        实际删除的文件数。
    """
    spill_dir = Path(tempfile.gettempdir()) / SPILL_DIR_NAME
    if not spill_dir.exists():
        return 0

    # 当前时间戳
    now_ts = datetime.now().timestamp()
    age_seconds = max_age_hours * 3600

    deleted = 0
    for entry in spill_dir.iterdir():
        # 只删文件，不递归子目录（spill 目录结构是扁平的）
        if not entry.is_file():
            continue
        try:
            mtime = entry.stat().st_mtime
            if (now_ts - mtime) > age_seconds:
                entry.unlink()
                deleted += 1
        except OSError as e:
            # 单个文件删除失败（如权限/占用）不影响其他文件清理
            logger.warning(f"清理 spill 文件失败 {entry}: {e}")
            continue

    if deleted > 0:
        logger.info(f"清理过期落盘文件: 删除 {deleted} 个（>{max_age_hours}h）")

    return deleted
