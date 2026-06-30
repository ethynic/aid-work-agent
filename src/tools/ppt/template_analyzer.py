"""
PPT 模板分析器

分析用户上传的 .pptx 模板，提取布局特征并匹配内容。
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger
from pptx import Presentation
from pptx.oxml.ns import qn

from src.tools.ppt.theme import get_theme, PPTTheme
from src.tools.ppt.generator import PPTGenerator


class TemplateAnalyzer:
    """分析用户上传的 .pptx 模板，提取布局特征。"""

    def analyze(self, template_path: str) -> dict:
        """返回模板分析结果。"""
        prs = Presentation(template_path)
        return {
            "slide_width": prs.slide_width,
            "slide_height": prs.slide_height,
            "theme_colors": self._extract_theme_colors(prs),
            "layouts": [
                self._analyze_layout(layout, index)
                for index, layout in enumerate(prs.slide_layouts)
            ],
        }

    def _extract_theme_colors(self, prs: Presentation) -> Dict[str, str]:
        """尝试提取模板的主题配色。"""
        try:
            theme = prs.slide_masters[0].slide_layouts[0].slide_master.element
            # 简化处理：返回空 dict，后续使用默认配色
            return {}
        except (AttributeError, IndexError):
            return {}

    def _analyze_layout(self, layout, index: int) -> dict:
        """分析单个 Slide Layout。"""
        return {
            "index": index,
            "name": layout.name,
            "inferred_type": self._infer_type(layout),
            "placeholders": [
                {
                    "idx": ph.placeholder_format.idx,
                    "type": str(ph.placeholder_format.type),
                    "name": ph.name,
                    "position": {
                        "left": ph.left, "top": ph.top,
                        "width": ph.width, "height": ph.height,
                    },
                }
                for ph in layout.placeholders
            ],
        }

    def _infer_type(self, layout) -> str:
        """推断布局类型。"""
        name = layout.name.lower()
        ph_types = [str(ph.placeholder_format.type) for ph in layout.placeholders]

        if "title" in name and "content" not in name and "and" not in name:
            return "cover"
        if "section" in name or "header" in name:
            return "section"
        if "two" in name or "comparison" in name:
            return "content"
        if "blank" in name:
            return "content"
        if "title" in name and ("content" in name or "and" in name):
            return "content"
        return "content"

    def match_content_to_layouts(self, plan: dict, analysis: dict) -> list:
        """将内容大纲的每页匹配到模板的最佳 Layout。"""
        matches = []
        layouts = analysis["layouts"]

        for slide in plan.get("slides", []):
            slide_type = slide.get("type", "content")
            candidates = [l for l in layouts if l["inferred_type"] == slide_type]
            if not candidates:
                candidates = [l for l in layouts if l["inferred_type"] == "content"]
            chosen = candidates[0] if candidates else (layouts[0] if layouts else None)

            matches.append({
                "slide": slide,
                "layout": chosen,
            })

        return matches

    def generate_from_template(self, template_path: str, matches: list,
                               plan: dict) -> str:
        """基于模板匹配结果生成 PPT。"""
        prs = Presentation(template_path)

        # 删除模板中已有的幻灯片（保留 layouts）
        while len(prs.slides) > 0:
            rId = prs.slides._sldIdLst[0].get(qn("r:id"))
            prs.part.drop_rel(rId)
            del prs.slides._sldIdLst[0]

        for match in matches:
            layout_info = match["layout"]
            if layout_info is None:
                continue

            layout = prs.slide_layouts[layout_info["index"]]
            slide = prs.slides.add_slide(layout)
            self._fill_placeholders(slide, match["slide"])

        # 保存
        save_dir = self._get_output_dir()
        save_dir.mkdir(parents=True, exist_ok=True)

        title = plan.get("title", "presentation")
        safe_name = "".join(c if c.isalnum() or c in "._- " else "_" for c in title)
        output_path = save_dir / f"{safe_name}.pptx"
        prs.save(str(output_path))

        return str(output_path.absolute())

    def _fill_placeholders(self, slide, data: dict):
        """填充占位符。"""
        for ph in slide.placeholders:
            idx = ph.placeholder_format.idx
            ph_type = str(ph.placeholder_format.type)

            if idx == 0 or "TITLE" in ph_type:
                ph.text = data.get("title", "")
            elif idx == 1 or "BODY" in ph_type or "SUBTITLE" in ph_type:
                points = data.get("points", [])
                if points:
                    ph.text = "\n".join(points)
                else:
                    ph.text = data.get("subtitle", data.get("intro", ""))

    def _get_output_dir(self) -> Path:
        try:
            from src.main import _get_tenant_upload_dir
            return _get_tenant_upload_dir()
        except (ImportError, AttributeError):
            return Path("storage/ppt")
