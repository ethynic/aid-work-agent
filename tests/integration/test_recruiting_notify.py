"""面试邀约企微通知集成测试（Phase 1，真实 PG + 临时租户）

覆盖（设计 docs/design/recruiting/recruiting-interview-notify-design.md）：
- settings：webhook Fernet 加密往返 / 掩码输出 / 掩码入参保留旧值 / upsert 幂等 / None 清除
- push_interview_notify：未启用不调网络、pre/done 两模板内容、done+at_mobiles 补 text、
  发送失败写 failed 日志不外抛、事前知会独立开关
- 工具 execute：pre/done 返回结构、NO_IDENTITY、未启用文案（不阻塞邀约，success=True）
- API：GET 默认结构 / PUT 掩码保留 / resend 补推改状态、404、未启用 400

测试策略（镜像 test_recruiting_job_apis.py）：真实 PostgreSQL + 临时租户（teardown 物理删除），
mock get_current_tenant_id；发送动作用 monkeypatch 替身（不打真实企微接口）。
"""
import os
import uuid as uuid_module
from unittest.mock import AsyncMock, patch

# 必须在导入服务前设置主密钥（Fernet 加解密用，沿 wecom_personal_rpa 测试范式）
os.environ.setdefault("RPA_SECRET_KEY", "test-recruiting-notify-key-32-bytes+")

import pytest

pytestmark = pytest.mark.integration

WEBHOOK_PLAIN = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abcd1234efgh5678"


@pytest.fixture(scope="module", autouse=True)
def _ensure_tables():
    """模块级幂等建表（测试库可能未跑过服务启动初始化）"""
    from src.db.database import get_db_connection
    from src.services.recruiting_job_service import init_recruiting_job_tables
    from src.api.recruiting_operator import init_recruiting_operator_tables
    from src.services.recruiting_notify_service import init_recruiting_notify_tables

    with get_db_connection() as conn:
        # notify_logs.resume_id FK 引用简历表（2026-09-01 联动加列），必须先建 jobs → resumes
        init_recruiting_job_tables(conn)
        init_recruiting_operator_tables(conn)
        init_recruiting_notify_tables(conn)
        conn.commit()


@pytest.fixture
def temp_tenant():
    """临时租户，teardown 物理删除（含通知两表数据）"""
    from src.saas.db.tenant_db import TenantDB
    from src.db.database import get_db_connection

    tenant_code = f"T{uuid_module.uuid4().hex[:6].upper()}"
    tenant = TenantDB.create(
        company_name=f"面试通知测试租户-{tenant_code}",
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
            cur = conn.cursor()
            cur.execute("DELETE FROM bs_recruiting_notify_logs WHERE tenant_id = %s", (tenant_id,))
            cur.execute("DELETE FROM bs_recruiting_notify_settings WHERE tenant_id = %s", (tenant_id,))
            # 简历/职位联动测试可能建了简历（notify_logs.resume_id FK 随删置 NULL，先清日志已兜底）
            cur.execute("DELETE FROM bs_recruiting_operator_resumes WHERE tenant_id = %s", (tenant_id,))
            cur.execute("DELETE FROM bs_recruiting_operator_jobs WHERE tenant_id = %s", (tenant_id,))
            conn.commit()
    except Exception:
        pass
    TenantDB.delete(tenant_id)
    # TenantDB.delete 仅软删除，追加物理删除避免测试租户堆积
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM tenants WHERE tenant_id = %s", (tenant_id,))
            conn.commit()
    except Exception:
        pass


def _call(coro):
    """同步驱动 async 协程（独立 event loop，跑完即关）"""
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _unpack(response):
    """把 JSONResponse 解包为 dict；普通 dict 直接返回"""
    import json
    from fastapi.responses import JSONResponse

    if isinstance(response, JSONResponse):
        return json.loads(response.body)
    return response


def _mock_tenant_ctx(tenant_id: str):
    return patch("src.api.recruiting_operator.get_current_tenant_id", return_value=tenant_id)


def _get_log_row(tenant_id: str, log_id: int):
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM bs_recruiting_notify_logs WHERE id = %s AND tenant_id = %s",
            (log_id, tenant_id),
        )
        return cursor.fetchone()


