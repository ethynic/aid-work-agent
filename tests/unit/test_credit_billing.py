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

# sentinel：区分 TestTieredMergedCall._call 缺省（用 tiered tcp）与显式 tcp=None（无单价记录）
_MISSING = object()


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
        """model 与兜底模型均无价目行返回 0.0"""
        from src.services.billing import calculate_credit_cost

        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db, \
             patch("src.services.billing.create_settings", return_value=fixed_settings):
            mock_tcp_db.get_by_model_name.return_value = None
            result = calculate_credit_cost(prompt_tokens=1000, completion_tokens=500, model="unknown-model")
            assert result == 0.0
            # 兜底逻辑：先查模型自身价，再查兜底模型价
            assert mock_tcp_db.get_by_model_name.call_count == 2

    def test_unpriced_model_billed_at_fallback_model_prices(self, fixed_settings):
        """模型无价目行 → 按兜底模型 deepseek-flash 价格计费（2026-09-15 定版，
        修复部署主模型名不在价目表时总结/后台 LLM 计费落 0 的缺口）"""
        from src.services.billing import (
            BILLING_FALLBACK_MODEL,
            calculate_credit_cost_with_breakdown,
        )

        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db, \
             patch("src.services.billing.create_settings", return_value=fixed_settings):
            mock_tcp_db.get_by_model_name.side_effect = [None, _make_tcp(2.0, 8.0)]
            credit_cost, breakdown = calculate_credit_cost_with_breakdown(
                prompt_tokens=1000, completion_tokens=500, model="unpriced-model")

        # token_cost = (1000*2.0 + 500*8.0)/1e6 = 0.006 → ceil(0.006*100*100)/100 = 0.60
        assert credit_cost == 0.60
        assert breakdown["price_model"] == BILLING_FALLBACK_MODEL
        assert breakdown["price_fallback"] is True
        calls = mock_tcp_db.get_by_model_name.call_args_list
        assert calls[0][0] == ("unpriced-model",)
        assert calls[1][0] == (BILLING_FALLBACK_MODEL,)

    def test_priced_model_has_no_fallback_marker(self, fixed_settings):
        """模型自身有价目行：正常计价，breakdown 不带兜底标记"""
        from src.services.billing import calculate_credit_cost_with_breakdown

        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db, \
             patch("src.services.billing.create_settings", return_value=fixed_settings):
            mock_tcp_db.get_by_model_name.return_value = _make_tcp(0.8, 2.0)
            credit_cost, breakdown = calculate_credit_cost_with_breakdown(
                prompt_tokens=1000, completion_tokens=500, model="test-model")

        assert credit_cost == 0.18
        assert breakdown["price_model"] == "test-model"
        assert "price_fallback" not in breakdown

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

    def test_cache_creation_price_takes_effect(self, fixed_settings):
        """显式缓存创建按输入价 125% 计费，且从 non_cached 中剔除"""
        from src.services.billing import calculate_credit_cost_with_breakdown

        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db, \
             patch("src.services.billing.create_settings", return_value=fixed_settings):
            # input_price=0.8, cached_input_price=0.16（命中 20% 口径），output=2.0
            mock_tcp_db.get_by_model_name.return_value = _make_tcp(0.8, 2.0, cached_input_price_per_m=0.16)
            # prompt=1000, cached=400, creation=200, completion=500
            # non_cached_input = 1000 - 400 - 200 = 400
            # creation_price = 0.8 * 1.25 = 1.0
            # token_cost = (400*0.8 + 400*0.16 + 200*1.0 + 500*2.0) / 1e6
            #            = (320 + 64 + 200 + 1000) / 1e6 = 1584 / 1e6 = 0.001584
            # credit_cost = ceil(0.001584 * 100 * 100) / 100 = ceil(15.84) / 100 = 16 / 100 = 0.16
            credit_cost, breakdown = calculate_credit_cost_with_breakdown(
                prompt_tokens=1000,
                completion_tokens=500,
                model="test-model",
                cached_input_tokens=400,
                cache_creation_input_tokens=200,
            )
            assert credit_cost == 0.16
            assert breakdown["non_cached_input_tokens"] == 400
            assert breakdown["unit_prices"]["cache_creation_input_per_m"] == 1.0
            assert breakdown["credits"]["cache_creation_input"] == pytest.approx(200 * 1.0 / 1_000_000 * 100)

    def test_cache_creation_no_cached_price_fallback(self, fixed_settings):
        """无缓存单价（cached_input_price_per_m=None）时，缓存创建按 0 计费、归入普通输入"""
        from src.services.billing import calculate_credit_cost_with_breakdown

        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db, \
             patch("src.services.billing.create_settings", return_value=fixed_settings):
            mock_tcp_db.get_by_model_name.return_value = _make_tcp(0.8, 2.0, cached_input_price_per_m=None)
            # prompt=1000, creation=200, completion=0
            # 无缓存单价走原公式：non_cached=1000 全量按输入价，creation 不参与
            # token_cost = 1000*0.8/1e6 = 0.0008 -> credit = ceil(8.0)/100 = 0.08
            credit_cost, breakdown = calculate_credit_cost_with_breakdown(
                prompt_tokens=1000,
                completion_tokens=0,
                model="test-model",
                cache_creation_input_tokens=200,
            )
            assert credit_cost == 0.08
            assert breakdown["unit_prices"]["cache_creation_input_per_m"] is None
            assert breakdown["credits"]["cache_creation_input"] == 0.0

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


