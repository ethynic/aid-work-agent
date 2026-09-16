"""external_push 用户身份 token 鉴权头自动注入单测

覆盖 2026-09-16 erp11096 Code=-99 事故修复：
- parse_api_meta 解析 api-meta 的可选 user_token_header 键（缺省 Client-Authorize-Token）
- _ensure_user_token_header 把系统持有的 client_token 强制注入 http_api 调用 headers
  （缺头注入 / 已有头覆盖 / 大小写去重 / headers 为 JSON 字符串 / 空 token 不干预）
"""

import pytest

from src.services.recap.tasks.external_push import (
    _DEFAULT_USER_TOKEN_HEADER,
    _ensure_user_token_header,
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
{header_line}
```

正文
"""


def _doc(header_line: str = "") -> str:
    return _DOC_TEMPLATE.format(header_line=header_line)


class TestParseApiMetaUserTokenHeader:
    def test_default_header_when_not_declared(self):
        meta = parse_api_meta(_doc(""))
        assert meta is not None
        assert meta["user_token_header"] == "Client-Authorize-Token"

    def test_declared_header_overrides_default(self):
        meta = parse_api_meta(_doc("user_token_header: X-User-Token"))
        assert meta is not None
        assert meta["user_token_header"] == "X-User-Token"


class TestEnsureUserTokenHeader:
    META = {"user_token_header": "Client-Authorize-Token"}
    TOKEN = "tok_abc123"

    def test_missing_header_injected(self):
        args = {"method": "POST", "url": "https://erp.example.com/api/v1/x", "body": {"a": 1}}
        corrected = _ensure_user_token_header(args, self.TOKEN, self.META)
        assert corrected["headers"]["Client-Authorize-Token"] == self.TOKEN
        assert corrected["url"] == args["url"]

    def test_existing_header_overwritten_with_fresh_token(self):
        args = {"headers": {"Client-Authorize-Token": "stale_token"}}
        corrected = _ensure_user_token_header(args, self.TOKEN, self.META)
        assert corrected["headers"]["Client-Authorize-Token"] == self.TOKEN

    def test_agent_token_header_preserved(self):
        args = {"headers": {"Api-Authorize-Token": "${AGENT_TOKEN}"}}
        corrected = _ensure_user_token_header(args, self.TOKEN, self.META)
        assert corrected["headers"]["Api-Authorize-Token"] == "${AGENT_TOKEN}"
        assert corrected["headers"]["Client-Authorize-Token"] == self.TOKEN

    def test_case_insensitive_duplicate_removed(self):
        args = {"headers": {"client-authorize-token": "stale"}}
        corrected = _ensure_user_token_header(args, self.TOKEN, self.META)
        assert list(corrected["headers"].keys()) == ["Client-Authorize-Token"]
        assert corrected["headers"]["Client-Authorize-Token"] == self.TOKEN

    def test_headers_as_json_string_parsed(self):
        args = {"headers": '{"Api-Authorize-Token": "${AGENT_TOKEN}"}'}
        corrected = _ensure_user_token_header(args, self.TOKEN, self.META)
        assert corrected["headers"]["Api-Authorize-Token"] == "${AGENT_TOKEN}"
        assert corrected["headers"]["Client-Authorize-Token"] == self.TOKEN

    def test_headers_invalid_json_rebuilt(self):
        args = {"headers": "not-a-json-object"}
        corrected = _ensure_user_token_header(args, self.TOKEN, self.META)
        assert corrected["headers"] == {"Client-Authorize-Token": self.TOKEN}

    def test_original_args_not_mutated(self):
        args = {"headers": {"Client-Authorize-Token": "stale"}}
        _ensure_user_token_header(args, self.TOKEN, self.META)
        assert args["headers"]["Client-Authorize-Token"] == "stale"

    def test_empty_token_untouched(self):
        args = {"method": "POST", "url": "https://erp.example.com/api/v1/x"}
        assert _ensure_user_token_header(args, "", self.META) is args

    def test_meta_header_name_used(self):
        corrected = _ensure_user_token_header(
            {}, self.TOKEN, {"user_token_header": "X-User-Token"}
        )
        assert corrected["headers"]["X-User-Token"] == self.TOKEN

    def test_default_header_constant(self):
        assert _DEFAULT_USER_TOKEN_HEADER == "Client-Authorize-Token"
