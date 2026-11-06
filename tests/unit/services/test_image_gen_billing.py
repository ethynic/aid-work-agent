"""图像生成按张计费单测（image_render 计费三件套）

覆盖：
- calculate_image_credit_cost / _with_breakdown 公式（ceil(images × price_per_call × factor × 100)/100）
- 单价缺失 / 张数 ≤0 / price_per_call ≤0 降级 0.0
- price_snapshot 注入免查库
- SessionRecord.add_image_usage 累加 + save() 合入 credit_cost 与 usage_breakdown["image"]
"""
import math
from unittest.mock import patch, MagicMock

import pytest


def _make_tcp(price_per_call: float) -> dict:
    return {
        "model_name": "qwen-image-edit",
        "price_per_call": price_per_call,
    }


@pytest.fixture
def fixed_settings():
    settings = MagicMock()
    settings.billing.image_usage_factor = 100
    return settings


class TestCalculateImageCreditCost:
    def test_basic_formula(self, fixed_settings):
        """2 张 × 0.25 元/张 × 100 = 50 积分"""
        from src.services.billing import calculate_image_credit_cost_with_breakdown

        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db, \
             patch("src.services.billing.create_settings", return_value=fixed_settings):
            mock_tcp_db.get_by_model_name.return_value = _make_tcp(0.25)
            credit, bd = calculate_image_credit_cost_with_breakdown(images=2, model="qwen-image-edit")

        assert credit == 50.0
        assert bd == {"unit_price_per_call": 0.25, "usage_factor": 100}
        mock_tcp_db.get_by_model_name.assert_called_once_with("qwen-image-edit")

    def test_ceil_to_two_decimals(self, fixed_settings):
        """2 张 × 0.13 元 × 100 = 26.0；1 张 × 0.11 × 100 = 11.0（不进位场景核对）"""
        from src.services.billing import calculate_image_credit_cost_with_breakdown

        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db, \
             patch("src.services.billing.create_settings", return_value=fixed_settings):
            mock_tcp_db.get_by_model_name.return_value = _make_tcp(0.13)
            credit, _ = calculate_image_credit_cost_with_breakdown(images=3, model="m")
        # 3 × 0.13 = 0.39 → ×100 = 39.0（浮点 39.000000000001 时 ceil 仍 39）
        assert credit == 39.0

    def test_usage_factor_override(self, fixed_settings):
        """覆盖系数生效：1 张 × 0.25 × 50 = 12.5"""
        from src.services.billing import calculate_image_credit_cost_with_breakdown

        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db, \
             patch("src.services.billing.create_settings", return_value=fixed_settings) as mock_cs:
            mock_tcp_db.get_by_model_name.return_value = _make_tcp(0.25)
            credit, bd = calculate_image_credit_cost_with_breakdown(
                images=1, model="m", usage_factor_override=50)

        assert credit == 12.5
        assert bd["usage_factor"] == 50
        mock_cs.assert_not_called()

    def test_missing_price_record_returns_zero(self, fixed_settings):
        from src.services.billing import calculate_image_credit_cost

        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db, \
             patch("src.services.billing.create_settings", return_value=fixed_settings):
            mock_tcp_db.get_by_model_name.return_value = None
            assert calculate_image_credit_cost(images=2, model="unknown") == 0.0

    def test_zero_price_per_call_returns_zero(self, fixed_settings):
        from src.services.billing import calculate_image_credit_cost

        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db, \
             patch("src.services.billing.create_settings", return_value=fixed_settings):
            mock_tcp_db.get_by_model_name.return_value = _make_tcp(0)
            assert calculate_image_credit_cost(images=2, model="m") == 0.0

    def test_non_positive_images_returns_zero(self, fixed_settings):
        from src.services.billing import calculate_image_credit_cost

        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db:
            assert calculate_image_credit_cost(images=0, model="m") == 0.0
            assert calculate_image_credit_cost(images=-1, model="m") == 0.0
            mock_tcp_db.get_by_model_name.assert_not_called()

    def test_price_snapshot_bypasses_db(self, fixed_settings):
        """price_snapshot 注入时不查库（审计快照口径）"""
        from src.services.billing import calculate_image_credit_cost_with_breakdown

        with patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db, \
             patch("src.services.billing.create_settings", return_value=fixed_settings):
            credit, bd = calculate_image_credit_cost_with_breakdown(
                images=2, model="m", price_snapshot=_make_tcp(0.25))

        assert credit == 50.0
        assert bd == {"unit_price_per_call": 0.25, "usage_factor": 100}
        mock_tcp_db.get_by_model_name.assert_not_called()


