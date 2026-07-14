"""Phase 2 P2.3：Agent 主循环 ImageRef 提取与 placement 归一化单测。

测试覆盖模块级辅助函数：
- ``_extract_image_refs_from_tool_result``：从工具返回 dict 中识别 ImageRef
- ``_normalize_image_placement``：补 placement 默认值

不直接驱动整个 ``process_message``（需要 mock 整个 LLM 流），仅测纯逻辑函数。
"""

import pytest

from src.core.agent import (
    _extract_image_refs_from_tool_result,
    _normalize_image_placement,
)


def _make_ref(file_id: str = "file_abc123", placement: str = "") -> dict:
    """构造最小合法 ImageRef dict（仅含 file_id + 可选 placement）。"""
    ref = {
        "file_id": file_id,
        "download_url": f"/api/files/{file_id}/download",
        "display_name": "test.png",
        "source": "knowledge_base",
        "usage": "thumbnail",
    }
    if placement:
        ref["placement"] = placement
    return ref


class TestExtractImageRefsFromToolResult:
    """验证从工具返回结果中提取 ImageRef 的识别规则。"""

    def test_extract_images_from_top_level_images_key(self):
        """工具返回 ``{"images": [ImageRef.dict]}`` → 提取出 1 个。"""
        result = {"success": True, "images": [_make_ref("file_img1")]}
        refs = _extract_image_refs_from_tool_result(result)
        assert len(refs) == 1
        assert refs[0]["file_id"] == "file_img1"

    def test_extract_images_from_top_level_cover_image(self):
        """工具返回 ``{"cover_image": ImageRef.dict}`` → 提取出 1 个。"""
        result = {
            "success": True,
            "name": "黄果树瀑布",
            "cover_image": _make_ref("file_cover1"),
        }
        refs = _extract_image_refs_from_tool_result(result)
        assert len(refs) == 1
        assert refs[0]["file_id"] == "file_cover1"

    def test_extract_images_from_nested_results(self):
        """attraction_search 模式：results 列表中嵌套 cover_image。

        - 有 cover_image 的项被提取
        - cover_image=None 的项跳过
        """
        result = {
            "success": True,
            "results": [
                {"name": "黄果树瀑布", "cover_image": _make_ref("file_a")},
                {"name": "无图景点", "cover_image": None},
                {"name": "小七孔", "cover_image": _make_ref("file_b")},
            ],
        }
        refs = _extract_image_refs_from_tool_result(result)
        assert [r["file_id"] for r in refs] == ["file_a", "file_b"]

    def test_extract_images_invalid_inputs_skipped(self):
        """``images`` 列表中的 None / 字符串 / 缺 file_id 项全部跳过。"""
        result = {
            "images": [
                None,
                "not_a_dict",
                {"no_file_id": True},
                _make_ref("file_valid"),
                {"file_id": ""},
            ]
        }
        refs = _extract_image_refs_from_tool_result(result)
        assert len(refs) == 1
        assert refs[0]["file_id"] == "file_valid"

    def test_extract_from_non_dict_returns_empty(self):
        """result 是 None / str / list → 空 list（不抛异常）。"""
        assert _extract_image_refs_from_tool_result(None) == []
        assert _extract_image_refs_from_tool_result("some string") == []
        assert _extract_image_refs_from_tool_result([1, 2, 3]) == []
        assert _extract_image_refs_from_tool_result(42) == []

    def test_extract_combined_top_and_nested(self):
        """顶层 images + 顶层 cover_image + results 内 cover_image 同时存在时全部提取。"""
        result = {
            "images": [_make_ref("file_top1"), _make_ref("file_top2")],
            "cover_image": _make_ref("file_cover"),
            "results": [{"cover_image": _make_ref("file_nested")}],
        }
        refs = _extract_image_refs_from_tool_result(result)
        assert {r["file_id"] for r in refs} == {
            "file_top1",
            "file_top2",
            "file_cover",
            "file_nested",
        }

    def test_extract_empty_dict_returns_empty(self):
        """空 dict 不报错，返回空 list。"""
        assert _extract_image_refs_from_tool_result({}) == []


class TestNormalizeImagePlacement:
    """验证 placement 默认值补全逻辑。"""

    def test_normalize_placement_adds_after_text_default(self):
        """refs 无 placement 字段 → 补 ``after_text``。"""
        refs = [_make_ref("file_a"), _make_ref("file_b")]
        _normalize_image_placement(refs)
        assert all(r["placement"] == "after_text" for r in refs)

    def test_normalize_placement_preserves_explicit(self):
        """已有 placement 的 ref（含 before_text/inline）→ 原值保留。"""
        refs = [
            _make_ref("file_a", placement="before_text"),
            _make_ref("file_b", placement="inline"),
            _make_ref("file_c", placement="after_text"),
        ]
        _normalize_image_placement(refs)
        assert refs[0]["placement"] == "before_text"
        assert refs[1]["placement"] == "inline"
        assert refs[2]["placement"] == "after_text"

    def test_normalize_placement_empty_string_treated_as_missing(self):
        """placement="" 视为缺失，补 ``after_text``。"""
        refs = [{"file_id": "x", "placement": ""}]
        _normalize_image_placement(refs)
        assert refs[0]["placement"] == "after_text"

    def test_normalize_placement_empty_list_no_error(self):
        """空 list 不报错（no-op）。"""
        _normalize_image_placement([])

    def test_normalize_placement_mutates_in_place(self):
        """确认函数原地修改（返回同一 list）。"""
        refs = [_make_ref("file_a")]
        ret = _normalize_image_placement(refs)
        assert ret is refs
        assert refs[0]["placement"] == "after_text"
