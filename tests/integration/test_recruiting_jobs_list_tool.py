"""boss_jobs_list 云端查询工具集成测试（简历-职位匹配 Phase 3，设计 §5.1 数据层；
Phase 4 补 options 编号选择元数据断言）

真实 DB（临时租户）验证：
- active 过滤：paused 职位不返回
- 字段完整：match_threshold / job_requirements 透传（可直接作 boss_filter 入参）
- 统计正确：resume_count = 该职位关联简历数，matched_count 仅 match_status='matched'
- options 元数据（Phase 4 §5.1）：与 jobs 同序，key/label 对应，
  description 拼要求三维度（educations 顿号连接）+ 简历/匹配统计，
  三维度全缺（含 job_requirements 为 null）写「要求未配置」，空 active 无 options 键
- 无 active 职位：空列表 + 引导去「职位管理」创建的提示文案

boss_jobs_list 为混合模式工具（注册在 LOCAL_PROXY_TOOL_CLASSES 但 execute 纯云端），
全程无需设备在线、不建 invocation。
"""

import uuid as uuid_module

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _ensure_tables():
    from src.db.database import get_db_connection
    from src.services.recruiting_job_service import init_recruiting_job_tables
    from src.services.recruiting_resume_service import init_recruiting_operator_tables

    with get_db_connection() as conn:
        init_recruiting_job_tables(conn)
        init_recruiting_operator_tables(conn)
        conn.commit()  # 连接上下文正常退出会 rollback，DDL 迁移必须显式提交
    yield