CANDIDATES_PRE = [
    {"name": "陈远健", "score": 90, "highlight": "5年电商后端 Yii2/MySQL/Redis", "time": "8月20日（周三）15:00"},
    {"name": "常晓飞", "score": 88, "highlight": "7年 PHP 开发经验", "time": "8月21日（周四）10:00"},
]
CANDIDATES_DONE = [
    {"name": "陈远健", "time": "8月20日（周三）15:00"},
    {"name": "常晓飞", "time": "8月21日（周四）10:00"},
]


class TestNotifySettings:
    def test_webhook_encrypted_roundtrip(self, temp_tenant):
        """存后读明文=原文；库里是 Fernet 密文（gAAAAA 开头且不含明文）"""
        from src.services import recruiting_notify_service as svc

        svc.upsert_settings(temp_tenant, webhook_url=WEBHOOK_PLAIN, enabled=True)
        settings = svc.get_settings(temp_tenant)
        assert settings["webhook_url"] == WEBHOOK_PLAIN
        assert settings["enabled"] is True

        from src.db.database import get_db_connection
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT webhook_url FROM bs_recruiting_notify_settings WHERE tenant_id = %s",
                (temp_tenant,),
            )
            stored = cursor.fetchone()["webhook_url"]
        assert stored.startswith("gAAAAA")
        assert WEBHOOK_PLAIN not in stored

    def test_masked_output(self, temp_tenant):
        from src.services import recruiting_notify_service as svc

        svc.upsert_settings(temp_tenant, webhook_url=WEBHOOK_PLAIN, enabled=True, at_mobiles=["13800000001"])
        masked = svc.get_settings_masked(temp_tenant)
        assert masked["webhook_url"] == f"***{WEBHOOK_PLAIN[-4:]}"
        assert masked["enabled"] is True
        assert masked["at_mobiles"] == ["13800000001"]
        assert masked["pre_notify_enabled"] is True

    def test_masked_default_when_absent(self, temp_tenant):
        from src.services import recruiting_notify_service as svc

        assert svc.get_settings(temp_tenant) is None
        masked = svc.get_settings_masked(temp_tenant)
        assert masked == {
            "tenant_id": temp_tenant,
            "enabled": False,
            "webhook_url": "",
            "at_mobiles": [],
            "pre_notify_enabled": True,
        }

    def test_upsert_idempotent_single_row(self, temp_tenant):
        from src.services import recruiting_notify_service as svc
        from src.db.database import get_db_connection

        svc.upsert_settings(temp_tenant, webhook_url=WEBHOOK_PLAIN, enabled=True)
        svc.upsert_settings(temp_tenant, enabled=False, at_mobiles=["13800000001"])
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) AS cnt FROM bs_recruiting_notify_settings WHERE tenant_id = %s",
                (temp_tenant,),
            )
            assert cursor.fetchone()["cnt"] == 1
        # 未传 webhook 保留旧值，其余字段更新
        settings = svc.get_settings(temp_tenant)
        assert settings["webhook_url"] == WEBHOOK_PLAIN
        assert settings["enabled"] is False
        assert settings["at_mobiles"] == ["13800000001"]

    def test_masked_or_empty_input_keeps_old_webhook(self, temp_tenant):
        from src.services import recruiting_notify_service as svc

        svc.upsert_settings(temp_tenant, webhook_url=WEBHOOK_PLAIN)
        svc.upsert_settings(temp_tenant, webhook_url="***5678")  # 掩码回传
        assert svc.get_settings(temp_tenant)["webhook_url"] == WEBHOOK_PLAIN
        svc.upsert_settings(temp_tenant, webhook_url="")  # 空串回传
        assert svc.get_settings(temp_tenant)["webhook_url"] == WEBHOOK_PLAIN

    def test_webhook_none_clears(self, temp_tenant):
        from src.services import recruiting_notify_service as svc

        svc.upsert_settings(temp_tenant, webhook_url=WEBHOOK_PLAIN)
        svc.upsert_settings(temp_tenant, webhook_url=None)
        assert svc.get_settings(temp_tenant)["webhook_url"] is None
        assert svc.get_settings_masked(temp_tenant)["webhook_url"] == ""


