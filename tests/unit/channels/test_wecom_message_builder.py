"""
企业微信消息构建器单元测试

测试消息类型构建、长消息拆分、消息类型检测。
"""

import pytest

from src.channels.wecom.message_builder import WeComMessageBuilder, _split_by_bytes


class TestBuildText:
    """文本消息构建测试"""

    def test_build_text(self):
        msg = WeComMessageBuilder.build_text("Hello", "1000001")
        assert msg["msgtype"] == "text"
        assert msg["agentid"] == "1000001"
        assert msg["text"]["content"] == "Hello"

    def test_build_text_empty(self):
        msg = WeComMessageBuilder.build_text("", "1000001")
        assert msg["text"]["content"] == ""


class TestBuildMarkdown:
    """Markdown 消息构建测试"""

    def test_build_markdown(self):
        content = "# 标题\n\n**粗体**"
        msg = WeComMessageBuilder.build_markdown(content, "1000001")
        assert msg["msgtype"] == "markdown"
        assert msg["markdown"]["content"] == content


class TestBuildTextCard:
    """文本卡片消息构建测试"""

    def test_build_textcard(self):
        msg = WeComMessageBuilder.build_textcard(
            "标题", "描述", "https://example.com", "1000001"
        )
        assert msg["msgtype"] == "textcard"
        assert msg["textcard"]["title"] == "标题"
        assert msg["textcard"]["url"] == "https://example.com"
        assert msg["textcard"]["btntxt"] == "详情"

    def test_build_textcard_custom_btn(self):
        msg = WeComMessageBuilder.build_textcard(
            "T", "D", "https://x.com", "1", btntxt="查看"
        )
        assert msg["textcard"]["btntxt"] == "查看"


class TestBuildImage:
    """图片消息构建测试"""

    def test_build_image(self):
        msg = WeComMessageBuilder.build_image("media_123", "1000001")
        assert msg["msgtype"] == "image"
        assert msg["image"]["media_id"] == "media_123"


class TestBuildFile:
    """文件消息构建测试"""

    def test_build_file(self):
        msg = WeComMessageBuilder.build_file("media_456", "1000001")
        assert msg["msgtype"] == "file"
        assert msg["file"]["media_id"] == "media_456"


class TestBuildNews:
    """图文消息构建测试"""

    def test_build_news(self):
        articles = [
            {"title": "A1", "url": "https://a.com"},
            {"title": "A2", "url": "https://b.com"},
        ]
        msg = WeComMessageBuilder.build_news(articles, "1000001")
        assert msg["msgtype"] == "news"
        assert len(msg["news"]["articles"]) == 2

    def test_build_news_max_8(self):
        """超过 8 条时应截断"""
        articles = [{"title": f"A{i}", "url": f"https://{i}.com"} for i in range(12)]
        msg = WeComMessageBuilder.build_news(articles, "1000001")
        assert len(msg["news"]["articles"]) == 8


class TestDetectMessageType:
    """消息类型检测测试"""

    def test_detect_markdown_heading(self):
        assert WeComMessageBuilder.detect_message_type("# 标题\n正文") == "markdown"

    def test_detect_markdown_bold(self):
        assert WeComMessageBuilder.detect_message_type("这是**粗体**文本") == "markdown"

    def test_detect_markdown_list(self):
        assert WeComMessageBuilder.detect_message_type("- 项目1\n- 项目2") == "markdown"

    def test_detect_markdown_link(self):
        assert WeComMessageBuilder.detect_message_type("[链接](https://x.com)") == "markdown"

    def test_detect_plain_text(self):
        assert WeComMessageBuilder.detect_message_type("普通纯文本消息") == "markdown"

    def test_detect_plain_text_default_text(self):
        assert WeComMessageBuilder.detect_message_type("普通纯文本", "text") == "text"


class TestSplitLongMessage:
    """长消息拆分测试"""

    def test_short_message_no_split(self):
        """短消息不拆分"""
        text = "短消息"
        parts = WeComMessageBuilder.split_long_message(text, max_bytes=2048)
        assert len(parts) == 1
        assert parts[0] == text

    def test_empty_message(self):
        parts = WeComMessageBuilder.split_long_message("", max_bytes=2048)
        assert parts == []

    def test_split_on_paragraph_boundary(self):
        """按段落边界拆分"""
        para1 = "第一段" * 200  # ~600 bytes
        para2 = "第二段" * 200  # ~600 bytes
        para3 = "第三段" * 200  # ~600 bytes
        text = f"{para1}\n\n{para2}\n\n{para3}"

        # 设置较小的 max_bytes 强制拆分
        parts = WeComMessageBuilder.split_long_message(text, max_bytes=800)
        assert len(parts) >= 2
        # 每段不超过限制
        for part in parts:
            assert len(part.encode("utf-8")) <= 800

    def test_split_preserves_content(self):
        """拆分后的内容拼接应与原始内容一致"""
        text = "段落A" * 100 + "\n\n" + "段落B" * 100 + "\n\n" + "段落C" * 100
        parts = WeComMessageBuilder.split_long_message(text, max_bytes=1000)
        reconstructed = "\n\n".join(parts)
        # 内容应完整保留
        assert "段落A" in reconstructed
        assert "段落B" in reconstructed
        assert "段落C" in reconstructed

    def test_split_single_long_paragraph(self):
        """单个超长段落按行拆分"""
        lines = [f"这是第{i}行内容" for i in range(50)]
        text = "\n".join(lines)

        parts = WeComMessageBuilder.split_long_message(text, max_bytes=500)
        assert len(parts) >= 2
        for part in parts:
            assert len(part.encode("utf-8")) <= 500

    def test_split_multibyte_utf8(self):
        """多字节 UTF-8 字符的拆分"""
        # 中文字符每字 3 字节
        text = "测" * 1000  # 3000 bytes
        parts = WeComMessageBuilder.split_long_message(text, max_bytes=500)
        assert len(parts) > 1
        total = "".join(parts)
        assert total == text


class TestSplitByBytes:
    """按字节拆分测试"""

    def test_short_string(self):
        result = _split_by_bytes("abc", 100)
        assert result == ["abc"]

    def test_empty_string(self):
        result = _split_by_bytes("", 100)
        assert result == []

    def test_exact_boundary(self):
        result = _split_by_bytes("abcdef", 3)
        assert result == ["abc", "def"]

    def test_multibyte_no_truncation(self):
        """确保不截断 UTF-8 字符"""
        text = "中文测试"
        result = _split_by_bytes(text, 6)  # 6 bytes = 2 个中文字符
        for part in result:
            # 每段应是有效字符串
            assert len(part.encode("utf-8")) <= 6
