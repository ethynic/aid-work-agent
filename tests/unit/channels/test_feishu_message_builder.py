"""
飞书消息构建器单元测试

锁定 P1 避坑点：所有 build_* 方法返回的 content 字段必须是 JSON 字符串。
"""

import json

import pytest

from src.channels.feishu.message_builder import (
    FeishuMessageBuilder,
    _has_markdown_pattern,
    _parse_inline_elements,
)


@pytest.fixture
def builder():
    return FeishuMessageBuilder()


class TestBuildText:
    """build_text 返回 content 为 JSON 字符串 (P1)"""

    def test_content_is_json_string(self, builder):
        result = builder.build_text("hello")
        assert result["msg_type"] == "text"
        assert isinstance(result["content"], str)
        # content 字符串解析后应为 {"text": "hello"}
        parsed = json.loads(result["content"])
        assert parsed == {"text": "hello"}

    def test_chinese_text(self, builder):
        result = builder.build_text("你好，飞书！")
        parsed = json.loads(result["content"])
        assert parsed["text"] == "你好，飞书！"

    def test_special_chars(self, builder):
        result = builder.build_text('包含 "引号" 和 \\反斜杠')
        # content 应是合法的 JSON 字符串
        parsed = json.loads(result["content"])
        assert "引号" in parsed["text"]


class TestBuildPost:
    """build_post 富文本消息"""

    def test_content_is_json_string(self, builder):
        paragraphs = [[{"tag": "text", "text": "hello"}]]
        result = builder.build_post("标题", paragraphs)
        assert result["msg_type"] == "post"
        assert isinstance(result["content"], str)
        parsed = json.loads(result["content"])
        assert "zh_cn" in parsed
        assert parsed["zh_cn"]["title"] == "标题"
        assert parsed["zh_cn"]["content"] == paragraphs

    def test_complex_paragraphs(self, builder):
        paragraphs = [
            [
                {"tag": "text", "text": "普通文字"},
                {"tag": "text", "text": "加粗", "style": ["bold"]},
                {"tag": "a", "text": "链接", "href": "https://example.com"},
            ]
        ]
        result = builder.build_post("标题", paragraphs)
        parsed = json.loads(result["content"])
        content = parsed["zh_cn"]["content"]
        assert len(content[0]) == 3


class TestBuildInteractive:
    """build_interactive 卡片消息"""

    def test_content_is_json_string(self, builder):
        elements = [{"tag": "div", "text": {"tag": "plain_text", "content": "内容"}}]
        result = builder.build_interactive(elements)
        assert result["msg_type"] == "interactive"
        assert isinstance(result["content"], str)
        parsed = json.loads(result["content"])
        assert parsed["elements"] == elements

    def test_with_header(self, builder):
        elements = [{"tag": "div"}]
        header = {"title": {"tag": "plain_text", "content": "卡片标题"}, "template": "blue"}
        result = builder.build_interactive(elements, header=header)
        parsed = json.loads(result["content"])
        assert parsed["header"] == header


class TestBuildImage:
    """build_image 图片消息"""

    def test_content_is_json_string(self, builder):
        result = builder.build_image("img_v2_test")
        assert result["msg_type"] == "image"
        assert isinstance(result["content"], str)
        parsed = json.loads(result["content"])
        assert parsed == {"image_key": "img_v2_test"}


class TestBuildFile:
    """build_file 文件消息"""

    def test_content_is_json_string(self, builder):
        result = builder.build_file("file_v2_test", "report.pdf")
        assert result["msg_type"] == "file"
        assert isinstance(result["content"], str)
        parsed = json.loads(result["content"])
        assert parsed == {"file_key": "file_v2_test", "file_name": "report.pdf"}


class TestDetectMessageType:
    """detect_message_type 智能选择"""

    def test_plain_text_returns_text(self, builder):
        assert builder.detect_message_type("普通文本") == "text"

    def test_markdown_returns_post(self, builder):
        assert builder.detect_message_type("**加粗**文字") == "post"
        assert builder.detect_message_type("# 标题") == "post"
        assert builder.detect_message_type("[链接](https://x.com)") == "post"
        assert builder.detect_message_type("- 列表项") == "post"
        assert builder.detect_message_type("`代码`") == "post"

    def test_with_files_returns_file(self, builder):
        # 有文件时优先返回 file
        assert builder.detect_message_type("随便", downloadable_files=[{"id": "x"}]) == "file"


