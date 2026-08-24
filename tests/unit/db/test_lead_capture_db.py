"""客户留资数据访问层单元测试

覆盖 LeadCaptureDB：
- create：手机号加密落库 + 返回解密手机号
- get_by_id：按 lead_id 查询（可选租户校验）
- list_by_tenant：日期段/客服账号/阶段/归属筛选 + 分页 + created_at DESC
- update_stage：合法阶段流转 / 非法阶段拒绝
- find_by_customer：客户级防重复反查
- stats：总留资 + 按留资方式分组 + 按客服账号分组（含 ratio）
"""
import pytest
from unittest.mock import MagicMock, patch

pytestmark = [pytest.mark.unit, pytest.mark.db]

_NOT_GIVEN = object()

LEAD_ROW = {
    "id": 1,
    "lead_id": "lead_lc_abcdef123456",
    "tenant_id": "tenant_001",
    "user_id": "tenant_user_1",
    "customer_user_id": "wx_ext_user_1",
    "channel_chat_id": "kfAAA",
    "kf_account_name": "售前客服",
    "contact_method": "phone",
    "phone": "ENC:13800138000",
    "contact_name": "张三",
    "demand_summary": "咨询企业版套餐价格",
    "source": "lead_capture",
    "stage": "new",
    "assigned_to": "emp_001",
    "assignee_name": "李老师",
    "transferred_to": None,
    "session_id": "sess_1",
    "created_at": "2026-08-21 10:00:00",
    "updated_at": "2026-08-21 10:00:00",
}


@pytest.fixture
def mock_encryption():
    """mock encryption_manager：encrypt 加 ENC: 前缀，decrypt 去除前缀"""
    with patch("src.saas.db.lead_capture_db.encryption_manager") as mock_enc:
        mock_enc.encrypt.side_effect = lambda p: f"ENC:{p}"
        mock_enc.decrypt.side_effect = lambda p: (
            p[4:] if isinstance(p, str) and p.startswith("ENC:") else p
        )
        yield mock_enc


def _mock_db(fetchone=_NOT_GIVEN, fetchall_return=_NOT_GIVEN, fetchall_sequence=None):
    mock_cursor = MagicMock()
    if fetchone is not _NOT_GIVEN:
        mock_cursor.fetchone.return_value = fetchone
    if fetchall_sequence is not None:
        mock_cursor.fetchall.side_effect = fetchall_sequence
    elif fetchall_return is not _NOT_GIVEN:
        mock_cursor.fetchall.return_value = fetchall_return
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    return mock_cursor, mock_conn


class TestCreate:
    def test_create_encrypts_phone_and_returns_decrypted(self, mock_encryption):
        from src.saas.db.lead_capture_db import LeadCaptureDB

        mock_cursor, mock_conn = _mock_db(fetchone=dict(LEAD_ROW))
        with patch("src.saas.db.lead_capture_db.get_db_connection") as mock_get_db:
            mock_get_db.return_value.__enter__.return_value = mock_conn
            result = LeadCaptureDB.create(
                tenant_id="tenant_001",
                lead_id="lead_lc_abcdef123456",
                user_id="tenant_user_1",
                customer_user_id="wx_ext_user_1",
                channel_chat_id="kfAAA",
                kf_account_name="售前客服",
                contact_method="phone",
                phone="13800138000",
                contact_name="张三",
                demand_summary="咨询企业版套餐价格",
                assigned_to="emp_001",
                assignee_name="李老师",
                session_id="sess_1",
            )

        # 落库参数中手机号为密文
        insert_args = mock_cursor.execute.call_args[0][1]
        assert "ENC:13800138000" in insert_args
        assert "13800138000" not in insert_args
        # 返回记录中手机号已解密
        assert result["phone"] == "13800138000"
        mock_conn.commit.assert_called()

    def test_create_phone_none_not_encrypted(self, mock_encryption):
        """contact_method 非 phone 或 phone 为空时，不加密直接落 None"""
        from src.saas.db.lead_capture_db import LeadCaptureDB

        mock_cursor, mock_conn = _mock_db(fetchone=None)
        with patch("src.saas.db.lead_capture_db.get_db_connection") as mock_get_db:
            mock_get_db.return_value.__enter__.return_value = mock_conn
            LeadCaptureDB.create(
                tenant_id="tenant_001",
                lead_id="lead_lc_qr123456",
                contact_method="qr",
            )

        insert_args = mock_cursor.execute.call_args[0][1]
        assert insert_args[7] is None  # phone 列
        mock_encryption.encrypt.assert_not_called()

    def test_create_db_exception_returns_none(self, mock_encryption):
        """数据库异常时回滚并返回 None"""
        from src.saas.db.lead_capture_db import LeadCaptureDB

        mock_cursor, mock_conn = _mock_db()
        mock_cursor.execute.side_effect = Exception("db down")
        with patch("src.saas.db.lead_capture_db.get_db_connection") as mock_get_db:
            mock_get_db.return_value.__enter__.return_value = mock_conn
            result = LeadCaptureDB.create(
                tenant_id="tenant_001",
                lead_id="lead_lc_x",
                contact_method="phone",
                phone="13800138000",
            )

        assert result is None
        mock_conn.rollback.assert_called()