class TestPushInterviewNotify:
    async def test_not_enabled_skips_network(self, temp_tenant, monkeypatch):
        """未配置/未 enabled：pushed=False 且不调网络（send_markdown 未被 await）"""
        from src.services import recruiting_notify_service as svc

        send_markdown = AsyncMock(return_value=(True, None))
        monkeypatch.setattr(svc.wecom_bot, "send_markdown", send_markdown)
        result = await svc.push_interview_notify(
            temp_tenant, "pre", "PHP开发工程师", CANDIDATES_PRE
        )
        assert result["pushed"] is False
        assert result["reason"] == "未启用"
        send_markdown.assert_not_awaited()

        # 配了但 enabled=False 同样静默
        svc.upsert_settings(temp_tenant, webhook_url=WEBHOOK_PLAIN, enabled=False)
        result = await svc.push_interview_notify(
            temp_tenant, "done", "PHP开发工程师", CANDIDATES_DONE
        )
        assert result["pushed"] is False and result["reason"] == "未启用"
        send_markdown.assert_not_awaited()

    async def test_pre_template_content_and_log(self, temp_tenant, monkeypatch):
        from src.services import recruiting_notify_service as svc

        svc.upsert_settings(temp_tenant, webhook_url=WEBHOOK_PLAIN, enabled=True)
        send_markdown = AsyncMock(return_value=(True, None))
        send_text = AsyncMock(return_value=(True, None))
        monkeypatch.setattr(svc.wecom_bot, "send_markdown", send_markdown)
        monkeypatch.setattr(svc.wecom_bot, "send_text", send_text)

        result = await svc.push_interview_notify(
            temp_tenant, "pre", "PHP开发工程师", CANDIDATES_PRE, note="技术面 1 小时"
        )
        assert result["pushed"] is True and result["log_id"]
        send_markdown.assert_awaited_once()
        send_text.assert_not_awaited()  # 事前知会不 @

        content = send_markdown.await_args.args[1]
        assert content.startswith("【面试邀约知会】PHP开发工程师")
        assert "> 陈远健 · 90分 · 5年电商后端 Yii2/MySQL/Redis" in content
        assert "> 常晓飞 · 88分 · 7年 PHP 开发经验" in content
        assert "拟安排时间：8月20日（周三）15:00 / 8月21日（周四）10:00" in content
        assert "备注：技术面 1 小时" in content
        assert "如对邀约有异议请尽快联系；无异议将按计划发出邀约" in content

        row = _get_log_row(temp_tenant, result["log_id"])
        assert row["kind"] == "pre"
        assert row["status"] == "sent"
        assert row["error"] is None
        assert row["candidates"][0]["name"] == "陈远健"

    async def test_done_template_with_at_mobiles_text(self, temp_tenant, monkeypatch):
        """done：通报模板 + at_mobiles 非空补 text（不 @ 的 markdown 在前）"""
        from src.services import recruiting_notify_service as svc

        svc.upsert_settings(
            temp_tenant, webhook_url=WEBHOOK_PLAIN, enabled=True, at_mobiles=["13800000001", "13900000002"]
        )
        send_markdown = AsyncMock(return_value=(True, None))
        send_text = AsyncMock(return_value=(True, None))
        monkeypatch.setattr(svc.wecom_bot, "send_markdown", send_markdown)
        monkeypatch.setattr(svc.wecom_bot, "send_text", send_text)

        result = await svc.push_interview_notify(
            temp_tenant, "done", "PHP开发工程师", CANDIDATES_DONE
        )
        assert result["pushed"] is True

        content = send_markdown.await_args.args[1]
        assert content.startswith("【面试邀约已完成】PHP开发工程师")
        assert "已向以下候选人发出面试邀约" in content
        assert "> 陈远健 · 8月20日（周三）15:00" in content
        assert "> 常晓飞 · 8月21日（周四）10:00" in content
        assert "请面试官及相关同事留意日程" in content

        send_text.assert_awaited_once()
        args = send_text.await_args.args
        assert args[0] == WEBHOOK_PLAIN
        assert args[1] == "面试邀约已完成，请查看上条详情"
        assert args[2] == ["13800000001", "13900000002"]
        row = _get_log_row(temp_tenant, result["log_id"])
        assert row["status"] == "sent"

    async def test_send_failure_logs_failed_no_raise(self, temp_tenant, monkeypatch):
        from src.services import recruiting_notify_service as svc

        svc.upsert_settings(temp_tenant, webhook_url=WEBHOOK_PLAIN, enabled=True)
        monkeypatch.setattr(
            svc.wecom_bot, "send_markdown",
            AsyncMock(return_value=(False, "企微接口错误 errcode=93000")),
        )
        result = await svc.push_interview_notify(
            temp_tenant, "pre", "PHP开发工程师", CANDIDATES_PRE
        )
        assert result["pushed"] is False
        assert "93000" in result["error"]
        row = _get_log_row(temp_tenant, result["log_id"])
        assert row["status"] == "failed"
        assert "93000" in row["error"]

    async def test_pre_notify_disabled_switch(self, temp_tenant, monkeypatch):
        """pre_notify_enabled=False：事前知会静默跳过，事后通报不受影响"""
        from src.services import recruiting_notify_service as svc

        svc.upsert_settings(temp_tenant, webhook_url=WEBHOOK_PLAIN, enabled=True, pre_notify_enabled=False)
        send_markdown = AsyncMock(return_value=(True, None))
        monkeypatch.setattr(svc.wecom_bot, "send_markdown", send_markdown)
        result = await svc.push_interview_notify(
            temp_tenant, "pre", "PHP开发工程师", CANDIDATES_PRE
        )
        assert result["pushed"] is False and result["reason"] == "事前知会未启用"
        send_markdown.assert_not_awaited()


