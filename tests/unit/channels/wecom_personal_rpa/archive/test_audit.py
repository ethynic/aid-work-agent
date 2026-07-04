"""archive.audit 单元测试

覆盖：
- 各 log_* 函数正确调用 db.write_audit
- payload 序列化正确
- 异常路径不抛错（审计失败仅日志，不阻断业务）
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from src.channels.wecom_personal_rpa.archive import audit


@pytest.fixture
def mock_write_audit():
    """mock db.write_audit 避免触达真实 DB。"""
    with patch.object(audit.rpa_db, "write_audit") as m:
        m.return_value = "audit_id_test"
        yield m


def test_log_callback_received_success(mock_write_audit):
    """验签通过事件 → category=archive_callback_received。"""
    audit.log_callback_received("t1", "chan1", verify_ok=True)
    mock_write_audit.assert_called_once()
    args = mock_write_audit.call_args
    assert args.kwargs["tenant_id"] == "t1"
    assert args.kwargs["category"] == "archive_callback_received"
    payload = json.loads(args.kwargs["payload_json"])
    assert payload["config_id"] == "chan1"
    assert payload["verify_ok"] is True


def test_log_callback_received_failed(mock_write_audit):
    """验签失败事件 → category=archive_callback_verify_failed。"""
    audit.log_callback_received("t1", "chan1", verify_ok=False, detail="签名不匹配")
    args = mock_write_audit.call_args
    assert args.kwargs["category"] == "archive_callback_verify_failed"
    payload = json.loads(args.kwargs["payload_json"])
    assert payload["verify_ok"] is False
    assert payload["detail"] == "签名不匹配"


def test_log_fetch_success(mock_write_audit):
    """拉取成功事件含 batch_size / processed / last_seq。"""
    audit.log_fetch_success(
        "t1", "chan1", source="fetcher",
        batch_size=100, processed=98, last_seq=5001,
    )
    payload = json.loads(mock_write_audit.call_args.kwargs["payload_json"])
    assert payload["batch_size"] == 100
    assert payload["processed"] == 98
    assert payload["last_seq"] == 5001
    assert payload["source"] == "fetcher"


def test_log_fetch_rate_limited(mock_write_audit):
    """45009 事件含 retry_after_seconds。"""
    audit.log_fetch_rate_limited("t1", "chan1", retry_after_seconds=60)
    payload = json.loads(mock_write_audit.call_args.kwargs["payload_json"])
    assert payload["retry_after_seconds"] == 60


def test_log_fetch_error_truncates_long_message(mock_write_audit):
    """错误消息过长会被截断（≤300 字符）防 audit 表 payload 字段超限。"""
    long_msg = "x" * 1000
    audit.log_fetch_error("t1", "chan1", "ValueError", long_msg, stage="decrypt")
    payload = json.loads(mock_write_audit.call_args.kwargs["payload_json"])
    assert payload["error_type"] == "ValueError"
    assert payload["stage"] == "decrypt"
    assert len(payload["error_msg"]) == 300


def test_log_callback_received_truncates_detail(mock_write_audit):
    """detail 过长会被截断（≤200）。"""
    long_detail = "y" * 500
    audit.log_callback_received("t1", "chan1", verify_ok=False, detail=long_detail)
    payload = json.loads(mock_write_audit.call_args.kwargs["payload_json"])
    assert len(payload["detail"]) == 200


def test_audit_failure_does_not_raise(mock_write_audit):
    """db.write_audit 抛异常时 log_* 不应抛出（业务不应被审计失败阻断）。"""
    mock_write_audit.side_effect = RuntimeError("DB down")
    # 不抛异常
    audit.log_fetch_success("t1", "chan1", "fetcher", 10, 10, 100)
    audit.log_callback_received("t1", "chan1", verify_ok=True)
    audit.log_fetch_error("t1", "chan1", "ErrType", "msg")
