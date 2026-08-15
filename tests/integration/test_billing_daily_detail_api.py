"""
每日用量明细下钻 API 集成测试（platform_admin 专用）

覆盖：
- platform_admin + X-Tenant-Id 调用返回 200 且 success: True
- tenant_admin / user 调用返回 success: False + "无权限"
- 未登录返回 401（require_admin raise HTTPException）
- 日期格式错误返回 success: False
- 分页参数 page=2, page_size=20 正确传给 SQL
"""

import json

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
    usage_breakdown: dict | None = None,
):
    """直接 SQL 写入 chat_records，绕过 ChatRecordDB.create 的余额扣减逻辑"""
    from src.db.database import get_db_connection

    record_id = f"rec_{uuid.uuid4().hex[:12]}"
    breakdown_json = json.dumps(usage_breakdown) if usage_breakdown else None
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if created_at:
            cursor.execute(
                """
                INSERT INTO chat_records
                (record_id, session_id, tenant_id, user_id, user_message, assistant_message,
                 prompt_tokens, completion_tokens, cached_input_tokens,
                 model, status, source_type, credit_cost, created_at, usage_breakdown)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    record_id, session_id, tenant_id, user_id,
                    "测试消息", "测试回复",
                    prompt_tokens, completion_tokens, cached_input_tokens,
                    "test-model", "completed", source_type, credit_cost, created_at,
                    breakdown_json,
                ),
            )
        else:
            cursor.execute(
                """
                INSERT INTO chat_records
                (record_id, session_id, tenant_id, user_id, user_message, assistant_message,
                 prompt_tokens, completion_tokens, cached_input_tokens,
                 model, status, source_type, credit_cost, usage_breakdown)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    record_id, session_id, tenant_id, user_id,
                    "测试消息", "测试回复",
                    prompt_tokens, completion_tokens, cached_input_tokens,
                    "test-model", "completed", source_type, credit_cost,
                    breakdown_json,
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
            assert "breakdown_items" in it, "平台管理员应返回 usage_breakdown 6 分项"
            assert "credit_cost" in it
            assert "created_at" in it

    def test_platform_admin_breakdown_items_parsed(self, temp_tenant_for_detail):
        """场景 1b：带 usage_breakdown 的记录 -> 平台管理员返回固定 6 分项对账结构

        验证 chat 三分项（未命中缓存输入/命中缓存输入/输出）、视频模型、ASR、向量模型
        的 qty / unit_price / usage_factor / credit / is_per_million 均正确解析，
        且缺失分项（video/embedding）字段为 None（前端按 "-" 兜底）。
        """
        from src.saas.api import billing_balance

        tenant_id = temp_tenant_for_detail
        today = datetime.now().strftime("%Y-%m-%d")

        breakdown = {
            "chat": {
                "non_cached_input_tokens": 641,
                "cached_input_tokens": 217088,
                "completion_tokens": 123,
                "total_tokens": 217852,
                "model": "deepseek-v4-flash",
                "credit": 1.24,
                "unit_prices": {"input_per_m": 3, "cached_input_per_m": 0.1, "output_per_m": 9},
                "usage_factor": 50,
                "credits": {
                    "non_cached_input": 0.09615,
                    "cached_input": 1.08544,
                    "output": 0.05535,
                },
            },
            "asr": {
                "calls": 1,
                "model": "aliyun-nls-asr",
                "credit": 1,
                "usage_factor": 100,
                "unit_price_per_call": 0.01,
            },
        }
        _insert_chat_record(
            tenant_id=tenant_id,
            user_id=f"detail_test_{uuid.uuid4().hex[:6]}",
            session_id=f"sess_{uuid.uuid4().hex[:8]}",
            credit_cost=3,
            usage_breakdown=breakdown,
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
        # 找到刚插入的记录，校验 6 分项结构
        items = [it for it in response["items"] if it["credit_cost"] == 3]
        assert len(items) == 1
        bd = items[0]["breakdown_items"]
        assert len(bd) == 6, "应返回固定 6 分项"

        # 6 分项 key 与标签
        assert [it["key"] for it in bd] == [
            "non_cached_input", "cached_input", "output", "video", "asr", "embedding",
        ]

        # chat 三分项：每百万单价 + 系数 + 积分
        non_cached = bd[0]
        assert non_cached["qty"] == 641
        assert non_cached["unit_price"] == 3
        assert non_cached["usage_factor"] == 50
        assert non_cached["credit"] == 0.09615
        assert non_cached["is_per_million"] is True

        cached = bd[1]
        assert cached["qty"] == 217088
        assert cached["unit_price"] == 0.1
        assert cached["usage_factor"] == 50
        assert cached["credit"] == 1.08544
        assert cached["is_per_million"] is True

        output = bd[2]
        assert output["qty"] == 123
        assert output["unit_price"] == 9
        assert output["usage_factor"] == 50
        assert output["credit"] == 0.05535
        assert output["is_per_million"] is True

        # 缺失分项（video/embedding）字段为 None
        video = bd[3]
        assert video["qty"] is None
        assert video["unit_price"] is None
        assert video["usage_factor"] is None
        assert video["credit"] is None
        assert video["is_per_million"] is False

        # ASR：每单位单价（元/次），无需 ÷1M
        asr = bd[4]
        assert asr["qty"] == 1
        assert asr["unit_price"] == 0.01
        assert asr["usage_factor"] == 100
        assert asr["credit"] == 1
        assert asr["is_per_million"] is False

        embedding = bd[5]
        assert embedding["qty"] is None
        assert embedding["is_per_million"] is True

    def test_platform_admin_old_breakdown_degrades_to_qty(self, temp_tenant_for_detail):
        """场景 1c：老数据（2026-08-14 前）usage_breakdown 无单价/系数/分项积分

        验证 chat 分项只有 prompt/completion/cached token 时：
        - non_cached_input qty 用 prompt - cached 兜底计算；
        - unit_price / usage_factor / credit 均为 None（前端降级只显示数量）。
        """
        from src.saas.api import billing_balance

        tenant_id = temp_tenant_for_detail
        today = datetime.now().strftime("%Y-%m-%d")

        old_breakdown = {
            "chat": {
                "prompt_tokens": 1000,
                "completion_tokens": 200,
                "cached_input_tokens": 600,
                "total_tokens": 1200,
                "model": "deepseek-v4-flash",
                "credit": 1.0,
            },
            "embedding": {
                "tokens": 500,
                "model": "text-embedding-v3",
                "credit": 0.1,
            },
            "asr": {
                "calls": 2,
                "model": "aliyun-nls-asr",
                "credit": 0.2,
            },
        }
        _insert_chat_record(
            tenant_id=tenant_id,
            user_id=f"detail_test_{uuid.uuid4().hex[:6]}",
            session_id=f"sess_{uuid.uuid4().hex[:8]}",
            credit_cost=2,
            usage_breakdown=old_breakdown,
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
        items = [it for it in response["items"] if it["credit_cost"] == 2]
        assert len(items) == 1
        bd = items[0]["breakdown_items"]
        assert len(bd) == 6

        # chat 三分项：数量可追溯，单价/系数/分项积分缺失
        non_cached = bd[0]
        assert non_cached["qty"] == 400, "老数据 non_cached_input 应为 prompt - cached = 1000 - 600"
        assert non_cached["unit_price"] is None
        assert non_cached["usage_factor"] is None
        assert non_cached["credit"] is None

        cached = bd[1]
        assert cached["qty"] == 600
        assert cached["unit_price"] is None

        output = bd[2]
        assert output["qty"] == 200
        assert output["unit_price"] is None

        # video 分项老数据不存在 -> qty None（前端显示 "-"）
        assert bd[3]["qty"] is None

        # asr / embedding 老数据有数量字段（calls / tokens），但无单价/系数
        asr = bd[4]
        assert asr["qty"] == 2
        assert asr["unit_price"] is None
        assert asr["usage_factor"] is None

        embedding = bd[5]
        assert embedding["qty"] == 500
        assert embedding["unit_price"] is None
        assert embedding["usage_factor"] is None

    def test_tenant_admin_success_without_token_fields(self, temp_tenant_for_detail):
        """场景 2：tenant_admin 调用 -> success: True，但不返回 token 三列

        租户管理员可查看自己租户的对话用量明细，但 prompt_tokens /
        cached_input_tokens / completion_tokens 三列仅平台管理员可见。
        """
        from src.saas.api import billing_balance

        tenant_id = temp_tenant_for_detail
        today = datetime.now().strftime("%Y-%m-%d")

        # 写入一条 chat_records，确保 items 非空以校验字段裁剪
        _insert_chat_record(
            tenant_id=tenant_id,
            user_id=f"detail_test_{uuid.uuid4().hex[:6]}",
            session_id=f"sess_{uuid.uuid4().hex[:8]}",
            credit_cost=5,
        )

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

        assert response["success"] is True
        assert response["total"] >= 1
        # 校验租户管理员不返回 token 三列
        for it in response["items"]:
            assert "record_id" in it
            assert "session_id" in it
            assert "session_title" in it
            assert "user_display" in it
            assert "source_type" in it
            assert "credit_cost" in it
            assert "created_at" in it
            assert "prompt_tokens" not in it, "租户管理员不应返回 prompt_tokens"
            assert "cached_input_tokens" not in it, "租户管理员不应返回 cached_input_tokens"
            assert "completion_tokens" not in it, "租户管理员不应返回 completion_tokens"

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