class TestNotifyResumeLink:
    """通知留痕与简历联动（2026-09-01）：resume_id 列写入 + 按简历查询"""

    def _insert_resume(self, tenant_id: str, candidate_name: str) -> int:
        from src.services import recruiting_resume_service

        record = recruiting_resume_service.create_resume_record(
            tenant_id, "u_notify_link", candidate_name=candidate_name,
        )
        return record["id"]

    async def test_single_candidate_push_persists_resume_id(self, temp_tenant, monkeypatch):
        """单候选人推送 + resume_id → 留痕行落 resume_id（FK 关联真实简历）"""
        from src.services import recruiting_notify_service as svc

        resume_id = self._insert_resume(temp_tenant, "陈远健")
        svc.upsert_settings(temp_tenant, webhook_url=WEBHOOK_PLAIN, enabled=True)
        monkeypatch.setattr(svc.wecom_bot, "send_markdown", AsyncMock(return_value=(True, None)))

        result = await svc.push_interview_notify(
            temp_tenant, "done", "PHP开发工程师",
            [{"name": "陈远健", "time": "8月20日（周三）15:00"}],
            resume_id=resume_id,
        )
        assert result["pushed"] is True
        row = _get_log_row(temp_tenant, result["log_id"])
        assert row["resume_id"] == resume_id

    async def test_multi_candidate_push_defaults_resume_id_null(self, temp_tenant, monkeypatch):
        """不传 resume_id（多候选人推送语义）→ 留痕行 resume_id 为 NULL"""
        from src.services import recruiting_notify_service as svc

        svc.upsert_settings(temp_tenant, webhook_url=WEBHOOK_PLAIN, enabled=True)
        monkeypatch.setattr(svc.wecom_bot, "send_markdown", AsyncMock(return_value=(True, None)))

        result = await svc.push_interview_notify(
            temp_tenant, "pre", "PHP开发工程师", CANDIDATES_PRE
        )
        assert result["pushed"] is True
        row = _get_log_row(temp_tenant, result["log_id"])
        assert row["resume_id"] is None

    async def test_list_notify_logs_by_resume(self, temp_tenant, monkeypatch):
        """按简历查询留痕：只返回关联该简历的日志（created_at DESC, id DESC），跨简历/跨租户不可见"""
        from src.services import recruiting_notify_service as svc

        resume_id = self._insert_resume(temp_tenant, "陈远健")
        svc.upsert_settings(temp_tenant, webhook_url=WEBHOOK_PLAIN, enabled=True)
        send_markdown = AsyncMock(return_value=(True, None))
        monkeypatch.setattr(svc.wecom_bot, "send_markdown", send_markdown)

        pre = await svc.push_interview_notify(
            temp_tenant, "pre", "PHP开发工程师", [{"name": "陈远健", "score": 90}], resume_id=resume_id
        )
        done = await svc.push_interview_notify(
            temp_tenant, "done", "PHP开发工程师", [{"name": "陈远健", "time": "8月20日"}], resume_id=resume_id
        )
        # 同租户但不关联该简历的推送 → 不出现在结果里
        other = await svc.push_interview_notify(
            temp_tenant, "done", "PHP开发工程师", CANDIDATES_DONE
        )
        assert pre["pushed"] and done["pushed"] and other["pushed"]

        items = svc.list_notify_logs_by_resume(temp_tenant, resume_id)
        assert [it["id"] for it in items] == [done["log_id"], pre["log_id"]]  # 最新在前
        assert items[0]["kind"] == "done" and items[1]["kind"] == "pre"
        # 轻量投影：只取六列
        assert set(items[0].keys()) == {"id", "kind", "status", "content", "error", "created_at"}

        # 租户隔离：他租户按同一 resume_id 查询 → 空列表
        other_tenant_id = f"T{uuid_module.uuid4().hex[:6].upper()}X"
        assert svc.list_notify_logs_by_resume(other_tenant_id, resume_id) == []

    async def test_tool_single_candidate_resolves_resume_id(self, temp_tenant, monkeypatch):
        """工具 execute：candidates 恰 1 人 → 解析姓名为 resume_id 传入 push（简历库有此人）"""
        from src.local_tools import proxy_tool
        from src.services import recruiting_resume_service

        record = recruiting_resume_service.create_resume_record(
            temp_tenant, "u1", candidate_name="常晓飞",
        )
        push = AsyncMock(return_value={"pushed": True, "log_id": 9})
        monkeypatch.setattr(proxy_tool.recruiting_notify_service, "push_interview_notify", push)
        result = await proxy_tool.BossInterviewNotifyTool().execute(
            _trusted_tenant_id=temp_tenant, kind="done", job_name="PHP开发工程师",
            candidates=[{"name": "常晓飞", "time": "8月21日"}],
        )
        assert result["success"] is True
        push.assert_awaited_once()
        assert push.await_args.kwargs["resume_id"] == record["id"]

    async def test_tool_multi_candidate_passes_resume_id_none(self, temp_tenant, monkeypatch):
        """工具 execute：多候选人 → 不解析，push 收到 resume_id=None（列语义：单候选人才关联）"""
        from src.local_tools import proxy_tool

        push = AsyncMock(return_value={"pushed": True, "log_id": 10})
        monkeypatch.setattr(proxy_tool.recruiting_notify_service, "push_interview_notify", push)
        await proxy_tool.BossInterviewNotifyTool().execute(
            _trusted_tenant_id=temp_tenant, kind="pre", job_name="PHP开发工程师",
            candidates=CANDIDATES_PRE,
        )
        assert push.await_args.kwargs["resume_id"] is None

    async def test_tool_resolve_failure_not_blocking(self, temp_tenant, monkeypatch):
        """工具 execute：简历解析抛异常 → resume_id=None 且推送照常（不阻塞邀约）"""
        from src.local_tools import proxy_tool

        def _boom(*_args, **_kwargs):
            raise RuntimeError("db down")

        monkeypatch.setattr(proxy_tool, "_find_resume_id_by_name", _boom)
        push = AsyncMock(return_value={"pushed": True, "log_id": 11})
        monkeypatch.setattr(proxy_tool.recruiting_notify_service, "push_interview_notify", push)
        result = await proxy_tool.BossInterviewNotifyTool().execute(
            _trusted_tenant_id=temp_tenant, kind="pre", job_name="PHP开发工程师",
            candidates=[{"name": "陈远健"}],
        )
        assert result["success"] is True
        push.assert_awaited_once()
        assert push.await_args.kwargs["resume_id"] is None

    async def test_tool_no_resume_in_library_passes_none(self, temp_tenant, monkeypatch):
        """工具 execute：简历库无该姓名 → resume_id=None，推送照常"""
        from src.local_tools import proxy_tool

        push = AsyncMock(return_value={"pushed": True, "log_id": 12})
        monkeypatch.setattr(proxy_tool.recruiting_notify_service, "push_interview_notify", push)
        result = await proxy_tool.BossInterviewNotifyTool().execute(
            _trusted_tenant_id=temp_tenant, kind="pre", job_name="PHP开发工程师",
            candidates=[{"name": "查无此人"}],
        )
        assert result["success"] is True
        assert push.await_args.kwargs["resume_id"] is None