class TestUsageFactorOverride:
    """Phase 2.1.1: usage_factor_override 参数测试"""

    def test_override_takes_effect(self):
        """传入 usage_factor_override 时覆盖 settings.billing.usage_factor"""
        from src.services.billing import calculate_credit_cost

        settings = MagicMock()
        settings.billing.usage_factor = 100  # settings 默认 100

        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db, \
             patch("src.services.billing.create_settings", return_value=settings):
            mock_tcp_db.get_by_model_name.return_value = _make_tcp(1.0, 0.0)
            # token_cost = 1_000_000 * 1.0 / 1e6 = 1.0 元
            # 默认 usage_factor=100 -> 100.00
            # override=200 -> 200.00
            result_default = calculate_credit_cost(
                prompt_tokens=1_000_000, completion_tokens=0, model="test-model",
            )
            result_override = calculate_credit_cost(
                prompt_tokens=1_000_000, completion_tokens=0, model="test-model",
                usage_factor_override=200,
            )
            assert result_default == 100.0
            assert result_override == 200.0

    def test_override_does_not_affect_default_behavior(self, fixed_settings):
        """不传 usage_factor_override 时维持原行为（读 settings.billing.usage_factor）"""
        from src.services.billing import calculate_credit_cost

        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db, \
             patch("src.services.billing.create_settings", return_value=fixed_settings):
            mock_tcp_db.get_by_model_name.return_value = _make_tcp(1.0, 0.0)
            # token_cost = 1.0 元，usage_factor=100 -> 100.00
            result = calculate_credit_cost(
                prompt_tokens=1_000_000, completion_tokens=0, model="test-model",
            )
            assert result == 100.0


class TestCalculateVideoCreditCost:
    """Phase 2.1.2: calculate_video_credit_cost 按秒计费测试"""

    @pytest.fixture
    def video_settings(self):
        """固定 billing.video_gen_usage_factor = 33"""
        settings = MagicMock()
        settings.billing.video_gen_usage_factor = 33
        return settings

    def test_basic_calculation(self, video_settings):
        """5 秒 × 0.20 元/秒 × 33 = 33.00"""
        from src.services.billing import calculate_video_credit_cost

        with patch("src.services.billing.create_settings", return_value=video_settings):
            result = calculate_video_credit_cost(
                seconds=5,
                cost_per_second_yuan=0.20,
                provider="wanx",
                model="wan2.7-r2v",
            )
            # ceil(5 * 0.20 * 33 * 100) / 100 = ceil(3300.0) / 100 = 33.00
            assert result == 33.0

    def test_factor_3_3x(self, video_settings):
        """video_gen_usage_factor=33 即 3.3 倍加价：5s × 0.10 元/s × 33 = 16.50"""
        from src.services.billing import calculate_video_credit_cost

        with patch("src.services.billing.create_settings", return_value=video_settings):
            result = calculate_video_credit_cost(
                seconds=5,
                cost_per_second_yuan=0.10,
                provider="minimax",
                model="MiniMax-H3",
            )
            # ceil(5 * 0.10 * 33 * 100) / 100 = ceil(1650.0) / 100 = 16.50
            assert result == 16.5

    def test_zero_seconds_returns_zero(self, video_settings):
        """0 秒返回 0.0"""
        from src.services.billing import calculate_video_credit_cost

        with patch("src.services.billing.create_settings", return_value=video_settings):
            result = calculate_video_credit_cost(
                seconds=0,
                cost_per_second_yuan=0.20,
                provider="wanx",
                model="wan2.7-r2v",
            )
            assert result == 0.0

    def test_negative_seconds_returns_zero(self, video_settings):
        """负秒数防御性返回 0.0"""
        from src.services.billing import calculate_video_credit_cost

        with patch("src.services.billing.create_settings", return_value=video_settings):
            result = calculate_video_credit_cost(
                seconds=-5,
                cost_per_second_yuan=0.20,
                provider="wanx",
                model="wan2.7-r2v",
            )
            assert result == 0.0

    def test_zero_price_returns_zero(self, video_settings):
        """单价为 0 返回 0.0"""
        from src.services.billing import calculate_video_credit_cost

        with patch("src.services.billing.create_settings", return_value=video_settings):
            result = calculate_video_credit_cost(
                seconds=5,
                cost_per_second_yuan=0.0,
                provider="wanx",
                model="wan2.7-r2v",
            )
            assert result == 0.0

    def test_ceil_rounding(self, video_settings):
        """向上取整到 0.01：3s × 0.07 元/s × 33 = 6.93（精确）；3s × 0.071 × 33 = 7.029 -> 7.03"""
        from src.services.billing import calculate_video_credit_cost

        with patch("src.services.billing.create_settings", return_value=video_settings):
            result = calculate_video_credit_cost(
                seconds=3,
                cost_per_second_yuan=0.071,
                provider="wanx",
                model="wan2.7-r2v",
            )
            # 3 * 0.071 * 33 = 7.029 -> ceil(702.9) / 100 = 7.03
            assert result == 7.03

    def test_factor_fallback_when_settings_missing(self):
        """settings.billing.video_gen_usage_factor 缺失时回退到默认 33"""
        from src.services.billing import calculate_video_credit_cost

        settings = MagicMock()
        # 模拟属性缺失：getattr 返回 None
        del settings.billing.video_gen_usage_factor
        settings.billing.video_gen_usage_factor = None

        with patch("src.services.billing.create_settings", return_value=settings):
            result = calculate_video_credit_cost(
                seconds=5,
                cost_per_second_yuan=0.20,
                provider="wanx",
                model="wan2.7-r2v",
            )
            # 因 settings.billing.video_gen_usage_factor=None，getattr 返回 None，`or 33` 兜底为 33
            assert result == 33.0


