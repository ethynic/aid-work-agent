"""boss_send_to 发送成功后自动回写沟通记录集成测试（2026-09-01）。

- 真发送（sent=true 且非 dry_run）+ 简历库有同名简历 → comm_logs 新增 1 行（direction=out / channel=boss / content=message 全文 / user_id=受信用户）
- 无同名简历 / dry_run / sent=false → 不回写；回写失败 → 发送结果 success 不受影响
- 话术模式（script_title）与缺 message（INVALID_ARGS）→ 不触达设备基类、不触发回写钩子
- monkeypatch 基类 execute 模拟设备成功结果，不碰真设备/DB invocation
"""
import uuid as uuid_module
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture(scope="module", autouse=True)
def _ensure_tables():
    from src.db.database import get_db_connection
    from src.api.recruiting_operator import init_recruiting_job_tables
    from src.services.recruiting_resume_service import init_recruiting_operator_tables as init_resumes
    from src.services.recruiting_resume_timeline_service import init_recruiting_timeline_tables

    # 顺序必须 jobs → resumes → timeline（comm_logs 外键引用简历表）
    with get_db_connection() as conn:
        init_recruiting_job_tables(conn)
        init_resumes(conn)
        init_recruiting_timeline_tables(conn)
        conn.commit()  # 连接上下文正常退出会 rollback，DDL 迁移必须显式提交
    yield


@pytest.fixture
def temp_tenant_with_user():
    from src.saas.db.tenant_db import TenantDB
    from src.db.database import get_db_connection

    tenant_code = f"T{uuid_module.uuid4().hex[:6].upper()}"
    tenant = TenantDB.create(
        company_name=f"发送回写测试租户-{tenant_code}",
        tenant_code=tenant_code,
        contact_name="测试联系人",
        contact_phone="13800000000",
    )
    if not tenant:
        pytest.skip("无法创建测试租户（DB 不可用）")
    tenant_id = tenant["tenant_id"]
    yield {"tenant_id": tenant_id, "user_id": f"u_{uuid_module.uuid4().hex[:8]}"}

    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            # comm_logs/invitations 随简历 ON DELETE CASCADE 级联删除，无需单独清理
            cur.execute("DELETE FROM bs_recruiting_operator_resumes WHERE tenant_id = %s", (tenant_id,))
            cur.execute("DELETE FROM bs_recruiting_operator_jobs WHERE tenant_id = %s", (tenant_id,))
            conn.commit()
    except Exception:
        pass
    TenantDB.delete(tenant_id)
    # TenantDB.delete 仅是软删除（status=deactivated），需追加物理删除避免测试租户堆积
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM tenants WHERE tenant_id = %s", (tenant_id,))
            conn.commit()
    except Exception:
        pass


def _call(coro):
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _device_success_payload(**overrides):
    """boss_send_to 设备端成功结果契约（bossSendTo.ts）：{to, via, sent, dry_run}"""
    payload = {"to": "张三丰", "via": "already", "sent": True, "dry_run": False}
    payload.update(overrides)
    return {
        "success": True,
        "code": None,
        "message": "发送执行成功",
        "effect": "applied",
        "data": payload,
        "invocation_id": "inv-test-wb",
    }


def _send_to(ctx, to, message, device_payload):
    """打桩基类 execute（模拟设备终态）后调用 BossSendToTool message 模式"""
    from src.local_tools.proxy_tool import BossSendToTool, LocalToolProxyTool

    with patch.object(LocalToolProxyTool, "execute", new=AsyncMock(return_value=device_payload)):
        return _call(BossSendToTool().execute(
            _trusted_tenant_id=ctx["tenant_id"], _trusted_user_id=ctx["user_id"],
            to=to, message=message,
        ))


def _comm_log_count(tenant_id) -> int:
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) AS cnt FROM bs_recruiting_operator_resume_comm_logs WHERE tenant_id = %s",
            (tenant_id,),
        )
        return cur.fetchone()["cnt"]


