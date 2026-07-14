# -*- coding: utf-8 -*-
"""UnifiedResponse 图片字段扩展（Phase 2 P2.2）单测"""
import pytest

from src.models.message import UnifiedResponse


def _image(file_id: str = "file_a", placement: str = "after_text") -> dict:
    return {
        "file_id": file_id,
        "download_url": f"/api/files/{file_id}/download",
        "display_name": "test.jpg",
        "mime_type": "image/jpeg",
        "size_bytes": 100,
        "source": "tool_generated",
        "usage": "inline",
        "placement": placement,
    }


class TestUnifiedResponseImages:
    def test_get_images_empty_when_not_set(self):
        """无 content.images 时返回空 list（历史消息兼容）"""
        resp = UnifiedResponse.from_text("hello", reply_to="msg_1")
        assert resp.get_images() == []

    def test_get_images_returns_list_from_content(self):
        """content.images 是 list 时返回"""
        resp = UnifiedResponse.from_text("hi", reply_to="msg_1")
        resp.set_images([_image()])
        images = resp.get_images()
        assert len(images) == 1
        assert images[0]["file_id"] == "file_a"

    def test_set_images_overwrites_existing(self):
        """set_images 覆盖式写入"""
        resp = UnifiedResponse.from_text("hi", reply_to="msg_1")
        resp.set_images([_image("file_a"), _image("file_b")])
        resp.set_images([_image("file_c")])
        assert len(resp.get_images()) == 1
        assert resp.get_images()[0]["file_id"] == "file_c"

    def test_set_images_none_clears(self):
        """set_images(None) 清空"""
        resp = UnifiedResponse.from_text("hi", reply_to="msg_1")
        resp.set_images([_image()])
        resp.set_images(None)
        assert resp.get_images() == []

    def test_add_image_appends_with_dedup(self):
        """add_image 追加，同 file_id 自动去重"""
        resp = UnifiedResponse.from_text("hi", reply_to="msg_1")
        resp.add_image(_image("file_a"))
        resp.add_image(_image("file_a"))  # 重复
        resp.add_image(_image("file_b"))
        assert len(resp.get_images()) == 2
        file_ids = [img["file_id"] for img in resp.get_images()]
        assert "file_a" in file_ids
        assert "file_b" in file_ids

    def test_add_image_ignores_invalid_dict(self):
        """add_image 忽略非 dict 或无 file_id 的输入"""
        resp = UnifiedResponse.from_text("hi", reply_to="msg_1")
        resp.add_image("not_a_dict")  # type: ignore[arg-type]
        resp.add_image({"no_file_id": True})
        resp.add_image(None)  # type: ignore[arg-type]
        assert resp.get_images() == []

    def test_get_images_when_content_images_not_list(self):
        """content.images 不是 list（脏数据）时返回空 list"""
        resp = UnifiedResponse.from_text("hi", reply_to="msg_1")
        resp.content["images"] = "not_a_list"
        assert resp.get_images() == []

    def test_round_trip_serialization(self):
        """Pydantic 序列化/反序列化 round-trip"""
        resp = UnifiedResponse.from_text("hi", reply_to="msg_1")
        resp.set_images([_image("file_a"), _image("file_b", placement="before_text")])
        dumped = resp.model_dump()
        loaded = UnifiedResponse(**dumped)
        assert len(loaded.get_images()) == 2
        assert loaded.get_images()[1]["placement"] == "before_text"

    def test_text_property_unaffected(self):
        """images 写入 content 不影响 text 属性"""
        resp = UnifiedResponse.from_text("hello", reply_to="msg_1")
        resp.set_images([_image()])
        assert resp.text == "hello"