class TestVideoCostPerSecondByResolution:
    """VideoChatService._get_cost_per_second 按 resolution 区分单价测试

    优先级：price_per_second_by_resolution[resolution] > price_per_second > 0
    """

    def _make_service(self):
        """构造一个 VideoChatService 实例（provider 构造失败时 _provider=None，不影响此测试）"""
        from src.video_agent.service import VideoChatService
        return VideoChatService()

    def test_by_resolution_hit(self):
        """JSONB 命中 resolution key 时用 JSONB 值（1080P=1.0）"""
        with patch("src.video_agent.service.TokenCostPriceDB") as mock_db:
            mock_db.get_by_model_name.return_value = {
                "model_name": "wan2.7-r2v",
                "price_per_second": 0.6,
                "price_per_second_by_resolution": {"720P": 0.6, "1080P": 1.0},
            }
            svc = self._make_service()
            svc._provider = MagicMock()
            svc._provider.name = "wanx"
            # _get_model_name 在 provider.name=wanx 时返回 settings.video_gen.wanx.model
            cost = svc._get_cost_per_second("wanx", "wanx", "1080P")
            assert cost == 1.0

    def test_by_resolution_null_fallback_to_price_per_second(self):
        """by_resolution 为 NULL 时回退到 price_per_second（MiniMax 暂未配 JSONB 的兼容场景）"""
        with patch("src.video_agent.service.TokenCostPriceDB") as mock_db:
            mock_db.get_by_model_name.return_value = {
                "model_name": "MiniMax-H3",
                "price_per_second": 0.5,
                "price_per_second_by_resolution": None,
            }
            svc = self._make_service()
            svc._provider = MagicMock()
            svc._provider.name = "minimax"
            cost = svc._get_cost_per_second("minimax", "minimax", "2K")
            assert cost == 0.5

    def test_by_resolution_key_missing_fallback(self):
        """JSONB 中无对应 resolution key 时回退到 price_per_second"""
        with patch("src.video_agent.service.TokenCostPriceDB") as mock_db:
            mock_db.get_by_model_name.return_value = {
                "model_name": "wan2.7-r2v",
                "price_per_second": 0.6,
                "price_per_second_by_resolution": {"720P": 0.6, "1080P": 1.0},
            }
            svc = self._make_service()
            svc._provider = MagicMock()
            svc._provider.name = "wanx"
            # 传 JSONB 中没有的 resolution（如 4K）
            cost = svc._get_cost_per_second("wanx", "wanx", "4K")
            assert cost == 0.6

    def test_both_null_returns_zero(self):
        """by_resolution 和 price_per_second 都为 NULL 返回 0"""
        with patch("src.video_agent.service.TokenCostPriceDB") as mock_db:
            mock_db.get_by_model_name.return_value = {
                "model_name": "wan2.7-r2v",
                "price_per_second": None,
                "price_per_second_by_resolution": None,
            }
            svc = self._make_service()
            svc._provider = MagicMock()
            svc._provider.name = "wanx"
            cost = svc._get_cost_per_second("wanx", "wanx", "1080P")
            assert cost == 0.0

    def test_no_price_record_returns_zero(self):
        """token_cost_prices 无匹配记录返回 0"""
        with patch("src.video_agent.service.TokenCostPriceDB") as mock_db:
            mock_db.get_by_model_name.return_value = None
            svc = self._make_service()
            svc._provider = MagicMock()
            svc._provider.name = "wanx"
            cost = svc._get_cost_per_second("wanx", "wanx", "1080P")
            assert cost == 0.0

    def test_db_exception_returns_zero(self):
        """DB 查询异常时返回 0，不抛出"""
        with patch("src.video_agent.service.TokenCostPriceDB") as mock_db:
            mock_db.get_by_model_name.side_effect = Exception("DB connection lost")
            svc = self._make_service()
            svc._provider = MagicMock()
            svc._provider.name = "wanx"
            cost = svc._get_cost_per_second("wanx", "wanx", "1080P")
            assert cost == 0.0


def _make_embedding_tcp(embedding_price_per_m: float) -> dict:
    """构造 embedding 计费的 TokenCostPriceDB 返回值"""
    return {
        "model_name": "text-embedding-v3",
        "embedding_price_per_m": embedding_price_per_m,
        "input_price_per_m": None,
        "output_price_per_m": None,
        "cached_input_price_per_m": None,
    }


def _make_asr_tcp(asr_price_per_call: float) -> dict:
    """构造 ASR 计费的 TokenCostPriceDB 返回值"""
    return {
        "model_name": "aliyun-nls-asr",
        "asr_price_per_call": asr_price_per_call,
        "input_price_per_m": None,
        "output_price_per_m": None,
        "cached_input_price_per_m": None,
    }


