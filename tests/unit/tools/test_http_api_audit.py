"""http_api 审计日志测试：请求/响应原文写入独立审计 sink"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.logging import _is_http_audit
from src.tools.network.http_api import (
    _AUDIT_BODY_LIMIT,
    HttpApiTool,
    _redact_mapping,
)

pytestmark = pytest.mark.tools


def _make_response(status_code=200, json_data=None, text='{"Code": 0}'):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else {"Code": 0}
    resp.text = text
    return resp


def _patch_client():
    """构造 AsyncClient mock 上下文"""
    return patch("src.tools.network.http_api.httpx.AsyncClient")


def _audit_records(mock_logger):
    """从 mock logger 中提取审计记录（单行 JSON）"""
    records = []
    for call in mock_logger.bind.return_value.info.call_args_list:
        records.append(json.loads(call.args[0]))
    return records


class TestRedactMapping:
    def test_sensitive_keys_redacted(self):
        data = {
            "mobile": "13916323347",
            "api_key": "sk-secret",
            "Authorization": "Bearer xxx",
            "nested": {"access_token": "t", "name": "张三"},
        }
        redacted = _redact_mapping(data)
        assert redacted["mobile"] == "13916323347"
        assert redacted["api_key"] == "[REDACTED]"
        assert redacted["Authorization"] == "[REDACTED]"
        assert redacted["nested"]["access_token"] == "[REDACTED]"
        assert redacted["nested"]["name"] == "张三"


class TestAuditFilter:
    def test_filter_matches_bind_flag(self):
        audit_record = {"extra": {"http_audit": True}}
        normal_record = {"extra": {}}
        assert _is_http_audit(audit_record) is True
        assert _is_http_audit(normal_record) is False


class TestExecuteAudit:
    @pytest.mark.asyncio
    async def test_success_call_writes_audit(self):
        tool = HttpApiTool()
        response = _make_response(200, {"Code": 0}, '{"Code": 0}')

        with patch("src.tools.network.http_api.logger") as mock_logger, \
             _patch_client() as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(return_value=response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await tool.execute(
                method="POST",
                url="https://erp.example.com/api/customer/create",
                query_params={"limit": "1"},
                body={"unionid": "wm_abc", "weixinnicheng": "夜未央",
                      "lianxidianhua": "13916323347"},
            )

        assert result["success"] is True
        records = _audit_records(mock_logger)
        assert len(records) == 1
        rec = records[0]
        assert rec["method"] == "POST"
        assert rec["url"] == "https://erp.example.com/api/customer/create"
        assert rec["status_code"] == 200
        # 请求原文完整保留（含手机号，供追溯）
        assert "13916323347" in rec["request_body"]["body"]
        assert "夜未央" in rec["request_body"]["body"]
        # 响应原文
        assert rec["response"]["body"] == '{"Code": 0}'
        assert rec["response"]["truncated"] is False
        assert isinstance(rec["duration_ms"], int)

    @pytest.mark.asyncio
    async def test_sensitive_query_param_redacted(self):
        tool = HttpApiTool()
        response = _make_response(200, {"ok": True}, '{"ok": true}')

        with patch("src.tools.network.http_api.logger") as mock_logger, \
             _patch_client() as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(return_value=response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await tool.execute(
                method="GET",
                url="https://api.example.com/list",
                query_params={"access_token": "sk-real-token", "limit": "1"},
            )

        rec = _audit_records(mock_logger)[0]
        assert rec["query_params"]["access_token"] == "[REDACTED]"
        assert rec["query_params"]["limit"] == "1"

    @pytest.mark.asyncio
    async def test_url_query_never_recorded(self):
        """URL 中的 query（可能含真实凭证）被剥离"""
        tool = HttpApiTool()
        response = _make_response(200, {"ok": True}, '{"ok": true}')

        with patch("src.tools.network.http_api.logger") as mock_logger, \
             _patch_client() as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(return_value=response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await tool.execute(method="GET", url="https://api.example.com/list?token=abc")

        rec = _audit_records(mock_logger)[0]
        assert rec["url"] == "https://api.example.com/list"

    @pytest.mark.asyncio
    async def test_timeout_writes_audit_with_error(self):
        import httpx

        tool = HttpApiTool()
        with patch("src.tools.network.http_api.logger") as mock_logger, \
             _patch_client() as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await tool.execute(method="POST", url="https://api.example.com/x")

        assert result["success"] is False
        records = _audit_records(mock_logger)
        assert len(records) == 1
        rec = records[0]
        assert rec["status_code"] is None
        assert "超时" in rec["error"]
        assert rec["response"] is None

    @pytest.mark.asyncio
    async def test_large_response_truncated(self):
        tool = HttpApiTool()
        big_text = "x" * (_AUDIT_BODY_LIMIT + 100)
        response = _make_response(200, {"ok": True}, big_text)

        with patch("src.tools.network.http_api.logger") as mock_logger, \
             _patch_client() as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(return_value=response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await tool.execute(method="GET", url="https://api.example.com/big")

        rec = _audit_records(mock_logger)[0]
        assert rec["response"]["truncated"] is True
        assert len(rec["response"]["body"]) == _AUDIT_BODY_LIMIT

    @pytest.mark.asyncio
    async def test_validation_failure_no_audit(self):
        """未发请求的参数校验失败不产生审计记录"""
        tool = HttpApiTool()
        with patch("src.tools.network.http_api.logger") as mock_logger:
            result = await tool.execute(method="GET", url="")
        assert result["success"] is False
        assert mock_logger.bind.return_value.info.call_args_list == []