class TestSessionRecordImageUsage:
    def test_add_image_usage_accumulates(self):
        from src.services.session_record import SessionRecordManager

        SessionRecordManager.end_record()
        record = SessionRecordManager.start_record(
            session_id="s_img", user_id="u1", user_message="m", tenant_id="t1"
        )
        record.add_image_usage(images=1, model="qwen-image-edit")
        record.add_image_usage(images=2, model="qwen-image-edit")

        assert record.image_calls == 3
        bd = record.usage_breakdown["image"]
        assert bd["images"] == 3
        assert bd["model"] == "qwen-image-edit"
        SessionRecordManager.end_record()

    def test_add_image_usage_ignores_non_positive(self):
        from src.services.session_record import SessionRecordManager

        SessionRecordManager.end_record()
        record = SessionRecordManager.start_record(
            session_id="s_img0", user_id="u1", user_message="m", tenant_id="t1"
        )
        record.add_image_usage(images=0)
        record.add_image_usage(images=None)

        assert record.image_calls == 0
        SessionRecordManager.end_record()

    def test_save_merges_image_credit(self):
        """save() 把 image 张数合入 credit_cost 与 usage_breakdown["image"]（含单价快照）"""
        from src.services.session_record import SessionRecordManager

        saved = []

        def _fake_create(**kwargs):
            saved.append(kwargs)
            return {"record_id": "rec_img"}

        settings = MagicMock()
        settings.billing.image_usage_factor = 100
        SessionRecordManager.end_record()
        record = SessionRecordManager.start_record(
            session_id="s_img_save", user_id="u1", user_message="出图", tenant_id="t1"
        )
        record.add_image_usage(images=2, model="qwen-image-edit")

        with patch("src.db.models.ChatRecordDB.create", side_effect=_fake_create), \
             patch("src.services.billing.TokenCostPriceDB") as mock_tcp_db, \
             patch("src.services.billing.create_settings", return_value=settings):
            mock_tcp_db.get_by_model_name.return_value = _make_tcp(0.25)
            record.save()

        kwargs = saved[-1]
        assert kwargs["credit_cost"] == 50.0
        image_bd = kwargs["usage_breakdown"]["image"]
        assert image_bd["images"] == 2
        assert image_bd["model"] == "qwen-image-edit"
        assert image_bd["credit"] == 50.0
        assert image_bd["unit_price_per_call"] == 0.25
        assert image_bd["usage_factor"] == 100
        SessionRecordManager.end_record()

    def test_save_without_image_no_image_block(self):
        """无出图时 usage_breakdown 不含 image 键，credit_cost 不受影响"""
        from src.services.session_record import SessionRecordManager

        saved = []

        def _fake_create(**kwargs):
            saved.append(kwargs)
            return {"record_id": "rec_noimg"}

        SessionRecordManager.end_record()
        SessionRecordManager.start_record(
            session_id="s_noimg", user_id="u1", user_message="纯文本", tenant_id="t1"
        ).add_llm_usage({"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})

        with patch("src.db.models.ChatRecordDB.create", side_effect=_fake_create):
            record = SessionRecordManager.get_current_record()
            record.save()

        kwargs = saved[-1]
        assert "image" not in (kwargs["usage_breakdown"] or {})
        SessionRecordManager.end_record()
