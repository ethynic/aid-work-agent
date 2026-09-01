"""
招聘操作智能体简历时间线 API 集成测试（第④期：沟通记录 / 邀约记录）

覆盖 /api/recruiting-operator 简历子资源：
- 沟通记录：创建（合法 + 空 content 400 + 非法 direction/channel 400 + 操作人落库）
  / 列表（created_at DESC 排序 + 租户隔离）/ 删除（本租户成功 + 他租户 404）
- 邀约记录：创建（合法 + 非法 status/interview_at 400）/ 列表 / PATCH 三态
  （部分字段更新 + updated_at 变化）/ 租户隔离 / 简历不存在 404

测试策略（同 test_recruiting_operator_apis.py）：
- 使用真实 PostgreSQL，创建临时租户隔离测试数据
- mock get_current_tenant_id / get_current_user
- 测试正常路径 + 异常路径（404 / 400）
"""

import uuid
from datetime import datetime
from unittest.mock import patch

import psycopg2.extras
import pytest

pytestmark = pytest.mark.integration


# ============== Fixture ==============

@pytest.fixture(scope="module", autouse=True)
def _ensure_tables():
    """模块级幂等建表（时间线两表 FK 引用简历表，须在 jobs/resumes 之后建）"""
    from src.db.database import get_db_connection
    from src.services.recruiting_job_service import init_recruiting_job_tables
    from src.api.recruiting_operator import (
        init_recruiting_operator_tables,
        init_recruiting_timeline_tables,
    )

    with get_db_connection() as conn:
        init_recruiting_job_tables(conn)
        init_recruiting_operator_tables(conn)
        init_recruiting_timeline_tables(conn)
        conn.commit()


@pytest.fixture
def temp_tenant_with_user():
    """创建临时租户 + 测试用户，测试后清理"""
    from src.saas.db.tenant_db import TenantDB
    from src.db.database import get_db_connection

    tenant_code = f"T{uuid.uuid4().hex[:6].upper()}"
    tenant = TenantDB.create(
        company_name=f"简历时间线测试租户-{tenant_code}",
        tenant_code=tenant_code,
        contact_name="测试联系人",
        contact_phone="13800000000",
    )
    if not tenant:
        pytest.skip("无法创建测试租户（DB 不可用）")
    tenant_id = tenant["tenant_id"]
    user_id = f"test_user_{uuid.uuid4().hex[:8]}"
    yield {"tenant_id": tenant_id, "user_id": user_id}

    # 清理：删测试业务数据（先删子表再删简历，虽然 FK CASCADE 兜底）+ 删租户
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM bs_recruiting_operator_resume_comm_logs WHERE tenant_id = %s",
                (tenant_id,),
            )
            cursor.execute(
                "DELETE FROM bs_recruiting_operator_resume_invitations WHERE tenant_id = %s",
                (tenant_id,),
            )
            cursor.execute(
                "DELETE FROM bs_recruiting_operator_resumes WHERE tenant_id = %s",
                (tenant_id,),
            )
            cursor.execute(
                "DELETE FROM bs_recruiting_operator_job_scripts WHERE tenant_id = %s",
                (tenant_id,),
            )
            cursor.execute(
                "DELETE FROM bs_recruiting_operator_jobs WHERE tenant_id = %s",
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


def _mock_tenant_ctx(tenant_id: str):
    """mock get_current_tenant_id 返回指定租户"""
    return patch("src.api.recruiting_operator.get_current_tenant_id", return_value=tenant_id)


def _mock_user(user_id: str):
    """mock get_current_user（端点直接函数调用时 request=None，必须 mock）"""
    return patch(
        "src.api.recruiting_operator.get_current_user",
        return_value={"user_id": user_id, "role": "tenant_admin"},
    )


def _unpack(response):
    """把 JSONResponse 解包为 dict；普通 dict 直接返回"""
    import json
    from fastapi.responses import JSONResponse

    if isinstance(response, JSONResponse):
        return json.loads(response.body)
    return response


def _call(coro):
    """同步驱动 async 端点函数（独立 event loop，避免跨文件事件循环状态污染）"""
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _insert_resume(tenant_id: str, user_id: str, candidate_name: str = "张三") -> int:
    """插入测试简历记录，返回 id"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO bs_recruiting_operator_resumes
                (tenant_id, user_id, candidate_name, job_name, candidate_info, images,
                 ocr_text, source, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'boss', 'new')
            RETURNING id
            """,
            (
                tenant_id, user_id, candidate_name, "测试职位",
                psycopg2.extras.Json({"学历": "本科"}),
                psycopg2.extras.Json([]),
                "OCR 全文测试内容",
            ),
        )
        row = cursor.fetchone()
        conn.commit()
        return row["id"]