class TestCalculateEmbeddingCreditCost:
    """LLM 计费接入改造：calculate_embedding_credit_cost 按 token 计费测试"""

    @pytest.fixture
    def embedding_settings(self):
        """固定 billing.embedding_usage_factor = 100"""
        settings = MagicMock()
        settings.billing.embedding_usage_factor = 100
        return settings

    def test_basic_calculation(self, embedding_settings):
        """1M tokens × 0.7 元/百万 × 100 = 70.00"""
        from src.services.billing import calculate_embedding_credit_cost

        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db, \
             patch("src.services.billing.create_settings", return_value=embedding_settings):
            mock_tcp_db.get_by_model_name.return_value = _make_embedding_tcp(0.7)
            # token_cost = 1_000_000 * 0.7 / 1e6 = 0.7 元
            # credit_cost = ceil(0.7 * 100 * 100) / 100 = ceil(7000.0) / 100 = 70.00
            result = calculate_embedding_credit_cost(embedding_tokens=1_000_000)
            assert result == 70.0

    def test_default_model_is_text_embedding_v3(self, embedding_settings):
        """不传 model 时默认查 text-embedding-v3"""
        from src.services.billing import calculate_embedding_credit_cost

        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db, \
             patch("src.services.billing.create_settings", return_value=embedding_settings):
            mock_tcp_db.get_by_model_name.return_value = _make_embedding_tcp(0.7)
            calculate_embedding_credit_cost(embedding_tokens=1000)
            mock_tcp_db.get_by_model_name.assert_called_with("text-embedding-v3")

    def test_zero_tokens_returns_zero(self, embedding_settings):
        """0 token 返回 0.0"""
        from src.services.billing import calculate_embedding_credit_cost

        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db, \
             patch("src.services.billing.create_settings", return_value=embedding_settings):
            mock_tcp_db.get_by_model_name.return_value = _make_embedding_tcp(0.7)
            result = calculate_embedding_credit_cost(embedding_tokens=0)
            assert result == 0.0

    def test_negative_tokens_returns_zero(self, embedding_settings):
        """负 token 防御性返回 0.0"""
        from src.services.billing import calculate_embedding_credit_cost

        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db, \
             patch("src.services.billing.create_settings", return_value=embedding_settings):
            mock_tcp_db.get_by_model_name.return_value = _make_embedding_tcp(0.7)
            result = calculate_embedding_credit_cost(embedding_tokens=-100)
            assert result == 0.0

    def test_missing_model_returns_zero(self, embedding_settings):
        """model 为空返回 0.0"""
        from src.services.billing import calculate_embedding_credit_cost

        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db, \
             patch("src.services.billing.create_settings", return_value=embedding_settings):
            result = calculate_embedding_credit_cost(embedding_tokens=1000, model=None)
            assert result == 0.0
            mock_tcp_db.get_by_model_name.assert_not_called()

    def test_missing_price_record_returns_zero(self, embedding_settings):
        """token_cost_prices 无匹配记录返回 0.0"""
        from src.services.billing import calculate_embedding_credit_cost

        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db, \
             patch("src.services.billing.create_settings", return_value=embedding_settings):
            mock_tcp_db.get_by_model_name.return_value = None
            result = calculate_embedding_credit_cost(embedding_tokens=1000)
            assert result == 0.0

    def test_zero_price_returns_zero(self, embedding_settings):
        """embedding_price_per_m 为 0 返回 0.0"""
        from src.services.billing import calculate_embedding_credit_cost

        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db, \
             patch("src.services.billing.create_settings", return_value=embedding_settings):
            mock_tcp_db.get_by_model_name.return_value = _make_embedding_tcp(0)
            result = calculate_embedding_credit_cost(embedding_tokens=1000)
            assert result == 0.0

    def test_usage_factor_takes_effect(self):
        """embedding_usage_factor 配置生效：系数 200 是系数 100 的 2 倍"""
        from src.services.billing import calculate_embedding_credit_cost

        settings_factor_100 = MagicMock()
        settings_factor_100.billing.embedding_usage_factor = 100

        settings_factor_200 = MagicMock()
        settings_factor_200.billing.embedding_usage_factor = 200

        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db:
            mock_tcp_db.get_by_model_name.return_value = _make_embedding_tcp(0.7)
            # token_cost = 1_000_000 * 0.7 / 1e6 = 0.7 元
            with patch("src.services.billing.create_settings", return_value=settings_factor_100):
                cost_100 = calculate_embedding_credit_cost(embedding_tokens=1_000_000)
            with patch("src.services.billing.create_settings", return_value=settings_factor_200):
                cost_200 = calculate_embedding_credit_cost(embedding_tokens=1_000_000)

        # 0.7 * 100 = 70.00；0.7 * 200 = 140.00
        assert cost_100 == 70.0
        assert cost_200 == 140.0
        assert cost_200 == 2 * cost_100

    def test_ceil_rounding(self, embedding_settings):
        """向上取整到 0.01：1 token × 0.7 × 100 = 7e-5 -> 0.01"""
        from src.services.billing import calculate_embedding_credit_cost

        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db, \
             patch("src.services.billing.create_settings", return_value=embedding_settings):
            mock_tcp_db.get_by_model_name.return_value = _make_embedding_tcp(0.7)
            # token_cost = 1 * 0.7 / 1e6 = 7e-7
            # credit_cost = ceil(7e-7 * 100 * 100) / 100 = ceil(7e-3) / 100 = 1 / 100 = 0.01
            result = calculate_embedding_credit_cost(embedding_tokens=1)
            assert result == 0.01

    def test_usage_factor_override(self, embedding_settings):
        """usage_factor_override 覆盖 settings 配置"""
        from src.services.billing import calculate_embedding_credit_cost

        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db, \
             patch("src.services.billing.create_settings", return_value=embedding_settings):
            mock_tcp_db.get_by_model_name.return_value = _make_embedding_tcp(0.7)
            # token_cost = 0.7 元，override=50 -> 35.00
            result = calculate_embedding_credit_cost(
                embedding_tokens=1_000_000, usage_factor_override=50,
            )
            assert result == 35.0

    def test_factor_fallback_when_settings_missing(self):
        """settings.billing.embedding_usage_factor 缺失时回退到默认 100"""
        from src.services.billing import calculate_embedding_credit_cost

        settings = MagicMock()
        settings.billing.embedding_usage_factor = None

        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db, \
             patch("src.services.billing.create_settings", return_value=settings):
            mock_tcp_db.get_by_model_name.return_value = _make_embedding_tcp(0.7)
            result = calculate_embedding_credit_cost(embedding_tokens=1_000_000)
            # 0.7 * 100 = 70.00
            assert result == 70.0


