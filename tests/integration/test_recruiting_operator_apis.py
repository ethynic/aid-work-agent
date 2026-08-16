"""
招聘操作智能体简历库 API 集成测试

覆盖 /api/recruiting-operator 简历 CRUD：
- 创建：file_id 引用路 / base64 直传路（落盘到临时目录）/ 参数校验（mime/source/fetched_at）
- 列表：分页 / keyword/job_name/status/fetched_at 区间筛选 / 轻量不含 ocr_text / 职位下拉
- 详情：含 ocr_text、images、candidate_info
- 更新：状态流转 + 非法状态 400 / remark 等字段
- 删除
- 租户隔离：另一租户不可见 / 不可改删

测试策略（同 test_video_agent_apis.py）：
- 使用真实 PostgreSQL，创建临时租户隔离测试数据
- mock get_current_tenant_id / get_current_user
- base64 落盘目录 monkeypatch 到 tmp_path，避免污染仓库 storage/
- 测试正常路径 + 异常路径（404 / 400）
"""

import base64
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import psycopg2.extras
import pytest

pytestmark = pytest.mark.integration


# 1x1 透明 PNG，用于 base64 直传路测试
_TINY_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
    "AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


# ============== Fixture ==============

@pytest.fixture(scope="module", autouse=True)
def _ensure_tables():
    """模块级幂等建表（测试库可能未跑过服务启动初始化）"""
    from src.db.database import get_db_connection
    from src.api.recruiting_operator import init_recruiting_operator_tables

    with get_db_connection() as conn:
        init_recruiting_operator_tables(conn)
        conn.commit()


@pytest.fixture
def temp_tenant_with_user():
    """创建临时租户 + 测试用户，测试后清理"""
    from src.saas.db.tenant_db import TenantDB
    from src.db.database import get_db_connection

    tenant_code = f"T{uuid.uuid4().hex[:6].upper()}"
    tenant = TenantDB.create(
        company_name=f"招聘智能体测试租户-{tenant_code}",
        tenant_code=tenant_code,
        contact_name="测试联系人",
        contact_phone="13800000000",
    )
    if not tenant:
        pytest.skip("无法创建测试租户（DB 不可用）")
    tenant_id = tenant["tenant_id"]
    user_id = f"test_user_{uuid.uuid4().hex[:8]}"
    yield {"tenant_id": tenant_id, "user_id": user_id}

    # 清理：删测试业务数据 + 删租户
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM bs_recruiting_operator_resumes WHERE tenant_id = %s",
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


