# -*- coding: utf-8 -*-
"""src/core/agent_events.py 的单元测试

覆盖：
- make_image_event 三种 placement（after_text/before_text/inline）
- 空图片列表
- 非 list 输入（None 兜底）
- 事件结构与 make_event 一致（含 type/timestamp）
- mask_tool_args 脱敏（敏感键、大小写、超长截断、嵌套深度、不改原参数）
- 工具结果内容处理纯函数（自 agent.py 迁入）：
  _truncate_tool_content / _extract_image_refs_from_tool_result /
  _normalize_image_placement，以及 agent.py re-export 等价性
"""
import pytest

import src.core.agent_events as agent_events_module
from src.core.agent_events import (
    MAX_ARG_NEST_DEPTH,
    MAX_ARG_VALUE_LENGTH,
    _extract_image_refs_from_tool_result,
    _normalize_image_placement,
    _truncate_tool_content,
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


class TestTruncateToolContent:
    """_truncate_tool_content：超长工具结果截断"""

    def test_below_threshold_returns_unchanged(self):
        """阈值以下（含恰好等于阈值）原样返回"""
        content = "a" * 12000
        assert _truncate_tool_content(content) == content
        assert _truncate_tool_content("short") == "short"

    def test_over_threshold_keeps_head_and_tail(self):
        """超长内容保留头部 + 尾部 + 省略标记"""
        content = "a" * 13000
        result = _truncate_tool_content(content)
        assert result.startswith("a" * 6000)
        assert result.endswith("a" * 2000)
        assert "...[已截断：共 13000 字符，省略 5000 字符]..." in result

    def test_custom_params(self):
        """自定义 max_chars / head_chars / threshold 生效"""
        result = _truncate_tool_content("a" * 100, max_chars=30, head_chars=10, threshold=50)
        assert result.startswith("a" * 10)
        assert result.endswith("a" * 20)
        assert "...[已截断：共 100 字符，省略 70 字符]..." in result


class TestExtractImageRefs:
    """_extract_image_refs_from_tool_result：ImageRef 提取"""

    def test_top_level_images(self):
        """顶层 images 列表：含 file_id 的 dict 被提取"""
        img1 = {"file_id": "f1", "url": "u1"}
        img2 = {"file_id": "f2"}
        result = {"images": [img1, img2, {"no_file_id": True}, "not_dict"]}
        assert _extract_image_refs_from_tool_result(result) == [img1, img2]

    def test_top_level_cover_image(self):
        """顶层 cover_image：单个 dict 被提取，无 file_id / None 不提取"""
        cover = {"file_id": "cover1"}
        assert _extract_image_refs_from_tool_result({"cover_image": cover}) == [cover]
        assert _extract_image_refs_from_tool_result({"cover_image": {"url": "x"}}) == []
        assert _extract_image_refs_from_tool_result({"cover_image": None}) == []

    def test_nested_results_cover_image(self):
        """results 列表中每项的 cover_image 被提取"""
        cover_a = {"file_id": "ca"}
        cover_b = {"file_id": "cb"}
        result = {
            "results": [
                {"name": "a", "cover_image": cover_a},
                {"name": "b", "cover_image": cover_b},
                {"name": "c", "cover_image": None},
                {"name": "d"},
                "not_dict",
            ]
        }
        assert _extract_image_refs_from_tool_result(result) == [cover_a, cover_b]

    def test_non_dict_input_returns_empty(self):
        """非 dict 输入一律返回空列表"""
        assert _extract_image_refs_from_tool_result(None) == []
        assert _extract_image_refs_from_tool_result("some string") == []
        assert _extract_image_refs_from_tool_result([1, 2, 3]) == []
        assert _extract_image_refs_from_tool_result(42) == []
        assert _extract_image_refs_from_tool_result({}) == []


class TestNormalizeImagePlacement:
    """_normalize_image_placement：placement 默认值补齐"""

    def test_fills_missing_and_falsy_placement(self):
        """placement 缺失 / 空串 / None 统一补 after_text"""
        refs = [
            {"file_id": "f1"},
            {"file_id": "f2", "placement": ""},
            {"file_id": "f3", "placement": None},
        ]
        ret = _normalize_image_placement(refs)
        assert ret is refs
        assert all(ref["placement"] == "after_text" for ref in refs)

    def test_keeps_existing_placement(self):
        """已有非空 placement 保留原值"""
        refs = [
            {"file_id": "f1", "placement": "before_text"},
            {"file_id": "f2", "placement": "inline"},
        ]
        _normalize_image_placement(refs)
        assert refs[0]["placement"] == "before_text"
        assert refs[1]["placement"] == "inline"

    def test_empty_list(self):
        """空列表原样返回"""
        assert _normalize_image_placement([]) == []


class TestAgentReExportEquivalence:
    """re-export 等价性：agent.py 的名字与 agent_events 模块指向同一函数对象。

    注意：src.core.agent 在模块级初始化 LLM 网关，缺少 API Key 的本地环境
    无法导入（基线已知问题），此时跳过本类，不影响纯函数测试独立运行。
    """

    def _get_agent_module(self):
        try:
            import src.core.agent as agent_module
        except ValueError as exc:
            # 模块级 LLM 网关初始化失败（缺 API Key），属本地环境基线问题
            pytest.skip(f"src.core.agent 无法导入：{exc}")
        return agent_module

    def test_agent_reexports_same_objects(self):
        agent_module = self._get_agent_module()
        assert agent_module._truncate_tool_content is agent_events_module._truncate_tool_content
        assert (
            agent_module._extract_image_refs_from_tool_result
            is agent_events_module._extract_image_refs_from_tool_result
        )
        assert (
            agent_module._normalize_image_placement
            is agent_events_module._normalize_image_placement
        )