class TestCalculateAsrCreditCost:
    """LLM 计费接入改造：calculate_asr_credit_cost 按调用次数计费测试"""

    @pytest.fixture
    def asr_settings(self):
        """固定 billing.asr_usage_factor = 100"""
        settings = MagicMock()
        settings.billing.asr_usage_factor = 100
        return settings

    def test_basic_calculation(self, asr_settings):
        """10 次 × 0.06 元/次 × 100 = 60.00"""
        from src.services.billing import calculate_asr_credit_cost

        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db, \
             patch("src.services.billing.create_settings", return_value=asr_settings):
            mock_tcp_db.get_by_model_name.return_value = _make_asr_tcp(0.06)
            # cost = 10 * 0.06 = 0.6 元
            # credit_cost = ceil(0.6 * 100 * 100) / 100 = ceil(6000.0) / 100 = 60.00
            result = calculate_asr_credit_cost(asr_calls=10)
            assert result == 60.0

    def test_default_model_is_aliyun_nls_asr(self, asr_settings):
        """不传 model 时默认查 aliyun-nls-asr"""
        from src.services.billing import calculate_asr_credit_cost

        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db, \
             patch("src.services.billing.create_settings", return_value=asr_settings):
            mock_tcp_db.get_by_model_name.return_value = _make_asr_tcp(0.06)
            calculate_asr_credit_cost(asr_calls=1)
            mock_tcp_db.get_by_model_name.assert_called_with("aliyun-nls-asr")

    def test_zero_calls_returns_zero(self, asr_settings):
        """0 次返回 0.0"""
        from src.services.billing import calculate_asr_credit_cost

        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db, \
             patch("src.services.billing.create_settings", return_value=asr_settings):
            mock_tcp_db.get_by_model_name.return_value = _make_asr_tcp(0.06)
            result = calculate_asr_credit_cost(asr_calls=0)
            assert result == 0.0

    def test_negative_calls_returns_zero(self, asr_settings):
        """负次数防御性返回 0.0"""
        from src.services.billing import calculate_asr_credit_cost

        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db, \
             patch("src.services.billing.create_settings", return_value=asr_settings):
            mock_tcp_db.get_by_model_name.return_value = _make_asr_tcp(0.06)
            result = calculate_asr_credit_cost(asr_calls=-5)
            assert result == 0.0

    def test_missing_model_returns_zero(self, asr_settings):
        """model 为空返回 0.0"""
        from src.services.billing import calculate_asr_credit_cost

        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db, \
             patch("src.services.billing.create_settings", return_value=asr_settings):
            result = calculate_asr_credit_cost(asr_calls=1, model=None)
            assert result == 0.0
            mock_tcp_db.get_by_model_name.assert_not_called()

    def test_missing_price_record_returns_zero(self, asr_settings):
        """token_cost_prices 无匹配记录返回 0.0"""
        from src.services.billing import calculate_asr_credit_cost

        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db, \
             patch("src.services.billing.create_settings", return_value=asr_settings):
            mock_tcp_db.get_by_model_name.return_value = None
            result = calculate_asr_credit_cost(asr_calls=1)
            assert result == 0.0

    def test_zero_price_returns_zero(self, asr_settings):
        """asr_price_per_call 为 0 返回 0.0"""
        from src.services.billing import calculate_asr_credit_cost

        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db, \
             patch("src.services.billing.create_settings", return_value=asr_settings):
            mock_tcp_db.get_by_model_name.return_value = _make_asr_tcp(0)
            result = calculate_asr_credit_cost(asr_calls=1)
            assert result == 0.0

    def test_usage_factor_override(self, asr_settings):
        """usage_factor_override 覆盖 settings 配置"""
        from src.services.billing import calculate_asr_credit_cost

        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db, \
             patch("src.services.billing.create_settings", return_value=asr_settings):
            mock_tcp_db.get_by_model_name.return_value = _make_asr_tcp(0.06)
            # cost = 10 * 0.06 = 0.6 元，override=50 -> 30.00
            result = calculate_asr_credit_cost(asr_calls=10, usage_factor_override=50)
            assert result == 30.0

    def test_ceil_rounding(self, asr_settings):
        """向上取整到 0.01：1 次 × 0.06 × 100 = 6.00"""
        from src.services.billing import calculate_asr_credit_cost

        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db, \
             patch("src.services.billing.create_settings", return_value=asr_settings):
            mock_tcp_db.get_by_model_name.return_value = _make_asr_tcp(0.06)
            # cost = 1 * 0.06 = 0.06 元
            # credit_cost = ceil(0.06 * 100 * 100) / 100 = ceil(600.0) / 100 = 6.00
            result = calculate_asr_credit_cost(asr_calls=1)
            assert result == 6.0

    def test_factor_fallback_when_settings_missing(self):
        """settings.billing.asr_usage_factor 缺失时回退到默认 100"""
        from src.services.billing import calculate_asr_credit_cost

        settings = MagicMock()
        settings.billing.asr_usage_factor = None

        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db, \
             patch("src.services.billing.create_settings", return_value=settings):
            mock_tcp_db.get_by_model_name.return_value = _make_asr_tcp(0.06)
            result = calculate_asr_credit_cost(asr_calls=10)
            # 10 * 0.06 * 100 = 60.00
            assert result == 60.0


