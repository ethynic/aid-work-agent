"""external_push 智能体身份鉴权头（AGENT_TOKEN）自动注入单测

覆盖 2026-09-17 erp11095 Code=-99「请求缺少身份令牌」事故修复：
- parse_api_meta 解析 api-meta 的可选 agent_token_header 键（缺省 Api-Authorize-Token）
- _ensure_agent_token_header 把 ${AGENT_TOKEN} 占位符强制注入 http_api 调用 headers
  （缺头注入 / 已有头覆盖 / 大小写去重 / 误写头清除）
"""

import pytest

from src.services.recap.tasks.external_push import (
    _DEFAULT_AGENT_TOKEN_HEADER,
    _ensure_agent_token_header,
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


class TestParseApiMetaAgentTokenHeader:
    def test_default_header_when_not_declared(self):
        meta = parse_api_meta(_doc(""))
        assert meta is not None
        assert meta["agent_token_header"] == "Api-Authorize-Token"

    def test_declared_header_overrides_default(self):
        meta = parse_api_meta(_doc("agent_token_header: X-Agent-Token"))
        assert meta is not None
        assert meta["agent_token_header"] == "X-Agent-Token"


class TestEnsureAgentTokenHeader:
    META = {"agent_token_header": "Api-Authorize-Token"}

    def test_missing_header_injected(self):
        args = {"method": "POST", "url": "https://erp.example.com/api/v1/x", "body": {"a": 1}}
        corrected = _ensure_agent_token_header(args, self.META)
        assert corrected["headers"]["Api-Authorize-Token"] == "${AGENT_TOKEN}"
        assert corrected["url"] == args["url"]

    def test_existing_header_overwritten(self):
        args = {"headers": {"Api-Authorize-Token": "copied_literal_token"}}
        corrected = _ensure_agent_token_header(args, self.META)
        assert corrected["headers"]["Api-Authorize-Token"] == "${AGENT_TOKEN}"

    def test_wrong_header_with_placeholder_removed(self):
        args = {"headers": {"Authorization": "Bearer ${AGENT_TOKEN}", "X-API-Key": "${AGENT_TOKEN}"}}
        corrected = _ensure_agent_token_header(args, self.META)
        assert "Authorization" not in corrected["headers"]
        assert "X-API-Key" not in corrected["headers"]
        assert corrected["headers"]["Api-Authorize-Token"] == "${AGENT_TOKEN}"

    def test_user_token_header_preserved(self):
        args = {"headers": {"Client-Authorize-Token": "tok_abc123"}}
        corrected = _ensure_agent_token_header(args, self.META)
        assert corrected["headers"]["Client-Authorize-Token"] == "tok_abc123"

    def test_case_insensitive_duplicate_removed(self):
        args = {"headers": {"api-authorize-token": "stale"}}
        corrected = _ensure_agent_token_header(args, self.META)
        assert list(corrected["headers"].keys()) == ["Api-Authorize-Token"]
        assert corrected["headers"]["Api-Authorize-Token"] == "${AGENT_TOKEN}"

    def test_original_args_not_mutated(self):
        args = {"headers": {"Authorization": "Bearer ${AGENT_TOKEN}"}}
        _ensure_agent_token_header(args, self.META)
        assert args["headers"]["Authorization"] == "Bearer ${AGENT_TOKEN}"

    def test_meta_header_name_used(self):
        corrected = _ensure_agent_token_header({}, {"agent_token_header": "X-Agent-Token"})
        assert corrected["headers"]["X-Agent-Token"] == "${AGENT_TOKEN}"

    def test_default_header_constant(self):
        assert _DEFAULT_AGENT_TOKEN_HEADER == "Api-Authorize-Token"
