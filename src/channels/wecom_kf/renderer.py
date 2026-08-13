"""
企业微信客服 Markdown 渲染器

复用 src/services/x_to_image 的浏览器池和长图后处理能力：
- browser_pool.acquire_page：元素级截图（render_table，仅截 table 元素）
- browser_pool.shoot + finalize_long_image：整页长图 + 空白检测 + 高度截断 +
  体积控制（render_markdown，含表格/图片/文本的所有元素）

HTML 模板针对微信聊天窗口优化（窄宽度 420px、14px 字体）。
文件名基于内容 hash，自然去重。
"""
import hashlib
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from loguru import logger

from src.core.storage import ensure_tenant_storage_dir


class WeComKfRenderer:
    """将 markdown 渲染为图片（复用 x_to_image 服务的浏览器池与长图后处理）。"""

    # HTML 模板：白底、系统中文栈、窄宽度适配微信气泡
    HTML_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  body {{
    margin: 0;
    padding: 8px;
    background: #ffffff;
    font-family: -apple-system, "PingFang SC", "Microsoft YaHei", "Helvetica Neue", sans-serif;
    font-size: 14px;
    color: #333333;
    line-height: 1.6;
  }}
  table {{
    border-collapse: collapse;
    width: auto;
    max-width: 420px;
    margin: 6px 0;
  }}
  th {{
    background: #f0f0f0;
    font-weight: bold;
    text-align: left;
    padding: 6px 10px;
    border: 1px solid #d0d0d0;
    white-space: nowrap;
  }}
  td {{
    padding: 6px 10px;
    border: 1px solid #d0d0d0;
  }}
  tr:nth-child(even) td {{
    background: #fafafa;
  }}
  img {{
    max-width: 420px;
    height: auto;
    display: block;
    margin: 6px 0;
    border-radius: 4px;
  }}
  h1, h2, h3, h4, h5, h6 {{
    margin: 8px 0 4px;
    line-height: 1.4;
  }}
  p {{
    margin: 4px 0;
  }}
  pre {{
    background: #f6f8fa;
    padding: 8px;
    border-radius: 4px;
    overflow-x: auto;
    font-size: 12px;
  }}
  code {{
    background: #f6f8fa;
    padding: 1px 4px;
    border-radius: 3px;
    font-size: 12px;
  }}
  pre code {{
    background: transparent;
    padding: 0;
  }}
  ul, ol {{
    margin: 4px 0;
    padding-left: 20px;
  }}
  blockquote {{
    border-left: 3px solid #d0d0d0;
    padding-left: 8px;
    color: #666;
    margin: 4px 0;
  }}