class TestNotifyToolExecute:
    async def test_no_identity(self):
        from src.local_tools.proxy_tool import BossInterviewNotifyTool

        result = await BossInterviewNotifyTool().execute()
        assert result["success"] is False
        assert result["code"] == "NO_IDENTITY"

    async def test_pre_structure(self, temp_tenant, monkeypatch):
        from src.local_tools import proxy_tool

        push = AsyncMock(return_value={"pushed": True, "log_id": 7})
        monkeypatch.setattr(proxy_tool.recruiting_notify_service, "push_interview_notify", push)
        result = await proxy_tool.BossInterviewNotifyTool().execute(
            _trusted_tenant_id=temp_tenant, _trusted_user_id="u1",
            kind="pre", job_name="PHP开发工程师", candidates=CANDIDATES_PRE, note="备注",
        )
        assert result["success"] is True
        assert result["message"] == "事前知会已发送至企微群"
        assert result["data"] == {"pushed": True, "log_id": 7}
        push.assert_awaited_once()
        assert push.await_args.kwargs["kind"] == "pre"

    async def test_done_structure(self, temp_tenant, monkeypatch):
        from src.local_tools import proxy_tool

        push = AsyncMock(return_value={"pushed": True, "log_id": 8})
        monkeypatch.setattr(proxy_tool.recruiting_notify_service, "push_interview_notify", push)
        result = await proxy_tool.BossInterviewNotifyTool().execute(
            _trusted_tenant_id=temp_tenant, kind="done",
            job_name="PHP开发工程师", candidates=CANDIDATES_DONE,
        )
        assert result["success"] is True
        assert result["message"] == "面试邀约通报已发送至企微群"
        assert result["data"]["log_id"] == 8

    async def test_not_enabled_message_not_blocking(self, temp_tenant):
        """未配置租户直调：success=True + 未启用文案（通知失败不阻塞邀约）"""
        from src.local_tools.proxy_tool import BossInterviewNotifyTool

        result = await BossInterviewNotifyTool().execute(
            _trusted_tenant_id=temp_tenant, kind="pre",
            job_name="PHP开发工程师", candidates=CANDIDATES_PRE,
        )
        assert result["success"] is True
        assert "企微通知未启用" in result["message"]
        assert result["data"]["pushed"] is False

    async def test_push_failure_message_not_blocking(self, temp_tenant, monkeypatch):
        from src.local_tools import proxy_tool

        monkeypatch.setattr(
            proxy_tool.recruiting_notify_service, "push_interview_notify",
            AsyncMock(return_value={"pushed": False, "error": "企微接口错误 errcode=93000"}),
        )
        result = await proxy_tool.BossInterviewNotifyTool().execute(
            _trusted_tenant_id=temp_tenant, kind="done",
            job_name="PHP开发工程师", candidates=CANDIDATES_DONE,
        )
        assert result["success"] is True
        assert "通知发送失败（不影响邀约，可继续）" in result["message"]
        assert "93000" in result["message"]

    async def test_push_exception_swallowed(self, temp_tenant, monkeypatch):
        """编排层外抛（防御路径）：工具仍返回 success=True 不阻塞邀约"""
        from src.local_tools import proxy_tool

        monkeypatch.setattr(
            proxy_tool.recruiting_notify_service, "push_interview_notify",
            AsyncMock(side_effect=RuntimeError("db down")),
        )
        result = await proxy_tool.BossInterviewNotifyTool().execute(
            _trusted_tenant_id=temp_tenant, kind="pre",
            job_name="PHP开发工程师", candidates=CANDIDATES_PRE,
        )
        assert result["success"] is True
        assert "通知发送失败（不影响邀约，可继续）" in result["message"]

    async def test_executor_coerced_model_instances(self, temp_tenant, monkeypatch):
        """回归：ToolExecutor 按 InputModel 强转后 candidates 是
        BossInterviewNotifyCandidate 实例而非 dict（src/tools/executor.py
        对 model_fields_set 逐键 getattr 回填）——工具层必须 model_dump
        为 dict，否则服务层 c.get(...) 抛 AttributeError 被兜底吞掉，
        生产路径永远「通知服务异常」而测试（直传 dict）全绿。
        """
        from src.local_tools import proxy_tool
        from src.local_tools.proxy_tool import BossInterviewNotifyInput
        from src.services import recruiting_notify_service as svc

        # 1) 按 executor 的真实做法构造强转后的参数
        params = {
            "kind": "pre", "job_name": "PHP开发工程师",
            "candidates": CANDIDATES_PRE, "note": "技术面 1 小时",
        }
        validated = BossInterviewNotifyInput(**params)
        coerced = dict(params)
        for key in validated.model_fields_set:
            coerced[key] = getattr(validated, key)
        assert not isinstance(coerced["candidates"][0], dict)  # 确认复现前提

        # 2) 真实服务链路（仅 mock 发送）：强转参数能走通模板与留痕
        svc.upsert_settings(temp_tenant, webhook_url=WEBHOOK_PLAIN, enabled=True)
        send_markdown = AsyncMock(return_value=(True, None))
        monkeypatch.setattr(svc.wecom_bot, "send_markdown", send_markdown)
        result = await proxy_tool.BossInterviewNotifyTool().execute(
            _trusted_tenant_id=temp_tenant, **coerced
        )
        assert result["success"] is True
        assert result["data"]["pushed"] is True
        content = send_markdown.await_args.args[1]
        assert "> 陈远健 · 90分" in content


