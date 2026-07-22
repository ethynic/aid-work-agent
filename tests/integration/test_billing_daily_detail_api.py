"""
每日用量明细下钻 API 集成测试（platform_admin 专用）

覆盖：
- platform_admin + X-Tenant-Id 调用返回 200 且 success: True
- tenant_admin / user 调用返回 success: False + "无权限"
- 未登录返回 401（require_admin raise HTTPException）
- 日期格式错误返回 success: False
- 分页参数 page=2, page_size=20 正确传给 SQL
"""

import pytest
import uuid
from datetime import datetime
from unittest.mock import patch

pytestmark = pytest.mark.integration


@pytest.fixture
def temp_tenant_for_detail():
    """创建临时租户用于明细测试，测试后清理"""
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

    # 清理：删除测试数据，软删租户，再物理删除
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM chat_records WHERE tenant_id = %s AND user_id LIKE 'detail_test_%%'",
                (tenant_id,),
            )
            # 清理 channel_sessions 测试数据（wecom_kf 渠道场景）
            cursor.execute(
                "DELETE FROM channel_sessions WHERE tenant_id = %s AND channel_user_id LIKE 'detail_test_%%'",
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


def _insert_chat_record(
    tenant_id: str,
    user_id: str,
    session_id: str,
    credit_cost: int = 1,
    prompt_tokens: int = 100,
    completion_tokens: int = 50,
    cached_input_tokens: int = 0,
    source_type: str = "chat",
    created_at: str | None = None,
):
    """直接 SQL 写入 chat_records，绕过 ChatRecordDB.create 的余额扣减逻辑"""
    from src.db.database import get_db_connection

    record_id = f"rec_{uuid.uuid4().hex[:12]}"
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if created_at:
            cursor.execute(
                """
                INSERT INTO chat_records
                (record_id, session_id, tenant_id, user_id, user_message, assistant_message,
                 prompt_tokens, completion_tokens, cached_input_tokens,
                 model, status, source_type, credit_cost, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    record_id, session_id, tenant_id, user_id,
                    "测试消息", "测试回复",
                    prompt_tokens, completion_tokens, cached_input_tokens,
                    "test-model", "completed", source_type, credit_cost, created_at,
                ),
            )
        else:
            cursor.execute(
                """
                INSERT INTO chat_records
                (record_id, session_id, tenant_id, user_id, user_message, assistant_message,
                 prompt_tokens, completion_tokens, cached_input_tokens,
                 model, status, source_type, credit_cost)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    record_id, session_id, tenant_id, user_id,
                    "测试消息", "测试回复",
                    prompt_tokens, completion_tokens, cached_input_tokens,
                    "test-model", "completed", source_type, credit_cost,
                ),
            )
        conn.commit()
    return record_id


def _insert_channel_session(
    tenant_id: str,
    session_id: str,
    channel_type: str = "wecom_kf",
    channel_user_id: str | None = None,
    title: str | None = None,
    user_id: str | None = None,
):
    """直接 SQL 写入 channel_sessions，用于测试渠道会话标题 JOIN"""
    from src.db.database import get_db_connection

    if channel_user_id is None:
        channel_user_id = f"detail_test_{uuid.uuid4().hex[:6]}"
    if title is None:
        title = f"测试{channel_type}会话"

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO channel_sessions
            (session_id, tenant_id, channel_type, channel_user_id, user_id, title)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (session_id, tenant_id, channel_type, channel_user_id, user_id, title),
        )
        conn.commit()
    return channel_user_id