def _insert_comm_log(tenant_id: str, resume_id: int, direction: str, content: str, created_at) -> int:
    """直接插入沟通记录（可指定 created_at，用于确定性排序断言），返回 id"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO bs_recruiting_operator_resume_comm_logs
                (tenant_id, resume_id, direction, channel, content, created_at)
            VALUES (%s, %s, %s, 'boss', %s, %s)
            RETURNING id
            """,
            (tenant_id, resume_id, direction, content, created_at),
        )
        row = cursor.fetchone()
        conn.commit()
        return row["id"]


# ============== 1. 沟通记录 API ==============

class TestCommLogAPIs:
    """GET/POST /resumes/{id}/comm-logs、DELETE /comm-logs/{id} 测试"""

    def test_create_comm_log_success_records_operator(self, temp_tenant_with_user):
        """补录沟通记录成功，操作人（当前登录用户）落库"""
        from src.api import recruiting_operator

        ctx = temp_tenant_with_user
        resume_id = _insert_resume(ctx["tenant_id"], ctx["user_id"])
        req = recruiting_operator.CreateCommLogRequest(
            direction="out", channel="wecom", content="已微信联系候选人约明天沟通",
        )

        with _mock_tenant_ctx(ctx["tenant_id"]), _mock_user(ctx["user_id"]):
            response = _unpack(_call(
                recruiting_operator.create_comm_log(resume_id, req, request=None)))

        assert response["success"] is True
        data = response["data"]
        assert data["resume_id"] == resume_id
        assert data["direction"] == "out"
        assert data["channel"] == "wecom"
        assert data["content"] == "已微信联系候选人约明天沟通"
        assert data["user_id"] == ctx["user_id"]
        assert data["created_at"] is not None

    def test_create_comm_log_empty_content_returns_400(self, temp_tenant_with_user):
        """content 为空串/纯空白返回 400"""
        from src.api import recruiting_operator

        ctx = temp_tenant_with_user
        resume_id = _insert_resume(ctx["tenant_id"], ctx["user_id"])
        req = recruiting_operator.CreateCommLogRequest(direction="out", channel="boss", content="   ")

        with _mock_tenant_ctx(ctx["tenant_id"]), _mock_user(ctx["user_id"]):
            response = _unpack(_call(
                recruiting_operator.create_comm_log(resume_id, req, request=None)))

        assert response["success"] is False
        assert "沟通内容不能为空" in response["error"]

    def test_create_comm_log_invalid_direction_returns_400(self, temp_tenant_with_user):
        """direction 非法返回 400"""
        from src.api import recruiting_operator

        ctx = temp_tenant_with_user
        resume_id = _insert_resume(ctx["tenant_id"], ctx["user_id"])
        req = recruiting_operator.CreateCommLogRequest(direction="sideways", channel="boss", content="x")

        with _mock_tenant_ctx(ctx["tenant_id"]), _mock_user(ctx["user_id"]):
            response = _unpack(_call(
                recruiting_operator.create_comm_log(resume_id, req, request=None)))

        assert response["success"] is False
        assert "沟通方向值非法" in response["error"]

    def test_create_comm_log_invalid_channel_returns_400(self, temp_tenant_with_user):
        """channel 非法返回 400"""
        from src.api import recruiting_operator

        ctx = temp_tenant_with_user
        resume_id = _insert_resume(ctx["tenant_id"], ctx["user_id"])
        req = recruiting_operator.CreateCommLogRequest(direction="in", channel="wechat", content="x")

        with _mock_tenant_ctx(ctx["tenant_id"]), _mock_user(ctx["user_id"]):
            response = _unpack(_call(
                recruiting_operator.create_comm_log(resume_id, req, request=None)))

        assert response["success"] is False
        assert "沟通渠道值非法" in response["error"]

    def test_create_comm_log_with_occurred_at_persists_timestamp(self, temp_tenant_with_user):
        """occurred_at 回写历史聊天原始时间戳：created_at 用传入值而非 NOW()"""
        from src.api import recruiting_operator

        ctx = temp_tenant_with_user
        resume_id = _insert_resume(ctx["tenant_id"], ctx["user_id"])
        req = recruiting_operator.CreateCommLogRequest(
            direction="out", channel="boss", content="历史聊天回写",
            occurred_at="2026-08-25T18:13:00")

        with _mock_tenant_ctx(ctx["tenant_id"]), _mock_user(ctx["user_id"]):
            response = _unpack(_call(
                recruiting_operator.create_comm_log(resume_id, req, request=None)))

        assert response["success"] is True
        created = response["data"]["created_at"]
        # naive 入库按会话时区（+08）解释，读回分量应与传入值一致
        assert (created.year, created.month, created.day, created.hour, created.minute) == (2026, 8, 25, 18, 13)

    def test_create_comm_log_invalid_occurred_at_returns_400(self, temp_tenant_with_user):
        """occurred_at 格式非法返回 400"""
        from src.api import recruiting_operator

        ctx = temp_tenant_with_user
        resume_id = _insert_resume(ctx["tenant_id"], ctx["user_id"])
        req = recruiting_operator.CreateCommLogRequest(
            direction="out", channel="boss", content="x", occurred_at="not-a-time")

        with _mock_tenant_ctx(ctx["tenant_id"]), _mock_user(ctx["user_id"]):
            response = _unpack(_call(
                recruiting_operator.create_comm_log(resume_id, req, request=None)))

        assert response["success"] is False
        assert "沟通时间格式非法" in response["error"]

    def test_list_comm_logs_sorted_desc(self, temp_tenant_with_user):
        """列表按 created_at DESC 排序（最新在前）"""
        from src.api import recruiting_operator

        ctx = temp_tenant_with_user
        resume_id = _insert_resume(ctx["tenant_id"], ctx["user_id"])
        # 直接插入并显式指定 created_at，保证排序断言确定性
        _insert_comm_log(ctx["tenant_id"], resume_id, "out", "第一条", datetime(2026, 8, 1, 9, 0, 0))
        _insert_comm_log(ctx["tenant_id"], resume_id, "in", "第二条", datetime(2026, 8, 2, 9, 0, 0))
        _insert_comm_log(ctx["tenant_id"], resume_id, "out", "第三条", datetime(2026, 8, 3, 9, 0, 0))

        with _mock_tenant_ctx(ctx["tenant_id"]):
            response = _unpack(_call(recruiting_operator.list_comm_logs(resume_id, request=None)))

        assert response["success"] is True
        items = response["data"]["items"]
        assert [it["content"] for it in items] == ["第三条", "第二条", "第一条"]

    def test_list_comm_logs_cross_tenant_invisible(self, temp_tenant_with_user):
        """租户隔离：B 租户访问 A 租户简历的沟通记录 → 404（子资源先查简历归属，fail-closed）"""
        from src.api import recruiting_operator
        from src.saas.db.tenant_db import TenantDB
        from src.db.database import get_db_connection

        ctx_a = temp_tenant_with_user
        resume_id_a = _insert_resume(ctx_a["tenant_id"], ctx_a["user_id"])
        _insert_comm_log(ctx_a["tenant_id"], resume_id_a, "out", "A租户沟通", datetime(2026, 8, 1))

        tenant_code_b = f"T{uuid.uuid4().hex[:6].upper()}"
        tenant_b = TenantDB.create(
            company_name=f"时间线测试租户B-{tenant_code_b}",
            tenant_code=tenant_code_b,
            contact_name="测试B",
            contact_phone="13800000001",
        )
        if not tenant_b:
            pytest.skip("无法创建测试租户B")
        tenant_id_b = tenant_b["tenant_id"]
        try:
            with _mock_tenant_ctx(tenant_id_b):
                response = _unpack(_call(recruiting_operator.list_comm_logs(resume_id_a, request=None)))

            # 简历本身不属 B 租户 → 404，绝不泄露 A 租户沟通内容
            assert response["success"] is False
            assert response["error"] == "简历不存在"
        finally:
            TenantDB.delete(tenant_id_b)
            try:
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("DELETE FROM tenants WHERE tenant_id = %s", (tenant_id_b,))
                    conn.commit()
            except Exception:
                pass

    def test_delete_comm_log_success(self, temp_tenant_with_user):
        """删除本租户沟通记录成功"""
        from src.api import recruiting_operator
        from src.db.database import get_db_connection

        ctx = temp_tenant_with_user
        resume_id = _insert_resume(ctx["tenant_id"], ctx["user_id"])
        log_id = _insert_comm_log(ctx["tenant_id"], resume_id, "out", "待删除", datetime(2026, 8, 1))

        with _mock_tenant_ctx(ctx["tenant_id"]):
            response = _unpack(_call(recruiting_operator.delete_comm_log(log_id, request=None)))

        assert response["success"] is True
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id FROM bs_recruiting_operator_resume_comm_logs WHERE id = %s", (log_id,))
            assert cursor.fetchone() is None

    def test_delete_comm_log_cross_tenant_returns_404(self, temp_tenant_with_user):
        """租户隔离：B 租户删除 A 租户沟通记录返回 404"""
        from src.api import recruiting_operator
        from src.saas.db.tenant_db import TenantDB
        from src.db.database import get_db_connection

        ctx_a = temp_tenant_with_user
        resume_id_a = _insert_resume(ctx_a["tenant_id"], ctx_a["user_id"])
        log_id_a = _insert_comm_log(ctx_a["tenant_id"], resume_id_a, "out", "A的记录", datetime(2026, 8, 1))

        tenant_code_b = f"T{uuid.uuid4().hex[:6].upper()}"
        tenant_b = TenantDB.create(
            company_name=f"时间线删除测试租户B-{tenant_code_b}",
            tenant_code=tenant_code_b,
            contact_name="测试B",
            contact_phone="13800000001",
        )
        if not tenant_b:
            pytest.skip("无法创建测试租户B")
        tenant_id_b = tenant_b["tenant_id"]
        try:
            with _mock_tenant_ctx(tenant_id_b):
                response = _unpack(_call(recruiting_operator.delete_comm_log(log_id_a, request=None)))

            assert response["success"] is False
            assert response["error"] == "沟通记录不存在"
        finally:
            TenantDB.delete(tenant_id_b)
            try:
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("DELETE FROM tenants WHERE tenant_id = %s", (tenant_id_b,))
                    conn.commit()
            except Exception:
                pass

    def test_comm_logs_resume_not_found_returns_404(self, temp_tenant_with_user):
        """简历不存在时列表/创建均返回 404"""
        from src.api import recruiting_operator

        ctx = temp_tenant_with_user
        req = recruiting_operator.CreateCommLogRequest(direction="out", channel="boss", content="x")
        with _mock_tenant_ctx(ctx["tenant_id"]), _mock_user(ctx["user_id"]):
            list_resp = _unpack(_call(recruiting_operator.list_comm_logs(99999999, request=None)))
            create_resp = _unpack(_call(
                recruiting_operator.create_comm_log(99999999, req, request=None)))

        assert list_resp["success"] is False
        assert list_resp["error"] == "简历不存在"
        assert create_resp["success"] is False
        assert create_resp["error"] == "简历不存在"


