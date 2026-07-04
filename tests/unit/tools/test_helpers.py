"""
src.tools._helpers 单元测试

覆盖：
- truncate_text：未超长不截断 / 超长截断 / None/空串边界 / suffix 拼接 / 边界长度
- sanitize_error：固定安全消息 / set 白名单命中 / (set, 分类兜底) / 都不命中走 fallback /
                  字符串入参 / 不泄漏 str(e) 原文
"""

import pytest

pytestmark = [pytest.mark.tools, pytest.mark.unit]

from src.tools._helpers import sanitize_error, truncate_text


# ============================================================
# truncate_text
# ============================================================


class TestTruncateText:
    """文本截断行为测试。"""

    def test_short_text_not_truncated(self):
        """未超长：原样返回，truncated=False。"""
        text, truncated = truncate_text("hello", limit=10)
        assert text == "hello"
        assert truncated is False

    def test_exact_length_not_truncated(self):
        """长度恰好等于 limit：不算超长，不截断。"""
        text, truncated = truncate_text("hello", limit=5)
        assert text == "hello"
        assert truncated is False

    def test_long_text_truncated_with_suffix(self):
        """超长：截到 limit 并拼默认后缀，truncated=True。"""
        text, truncated = truncate_text("abcdefgh", limit=3)
        assert text == "abc..."
        assert truncated is True

    def test_truncated_length_equals_limit_plus_suffix(self):
        """截断后文本长度 == limit + len(suffix)。"""
        limit = 4
        text, truncated = truncate_text("x" * 100, limit=limit)
        assert truncated is True
        assert len(text) == limit + 3  # 默认 suffix "..." = 3

    def test_custom_suffix(self):
        """自定义后缀拼接正确。"""
        text, truncated = truncate_text("abcdefgh", limit=3, suffix="<…>")
        assert text == "abc<…>"
        assert truncated is True

    def test_empty_suffix(self):
        """空后缀：截断后仅剩 limit 个字符。"""
        text, truncated = truncate_text("abcdefgh", limit=3, suffix="")
        assert text == "abc"
        assert truncated is True

    def test_none_returns_empty_string_not_truncated(self):
        """None：返回 ('', False)，不报错。"""
        text, truncated = truncate_text(None, limit=5)
        assert text == ""
        assert truncated is False

    def test_empty_string_not_truncated(self):
        """空串：原样返回，不截断。"""
        text, truncated = truncate_text("", limit=5)
        assert text == ""
        assert truncated is False

    def test_invalid_limit_returns_original(self):
        """limit <= 0：不截断，原样返回，避免吞掉全部内容。"""
        for bad_limit in (0, -1):
            text, truncated = truncate_text("hello", limit=bad_limit)
            assert text == "hello"
            assert truncated is False

    def test_multibyte_chars_counted_by_codepoint(self):
        """多字节字符按码点计数（中文一个字算 1）。"""
        text, truncated = truncate_text("你好世界测试", limit=4)
        assert truncated is True
        assert text == "你好世界..."


# ============================================================
# sanitize_error
# ============================================================


class NetError(Exception):
    """模拟网络异常。"""


class SecurityError(Exception):
    """模拟安全异常（对标 ppt HtmlExportSecurityError）。"""


class OtherError(Exception):
    """模拟未登记异常。"""


