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

