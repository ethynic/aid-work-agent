"""sms_cli.py 短信计费 §9.2 单元测试

验证：
- 计费身份仅取 AID_* 环境变量（不信任 LLM argv 传参），缺失 fail-closed 拒绝发送
- 余额预检：credit_balance 不足 1 积分拒绝；租户不存在拒绝；异常降级放行
- 发送成功后落账：ChatRecordDB.create 带 credit_cost=1.0、手机号脱敏、source_type
- 发送失败 / 通道不可用不落账
- argv 的 --tenant-id 不参与计费身份（仅日志）
"""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [pytest.mark.skills, pytest.mark.unit]

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[3]
    / "src" / "skills" / "sms-verification-1.0.0" / "scripts" / "sms_cli.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location(
        f"sms_cli_billing_{id(_load_module)}", _SCRIPT_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def mod():
    return _load_module()


def _run_send(mod, argv=None, env=None, send_ok=True, **send_patches):
    """执行 send_sms()，返回 (result, ChatRecordDB mock)"""
    env = env if env is not None else {
        "AID_TENANT_ID": "tenant_001", "AID_USER_ID": "user_001",
        "AID_SESSION_ID": "sess_001",
    }
    sender = MagicMock()
    sender.is_available.return_value = True
    with patch.dict(mod.os.environ, env, clear=False), \
         patch.object(mod.sms_manager, "get_sender", return_value=sender), \
         patch.object(mod, "_check_rate_limit", return_value=None), \
         patch.object(mod, "_check_credit_balance", return_value=None), \
         patch.object(mod, "_ensure_db_pool"), \
         patch.object(mod, "send_sms_code", return_value=send_ok), \
         patch.object(mod, "_record_send") as record_send_mock, \
         patch("src.db.models.ChatRecordDB") as chat_record_mock:
        kwargs = {"mobile": "13800138000", "tenant_id": "spoofed_tenant"}
        kwargs.update(send_patches)
        result = mod.send_sms(**kwargs)
    return result, chat_record_mock, record_send_mock


class TestBillingIdentity:
    def test_identity_from_env_only(self, mod):
        with patch.dict(mod.os.environ, {
            "AID_TENANT_ID": "tenant_001", "AID_USER_ID": "user_001",
            "AID_SESSION_ID": "sess_001",
        }, clear=False):
            identity = mod._resolve_billing_identity()

        assert identity == {"tenant_id": "tenant_001", "user_id": "user_001",
                            "session_id": "sess_001"}

    def test_missing_tenant_rejects_send(self, mod):
        """AID_TENANT_ID 缺失：fail-closed 拒绝，不调 send_sms_code，不落账"""
        result, chat_record_mock, _ = _run_send(
            mod, env={"AID_TENANT_ID": "", "AID_USER_ID": "", "AID_SESSION_ID": ""})

        assert result["success"] is False
        assert result["reason"] == "billing_identity_missing"
        chat_record_mock.create.assert_not_called()

    def test_argv_tenant_id_not_used_for_billing(self, mod):
        """LLM 传参 tenant_id=spoofed_tenant 不作为计费身份（仅日志）"""
        result, chat_record_mock, _ = _run_send(mod)

        assert result["success"] is True
        create_kwargs = chat_record_mock.create.call_args.kwargs
        assert create_kwargs["tenant_id"] == "tenant_001"


