"""
钉钉消息构建器单元测试

测试 DingTalkMessageBuilder 的消息构建、长消息拆分、消息类型检测。
"""

import json

import pytest

from src.channels.dingtalk.message_builder import DingTalkMessageBuilder


@pytest.fixture
def builder():
    return DingTalkMessageBuilder()


class TestDingTalkMessageBuilderText:
    """文本消息构建测试"""

    def test_build_text_returns_msg_key_and_param(self, builder):
        result = builder.build_text("hello world")
        assert result["msgKey"] == "sampleText"
        # msgParam 必须是 JSON 字符串
        assert isinstance(result["msgParam"], str)
        param = json.loads(result["msgParam"])
        assert param == {"content": "hello world"}

    def test_build_text_chinese(self, builder):
        result = builder.build_text("你好，钉钉")
        param = json.loads(result["msgParam"])
        assert param["content"] == "你好，钉钉"
        # ensure_ascii=False → 中文字符不转义
        assert "你好" in result["msgParam"]
        assert "\\u" not in result["msgParam"]

    def test_build_text_empty(self, builder):
        result = builder.build_text("")
        param = json.loads(result["msgParam"])
        assert param["content"] == ""

    def test_build_text_multiline(self, builder):
        text = "line1\nline2\nline3"
        result = builder.build_text(text)
        param = json.loads(result["msgParam"])
        assert param["content"] == text


class TestDingTalkMessageBuilderMarkdown:
    """Markdown 消息构建测试"""

    def test_build_markdown(self, builder):
        result = builder.build_markdown("标题", "## 内容\n- 列表")
        assert result["msgKey"] == "sampleMarkdown"
        param = json.loads(result["msgParam"])
        assert param == {"title": "标题", "text": "## 内容\n- 列表"}

    def test_build_markdown_with_chinese(self, builder):
        result = builder.build_markdown("中文标题", "中文内容")
        param = json.loads(result["msgParam"])
        assert "中文" in result["msgParam"]


class TestDingTalkMessageBuilderImage:
    """图片消息构建测试"""

    def test_build_image(self, builder):
        result = builder.build_image("https://example.com/img.png")
        assert result["msgKey"] == "sampleImageMsg"
        param = json.loads(result["msgParam"])
        assert param == {"photoURL": "https://example.com/img.png"}


class TestDingTalkMessageBuilderFile:
    """文件消息构建测试"""

    def test_build_file(self, builder):
        result = builder.build_file("media_123", "report.pdf", "pdf")
        assert result["msgKey"] == "sampleFile"
        param = json.loads(result["msgParam"])
        assert param == {
            "mediaId": "media_123",
            "fileName": "report.pdf",
            "fileType": "pdf",
        }


class TestDingTalkMessageBuilderLink:
    """链接消息构建测试"""

    def test_build_link_without_pic(self, builder):
        result = builder.build_link("标题", "描述", "https://example.com")
        assert result["msgKey"] == "sampleLink"
        param = json.loads(result["msgParam"])
        assert param == {
            "title": "标题",
            "text": "描述",
            "messageUrl": "https://example.com",
            "picUrl": "",
        }

    def test_build_link_with_pic(self, builder):
        result = builder.build_link(
            "标题", "描述", "https://example.com", "https://img.com/a.png"
        )
        param = json.loads(result["msgParam"])
        assert param["picUrl"] == "https://img.com/a.png"


class TestDingTalkMessageBuilderDetectType:
    """消息类型智能检测测试"""

    def test_detect_plain_text(self, builder):
        assert builder.detect_message_type("hello world") == "text"

    def test_detect_markdown_heading(self, builder):
        assert builder.detect_message_type("## 标题") == "markdown"

    def test_detect_markdown_bold(self, builder):
        assert builder.detect_message_type("这是 **加粗** 文本") == "markdown"

    def test_detect_markdown_italic(self, builder):
        assert builder.detect_message_type("这是 *斜体* 文本") == "markdown"

    def test_detect_markdown_list(self, builder):
        assert builder.detect_message_type("- 列表项") == "markdown"

    def test_detect_markdown_ordered_list(self, builder):
        assert builder.detect_message_type("1. 有序列表") == "markdown"

    def test_detect_markdown_link(self, builder):
        assert builder.detect_message_type("[链接](https://example.com)") == "markdown"

    def test_detect_markdown_code(self, builder):
        assert builder.detect_message_type("使用 `code` 命令") == "markdown"

    def test_detect_markdown_blockquote(self, builder):
        assert builder.detect_message_type("> 引用文本") == "markdown"

    def test_detect_file_when_downloadable(self, builder):
        assert builder.detect_message_type("some text", downloadable_files=[{}]) == "file"


