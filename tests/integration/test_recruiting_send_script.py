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
        conn.commit()  # 连接上下文正常退出会 rollback，DDL 迁移必须显式提交
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


def _cli_success_result(payload=None):
    return {
        "success": True,
        "code": None,
        "message": "发送执行成功",
        "effect": "applied",
        "data": payload or {},
        "invocation_id": "inv-test-1",
    }


# 职位库已不做 demo 预置，测试显式自建职位 + 用到的四条话术（标题/分类与原预置一致）
_PHP_JOB_NAME = "PHP开发工程师（Laravel）"


def _seed_php_job(ctx) -> None:
    """给测试租户显式创建 PHP 职位与四条话术（替代已删除的 ensure_default_job 预置）"""
    from src.services import recruiting_job_service

    job = recruiting_job_service.create_job(ctx["tenant_id"], job_name=_PHP_JOB_NAME)
    for category, title, content in (
        ("初次开场", "开场·技术栈匹配",
         "您好！我们团队主力技术栈是 PHP 8 + Laravel，方便聊聊您最近的项目吗？"),
        ("初次开场", "开场·简历亮点切入",
         "您好！看了您的简历，{{简历亮点}} 方面的经验让我印象很深，很想和您聊聊。"),
        ("了解摸底", "摸底·AI 编程工具（重点）",
         "您日常开发中会用哪些 AI 编程工具？能举个例子说说它怎么帮您提效的吗？"),
        ("邀约推进", "邀约·面试安排",
         "想约您一次技术面（1 小时左右，会聊 Laravel 实战），您这周什么时间方便？"),
    ):
        recruiting_job_service.create_script(
            ctx["tenant_id"], job["id"], category=category, title=title, content=content,
        )


class TestScriptMode:
    def test_need_fill_with_resume_excerpt(self, temp_tenant_with_user):
        """script_title → SCRIPT_NEEDS_FILL：话术原文 + 该候选人简历摘录（占位符交给 LLM）"""
        from src.services import recruiting_resume_service
        from src.local_tools.proxy_tool import BossSendToTool

        ctx = temp_tenant_with_user
        # 显式建职位话术 + 造一份张三丰简历
        _seed_php_job(ctx)
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
        from src.local_tools.proxy_tool import BossSendToTool

        ctx = temp_tenant_with_user
        _seed_php_job(ctx)
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
        from src.local_tools.proxy_tool import BossSendToTool

        ctx = temp_tenant_with_user
        _seed_php_job(ctx)
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