class TestGetById:
    def test_get_by_id_with_tenant_filter(self, mock_encryption):
        from src.saas.db.lead_capture_db import LeadCaptureDB

        mock_cursor, mock_conn = _mock_db(fetchone=dict(LEAD_ROW))
        with patch("src.saas.db.lead_capture_db.get_db_connection") as mock_get_db:
            mock_get_db.return_value.__enter__.return_value = mock_conn
            result = LeadCaptureDB.get_by_id("lead_lc_abcdef123456", "tenant_001")

        sql, params = mock_cursor.execute.call_args[0]
        assert "tenant_id = %s" in sql
        assert params == ("lead_lc_abcdef123456", "tenant_001")
        assert result["phone"] == "13800138000"

    def test_get_by_id_without_tenant(self, mock_encryption):
        from src.saas.db.lead_capture_db import LeadCaptureDB

        mock_cursor, mock_conn = _mock_db(fetchone=dict(LEAD_ROW))
        with patch("src.saas.db.lead_capture_db.get_db_connection") as mock_get_db:
            mock_get_db.return_value.__enter__.return_value = mock_conn
            result = LeadCaptureDB.get_by_id("lead_lc_abcdef123456")

        sql, params = mock_cursor.execute.call_args[0]
        assert "tenant_id" not in sql
        assert params == ("lead_lc_abcdef123456",)
        assert result is not None

    def test_get_by_id_not_found_returns_none(self, mock_encryption):
        from src.saas.db.lead_capture_db import LeadCaptureDB

        mock_cursor, mock_conn = _mock_db(fetchone=None)
        with patch("src.saas.db.lead_capture_db.get_db_connection") as mock_get_db:
            mock_get_db.return_value.__enter__.return_value = mock_conn
            result = LeadCaptureDB.get_by_id("lead_lc_none")

        assert result is None


class TestListByTenant:
    def _run(self, **kwargs):
        from src.saas.db.lead_capture_db import LeadCaptureDB

        mock_cursor, mock_conn = _mock_db(
            fetchone={"cnt": 5},
            fetchall_return=[dict(LEAD_ROW)] * 5,
        )
        with patch("src.saas.db.lead_capture_db.get_db_connection") as mock_get_db:
            mock_get_db.return_value.__enter__.return_value = mock_conn
            result = LeadCaptureDB.list_by_tenant("tenant_001", **kwargs)
            return result, mock_cursor.execute.call_args_list

    def test_default_order_and_pagination(self, mock_encryption):
        result, calls = self._run(page=1, page_size=20)
        count_sql = calls[0][0][0]
        list_sql = calls[1][0][0]
        assert "tenant_id = %s" in count_sql
        assert "ORDER BY created_at DESC" in list_sql
        assert "LIMIT %s OFFSET %s" in list_sql
        assert calls[1][0][1] == ["tenant_001", 20, 0]
        assert result["total"] == 5
        assert len(result["leads"]) == 5
        assert result["leads"][0]["phone"] == "13800138000"

    def test_filters_applied(self, mock_encryption):
        _, calls = self._run(
            page=1,
            page_size=20,
            start_date="2026-08-01",
            end_date="2026-08-21",
            channel_chat_id="kfAAA",
            stage="new",
            assigned_to="emp_001",
        )
        list_sql = calls[1][0][0]
        list_params = calls[1][0][1]
        assert "created_at >= %s" in list_sql
        # end_date 含当日：< 次日零点 语义
        assert "::date + INTERVAL '1 day'" in list_sql
        assert "channel_chat_id = %s" in list_sql
        assert "stage = %s" in list_sql
        assert "assigned_to = %s" in list_sql
        # 参数顺序：tenant + 4 个过滤条件 + 分页
        assert list_params[:-2] == [
            "tenant_001",
            "2026-08-01",
            "2026-08-21",
            "kfAAA",
            "new",
            "emp_001",
        ]
        assert list_params[-2:] == [20, 0]


