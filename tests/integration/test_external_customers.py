"""
外部接待客户 API 集成测试（真实 PostgreSQL）

覆盖「客户 × 客服账号」组合粒度改造：
- GET /users 返回组合行（wecom_kf 按 open_kfid 拆分、其它渠道折叠、无会话客户）
- GET /users 返回行带 kf_name（客服账号名反查，未匹配回退原始 id）
- GET /users/{id}/sessions 支持 channel_type + channel_chat_id 组合过滤
- 引流员工下钻 referrer_user_id 过滤
"""

import json
import uuid

import pytest
from unittest.mock import patch

pytestmark = pytest.mark.integration


@pytest.fixture
def temp_tenant_for_external():
    """创建临时租户用于外部客户测试，测试后清理"""
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
                "DELETE FROM customer_referrals WHERE tenant_id = %s AND customer_user_id LIKE 'ext_test_%%'",
                (tenant_id,),
            )
            cursor.execute(
                "DELETE FROM channel_sessions WHERE tenant_id = %s AND channel_user_id LIKE 'ext_test_%%'",
                (tenant_id,),
            )
            cursor.execute(
                "DELETE FROM users WHERE tenant_id = %s AND user_id LIKE 'ext_test_%%'",
                (tenant_id,),
            )
            cursor.execute(
                "DELETE FROM tenant_channel_configs WHERE tenant_id = %s AND config_id LIKE 'cfg_%%'",
                (tenant_id,),
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


def _insert_user(tenant_id: str, user_id: str, username: str = None,
                 nickname: str = None, source: str = "wecom_kf"):
    """直接 SQL 写入外部客户（source 非空）"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO users (user_id, tenant_id, username, nickname, source, role, status)
            VALUES (%s, %s, %s, %s, %s, 'user', 'active')
            """,
            (user_id, tenant_id, username or user_id, nickname or username or user_id, source),
        )
        conn.commit()
    return user_id


def _insert_channel_session(tenant_id: str, session_id: str, user_id: str,
                            channel_type: str = "wecom_kf",
                            channel_chat_id: str | None = None):
    """直接 SQL 写入渠道会话"""
    from src.db.database import get_db_connection

    channel_user_id = f"ext_test_{uuid.uuid4().hex[:6]}"
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO channel_sessions
            (session_id, tenant_id, channel_type, channel_user_id, user_id, title, channel_chat_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (session_id, tenant_id, channel_type, channel_user_id, user_id,
             f"{channel_type}会话{channel_user_id}", channel_chat_id),
        )
        conn.commit()
    return channel_user_id


def _insert_wecom_kf_config(tenant_id: str, kf_accounts: list[dict]):
    """直接 SQL 写入 tenant_channel_configs（wecom_kf 渠道）"""
    from src.db.database import get_db_connection

    config_id = f"cfg_{uuid.uuid4().hex[:12]}"
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO tenant_channel_configs
            (config_id, tenant_id, channel_type, name, config, verified)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (config_id, tenant_id, "wecom_kf", "测试客服配置",
             json.dumps({"kf_account": kf_accounts}, ensure_ascii=False), 1),
        )
        conn.commit()
    return config_id


def _insert_employee(tenant_id: str, user_id: str, nickname: str = None):
    """直接 SQL 写入引流员工账号（source 为 NULL，不会进入外部客户列表）"""
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


def _insert_referral(tenant_id: str, customer_user_id: str, referrer_user_id: str, open_kfid: str):
    """直接 SQL 写入引流关系"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO customer_referrals (tenant_id, referrer_user_id, customer_user_id, open_kfid)
            VALUES (%s, %s, %s, %s)
            """,
            (tenant_id, referrer_user_id, customer_user_id, open_kfid),
        )
        conn.commit()


def _call(fn, *args, **kwargs):
    """在补丁 require_admin + settings 下同步调用异步 API 函数"""
    import asyncio
    return asyncio.get_event_loop().run_until_complete(fn(*args, **kwargs))


class FakeRequest:
    pass


