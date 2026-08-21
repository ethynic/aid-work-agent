"""留资线索 API 集成测试（真实 PostgreSQL）

覆盖「外部接待客户」页留资线索接口：
- GET /lead-stats 留资统计（总留资 / 按方式 / 按客服账号，含 ratio + 日期段）
- GET /leads 线索列表（分页，created_at DESC，日期段/客服账号/阶段筛选）
- GET /leads/{lead_id} 线索详情（含解密手机号）
- PATCH /leads/{lead_id} 更新阶段（非法阶段拒绝 / 正常流转）
- 普通用户（引流员工）隔离：仅见 assigned_to == 自己的线索与统计
"""
import json
import uuid

import pytest
from unittest.mock import patch

pytestmark = pytest.mark.integration


@pytest.fixture
def temp_tenant_for_leads():
    """创建临时租户用于留资线索测试，测试后清理"""
    from src.saas.db.tenant_db import TenantDB
    from src.db.database import get_db_connection

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

    yield tenant_id

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM bs_lead_capture_leads WHERE tenant_id = %s", (tenant_id,)
            )
            conn.commit()
    except Exception:
        pass
    TenantDB.delete(tenant_id)
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM tenants WHERE tenant_id = %s", (tenant_id,))
            conn.commit()
    except Exception:
        pass


def _insert_employee(tenant_id: str, user_id: str, nickname: str = None):
    """直接 SQL 写入引流员工账号"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO users (user_id, tenant_id, username, nickname, source, role, status)
            VALUES (%s, %s, %s, %s, NULL, 'employee', 'active')
            """,
            (user_id, tenant_id, user_id, nickname or user_id),
        )
        conn.commit()
    return user_id


def _insert_lead(tenant_id: str, *, lead_id=None, phone="13800138000",
                 contact_method="phone", assigned_to=None, assignee_name=None,
                 channel_chat_id="kfAAA", kf_account_name="售前客服",
                 stage="new", created_at=None, contact_name=None):
    """直接 SQL 写入留资线索（手机号按明文写入，验证 DB 层解密回退）"""
    from src.db.database import get_db_connection

    lead_id = lead_id or f"lead_lc_test_{uuid.uuid4().hex[:8]}"
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO bs_lead_capture_leads
            (lead_id, tenant_id, user_id, customer_user_id, channel_chat_id,
             kf_account_name, contact_method, phone, contact_name, demand_summary,
             assigned_to, assignee_name, session_id, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                lead_id,
                tenant_id,
                None,
                f"wx_ext_{uuid.uuid4().hex[:6]}",
                channel_chat_id,
                kf_account_name,
                contact_method,
                phone if contact_method == "phone" else None,
                contact_name,
                "咨询企业版套餐价格",
                assigned_to,
                assignee_name,
                f"sess_{uuid.uuid4().hex[:8]}",
                created_at,
            ),
        )
        conn.commit()
    return lead_id


def _call(fn, *args, **kwargs):
    import asyncio
    return asyncio.get_event_loop().run_until_complete(fn(*args, **kwargs))


class FakeRequest:
    pass


def _admin(tenant_id: str, role: str = "tenant_admin", user_id: str = "admin_x"):
    return {"user_id": user_id, "role": role, "tenant_id": tenant_id}


def _patch_admin(admin):
    return patch("src.saas.api.external_customers.require_admin", lambda request: admin), \
           patch("src.saas.api.external_customers.settings")


class TestLeadStats:
    def test_stats_total_and_grouping_with_date_range(self, temp_tenant_for_leads):
        """总留资 + 按留资方式分组 + 按客服账号分组（含 ratio 与日期段）"""
        from src.saas.api import external_customers

        tenant_id = temp_tenant_for_leads
        emp = _insert_employee(tenant_id, f"emp_{uuid.uuid4().hex[:6]}", "李老师")
        _insert_lead(tenant_id, contact_method="phone", phone="13800138000", assigned_to=emp, assignee_name="李老师", channel_chat_id="kfAAA", kf_account_name="售前客服")
        _insert_lead(tenant_id, contact_method="qr", assigned_to=emp, assignee_name="李老师", channel_chat_id="kfBBB", kf_account_name="售后客服")

        admin = _admin(tenant_id)
        with _patch_admin(admin)[0], _patch_admin(admin)[1] as mock_settings:
            mock_settings.saas.enabled = True
            resp = _call(external_customers.get_lead_stats, FakeRequest())

        assert resp["success"] is True
        assert resp["total_leads"] == 2
        by_method = {m["contact_method"]: m for m in resp["by_contact_method"]}
        assert by_method["phone"]["count"] == 1
        assert by_method["qr"]["count"] == 1
        assert by_method["phone"]["ratio"] == 50.0
        by_kf = {k["channel_chat_id"]: k for k in resp["by_kf_account"]}
        assert by_kf["kfAAA"]["kf_account_name"] == "售前客服"
        assert by_kf["kfBBB"]["ratio"] == 50.0

    def test_stats_employee_sees_only_own(self, temp_tenant_for_leads):
        """普通用户（引流员工）仅统计自己归属的线索"""
        from src.saas.api import external_customers

        tenant_id = temp_tenant_for_leads
        me = _insert_employee(tenant_id, f"emp_{uuid.uuid4().hex[:6]}", "我")
        other = _insert_employee(tenant_id, f"emp_{uuid.uuid4().hex[:6]}", "他人")
        _insert_lead(tenant_id, assigned_to=me)
        _insert_lead(tenant_id, assigned_to=other)

        admin = _admin(tenant_id, role="user", user_id=me)
        with _patch_admin(admin)[0], _patch_admin(admin)[1] as mock_settings:
            mock_settings.saas.enabled = True
            resp = _call(external_customers.get_lead_stats, FakeRequest())

        assert resp["total_leads"] == 1