class TestCreditBalance:
    def _db_conn(self, row):
        cursor = MagicMock()
        cursor.fetchone.return_value = row
        conn = MagicMock()
        conn.cursor.return_value = cursor
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=conn)
        ctx.__exit__ = MagicMock(return_value=False)
        return ctx

    def test_insufficient_balance_blocks(self, mod):
        with patch("src.db.database.get_db_connection",
                   return_value=self._db_conn({"credit_balance": 0.5})):
            result = mod._check_credit_balance("tenant_001")

        assert result is not None
        assert result["reason"] == "insufficient_credit"

    def test_sufficient_balance_passes(self, mod):
        with patch("src.db.database.get_db_connection",
                   return_value=self._db_conn({"credit_balance": 10.0})):
            result = mod._check_credit_balance("tenant_001")

        assert result is None

    def test_tenant_not_found_blocks(self, mod):
        with patch("src.db.database.get_db_connection",
                   return_value=self._db_conn(None)):
            result = mod._check_credit_balance("tenant_404")

        assert result is not None
        assert result["reason"] == "tenant_not_found"

    def test_db_error_degrades_to_pass(self, mod):
        """预检异常不阻断发送（落账阶段兜底）"""
        with patch("src.db.database.get_db_connection",
                   side_effect=Exception("db down")):
            result = mod._check_credit_balance("tenant_001")

        assert result is None


class TestSendBilling:
    def test_send_success_records_billing(self, mod):
        result, chat_record_mock, record_send_mock = _run_send(mod)

        assert result["success"] is True
        chat_record_mock.create.assert_called_once()
        kwargs = chat_record_mock.create.call_args.kwargs
        assert kwargs["credit_cost"] == 1.0
        assert kwargs["tenant_id"] == "tenant_001"
        assert kwargs["user_id"] == "user_001"
        assert kwargs["source_type"] == "sms_verification"
        # 手机号脱敏（保留前 3 后 4）
        assert "13800138000" not in kwargs["user_message"]
        assert "138****8000" in kwargs["user_message"]
        assert kwargs["session_id"].startswith("sms_")
        record_send_mock.assert_called_once()

    def test_send_failure_no_billing(self, mod):
        result, chat_record_mock, _ = _run_send(mod, send_ok=False)

        assert result["success"] is False
        chat_record_mock.create.assert_not_called()

    def test_insufficient_balance_rejected_before_send(self, mod):
        sender = MagicMock()
        sender.is_available.return_value = True
        blocked = {"success": False, "error": "积分余额不足",
                   "reason": "insufficient_credit"}
        with patch.dict(mod.os.environ, {"AID_TENANT_ID": "tenant_001"},
                        clear=False), \
             patch.object(mod.sms_manager, "get_sender", return_value=sender), \
             patch.object(mod, "_check_rate_limit", return_value=None), \
             patch.object(mod, "_check_credit_balance", return_value=blocked), \
             patch.object(mod, "send_sms_code") as send_mock, \
             patch("src.db.models.ChatRecordDB") as chat_record_mock:
            result = mod.send_sms(mobile="13800138000")

        assert result["success"] is False
        assert result["reason"] == "insufficient_credit"
        send_mock.assert_not_called()
        chat_record_mock.create.assert_not_called()

    def test_billing_failure_does_not_fail_send(self, mod):
        """短信已发出，落账异常只记日志，不向用户报错"""
        sender = MagicMock()
        sender.is_available.return_value = True
        with patch.dict(mod.os.environ, {
            "AID_TENANT_ID": "tenant_001", "AID_USER_ID": "user_001",
            "AID_SESSION_ID": "sess_001",
        }, clear=False), \
             patch.object(mod.sms_manager, "get_sender", return_value=sender), \
             patch.object(mod, "_check_rate_limit", return_value=None), \
             patch.object(mod, "_check_credit_balance", return_value=None), \
             patch.object(mod, "_ensure_db_pool"), \
             patch.object(mod, "send_sms_code", return_value=True), \
             patch.object(mod, "_record_send"), \
             patch("src.db.models.ChatRecordDB",
                   **{"create.side_effect": Exception("billing down")}):
            result = mod.send_sms(mobile="13800138000")

        assert result["success"] is True
        assert result["expires_in_seconds"] == 900


class TestMaskMobile:
    def test_mask_mobile(self, mod):
        assert mod._mask_mobile("13800138000") == "138****8000"
        assert mod._mask_mobile("123") == "***"
        assert mod._mask_mobile("") == "***"