class TestMarkdownToPostParagraphs:
    """markdown_to_post_paragraphs 转换"""

    def test_plain_line(self, builder):
        result = builder.markdown_to_post_paragraphs("hello world")
        assert result == [[{"tag": "text", "text": "hello world"}]]

    def test_bold(self, builder):
        result = builder.markdown_to_post_paragraphs("这是 **加粗** 文字")
        assert len(result) == 1
        elements = result[0]
        assert elements[0] == {"tag": "text", "text": "这是 "}
        assert elements[1] == {"tag": "text", "text": "加粗", "style": ["bold"]}
        assert elements[2] == {"tag": "text", "text": " 文字"}

    def test_link(self, builder):
        result = builder.markdown_to_post_paragraphs("点击 [这里](https://x.com)")
        elements = result[0]
        assert elements[-1] == {"tag": "a", "text": "这里", "href": "https://x.com"}

    def test_multiple_paragraphs(self, builder):
        text = "第一段\n\n第二段"
        result = builder.markdown_to_post_paragraphs(text)
        assert len(result) == 2


class TestSplitLongMessage:
    """split_long_message 三级拆分"""

    def test_short_message_not_split(self, builder):
        result = builder.split_long_message("短消息")
        assert result == ["短消息"]

    def test_empty_message(self, builder):
        assert builder.split_long_message("") == []

    def test_split_by_paragraph(self, builder):
        # 两段各 3000 字节，单段不超限，合并后 6001 字节超限 → 按段落拆成 2 段
        para1 = "a" * 3000
        para2 = "b" * 3000
        text = f"{para1}\n\n{para2}"
        result = builder.split_long_message(text, max_bytes=5000)
        assert len(result) == 2
        # 每段都不超过 max_bytes
        for part in result:
            assert len(part.encode("utf-8")) <= 5000
        assert para1 in result[0]
        assert para2 in result[1]

    def test_split_by_line_when_paragraph_too_long(self, builder):
        # 单段超过 max_bytes，但每行不超过
        lines = ["行" * 100 for _ in range(50)]  # 每行 300 字节
        text = "\n".join(lines)  # 共 15000 字节
        result = builder.split_long_message(text, max_bytes=1000)
        assert len(result) > 1
        for part in result:
            assert len(part.encode("utf-8")) <= 1000

    def test_split_by_byte_for_very_long_line(self, builder):
        # 单行超过 max_bytes
        long_line = "字" * 5000  # 15000 字节
        result = builder.split_long_message(long_line, max_bytes=1000)
        assert len(result) > 1
        for part in result:
            assert len(part.encode("utf-8")) <= 1000
        # 拼接后内容不变
        assert "".join(result) == long_line

    def test_chinese_char_not_truncated(self, builder):
        """UTF-8 中文字符 3 字节，不能在字节中间截断"""
        text = "中" * 500  # 1500 字节
        result = builder.split_long_message(text, max_bytes=100)
        reconstructed = "".join(result)
        assert reconstructed == text


class TestHelperFunctions:
    """辅助函数测试"""

    def test_has_markdown_pattern(self):
        assert _has_markdown_pattern("**bold**") is True
        assert _has_markdown_pattern("# heading") is True
        assert _has_markdown_pattern("plain text only") is False
        assert _has_markdown_pattern("- list") is True

    def test_parse_inline_elements_plain(self):
        elements = _parse_inline_elements("plain text")
        assert elements == [{"tag": "text", "text": "plain text"}]

    def test_parse_inline_elements_mixed(self):
        elements = _parse_inline_elements("A **B** C [D](https://x.com) E")
        # A / bold B / C / link D / E
        tags = [e["tag"] for e in elements]
        assert "a" in tags
        assert any(e.get("style") == ["bold"] for e in elements)
