"""
HTML 渲染器。

把 HTML 字符串或 .html 文件渲染为一张全页长图。参见设计文档 §5.6。

关键设计：尊重用户自带样式，绝不强制套用会覆盖用户样式的 stylesheet。
- 若输入是完整 HTML 文档（含 <html> 或 <!DOCTYPE>）：基本原样透传，
  仅在未显式设置 body 默认 margin 时由浏览器默认 UA 样式决定（不注入额外样式）。
- 若输入是 HTML 片段：包裹成完整文档，注入 body { margin:0; padding:16px; } 作为兜底样式。
"""
from pathlib import Path
from typing import List

from loguru import logger

from src.services.x_to_image.models import XToImageInput
from src.services.x_to_image.renderers.base import ImageRendererBase


class HtmlRenderer(ImageRendererBase):
    """HTML 字符串 / .html 文件 → 截图。"""

    name = "html"

    async def render(self, inp: XToImageInput, work_dir: Path) -> List[str]:
        """
        将 HTML（字符串或文件）渲染为一张全页长图。

        Args:
            inp: 转换输入。is_file_path=True 时 source 为 .html 文件绝对路径，
                 否则 source 为 HTML 字符串/片段。
            work_dir: 本次转换的临时工作目录

        Returns:
            含一张页图 PNG 绝对路径的列表。
        """
        if inp.is_file_path:
            p = Path(inp.source)
            if not p.exists():
                raise ValueError(f"HTML 文件不存在: {inp.source}")
            content = p.read_text(encoding="utf-8")
        else:
            content = inp.source

        # tenant_id 提供时，把图片引用（file_id: / 远程 URL）解析为 base64 data URI。
        # headless Chromium 对 set_content 页面（about:blank）禁止加载本地文件，必须内联。
        if inp.tenant_id:
            from src.tools._image_inliner import inline_images_as_data_uri
            try:
                content, _refs = await inline_images_as_data_uri(
                    content, tenant_id=inp.tenant_id, user_id=inp.user_id,
                )
            except Exception as e:
                logger.warning(f"[HtmlRenderer] 图片内联失败，回退原始 HTML: {e}")

        full_html = _wrap_to_full_doc(content)
        path = await self._shoot_full_page(full_html, inp, work_dir)
        logger.debug(f"HtmlRenderer 渲染完成: {path}")
        return [path]


def _is_full_html_doc(content: str) -> bool:
    """判断是否已是完整 HTML 文档（含 <html> 或 <!DOCTYPE>，大小写不敏感）。"""
    lowered = content.lower()
    return "<html" in lowered or "<!doctype" in lowered


def _wrap_to_full_doc(content: str) -> str:
    """
    确保产出一篇完整 HTML 文档（DOCTYPE / charset utf-8 / body）。

    - 完整文档：基本原样透传（尊重用户自带样式，不注入覆盖性 stylesheet）。
    - HTML 片段：包裹为完整文档，仅注入 body { margin:0; padding:16px; } 兜底样式。
    """
    if _is_full_html_doc(content):
        # 用户已有完整的 HTML 结构（含 <html> 或 <!DOCTYPE>），
        # 其自带样式可能是渲染的核心意图，原样透传以避免破坏。
        return content
    # 片段：补全 DOCTYPE / charset / body 兜底 padding
    return """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<style>
  html, body {
    margin: 0;
    padding: 16px;
    background: #fff;
  }
</style>
</head>
<body>
{content}
</body>
</html>""".replace("{content}", content)
