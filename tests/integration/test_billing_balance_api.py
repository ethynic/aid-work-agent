"""
租户余额/用量明细 API 集成测试（#37）

覆盖：
- /balance 返回正确余额
- /usage 按日聚合
- /recharges 只读列表
"""

import pytest
from unittest.mock import patch

pytestmark = pytest.mark.integration


@pytest.fixture
def temp_tenant_with_data():
    """创建临时租户 + 一条充值记录 + 一条 chat_records（含 credit_cost），测试后清理"""
    from src.saas.db.tenant_db import TenantDB
    from src.db.models import TenantRechargesDB, ChatRecordDB
    from src.db.database import get_db_connection
    from src.core.cache_utils import invalidate_tenant_cache
    import uuid

    tenant_code = f"T{uuid.uuid4().hex[:6].upper()}"
    tenant = TenantDB.create(
        company_name=f"测试租户-{tenant_code}",
        tenant_code=tenant_code,
        contact_name="测试联系人",
        contact_phone="13800000000",
    )
    if not tenant:
        pytest.skip("无法创建测试租户（DB 不可用）")
    tenant_id = tenant["tenant_id"]

    # 充值 1000 积分
    TenantRechargesDB.create(
        tenant_id=tenant_id,
        amount_yuan=100.0,
        credits=1000,
        rate=10,
        source="manual",
        operator_id="test_op",
        operator_name="测试员",
    )

    # 写一条 chat_records（消耗 5 积分）
    ChatRecordDB.create(
        session_id=f"test_session_{uuid.uuid4().hex[:8]}",
        tenant_id=tenant_id,
        user_id="test_user",
        user_message="测试消息",
        assistant_message="测试回复",
        prompt_tokens=100,
        completion_tokens=50,
        model="test-model",
        status="completed",
        credit_cost=5,
    )

    invalidate_tenant_cache(tenant_id)

    yield tenant_id

    # 清理
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM chat_records WHERE tenant_id = %s AND user_id = 'test_user'",
                (tenant_id,),
            )
            cursor.execute("DELETE FROM tenant_recharges WHERE tenant_id = %s", (tenant_id,))
            conn.commit()
    except Exception:
        pass
    TenantDB.delete(tenant_id)


