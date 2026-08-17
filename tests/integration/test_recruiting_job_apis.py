"""
招聘操作智能体职位库 API 集成测试

覆盖 /api/recruiting-operator 职位库 CRUD（jobs + job_scripts）：
- 预置：列表首次访问自动插入「PHP开发工程师（Laravel）」+ 13 条话术（幂等，仅一次）
- 职位 CRUD：创建 / 更新（重名 400）/ 删除（级联删话术）
- 话术 CRUD：创建（分类校验）/ 更新 / 删除
- 租户隔离：B 租户看不到 A 的职位，不可详情/更新/删除/挂话术
- 错误：重名 400 / 不存在 id 404 / 非法 UUID 400 / 空名称 400

测试策略（镜像 test_recruiting_operator_apis.py）：
- 使用真实 PostgreSQL，创建临时租户隔离测试数据
- mock get_current_tenant_id / get_current_user
- 测试正常路径 + 异常路径（404 / 400）
"""

import uuid as uuid_module
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.integration


# ============== Fixture ==============

@pytest.fixture(scope="module", autouse=True)
def _ensure_tables():
    """模块级幂等建表（测试库可能未跑过服务启动初始化）"""
    from src.db.database import get_db_connection
    from src.api.recruiting_operator import init_recruiting_job_tables

    with get_db_connection() as conn:
        init_recruiting_job_tables(conn)
        conn.commit()