# ============== qwen3.7-flash 分段计价（tiered_pricing） ==============
# 百炼官方三档：0<T≤32K=输入0.2/缓存0.02/输出0.8；32K<T≤256K=0.6/0.06/2.4；256K<T≤1M=1.2/0.12/4.8
# 缓存命中按输入价 10% 计费（cached_input_per_m=0.02/0.06/0.12）；显式缓存创建按输入价 125%（0.25/0.75/1.5）。
# 计费口径：每轮 LLM 调用（= 一次 API 请求）按该轮输入 token 数取档，逐轮累加成本；
#           合并后的单价由 {成本价合计}/{token数合计} 反向算出，保证对账自洽。


def _make_tiered_tcp() -> dict:
    """构造带 tiered_pricing 的 TokenCostPriceDB 返回值（qwen3.7-flash 三档）"""
    return {
        "model_name": "qwen3.7-flash",
        "input_price_per_m": None,
        "cached_input_price_per_m": None,
        "output_price_per_m": None,
        "tiered_pricing": [
            {"max_input": 32768,   "input_per_m": 0.2, "cached_input_per_m": 0.02, "output_per_m": 0.8},
            {"max_input": 262144,  "input_per_m": 0.6, "cached_input_per_m": 0.06, "output_per_m": 2.4},
            {"max_input": 1048576, "input_per_m": 1.2, "cached_input_per_m": 0.12, "output_per_m": 4.8},
        ],
    }


