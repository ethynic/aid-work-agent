"""通用图片 inliner：扫描文本中的图片引用，下载/解析为本地路径。

设计文档：docs/system/image-asset-pipeline-design.md §6.1
开发计划：docs/plans/plan-image-asset-pipeline.md P1.4（markdown）/ Phase 3（html）

支持的引用形式：

Markdown syntax（Phase 1，md_to_word / md_to_pdf 走此路径）：
  ![alt](file_id:file_xxx)   → ImageRef 直接引用（零拷贝，最快）
  ![alt](https://...)        → fetch_to_local 下载并注册

HTML syntax（Phase 3，html_to_pdf 走此路径）：
  <img ... src="file_id:file_xxx" ...>   → 解析 file_id 为本地路径
  <img ... src="https://..." ...>        → fetch_to_local 下载并注册
  仅替换 src 值，保留 <img> 标签其余属性。

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

# <img ... src="file_id:file_xxx" ...> — HTML img file_id（Phase 3）
# group(1)=`<img ... src=`，group(2)=引号，group(3)=file_id；\2 反向引用保证引号配对
_HTML_IMG_FILE_ID_PATTERN = re.compile(
    r'(<img\b[^>]*?\bsrc=)(["\'])file_id:([a-z0-9_]+)\2',
    re.IGNORECASE,
)

# <img ... src="https://..." ...> — HTML img 远程 URL（Phase 3）
_HTML_IMG_REMOTE_PATTERN = re.compile(
    r'(<img\b[^>]*?\bsrc=)(["\'])(https?://[^\s"\']+)\2',
    re.IGNORECASE,
)


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

    支持的引用形式：
      markdown syntax（默认）：
        ![alt](file_id:file_xxx)   → registry.get_ref_by_file_id + resolve_local_path
        ![alt](https://...)        → registry.fetch_to_local + resolve_local_path
      html syntax（Phase 3，pdf_process.html_to_pdf 接入）：
        <img ... src="file_id:file_xxx" ...>   → 同上，仅替换 src 值
        <img ... src="https://..." ...>        → 同上，仅替换 src 值

    Args:
        text: 原始文本（Markdown 或 HTML）
        tenant_id: 租户 ID（远程 URL 下载时必填）
        user_id: 用户 ID（远程 URL 下载时附带）
        syntax: 语法类型，"markdown"（默认）或 "html"；其它值记 warning 后按 markdown 处理
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

    # 仅支持 markdown / html；其它值保守降级为 markdown
    if syntax not in ("markdown", "html"):
        logger.warning(
            f"[image_inliner] syntax={syntax} 暂不支持，按 markdown 处理"
        )
        syntax = "markdown"

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

    if syntax == "markdown":
        # ----------------------------------------------------
        # Markdown：1. 替换 file_id: scheme
        # ----------------------------------------------------
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

        # ----------------------------------------------------
        # Markdown：2. 替换远程 URL（可选）
        # ----------------------------------------------------
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

    else:  # syntax == "html"（Phase 3）
        # ----------------------------------------------------
        # HTML：1. 替换 <img src="file_id:...">
        # ----------------------------------------------------
        async def _replace_img_file_id(m: Match) -> str:
            prefix, quote, file_id = m.group(1), m.group(2), m.group(3)
            try:
                ref = await registry.get_ref_by_file_id(file_id)
                if ref is None:
                    logger.warning(
                        f"[image_inliner] file_id={file_id} 在 registry 中找不到，保留原文"
                    )
                    return m.group(0)
                local_path = await registry.resolve_local_path(ref)
                _add_ref(ref)
                # 仅替换 src 值，保留 <img ...> 标签其余属性
                return f"{prefix}{quote}{local_path}{quote}"
            except Exception as e:
                logger.warning(
                    f"[image_inliner] 解析 file_id={file_id} 失败，保留原文: {e}"
                )
                return m.group(0)

        text = await _are_sub(_HTML_IMG_FILE_ID_PATTERN, _replace_img_file_id, text)

        # ----------------------------------------------------
        # HTML：2. 替换 <img src="https://...">（可选）
        # ----------------------------------------------------
        if fetch_remote:
            async def _replace_img_remote(m: Match) -> str:
                prefix, quote, url = m.group(1), m.group(2), m.group(3)
                try:
                    ref = await registry.fetch_to_local(
                        url,
                        tenant_id=tenant_id,
                        user_id=user_id,
                        display_name=None,
                    )
                    local_path = await registry.resolve_local_path(ref)
                    _add_ref(ref)
                    return f"{prefix}{quote}{local_path}{quote}"
                except Exception as e:
                    logger.warning(
                        f"[image_inliner] 下载 url={url} 失败，保留原文: {e}"
                    )
                    return m.group(0)

            text = await _are_sub(_HTML_IMG_REMOTE_PATTERN, _replace_img_remote, text)

    return text, refs


# ============================================================
# 对外入口：inline_images_as_data_uri（x-to-image HTML 场景专用）
# ============================================================

# 单文件转 base64 的体积上限（10MB），避免把超大图内联进 HTML 撑爆内存/长图
_DATA_URI_MAX_BYTES = 10 * 1024 * 1024


def _file_to_data_uri(local_path: str, mime_type: Optional[str]) -> Optional[str]:
    """读本地图片文件 → base64 data URI。

    供 inline_images_as_data_uri 使用：x-to-image 的 headless Chromium 对
    set_content 页面（origin=about:blank）禁止加载本地文件，必须把图片
    内联成 data URI 才能渲染。

    Args:
        local_path: 本地图片绝对路径
        mime_type: MIME 类型（取自 ImageRef.mime_type），None 兜底 image/png

    Returns:
        data URI 字符串；文件不存在/超限/读取失败时返回 None（调用方保留原 src）
    """
    import base64
    try:
        p = Path(local_path)
        if not p.exists():
            logger.warning(f"[image_inliner] data URI 转换：文件不存在 {local_path}")
            return None
        size = p.stat().st_size
        if size > _DATA_URI_MAX_BYTES:
            logger.warning(
                f"[image_inliner] data URI 转换：文件超限({size} > {_DATA_URI_MAX_BYTES})，跳过 {local_path}"
            )
            return None
        data = p.read_bytes()
        b64 = base64.b64encode(data).decode()
        return f"data:{mime_type or 'image/png'};base64,{b64}"
    except Exception as e:
        logger.warning(f"[image_inliner] data URI 转换失败 {local_path}: {e}")
        return None


async def inline_images_as_data_uri(
    text: str,
    tenant_id: str,
    user_id: Optional[str] = None,
    fetch_remote: bool = True,
) -> Tuple[str, List[ImageRef]]:
    """把 HTML 中的 <img src="file_id:xxx"> / <img src="https://..."> 解析为 base64 data URI。

    与 inline_images() 的区别：输出 **base64 data URI**（而非本地路径）。
    专供 x-to-image 的 set_content 渲染场景——headless Chromium 对 about:blank
    页面禁止加载本地文件（file:/// 与绝对路径均失败），只有 data URI 能可靠渲染。

    仅处理 HTML syntax（markdown 场景走 md_to_word 用 inline_images 返回本地路径，
    docx 能嵌入本地文件，不需要 data URI）。

    Args:
        text: HTML 文本
        tenant_id: 租户 ID（远程 URL 下载时必填；file_id 解析需要）
        user_id: 用户 ID（远程 URL 下载时附带）
        fetch_remote: 是否处理远程 URL（默认 True；False 时仅替换 file_id:）

    Returns:
        (处理后的文本, ImageRef 列表)
        - 所有命中的 src 被替换为 base64 data URI
        - 单图失败（file_id 找不到 / 下载失败 / 读文件失败 / 超限）保留原 src，记 warning
    """
    if not text:
        return text, []

    refs: List[ImageRef] = []
    seen_file_ids: set = set()
    registry = get_image_registry()

    def _add_ref(ref: Optional[ImageRef]) -> None:
        if ref is None or ref.file_id in seen_file_ids:
            return
        seen_file_ids.add(ref.file_id)
        refs.append(ref)

    # 1. 替换 <img src="file_id:file_xxx">
    async def _replace_img_file_id(m: Match) -> str:
        prefix, quote, file_id = m.group(1), m.group(2), m.group(3)
        try:
            ref = await registry.get_ref_by_file_id(file_id)
            if ref is None:
                logger.warning(
                    f"[image_inliner] file_id={file_id} 在 registry 中找不到，保留原 src"
                )
                return m.group(0)
            local_path = await registry.resolve_local_path(ref)
            _add_ref(ref)
            data_uri = _file_to_data_uri(local_path, ref.mime_type)
            if data_uri is None:
                return m.group(0)
            return f"{prefix}{quote}{data_uri}{quote}"
        except Exception as e:
            logger.warning(f"[image_inliner] 解析 file_id={file_id} 失败，保留原 src: {e}")
            return m.group(0)

    text = await _are_sub(_HTML_IMG_FILE_ID_PATTERN, _replace_img_file_id, text)

    # 2. 替换 <img src="https://...">
    if fetch_remote:
        async def _replace_img_remote(m: Match) -> str:
            prefix, quote, url = m.group(1), m.group(2), m.group(3)
            try:
                ref = await registry.fetch_to_local(
                    url, tenant_id=tenant_id, user_id=user_id, display_name=None,
                )
                local_path = await registry.resolve_local_path(ref)
                _add_ref(ref)
                data_uri = _file_to_data_uri(local_path, ref.mime_type)
                if data_uri is None:
                    return m.group(0)
                return f"{prefix}{quote}{data_uri}{quote}"
            except Exception as e:
                logger.warning(f"[image_inliner] 下载 url={url} 失败，保留原 src: {e}")
                return m.group(0)

        text = await _are_sub(_HTML_IMG_REMOTE_PATTERN, _replace_img_remote, text)

    return text, refs


__all__ = ["inline_images", "inline_images_as_data_uri"]
