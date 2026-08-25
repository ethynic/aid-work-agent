# -*- coding: utf-8 -*-
"""src/core/agent_events.py 的单元测试

覆盖：
- make_image_event 三种 placement（after_text/before_text/inline）
- 空图片列表
- 非 list 输入（None 兜底）
- 事件结构与 make_event 一致（含 type/timestamp）
- mask_tool_args 脱敏（敏感键、大小写、超长截断、嵌套深度、不改原参数）
"""
import pytest

from src.core.agent_events import (
    MAX_ARG_NEST_DEPTH,
    MAX_ARG_VALUE_LENGTH,
    make_event,
    make_image_event,
    mask_tool_args,
)


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


class TestMaskToolArgs:
    def test_sensitive_keys_masked(self):
        """凭据类键名值替换为 ***"""
        args = {"password": "secret123", "token": "abc", "query": "关键词"}
        masked = mask_tool_args(args)
        assert masked["password"] == "***"
        assert masked["token"] == "***"
        assert masked["query"] == "关键词"

    def test_case_and_separator_insensitive(self):
        """键名大小写、连字符归一后仍命中脱敏"""
        args = {"Password": "x", "API_KEY": "y", "api-key": "z"}
        masked = mask_tool_args(args)
        assert masked["Password"] == "***"
        assert masked["API_KEY"] == "***"
        assert masked["api-key"] == "***"

    def test_nested_sensitive_keys(self):
        """嵌套 dict/list 中的敏感键同样脱敏"""
        args = {
            "config": {"secret_key": "sk-xx", "host": "10.0.0.1"},
            "files": [{"path": "/tmp/a.txt", "token": "tok1"}],
        }
        masked = mask_tool_args(args)
        assert masked["config"]["secret_key"] == "***"
        assert masked["config"]["host"] == "10.0.0.1"
        assert masked["files"][0]["token"] == "***"
        assert masked["files"][0]["path"] == "/tmp/a.txt"

    def test_long_string_truncated(self):
        """超长字符串截断并附省略标记"""
        long_value = "x" * (MAX_ARG_VALUE_LENGTH + 100)
        masked = mask_tool_args({"content": long_value})
        assert masked["content"].startswith("x" * MAX_ARG_VALUE_LENGTH)
        assert "截断" in masked["content"]
        assert len(masked["content"]) < len(long_value)

    def test_short_string_untouched(self):
        """短字符串原样保留"""
        masked = mask_tool_args({"content": "短文本"})
        assert masked["content"] == "短文本"

    def test_deep_nesting_capped(self):
        """嵌套深度超限时整体替换为省略标记"""
        deep = {"a": {}}
        cur = deep["a"]
        for _ in range(MAX_ARG_NEST_DEPTH + 2):
            cur["b"] = {}
            cur = cur["b"]
        masked = mask_tool_args(deep)
        # 深度超过上限处出现省略标记（不再继续展开）
        assert any(
            isinstance(v, str) and "嵌套过深" in v
            for v in _flatten_values(masked)
        )

    def test_original_args_unchanged(self):
        """脱敏返回新结构，不改动原始参数"""
        args = {"password": "secret", "data": {"content": "y" * 500}}
        mask_tool_args(args)
        assert args["password"] == "secret"
        assert args["data"]["content"] == "y" * 500

    def test_primitives_passthrough(self):
        """数字/布尔/None 原样透传"""
        args = {"count": 3, "flag": True, "none": None, "score": 1.5}
        assert mask_tool_args(args) == args

    def test_list_top_level(self):
        """顶层 list 输入同样递归脱敏"""
        masked = mask_tool_args([{"token": "t"}, "正常"])
        assert masked[0]["token"] == "***"
        assert masked[1] == "正常"


def _flatten_values(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _flatten_values(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _flatten_values(v)
    else:
        yield obj