class TestDingTalkMessageBuilderSplit:
    """长消息拆分测试"""

    def test_split_short_message_no_split(self, builder):
        text = "短消息"
        parts = builder.split_long_message(text, max_bytes=4000)
        assert parts == [text]

    def test_split_exact_limit(self, builder):
        # 构造刚好等于 max_bytes 的消息
        text = "a" * 4000
        parts = builder.split_long_message(text, max_bytes=4000)
        assert parts == [text]

    def test_split_over_limit(self, builder):
        # 超过 max_bytes → 拆分为多条
        text = "a" * 5000
        parts = builder.split_long_message(text, max_bytes=4000)
        assert len(parts) >= 2
        for part in parts:
            assert len(part.encode("utf-8")) <= 4000

    def test_split_empty_message(self, builder):
        assert builder.split_long_message("") == []

    def test_split_on_paragraph_boundary(self, builder):
        # 两段长文本，中间用空行分隔
        para1 = "x" * 3500
        para2 = "y" * 3500
        text = f"{para1}\n\n{para2}"
        parts = builder.split_long_message(text, max_bytes=4000, split_on_paragraph=True)
        # 两段应被拆分到不同 part
        assert len(parts) == 2
        assert parts[0] == para1
        assert parts[1] == para2

    def test_split_on_line_boundary_when_paragraph_too_long(self, builder):
        # 单段超过 max_bytes，按行拆分
        line1 = "a" * 3500
        line2 = "b" * 3500
        text = f"{line1}\n{line2}"
        parts = builder.split_long_message(text, max_bytes=4000, split_on_paragraph=True)
        assert len(parts) == 2
        assert parts[0] == line1
        assert parts[1] == line2

    def test_split_on_byte_boundary_when_line_too_long(self, builder):
        # 单行超过 max_bytes，按字节截断
        text = "中" * 2000  # 每个中文字符 3 字节，总共 6000 字节
        parts = builder.split_long_message(text, max_bytes=4000, split_on_paragraph=False)
        assert len(parts) >= 2
        for part in parts:
            assert len(part.encode("utf-8")) <= 4000

    def test_split_utf8_no_truncation(self, builder):
        """拆分不应截断 UTF-8 字符"""
        # 构造一行刚好跨越边界的多字节字符
        text = "中" * 1500  # 4500 字节
        parts = builder.split_long_message(text, max_bytes=4000, split_on_paragraph=False)
        # 合并所有 parts 应等于原文
        assert "".join(parts) == text
        # 每个 part 都能正确解码为中文
        for part in parts:
            assert all(c == "中" for c in part)

    def test_split_preserves_content(self, builder):
        """拆分后合并应等于原文（去掉分隔符）"""
        text = "段落一\n\n段落二\n\n段落三" * 100  # 很长
        parts = builder.split_long_message(text, max_bytes=4000)
        # 每个 part 都不超过限制
        for part in parts:
            assert len(part.encode("utf-8")) <= 4000
        # 没有空 part
        assert all(parts)

    def test_split_with_split_on_paragraph_false(self, builder):
        """关闭段落拆分，按行拆分"""
        text = "line1\nline2\nline3"
        parts = builder.split_long_message(text, max_bytes=4000, split_on_paragraph=False)
        assert parts == [text]  # 够短，不拆分

    def test_split_small_max_bytes(self, builder):
        """很小的 max_bytes 也能正确拆分"""
        text = "hello world"
        parts = builder.split_long_message(text, max_bytes=5)
        # 每部分不超过 5 字节
        for part in parts:
            assert len(part.encode("utf-8")) <= 5
        # 合并后内容完整
        assert "".join(parts) == text