class TestListExternalUsersCombination:
    """GET /users 组合粒度"""

    def test_wecom_kf_split_into_two_rows_with_kf_name(self, temp_tenant_for_external):
        """同一客户跟两个客服账号对话 → 返回两行，各带正确 kf_name"""
        from src.saas.api import external_customers

        tenant_id = temp_tenant_for_external
        user_id = _insert_user(tenant_id, f"ext_test_{uuid.uuid4().hex[:6]}", username="客户A")
        kf_a = f"open_kfid_a_{uuid.uuid4().hex[:6]}"
        kf_b = f"open_kfid_b_{uuid.uuid4().hex[:6]}"
        _insert_wecom_kf_config(tenant_id, [
            {"open_kfid": kf_a, "name": "客服-曹老师"},
            {"open_kfid": kf_b, "name": "客服-李老师"},
        ])
        _insert_channel_session(tenant_id, f"sess_{uuid.uuid4().hex[:8]}", user_id,
                                channel_type="wecom_kf", channel_chat_id=kf_a)
        _insert_channel_session(tenant_id, f"sess_{uuid.uuid4().hex[:8]}", user_id,
                                channel_type="wecom_kf", channel_chat_id=kf_b)

        def fake_require_admin(request):
            return {"user_id": "admin_x", "role": "platform_admin", "tenant_id": tenant_id}

        with patch("src.saas.api.external_customers.require_admin", fake_require_admin), \
             patch("src.saas.api.external_customers.settings") as mock_settings:
            mock_settings.saas.enabled = True
            resp = _call(external_customers.list_external_users, FakeRequest(), page=1, page_size=20)

        assert resp["success"] is True
        assert resp["total"] == 2, "同一客户两个客服账号应返回两行组合"
        rows = resp["users"]
        assert len(rows) == 2
        by_kfid = {r["channel_chat_id"]: r for r in rows}
        assert by_kfid[kf_a]["kf_name"] == "客服-曹老师"
        assert by_kfid[kf_b]["kf_name"] == "客服-李老师"
        assert all(r["user_id"] == user_id for r in rows)
        assert all(r["channel_type"] == "wecom_kf" for r in rows)

    def test_legacy_null_and_other_channel_fold_to_empty(self, temp_tenant_for_external):
        """legacy NULL 会话与其它渠道会话折叠为空串组合，无会话客户单行"""
        from src.saas.api import external_customers

        tenant_id = temp_tenant_for_external
        u_legacy = _insert_user(tenant_id, f"ext_test_{uuid.uuid4().hex[:6]}", nickname="客户B")
        u_none = _insert_user(tenant_id, f"ext_test_{uuid.uuid4().hex[:6]}", nickname="客户C")
        # wecom_kf legacy NULL 会话
        _insert_channel_session(tenant_id, f"sess_{uuid.uuid4().hex[:8]}", u_legacy,
                                channel_type="wecom_kf", channel_chat_id=None)
        # 其它渠道（wecom）会话：即使 channel_chat_id 非空也应折叠为空串
        _insert_channel_session(tenant_id, f"sess_{uuid.uuid4().hex[:8]}", u_legacy,
                                channel_type="wecom", channel_chat_id="grp_xxx")

        def fake_require_admin(request):
            return {"user_id": "admin_x", "role": "platform_admin", "tenant_id": tenant_id}

        with patch("src.saas.api.external_customers.require_admin", fake_require_admin), \
             patch("src.saas.api.external_customers.settings") as mock_settings:
            mock_settings.saas.enabled = True
            resp = _call(external_customers.list_external_users, FakeRequest(), page=1, page_size=20)

        assert resp["success"] is True
        rows = resp["users"]
        # 客户B：wecom_kf 空串一行 + wecom 空串一行（channel_type 区分）
        rows_b = [r for r in rows if r["user_id"] == u_legacy]
        assert len(rows_b) == 2
        types = sorted(r["channel_type"] for r in rows_b)
        assert types == ["wecom", "wecom_kf"]
        assert all(r["channel_chat_id"] == "" for r in rows_b)
        assert all(r["kf_name"] is None for r in rows_b)
        # 客户C：无会话单行，channel_chat_id 归一为空串、kf_name None
        rows_c = [r for r in rows if r["user_id"] == u_none]
        assert len(rows_c) == 1
        assert rows_c[0]["channel_chat_id"] == ""
        assert rows_c[0]["kf_name"] is None
        assert rows_c[0]["channel_type"] is None

    def test_referrer_drill_down_filter(self, temp_tenant_for_external):
        """引流统计下钻：referrer_user_id 过滤只返回该员工引流的客户组合"""
        from src.saas.api import external_customers

        tenant_id = temp_tenant_for_external
        u_referred = _insert_user(tenant_id, f"ext_test_{uuid.uuid4().hex[:6]}", nickname="客户D")
        u_other = _insert_user(tenant_id, f"ext_test_{uuid.uuid4().hex[:6]}", nickname="客户E")
        kf = f"open_kfid_r_{uuid.uuid4().hex[:6]}"
        _insert_wecom_kf_config(tenant_id, [{"open_kfid": kf, "name": "客服-R"}])
        _insert_channel_session(tenant_id, f"sess_{uuid.uuid4().hex[:8]}", u_referred,
                                channel_type="wecom_kf", channel_chat_id=kf)
        _insert_channel_session(tenant_id, f"sess_{uuid.uuid4().hex[:8]}", u_other,
                                channel_type="wecom_kf", channel_chat_id=kf)
        emp_id = f"emp_{uuid.uuid4().hex[:6]}"
        _insert_employee(tenant_id, emp_id, nickname="引流张经理")
        _insert_referral(tenant_id, u_referred, emp_id, kf)

        def fake_require_admin(request):
            return {"user_id": "admin_x", "role": "platform_admin", "tenant_id": tenant_id}

        with patch("src.saas.api.external_customers.require_admin", fake_require_admin), \
             patch("src.saas.api.external_customers.settings") as mock_settings:
            mock_settings.saas.enabled = True
            resp = _call(external_customers.list_external_users, FakeRequest(),
                         referrer_user_id=emp_id, page=1, page_size=20)

        assert resp["success"] is True
        assert resp["total"] == 1
        assert resp["users"][0]["user_id"] == u_referred
        assert resp["users"][0]["referrer_name"] == "引流张经理"