@pytest.fixture
def temp_tenant_with_user():
    from src.saas.db.tenant_db import TenantDB
    from src.db.database import get_db_connection

    tenant_code = f"T{uuid_module.uuid4().hex[:6].upper()}"
    tenant = TenantDB.create(
        company_name=f"职位库查询测试租户-{tenant_code}",
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


def _set_match_status(tenant_id, candidate_name, status, score):
    """直接 SQL 置评分列（评分服务走 LLM，测试不真调网）"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE bs_recruiting_operator_resumes SET match_status = %s, match_score = %s "
            "WHERE tenant_id = %s AND candidate_name = %s",
            (status, score, tenant_id, candidate_name),
        )
        conn.commit()


class TestBossJobsListTool:
    def test_no_identity(self):
        from src.local_tools.proxy_tool import BossJobsListTool

        r = _call(BossJobsListTool().execute())
        assert r["success"] is False
        assert r["code"] == "NO_IDENTITY"

    def test_active_filter_fields_and_stats(self, temp_tenant_with_user):
        """paused 不返回；active 职位带要求/阈值；简历数与匹配数统计正确"""
        from src.local_tools.proxy_tool import BossJobsListTool
        from src.services import recruiting_job_service, recruiting_resume_service

        ctx = temp_tenant_with_user
        # 显式建 PHP 职位（active）补上要求；另建一个 paused 职位不应出现
        php = recruiting_job_service.create_job(ctx["tenant_id"], job_name="PHP开发工程师（Laravel）")
        recruiting_job_service.update_job(
            ctx["tenant_id"], php["id"],
            job_requirements={"experience": "3-5年", "educations": ["本科"], "salary": "10-20K"},
        )
        recruiting_job_service.create_job(ctx["tenant_id"], job_name="Java后端工程师", status="paused")

        # 三份简历硬关联 PHP 职位：两份 matched、一份未评分
        for name in ("刘草威", "何先生", "孙七"):
            recruiting_resume_service.create_resume_record(
                ctx["tenant_id"], ctx["user_id"],
                candidate_name=name, job_id=php["id"], ocr_text=f"{name} 的简历",
            )
        _set_match_status(ctx["tenant_id"], "刘草威", "matched", 82)
        _set_match_status(ctx["tenant_id"], "何先生", "matched", 75)
        _set_match_status(ctx["tenant_id"], "孙七", "unmatched", 61)

        r = _call(BossJobsListTool().execute(
            _trusted_tenant_id=ctx["tenant_id"], _trusted_user_id=ctx["user_id"]
        ))
        assert r["success"] is True
        items = r["data"]["jobs"]
        assert [j["job_name"] for j in items] == ["PHP开发工程师（Laravel）"]  # paused 的 Java 不返回
        item = items[0]
        assert item["job_id"] == php["id"]
        assert item["status"] == "active"
        assert item["match_threshold"] == 70
        assert item["job_requirements"] == {
            "experience": "3-5年", "educations": ["本科"], "salary": "10-20K",
        }
        assert item["resume_count"] == 3
        assert item["matched_count"] == 2
        assert "PHP开发工程师（Laravel）" in r["message"]
        # Phase 4 options：与 jobs 同序，key/label 对应，description 拼要求三维度 + 统计
        options = r["data"]["options"]
        assert len(options) == len(items)
        assert [o["key"] for o in options] == [j["job_id"] for j in items]
        assert [o["label"] for o in options] == [j["job_name"] for j in items]
        assert options[0]["description"] == "要求 3-5年/本科/10-20K · 简历 3 · 匹配 2"

    def test_options_description_variants(self, temp_tenant_with_user):
        """options.description 拼接规则：三维度部分缺省 / educations 顿号 / 全缺「要求未配置」"""
        from src.local_tools.proxy_tool import BossJobsListTool
        from src.services import recruiting_job_service

        ctx = temp_tenant_with_user
        # 显式建 PHP 职位（job_requirements 保持 null → 不崩 + 「要求未配置」）
        recruiting_job_service.create_job(ctx["tenant_id"], job_name="PHP开发工程师（Laravel）")
        recruiting_job_service.create_job(
            ctx["tenant_id"], job_name="全栈工程师",
            job_requirements={"educations": ["本科", "硕士"]},  # 仅学历（顿号连接）
        )
        recruiting_job_service.create_job(
            ctx["tenant_id"], job_name="Golang后端工程师",
            job_requirements={"experience": "3-5年", "educations": ["本科", "硕士"], "salary": "20-50K"},
        )

        r = _call(BossJobsListTool().execute(
            _trusted_tenant_id=ctx["tenant_id"], _trusted_user_id=ctx["user_id"]
        ))
        assert r["success"] is True
        items = r["data"]["jobs"]
        options = r["data"]["options"]
        assert [o["key"] for o in options] == [j["job_id"] for j in items]
        by_label = {o["label"]: o["description"] for o in options}
        assert by_label["PHP开发工程师（Laravel）"] == "要求未配置 · 简历 0 · 匹配 0"
        assert by_label["全栈工程师"] == "要求 本科、硕士 · 简历 0 · 匹配 0"
        assert by_label["Golang后端工程师"] == "要求 3-5年/本科、硕士/20-50K · 简历 0 · 匹配 0"

    def test_no_active_jobs_returns_empty_with_hint(self, temp_tenant_with_user):
        """全部职位 paused → 空列表 + 引导去「职位管理」创建"""
        from src.local_tools.proxy_tool import BossJobsListTool
        from src.services import recruiting_job_service

        ctx = temp_tenant_with_user
        recruiting_job_service.create_job(ctx["tenant_id"], job_name="PHP开发工程师（Laravel）")
        for job in recruiting_job_service.list_jobs(ctx["tenant_id"]):
            recruiting_job_service.update_job(ctx["tenant_id"], job["id"], status="paused")

        r = _call(BossJobsListTool().execute(
            _trusted_tenant_id=ctx["tenant_id"], _trusted_user_id=ctx["user_id"]
        ))
        assert r["success"] is True
        assert r["data"]["jobs"] == []
        assert "options" not in r["data"]  # 空 active 不带 options 键（Phase 4 约定）
        assert "职位管理" in r["message"]

    def test_unlinked_resumes_not_counted(self, temp_tenant_with_user):
        """未关联职位（job_id NULL）的简历不计入任何职位统计"""
        from src.local_tools.proxy_tool import BossJobsListTool
        from src.services import recruiting_job_service, recruiting_resume_service

        ctx = temp_tenant_with_user
        php = recruiting_job_service.create_job(ctx["tenant_id"], job_name="PHP开发工程师（Laravel）")
        # create_resume_record 不做 job_name→job_id 自动关联（仅工具落库路解析），job_id 保持 NULL
        recruiting_resume_service.create_resume_record(
            ctx["tenant_id"], ctx["user_id"],
            candidate_name="游离简历", job_name="不存在的职位", ocr_text="内容",
        )
        r = _call(BossJobsListTool().execute(
            _trusted_tenant_id=ctx["tenant_id"], _trusted_user_id=ctx["user_id"]
        ))
        assert r["success"] is True
        item = next(j for j in r["data"]["jobs"] if j["job_id"] == php["id"])
        assert item["resume_count"] == 0
        assert item["matched_count"] == 0