class TestSendToWriteback:
    def test_writeback_creates_comm_log(self, temp_tenant_with_user):
        """真发送 + 简历库有同名简历 → 回写 1 条沟通记录（direction/channel/content/user_id 断言）"""
        from src.services import recruiting_resume_service
        from src.services import recruiting_resume_timeline_service

        ctx = temp_tenant_with_user
        record = recruiting_resume_service.create_resume_record(
            ctx["tenant_id"], ctx["user_id"],
            candidate_name="张三丰", job_name="PHP开发工程师（Laravel）",
            ocr_text="八年 Laravel 经验。",
        )
        msg = "您好！我们团队主力技术栈是 PHP 8 + Laravel，方便聊聊吗？"
        r = _send_to(ctx, "张三丰", msg, _device_success_payload(to="张三丰"))
        assert r["success"] is True

        logs = recruiting_resume_timeline_service.list_comm_logs(ctx["tenant_id"], record["id"])
        assert len(logs) == 1
        log = logs[0]
        assert log["direction"] == "out"
        assert log["channel"] == "boss"
        assert log["content"] == msg
        assert log["user_id"] == ctx["user_id"]

    def test_writeback_skipped_when_no_resume(self, temp_tenant_with_user):
        """简历库无该姓名 → 静默跳过（不新增行），发送结果 success 不受影响"""
        ctx = temp_tenant_with_user
        r = _send_to(ctx, "李四", "您好，聊聊？", _device_success_payload(to="李四"))
        assert r["success"] is True
        assert _comm_log_count(ctx["tenant_id"]) == 0

    def test_writeback_skipped_on_dry_run(self, temp_tenant_with_user):
        """dry_run=true（只输入不发送）→ 不回写"""
        from src.services import recruiting_resume_service

        ctx = temp_tenant_with_user
        recruiting_resume_service.create_resume_record(
            ctx["tenant_id"], ctx["user_id"],
            candidate_name="张三丰", job_name="PHP开发工程师（Laravel）",
        )
        r = _send_to(ctx, "张三丰", "您好", _device_success_payload(sent=True, dry_run=True))
        assert r["success"] is True
        assert _comm_log_count(ctx["tenant_id"]) == 0

    def test_writeback_skipped_when_not_sent(self, temp_tenant_with_user):
        """sent=false（设备未真发送）→ 不回写"""
        from src.services import recruiting_resume_service

        ctx = temp_tenant_with_user
        recruiting_resume_service.create_resume_record(
            ctx["tenant_id"], ctx["user_id"],
            candidate_name="张三丰", job_name="PHP开发工程师（Laravel）",
        )
        r = _send_to(ctx, "张三丰", "您好", _device_success_payload(sent=False))
        assert r["success"] is True
        assert _comm_log_count(ctx["tenant_id"]) == 0

    def test_writeback_failure_never_breaks_send_result(self, temp_tenant_with_user):
        """回写 DB 异常只留日志：发送结果原样返回（success/data 不变），不留半截数据"""
        from src.services import recruiting_resume_service
        from src.services import recruiting_resume_timeline_service

        ctx = temp_tenant_with_user
        recruiting_resume_service.create_resume_record(
            ctx["tenant_id"], ctx["user_id"],
            candidate_name="张三丰", job_name="PHP开发工程师（Laravel）",
        )
        payload = _device_success_payload(to="张三丰")
        with patch.object(
            recruiting_resume_timeline_service, "create_comm_log",
            side_effect=RuntimeError("db down"),
        ):
            r = _send_to(ctx, "张三丰", "您好", payload)
        assert r["success"] is True
        assert r["data"] == payload["data"]  # 结果契约不变（data 不加键）
        assert _comm_log_count(ctx["tenant_id"]) == 0

    def test_writeback_duplicate_names_takes_latest(self, temp_tenant_with_user):
        """同名多份简历 → 回写到最新一份（list_resumes created_at DESC + 精确名优先）"""
        from src.services import recruiting_resume_service
        from src.services import recruiting_resume_timeline_service

        ctx = temp_tenant_with_user
        recruiting_resume_service.create_resume_record(
            ctx["tenant_id"], ctx["user_id"], candidate_name="王重八",
        )
        latest = recruiting_resume_service.create_resume_record(
            ctx["tenant_id"], ctx["user_id"], candidate_name="王重八",
        )
        r = _send_to(ctx, "王重八", "您好", _device_success_payload(to="王重八"))
        assert r["success"] is True
        assert _comm_log_count(ctx["tenant_id"]) == 1
        logs = recruiting_resume_timeline_service.list_comm_logs(ctx["tenant_id"], latest["id"])
        assert len(logs) == 1  # 旧的「王重八」简历不写

    def test_script_mode_never_hits_device_or_writeback(self, temp_tenant_with_user):
        """话术模式（script_title → SCRIPT_NEEDS_FILL）：不触达设备基类、不触发回写钩子"""
        from src.local_tools.proxy_tool import BossSendToTool, LocalToolProxyTool
        from src.services import recruiting_job_service
        from src.services import recruiting_resume_service

        ctx = temp_tenant_with_user
        recruiting_resume_service.create_resume_record(
            ctx["tenant_id"], ctx["user_id"], candidate_name="张三丰",
        )
        job = recruiting_job_service.create_job(ctx["tenant_id"], job_name="PHP开发工程师（Laravel）")
        recruiting_job_service.create_script(
            ctx["tenant_id"], job["id"], category="初次开场", title="开场·Laravel",
            content="您好，我们主栈 Laravel，{{年限}} 年经验方便聊聊吗？",
        )

        device_execute = AsyncMock(return_value=_device_success_payload())
        with patch.object(LocalToolProxyTool, "execute", new=device_execute):
            r = _call(BossSendToTool().execute(
                _trusted_tenant_id=ctx["tenant_id"], _trusted_user_id=ctx["user_id"],
                to="张三丰", script_title="开场·Laravel",
            ))
        # 话术模式只返回话术待填结果，绝不触达设备，也绝不回写
        assert r["success"] is False
        assert r["code"] == "SCRIPT_NEEDS_FILL"
        device_execute.assert_not_awaited()
        assert _comm_log_count(ctx["tenant_id"]) == 0

    def test_missing_message_never_hits_device_or_writeback(self, temp_tenant_with_user):
        """缺 message（无 script_title → INVALID_ARGS）：不触达设备基类、不触发回写钩子"""
        from src.local_tools.proxy_tool import BossSendToTool, LocalToolProxyTool

        ctx = temp_tenant_with_user
        device_execute = AsyncMock(return_value=_device_success_payload())
        with patch.object(LocalToolProxyTool, "execute", new=device_execute):
            r = _call(BossSendToTool().execute(
                _trusted_tenant_id=ctx["tenant_id"], _trusted_user_id=ctx["user_id"],
                to="张三丰",
            ))
        assert r["success"] is False
        assert r["code"] == "INVALID_ARGS"
        device_execute.assert_not_awaited()
        assert _comm_log_count(ctx["tenant_id"]) == 0
