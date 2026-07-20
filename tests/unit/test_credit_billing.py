"""
积分计费算法单测（#37）

覆盖：
- 计费算法（mock TokenCostPriceDB.get_by_model_name）
- 单价缺失返回 0
- usage_factor 配置生效
- cached_input_tokens 本期不计入
"""

import math
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture
def fixed_settings():
    """固定 billing.usage_factor = 100 的 settings"""
    settings = MagicMock()
    settings.billing.usage_factor = 100
    return settings


def _make_tcp(input_price_per_m: float, output_price_per_m: float) -> dict:
    """构造 TokenCostPriceDB.get_by_model_name 的返回值"""
    return {
        "model_name": "test-model",
        "input_price_per_m": input_price_per_m,
        "output_price_per_m": output_price_per_m,
    }


class TestCalculateCreditCost:
    def test_basic_calculation(self, fixed_settings):
        """正常路径：积分 = ceil((prompt * input_price + completion * output_price) / 1e6 * usage_factor)"""
        from src.services.billing import calculate_credit_cost

        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db, \
             patch("src.services.billing.create_settings", return_value=fixed_settings):
            mock_tcp_db.get_by_model_name.return_value = _make_tcp(0.8, 2.0)
            # token_cost = (1000 * 0.8 + 500 * 2.0) / 1_000_000 = 0.0018
            # credit_cost = ceil(0.0018 * 100) = 1
            result = calculate_credit_cost(prompt_tokens=1000, completion_tokens=500, model="test-model")
            assert result == 1

    def test_missing_model_returns_zero(self, fixed_settings):
        """model 为空返回 0"""
        from src.services.billing import calculate_credit_cost

        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db, \
             patch("src.services.billing.create_settings", return_value=fixed_settings):
            result = calculate_credit_cost(prompt_tokens=1000, completion_tokens=500, model=None)
            assert result == 0
            mock_tcp_db.get_by_model_name.assert_not_called()

    def test_missing_price_record_returns_zero(self, fixed_settings):
        """token_cost_prices 无匹配记录返回 0"""
        from src.services.billing import calculate_credit_cost

        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db, \
             patch("src.services.billing.create_settings", return_value=fixed_settings):
            mock_tcp_db.get_by_model_name.return_value = None
            result = calculate_credit_cost(prompt_tokens=1000, completion_tokens=500, model="unknown-model")
            assert result == 0

    def test_zero_price_returns_zero(self, fixed_settings):
        """单价全部为 0 返回 0"""
        from src.services.billing import calculate_credit_cost

        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db, \
             patch("src.services.billing.create_settings", return_value=fixed_settings):
            mock_tcp_db.get_by_model_name.return_value = _make_tcp(0, 0)
            result = calculate_credit_cost(prompt_tokens=1000, completion_tokens=500, model="test-model")
            assert result == 0

    def test_usage_factor_takes_effect(self):
        """usage_factor 配置生效：相同 token 数下，系数 200 是系数 100 的 2 倍"""
        from src.services.billing import calculate_credit_cost

        # 大 token 数让结果足够大，避免 ceil 影响
        prompt_tokens = 1_000_000  # 一百万
        completion_tokens = 500_000

        settings_factor_100 = MagicMock()
        settings_factor_100.billing.usage_factor = 100

        settings_factor_200 = MagicMock()
        settings_factor_200.billing.usage_factor = 200

        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db:
            mock_tcp_db.get_by_model_name.return_value = _make_tcp(1.0, 2.0)
            # token_cost = (1e6 * 1.0 + 5e5 * 2.0) / 1e6 = 2.0 元
            with patch("src.services.billing.create_settings", return_value=settings_factor_100):
                cost_100 = calculate_credit_cost(prompt_tokens, completion_tokens, "test-model")
            with patch("src.services.billing.create_settings", return_value=settings_factor_200):
                cost_200 = calculate_credit_cost(prompt_tokens, completion_tokens, "test-model")

        assert cost_100 == 200   # 2.0 * 100
        assert cost_200 == 400   # 2.0 * 200
        assert cost_200 == 2 * cost_100

    def test_zero_tokens_returns_zero(self, fixed_settings):
        """0 token 返回 0"""
        from src.services.billing import calculate_credit_cost

        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db, \
             patch("src.services.billing.create_settings", return_value=fixed_settings):
            mock_tcp_db.get_by_model_name.return_value = _make_tcp(0.8, 2.0)
            result = calculate_credit_cost(prompt_tokens=0, completion_tokens=0, model="test-model")
            assert result == 0

    def test_ceil_rounding_up(self, fixed_settings):
        """向上取整：任何非零 token_cost 都应至少得 1 积分"""
        from src.services.billing import calculate_credit_cost

        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db, \
             patch("src.services.billing.create_settings", return_value=fixed_settings):
            mock_tcp_db.get_by_model_name.return_value = _make_tcp(0.8, 2.0)
            # token_cost = (1 * 0.8 + 0 * 2.0) / 1e6 = 8e-7
            # credit_cost = ceil(8e-7 * 100) = ceil(8e-5) = 1
            result = calculate_credit_cost(prompt_tokens=1, completion_tokens=0, model="test-model")
            assert result == 1