class TestUpdateStage:
    def test_valid_stage_updates(self, mock_encryption):
        from src.saas.db.lead_capture_db import LeadCaptureDB

        mock_cursor, mock_conn = _mock_db()
        mock_cursor.rowcount = 1
        with patch("src.saas.db.lead_capture_db.get_db_connection") as mock_get_db:
            mock_get_db.return_value.__enter__.return_value = mock_conn
            result = LeadCaptureDB.update_stage(
                "lead_lc_abcdef123456", "converted", "tenant_001"
            )

        assert result is True
        sql, params = mock_cursor.execute.call_args[0]
        assert "stage = %s" in sql
        assert params == ("converted", "lead_lc_abcdef123456", "tenant_001")

    def test_invalid_stage_rejected(self, mock_encryption):
        from src.saas.db.lead_capture_db import LeadCaptureDB

        with patch("src.saas.db.lead_capture_db.get_db_connection") as mock_get_db:
            result = LeadCaptureDB.update_stage(
                "lead_lc_abcdef123456", "bogus", "tenant_001"
            )

        assert result is False
        mock_get_db.assert_not_called()


class TestFindByCustomer:
    def test_find_by_customer_queries_latest(self, mock_encryption):
        from src.saas.db.lead_capture_db import LeadCaptureDB

        mock_cursor, mock_conn = _mock_db(fetchone=dict(LEAD_ROW))
        with patch("src.saas.db.lead_capture_db.get_db_connection") as mock_get_db:
            mock_get_db.return_value.__enter__.return_value = mock_conn
            result = LeadCaptureDB.find_by_customer("wx_ext_user_1", "tenant_001")

        sql, params = mock_cursor.execute.call_args[0]
        assert "customer_user_id = %s" in sql
        assert "tenant_id = %s" in sql
        assert "ORDER BY created_at DESC" in sql
        assert "LIMIT 1" in sql
        assert params == ("wx_ext_user_1", "tenant_001")
        assert result["phone"] == "13800138000"

    def test_find_by_customer_empty_returns_none(self, mock_encryption):
        from src.saas.db.lead_capture_db import LeadCaptureDB

        with patch("src.saas.db.lead_capture_db.get_db_connection") as mock_get_db:
            result = LeadCaptureDB.find_by_customer("", "tenant_001")

        assert result is None
        mock_get_db.assert_not_called()


class TestStats:
    def _run(self, **kwargs):
        from src.saas.db.lead_capture_db import LeadCaptureDB

        total_row = {"cnt": 10}
        by_method_rows = [
            {"contact_method": "phone", "count": 7},
            {"contact_method": "qr", "count": 3},
        ]
        by_kf_rows = [
            {"channel_chat_id": "kfAAA", "kf_account_name": "售前客服", "count": 10},
        ]
        mock_cursor, mock_conn = _mock_db(
            fetchone=total_row,
            fetchall_sequence=[by_method_rows, by_kf_rows],
        )
        with patch("src.saas.db.lead_capture_db.get_db_connection") as mock_get_db:
            mock_get_db.return_value.__enter__.return_value = mock_conn
            result = LeadCaptureDB.stats("tenant_001", **kwargs)
            return result, mock_cursor.execute.call_args_list

    def test_stats_with_date_range(self, mock_encryption):
        result, calls = self._run(
            start_date="2026-08-01",
            end_date="2026-08-21",
        )
        # 三张查询（总数 / 按方式 / 按客服账号）都带 tenant + 日期条件
        for sql, params in (c[0] for c in calls):
            assert "tenant_id = %s" in sql
            if "GROUP BY" in sql or "COUNT(*)" in sql:
                assert "created_at >= %s" in sql
                assert "INTERVAL '1 day'" in sql
        assert result["total_leads"] == 10
        # 按留资方式分组 + ratio
        by_method = result["by_contact_method"]
        assert by_method[0] == {"contact_method": "phone", "count": 7, "ratio": 70.0}
        assert by_method[1] == {"contact_method": "qr", "count": 3, "ratio": 30.0}
        # 按客服账号分组 + ratio
        by_kf = result["by_kf_account"]
        assert by_kf[0]["channel_chat_id"] == "kfAAA"
        assert by_kf[0]["kf_account_name"] == "售前客服"
        assert by_kf[0]["count"] == 10
        assert by_kf[0]["ratio"] == 100.0

    def test_stats_assigned_to_filter(self, mock_encryption):
        _, calls = self._run(assigned_to="emp_001")
        for sql, params in (c[0] for c in calls):
            assert "assigned_to = %s" in sql
            assert "emp_001" in params

    def test_stats_zero_total_ratio_zero(self, mock_encryption):
        from src.saas.db.lead_capture_db import LeadCaptureDB

        mock_cursor, mock_conn = _mock_db(
            fetchone={"cnt": 0},
            fetchall_sequence=[[], []],
        )
        with patch("src.saas.db.lead_capture_db.get_db_connection") as mock_get_db:
            mock_get_db.return_value.__enter__.return_value = mock_conn
            result = LeadCaptureDB.stats("tenant_001")

        assert result["total_leads"] == 0
        assert result["by_contact_method"] == []
        assert result["by_kf_account"] == []
