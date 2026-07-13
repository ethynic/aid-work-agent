"""通用图片 inliner：扫描文本中的图片引用，下载/解析为本地路径。

设计文档：docs/system/image-asset-pipeline-design.md §6.1
开发计划：docs/plans/plan-image-asset-pipeline.md P1.4

Phase 1 支持两类引用（Markdown syntax）：
  ![alt](file_id:file_xxx)   → ImageRef 直接引用（零拷贝，最快）
  ![alt](https://...)        → fetch_to_local 下载并注册

替换失败（找不到 file_id / 下载失败）时不抛异常，保留原文，记 warning，
继续处理其他图片，避免单张图阻断整篇文档生成。

多张图并发处理（asyncio.gather），加速文档生成。
"""

import asyncio
import re
from pathlib import Path
from typing import Awaitable, Callable, List, Match, Optional, Tuple

from loguru import logger

from src.core.image_asset import ImageRef, get_image_registry


# ============================================================
# 正则：识别 Markdown 中的图片引用
# ============================================================

# ![alt](file_id:file_xxx) — file_id 只允许小写字母/数字/下划线
_FILE_ID_PATTERN = re.compile(r'!\[([^\]]*)\]\(file_id:([a-z0-9_]+)\)')

# ![alt](https://...) / ![alt](http://...) — 远程 URL
_REMOTE_URL_PATTERN = re.compile(r'!\[([^\]]*)\]\((https?://[^\s)]+)\)')


# ============================================================
# 异步正则替换辅助
# ============================================================

async def _are_sub(
    pattern: re.Pattern,
    async_replacer: Callable[[Match], Awaitable[str]],
    text: str,
) -> str:
    """异步正则替换。

    标准 `re.sub` 不支持 async replace 函数，需要自己实现：
      1. 用 pattern.finditer 收集所有 match
      2. 用 asyncio.gather 并发执行 async_replacer（多张图加速）
      3. 重组文本：text[:m0.start()] + new0 + text[m0.end():m1.start()] + new1 + ...

    Args:
        pattern: 编译好的正则
        async_replacer: 异步替换函数，接收 Match，返回替换后的字符串
        text: 原始文本

    Returns:
        替换后的文本；无 match 时原样返回
    """
    matches = list(pattern.finditer(text))
    if not matches:
        return text

    # 并发执行所有替换
    new_parts = await asyncio.gather(
        *(async_replacer(m) for m in matches),
        return_exceptions=False,  # 单张图失败由 async_replacer 自己 catch，不向上抛
    )

    # 重组文本（索引必须严格对齐 match.start/end）
    result: List[str] = []
    last_end = 0
    for m, new_text in zip(matches, new_parts):
        result.append(text[last_end:m.start()])
        result.append(new_text)
        last_end = m.end()
    result.append(text[last_end:])
    return "".join(result)


# ============================================================
# 对外入口：inline_images
# ============================================================

async def inline_images(
    text: str,
    tenant_id: str,
    user_id: Optional[str] = None,
    syntax: str = "markdown",
    fetch_remote: bool = True,
) -> Tuple[str, List[ImageRef]]:
    """扫描文本中的图片引用，下载/解析为本地路径。

    Phase 1 支持的引用形式（markdown syntax）：
      ![alt](file_id:file_xxx)   → registry.get_ref_by_file_id + resolve_local_path
      ![alt](https://...)        → registry.fetch_to_local + resolve_local_path

    Args:
        text: 原始 Markdown 文本
        tenant_id: 租户 ID（远程 URL 下载时必填）
        user_id: 用户 ID（远程 URL 下载时附带）
        syntax: 语法类型，目前仅支持 "markdown"；"html" 在 Phase 1 不支持，
                会记 warning 并按 markdown 处理（保守降级）
        fetch_remote: 是否处理远程 URL（默认 True；False 时仅替换 file_id:，
                      远程 URL 保持原样）

    Returns:
        (处理后的文本, ImageRef 列表)
        - 处理后的文本：所有命中的 src 被替换为本地绝对路径字符串
        - ImageRef 列表：按 file_id 去重后的所有命中图片
        - 单张图失败时保留原文，不抛异常
    """
    if not text:
        return text, []

    # HTML syntax 在 Phase 1 不支持（旅游顾问走 Markdown）
    if syntax != "markdown":
        logger.warning(
            f"[image_inliner] syntax={syntax} 暂不支持，按 markdown 处理"
        )

    refs: List[ImageRef] = []
    seen_file_ids: set = set()
    registry = get_image_registry()

    def _add_ref(ref: Optional[ImageRef]) -> None:
        """去重追加 ImageRef 到 refs 列表。"""
        if ref is None:
            return
        if ref.file_id in seen_file_ids:
            return
        seen_file_ids.add(ref.file_id)
        refs.append(ref)

    # --------------------------------------------------------
    # 1. 替换 file_id: scheme
    # --------------------------------------------------------
    async def _replace_file_id(m: Match) -> str:
        alt, file_id = m.group(1), m.group(2)
        try:
            ref = await registry.get_ref_by_file_id(file_id)
            if ref is None:
                logger.warning(
                    f"[image_inliner] file_id={file_id} 在 registry 中找不到，保留原文"
                )
                return m.group(0)
            local_path = await registry.resolve_local_path(ref)
            _add_ref(ref)
            return f"![{alt}]({local_path})"
        except Exception as e:
            logger.warning(
                f"[image_inliner] 解析 file_id={file_id} 失败，保留原文: {e}"
            )
            return m.group(0)

    text = await _are_sub(_FILE_ID_PATTERN, _replace_file_id, text)

    # --------------------------------------------------------
    # 2. 替换远程 URL（可选）
    # --------------------------------------------------------
    if fetch_remote:
        async def _replace_remote(m: Match) -> str:
            alt, url = m.group(1), m.group(2)
            try:
                ref = await registry.fetch_to_local(
                    url,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    display_name=alt or None,
                )
                local_path = await registry.resolve_local_path(ref)
                _add_ref(ref)
                return f"![{alt}]({local_path})"
            except Exception as e:
                logger.warning(
                    f"[image_inliner] 下载 url={url} 失败，保留原文: {e}"
                )
                return m.group(0)

        text = await _are_sub(_REMOTE_URL_PATTERN, _replace_remote, text)

    return text, refs


__all__ = ["inline_images"]