class TestNotifyApi:
    def test_get_default_structure(self, temp_tenant):
        from src.api.recruiting_operator import get_notify_settings

        with _mock_tenant_ctx(temp_tenant):
            resp = _call(get_notify_settings(request=None))
        assert resp["success"] is True
        assert resp["data"] == {
            "tenant_id": temp_tenant,
            "enabled": False,
            "webhook_url": "",
            "at_mobiles": [],
            "pre_notify_enabled": True,
        }

    def test_put_masked_return_and_keep_old(self, temp_tenant):
        from src.api.recruiting_operator import get_notify_settings, update_notify_settings
        from src.services import recruiting_notify_service as svc

        with _mock_tenant_ctx(temp_tenant):
            resp = _call(update_notify_settings(
                request=None,
                req=_req({"enabled": True, "webhook_url": WEBHOOK_PLAIN, "at_mobiles": ["13800000001"]}),
            ))
        assert resp["success"] is True
        assert resp["data"]["webhook_url"] == f"***{WEBHOOK_PLAIN[-4:]}"
        assert resp["data"]["enabled"] is True

        # 回传掩码 + 关闭开关：webhook 保留旧值
        with _mock_tenant_ctx(temp_tenant):
            resp = _call(update_notify_settings(
                request=None, req=_req({"webhook_url": resp["data"]["webhook_url"], "enabled": False}),
            ))
        assert resp["data"]["webhook_url"] == f"***{WEBHOOK_PLAIN[-4:]}"
        settings = svc.get_settings(temp_tenant)
        assert settings["webhook_url"] == WEBHOOK_PLAIN
        assert settings["enabled"] is False

        with _mock_tenant_ctx(temp_tenant):
            got = _call(get_notify_settings(request=None))
        assert got["data"]["enabled"] is False

    def test_resend_updates_log_status(self, temp_tenant, monkeypatch):
        """补推：先制造 failed 日志，再修复发送后 resend → 状态改 sent"""
        from src.api.recruiting_operator import resend_notify_log
        from src.services import recruiting_notify_service as svc

        svc.upsert_settings(temp_tenant, webhook_url=WEBHOOK_PLAIN, enabled=True)
        monkeypatch.setattr(
            svc.wecom_bot, "send_markdown",
            AsyncMock(return_value=(False, "企微接口错误 errcode=93000")),
        )
        failed = _call(svc.push_interview_notify(
            temp_tenant, "done", "PHP开发工程师", CANDIDATES_DONE
        ))
        assert failed["pushed"] is False

        monkeypatch.setattr(svc.wecom_bot, "send_markdown", AsyncMock(return_value=(True, None)))
        monkeypatch.setattr(svc.wecom_bot, "send_text", AsyncMock(return_value=(True, None)))
        with _mock_tenant_ctx(temp_tenant):
            resp = _call(resend_notify_log(request=None, req=_req({"log_id": failed["log_id"]})))
        assert resp["success"] is True
        assert resp["data"]["pushed"] is True
        assert _get_log_row(temp_tenant, failed["log_id"])["status"] == "sent"

    def test_resend_not_found_404(self, temp_tenant):
        from src.api.recruiting_operator import resend_notify_log

        with _mock_tenant_ctx(temp_tenant):
            resp = _call(resend_notify_log(request=None, req=_req({"log_id": 999999999})))
        assert resp.status_code == 404
        assert _unpack(resp)["error"] == "通知记录不存在"

    def test_resend_disabled_400(self, temp_tenant, monkeypatch):
        from src.api.recruiting_operator import resend_notify_log
        from src.services import recruiting_notify_service as svc

        svc.upsert_settings(temp_tenant, webhook_url=WEBHOOK_PLAIN, enabled=True)
        monkeypatch.setattr(svc.wecom_bot, "send_markdown", AsyncMock(return_value=(True, None)))
        log = _call(svc.push_interview_notify(temp_tenant, "pre", "PHP开发工程师", CANDIDATES_PRE))

        svc.upsert_settings(temp_tenant, enabled=False)  # 关闭后补推被拒（全静默语义）
        with _mock_tenant_ctx(temp_tenant):
            resp = _call(resend_notify_log(request=None, req=_req({"log_id": log["log_id"]})))
        assert resp.status_code == 400
        body = _unpack(resp)
        assert body["success"] is False
        assert "未启用" in body["error"]


def _req(payload: dict):
    """构造 Pydantic 请求模型实例"""
    from src.api.recruiting_operator import NotifyResendRequest, NotifySettingsRequest

    if "log_id" in payload:
        return NotifyResendRequest(**payload)
    return NotifySettingsRequest(**payload)
