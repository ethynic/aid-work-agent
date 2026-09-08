"""external_push http_method 守卫单测

覆盖 2026-09-08 Code=-99 事故修复：
- parse_api_meta 解析 api-meta 的可选 http_method 键（大写归一 / 未声明移除）
- _normalize_http_method 按声明强制纠正 http_api 调用 method（未声明不干预）
"""

import pytest

from src.services.recap.tasks.external_push import (
    _normalize_http_method,
    parse_api_meta,
)

pytestmark = pytest.mark.unit


_DOC_TEMPLATE = """# 测试租户接口文档

<!-- api-meta -->
```api-meta
login_url: https://erp.example.com/api/v1/erp.delegate/login
auth_mode: delegate_login
user_token_name: client_token
external_userid_field: unionid
{method_line}
```

正文
"""


def _doc(method_line: str = "") -> str:
    return _DOC_TEMPLATE.format(method_line=method_line)


class TestParseApiMetaHttpMethod:
    def test_http_method_declared_normalized_upper(self):
        meta = parse_api_meta(_doc("http_method: post"))
        assert meta is not None
        assert meta["http_method"] == "POST"

    def test_http_method_not_declared_removed(self):
        meta = parse_api_meta(_doc(""))
        assert meta is not None
        assert "http_method" not in meta

    def test_http_method_blank_value_removed(self):
        meta = parse_api_meta(_doc("http_method: "))
        assert meta is not None
        assert "http_method" not in meta

    def test_existing_defaults_still_work(self):
        meta = parse_api_meta(_doc(""))
        assert meta["user_token_name"] == "client_token"
        assert meta["external_userid_field"] == "unionid"


class TestNormalizeHttpMethod:
    META_POST = {"http_method": "POST"}
    META_EMPTY = {}

    def test_model_get_corrected_to_declared_post(self):
        args = {"method": "GET", "url": "https://erp.example.com/api/v1/x", "body": {"a": 1}}
        corrected = _normalize_http_method(args, self.META_POST)
        assert corrected["method"] == "POST"
        assert corrected["url"] == args["url"]
        # 原始 args 不被原地修改
        assert args["method"] == "GET"

    def test_method_case_insensitive_match(self):
        args = {"method": "post", "url": "https://erp.example.com/api/v1/x"}
        assert _normalize_http_method(args, self.META_POST) is args

    def test_missing_method_defaults_get_then_corrected(self):
        args = {"url": "https://erp.example.com/api/v1/x"}
        corrected = _normalize_http_method(args, self.META_POST)
        assert corrected["method"] == "POST"

    def test_no_declaration_untouched(self):
        args = {"method": "GET", "url": "https://erp.example.com/api/v1/x"}
        assert _normalize_http_method(args, self.META_EMPTY) is args