</style>
</head>
<body>{html_content}</body>
</html>"""

    # Markdown 图片 file_id: scheme 正则（与 _image_inliner.py 保持一致）
    _FILE_ID_IMAGE_PATTERN = re.compile(
        r'!\[([^\]]*)\]\(file_id:([a-z0-9_]+)\)'
    )

    # 渲染视窗宽度（适配微信气泡）
    _VIEWPORT_WIDTH = 440

    def __init__(
        self,
        upload_dir: str = "./storage/uploads/wecom_kf",
        tenant_id: str = "",
    ):
        self._upload_dir = upload_dir
        self._tenant_id = tenant_id or ""
        os.makedirs(self._upload_dir, exist_ok=True)

    def set_tenant_id(self, tenant_id: str) -> None:
        """设置租户 ID（由 adapter 注入），设置后落盘走 tenants 规范。"""
        self._tenant_id = tenant_id or ""

    def _resolve_save_dir(self) -> str:
        """解析最终保存目录：有租户走 tenants 规范，无租户回退旧路径。"""
        if self._tenant_id:
            return ensure_tenant_storage_dir(self._tenant_id, "conversation")
        os.makedirs(self._upload_dir, exist_ok=True)
        return self._upload_dir

    async def is_available(self) -> bool:
        """检查 Playwright + Chromium 是否可用（委托给 browser_pool）。"""
        from src.services.x_to_image.renderers.browser_pool import browser_pool
        return await browser_pool.is_available()

    async def render_table(self, markdown_table: str) -> Optional[str]:
        """
        将 markdown 表格渲染为 PNG 图片（元素级截图，仅截 table 元素）。

        Args:
            markdown_table: 原始 markdown 表格文本

        Returns:
            生成的 PNG 文件路径，失败返回 None
        """
        if not markdown_table:
            return None
        if not await self.is_available():
            return None

        from src.services.x_to_image.renderers.browser_pool import browser_pool

        try:
            import markdown

            html = markdown.markdown(markdown_table, extensions=["tables"])
            full_html = self.HTML_TEMPLATE.format(html_content=html)

            content_hash = hashlib.md5(markdown_table.encode()).hexdigest()[:12]
            output_path = os.path.join(
                self._resolve_save_dir(), f"table_{content_hash}.png"
            )

            async with browser_pool.acquire_page(width=self._VIEWPORT_WIDTH) as page:
                await page.set_content(full_html, wait_until="networkidle")
                table_element = await page.query_selector("table")
                if not table_element:
                    logger.warning("表格渲染失败：HTML 中未找到 table 元素")
                    return None
                await table_element.screenshot(path=output_path)

            file_size = os.path.getsize(output_path) if os.path.exists(output_path) else 0
            logger.info(f"表格已渲染为图片: {output_path} ({file_size} bytes)")
            return output_path
        except Exception as e:
            logger.error(f"表格渲染失败: {e}", exc_info=True)
            return None

    async def render_markdown(self, markdown_text: str) -> Optional[str]:
        """
        将整段 markdown 渲染为 PNG 长图（含表格、图片、文本等所有元素）。

        用于 wecom_kf 渠道在 LLM 回复包含表格或图片时，一次性发送单张长图，
        规避单次咨询 5 次回复限制。

        处理流程：
        1. 扫描 markdown 中的 `![alt](file_id:xxx)`，通过 Redis 查本地路径，
           替换为 `file://` URL 供 Playwright 加载
        2. markdown 库转 HTML（启用 tables / fenced_code / nl2br / sane_lists 扩展）
        3. browser_pool.shoot() 生成临时页图（整页截图）
        4. finalize_long_image 做空白检测 + 高度截断 + 2MB 体积控制
        5. 把最终长图 move 到持久化目录（storage/uploads/wecom_kf/）

        Args:
            markdown_text: 原始 markdown 文本

        Returns:
            生成的图片文件路径（PNG 或 JPEG），失败返回 None
        """
        if not markdown_text:
            return None
        if not await self.is_available():
            return None

        from src.services.x_to_image.image_utils import finalize_long_image
        from src.services.x_to_image.models import InputType, XToImageInput
        from src.services.x_to_image.renderers.browser_pool import browser_pool

        # 1. 解析 file_id: scheme 为本地 file:// URL
        resolved_text = await self._resolve_file_id_images(markdown_text)

        # 2. markdown -> HTML
        try:
            import markdown

            html = markdown.markdown(
                resolved_text,
                extensions=["tables", "fenced_code", "nl2br", "sane_lists"],
            )
            full_html = self.HTML_TEMPLATE.format(html_content=html)
        except Exception as e:
            logger.error(f"markdown 转 HTML 失败: {e}", exc_info=True)
            return None

        # 3. 临时目录 + browser_pool 生成页图
        work_dir = Path(tempfile.mkdtemp(prefix="wecom_kf_md_"))
        try:
            try:
                page_path = await browser_pool.shoot(
                    full_html, width=self._VIEWPORT_WIDTH, out_dir=work_dir
                )
            except Exception as e:
                logger.error(f"browser_pool.shoot 失败: {e}", exc_info=True)
                return None

            # 4. 后处理:空白检测 + 高度截断 + 体积控制
            #    企微临时素材 image 限 2MB，超限 finalize_long_image 自动转 JPEG(quality=85)
            content_hash = hashlib.md5(markdown_text.encode()).hexdigest()[:12]
            inp = XToImageInput(
                source=markdown_text,
                content_type=InputType.MARKDOWN,
                width=self._VIEWPORT_WIDTH,
                output_name=f"md_{content_hash}",
                max_height=20_000,
                max_file_size_mb=2,
            )
            try:
                result = await finalize_long_image(
                    [page_path], inp, work_dir, renderer_name="wecom_kf_md"
                )
            except Exception as e:
                logger.error(f"finalize_long_image 失败: {e}", exc_info=True)
                return None

            if not result.success or not result.image_path:
                logger.warning(f"markdown 长图后处理失败: {result.error}")
                return None

            # 5. 把最终长图 move 到持久化目录
            final_path = os.path.join(
                self._resolve_save_dir(), os.path.basename(result.image_path)
            )
            try:
                shutil.move(result.image_path, final_path)
            except Exception as e:
                logger.error(
                    f"移动长图到持久化目录失败: {e}", exc_info=True
                )
                return None
            logger.info(
                f"markdown 已渲染为长图: {final_path} "
                f"({result.file_size} bytes, truncated={result.truncated})"
            )
            return final_path
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    async def _resolve_file_id_images(self, markdown_text: str) -> str:
        """把 markdown 中的 `![alt](file_id:xxx)` 替换为 `![alt](file:///abs/path)`。

        通过 Redis `uploaded_file:{file_id}` hash 的 path 字段查本地路径。
        解析失败的引用保留原文（Playwright 渲染时会显示破图占位，但不阻断整张长图生成）。

        Args:
            markdown_text: 原始 markdown 文本

        Returns:
            替换后的 markdown 文本
        """
        try:
            from src.core.redis_client import redis_client
        except Exception as e:
            logger.warning(f"redis_client 导入失败，file_id: 图片无法解析: {e}")
            return markdown_text

        def _replace(m: "re.Match[str]") -> str:
            alt, file_id = m.group(1), m.group(2)
            try:
                key = redis_client.make_key("uploaded_file", file_id)
                path_str = redis_client.hget(key, "path")
                if not path_str or not os.path.exists(path_str):
                    logger.warning(
                        f"file_id={file_id} 在 Redis 中无 path 或文件不存在，保留原文"
                    )
                    return m.group(0)
                # 转 file:// URL 供 Playwright 加载（绝对路径需以 / 开头）
                abs_path = os.path.abspath(path_str)
                return f"![{alt}](file://{abs_path})"
            except Exception as e:
                logger.warning(
                    f"解析 file_id={file_id} 失败，保留原文: {e}"
                )
                return m.group(0)

        return self._FILE_ID_IMAGE_PATTERN.sub(_replace, markdown_text)

    async def close(self):
        """no-op：浏览器生命周期由 browser_pool 统一管理。

        保留此方法以兼容 adapter.close() 的调用约定（`if self._renderer: await self._renderer.close()`），
        避免删除方法导致 AttributeError。
        """
        pass