class TestTieredSingleCall:
    """calculate_credit_cost_with_breakdown 对 tiered 模型按单次输入 token 取档"""

    def _call(self, prompt_tokens, completion_tokens=0, cached_input_tokens=0,
              cache_creation_input_tokens=0, fixed_settings=None):
        from src.services.billing import calculate_credit_cost_with_breakdown
        settings = fixed_settings or MagicMock()
        settings.billing.usage_factor = 100  # MagicMock 上 getattr 会自生成 truthy mock，须显式赋值
        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db, \
             patch("src.services.billing.create_settings", return_value=settings):
            mock_tcp_db.get_by_model_name.return_value = _make_tiered_tcp()
            return calculate_credit_cost_with_breakdown(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                model="qwen3.7-flash",
                cached_input_tokens=cached_input_tokens,
                cache_creation_input_tokens=cache_creation_input_tokens,
            )

    def test_tier1_price(self):
        """输入 1000（档1）：input=0.2 -> 0.02"""
        credit_cost, breakdown = self._call(prompt_tokens=1000)
        assert credit_cost == 0.02
        assert breakdown["unit_prices"]["input_per_m"] == 0.2
        assert breakdown["tiered"] is True

    def test_tier2_price(self):
        """输入 50000（档2）：input=0.6 -> 3.00"""
        credit_cost, _ = self._call(prompt_tokens=50000)
        assert credit_cost == 3.0

    def test_tier3_price(self):
        """输入 300000（档3）：input=1.2 -> 36.00"""
        credit_cost, _ = self._call(prompt_tokens=300000)
        assert credit_cost == 36.0

    def test_tier_boundary_32768(self):
        """边界 32768（≤32K）属档1（0.2）"""
        credit_cost, breakdown = self._call(prompt_tokens=32768)
        assert breakdown["unit_prices"]["input_per_m"] == 0.2

    def test_tier_boundary_32769(self):
        """边界 32769（>32K）属档2（0.6）"""
        credit_cost, breakdown = self._call(prompt_tokens=32769)
        assert breakdown["unit_prices"]["input_per_m"] == 0.6

    def test_tier_boundary_262144(self):
        """边界 262144（≤256K）属档2（0.6）"""
        credit_cost, breakdown = self._call(prompt_tokens=262144)
        assert breakdown["unit_prices"]["input_per_m"] == 0.6

    def test_tier_boundary_262145(self):
        """边界 262145（>256K）属档3（1.2）"""
        credit_cost, breakdown = self._call(prompt_tokens=262145)
        assert breakdown["unit_prices"]["input_per_m"] == 1.2

    def test_tier_over_max_fallback_last(self):
        """输入超过最大档（>1M）兜底用最后一档（1.2）"""
        credit_cost, breakdown = self._call(prompt_tokens=2000000)
        assert breakdown["unit_prices"]["input_per_m"] == 1.2

    def test_tier_cached_price(self):
        """tiered 模型区分缓存命中：cached 部分按该档缓存单价（输入价 10%）"""
        # prompt=1000(档1), cached=400 -> non_cached=600
        # token_cost = (600*0.2 + 400*0.02 + 0*0.8)/1e6 = (120+8)/1e6 = 0.000128
        # credit_cost = ceil(0.000128*100*100)/100 = ceil(1.28)/100 = 2/100 = 0.02
        credit_cost, breakdown = self._call(prompt_tokens=1000, cached_input_tokens=400)
        assert credit_cost == 0.02
        assert breakdown["unit_prices"]["cached_input_per_m"] == 0.02

    def test_tier_cache_creation_price(self):
        """tiered 显式缓存创建：按该档输入价 125% 计费，且从 non_cached 中剔除"""
        # prompt=1000(档1), cached=400, creation=200 -> non_cached=400
        # token_cost = (400*0.2 + 400*0.02 + 200*0.25 + 0*0.8)/1e6 = (80+8+50)/1e6 = 0.000138
        # credit_cost = ceil(0.000138*100*100)/100 = ceil(1.38)/100 = 2/100 = 0.02
        credit_cost, breakdown = self._call(prompt_tokens=1000, cached_input_tokens=400,
                                            cache_creation_input_tokens=200)
        assert credit_cost == 0.02
        # 反向单价 = 创建成本/创建token = (200*0.25)/200 = 0.25（输入价 0.2 × 1.25）
        assert breakdown["unit_prices"]["cache_creation_input_per_m"] == 0.25
        assert breakdown["non_cached_input_tokens"] == 400
        assert breakdown["credits"]["cache_creation_input"] == pytest.approx(200 * 0.25 / 1_000_000 * 100)


