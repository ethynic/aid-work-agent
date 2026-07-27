"""
积分计费算法单测（#37）

覆盖：
- 计费算法（mock TokenCostPriceDB.get_by_model_name）
- 单价缺失返回 0.0
- usage_factor 配置生效
- cached_input_price_per_m 有值时按新公式计费
- cached_input_price_per_m 为 NULL 时按原公式计费
- 2 位小数精度（向上取整到 0.01）
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


def _make_tcp(
    input_price_per_m: float,
    output_price_per_m: float,
    cached_input_price_per_m=None,
) -> dict:
    """构造 TokenCostPriceDB.get_by_model_name 的返回值

    cached_input_price_per_m 默认 None 表示该模型不区分缓存命中
    """
    return {
        "model_name": "test-model",
        "input_price_per_m": input_price_per_m,
        "cached_input_price_per_m": cached_input_price_per_m,
        "output_price_per_m": output_price_per_m,
    }


class TestCalculateCreditCost:
    def test_basic_calculation(self, fixed_settings):
        """正常路径：积分 = ceil((prompt * input_price + completion * output_price) / 1e6 * usage_factor * 100) / 100"""
        from src.services.billing import calculate_credit_cost

        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db, \
             patch("src.services.billing.create_settings", return_value=fixed_settings):
            mock_tcp_db.get_by_model_name.return_value = _make_tcp(0.8, 2.0)
            # token_cost = (1000 * 0.8 + 500 * 2.0) / 1_000_000 = 0.0018
            # credit_cost = ceil(0.0018 * 100 * 100) / 100 = ceil(18.0) / 100 = 0.18
            result = calculate_credit_cost(prompt_tokens=1000, completion_tokens=500, model="test-model")
            assert result == 0.18

    def test_missing_model_returns_zero(self, fixed_settings):
        """model 为空返回 0.0"""
        from src.services.billing import calculate_credit_cost

        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db, \
             patch("src.services.billing.create_settings", return_value=fixed_settings):
            result = calculate_credit_cost(prompt_tokens=1000, completion_tokens=500, model=None)
            assert result == 0.0
            mock_tcp_db.get_by_model_name.assert_not_called()

    def test_missing_price_record_returns_zero(self, fixed_settings):
        """token_cost_prices 无匹配记录返回 0.0"""
        from src.services.billing import calculate_credit_cost

        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db, \
             patch("src.services.billing.create_settings", return_value=fixed_settings):
            mock_tcp_db.get_by_model_name.return_value = None
            result = calculate_credit_cost(prompt_tokens=1000, completion_tokens=500, model="unknown-model")
            assert result == 0.0

    def test_zero_price_returns_zero(self, fixed_settings):
        """单价全部为 0 返回 0.0"""
        from src.services.billing import calculate_credit_cost

        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db, \
             patch("src.services.billing.create_settings", return_value=fixed_settings):
            mock_tcp_db.get_by_model_name.return_value = _make_tcp(0, 0)
            result = calculate_credit_cost(prompt_tokens=1000, completion_tokens=500, model="test-model")
            assert result == 0.0

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

        # 2.0 * 100 = 200.00（已是 2 位小数，不再进位）
        # 2.0 * 200 = 400.00
        assert cost_100 == 200.0
        assert cost_200 == 400.0
        assert cost_200 == 2 * cost_100

    def test_zero_tokens_returns_zero(self, fixed_settings):
        """0 token 返回 0.0"""
        from src.services.billing import calculate_credit_cost

        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db, \
             patch("src.services.billing.create_settings", return_value=fixed_settings):
            mock_tcp_db.get_by_model_name.return_value = _make_tcp(0.8, 2.0)
            result = calculate_credit_cost(prompt_tokens=0, completion_tokens=0, model="test-model")
            assert result == 0.0

    def test_ceil_rounding_up_to_two_decimals(self, fixed_settings):
        """向上取整到 0.01：任何非零 token_cost 都应至少得 0.01 积分"""
        from src.services.billing import calculate_credit_cost

        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db, \
             patch("src.services.billing.create_settings", return_value=fixed_settings):
            mock_tcp_db.get_by_model_name.return_value = _make_tcp(0.8, 2.0)
            # token_cost = (1 * 0.8 + 0 * 2.0) / 1e6 = 8e-7
            # credit_cost = ceil(8e-7 * 100 * 100) / 100 = ceil(8e-3) / 100 = 1 / 100 = 0.01
            result = calculate_credit_cost(prompt_tokens=1, completion_tokens=0, model="test-model")
            assert result == 0.01

    def test_cached_input_price_takes_effect(self, fixed_settings):
        """cached_input_price_per_m 有值时走新公式：cached 部分按缓存单价，剩余按输入单价"""
        from src.services.billing import calculate_credit_cost

        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db, \
             patch("src.services.billing.create_settings", return_value=fixed_settings):
            # input_price=0.8, cached_input_price=0.16, output_price=2.0
            mock_tcp_db.get_by_model_name.return_value = _make_tcp(0.8, 2.0, cached_input_price_per_m=0.16)
            # prompt=1000, cached=400, completion=500
            # non_cached_input = 1000 - 400 = 600
            # token_cost = (600 * 0.8 + 400 * 0.16 + 500 * 2.0) / 1e6
            #            = (480 + 64 + 1000) / 1e6 = 1544 / 1e6 = 0.001544
            # credit_cost = ceil(0.001544 * 100 * 100) / 100 = ceil(15.44) / 100 = 16 / 100 = 0.16
            result = calculate_credit_cost(
                prompt_tokens=1000,
                completion_tokens=500,
                model="test-model",
                cached_input_tokens=400,
            )
            assert result == 0.16

    def test_cached_input_price_none_fallback(self, fixed_settings):
        """cached_input_price_per_m 为 None 时走原公式：cached_input_tokens 不参与计费"""
        from src.services.billing import calculate_credit_cost

        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db, \
             patch("src.services.billing.create_settings", return_value=fixed_settings):
            mock_tcp_db.get_by_model_name.return_value = _make_tcp(0.8, 2.0, cached_input_price_per_m=None)
            # 即使传了 cached_input_tokens，因 cached_input_price_per_m 为 None 走原公式
            # token_cost = (1000 * 0.8 + 500 * 2.0) / 1e6 = 0.0018
            # credit_cost = ceil(0.0018 * 100 * 100) / 100 = ceil(18.0) / 100 = 0.18
            result = calculate_credit_cost(
                prompt_tokens=1000,
                completion_tokens=500,
                model="test-model",
                cached_input_tokens=400,
            )
            assert result == 0.18

    def test_cached_price_lower_than_input(self, fixed_settings):
        """cached 单价远低于 input 单价时，大量缓存命中应显著降低积分"""
        from src.services.billing import calculate_credit_cost

        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db, \
             patch("src.services.billing.create_settings", return_value=fixed_settings):
            # input_price=3.0, cached_input_price=0.025, output_price=6.0（deepseek-v4-pro）
            mock_tcp_db.get_by_model_name.return_value = _make_tcp(3.0, 6.0, cached_input_price_per_m=0.025)

            # 1M prompt，900k 命中缓存，0 completion
            # 场景 A：cached_input_tokens=900_000
            # non_cached = 100_000
            # token_cost_a = (100_000 * 3.0 + 900_000 * 0.025 + 0) / 1e6
            #              = (300_000 + 22_500) / 1e6 = 0.3225 元
            # credit_cost_a = ceil(0.3225 * 100 * 100) / 100 = ceil(3225.0) / 100 = 32.25
            cost_with_cache = calculate_credit_cost(
                prompt_tokens=1_000_000,
                completion_tokens=0,
                model="test-model",
                cached_input_tokens=900_000,
            )

            # 场景 B：cached_input_tokens=0（无缓存命中）
            # token_cost_b = (1_000_000 * 3.0 + 0) / 1e6 = 3.0 元
            # credit_cost_b = ceil(3.0 * 100 * 100) / 100 = ceil(30000.0) / 100 = 300.0
            cost_without_cache = calculate_credit_cost(
                prompt_tokens=1_000_000,
                completion_tokens=0,
                model="test-model",
                cached_input_tokens=0,
            )

        assert cost_with_cache == 32.25
        assert cost_without_cache == 300.0
        # 缓存命中应显著降低积分（约 1/9）
        assert cost_with_cache < cost_without_cache / 9

    def test_cached_tokens_exceed_prompt_defensive(self, fixed_settings):
        """防御性：cached_input_tokens > prompt_tokens 时按 0 非缓存处理，不产生负数"""
        from src.services.billing import calculate_credit_cost

        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db, \
             patch("src.services.billing.create_settings", return_value=fixed_settings):
            mock_tcp_db.get_by_model_name.return_value = _make_tcp(0.8, 2.0, cached_input_price_per_m=0.16)
            # prompt=500, cached=1000（异常输入），completion=0
            # non_cached_input = max(500 - 1000, 0) = 0
            # token_cost = (0 * 0.8 + 1000 * 0.16 + 0 * 2.0) / 1e6 = 0.00016
            # credit_cost = ceil(0.00016 * 100 * 100) / 100 = ceil(1.6) / 100 = 2 / 100 = 0.02
            result = calculate_credit_cost(
                prompt_tokens=500,
                completion_tokens=0,
                model="test-model",
                cached_input_tokens=1000,
            )
            assert result == 0.02

    def test_two_decimal_precision_no_extra_ceil(self, fixed_settings):
        """2 位小数已是精度上限时不再进位（token_cost * factor = 0.0150 -> 1.50）"""
        from src.services.billing import calculate_credit_cost

        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db, \
             patch("src.services.billing.create_settings", return_value=fixed_settings):
            # 0.0150 元 * 100 = 1.50（已 2 位小数）
            # token_cost = 1_000_000 * 0.0150 / 1e6 = 0.0150
            mock_tcp_db.get_by_model_name.return_value = _make_tcp(0.0150, 0.0)
            result = calculate_credit_cost(
                prompt_tokens=1_000_000,
                completion_tokens=0,
                model="test-model",
            )
            # ceil(0.0150 * 100 * 100) / 100 = ceil(150.0) / 100 = 1.50
            assert result == 1.50

    def test_third_decimal_rounds_up(self, fixed_settings):
        """第 3 位小数非零时向上取整到 0.01（token_cost * factor = 0.01571 -> 1.58）"""
        from src.services.billing import calculate_credit_cost

        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db, \
             patch("src.services.billing.create_settings", return_value=fixed_settings):
            # 0.01571 元 * 100 = 1.571
            # token_cost = 1_000_000 * 0.01571 / 1e6 = 0.01571
            mock_tcp_db.get_by_model_name.return_value = _make_tcp(0.01571, 0.0)
            result = calculate_credit_cost(
                prompt_tokens=1_000_000,
                completion_tokens=0,
                model="test-model",
            )
            # ceil(0.01571 * 100 * 100) / 100 = ceil(157.1) / 100 = 158 / 100 = 1.58
            assert result == 1.58

    def test_integer_result_returns_float(self, fixed_settings):
        """整数结果仍以 float 返回（保持类型一致，避免 Decimal 序列化为字符串）"""
        from src.services.billing import calculate_credit_cost

        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db, \
             patch("src.services.billing.create_settings", return_value=fixed_settings):
            # 1.0 元 * 100 = 100.00
            mock_tcp_db.get_by_model_name.return_value = _make_tcp(1.0, 0.0)
            result = calculate_credit_cost(
                prompt_tokens=1_000_000,
                completion_tokens=0,
                model="test-model",
            )
            assert result == 100.0
            assert isinstance(result, float)