class TestGetUserSessionsFilter:
    """GET /users/{id}/sessions 组合过滤"""

    def test_filter_by_channel_chat_id(self, temp_tenant_for_external):
        """channel_type + channel_chat_id 组合过滤只返回该客服账号的会话"""
        from src.saas.api import external_customers

        tenant_id = temp_tenant_for_external
        user_id = _insert_user(tenant_id, f"ext_test_{uuid.uuid4().hex[:6]}", nickname="客户F")
        kf_a = f"open_kfid_a_{uuid.uuid4().hex[:6]}"
        kf_b = f"open_kfid_b_{uuid.uuid4().hex[:6]}"
        sess_a = f"sess_{uuid.uuid4().hex[:8]}"
        sess_b = f"sess_{uuid.uuid4().hex[:8]}"
        _insert_channel_session(tenant_id, sess_a, user_id, channel_type="wecom_kf", channel_chat_id=kf_a)
        _insert_channel_session(tenant_id, sess_b, user_id, channel_type="wecom_kf", channel_chat_id=kf_b)

        def fake_require_admin(request):
            return {"user_id": "admin_x", "role": "platform_admin", "tenant_id": tenant_id}

        with patch("src.saas.api.external_customers.require_admin", fake_require_admin), \
             patch("src.saas.api.external_customers.settings") as mock_settings:
            mock_settings.saas.enabled = True
            resp = _call(external_customers.get_user_sessions, FakeRequest(), user_id=user_id,
                         channel_type="wecom_kf", channel_chat_id=kf_a, page=1, page_size=20)

        assert resp["success"] is True
        sessions = resp["sessions"]
        assert len(sessions) == 1, "只应返回指定客服账号的会话"
        assert sessions[0]["session_id"] == sess_a
        assert sessions[0]["channel_chat_id"] == kf_a

    def test_empty_chat_id_returns_legacy_null_session(self, temp_tenant_for_external):
        """channel_chat_id 空串过滤只返回 legacy NULL 会话（不混入其它渠道）"""
        from src.saas.api import external_customers

        tenant_id = temp_tenant_for_external
        user_id = _insert_user(tenant_id, f"ext_test_{uuid.uuid4().hex[:6]}", nickname="客户G")
        sess_legacy = f"sess_{uuid.uuid4().hex[:8]}"
        sess_wecom = f"sess_{uuid.uuid4().hex[:8]}"
        _insert_channel_session(tenant_id, sess_legacy, user_id, channel_type="wecom_kf", channel_chat_id=None)
        _insert_channel_session(tenant_id, sess_wecom, user_id, channel_type="wecom", channel_chat_id=None)

        def fake_require_admin(request):
            return {"user_id": "admin_x", "role": "platform_admin", "tenant_id": tenant_id}

        with patch("src.saas.api.external_customers.require_admin", fake_require_admin), \
             patch("src.saas.api.external_customers.settings") as mock_settings:
            mock_settings.saas.enabled = True
            # wecom_kf + 空串：只应命中 legacy NULL 的 wecom_kf 会话
            resp = _call(external_customers.get_user_sessions, FakeRequest(), user_id=user_id,
                         channel_type="wecom_kf", channel_chat_id="", page=1, page_size=20)

        assert resp["success"] is True
        sessions = resp["sessions"]
        assert len(sessions) == 1, "空串过滤应只返回 wecom_kf legacy NULL 会话，不混入其它渠道"
        assert sessions[0]["session_id"] == sess_legacy
        assert sessions[0]["channel_type"] == "wecom_kf"