class TestDailyUsageDetailAPI:
    """GET /api/saas/billing/usage/daily-detail 集成测试"""

    def test_platform_admin_with_x_tenant_id_returns_200(self, temp_tenant_for_detail):
        """场景 1：platform_admin + X-Tenant-Id 调用 -> 200 + success: True + items 字段"""
        from src.saas.api import billing_balance

        tenant_id = temp_tenant_for_detail
        today = datetime.now().strftime("%Y-%m-%d")

        # 写入一条 chat_records
        _insert_chat_record(
            tenant_id=tenant_id,
            user_id=f"detail_test_{uuid.uuid4().hex[:6]}",
            session_id=f"sess_{uuid.uuid4().hex[:8]}",
            credit_cost=5,
        )

        def fake_require_admin(request):
            return {
                "user_id": "platform_admin_xxx",
                "role": "platform_admin",
                "tenant_id": tenant_id,  # require_admin 已根据 X-Tenant-Id 切换
            }

        class FakeRequest:
            pass

        with patch("src.saas.api.billing_balance.require_admin", fake_require_admin), \
             patch("src.saas.api.billing_balance.settings") as mock_settings:
            mock_settings.saas.enabled = True

            import asyncio
            response = asyncio.get_event_loop().run_until_complete(
                billing_balance.get_daily_usage_detail(
                    FakeRequest(),
                    date=today,
                    page=1,
                    page_size=20,
                )
            )

        assert response["success"] is True
        assert response["date"] == today
        assert "items" in response
        assert response["total"] >= 1
        # 校验返回字段
        for it in response["items"]:
            assert "record_id" in it
            assert "session_id" in it
            assert "session_title" in it
            assert "user_display" in it
            assert "source_type" in it
            assert "prompt_tokens" in it
            assert "cached_input_tokens" in it
            assert "completion_tokens" in it
            assert "credit_cost" in it
            assert "created_at" in it

    def test_tenant_admin_returns_forbidden(self, temp_tenant_for_detail):
        """场景 2：tenant_admin 调用 -> success: False + message 含"无权限" """
        from src.saas.api import billing_balance

        tenant_id = temp_tenant_for_detail
        today = datetime.now().strftime("%Y-%m-%d")

        def fake_require_admin(request):
            return {
                "user_id": "tenant_admin_xxx",
                "role": "tenant_admin",
                "tenant_id": tenant_id,
            }

        class FakeRequest:
            pass

        with patch("src.saas.api.billing_balance.require_admin", fake_require_admin), \
             patch("src.saas.api.billing_balance.settings") as mock_settings:
            mock_settings.saas.enabled = True

            import asyncio
            response = asyncio.get_event_loop().run_until_complete(
                billing_balance.get_daily_usage_detail(
                    FakeRequest(),
                    date=today,
                    page=1,
                    page_size=20,
                )
            )

        assert response["success"] is False
        assert "无权限" in response["message"]

    def test_normal_user_returns_forbidden(self, temp_tenant_for_detail):
        """场景 3：普通 user 调用 -> success: False + message 含"无权限" """
        from src.saas.api import billing_balance

        tenant_id = temp_tenant_for_detail
        today = datetime.now().strftime("%Y-%m-%d")

        def fake_require_admin(request):
            return {
                "user_id": "normal_user_xxx",
                "role": "user",
                "tenant_id": tenant_id,
            }

        class FakeRequest:
            pass

        with patch("src.saas.api.billing_balance.require_admin", fake_require_admin), \
             patch("src.saas.api.billing_balance.settings") as mock_settings:
            mock_settings.saas.enabled = True

            import asyncio
            response = asyncio.get_event_loop().run_until_complete(
                billing_balance.get_daily_usage_detail(
                    FakeRequest(),
                    date=today,
                    page=1,
                    page_size=20,
                )
            )

        assert response["success"] is False
        assert "无权限" in response["message"]

    def test_unauthenticated_returns_401(self):
        """场景 4：未登录（require_admin raise HTTPException(401)）"""
        from fastapi import HTTPException
        from src.saas.api import billing_balance

        today = datetime.now().strftime("%Y-%m-%d")

        def fake_require_admin(request):
            # 模拟 require_admin 在未登录时 raise 401
            raise HTTPException(status_code=401, detail="未登录或登录已过期")

        class FakeRequest:
            pass

        with patch("src.saas.api.billing_balance.require_admin", fake_require_admin), \
             patch("src.saas.api.billing_balance.settings") as mock_settings:
            mock_settings.saas.enabled = True

            import asyncio
            with pytest.raises(HTTPException) as exc_info:
                asyncio.get_event_loop().run_until_complete(
                    billing_balance.get_daily_usage_detail(
                        FakeRequest(),
                        date=today,
                        page=1,
                        page_size=20,
                    )
                )
            assert exc_info.value.status_code == 401

    def test_invalid_date_format_returns_error(self, temp_tenant_for_detail):
        """场景 5：日期格式错误 -> success: False + message 含"日期格式错误" """
        from src.saas.api import billing_balance

        tenant_id = temp_tenant_for_detail

        def fake_require_admin(request):
            return {
                "user_id": "platform_admin_xxx",
                "role": "platform_admin",
                "tenant_id": tenant_id,
            }

        class FakeRequest:
            pass

        # 测试多种错误格式
        # 注意：Python strptime("%Y-%m-%d") 接受单位数月/日（如 2026-7-22），
        # 所以这里用真正无效的格式
        invalid_dates = [
            "invalid",       # 非日期字符串
            "2026/07/22",    # 分隔符错（应为 -）
            "26-07-22",      # 年份非 4 位
            "2026-13-01",    # 月份超界
            "2026-02-30",    # 日期不存在
        ]

        for invalid_date in invalid_dates:
            with patch("src.saas.api.billing_balance.require_admin", fake_require_admin), \
                 patch("src.saas.api.billing_balance.settings") as mock_settings:
                mock_settings.saas.enabled = True

                import asyncio
                response = asyncio.get_event_loop().run_until_complete(
                    billing_balance.get_daily_usage_detail(
                        FakeRequest(),
                        date=invalid_date,
                        page=1,
                        page_size=20,
                    )
                )

            assert response["success"] is False, f"日期 {invalid_date} 应被拒绝"
            assert "日期格式错误" in response["message"], f"日期 {invalid_date} 应提示格式错误"

    def test_pagination_page2_page_size20(self, temp_tenant_for_detail):
        """场景 6：分页参数 page=2, page_size=20 -> 正确返回第二页数据"""
        from src.saas.api import billing_balance

        tenant_id = temp_tenant_for_detail
        today = datetime.now().strftime("%Y-%m-%d")
        user_id = f"detail_test_{uuid.uuid4().hex[:6]}"

        # 写入 25 条记录（同一天），page_size=20 -> page1=20 条, page2=5 条
        for i in range(25):
            _insert_chat_record(
                tenant_id=tenant_id,
                user_id=user_id,
                session_id=f"sess_{uuid.uuid4().hex[:8]}",
                credit_cost=i + 1,
            )

        def fake_require_admin(request):
            return {
                "user_id": "platform_admin_xxx",
                "role": "platform_admin",
                "tenant_id": tenant_id,
            }

        class FakeRequest:
            pass

        with patch("src.saas.api.billing_balance.require_admin", fake_require_admin), \
             patch("src.saas.api.billing_balance.settings") as mock_settings:
            mock_settings.saas.enabled = True

            import asyncio
            # 查第 2 页
            response = asyncio.get_event_loop().run_until_complete(
                billing_balance.get_daily_usage_detail(
                    FakeRequest(),
                    date=today,
                    page=2,
                    page_size=20,
                )
            )

        assert response["success"] is True
        assert response["page"] == 2
        assert response["page_size"] == 20
        assert response["total"] >= 25
        # 第 2 页应有 5 条（25 - 20）
        assert len(response["items"]) == 5
        # 校验每条都有必要字段
        for it in response["items"]:
            assert "record_id" in it
            assert "credit_cost" in it
            assert "created_at" in it

    def test_channel_session_title_joined_from_channel_sessions(self, temp_tenant_for_detail):
        """场景 7：wecom_kf 渠道会话 -> session_title 从 channel_sessions 取，不为空

        回归 bug：原本只 JOIN chat_sessions，渠道会话（wecom_kf/dingtalk/feishu）的
        session_id 在 chat_sessions 表里不存在，导致标题返回空。
        """
        from src.saas.api import billing_balance

        tenant_id = temp_tenant_for_detail
        today = datetime.now().strftime("%Y-%m-%d")
        session_id = f"sess_{uuid.uuid4().hex[:8]}"
        expected_title = "张三的企微会话"

        # 1. 在 channel_sessions 插入一条 wecom_kf 会话（不在 chat_sessions 里）
        _insert_channel_session(
            tenant_id=tenant_id,
            session_id=session_id,
            channel_type="wecom_kf",
            title=expected_title,
        )

        # 2. 在 chat_records 写入对应记录，source_type=wecom_kf
        _insert_chat_record(
            tenant_id=tenant_id,
            user_id=f"detail_test_{uuid.uuid4().hex[:6]}",
            session_id=session_id,
            credit_cost=3,
            source_type="wecom_kf",
        )

        def fake_require_admin(request):
            return {
                "user_id": "platform_admin_xxx",
                "role": "platform_admin",
                "tenant_id": tenant_id,
            }

        class FakeRequest:
            pass

        with patch("src.saas.api.billing_balance.require_admin", fake_require_admin), \
             patch("src.saas.api.billing_balance.settings") as mock_settings:
            mock_settings.saas.enabled = True

            import asyncio
            response = asyncio.get_event_loop().run_until_complete(
                billing_balance.get_daily_usage_detail(
                    FakeRequest(),
                    date=today,
                    page=1,
                    page_size=20,
                )
            )

        assert response["success"] is True
        # 找到 wecom_kf 那条记录，校验 session_title 从 channel_sessions 取到
        items = [it for it in response["items"] if it["session_id"] == session_id]
        assert len(items) == 1, "应能查到刚插入的 wecom_kf 记录"
        assert items[0]["session_title"] == expected_title, \
            f"渠道会话标题应从 channel_sessions 取，期望 '{expected_title}'，实际 '{items[0]['session_title']}'"
        assert items[0]["source_type"] == "wecom_kf"