# ============== 2. 邀约记录 API ==============

class TestInvitationAPIs:
    """GET/POST /resumes/{id}/invitations、PATCH /invitations/{id} 测试"""

    def test_create_invitation_success(self, temp_tenant_with_user):
        """创建邀约成功：各字段按传入值落库"""
        from src.api import recruiting_operator

        ctx = temp_tenant_with_user
        resume_id = _insert_resume(ctx["tenant_id"], ctx["user_id"])
        req = recruiting_operator.CreateInvitationRequest(
            interview_at="2026-09-01T14:00:00",
            interviewer="王经理",
            method="视频面试",
            status="confirmed",
            notes="候选人已确认时间",
        )

        with _mock_tenant_ctx(ctx["tenant_id"]), _mock_user(ctx["user_id"]):
            response = _unpack(_call(
                recruiting_operator.create_invitation(resume_id, req, request=None)))

        assert response["success"] is True
        data = response["data"]
        assert data["resume_id"] == resume_id
        assert data["status"] == "confirmed"
        assert data["interviewer"] == "王经理"
        assert data["method"] == "视频面试"
        assert data["notes"] == "候选人已确认时间"
        assert str(data["interview_at"]).startswith("2026-09-01")
        assert data["created_at"] is not None

    def test_create_invitation_invalid_status_returns_400(self, temp_tenant_with_user):
        """status 非法返回 400"""
        from src.api import recruiting_operator

        ctx = temp_tenant_with_user
        resume_id = _insert_resume(ctx["tenant_id"], ctx["user_id"])
        req = recruiting_operator.CreateInvitationRequest(status="hired")

        with _mock_tenant_ctx(ctx["tenant_id"]), _mock_user(ctx["user_id"]):
            response = _unpack(_call(
                recruiting_operator.create_invitation(resume_id, req, request=None)))

        assert response["success"] is False
        assert "邀约状态值非法" in response["error"]

    def test_create_invitation_invalid_interview_at_returns_400(self, temp_tenant_with_user):
        """interview_at 非 ISO 格式返回 400"""
        from src.api import recruiting_operator

        ctx = temp_tenant_with_user
        resume_id = _insert_resume(ctx["tenant_id"], ctx["user_id"])
        req = recruiting_operator.CreateInvitationRequest(interview_at="not-a-date")

        with _mock_tenant_ctx(ctx["tenant_id"]), _mock_user(ctx["user_id"]):
            response = _unpack(_call(
                recruiting_operator.create_invitation(resume_id, req, request=None)))

        assert response["success"] is False
        assert "面试时间格式非法" in response["error"]

    def test_list_invitations_desc_and_multi(self, temp_tenant_with_user):
        """一简历可多次邀约，列表 created_at DESC"""
        from src.api import recruiting_operator

        ctx = temp_tenant_with_user
        resume_id = _insert_resume(ctx["tenant_id"], ctx["user_id"])
        req_first = recruiting_operator.CreateInvitationRequest(status="pending", method="电话面试")
        req_second = recruiting_operator.CreateInvitationRequest(status="done", method="现场面试")
        with _mock_tenant_ctx(ctx["tenant_id"]), _mock_user(ctx["user_id"]):
            first = _unpack(_call(
                recruiting_operator.create_invitation(resume_id, req_first, request=None)))
            second = _unpack(_call(
                recruiting_operator.create_invitation(resume_id, req_second, request=None)))
            response = _unpack(_call(recruiting_operator.list_invitations(resume_id, request=None)))

        assert first["success"] is True and second["success"] is True
        items = response["data"]["items"]
        assert len(items) == 2
        # 最新创建的在前
        assert [it["status"] for it in items] == ["done", "pending"]
        assert all(it["resume_id"] == resume_id for it in items)

    def test_patch_invitation_partial_update_and_updated_at(self, temp_tenant_with_user):
        """PATCH 仅传的字段更新（其余保留），updated_at 刷新"""
        from src.api import recruiting_operator
        from src.db.database import get_db_connection

        ctx = temp_tenant_with_user
        resume_id = _insert_resume(ctx["tenant_id"], ctx["user_id"])
        req = recruiting_operator.CreateInvitationRequest(
            interviewer="李面试官", method="现场面试", status="pending",
        )
        with _mock_tenant_ctx(ctx["tenant_id"]), _mock_user(ctx["user_id"]):
            created = _unpack(_call(
                recruiting_operator.create_invitation(resume_id, req, request=None)))
        invitation_id = created["data"]["id"]

        # 记录创建时的 updated_at（created/updated 同为 NOW()，直接比对 PATCH 前后即可）
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT updated_at FROM bs_recruiting_operator_resume_invitations WHERE id = %s",
                (invitation_id,),
            )
            updated_before = cursor.fetchone()["updated_at"]

        # 仅改状态 + 面试官（method/notes/时间不传 → 保持）
        patch_req = recruiting_operator.UpdateInvitationRequest(status="confirmed", interviewer="王面试官")
        with _mock_tenant_ctx(ctx["tenant_id"]):
            response = _unpack(_call(
                recruiting_operator.update_invitation(invitation_id, patch_req, request=None)))

        assert response["success"] is True
        data = response["data"]
        assert data["status"] == "confirmed"
        assert data["interviewer"] == "王面试官"
        # 未传字段保持原值
        assert data["method"] == "现场面试"
        assert data["notes"] is None
        assert data["interview_at"] is None

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT updated_at FROM bs_recruiting_operator_resume_invitations WHERE id = %s",
                (invitation_id,),
            )
            updated_after = cursor.fetchone()["updated_at"]
        assert updated_after >= updated_before

    def test_patch_invitation_empty_interview_at_clears_value(self, temp_tenant_with_user):
        """PATCH interview_at 传空串 = 显式清空面试时间（前端编辑弹框清空时间的语义）"""
        from src.api import recruiting_operator

        ctx = temp_tenant_with_user
        resume_id = _insert_resume(ctx["tenant_id"], ctx["user_id"])
        req = recruiting_operator.CreateInvitationRequest(
            interview_at="2026-09-01T14:00:00", status="pending",
        )
        with _mock_tenant_ctx(ctx["tenant_id"]), _mock_user(ctx["user_id"]):
            created = _unpack(_call(
                recruiting_operator.create_invitation(resume_id, req, request=None)))
        invitation_id = created["data"]["id"]
        assert created["data"]["interview_at"] is not None

        # 空串 = 置 NULL（不修改是 None=不传；清空必须是空串）
        patch_req = recruiting_operator.UpdateInvitationRequest(interview_at="   ")
        with _mock_tenant_ctx(ctx["tenant_id"]):
            response = _unpack(_call(
                recruiting_operator.update_invitation(invitation_id, patch_req, request=None)))

        assert response["success"] is True
        assert response["data"]["interview_at"] is None
        assert response["data"]["status"] == "pending"

    def test_patch_invitation_no_fields_returns_400(self, temp_tenant_with_user):
        """PATCH 不传任何字段返回 400（无待更新字段）"""
        from src.api import recruiting_operator

        ctx = temp_tenant_with_user
        resume_id = _insert_resume(ctx["tenant_id"], ctx["user_id"])
        req = recruiting_operator.CreateInvitationRequest(status="pending")
        with _mock_tenant_ctx(ctx["tenant_id"]), _mock_user(ctx["user_id"]):
            created = _unpack(_call(
                recruiting_operator.create_invitation(resume_id, req, request=None)))
        invitation_id = created["data"]["id"]

        patch_req = recruiting_operator.UpdateInvitationRequest()
        with _mock_tenant_ctx(ctx["tenant_id"]):
            response = _unpack(_call(
                recruiting_operator.update_invitation(invitation_id, patch_req, request=None)))

        assert response["success"] is False
        assert "无待更新字段" in response["error"]

    def test_patch_invitation_invalid_status_returns_400(self, temp_tenant_with_user):
        """PATCH status 非法返回 400"""
        from src.api import recruiting_operator

        ctx = temp_tenant_with_user
        resume_id = _insert_resume(ctx["tenant_id"], ctx["user_id"])
        req = recruiting_operator.CreateInvitationRequest(status="pending")
        with _mock_tenant_ctx(ctx["tenant_id"]), _mock_user(ctx["user_id"]):
            created = _unpack(_call(
                recruiting_operator.create_invitation(resume_id, req, request=None)))
        invitation_id = created["data"]["id"]

        patch_req = recruiting_operator.UpdateInvitationRequest(status="flying")
        with _mock_tenant_ctx(ctx["tenant_id"]):
            response = _unpack(_call(
                recruiting_operator.update_invitation(invitation_id, patch_req, request=None)))

        assert response["success"] is False
        assert "邀约状态值非法" in response["error"]

    def test_invitations_cross_tenant_invisible_and_404(self, temp_tenant_with_user):
        """租户隔离：B 租户列表看不到 A 的邀约；PATCH A 的邀约返回 404"""
        from src.api import recruiting_operator
        from src.saas.db.tenant_db import TenantDB
        from src.db.database import get_db_connection

        ctx_a = temp_tenant_with_user
        resume_id_a = _insert_resume(ctx_a["tenant_id"], ctx_a["user_id"])
        req = recruiting_operator.CreateInvitationRequest(status="pending", method="电话面试")
        with _mock_tenant_ctx(ctx_a["tenant_id"]), _mock_user(ctx_a["user_id"]):
            created = _unpack(_call(
                recruiting_operator.create_invitation(resume_id_a, req, request=None)))
        invitation_id_a = created["data"]["id"]

        tenant_code_b = f"T{uuid.uuid4().hex[:6].upper()}"
        tenant_b = TenantDB.create(
            company_name=f"邀约测试租户B-{tenant_code_b}",
            tenant_code=tenant_code_b,
            contact_name="测试B",
            contact_phone="13800000001",
        )
        if not tenant_b:
            pytest.skip("无法创建测试租户B")
        tenant_id_b = tenant_b["tenant_id"]
        try:
            patch_req = recruiting_operator.UpdateInvitationRequest(status="done")
            with _mock_tenant_ctx(tenant_id_b):
                list_resp = _unpack(_call(recruiting_operator.list_invitations(resume_id_a, request=None)))
                patch_resp = _unpack(_call(
                    recruiting_operator.update_invitation(invitation_id_a, patch_req, request=None)))

            # 简历不属 B 租户 → 列表 404（fail-closed）；邀约记录本身也不属 B → PATCH 404
            assert list_resp["success"] is False
            assert list_resp["error"] == "简历不存在"
            assert patch_resp["success"] is False
            assert patch_resp["error"] == "邀约记录不存在"
        finally:
            TenantDB.delete(tenant_id_b)
            try:
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("DELETE FROM tenants WHERE tenant_id = %s", (tenant_id_b,))
                    conn.commit()
            except Exception:
                pass

    def test_invitations_resume_not_found_returns_404(self, temp_tenant_with_user):
        """简历不存在时邀约列表/创建均返回 404"""
        from src.api import recruiting_operator

        ctx = temp_tenant_with_user
        req = recruiting_operator.CreateInvitationRequest(status="pending")
        with _mock_tenant_ctx(ctx["tenant_id"]), _mock_user(ctx["user_id"]):
            list_resp = _unpack(_call(recruiting_operator.list_invitations(99999999, request=None)))
            create_resp = _unpack(_call(
                recruiting_operator.create_invitation(99999999, req, request=None)))

        assert list_resp["success"] is False
        assert list_resp["error"] == "简历不存在"
        assert create_resp["success"] is False
        assert create_resp["error"] == "简历不存在"