class TestBillingBalanceAPI:
    """billing_balance API 测试"""

    def test_balance_returns_correct_value(self, temp_tenant_with_data):
        """/balance 返回的余额 = 充值 - chat_records 消耗"""
        from src.saas.api import billing_balance
        from src.core.cache_utils import invalidate_tenant_cache

        tenant_id = temp_tenant_with_data
        invalidate_tenant_cache(tenant_id)

        # mock require_admin 返回该租户
        def fake_require_admin(request):
            return {"user_id": "test_user", "role": "tenant_admin", "tenant_id": tenant_id}

        class FakeRequest:
            pass

        with patch("src.saas.api.billing_balance.require_admin", fake_require_admin), \
             patch("src.saas.api.billing_balance.settings") as mock_settings:
            mock_settings.saas.enabled = True

            import asyncio
            response = asyncio.get_event_loop().run_until_complete(
                billing_balance.get_balance(FakeRequest())
            )

        assert response["success"] is True
        balance = response["balance"]
        # 余额 = 1000 - 5 = 995
        assert balance["credit_balance"] == 995
        assert balance["daily_avg_cost_7d"] >= 0
        # 日均消耗 5/7 = 0（向下取整）
        assert balance["daily_avg_cost_7d"] == 0
        # 日均 0 且余额 > 0 时返回 -1
        assert balance["estimated_days_left"] == -1

    def test_usage_returns_aggregated_items(self, temp_tenant_with_data):
        """/usage 按日聚合返回明细"""
        from src.saas.api import billing_balance

        tenant_id = temp_tenant_with_data

        def fake_require_admin(request):
            return {"user_id": "test_user", "role": "tenant_admin", "tenant_id": tenant_id}

        class FakeRequest:
            pass

        with patch("src.saas.api.billing_balance.require_admin", fake_require_admin), \
             patch("src.saas.api.billing_balance.settings") as mock_settings:
            mock_settings.saas.enabled = True

            import asyncio
            response = asyncio.get_event_loop().run_until_complete(
                billing_balance.get_usage(
                    FakeRequest(),
                    date_from=None,
                    date_to=None,
                    session_id=None,
                    model=None,
                    page=1,
                    page_size=20,
                )
            )

        assert response["success"] is True
        items = response["items"]
        assert len(items) >= 1
        # 累计 credit_cost 应为 5
        total_cost = sum(it["credit_cost"] for it in items)
        assert total_cost == 5
        # 每条含必要字段
        for it in items:
            assert "date" in it
            assert "credit_cost" in it
            assert "session_count" in it
            assert "message_count" in it

    def test_usage_returns_cross_page_summary(self, temp_tenant_with_data):
        """/usage 返回的 summary 字段为全量汇总（跨页稳定）

        场景：构造分页（page_size=1）时，summary 仍返回全量汇总，
        而非当前页数据，避免翻页时汇总变化。
        """
        from src.saas.api import billing_balance
        from src.db.models import ChatRecordDB
        import uuid

        tenant_id = temp_tenant_with_data

        # 再补 1 条记录（共 2 条，确保跨页）
        ChatRecordDB.create(
            session_id=f"test_session_{uuid.uuid4().hex[:8]}",
            tenant_id=tenant_id,
            user_id="test_user",
            user_message="测试消息2",
            assistant_message="测试回复2",
            prompt_tokens=100,
            completion_tokens=50,
            model="test-model",
            status="completed",
            credit_cost=7,
        )

        def fake_require_admin(request):
            return {"user_id": "test_user", "role": "tenant_admin", "tenant_id": tenant_id}

        class FakeRequest:
            pass

        with patch("src.saas.api.billing_balance.require_admin", fake_require_admin), \
             patch("src.saas.api.billing_balance.settings") as mock_settings:
            mock_settings.saas.enabled = True

            import asyncio
            # page_size=1 强制分页
            response = asyncio.get_event_loop().run_until_complete(
                billing_balance.get_usage(
                    FakeRequest(),
                    date_from=None,
                    date_to=None,
                    session_id=None,
                    model=None,
                    page=1,
                    page_size=1,
                )
            )

        assert response["success"] is True
        # summary 字段必须存在
        assert "summary" in response
        summary = response["summary"]
        # 全量汇总：5 + 7 = 12（不受分页影响）
        assert summary["total_credit_cost"] == 12
        # 全量消息数：2
        assert summary["total_message_count"] == 2
        # 全量会话数：2（两条记录的 session_id 不同）
        assert summary["total_session_count"] == 2
        # 当前页 items 只有 1 条，但 summary 是全量
        assert len(response["items"]) == 1

    def test_recharges_returns_readonly_list(self, temp_tenant_with_data):
        """/recharges 返回只读列表，不包含 operator_id"""
        from src.saas.api import billing_balance

        tenant_id = temp_tenant_with_data

        def fake_require_admin(request):
            return {"user_id": "test_user", "role": "tenant_admin", "tenant_id": tenant_id}

        class FakeRequest:
            pass

        with patch("src.saas.api.billing_balance.require_admin", fake_require_admin), \
             patch("src.saas.api.billing_balance.settings") as mock_settings:
            mock_settings.saas.enabled = True

            import asyncio
            response = asyncio.get_event_loop().run_until_complete(
                billing_balance.list_my_recharges(FakeRequest(), page=1, page_size=20)
            )

        assert response["success"] is True
        items = response["items"]
        assert len(items) >= 1
        # 只读视图不应含 operator_id
        for it in items:
            assert "operator_id" not in it
            assert "payment_order_id" not in it
        # 至少一条 credits=1000
        assert any(it["credits"] == 1000 for it in items)
