"""
租户充值管理 API 集成测试（#37）

覆盖：
- ORM 层：TenantRechargesDB.create / delete / list / stats
  - create 后 tenants.credit_balance += credits（同事务原子）
  - delete 后 tenants.credit_balance -= credits（同事务原子）
- API 层：billing_recharges 路由权限校验
  - 非 platform_admin 被拒
  - 缺失字段返回 400
"""

import pytest
from unittest.mock import patch

pytestmark = pytest.mark.integration


@pytest.fixture
def temp_tenant():
    """创建临时租户用于测试，测试后清理"""
    from src.saas.db.tenant_db import TenantDB
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
    yield tenant
    # 清理：删除测试产生的充值记录 + 删除租户
    from src.db.database import get_db_connection
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM tenant_recharges WHERE tenant_id = %s", (tenant_id,))
            conn.commit()
    except Exception:
        pass
    TenantDB.delete(tenant_id)


class TestTenantRechargesORM:
    """ORM 层：TenantRechargesDB 增删改查 + 余额原子变更"""

    def test_create_increases_balance(self, temp_tenant):
        """创建充值后，租户余额 += credits"""
        from src.db.models import TenantRechargesDB
        from src.saas.db.tenant_db import TenantDB

        tenant_id = temp_tenant["tenant_id"]
        # 初始余额可能是 0 或已有值
        before = TenantDB.get_by_id(tenant_id)
        balance_before = int(before.get("credit_balance") or 0)

        record = TenantRechargesDB.create(
            tenant_id=tenant_id,
            amount_yuan=100.0,
            credits=1000,
            rate=10,
            source="manual",
            operator_id="test_operator",
            operator_name="测试操作员",
            remark="测试充值",
        )
        assert record is not None
        assert record["credits"] == 1000
        assert record["tenant_id"] == tenant_id

        # 失效缓存后查余额
        from src.core.cache_utils import invalidate_tenant_cache
        invalidate_tenant_cache(tenant_id)
        after = TenantDB.get_by_id(tenant_id)
        balance_after = int(after.get("credit_balance") or 0)
        assert balance_after == balance_before + 1000

    def test_delete_decreases_balance(self, temp_tenant):
        """删除充值后，租户余额 -= credits（回扣）"""
        from src.db.models import TenantRechargesDB
        from src.saas.db.tenant_db import TenantDB
        from src.core.cache_utils import invalidate_tenant_cache

        tenant_id = temp_tenant["tenant_id"]

        # 先创建一条充值
        record = TenantRechargesDB.create(
            tenant_id=tenant_id,
            amount_yuan=200.0,
            credits=2000,
            rate=10,
            source="manual",
            operator_id="test_operator",
        )
        assert record is not None
        recharge_id = record["id"]

        invalidate_tenant_cache(tenant_id)
        before = TenantDB.get_by_id(tenant_id)
        balance_before = int(before.get("credit_balance") or 0)

        # 删除
        deleted = TenantRechargesDB.delete(recharge_id)
        assert deleted is not None
        assert deleted["id"] == recharge_id

        invalidate_tenant_cache(tenant_id)
        after = TenantDB.get_by_id(tenant_id)
        balance_after = int(after.get("credit_balance") or 0)
        assert balance_after == balance_before - 2000

    def test_list_with_tenant_filter(self, temp_tenant):
        """list 按 tenant_id 筛选"""
        from src.db.models import TenantRechargesDB

        tenant_id = temp_tenant["tenant_id"]
        TenantRechargesDB.create(
            tenant_id=tenant_id, amount_yuan=50.0, credits=500, rate=10, source="manual"
        )
        result = TenantRechargesDB.list(tenant_id=tenant_id, page=1, page_size=10)
        assert "items" in result
        assert "total" in result
        assert all(item["tenant_id"] == tenant_id for item in result["items"])
        assert len(result["items"]) >= 1

    def test_stats_returns_summary(self, temp_tenant):
        """stats 返回汇总信息"""
        from src.db.models import TenantRechargesDB

        tenant_id = temp_tenant["tenant_id"]
        TenantRechargesDB.create(
            tenant_id=tenant_id, amount_yuan=80.0, credits=800, rate=10, source="manual"
        )
        stats = TenantRechargesDB.stats(tenant_id=tenant_id)
        assert "total_amount_yuan" in stats
        assert "total_credits" in stats
        assert "total_count" in stats
        assert "recent_7d_trend" in stats
        assert stats["total_count"] >= 1
        assert stats["total_credits"] >= 800


class TestBillingRechargesAPI:
    """billing_recharges API 权限与请求校验"""

    def test_non_platform_admin_rejected(self):
        """非 platform_admin 角色访问充值列表被拒"""
        from src.saas.api import billing_recharges
        from fastapi import HTTPException

        # mock require_admin 返回 tenant_admin
        def fake_require_admin(request):
            return {"user_id": "u1", "role": "tenant_admin", "tenant_id": "t1"}

        with patch("src.saas.api.billing_recharges.require_admin", fake_require_admin), \
             patch("src.saas.api.billing_recharges.settings") as mock_settings:
            mock_settings.saas.enabled = True

            class FakeRequest:
                pass

            with pytest.raises(HTTPException) as exc_info:
                import asyncio
                asyncio.get_event_loop().run_until_complete(
                    billing_recharges.list_recharges(FakeRequest())
                )
            assert exc_info.value.status_code == 403

    def test_create_missing_tenant_id_returns_400(self):
        """缺失 tenant_id 时 Pydantic 校验返回 422"""
        from src.saas.api.billing_recharges import RechargeCreateRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            RechargeCreateRequest(amount_yuan=100.0)

    def test_create_invalid_amount_returns_422(self):
        """amount_yuan <= 0 时校验失败"""
        from src.saas.api.billing_recharges import RechargeCreateRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            RechargeCreateRequest(tenant_id="t1", amount_yuan=0)
        with pytest.raises(ValidationError):
            RechargeCreateRequest(tenant_id="t1", amount_yuan=-10)
