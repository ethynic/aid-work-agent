"""
协会客户端服务端 API 单测。

重点覆盖：
- 计费 ×10 公式（record_llm_usage：raw_credit × multiplier = credit_cost）
- 激活码生成格式与 hash 校验
- 鉴权中间件 verify_client_token 逻辑
- 激活流程状态流转
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.db.client_binding_db import (
    ClientActivationCodeDB,
    ClientBindingDB,
    ClientUsageLogDB,
    _client_credit_multiplier,
)


# ============== 计费 ×10 公式 ==============

class TestClientCreditMultiplier:
    """客户端积分膨胀系数测试。"""

    def test_multiplier_default_is_5(self):
        """默认系数 5.0"""
        assert _client_credit_multiplier() == 10.0

    def test_multiplier_from_settings(self):
        """从 settings.client.credit_multiplier 读取"""
        with patch("src.db.client_binding_db.settings") as mock_settings:
            mock_settings.client.credit_multiplier = 8.0
            assert _client_credit_multiplier() == 8.0

    def test_multiplier_fallback_on_error(self):
        """settings 异常时回退到 10.0"""
        with patch("src.db.client_binding_db.settings") as mock_settings:
            # 让 getattr 触发异常（PropertyAccessError 或任意异常）
            type(mock_settings.client).credit_multiplier = property(lambda self: (_ for _ in ()).throw(RuntimeError("boom")))
            assert _client_credit_multiplier() == 10.0


class TestRecordLlmUsageBilling:
    """record_llm_usage 计费公式：raw_credit × 10 = credit_cost，2位小数向上取整。"""

    @patch("src.db.client_binding_db.calculate_credit_cost")
    @patch("src.db.client_binding_db.get_db_connection")
    def test_credit_cost_is_10x_raw(self, mock_conn, mock_calc):
        """credit_cost = ceil(raw_credit × 10 × 100) / 100"""
        mock_calc.return_value = 0.40  # 标准积分
        cursor = MagicMock()
        # UPDATE RETURNING 的 fetchone（INSERT RETURNING 结果未被消费）
        cursor.fetchone.return_value = {"credit_balance": 98.0}
        mock_conn.return_value.__enter__.return_value.cursor.return_value = cursor

        result = ClientUsageLogDB.record_llm_usage(
            tenant_id="tenant_test",
            binding_id="cb_test",
            model="deepseek-v4-flash",
            provider="deepseek",
            usage={"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500},
        )

        assert result["raw_credit_cost"] == 0.40
        # 0.40 × 10 = 4.00
        assert result["credit_cost"] == 4.00
        assert result["balance_after"] == 98.0

    @patch("src.db.client_binding_db.calculate_credit_cost")
    @patch("src.db.client_binding_db.get_db_connection")
    def test_credit_cost_rounds_up(self, mock_conn, mock_calc):
        """2位小数向上取整：0.001 × 10 = 0.01"""
        mock_calc.return_value = 0.001
        cursor = MagicMock()
        cursor.fetchone.return_value = {"credit_balance": 99.99}
        mock_conn.return_value.__enter__.return_value.cursor.return_value = cursor

        result = ClientUsageLogDB.record_llm_usage(
            tenant_id="t", binding_id="b", model="m", provider="p",
            usage={"prompt_tokens": 1},
        )
        # ceil(0.001 × 10 × 100) / 100 = ceil(1.0) / 100 = 0.01
        assert result["credit_cost"] == 0.01

    @patch("src.db.client_binding_db.calculate_credit_cost")
    @patch("src.db.client_binding_db.get_db_connection")
    def test_zero_raw_gives_zero_credit(self, mock_conn, mock_calc):
        """raw_credit=0 时不扣费"""
        mock_calc.return_value = 0.0
        cursor = MagicMock()
        cursor.fetchone.return_value = {"id": 1}
        mock_conn.return_value.__enter__.return_value.cursor.return_value = cursor

        result = ClientUsageLogDB.record_llm_usage(
            tenant_id="t", binding_id="b", model="m", provider="p",
            usage={"prompt_tokens": 0, "completion_tokens": 0},
        )
        assert result["credit_cost"] == 0.0
        assert result["raw_credit_cost"] == 0.0

    @patch("src.db.client_binding_db.calculate_credit_cost")
    @patch("src.db.client_binding_db.get_db_connection")
    def test_balance_after_returned_when_deducted(self, mock_conn, mock_calc):
        """扣费后返回 balance_after"""
        mock_calc.return_value = 1.0  # raw=1, cost=10
        cursor = MagicMock()
        cursor.fetchone.return_value = {"credit_balance": 90.0}
        mock_conn.return_value.__enter__.return_value.cursor.return_value = cursor

        result = ClientUsageLogDB.record_llm_usage(
            tenant_id="t", binding_id="b", model="m", provider="p",
            usage={"prompt_tokens": 100},
        )
        assert result["credit_cost"] == 10.0
        assert result["balance_after"] == 90.0


# ============== 激活码生成与校验 ==============

class TestActivationCodeGeneration:
    """激活码格式与 hash 校验。"""

    def test_generate_code_format(self):
        """格式：AC- + 12 位去混淆字符"""
        for _ in range(50):  # 跑50次确认格式稳定
            code = ClientActivationCodeDB.generate_code()
            assert code.startswith("AC-")
            body = code[3:]
            assert len(body) == 12
            # 不含易混淆字符 0/O/1/I/l
            forbidden = set("01OoilI")
            assert not (forbidden & set(body)), f"code={code} 含易混淆字符"

    def test_hash_and_verify_roundtrip(self):
        """hash → verify 往返"""
        code = "AC-ABCDEFGHJKM2"
        h = ClientActivationCodeDB.hash_code(code)
        assert ClientActivationCodeDB.verify_code(code, h) is True
        assert ClientActivationCodeDB.verify_code("AC-WRONGCODE00", h) is False

    def test_verify_handles_malformed_hash(self):
        """损坏的 hash 不崩溃，返回 False"""
        assert ClientActivationCodeDB.verify_code("AC-TEST", "not-a-hash") is False


# ============== 鉴权中间件 ==============

class TestVerifyClientToken:
    """verify_client_token 逻辑（mock DB）。"""

    def test_empty_token_returns_none(self):
        from src.api.client_auth import verify_client_token
        assert verify_client_token("") is None
        assert verify_client_token("   ") is None
        assert verify_client_token(None) is None

    @patch("src.api.client_auth.ClientBindingDB.get_by_token")
    def test_invalid_token_returns_none(self, mock_get):
        """token 查不到绑定返回 None"""
        from src.api.client_auth import verify_client_token
        mock_get.return_value = None
        assert verify_client_token("invalid-token") is None

    @patch("src.api.client_auth.TenantDB.get_by_id")
    @patch("src.api.client_auth.ClientBindingDB.get_by_token")
    def test_disabled_binding_returns_none(self, mock_get, mock_tenant):
        """绑定 status != active 返回 None"""
        from src.api.client_auth import verify_client_token
        mock_get.return_value = {
            "binding_id": "cb_1", "tenant_id": "t1", "status": "disabled",
            "access_token": "tok", "client_name": None,
        }
        assert verify_client_token("tok") is None

    @patch("src.api.client_auth.TenantDB.get_by_id")
    @patch("src.api.client_auth.ClientBindingDB.get_by_token")
    def test_inactive_tenant_returns_none(self, mock_get, mock_tenant):
        """租户 status != active 返回 None"""
        from src.api.client_auth import verify_client_token
        mock_get.return_value = {
            "binding_id": "cb_1", "tenant_id": "t1", "status": "active",
            "access_token": "tok", "client_name": None, "expires_at": None,
        }
        mock_tenant.return_value = {"status": "suspended", "credit_balance": 100}
        assert verify_client_token("tok") is None

    @patch("src.api.client_auth.ClientBindingDB.update_last_seen")
    @patch("src.api.client_auth.TenantDB.get_by_id")
    @patch("src.api.client_auth.ClientBindingDB.get_by_token")
    def test_valid_token_returns_binding(self, mock_get, mock_tenant, mock_seen):
        """有效 token + active 绑定 + active 租户 → 返回 ClientBinding"""
        from src.api.client_auth import verify_client_token, ClientBinding
        mock_get.return_value = {
            "binding_id": "cb_1", "tenant_id": "tenant_abc", "status": "active",
            "access_token": "valid-tok", "client_name": "测试电脑", "expires_at": None,
        }
        mock_tenant.return_value = {
            "status": "active", "credit_balance": 5000.0, "company_name": "测试协会",
        }
        binding = verify_client_token("valid-tok")
        assert binding is not None
        assert binding.binding_id == "cb_1"
        assert binding.tenant_id == "tenant_abc"
        assert binding.tenant["credit_balance"] == 5000.0

    @patch("src.api.client_auth.TenantDB.get_by_id")
    @patch("src.api.client_auth.ClientBindingDB.get_by_token")
    def test_expired_binding_returns_none(self, mock_get, mock_tenant):
        """绑定已过期返回 None"""
        from src.api.client_auth import verify_client_token
        mock_get.return_value = {
            "binding_id": "cb_1", "tenant_id": "t1", "status": "active",
            "access_token": "tok", "client_name": None,
            "expires_at": datetime.now() - timedelta(days=1),  # 昨天过期
        }
        assert verify_client_token("tok") is None


class TestGetClientTokenFromHeader:
    """Authorization 头解析。"""

    def test_valid_bearer(self):
        from src.api.client_auth import get_client_token_from_header
        assert get_client_token_from_header("Bearer abc123") == "abc123"
        assert get_client_token_from_header("bearer abc123") == "abc123"  # 大小写不敏感

    def test_missing_header(self):
        from src.api.client_auth import get_client_token_from_header
        assert get_client_token_from_header(None) is None
        assert get_client_token_from_header("") is None

    def test_wrong_scheme(self):
        from src.api.client_auth import get_client_token_from_header
        assert get_client_token_from_header("Basic abc123") is None

    def test_no_token_part(self):
        from src.api.client_auth import get_client_token_from_header
        assert get_client_token_from_header("Bearer") is None
