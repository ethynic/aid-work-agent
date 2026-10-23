# pptx_gen — 模板风格复用 + AI 内容生成的 PPT 技能
# 所有产出均为原生可编辑 PPT（文本框/形状），无图片页。
# 字体策略：全 deck 只用商用免费的思源黑体 CN（SIL OFL 1.1），见 fonts.py
from .template_analyzer import analyze
from .generator import generate
# 路线二：VI 提取 + 原生形状渲染（内容丰富 / 视觉美观 / 完全可编辑）
from .vi import extract_vi
from .pipeline import generate_from_vi, preview_pngs
from .native import generate_native
# 迭代修改：对已生成的 deck 做手术式编辑
from .editor import edit_deck
# 商用免费字体策略（字体名 / 全 deck 合规化 / 字体嵌入）
from .fonts import (FONT_NAME, FONT_NAME_LATIN, font_file, latin_font_file,
                    remap_typeface, remap_presentation, embed_fonts)

__all__ = [
    "analyze", "generate",            # 路线一
    "extract_vi", "generate_from_vi", # 路线二（= generate_native，原生可编辑）
    "generate_native", "preview_pngs",
    "edit_deck",                      # 修改已生成的 PPT
    "FONT_NAME", "FONT_NAME_LATIN", "font_file", "latin_font_file",
    "remap_typeface", "remap_presentation", "embed_fonts",   # 商用免费字体策略
]