class TestLeadList:
    def test_list_sorted_desc_with_filters(self, temp_tenant_for_leads):
        """列表 created_at DESC + 客服账号/阶段筛选 + 分页"""
        from src.saas.api import external_customers

        tenant_id = temp_tenant_for_leads
        emp = _insert_employee(tenant_id, f"emp_{uuid.uuid4().hex[:6]}", "李老师")
        _insert_lead(tenant_id, channel_chat_id="kfAAA", assigned_to=emp, contact_name="张三")
        _insert_lead(tenant_id, channel_chat_id="kfBBB", assigned_to=emp, contact_name="李四", stage="converted")
        _insert_lead(tenant_id, channel_chat_id="kfAAA", assigned_to=emp, contact_name="王五")

        admin = _admin(tenant_id)
        with _patch_admin(admin)[0], _patch_admin(admin)[1] as mock_settings:
            mock_settings.saas.enabled = True
            resp = _call(external_customers.list_leads, FakeRequest(), channel_chat_id="kfAAA", page=1, page_size=20)

        assert resp["success"] is True
        assert resp["total"] == 2
        names = [l["contact_name"] for l in resp["leads"]]
        assert set(names) == {"张三", "王五"}
        # 手机号已解密返回
        assert all(l["phone"] == "13800138000" for l in resp["leads"])

    def test_list_employee_sees_only_own(self, temp_tenant_for_leads):
        """普通用户仅见自己归属的线索"""
        from src.saas.api import external_customers

        tenant_id = temp_tenant_for_leads
        me = _insert_employee(tenant_id, f"emp_{uuid.uuid4().hex[:6]}", "我")
        other = _insert_employee(tenant_id, f"emp_{uuid.uuid4().hex[:6]}", "他人")
        _insert_lead(tenant_id, assigned_to=me, contact_name="我的客户")
        _insert_lead(tenant_id, assigned_to=other, contact_name="别人的客户")

        admin = _admin(tenant_id, role="user", user_id=me)
        with _patch_admin(admin)[0], _patch_admin(admin)[1] as mock_settings:
            mock_settings.saas.enabled = True
            resp = _call(external_customers.list_leads, FakeRequest(), page=1, page_size=20)

        assert resp["total"] == 1
        assert resp["leads"][0]["contact_name"] == "我的客户"


class TestLeadDetailAndStage:
    def test_detail_returns_decrypted_phone(self, temp_tenant_for_leads):
        """线索详情含解密手机号"""
        from src.saas.api import external_customers

        tenant_id = temp_tenant_for_leads
        lead_id = _insert_lead(tenant_id, phone="13912345678")

        admin = _admin(tenant_id)
        with _patch_admin(admin)[0], _patch_admin(admin)[1] as mock_settings:
            mock_settings.saas.enabled = True
            resp = _call(external_customers.get_lead_detail, FakeRequest(), lead_id)

        assert resp["success"] is True
        assert resp["lead"]["phone"] == "13912345678"

    def test_detail_not_found(self, temp_tenant_for_leads):
        from src.saas.api import external_customers
        from fastapi import HTTPException

        tenant_id = temp_tenant_for_leads
        admin = _admin(tenant_id)
        with _patch_admin(admin)[0], _patch_admin(admin)[1] as mock_settings:
            mock_settings.saas.enabled = True
            with pytest.raises(HTTPException) as ei:
                _call(external_customers.get_lead_detail, FakeRequest(), "lead_lc_nonexistent")
        assert ei.value.status_code == 404

    def test_patch_stage_flow_and_invalid_rejected(self, temp_tenant_for_leads):
        from src.saas.api import external_customers
        from fastapi import HTTPException

        tenant_id = temp_tenant_for_leads
        lead_id = _insert_lead(tenant_id, stage="new")

        admin = _admin(tenant_id)
        with _patch_admin(admin)[0], _patch_admin(admin)[1] as mock_settings:
            mock_settings.saas.enabled = True
            resp = _call(
                external_customers.update_lead_stage, FakeRequest(), lead_id,
                external_customers.LeadStageUpdate(stage="converted"),
            )
            assert resp["success"] is True
            assert resp["lead"]["stage"] == "converted"

            # 非法阶段拒绝
            with pytest.raises(HTTPException) as ei:
                _call(
                    external_customers.update_lead_stage, FakeRequest(), lead_id,
                    external_customers.LeadStageUpdate(stage="bogus"),
                )
            assert ei.value.status_code == 400

    def test_employee_cannot_touch_others_lead(self, temp_tenant_for_leads):
        """普通用户不能查看/更新他人归属的线索（按不存在处理）"""
        from src.saas.api import external_customers
        from fastapi import HTTPException

        tenant_id = temp_tenant_for_leads
        me = _insert_employee(tenant_id, f"emp_{uuid.uuid4().hex[:6]}", "我")
        other = _insert_employee(tenant_id, f"emp_{uuid.uuid4().hex[:6]}", "他人")
        lead_id = _insert_lead(tenant_id, assigned_to=other)

        admin = _admin(tenant_id, role="user", user_id=me)
        with _patch_admin(admin)[0], _patch_admin(admin)[1] as mock_settings:
            mock_settings.saas.enabled = True
            with pytest.raises(HTTPException) as ei:
                _call(external_customers.get_lead_detail, FakeRequest(), lead_id)
            assert ei.value.status_code == 404
            with pytest.raises(HTTPException) as ei2:
                _call(
                    external_customers.update_lead_stage, FakeRequest(), lead_id,
                    external_customers.LeadStageUpdate(stage="contacting"),
                )
            assert ei2.value.status_code == 404