class TestTieredMergedCall:
    """calculate_llm_credit_cost_with_breakdown 多轮逐档合并计费"""

    def _call(self, usage_calls, model="qwen3.7-flash", fixed_settings=None, tcp=_MISSING,
              usage_factor_override=None):
        from src.services.billing import calculate_llm_credit_cost_with_breakdown
        settings = fixed_settings or MagicMock()
        settings.billing.usage_factor = 100  # MagicMock 上 getattr 会自生成 truthy mock，须显式赋值
        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db, \
             patch("src.services.billing.create_settings", return_value=settings):
            # 显式传 tcp=None 表示"无单价记录"；缺省用 tiered tcp
            mock_tcp_db.get_by_model_name.return_value = (
                _make_tiered_tcp() if tcp is _MISSING else tcp
            )
            return calculate_llm_credit_cost_with_breakdown(
                usage_calls=usage_calls, model=model,
                usage_factor_override=usage_factor_override,
            )

    def test_two_calls_merged(self):
        """两轮 [10K(档1), 40K(档2)] 各自取档，反向单价 = 加权平均，总成本对账自洽"""
        credit_cost, breakdown = self._call([
            {"prompt_tokens": 10000, "completion_tokens": 1000, "cached_tokens": 0},
            {"prompt_tokens": 40000, "completion_tokens": 1000, "cached_tokens": 0},
        ])
        # sum_input = 10000*0.2 + 40000*0.6 = 2000 + 24000 = 26000
        # sum_output = 1000*0.8 + 1000*2.4 = 800 + 2400 = 3200
        # token_cost = (26000 + 3200)/1e6 = 0.0292
        # credit_cost = ceil(0.0292*100*100)/100 = ceil(292.0)/100 = 2.92
        assert credit_cost == 2.92
        # 合并反向单价：input=26000/50000=0.52；output=3200/2000=1.6；cached 无命中 -> None
        assert breakdown["unit_prices"]["input_per_m"] == 0.52
        assert breakdown["unit_prices"]["output_per_m"] == 1.6
        assert breakdown["unit_prices"]["cached_input_per_m"] is None
        assert breakdown["tiered"] is True
        # 对账：input 0.52*50000/1e6*100=2.6；output 1.6*2000/1e6*100=0.32；合计=2.92
        assert breakdown["credits"]["non_cached_input"] == 2.6
        assert breakdown["credits"]["output"] == 0.32

    def test_merged_cached_tokens(self):
        """多轮含缓存命中：cached 部分按各自档位缓存单价（输入价 10%）合并"""
        credit_cost, breakdown = self._call([
            {"prompt_tokens": 10000, "completion_tokens": 0, "cached_tokens": 3000},
            {"prompt_tokens": 40000, "completion_tokens": 0, "cached_tokens": 5000},
        ])
        # 轮1(档1)：non_cached=7000*0.2=1400；cached=3000*0.02=60
        # 轮2(档2)：non_cached=35000*0.6=21000；cached=5000*0.06=300
        # sum_input=22400；sum_cached=360；w_input=42000；w_cached=8000
        # token_cost=(22400+360)/1e6=0.02276 -> credit=ceil(227.6)/100=2.28
        assert credit_cost == 2.28
        # 合并反向单价 = 精确除法（对账自洽），用 approx 比较
        assert breakdown["unit_prices"]["input_per_m"] == pytest.approx(22400 / 42000)
        assert breakdown["unit_prices"]["cached_input_per_m"] == pytest.approx(360 / 8000)
        # 对账：input 22400/1e6*100=2.24；cached 360/1e6*100=0.036
        assert breakdown["credits"]["non_cached_input"] == 2.24
        assert round(breakdown["credits"]["cached_input"], 6) == 0.036

    def test_merged_cache_creation_tokens(self):
        """多轮含显式缓存创建：creation 部分按各自档位输入价 125% 合并，且从 non_cached 中剔除"""
        credit_cost, breakdown = self._call([
            {"prompt_tokens": 10000, "completion_tokens": 0, "cached_tokens": 0, "cache_creation_tokens": 2000},
            {"prompt_tokens": 40000, "completion_tokens": 0, "cached_tokens": 0, "cache_creation_tokens": 1000},
        ])
        # 轮1(档1)：non_cached=8000*0.2=1600；creation=2000*0.25=500
        # 轮2(档2)：non_cached=39000*0.6=23400；creation=1000*0.75=750
        # sum_input=25000；sum_creation=1250；w_input=47000；w_creation=3000
        # token_cost=(25000+1250)/1e6=0.02625 -> credit=ceil(262.5)/100=2.63
        assert credit_cost == 2.63
        # 合并反向单价：creation = 1250/3000 = 0.41666...；input = 25000/47000
        assert breakdown["unit_prices"]["cache_creation_input_per_m"] == pytest.approx(1250 / 3000)
        assert breakdown["unit_prices"]["input_per_m"] == pytest.approx(25000 / 47000)
        assert breakdown["cache_creation_input_tokens"] == 3000
        assert breakdown["non_cached_input_tokens"] == 47000
        # 对账：input 25000/1e6*100=2.5；creation 1250/1e6*100=0.125
        assert breakdown["credits"]["non_cached_input"] == 2.5
        assert round(breakdown["credits"]["cache_creation_input"], 6) == 0.125

    def test_merged_division_by_zero(self):
        """某分项 token 数为 0 时对应单价置 None，不 crash"""
        credit_cost, breakdown = self._call([
            {"prompt_tokens": 1000, "completion_tokens": 0, "cached_tokens": 0},
        ])
        assert breakdown["unit_prices"]["cached_input_per_m"] is None
        assert breakdown["unit_prices"]["output_per_m"] is None
        assert breakdown["unit_prices"]["input_per_m"] == 0.2

    def test_non_tiered_fallback(self):
        """非 tiered 模型：calculate_llm_credit_cost_with_breakdown 回退按累计 token 走原逻辑"""
        from src.services.billing import calculate_credit_cost
        tcp = _make_tcp(0.8, 2.0)  # 无 tiered_pricing
        usage_calls = [
            {"prompt_tokens": 1000, "completion_tokens": 500, "cached_tokens": 0},
            {"prompt_tokens": 1000, "completion_tokens": 500, "cached_tokens": 0},
        ]
        credit_cost, breakdown = self._call(usage_calls, model="test-model", tcp=tcp)
        # 累计 prompt=2000, completion=1000
        # token_cost = (2000*0.8 + 1000*2.0)/1e6 = 0.0036 -> credit = ceil(36.0)/100 = 0.36
        assert credit_cost == 0.36
        assert breakdown.get("tiered") is None

    def test_empty_calls_returns_zero(self):
        """usage_calls 为空返回 0.0"""
        credit_cost, _ = self._call([], model="qwen3.7-flash")
        assert credit_cost == 0.0

    def test_missing_model_returns_zero(self):
        """model 为空返回 0.0"""
        credit_cost, _ = self._call([{"prompt_tokens": 1000, "completion_tokens": 0, "cached_tokens": 0}], model=None)
        assert credit_cost == 0.0

    def test_missing_price_record_returns_zero(self):
        """tcp 无匹配记录返回 0.0"""
        credit_cost, _ = self._call([{"prompt_tokens": 1000, "completion_tokens": 0, "cached_tokens": 0}],
                                    model="unknown", tcp=None)
        assert credit_cost == 0.0

    def test_usage_factor_override(self):
        """usage_factor_override 覆盖 settings 配置（传 200 时积分翻倍）"""
        settings = MagicMock()
        settings.billing.usage_factor = 100
        credit_cost, breakdown = self._call(
            [{"prompt_tokens": 10000, "completion_tokens": 0, "cached_tokens": 0}],
            fixed_settings=settings,
            usage_factor_override=200,
        )
        # 单轮 10K(档1)：token_cost = 10000*0.2/1e6 = 0.002
        # override=200：credit = ceil(0.002 * 200 * 100)/100 = ceil(40)/100 = 0.4
        # （若 override 未生效，会落到 settings 的 100，credit = 0.2）
        assert credit_cost == 0.4
        assert breakdown["usage_factor"] == 200

