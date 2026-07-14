# -*- coding: utf-8 -*-
"""src/core/agent_events.py 的单元测试

覆盖：
- make_image_event 三种 placement（after_text/before_text/inline）
- 空图片列表
- 非 list 输入（None 兜底）
- 事件结构与 make_event 一致（含 type/timestamp）
"""
import pytest

from src.core.agent_events import make_event, make_image_event


def _sample_ref(file_id: str = "file_abc123") -> dict:
    return {
        "file_id": file_id,
        "download_url": f"/api/files/{file_id}/download",
        "display_name": "测试图.jpg",
        "mime_type": "image/jpeg",
        "size_bytes": 100,
        "source": "tool_generated",
        "usage": "inline",
        "placement": "after_text",
    }


class TestMakeImageEvent:
    def test_after_text_default_placement(self):
        """默认 placement 是 after_text"""
        refs = [_sample_ref()]
        event = make_image_event(refs)
        assert event["type"] == "images"
        assert event["placement"] == "after_text"
        assert event["images"] == refs
        assert "timestamp" in event
        assert isinstance(event["timestamp"], int)

    def test_before_text_placement(self):
        """placement=before_text 透传"""
        event = make_image_event([_sample_ref()], placement="before_text")
        assert event["placement"] == "before_text"

    def test_inline_placement(self):
        """placement=inline 透传（Phase 2 仍按 after_text 渲染，但事件本身透传）"""
        event = make_image_event([_sample_ref()], placement="inline")
        assert event["placement"] == "inline"

    def test_empty_images_list(self):
        """空 list 允许（前端会跳过渲染）"""
        event = make_image_event([])
        assert event["images"] == []
        assert event["type"] == "images"

    def test_none_images_treated_as_empty(self):
        """images=None 兜底为空 list（防 JSON 序列化 None）"""
        event = make_image_event(None)  # type: ignore[arg-type]
        assert event["images"] == []

    def test_multiple_images_preserved(self):
        """多张图保留顺序"""
        refs = [_sample_ref(f"file_{i}") for i in range(3)]
        event = make_image_event(refs)
        assert event["images"] == refs
        assert len(event["images"]) == 3

    def test_images_are_copied_not_referenced(self):
        """images 字段应是 list(...) 浅拷贝，避免外部修改污染事件"""
        refs = [_sample_ref()]
        event = make_image_event(refs)
        refs.clear()
        # 事件中的 images 不应被影响
        assert len(event["images"]) == 1

    def test_event_structure_compatible_with_make_event(self):
        """事件结构（type/timestamp）与 make_event 一致"""
        event = make_image_event([_sample_ref()])
        # 与 make_event 字段命名一致
        assert event["type"] == "images"
        assert isinstance(event["timestamp"], int)
        # timestamp 是毫秒级（10 位以上）
        assert event["timestamp"] > 1_000_000_000
