"""话术发送闭环集成测试：boss_send_to / boss_send_current 的 script_title 话术模式。

- script_title → SCRIPT_NEEDS_FILL（话术原文 + 候选人简历摘录，占位符由 LLM 填）
- message 模式 → 正常透传本机执行（monkeypatch 基类 execute，不碰真设备/DB invocation）
"""
import json
import uuid as uuid_module
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture(scope="module", autouse=True)
def _ensure_tables():
    from src.db.database import get_db_connection
    from src.api.recruiting_operator import init_recruiting_job_tables
    from src.services.recruiting_resume_service import init_recruiting_operator_tables as init_resumes

    with get_db_connection() as conn:
        init_recruiting_job_tables(conn)
        init_resumes(conn)
    yield


@pytest.fixture
def temp_tenant_with_user():
    from src.saas.db.tenant_db import TenantDB
    from src.db.database import get_db_connection

    tenant_code = f"T{uuid_module.uuid4().hex[:6].upper()}"
    tenant = TenantDB.create(
        company_name=f"话术发送测试租户-{tenant_code}",
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
            cur.execute(
                "DELETE FROM bs_recruiting_operator_job_scripts WHERE tenant_id = %s", (tenant_id,)
            )
            cur.execute("DELETE FROM bs_recruiting_operator_jobs WHERE tenant_id = %s", (tenant_id,))
            cur.execute("DELETE FROM bs_recruiting_operator_resumes WHERE tenant_id = %s", (tenant_id,))
            conn.commit()
    except Exception:
        pass
    TenantDB.delete(tenant_id)


def _call(coro):
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _cli_success_result(payload=None):
    return {
        "success": True,
        "code": None,
        "message": "发送执行成功",
        "effect": "applied",
        "data": payload or {},
        "invocation_id": "inv-test-1",
    }


class TestScriptMode:
    def test_need_fill_with_resume_excerpt(self, temp_tenant_with_user):
        """script_title → SCRIPT_NEEDS_FILL：话术原文 + 该候选人简历摘录（占位符交给 LLM）"""
        from src.services import recruiting_job_service, recruiting_resume_service
        from src.local_tools.proxy_tool import BossSendToTool

        ctx = temp_tenant_with_user
        # 触发预置（PHP 职位 + 13 条话术）+ 造一份张三丰简历
        recruiting_job_service.list_jobs(ctx["tenant_id"])
        recruiting_resume_service.create_resume_record(
            ctx["tenant_id"], ctx["user_id"],
            candidate_name="张三丰", job_name="PHP开发工程师（Laravel）",
            ocr_text="八年 Laravel 经验，主导过日活十万级 SaaS。" + "细节。" * 300,
        )
        r = _call(BossSendToTool().execute(
            _trusted_tenant_id=ctx["tenant_id"], _trusted_user_id=ctx["user_id"],
            to="张三丰", script_title="开场·技术栈匹配",
        ))
        assert r["success"] is False
        assert r["code"] == "SCRIPT_NEEDS_FILL"
        assert r["script"]["job_name"] == "PHP开发工程师（Laravel）"
        assert r["script"]["category"] == "初次开场"
        assert "Laravel" in r["script"]["content"]
        assert r["resume_excerpt"].startswith("八年 Laravel 经验")
        assert len(r["resume_excerpt"]) <= 600  # 截断保护上下文

    def test_title_contains_match(self, temp_tenant_with_user):
        """标题包含匹配：只给「AI 编程工具」能命中「摸底·AI 编程工具（重点）」"""
        from src.services import recruiting_job_service
        from src.local_tools.proxy_tool import BossSendToTool

        ctx = temp_tenant_with_user
        recruiting_job_service.list_jobs(ctx["tenant_id"])
        r = _call(BossSendToTool().execute(
            _trusted_tenant_id=ctx["tenant_id"], _trusted_user_id=ctx["user_id"],
            to="李四", script_title="AI 编程工具",
        ))
        assert r["code"] == "SCRIPT_NEEDS_FILL"
        assert r["script"]["category"] == "了解摸底"
        # 简历库无「李四」→ 给 resume_hint 引导
        assert "resume_excerpt" not in r
        assert "李四" in r["resume_hint"]

    def test_script_not_found_lists_titles(self, temp_tenant_with_user):
        from src.services import recruiting_job_service
        from src.local_tools.proxy_tool import BossSendToTool

        ctx = temp_tenant_with_user
        recruiting_job_service.list_jobs(ctx["tenant_id"])
        r = _call(BossSendToTool().execute(
            _trusted_tenant_id=ctx["tenant_id"], _trusted_user_id=ctx["user_id"],
            to="王五", script_title="不存在的开场",
        ))
        assert r["success"] is False
        assert r["code"] == "NOT_FOUND"
        assert "开场·技术栈匹配" in r["message"]

    def test_script_and_message_exclusive(self, temp_tenant_with_user):
        from src.local_tools.proxy_tool import BossSendToTool

        r = _call(BossSendToTool().execute(
            _trusted_tenant_id="t1", _trusted_user_id="u1",
            to="张三", script_title="开场·技术栈匹配", message="你好",
        ))
        assert r["code"] == "INVALID_ARGS"
        assert "二选一" in r["message"]

    def test_no_identity(self):
        from src.local_tools.proxy_tool import BossSendToTool

        r = _call(BossSendToTool().execute(to="张三", script_title="开场·技术栈匹配"))
        assert r["code"] == "NO_IDENTITY"


class TestMessageModePassthrough:
    def test_send_to_message_passthrough(self, temp_tenant_with_user):
        """message 模式 → 走基类正常下发本机执行（打桩验证透传）"""
        from src.local_tools.proxy_tool import BossSendToTool, LocalToolProxyTool

        ctx = temp_tenant_with_user
        fake = _cli_success_result({"to": "张三", "sent": True})
        with patch.object(LocalToolProxyTool, "execute", new=AsyncMock(return_value=fake)) as m:
            r = _call(BossSendToTool().execute(
                _trusted_tenant_id=ctx["tenant_id"], _trusted_user_id=ctx["user_id"],
                to="张三", message="您好，我们是 Laravel 团队",
            ))
        assert r["success"] is True
        # 透传给基类的参数：业务参数原样（message/to/dry_run），_trusted 保留
        called = m.call_args.kwargs
        assert called["to"] == "张三"
        assert called["message"] == "您好，我们是 Laravel 团队"

    def test_send_current_need_fill(self, temp_tenant_with_user):
        from src.services import recruiting_job_service
        from src.local_tools.proxy_tool import BossSendCurrentTool

        ctx = temp_tenant_with_user
        recruiting_job_service.list_jobs(ctx["tenant_id"])
        r = _call(BossSendCurrentTool().execute(
            _trusted_tenant_id=ctx["tenant_id"], _trusted_user_id=ctx["user_id"],
            script_title="邀约·面试安排",
        ))
        assert r["code"] == "SCRIPT_NEEDS_FILL"
        assert r["script"]["category"] == "邀约推进"
        assert "resume_excerpt" not in r  # send_current 无候选人姓名，不带摘录

    def test_missing_message(self, temp_tenant_with_user):
        from src.local_tools.proxy_tool import BossSendCurrentTool

        r = _call(BossSendCurrentTool().execute(
            _trusted_tenant_id="t1", _trusted_user_id="u1",
        ))
        assert r["code"] == "INVALID_ARGS"
        assert "message" in r["message"]