class TestSanitizeError:
    """错误脱敏行为测试。"""

    def test_fixed_safe_message_by_type(self):
        """形态1：异常类型命中即返回固定安全消息。"""
        safe = {NetError: "网络不可用，请稍后重试"}
        result = sanitize_error(NetError("connection refused to db://internal"), safe)
        assert result == "网络不可用，请稍后重试"

    def test_set_whitelist_hit_transparent_message(self):
        """形态2：set 白名单命中，透传 str(error)。"""
        safe = {SecurityError: {"文件超过 10MB 限制", "文件不存在"}}
        result = sanitize_error(SecurityError("文件超过 10MB 限制"), safe)
        assert result == "文件超过 10MB 限制"

    def test_set_whitelist_miss_falls_back(self):
        """形态2：set 白名单未命中，走兜底 fallback（不透传 str(e)）。"""
        safe = {SecurityError: {"文件超过 10MB 限制"}}
        result = sanitize_error(SecurityError("SECRET_INTERNAL detail=xyz"), safe)
        assert result == "操作失败，请稍后重试"

    def test_tuple_whitelist_hit(self):
        """形态3：(set, 分类兜底) 命中透传。"""
        safe = {SecurityError: ({"文件超过 10MB 限制"}, "HTML 文件未通过安全检查")}
        result = sanitize_error(SecurityError("文件超过 10MB 限制"), safe)
        assert result == "文件超过 10MB 限制"

    def test_tuple_whitelist_miss_uses_category_fallback(self):
        """形态3：未命中集合，返回分类兜底消息（而非全局 fallback）。"""
        safe = {SecurityError: ({"文件超过 10MB 限制"}, "HTML 文件未通过安全检查")}
        result = sanitize_error(SecurityError("UNKNOWN detail"), safe)
        assert result == "HTML 文件未通过安全检查"

    def test_no_type_match_uses_fallback(self):
        """异常类型未在白名单：走 fallback。"""
        safe = {NetError: "网络不可用，请稍后重试"}
        result = sanitize_error(OtherError("db password=admin"), safe)
        assert result == "操作失败，请稍后重试"

    def test_no_whitelist_uses_fallback(self):
        """未提供 safe_messages：直接走 fallback。"""
        result = sanitize_error(NetError("connection refused"))
        assert result == "操作失败，请稍后重试"

    def test_custom_fallback(self):
        """自定义 fallback 生效。"""
        result = sanitize_error(OtherError("x"), fallback="PPT生成失败，请稍后重试")
        assert result == "PPT生成失败，请稍后重试"

    def test_string_input_in_whitelist_transparent(self):
        """字符串入参：命中 set 白名单则透传该字符串。"""
        safe = {SecurityError: {"文件超过 10MB 限制"}}
        result = sanitize_error("文件超过 10MB 限制", safe)
        assert result == "文件超过 10MB 限制"

    def test_string_input_not_in_whitelist_falls_back(self):
        """字符串入参：不在白名单则走 fallback，不泄漏。"""
        safe = {SecurityError: {"文件超过 10MB 限制"}}
        result = sanitize_error("leaked secret: password=admin", safe)
        assert result == "操作失败，请稍后重试"

    def test_string_input_no_whitelist_falls_back(self):
        """字符串入参 + 无白名单：直接 fallback。"""
        result = sanitize_error("some random message")
        assert result == "操作失败，请稍后重试"

    # ---- 安全性核心断言：绝不泄漏 str(e) 原文 ----

    def test_does_not_leak_exception_message(self):
        """含敏感信息的异常消息绝不出现在返回值中。"""
        secret = "password=admin;token=abc123"
        safe = {ValueError: "输入格式不正确"}
        result = sanitize_error(ValueError(secret), safe)
        assert secret not in result
        assert result == "输入格式不正确"

    def test_does_not_leak_when_no_whitelist(self):
        """无白名单时异常原文也绝不泄漏。"""
        secret = "Traceback (most recent call last): password=admin"
        result = sanitize_error(RuntimeError(secret))
        assert secret not in result
        assert result == "操作失败，请稍后重试"

    def test_none_error_uses_fallback(self):
        """None 入参：走 fallback，不报错。"""
        result = sanitize_error(None, {ValueError: "bad"})
        assert result == "操作失败，请稍后重试"

    def test_empty_fallback_defaults(self):
        """传空 fallback 时回退到默认兜底消息。"""
        result = sanitize_error(OtherError("x"), fallback="")
        assert result == "操作失败，请稍后重试"
