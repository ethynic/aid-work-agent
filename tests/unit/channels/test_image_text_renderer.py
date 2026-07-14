# -*- coding: utf-8 -*-
"""渠道文本+图片占位符渲染（Phase 2 P2.9.0）单测"""
import pytest

from src.channels._image_text_renderer import render_text_with_image_placeholders


def _img(file_id: str, name: str, placement: str = "after_text") -> dict:
    return {
        "file_id": file_id,
        "display_name": name,
        "placement": placement,
        "source": "knowledge_base",
        "usage": "thumbnail",
    }


class TestRenderTextPlaceholders:
    def test_after_text_no_placeholder(self):
        """placement=after_text → 文本不变"""
        text = "你好，这是介绍。"
        result = render_text_with_image_placeholders(text, [_img("file_1", "封面.jpg")])
        assert result == text

    def test_before_text_prefix_placeholder(self):
        """placement=before_text → 文本开头插入 [图片：name]"""
        text = "景点介绍"
        result = render_text_with_image_placeholders(
            text,
            [_img("file_1", "封面.jpg", placement="before_text")],
        )
        assert result.startswith("[图片：封面.jpg]")
        # 占位符和原文本之间是换行
        assert "[图片：封面.jpg]\n景点介绍" == result

    def test_before_text_multiple_images(self):
        """多张 before_text 图：每张一行，前缀拼接"""
        text = "正文"
        result = render_text_with_image_placeholders(
            text,
            [
                _img("file_1", "封面.jpg", placement="before_text"),
                _img("file_2", "副图.jpg", placement="before_text"),
            ],
        )
        assert result.startswith("[图片：封面.jpg]\n[图片：副图.jpg]\n")
        assert result.endswith("正文")

    def test_inline_replaces_markdown(self):
        """placement=inline + Markdown → 替换为 [图片：alt]"""
        text = "前面文字 ![黄果树](file_id:file_abc123) 后面文字"
        result = render_text_with_image_placeholders(
            text,
            [_img("file_abc123", "黄果树.jpg", placement="inline")],
        )
        assert "![黄果树](file_id:file_abc123)" not in result
        assert "[图片：黄果树]" in result
        assert "前面文字" in result
        assert "后面文字" in result

    def test_inline_uses_alt_when_display_name_different(self):
        """inline 优先用 Markdown 中的 alt，不用 display_name"""
        text = "![看图](file_id:file_x)"
        result = render_text_with_image_placeholders(
            text,
            [_img("file_x", "原始文件名.jpg", placement="inline")],
        )
        assert "[图片：看图]" in result
        assert "原始文件名" not in result

    def test_inline_no_alt_falls_back_to_display_name(self):
        """inline 的 alt 为空时，从 images 列表查 display_name"""
        text = "![](file_id:file_x)"
        result = render_text_with_image_placeholders(
            text,
            [_img("file_x", "封面.jpg", placement="inline")],
        )
        assert "[图片：封面.jpg]" in result

    def test_inline_no_alt_no_match_uses_file_id(self):
        """inline 的 alt 为空且 images 中找不到对应 file_id → 用 file_id 兜底"""
        text = "![](file_id:file_unknown)"
        result = render_text_with_image_placeholders(
            text,
            [_img("file_other", "其他.jpg", placement="inline")],
        )
        assert "[图片：file_unknown]" in result

    def test_mixed_placements(self):
        """三种 placement 混合"""
        text = "正文 ![小图](file_id:file_inline) 结尾"
        result = render_text_with_image_placeholders(
            text,
            [
                _img("file_before", "顶部图.jpg", placement="before_text"),
                _img("file_after", "底部图.jpg", placement="after_text"),
                _img("file_inline", "行内图.jpg", placement="inline"),
            ],
        )
        # before_text 开头有占位符
        assert result.startswith("[图片：顶部图.jpg]\n")
        # after_text 不加占位符
        assert "底部图" not in result
        # inline 被替换（用 alt "小图"，不用 display_name "行内图.jpg"）
        assert "[图片：小图]" in result
        assert "行内图.jpg" not in result
        assert "![小图](file_id:file_inline)" not in result

    def test_empty_text_unchanged(self):
        """空文本 → 原样返回"""
        assert render_text_with_image_placeholders("", [_img("file_1", "x.jpg")]) == ""

    def test_empty_images_unchanged(self):
        """无图片 → 原样返回"""
        text = "你好"
        assert render_text_with_image_placeholders(text, []) == text

    def test_display_name_newline_sanitized(self):
        """display_name 含换行 → 替换为空格（防 UI 错位/注入）"""
        text = "正文"
        result = render_text_with_image_placeholders(
            text,
            [_img("file_1", "恶\n意\n注入", placement="before_text")],
        )
        assert "\n" not in result.replace("\n正文", "")  # 只有占位符和正文之间的换行
        assert "恶 意 注入" in result

    def test_display_name_truncated_when_too_long(self):
        """超长 display_name → 截断 + ..."""
        long_name = "a" * 100
        text = "正文"
        result = render_text_with_image_placeholders(
            text,
            [_img("file_1", long_name, placement="before_text")],
        )
        # 截断到 40 + "..."
        assert "a" * 40 + "..." in result
        assert "a" * 100 not in result

    def test_non_dict_image_entry_ignored(self):
        """images 列表含非 dict 条目 → 不抛异常"""
        text = "正文"
        # 部分条目是 None / str / 缺 placement
        mixed = [_img("file_1", "x.jpg", placement="before_text"), None, "not_a_dict", {"no_placement": True}]
        result = render_text_with_image_placeholders(text, mixed)  # type: ignore[arg-type]
        assert "[图片：x.jpg]" in result