class TestScriptNeedFillMatchFields:
    """SCRIPT_NEEDS_FILL 升级（简历-职位匹配 Phase 3 设计 §5）：带 match_score / key_info（截断）

    四分支：已评分 / 未评分 / 简历在库但 OCR 正文为空（Phase 3 CR 遗留，Phase 4 补） / 无简历。
    """

    @staticmethod
    def _set_match_fields(tenant_id, resume_id, score, status, key_info):
        """直接 SQL 置评分列（评分服务走 LLM，测试不真调网）"""
        import psycopg2.extras
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE bs_recruiting_operator_resumes "
                "SET match_score = %s, match_status = %s, key_info = %s "
                "WHERE id = %s AND tenant_id = %s",
                (score, status,
                 psycopg2.extras.Json(key_info) if key_info is not None else None,
                 resume_id, tenant_id),
            )
            conn.commit()

    def test_need_fill_with_score_and_truncated_key_info(self, temp_tenant_with_user):
        """已评分简历：带 match_score + key_info，超长字段按上限截断（防 OCR 注入借道）"""
        from src.local_tools.proxy_tool import (
            KEY_INFO_LIST_ITEM_MAX_CHARS,
            KEY_INFO_LIST_MAX_ITEMS,
            KEY_INFO_STR_MAX_CHARS,
            BossSendToTool,
        )
        from src.services import recruiting_resume_service

        ctx = temp_tenant_with_user
        _seed_php_job(ctx)
        record = recruiting_resume_service.create_resume_record(
            ctx["tenant_id"], ctx["user_id"],
            candidate_name="刘草威", job_name="PHP开发工程师（Laravel）",
            ocr_text="十年 Laravel 经验，主导日活百万级 SaaS。",
        )
        self._set_match_fields(
            ctx["tenant_id"], record["id"], 82, "matched",
            {
                "years_of_experience": 10,
                "education": "本" * 300,  # 超 200 字 → 截断
                "current_company": "xx科技",
                "core_skills": ["PHP", "Laravel", "MySQL"],
                "highlights": [f"亮点{i}_" + "长" * 100 for i in range(10)],  # 10 项超长 → ≤8 项且每项 ≤50 字
                "ai_tool_usage": "熟练：Cursor + Claude Code 日常开发",
                "salary_expectation": "15-25K",
                "concerns": ["行业跨度大"],
            },
        )
        r = _call(BossSendToTool().execute(
            _trusted_tenant_id=ctx["tenant_id"], _trusted_user_id=ctx["user_id"],
            to="刘草威", script_title="开场·简历亮点切入",
        ))
        assert r["code"] == "SCRIPT_NEEDS_FILL"
        assert r["match_score"] == 82
        assert r["key_info"]["years_of_experience"] == 10
        assert len(r["key_info"]["education"]) == KEY_INFO_STR_MAX_CHARS
        assert len(r["key_info"]["highlights"]) == KEY_INFO_LIST_MAX_ITEMS
        assert all(len(h) <= KEY_INFO_LIST_ITEM_MAX_CHARS for h in r["key_info"]["highlights"])
        assert r["key_info"]["concerns"] == ["行业跨度大"]
        assert r["resume_excerpt"].startswith("十年 Laravel 经验")
        # 引导文案升级：优先 key_info.highlights，其次 resume_excerpt，严禁编造
        assert "key_info.highlights" in r["message"]
        assert "resume_excerpt" in r["message"]
        assert "严禁编造" in r["message"]

    def test_need_fill_unscored_resume_returns_null_match_fields(self, temp_tenant_with_user):
        """简历未评分（评分失败留 NULL 的兜底路）：match_score/key_info=null，excerpt 路径不崩"""
        from src.local_tools.proxy_tool import BossSendToTool
        from src.services import recruiting_resume_service

        ctx = temp_tenant_with_user
        _seed_php_job(ctx)
        recruiting_resume_service.create_resume_record(
            ctx["tenant_id"], ctx["user_id"],
            candidate_name="何先生", job_name="PHP开发工程师（Laravel）",
            ocr_text="五年 PHP 经验。",
        )
        r = _call(BossSendToTool().execute(
            _trusted_tenant_id=ctx["tenant_id"], _trusted_user_id=ctx["user_id"],
            to="何先生", script_title="开场·技术栈匹配",
        ))
        assert r["code"] == "SCRIPT_NEEDS_FILL"
        assert r["match_score"] is None
        assert r["key_info"] is None
        assert r["resume_excerpt"].startswith("五年 PHP")

    def test_need_fill_empty_ocr_returns_hint_with_match_fields(self, temp_tenant_with_user):
        """简历在库但 OCR 正文为空：给 resume_hint（明示空正文）而非 resume_excerpt；
        match_score / key_info 为该简历评分结果，正常携带"""
        from src.local_tools.proxy_tool import BossSendToTool
        from src.services import recruiting_resume_service

        ctx = temp_tenant_with_user
        _seed_php_job(ctx)
        record = recruiting_resume_service.create_resume_record(
            ctx["tenant_id"], ctx["user_id"],
            candidate_name="王五", job_name="PHP开发工程师（Laravel）",
            ocr_text="",  # 简历在库但 OCR 正文为空
        )
        self._set_match_fields(
            ctx["tenant_id"], record["id"], 82, "matched",
            {"highlights": ["日活十万级 SaaS 主导"]},
        )
        r = _call(BossSendToTool().execute(
            _trusted_tenant_id=ctx["tenant_id"], _trusted_user_id=ctx["user_id"],
            to="王五", script_title="开场·简历亮点切入",
        ))
        assert r["code"] == "SCRIPT_NEEDS_FILL"
        assert r["match_score"] == 82
        assert r["key_info"]["highlights"] == ["日活十万级 SaaS 主导"]
        assert "resume_excerpt" not in r
        assert "王五" in r["resume_hint"]
        assert "OCR 正文为空" in r["resume_hint"]
        assert "严禁编造" in r["resume_hint"]

    def test_need_fill_no_resume_null_match_fields_with_hint(self, temp_tenant_with_user):
        """无简历：match_score/key_info=null + resume_hint 引导"""
        from src.local_tools.proxy_tool import BossSendToTool

        ctx = temp_tenant_with_user
        _seed_php_job(ctx)
        r = _call(BossSendToTool().execute(
            _trusted_tenant_id=ctx["tenant_id"], _trusted_user_id=ctx["user_id"],
            to="赵六", script_title="开场·技术栈匹配",
        ))
        assert r["code"] == "SCRIPT_NEEDS_FILL"
        assert r["match_score"] is None
        assert r["key_info"] is None
        assert "resume_excerpt" not in r
        assert "赵六" in r["resume_hint"]


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
        from src.local_tools.proxy_tool import BossSendCurrentTool

        ctx = temp_tenant_with_user
        _seed_php_job(ctx)
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