@pytest.fixture
def temp_storage_dir(tmp_path, monkeypatch):
    """把 base64 落盘目录指向临时目录，避免污染仓库 storage/"""
    target = tmp_path / "recruiting"
    target.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "src.services.recruiting_resume_service.ensure_tenant_storage_dir",
        lambda tenant_id, scene: str(target),
    )
    return target


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
    """同步驱动 async 端点函数。

    用独立的新 event loop 跑完即关闭，不用 asyncio.run()：
    asyncio.run 会在退出时 set_event_loop(None) 并置 _set_called，导致同进程内
    后续模块（如 test_video_agent_apis.py 的 asyncio.get_event_loop() 旧模式）
    抛 "There is no current event loop"，造成跨文件测试状态污染。
    """
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _insert_resume(
    tenant_id: str,
    user_id: str,
    candidate_name: str = "张三",
    job_name: str = "Python 后端工程师",
    status: str = "new",
    fetched_at=None,
    ocr_text: str = "OCR 全文测试内容",
    images=None,
) -> int:
    """插入测试简历记录，返回 id"""
    from src.db.database import get_db_connection

    if images is None:
        images = []
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO bs_recruiting_operator_resumes
                (tenant_id, user_id, candidate_name, job_name, candidate_info, images,
                 ocr_text, source, status, fetched_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'boss', %s, %s)
            RETURNING id
            """,
            (
                tenant_id, user_id, candidate_name, job_name,
                psycopg2.extras.Json({"学历": "本科", "工作年限": "5 年"}),
                psycopg2.extras.Json(images),
                ocr_text, status,
                fetched_at or datetime.now(),
            ),
        )
        row = cursor.fetchone()
        conn.commit()
        return row["id"]


# ============== 1. 创建 API ==============

class TestCreateResumeAPI:
    """POST /resumes 测试"""

    def test_create_resume_with_file_id_images(self, temp_tenant_with_user):
        """创建简历：file_id 引用路（前端先调 /api/upload）"""
        from src.api import recruiting_operator

        ctx = temp_tenant_with_user
        file_id = f"file_{uuid.uuid4().hex[:12]}"
        req = recruiting_operator.CreateResumeRequest(
            candidate_name="李四",
            job_name="前端工程师",
            candidate_info={"学历": "硕士", "城市": "杭州"},
            ocr_text="工作经历：...",
            images=[recruiting_operator.ResumeImageRef(file_id=file_id, name="简历截图1.png")],
            source="boss",
            fetched_at="2026-08-16T10:00:00",
            remark="CLI 采集",
        )

        with _mock_tenant_ctx(ctx["tenant_id"]), _mock_user(ctx["user_id"]):
            response = _unpack(_call(recruiting_operator.create_resume(req, request=None)))

        assert response["success"] is True
        data = response["data"]
        assert data["candidate_name"] == "李四"
        assert data["job_name"] == "前端工程师"
        assert data["status"] == "new"
        assert data["source"] == "boss"
        assert data["user_id"] == ctx["user_id"]
        assert data["candidate_info"]["学历"] == "硕士"
        assert data["images"] == [{"file_id": file_id, "name": "简历截图1.png"}]
        # fetched_at 按 ISO 解析入库
        assert str(data["fetched_at"]).startswith("2026-08-16")

    def test_create_resume_with_base64_images(self, temp_tenant_with_user, temp_storage_dir):
        """创建简历：base64 直传路，落盘到（mock 的）租户 recruiting 目录并转 file_id"""
        from src.api import recruiting_operator

        ctx = temp_tenant_with_user
        req = recruiting_operator.CreateResumeRequest(
            candidate_name="王五",
            job_name="测试工程师",
            images_base64=[
                recruiting_operator.ResumeImageBase64(
                    data=_TINY_PNG_BASE64, name="简历.png", mime_type="image/png"
                ),
            ],
            source="manual",
        )

        with _mock_tenant_ctx(ctx["tenant_id"]), _mock_user(ctx["user_id"]):
            response = _unpack(_call(recruiting_operator.create_resume(req, request=None)))

        assert response["success"] is True
        images = response["data"]["images"]
        assert len(images) == 1
        file_id = images[0]["file_id"]
        assert file_id.startswith("file_")
        # 文件已按 file_{uuid12}.png 落盘，stem 与 file_id 对齐（/api/files 兜底扫描契约）
        saved = list(temp_storage_dir.glob(f"{file_id}.*"))
        assert len(saved) == 1
        assert saved[0].suffix == ".png"
        assert base64.b64decode(_TINY_PNG_BASE64) == saved[0].read_bytes()

    def test_create_resume_base64_with_data_url_prefix(self, temp_tenant_with_user, temp_storage_dir):
        """base64 兼容 data:image/png;base64, 前缀写法"""
        from src.api import recruiting_operator

        ctx = temp_tenant_with_user
        req = recruiting_operator.CreateResumeRequest(
            candidate_name="赵六",
            images_base64=[
                recruiting_operator.ResumeImageBase64(
                    data=f"data:image/png;base64,{_TINY_PNG_BASE64}",
                    mime_type="image/png",
                ),
            ],
        )

        with _mock_tenant_ctx(ctx["tenant_id"]), _mock_user(ctx["user_id"]):
            response = _unpack(_call(recruiting_operator.create_resume(req, request=None)))

        assert response["success"] is True
        assert len(response["data"]["images"]) == 1

    def test_create_resume_rejects_non_image_mime(self, temp_tenant_with_user, temp_storage_dir):
        """base64 直传 mime_type 非 image/* 返回 400"""
        from src.api import recruiting_operator

        ctx = temp_tenant_with_user
        req = recruiting_operator.CreateResumeRequest(
            candidate_name="钱七",
            images_base64=[
                recruiting_operator.ResumeImageBase64(
                    data=base64.b64encode(b"hello").decode(), mime_type="text/plain"
                ),
            ],
        )

        with _mock_tenant_ctx(ctx["tenant_id"]), _mock_user(ctx["user_id"]):
            response = _unpack(_call(recruiting_operator.create_resume(req, request=None)))

        assert response["success"] is False
        # 未落盘任何文件
        assert not list(temp_storage_dir.iterdir())

    def test_create_resume_rejects_unsupported_image_mime(self, temp_tenant_with_user, temp_storage_dir):
        """base64 直传白名单外的图片类型（webp/bmp/svg）返回 400，不落盘"""
        from src.api import recruiting_operator

        ctx = temp_tenant_with_user
        req = recruiting_operator.CreateResumeRequest(
            candidate_name="赵八",
            images_base64=[
                recruiting_operator.ResumeImageBase64(
                    data=base64.b64encode(b"fake-webp").decode(), mime_type="image/webp"
                ),
            ],
        )

        with _mock_tenant_ctx(ctx["tenant_id"]), _mock_user(ctx["user_id"]):
            response = _unpack(_call(recruiting_operator.create_resume(req, request=None)))

        assert response["success"] is False
        assert not list(temp_storage_dir.iterdir())

    def test_create_resume_invalid_source_returns_400(self, temp_tenant_with_user):
        """source 非法返回 400"""
        from src.api import recruiting_operator

        ctx = temp_tenant_with_user
        req = recruiting_operator.CreateResumeRequest(candidate_name="孙八", source="unknown")

        with _mock_tenant_ctx(ctx["tenant_id"]), _mock_user(ctx["user_id"]):
            response = _unpack(_call(recruiting_operator.create_resume(req, request=None)))

        assert response["success"] is False

    def test_create_resume_invalid_fetched_at_returns_400(self, temp_tenant_with_user):
        """fetched_at 非 ISO 格式返回 400"""
        from src.api import recruiting_operator

        ctx = temp_tenant_with_user
        req = recruiting_operator.CreateResumeRequest(candidate_name="周九", fetched_at="not-a-date")

        with _mock_tenant_ctx(ctx["tenant_id"]), _mock_user(ctx["user_id"]):
            response = _unpack(_call(recruiting_operator.create_resume(req, request=None)))

        assert response["success"] is False

    def test_create_resume_missing_tenant_returns_400(self):
        """无租户 ID 返回 400"""
        from src.api import recruiting_operator

        req = recruiting_operator.CreateResumeRequest(candidate_name="吴十")
        with patch("src.api.recruiting_operator.get_current_tenant_id", return_value=None):
            response = _unpack(_call(recruiting_operator.create_resume(req, request=None)))

        assert response["success"] is False
        assert response["error"] == "租户 ID 缺失"


# ============== 2. 列表 API ==============

class TestListResumesAPI:
    """GET /resumes 测试"""

    def test_list_returns_paginated_items_without_ocr_text(self, temp_tenant_with_user):
        """列表分页返回，轻量不含 ocr_text"""
        from src.api import recruiting_operator

        ctx = temp_tenant_with_user
        for i in range(3):
            _insert_resume(ctx["tenant_id"], ctx["user_id"], candidate_name=f"候选人{i}")

        with _mock_tenant_ctx(ctx["tenant_id"]):
            response = _unpack(_call(recruiting_operator.list_resumes(
                request=None, page=1, page_size=2, keyword=None, job_name=None,
                status=None, fetched_at_from=None, fetched_at_to=None,
            )))

        assert response["success"] is True
        assert response["data"]["total"] == 3
        assert response["data"]["page"] == 1
        assert response["data"]["page_size"] == 2
        items = response["data"]["items"]
        assert len(items) == 2
        # 轻量列表不含 ocr_text 全文
        assert all("ocr_text" not in it for it in items)
        assert all(it["tenant_id"] == ctx["tenant_id"] for it in items)

    def test_list_filter_by_keyword(self, temp_tenant_with_user):
        """keyword 按候选人姓名模糊搜索（ILIKE）"""
        from src.api import recruiting_operator

        ctx = temp_tenant_with_user
        _insert_resume(ctx["tenant_id"], ctx["user_id"], candidate_name="张小明")
        _insert_resume(ctx["tenant_id"], ctx["user_id"], candidate_name="李大明")

        with _mock_tenant_ctx(ctx["tenant_id"]):
            response = _unpack(_call(recruiting_operator.list_resumes(
                request=None, page=1, page_size=20, keyword="小明", job_name=None,
                status=None, fetched_at_from=None, fetched_at_to=None,
            )))

        assert response["success"] is True
        items = response["data"]["items"]
        assert len(items) == 1
        assert items[0]["candidate_name"] == "张小明"

    def test_list_keyword_special_chars_are_literal(self, temp_tenant_with_user):
        """keyword 里的 %/_/\\ 按字面匹配（转义），尾随反斜杠不再触发 ILIKE 报错"""
        from src.api import recruiting_operator

        ctx = temp_tenant_with_user
        _insert_resume(ctx["tenant_id"], ctx["user_id"], candidate_name="王%五")
        _insert_resume(ctx["tenant_id"], ctx["user_id"], candidate_name="张小明")

        with _mock_tenant_ctx(ctx["tenant_id"]):
            # % 是字面字符：只命中名字里真的含 % 的记录
            response_pct = _unpack(_call(recruiting_operator.list_resumes(
                request=None, page=1, page_size=20, keyword="%", job_name=None,
                status=None, fetched_at_from=None, fetched_at_to=None,
            )))
            # 尾随反斜杠：正常返回空结果而不是 500（ILIKE 报错）
            response_bs = _unpack(_call(recruiting_operator.list_resumes(
                request=None, page=1, page_size=20, keyword="小\\", job_name=None,
                status=None, fetched_at_from=None, fetched_at_to=None,
            )))

        assert response_pct["success"] is True
        assert [i["candidate_name"] for i in response_pct["data"]["items"]] == ["王%五"]
        assert response_bs["success"] is True
        assert response_bs["data"]["items"] == []

    def test_list_filter_by_job_and_status(self, temp_tenant_with_user):
        """job_name + status 组合筛选"""
        from src.api import recruiting_operator

        ctx = temp_tenant_with_user
        _insert_resume(ctx["tenant_id"], ctx["user_id"], candidate_name="A", job_name="后端", status="new")
        _insert_resume(ctx["tenant_id"], ctx["user_id"], candidate_name="B", job_name="后端", status="shortlisted")
        _insert_resume(ctx["tenant_id"], ctx["user_id"], candidate_name="C", job_name="前端", status="new")

        with _mock_tenant_ctx(ctx["tenant_id"]):
            response = _unpack(_call(recruiting_operator.list_resumes(
                request=None, page=1, page_size=20, keyword=None, job_name="后端",
                status="shortlisted", fetched_at_from=None, fetched_at_to=None,
            )))

        assert response["success"] is True
        items = response["data"]["items"]
        assert len(items) == 1
        assert items[0]["candidate_name"] == "B"

    def test_list_filter_by_fetched_at_range(self, temp_tenant_with_user):
        """fetched_at_from/to 日期区间筛选（YYYY-MM-DD）"""
        from src.api import recruiting_operator

        ctx = temp_tenant_with_user
        base = datetime(2026, 8, 10, 12, 0, 0)
        _insert_resume(ctx["tenant_id"], ctx["user_id"], candidate_name="早", fetched_at=base - timedelta(days=5))
        _insert_resume(ctx["tenant_id"], ctx["user_id"], candidate_name="中", fetched_at=base)
        _insert_resume(ctx["tenant_id"], ctx["user_id"], candidate_name="晚", fetched_at=base + timedelta(days=5))

        with _mock_tenant_ctx(ctx["tenant_id"]):
            response = _unpack(_call(recruiting_operator.list_resumes(
                request=None, page=1, page_size=20, keyword=None, job_name=None,
                status=None, fetched_at_from="2026-08-09", fetched_at_to="2026-08-11",
            )))

        assert response["success"] is True
        items = response["data"]["items"]
        assert [it["candidate_name"] for it in items] == ["中"]

    def test_list_invalid_date_returns_400(self, temp_tenant_with_user):
        """日期格式非法返回 400"""
        from src.api import recruiting_operator

        ctx = temp_tenant_with_user
        with _mock_tenant_ctx(ctx["tenant_id"]):
            response = _unpack(_call(recruiting_operator.list_resumes(
                request=None, page=1, page_size=20, keyword=None, job_name=None,
                status=None, fetched_at_from="bad-date", fetched_at_to=None,
            )))

        assert response["success"] is False

    def test_list_jobs_distinct(self, temp_tenant_with_user):
        """GET /resumes/jobs 返回 distinct 职位列表"""
        from src.api import recruiting_operator

        ctx = temp_tenant_with_user
        _insert_resume(ctx["tenant_id"], ctx["user_id"], candidate_name="A", job_name="后端")
        _insert_resume(ctx["tenant_id"], ctx["user_id"], candidate_name="B", job_name="后端")
        _insert_resume(ctx["tenant_id"], ctx["user_id"], candidate_name="C", job_name="前端")

        with _mock_tenant_ctx(ctx["tenant_id"]):
            response = _unpack(_call(recruiting_operator.list_resume_jobs(request=None)))

        assert response["success"] is True
        assert response["data"]["jobs"] == ["前端", "后端"]


# ============== 3. 详情 / 更新 / 删除 API ==============

class TestResumeDetailPatchDeleteAPI:
    """GET/PATCH/DELETE /resumes/{id} 测试"""

    def test_get_resume_detail_includes_ocr_and_images(self, temp_tenant_with_user):
        """详情含 ocr_text、images、candidate_info"""
        from src.api import recruiting_operator

        ctx = temp_tenant_with_user
        file_id = f"file_{uuid.uuid4().hex[:12]}"
        resume_id = _insert_resume(
            ctx["tenant_id"], ctx["user_id"],
            images=[{"file_id": file_id, "name": "简历截图.png"}],
        )

        with _mock_tenant_ctx(ctx["tenant_id"]):
            response = _unpack(_call(recruiting_operator.get_resume(resume_id, request=None)))

        assert response["success"] is True
        data = response["data"]
        assert data["id"] == resume_id
        assert data["ocr_text"] == "OCR 全文测试内容"
        assert data["images"] == [{"file_id": file_id, "name": "简历截图.png"}]
        assert data["candidate_info"]["学历"] == "本科"

    def test_get_resume_not_found_returns_404(self, temp_tenant_with_user):
        """详情查不存在 id 返回 404"""
        from src.api import recruiting_operator

        ctx = temp_tenant_with_user
        with _mock_tenant_ctx(ctx["tenant_id"]):
            response = _unpack(_call(recruiting_operator.get_resume(99999999, request=None)))

        assert response["success"] is False
        assert response["error"] == "简历不存在"

    def test_patch_status_flow(self, temp_tenant_with_user):
        """PATCH 状态流转 new -> viewed -> shortlisted，updated_at 刷新"""
        from src.api import recruiting_operator
        from src.db.database import get_db_connection

        ctx = temp_tenant_with_user
        resume_id = _insert_resume(ctx["tenant_id"], ctx["user_id"], status="new")

        for next_status in ("viewed", "shortlisted"):
            req = recruiting_operator.UpdateResumeRequest(status=next_status)
            with _mock_tenant_ctx(ctx["tenant_id"]):
                response = _unpack(_call(recruiting_operator.update_resume(resume_id, req, request=None)))
            assert response["success"] is True
            assert response["data"]["status"] == next_status

        # DB 校验 + updated_at 已刷新
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT status, updated_at FROM bs_recruiting_operator_resumes WHERE id = %s",
                (resume_id,),
            )
            row = cursor.fetchone()
            assert row["status"] == "shortlisted"
            assert row["updated_at"] is not None

    def test_patch_invalid_status_returns_400(self, temp_tenant_with_user):
        """PATCH 非法状态返回 400"""
        from src.api import recruiting_operator

        ctx = temp_tenant_with_user
        resume_id = _insert_resume(ctx["tenant_id"], ctx["user_id"])

        req = recruiting_operator.UpdateResumeRequest(status="hired")
        with _mock_tenant_ctx(ctx["tenant_id"]):
            response = _unpack(_call(recruiting_operator.update_resume(resume_id, req, request=None)))

        assert response["success"] is False

    def test_patch_remark_and_fields(self, temp_tenant_with_user):
        """PATCH remark / job_name / candidate_name / candidate_info（仅传的字段）"""
        from src.api import recruiting_operator

        ctx = temp_tenant_with_user
        resume_id = _insert_resume(ctx["tenant_id"], ctx["user_id"], candidate_name="旧名")

        req = recruiting_operator.UpdateResumeRequest(
            remark="技术面表现不错",
            job_name="资深后端",
            candidate_name="新名",
            candidate_info={"期望薪资": "30k"},
        )
        with _mock_tenant_ctx(ctx["tenant_id"]):
            response = _unpack(_call(recruiting_operator.update_resume(resume_id, req, request=None)))

        assert response["success"] is True
        data = response["data"]
        assert data["remark"] == "技术面表现不错"
        assert data["job_name"] == "资深后端"
        assert data["candidate_name"] == "新名"
        assert data["candidate_info"] == {"期望薪资": "30k"}

    def test_patch_not_found_returns_404(self, temp_tenant_with_user):
        """PATCH 不存在 id 返回 404"""
        from src.api import recruiting_operator

        ctx = temp_tenant_with_user
        req = recruiting_operator.UpdateResumeRequest(status="viewed")
        with _mock_tenant_ctx(ctx["tenant_id"]):
            response = _unpack(_call(recruiting_operator.update_resume(99999999, req, request=None)))

        assert response["success"] is False
        assert response["error"] == "简历不存在"

    def test_delete_resume_success(self, temp_tenant_with_user):
        """DELETE 删除简历成功"""
        from src.api import recruiting_operator
        from src.db.database import get_db_connection

        ctx = temp_tenant_with_user
        resume_id = _insert_resume(ctx["tenant_id"], ctx["user_id"])

        with _mock_tenant_ctx(ctx["tenant_id"]):
            response = _unpack(_call(recruiting_operator.delete_resume(resume_id, request=None)))

        assert response["success"] is True
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id FROM bs_recruiting_operator_resumes WHERE id = %s",
                (resume_id,),
            )
            assert cursor.fetchone() is None

    def test_delete_resume_not_found_returns_404(self, temp_tenant_with_user):
        """DELETE 不存在 id 返回 404"""
        from src.api import recruiting_operator

        ctx = temp_tenant_with_user
        with _mock_tenant_ctx(ctx["tenant_id"]):
            response = _unpack(_call(recruiting_operator.delete_resume(99999999, request=None)))

        assert response["success"] is False
        assert response["error"] == "简历不存在"


# ============== 租户隔离测试 ==============

class TestTenantIsolation:
    """租户隔离测试：A 租户不能访问 B 租户的数据"""

    def test_cross_tenant_invisible(self, temp_tenant_with_user):
        """B 租户列表看不到 A 的数据；详情/更新/删除 A 的记录返回 404"""
        from src.api import recruiting_operator
        from src.saas.db.tenant_db import TenantDB
        from src.db.database import get_db_connection

        ctx_a = temp_tenant_with_user
        resume_id_a = _insert_resume(ctx_a["tenant_id"], ctx_a["user_id"], candidate_name="A租户候选人")

        # 创建 B 租户
        tenant_code_b = f"T{uuid.uuid4().hex[:6].upper()}"
        tenant_b = TenantDB.create(
            company_name=f"招聘智能体测试租户B-{tenant_code_b}",
            tenant_code=tenant_code_b,
            contact_name="测试B",
            contact_phone="13800000001",
        )
        if not tenant_b:
            pytest.skip("无法创建测试租户B")
        tenant_id_b = tenant_b["tenant_id"]

        try:
            # 列表不可见
            with _mock_tenant_ctx(tenant_id_b):
                list_resp = _unpack(_call(recruiting_operator.list_resumes(
                    request=None, page=1, page_size=20, keyword="A租户候选人", job_name=None,
                    status=None, fetched_at_from=None, fetched_at_to=None,
                )))
                assert list_resp["success"] is True
                assert list_resp["data"]["total"] == 0

                # 详情 / 更新 / 删除均 404
                get_resp = _unpack(_call(recruiting_operator.get_resume(resume_id_a, request=None)))
                assert get_resp["success"] is False
                assert get_resp["error"] == "简历不存在"

                patch_req = recruiting_operator.UpdateResumeRequest(status="viewed")
                patch_resp = _unpack(_call(recruiting_operator.update_resume(resume_id_a, patch_req, request=None)))
                assert patch_resp["success"] is False

                del_resp = _unpack(_call(recruiting_operator.delete_resume(resume_id_a, request=None)))
                assert del_resp["success"] is False
        finally:
            TenantDB.delete(tenant_id_b)
            try:
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("DELETE FROM tenants WHERE tenant_id = %s", (tenant_id_b,))
                    conn.commit()
            except Exception:
                pass
