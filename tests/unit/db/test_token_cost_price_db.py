"""TokenCostPriceDB 单元测试

覆盖 token_cost_prices 表访问类：
- get_by_model_name：返回行带 is_multimodal 多模态标识
- list_multimodal_models：按 is_multimodal=TRUE 查询多模态模型清单（供图片路由用）
"""
import pytest
from unittest.mock import MagicMock, patch

pytestmark = [pytest.mark.unit, pytest.mark.db]

PRICE_ROW = {
    "model_name": "GLM-5.3-Flash",
    "input_price_per_m": 0.8,
    "cached_input_price_per_m": 0.8,
    "output_price_per_m": 2.8,
    "price_per_second": None,
    "price_per_second_by_resolution": None,
    "embedding_price_per_m": None,
    "asr_price_per_call": None,
    "tiered_pricing": None,
    "is_multimodal": True,
}


def _mock_db(fetchone=None, fetchall_return=None):
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = fetchone
    mock_cursor.fetchall.return_value = fetchall_return or []
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    return mock_cursor, mock_conn


class TestGetByModelName:
    def test_returns_is_multimodal(self):
        from src.db.models import TokenCostPriceDB

        mock_cursor, mock_conn = _mock_db(fetchone=dict(PRICE_ROW))
        with patch("src.db.models.get_db_connection") as mock_get_db:
            mock_get_db.return_value.__enter__.return_value = mock_conn
            row = TokenCostPriceDB.get_by_model_name("GLM-5.3-Flash")

        assert row is not None
        assert row["is_multimodal"] is True
        assert row["input_price_per_m"] == 0.8
        assert row["output_price_per_m"] == 2.8
        # SELECT 必须包含 is_multimodal 列
        sql = mock_cursor.execute.call_args[0][0]
        assert "is_multimodal" in sql

    def test_text_model_is_multimodal_false(self):
        from src.db.models import TokenCostPriceDB

        text_row = dict(PRICE_ROW, model_name="qwen-plus", is_multimodal=False)
        _, mock_conn = _mock_db(fetchone=text_row)
        with patch("src.db.models.get_db_connection") as mock_get_db:
            mock_get_db.return_value.__enter__.return_value = mock_conn
            row = TokenCostPriceDB.get_by_model_name("qwen-plus")

        assert row["is_multimodal"] is False

    def test_case_insensitive_lookup(self):
        """模型名查询不区分大小写（SQL 用 LOWER 比对）"""
        from src.db.models import TokenCostPriceDB

        mock_cursor, mock_conn = _mock_db(fetchone=dict(PRICE_ROW))
        with patch("src.db.models.get_db_connection") as mock_get_db:
            mock_get_db.return_value.__enter__.return_value = mock_conn
            row = TokenCostPriceDB.get_by_model_name("glm-5.3-flash")

        assert row is not None
        sql = mock_cursor.execute.call_args[0][0]
        assert "LOWER(model_name) = LOWER" in sql

    def test_empty_model_name_returns_none(self):
        from src.db.models import TokenCostPriceDB

        assert TokenCostPriceDB.get_by_model_name("") is None
        assert TokenCostPriceDB.get_by_model_name(None) is None

    def test_not_found_returns_none(self):
        from src.db.models import TokenCostPriceDB

        _, mock_conn = _mock_db(fetchone=None)
        with patch("src.db.models.get_db_connection") as mock_get_db:
            mock_get_db.return_value.__enter__.return_value = mock_conn
            assert TokenCostPriceDB.get_by_model_name("nonexistent-model") is None


class TestListMultimodalModels:
    def test_returns_only_multimodal_rows(self):
        from src.db.models import TokenCostPriceDB

        rows = [
            {"model_name": "GLM-5.3-Flash", "input_price_per_m": 0.8,
             "cached_input_price_per_m": 0.8, "output_price_per_m": 2.8,
             "is_multimodal": True},
            {"model_name": "kimi-k3", "input_price_per_m": 20.0,
             "cached_input_price_per_m": 20.0, "output_price_per_m": 100.0,
             "is_multimodal": True},
        ]
        mock_cursor, mock_conn = _mock_db(fetchall_return=rows)
        with patch("src.db.models.get_db_connection") as mock_get_db:
            mock_get_db.return_value.__enter__.return_value = mock_conn
            result = TokenCostPriceDB.list_multimodal_models()

        assert [r["model_name"] for r in result] == ["GLM-5.3-Flash", "kimi-k3"]
        assert all(r["is_multimodal"] for r in result)
        # 查询必须按 is_multimodal=TRUE 过滤
        sql = mock_cursor.execute.call_args[0][0]
        assert "is_multimodal = TRUE" in sql

    def test_empty_when_no_multimodal_model(self):
        from src.db.models import TokenCostPriceDB

        _, mock_conn = _mock_db(fetchall_return=[])
        with patch("src.db.models.get_db_connection") as mock_get_db:
            mock_get_db.return_value.__enter__.return_value = mock_conn
            assert TokenCostPriceDB.list_multimodal_models() == []