class TestScriptJobScoping:
    """话术只在所属职位下解析（Phase 1 设计 §4.3）：必须先定位唯一职位，绝不跨职位混用"""

    def test_single_job_omission_resolves(self, temp_tenant_with_user):
        """租户恰好只有一个职位时省略 job_name 可解析（测试自建单职位）"""
        from src.local_tools.proxy_tool import BossSendToTool

        ctx = temp_tenant_with_user
        _seed_php_job(ctx)  # 仅一个职位
        r = _call(BossSendToTool().execute(
            _trusted_tenant_id=ctx["tenant_id"], _trusted_user_id=ctx["user_id"],
            to="候选人", script_title="开场·技术栈匹配",
        ))
        assert r["code"] == "SCRIPT_NEEDS_FILL"
        assert r["script"]["job_name"] == "PHP开发工程师（Laravel）"

    def test_multi_jobs_without_job_name_errors_with_list(self, temp_tenant_with_user):
        """多职位时不传 job_name → 报错，错误信息含全部职位清单"""
        from src.services import recruiting_job_service
        from src.local_tools.proxy_tool import BossSendToTool

        ctx = temp_tenant_with_user
        _seed_php_job(ctx)  # 自建 PHP 职位
        recruiting_job_service.create_job(ctx["tenant_id"], job_name="Java后端工程师")

        r = _call(BossSendToTool().execute(
            _trusted_tenant_id=ctx["tenant_id"], _trusted_user_id=ctx["user_id"],
            to="候选人", script_title="开场·技术栈匹配",
        ))
        assert r["success"] is False
        assert r["code"] == "NOT_FOUND"
        assert "job_name" in r["message"]
        assert "PHP开发工程师（Laravel）" in r["message"]
        assert "Java后端工程师" in r["message"]

    def test_job_not_found_lists_existing_jobs(self, temp_tenant_with_user):
        """职位定位不到 → 报错并列出现有职位名"""
        from src.services import recruiting_job_service
        from src.local_tools.proxy_tool import BossSendCurrentTool

        ctx = temp_tenant_with_user
        _seed_php_job(ctx)
        recruiting_job_service.create_job(ctx["tenant_id"], job_name="Java后端工程师")

        r = _call(BossSendCurrentTool().execute(
            _trusted_tenant_id=ctx["tenant_id"], _trusted_user_id=ctx["user_id"],
            script_title="开场·技术栈匹配", job_name="不存在的职位",
        ))
        assert r["success"] is False
        assert r["code"] == "NOT_FOUND"
        assert "不存在的职位" in r["message"]
        assert "PHP开发工程师（Laravel）" in r["message"]
        assert "Java后端工程师" in r["message"]

    def test_script_scoped_within_job_no_cross_hit(self, temp_tenant_with_user):
        """话术只在指定职位内找：A 职位的话术不会被 B 职位请求命中，反之亦然"""
        from src.services import recruiting_job_service
        from src.local_tools.proxy_tool import BossSendToTool

        ctx = temp_tenant_with_user
        _seed_php_job(ctx)  # 自建 A：PHP 职位（四条话术）
        job_b = recruiting_job_service.create_job(ctx["tenant_id"], job_name="Java后端工程师")
        recruiting_job_service.create_script(
            ctx["tenant_id"], job_b["id"],
            category="初次开场", title="开场·Java 匹配", content="您好，我们招 Java 工程师～",
        )

        # B 职位请求 A 的话术标题 → NOT_FOUND（不跨职位兜底到 A），错误列出 B 自己的可选话术
        r_cross = _call(BossSendToTool().execute(
            _trusted_tenant_id=ctx["tenant_id"], _trusted_user_id=ctx["user_id"],
            to="候选人", script_title="开场·技术栈匹配", job_name="Java后端工程师",
        ))
        assert r_cross["success"] is False
        assert r_cross["code"] == "NOT_FOUND"
        assert "Java后端工程师" in r_cross["message"]
        assert "开场·Java 匹配" in r_cross["message"]  # B 自己的可选话术
        assert "开场·技术栈匹配" not in r_cross["message"].split("可选：")[-1]  # 绝不混入 A 的话术

        # B 职位请求自己的话术 → 正常命中
        r_own = _call(BossSendToTool().execute(
            _trusted_tenant_id=ctx["tenant_id"], _trusted_user_id=ctx["user_id"],
            to="候选人", script_title="开场·Java 匹配", job_name="Java后端工程师",
        ))
        assert r_own["code"] == "SCRIPT_NEEDS_FILL"
        assert r_own["script"]["job_name"] == "Java后端工程师"
        assert "Java" in r_own["script"]["content"]

        # A 职位请求自己的话术 → 正常命中（不受 B 影响）
        r_a = _call(BossSendToTool().execute(
            _trusted_tenant_id=ctx["tenant_id"], _trusted_user_id=ctx["user_id"],
            to="候选人", script_title="开场·技术栈匹配", job_name="PHP开发工程师（Laravel）",
        ))
        assert r_a["code"] == "SCRIPT_NEEDS_FILL"
        assert r_a["script"]["job_name"] == "PHP开发工程师（Laravel）"