@pytest.fixture
def temp_tenant_with_user():
    """创建临时租户 + 测试用户，测试后清理（职位与话术一并清理）"""
    from src.saas.db.tenant_db import TenantDB
    from src.db.database import get_db_connection

    tenant_code = f"T{uuid_module.uuid4().hex[:6].upper()}"
    tenant = TenantDB.create(
        company_name=f"职位库测试租户-{tenant_code}",
        tenant_code=tenant_code,
        contact_name="测试联系人",
        contact_phone="13800000000",
    )
    if not tenant:
        pytest.skip("无法创建测试租户（DB 不可用）")
    tenant_id = tenant["tenant_id"]
    user_id = f"test_user_{uuid_module.uuid4().hex[:8]}"
    yield {"tenant_id": tenant_id, "user_id": user_id}

    # 清理：删测试业务数据 + 删租户
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
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
    """同步驱动 async 端点函数（独立 event loop，跑完即关，避免跨文件 loop 状态污染）"""
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _count_rows(table: str, tenant_id: str) -> int:
    """直查 DB 统计该租户在指定表的行数"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"SELECT COUNT(*) AS cnt FROM {table} WHERE tenant_id = %s", (tenant_id,))
        return cursor.fetchone()["cnt"]


# ============== 1. 默认职位预置 ==============

class TestDefaultJobSeeding:
    """列表/详情首次访问自动预置默认职位"""

    def test_list_first_time_seeds_default_job_and_scripts(self, temp_tenant_with_user):
        """空租户首次列表：自动插入 PHP 职位 + 13 条话术（3/4/3/3 四分类）"""
        from src.api import recruiting_operator

        ctx = temp_tenant_with_user
        with _mock_tenant_ctx(ctx["tenant_id"]):
            response = _unpack(_call(recruiting_operator.list_jobs(request=None)))

        assert response["success"] is True
        items = response["data"]["items"]
        assert len(items) == 1
        job = items[0]
        assert job["job_name"] == "PHP开发工程师（Laravel）"
        assert "PHP 8" in (job["notes"] or "")
        assert job["script_count"] == 13
        assert job["categories"] == ["初次开场", "了解摸底", "追问细节", "邀约推进"]
        assert job["tenant_id"] == ctx["tenant_id"]

        # 详情：13 条话术 + 分类分组形状
        with _mock_tenant_ctx(ctx["tenant_id"]):
            detail_resp = _unpack(_call(recruiting_operator.get_job(job["id"], request=None)))
        assert detail_resp["success"] is True
        detail = detail_resp["data"]
        assert len(detail["scripts"]) == 13
        group_counts = {g["category"]: len(g["scripts"]) for g in detail["script_groups"]}
        assert group_counts == {"初次开场": 3, "了解摸底": 4, "追问细节": 3, "邀约推进": 3}
        # 话术字段齐全，content 支持 {{占位符}}
        assert all(s["title"] and s["content"] for s in detail["scripts"])
        assert any("{{" in s["content"] for s in detail["scripts"])

    def test_list_seeding_idempotent(self, temp_tenant_with_user):
        """再次列表不重复插入（仍 1 个职位 13 条话术）"""
        from src.api import recruiting_operator

        ctx = temp_tenant_with_user
        for _ in range(2):
            with _mock_tenant_ctx(ctx["tenant_id"]):
                response = _unpack(_call(recruiting_operator.list_jobs(request=None)))
            assert response["success"] is True

        assert _count_rows("bs_recruiting_operator_jobs", ctx["tenant_id"]) == 1
        assert _count_rows("bs_recruiting_operator_job_scripts", ctx["tenant_id"]) == 13

    def test_seeding_skipped_when_job_exists(self, temp_tenant_with_user):
        """已有职位（非默认名）时不预置默认职位"""
        from src.api import recruiting_operator

        ctx = temp_tenant_with_user
        req = recruiting_operator.CreateJobRequest(job_name="资深后端工程师", notes="自建")
        with _mock_tenant_ctx(ctx["tenant_id"]), _mock_user(ctx["user_id"]):
            create_resp = _unpack(_call(recruiting_operator.create_job(req, request=None)))
        assert create_resp["success"] is True

        with _mock_tenant_ctx(ctx["tenant_id"]):
            list_resp = _unpack(_call(recruiting_operator.list_jobs(request=None)))
        names = [j["job_name"] for j in list_resp["data"]["items"]]
        assert names == ["资深后端工程师"]
        assert "PHP开发工程师（Laravel）" not in names


# ============== 2. 职位 + 话术 CRUD 全链 ==============

class TestJobCrudAPI:
    """职位 / 话术 CRUD 测试"""

    def test_full_chain_create_update_delete(self, temp_tenant_with_user):
        """全链：建职位 → 加话术 → 改话术 → 改职位 → 删职位级联删话术"""
        from src.api import recruiting_operator
        from src.db.database import get_db_connection

        ctx = temp_tenant_with_user

        # 1. 建职位（带空话术详情）
        create_req = recruiting_operator.CreateJobRequest(
            job_name="前端工程师（Vue）", notes="技术栈：Vue3 / TypeScript"
        )
        with _mock_tenant_ctx(ctx["tenant_id"]), _mock_user(ctx["user_id"]):
            create_resp = _unpack(_call(recruiting_operator.create_job(create_req, request=None)))
        assert create_resp["success"] is True
        job = create_resp["data"]
        job_id = job["id"]
        assert job["job_name"] == "前端工程师（Vue）"
        assert job["scripts"] == []
        assert job["script_count"] == 0

        # 2. 加两条话术（不同分类）
        script_req_1 = recruiting_operator.CreateJobScriptRequest(
            category="初次开场", title="开场·Vue 匹配", content="您好，我们招 Vue3 工程师～"
        )
        script_req_2 = recruiting_operator.CreateJobScriptRequest(
            category="邀约推进", title="邀约·约面", content="想约您本周技术面～"
        )
        with _mock_tenant_ctx(ctx["tenant_id"]):
            s1 = _unpack(_call(recruiting_operator.create_job_script(job_id, script_req_1, request=None)))
            s2 = _unpack(_call(recruiting_operator.create_job_script(job_id, script_req_2, request=None)))
        assert s1["success"] is True and s2["success"] is True
        script_id_1 = s1["data"]["id"]
        assert s1["data"]["job_id"] == job_id
        assert s1["data"]["category"] == "初次开场"

        # 详情含 2 条话术、分组正确
        with _mock_tenant_ctx(ctx["tenant_id"]):
            detail = _unpack(_call(recruiting_operator.get_job(job_id, request=None)))
        assert detail["data"]["script_count"] == 2
        group_counts = {g["category"]: len(g["scripts"]) for g in detail["data"]["script_groups"]}
        assert group_counts == {"初次开场": 1, "邀约推进": 1}

        # 3. 改话术（换分类 + 改标题正文）
        patch_req = recruiting_operator.UpdateJobScriptRequest(
            category="了解摸底", title="摸底·工程化", content="您平时怎么做组件设计？"
        )
        with _mock_tenant_ctx(ctx["tenant_id"]):
            patched = _unpack(_call(recruiting_operator.update_job_script(script_id_1, patch_req, request=None)))
        assert patched["success"] is True
        assert patched["data"]["category"] == "了解摸底"
        assert patched["data"]["title"] == "摸底·工程化"

        # 4. 改职位（改名 + 改备注）
        job_patch = recruiting_operator.UpdateJobRequest(job_name="资深前端工程师", notes="新增 React")
        with _mock_tenant_ctx(ctx["tenant_id"]):
            job_patched = _unpack(_call(recruiting_operator.update_job(job_id, job_patch, request=None)))
        assert job_patched["success"] is True
        assert job_patched["data"]["job_name"] == "资深前端工程师"
        # 更新返回也带话术
        assert job_patched["data"]["script_count"] == 2

        # 5. 删职位 → 级联删话术
        with _mock_tenant_ctx(ctx["tenant_id"]):
            del_resp = _unpack(_call(recruiting_operator.delete_job(job_id, request=None)))
        assert del_resp["success"] is True
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) AS cnt FROM bs_recruiting_operator_job_scripts WHERE job_id = %s",
                (job_id,),
            )
            assert cursor.fetchone()["cnt"] == 0

        # 删后再查 404
        with _mock_tenant_ctx(ctx["tenant_id"]):
            get_resp = _unpack(_call(recruiting_operator.get_job(job_id, request=None)))
        assert get_resp["success"] is False
        assert get_resp["error"] == "职位不存在"

    def test_create_job_duplicate_name_returns_400(self, temp_tenant_with_user):
        """重名职位返回 400"""
        from src.api import recruiting_operator

        ctx = temp_tenant_with_user
        req = recruiting_operator.CreateJobRequest(job_name="测试职位")
        with _mock_tenant_ctx(ctx["tenant_id"]), _mock_user(ctx["user_id"]):
            first = _unpack(_call(recruiting_operator.create_job(req, request=None)))
            second = _unpack(_call(recruiting_operator.create_job(req, request=None)))

        assert first["success"] is True
        assert second["success"] is False
        assert "已存在" in second["error"]

    def test_update_job_rename_to_existing_returns_400(self, temp_tenant_with_user):
        """改名撞上既有职位名返回 400"""
        from src.api import recruiting_operator

        ctx = temp_tenant_with_user
        ids = []
        for name in ("职位A", "职位B"):
            req = recruiting_operator.CreateJobRequest(job_name=name)
            with _mock_tenant_ctx(ctx["tenant_id"]), _mock_user(ctx["user_id"]):
                resp = _unpack(_call(recruiting_operator.create_job(req, request=None)))
            ids.append(resp["data"]["id"])

        patch_req = recruiting_operator.UpdateJobRequest(job_name="职位A")
        with _mock_tenant_ctx(ctx["tenant_id"]):
            resp = _unpack(_call(recruiting_operator.update_job(ids[1], patch_req, request=None)))
        assert resp["success"] is False
        assert "已存在" in resp["error"]

    def test_create_job_empty_name_returns_400(self, temp_tenant_with_user):
        """空职位名返回 400"""
        from src.api import recruiting_operator

        ctx = temp_tenant_with_user
        req = recruiting_operator.CreateJobRequest(job_name="   ")
        with _mock_tenant_ctx(ctx["tenant_id"]), _mock_user(ctx["user_id"]):
            resp = _unpack(_call(recruiting_operator.create_job(req, request=None)))
        assert resp["success"] is False

    def test_invalid_uuid_returns_400(self, temp_tenant_with_user):
        """非法 UUID（路径参数格式错）返回 400 而非 500"""
        from src.api import recruiting_operator

        ctx = temp_tenant_with_user
        with _mock_tenant_ctx(ctx["tenant_id"]):
            get_resp = _unpack(_call(recruiting_operator.get_job("not-a-uuid", request=None)))
            del_resp = _unpack(_call(recruiting_operator.delete_job("not-a-uuid", request=None)))
        assert get_resp["success"] is False
        assert "格式非法" in get_resp["error"]
        assert del_resp["success"] is False

    def test_job_not_found_returns_404(self, temp_tenant_with_user):
        """不存在 id：详情 / 更新 / 删除 / 挂话术均 404"""
        from src.api import recruiting_operator

        ctx = temp_tenant_with_user
        missing = str(uuid_module.uuid4())
        with _mock_tenant_ctx(ctx["tenant_id"]):
            get_resp = _unpack(_call(recruiting_operator.get_job(missing, request=None)))
            patch_resp = _unpack(_call(recruiting_operator.update_job(
                missing, recruiting_operator.UpdateJobRequest(notes="x"), request=None)))
            del_resp = _unpack(_call(recruiting_operator.delete_job(missing, request=None)))
            script_resp = _unpack(_call(recruiting_operator.create_job_script(
                missing,
                recruiting_operator.CreateJobScriptRequest(
                    category="初次开场", title="t", content="c"),
                request=None)))

        assert get_resp["success"] is False and get_resp["error"] == "职位不存在"
        assert patch_resp["success"] is False
        assert del_resp["success"] is False
        assert script_resp["success"] is False and script_resp["error"] == "职位不存在"

    def test_script_validation_and_not_found(self, temp_tenant_with_user):
        """话术分类非法 400 / 空标题 400 / 不存在话术更新删除 404"""
        from src.api import recruiting_operator

        ctx = temp_tenant_with_user
        job_req = recruiting_operator.CreateJobRequest(job_name="话术校验职位")
        with _mock_tenant_ctx(ctx["tenant_id"]), _mock_user(ctx["user_id"]):
            job_resp = _unpack(_call(recruiting_operator.create_job(job_req, request=None)))
        job_id = job_resp["data"]["id"]

        # 非法分类
        bad_cat = recruiting_operator.CreateJobScriptRequest(
            category="不存在的分类", title="t", content="c")
        with _mock_tenant_ctx(ctx["tenant_id"]):
            bad_cat_resp = _unpack(_call(recruiting_operator.create_job_script(job_id, bad_cat, request=None)))
        assert bad_cat_resp["success"] is False
        assert "分类非法" in bad_cat_resp["error"]

        # 空标题
        empty_title = recruiting_operator.CreateJobScriptRequest(
            category="初次开场", title="  ", content="c")
        with _mock_tenant_ctx(ctx["tenant_id"]):
            empty_resp = _unpack(_call(recruiting_operator.create_job_script(job_id, empty_title, request=None)))
        assert empty_resp["success"] is False

        # 不存在话术：更新 / 删除 404
        missing = str(uuid_module.uuid4())
        with _mock_tenant_ctx(ctx["tenant_id"]):
            patch_resp = _unpack(_call(recruiting_operator.update_job_script(
                missing, recruiting_operator.UpdateJobScriptRequest(title="新标题"), request=None)))
            del_resp = _unpack(_call(recruiting_operator.delete_job_script(missing, request=None)))
        assert patch_resp["success"] is False and patch_resp["error"] == "话术不存在"
        assert del_resp["success"] is False

    def test_missing_tenant_returns_400(self, temp_tenant_with_user):
        """无租户 ID 返回 400"""
        from src.api import recruiting_operator

        with patch("src.api.recruiting_operator.get_current_tenant_id", return_value=None):
            response = _unpack(_call(recruiting_operator.list_jobs(request=None)))
        assert response["success"] is False
        assert response["error"] == "租户 ID 缺失"


# ============== 租户隔离测试 ==============

class TestTenantIsolation:
    """租户隔离测试：A 租户不能访问 B 租户的数据"""

    def test_cross_tenant_invisible(self, temp_tenant_with_user):
        """B 租户列表看不到 A 的自建职位；详情/更新/删除/挂话术均 404"""
        from src.api import recruiting_operator
        from src.saas.db.tenant_db import TenantDB
        from src.db.database import get_db_connection

        ctx_a = temp_tenant_with_user
        # A 租户建自建职位（不触发默认预置语义混入）
        job_req = recruiting_operator.CreateJobRequest(job_name="A租户专属职位")
        with _mock_tenant_ctx(ctx_a["tenant_id"]), _mock_user(ctx_a["user_id"]):
            job_resp = _unpack(_call(recruiting_operator.create_job(job_req, request=None)))
        job_id_a = job_resp["data"]["id"]

        # 创建 B 租户
        tenant_code_b = f"T{uuid_module.uuid4().hex[:6].upper()}"
        tenant_b = TenantDB.create(
            company_name=f"职位库测试租户B-{tenant_code_b}",
            tenant_code=tenant_code_b,
            contact_name="测试B",
            contact_phone="13800000001",
        )
        if not tenant_b:
            pytest.skip("无法创建测试租户B")
        tenant_id_b = tenant_b["tenant_id"]

        try:
            # 列表不可见（B 租户首次列表只会有自己的默认预置职位，绝无 A 的自建职位）
            with _mock_tenant_ctx(tenant_id_b):
                list_resp = _unpack(_call(recruiting_operator.list_jobs(request=None)))
                assert list_resp["success"] is True
                names = [j["job_name"] for j in list_resp["data"]["items"]]
                assert "A租户专属职位" not in names
                # B 租户的预置职位是独立一份（各租户各自预置）
                assert names == ["PHP开发工程师（Laravel）"]

                # 详情 / 更新 / 删除 / 挂话术均 404
                get_resp = _unpack(_call(recruiting_operator.get_job(job_id_a, request=None)))
                assert get_resp["success"] is False
                assert get_resp["error"] == "职位不存在"

                patch_resp = _unpack(_call(recruiting_operator.update_job(
                    job_id_a, recruiting_operator.UpdateJobRequest(notes="越权"), request=None)))
                assert patch_resp["success"] is False

                del_resp = _unpack(_call(recruiting_operator.delete_job(job_id_a, request=None)))
                assert del_resp["success"] is False

                script_resp = _unpack(_call(recruiting_operator.create_job_script(
                    job_id_a,
                    recruiting_operator.CreateJobScriptRequest(
                        category="初次开场", title="越权", content="x"),
                    request=None)))
                assert script_resp["success"] is False

            # A 的职位未被 B 触碰
            with _mock_tenant_ctx(ctx_a["tenant_id"]):
                check = _unpack(_call(recruiting_operator.get_job(job_id_a, request=None)))
            assert check["success"] is True
            assert check["data"]["job_name"] == "A租户专属职位"
        finally:
            # 清理 B 租户数据 + 租户
            try:
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        "DELETE FROM bs_recruiting_operator_job_scripts WHERE tenant_id = %s",
                        (tenant_id_b,),
                    )
                    cursor.execute(
                        "DELETE FROM bs_recruiting_operator_jobs WHERE tenant_id = %s",
                        (tenant_id_b,),
                    )
                    conn.commit()
            except Exception:
                pass
            TenantDB.delete(tenant_id_b)
            try:
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("DELETE FROM tenants WHERE tenant_id = %s", (tenant_id_b,))
                    conn.commit()
            except Exception:
                pass
