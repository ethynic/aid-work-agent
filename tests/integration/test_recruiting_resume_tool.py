"""
简历库服务层 + boss_resume_detail 工具集成测试

分层升级（2026-08-16）新增覆盖：
- 服务层直测（src/services/recruiting_resume_service.py）：create（两路图片）/list/get/update/delete
- create_resume_record_from_tool_result 契约：合法 payload 入库（别名宽容解析）/ 缺 candidate_name /
  images 结构不符 → ResumePayloadError 且不落任何库/盘数据
- BossResumeDetailTool 编排（不碰真设备/DB invocation，monkeypatch 基类 execute）：
  成功落库+紧凑摘要（不含 base64/ocr 全文）/ CLI 失败原样透传不落库 / payload 非法 RESUME_PAYLOAD_INVALID
- import 安全：proxy_tool ↔ services 无循环依赖

P0 防错名（2026-09-02，2026-09-03 修订为观测不拦截）覆盖：
- name_source 非可信来源（缺失 / 'ocr' / 其他值）→ 照常入库 + 服务端 warning 日志（观测不拦截：
  服务端更新不得强制客户端更新，姓名正确性由客户端流程保证）；'param' / 'dom' 合法入库

简历-职位匹配 Phase 1（2026-08-17）新增覆盖：
- 落库关联解析（设计 §4.1）：job_name 精确命中关联 job_id / 0 命中 job_id NULL + 摘要 warning /
  payload 带 job_id 直用（非本租户拒绝）/ 绝不自动创建职位
- create_resume_record 新增 job_id 参数校验（格式/租户归属）

简历-职位匹配 Phase 2（2026-08-17）新增覆盖：
- 工具落库后自动评分（设计 §3）：detail/batch 摘要带 match_score/match_status/match_summary，
  评分失败 match_score=None + match_note「未评分」；单份评分异常不影响其余份与工具成功返回
- 全模块 autouse stub 评分 LLM（不真调网），评分服务直测见 test_recruiting_match_service.py

既有 24 个端点测试见 test_recruiting_operator_apis.py（验证 API 薄壳化没变行为）。
"""

import base64
import json
import subprocess
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.local_tools.proxy_tool import (
    LOCAL_PROXY_TOOL_CLASSES,
    BossResumeBatchTool,
    BossResumeDetailTool,
    LocalToolProxyTool,
)
from src.services import recruiting_match_service
from src.services import recruiting_resume_service as resume_service
from src.services import resume_vl_service

pytestmark = pytest.mark.integration


# 1x1 透明 PNG，用于 base64 直传路测试
_TINY_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
    "AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


# ============== Fixture ==============

@pytest.fixture(scope="module", autouse=True)
def _ensure_tables():
    """模块级幂等建表（测试库可能未跑过服务启动初始化；jobs 先建——resumes.job_id 外键引用 jobs 表）"""
    from src.db.database import get_db_connection
    from src.services.recruiting_job_service import init_recruiting_job_tables
    from src.services.recruiting_resume_service import init_recruiting_operator_tables

    with get_db_connection() as conn:
        init_recruiting_job_tables(conn)
        init_recruiting_operator_tables(conn)
        conn.commit()


@pytest.fixture
def temp_tenant_with_user():
    """创建临时租户 + 测试用户，测试后清理（含职位与话术，Phase 1 关联测试会建职位）"""
    from src.db.database import get_db_connection
    from src.saas.db.tenant_db import TenantDB

    tenant_code = f"T{uuid.uuid4().hex[:6].upper()}"
    tenant = TenantDB.create(
        company_name=f"简历库服务层测试租户-{tenant_code}",
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


@pytest.fixture
def temp_storage_dir(tmp_path, monkeypatch):
    """把 base64 落盘目录指向临时目录（服务层已抽走落盘逻辑，patch 服务模块），避免污染仓库 storage/"""
    target = tmp_path / "recruiting"
    target.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "src.services.recruiting_resume_service.ensure_tenant_storage_dir",
        lambda tenant_id, scene: str(target),
    )
    return target


@pytest.fixture(autouse=True)
def _stub_match_llm(monkeypatch):
    """全模块自动 stub VL 评估层与文本评分 LLM（不真调网）。

    - VL 层（v2 主路径，2026-09-17 去 OCR 化）：切片返回单带；evaluate_resume 按「是否有
      职位上下文」定分（有 → 82 分 matched，无 → score=null 未评分），name_seen=传入姓名
      （姓名门必过）；识别费取价 0（integration 不测计费台账，工具层单测覆盖）
    - 文本评分 LLM：无图历史记录 re-evaluate 的 fallback 路径用（固定 82 分）
    """
    async def _fake_evaluate(bands, candidate_name, job_ctx=None, model_param=None):
        scored = job_ctx is not None
        return {
            "name_seen": candidate_name,
            "resume_summary": f"{candidate_name}：全栈工程师，5 年经验（VL stub）",
            "score": 82 if scored else None,
            "match_summary": "PHP/Laravel 经验匹配，本科，5 年经验" if scored else None,
            "key_info": {"education": "本科", "core_skills": ["PHP", "Laravel"]},
            "model": "GLM-5.3-Flash",
            "usage": None,
        }

    monkeypatch.setattr("src.services.resume_vl_service.slice_stitched_image", lambda b64: ["BAND-STUB"])
    monkeypatch.setattr("src.services.resume_vl_service.evaluate_resume", _fake_evaluate)
    monkeypatch.setattr("src.local_tools.proxy_tool.resume_recognition_price", lambda: 0.0)

    class _StubMatchGateway:
        async def _chat(self, **kwargs):
            return {"content": json.dumps({
                "score": 82,
                "match_summary": "PHP/Laravel 经验匹配，本科，5 年经验",
                "key_info": {"education": "本科", "core_skills": ["PHP", "Laravel"]},
            }, ensure_ascii=False), "usage": None}

        # 评分走 chat_no_thinking（关思考收口），stub 两个方法名都对齐
        chat = _chat
        chat_no_thinking = _chat

    monkeypatch.setattr(recruiting_match_service, "llm_gateway", _StubMatchGateway())


def _call(coro):
    """同步驱动 async 函数（独立新 event loop，避免跨文件事件循环状态污染）"""
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _count_resumes(tenant_id: str) -> int:
    """统计该租户简历记录数（fail-loud「不落库」断言用）"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) AS total FROM bs_recruiting_operator_resumes WHERE tenant_id = %s",
            (tenant_id,),
        )
        return cursor.fetchone()["total"]


def _count_jobs(tenant_id: str) -> int:
    """统计该租户职位数（「绝不自动创建职位」断言用）"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) AS total FROM bs_recruiting_operator_jobs WHERE tenant_id = %s",
            (tenant_id,),
        )
        return cursor.fetchone()["total"]


# 紧凑摘要基础字段集合（Phase 1 新增 job_id；未关联职位时附带 warning；
# Phase 2 新增评分三键 match_score/match_status/match_summary，评分失败附 match_note；
# v2 去 OCR 化：ocr_char_count → summary_preview（VL 人物总结前 80 字））
_BASE_SUMMARY_KEYS = {
    "resume_id", "candidate_name", "job_name", "job_id", "image_count", "summary_preview"
}
_MATCH_SUMMARY_KEYS = {"match_score", "match_status", "match_summary"}


# ============== 1. 服务层直测 ==============

class TestResumeServiceCrud:
    """recruiting_resume_service CRUD 直测（不经过 HTTP 层）"""

    def test_create_both_image_paths(self, temp_tenant_with_user, temp_storage_dir):
        """create：file_id 引用路 + base64 直传路合并，base64 落盘转 file_id"""
        ctx = temp_tenant_with_user
        file_id = f"file_{uuid.uuid4().hex[:12]}"
        record = resume_service.create_resume_record(
            ctx["tenant_id"], ctx["user_id"],
            candidate_name="李四",
            job_name="前端工程师",
            candidate_info={"学历": "硕士"},
            ocr_text="OCR 全文",
            images=[{"file_id": file_id, "name": "已上传截图.png"}],
            images_base64=[{"data": _TINY_PNG_BASE64, "name": "直传.png", "mime_type": "image/png"}],
            source="manual",
            fetched_at="2026-08-16T10:00:00",
        )
        assert record["candidate_name"] == "李四"
        assert record["source"] == "manual"
        assert record["status"] == "new"
        assert record["user_id"] == ctx["user_id"]
        assert record["candidate_info"] == {"学历": "硕士"}
        # base64 直传路优先合并：第 1 张为落盘生成的 file_id，第 2 张为引用路的 file_id
        assert len(record["images"]) == 2
        assert record["images"][1]["file_id"] == file_id
        b64_file_id = record["images"][0]["file_id"]
        assert b64_file_id.startswith("file_")
        # base64 图片已落盘且字节一致
        saved = list(temp_storage_dir.glob(f"{b64_file_id}.*"))
        assert len(saved) == 1 and saved[0].suffix == ".png"
        assert base64.b64decode(_TINY_PNG_BASE64) == saved[0].read_bytes()

    def test_create_validation_errors(self, temp_tenant_with_user):
        """create：非法 source / 空姓名抛 ValueError"""
        ctx = temp_tenant_with_user
        with pytest.raises(ValueError, match="来源值非法"):
            resume_service.create_resume_record(ctx["tenant_id"], ctx["user_id"],
                                                candidate_name="x", source="unknown")
        with pytest.raises(ValueError, match="候选人姓名不能为空"):
            resume_service.create_resume_record(ctx["tenant_id"], ctx["user_id"], candidate_name="  ")
        assert _count_resumes(ctx["tenant_id"]) == 0

    def test_list_get_update_delete(self, temp_tenant_with_user):
        """list（keyword 筛选 + 轻量不含 ocr_text）/ get / update / delete 全链路"""
        ctx = temp_tenant_with_user
        created = resume_service.create_resume_record(
            ctx["tenant_id"], ctx["user_id"],
            candidate_name="张小明", job_name="后端", ocr_text="OCR_FULLTEXT_UNIQUE_XYZ",
        )
        resume_id = created["id"]

        # list：keyword 命中且轻量
        data = resume_service.list_resumes(ctx["tenant_id"], page=1, page_size=20, keyword="小明")
        assert data["total"] == 1
        assert data["items"][0]["candidate_name"] == "张小明"
        assert all("ocr_text" not in it for it in data["items"])

        # get：详情含 ocr_text
        detail = resume_service.get_resume(ctx["tenant_id"], resume_id)
        assert detail["ocr_text"] == "OCR_FULLTEXT_UNIQUE_XYZ"

        # update：状态 + 备注
        updated = resume_service.update_resume(
            ctx["tenant_id"], resume_id, status="shortlisted", remark="有意向"
        )
        assert updated["status"] == "shortlisted"
        assert updated["remark"] == "有意向"
        with pytest.raises(ValueError, match="状态值非法"):
            resume_service.update_resume(ctx["tenant_id"], resume_id, status="hired")

        # delete
        assert resume_service.delete_resume(ctx["tenant_id"], resume_id) is True
        assert resume_service.get_resume(ctx["tenant_id"], resume_id) is None
        assert resume_service.delete_resume(ctx["tenant_id"], resume_id) is False

    def test_list_distinct_jobs(self, temp_tenant_with_user):
        """list_distinct_jobs：distinct 职位去重排序"""
        ctx = temp_tenant_with_user
        for name, job in (("A", "后端"), ("B", "后端"), ("C", "前端")):
            resume_service.create_resume_record(
                ctx["tenant_id"], ctx["user_id"], candidate_name=name, job_name=job
            )
        assert resume_service.list_distinct_jobs(ctx["tenant_id"]) == ["前端", "后端"]


# ============== 2. CLI 结果契约测试 ==============

class TestToolResultContract:
    """create_resume_record_from_tool_result：CLI payload → 入库契约"""

    def test_valid_payload_with_aliases(self, temp_tenant_with_user, temp_storage_dir):
        """合法 payload（全用别名）入库：base64 落盘 + 记录正确 + source=boss"""
        ctx = temp_tenant_with_user
        payload = {
            "name": "王五",                       # 别名 → candidate_name
            "name_source": "param",                # 姓名=显式入参（非 OCR 来源守门）
            "position": "资深后端",                 # 别名 → job_name
            "basic_info": {"学历": "本科", "工作年限": "5 年"},
            "text": "OCR_FULLTEXT_UNIQUE_XYZ",     # 别名 → ocr_text
            "screenshots": [                        # 别名 → images（mime 缺省 image/png）
                {"base64": _TINY_PNG_BASE64, "name": "简历截图1.png"},
                {"base64": f"data:image/png;base64,{_TINY_PNG_BASE64}", "mime_type": "image/png"},
            ],
        }
        record = resume_service.create_resume_record_from_tool_result(
            ctx["tenant_id"], ctx["user_id"], payload
        )
        assert record["candidate_name"] == "王五"
        assert record["job_name"] == "资深后端"
        assert record["source"] == "boss"
        assert record["status"] == "new"
        assert record["candidate_info"] == {"学历": "本科", "工作年限": "5 年"}
        assert record["ocr_text"] == "OCR_FULLTEXT_UNIQUE_XYZ"
        assert len(record["images"]) == 2
        assert [it["name"] for it in record["images"]] == ["简历截图1.png", record["images"][1]["name"]]
        # 两张截图均已落盘
        assert len(list(temp_storage_dir.iterdir())) == 2

    def test_missing_candidate_name_raises_and_stores_nothing(self, temp_tenant_with_user, temp_storage_dir):
        """缺 candidate_name 抛 ResumePayloadError，且不落任何库/盘数据"""
        ctx = temp_tenant_with_user
        with pytest.raises(resume_service.ResumePayloadError, match="候选人姓名"):
            resume_service.create_resume_record_from_tool_result(
                ctx["tenant_id"], ctx["user_id"],
                {"job_name": "后端", "images": [{"base64": _TINY_PNG_BASE64}]},
            )
        assert _count_resumes(ctx["tenant_id"]) == 0
        assert not list(temp_storage_dir.iterdir())

    @pytest.mark.parametrize("name_source", [
        None,        # 缺失（旧版 CLI 不带该字段）
        "ocr",       # 旧版 CLI 的 OCR 首行启发式猜名（P0 修复前）
        "whatever",  # 任意其他值
    ])
    def test_name_source_untrusted_accepted_with_warning(
        self, temp_tenant_with_user, temp_storage_dir, name_source
    ):
        """name_source 非可信来源（缺失/'ocr'/其他值）不拒绝入库，只记 warning（观测不拦截）。

        2026-09-03 修订：初版的白名单强控（ResumePayloadError）撤销——服务端更新不得强制
        客户端更新；姓名正确性由客户端流程保证（batch=DOM 配对 / detail=显式入参+交叉校验），
        服务端只记 warning 供运营观测旧客户端，功能永不因该字段而中断。
        """
        ctx = temp_tenant_with_user
        payload = {
            "candidate_name": "王五",
            "images": [{"base64": _TINY_PNG_BASE64, "mime_type": "image/png"}],
        }
        if name_source is not None:
            payload["name_source"] = name_source
        record = resume_service.create_resume_record_from_tool_result(
            ctx["tenant_id"], ctx["user_id"], payload
        )
        assert record["candidate_name"] == "王五"
        assert _count_resumes(ctx["tenant_id"]) == 1

    def test_name_source_dom_accepted(self, temp_tenant_with_user, temp_storage_dir):
        """name_source='dom'（卡片 DOM 配对，批量链路）合法入库；'param' 已由别名用例覆盖"""
        ctx = temp_tenant_with_user
        record = resume_service.create_resume_record_from_tool_result(
            ctx["tenant_id"], ctx["user_id"],
            {"candidate_name": "刘草威", "name_source": "dom",
             "images": [{"base64": _TINY_PNG_BASE64, "mime_type": "image/png"}]},
        )
        assert record["candidate_name"] == "刘草威"

    @pytest.mark.parametrize("images", [
        "not-a-list",                                  # 非列表
        [{"name": "缺 base64"}],                        # 缺 base64 字段
        ["not-a-dict"],                                 # 项非对象
    ])
    def test_bad_images_structure_raises(self, temp_tenant_with_user, temp_storage_dir, images):
        """images 结构不符抛 ResumePayloadError，且不落任何库/盘数据"""
        ctx = temp_tenant_with_user
        with pytest.raises(resume_service.ResumePayloadError):
            resume_service.create_resume_record_from_tool_result(
                ctx["tenant_id"], ctx["user_id"],
                {"candidate_name": "赵六", "name_source": "param", "images": images},
            )
        assert _count_resumes(ctx["tenant_id"]) == 0
        assert not list(temp_storage_dir.iterdir())

    def test_bad_basic_info_raises(self, temp_tenant_with_user):
        """basic_info 非对象抛 ResumePayloadError"""
        ctx = temp_tenant_with_user
        with pytest.raises(resume_service.ResumePayloadError, match="basic_info"):
            resume_service.create_resume_record_from_tool_result(
                ctx["tenant_id"], ctx["user_id"],
                {"candidate_name": "赵六", "name_source": "param", "basic_info": "不是对象"},
            )
        assert _count_resumes(ctx["tenant_id"]) == 0

    def test_resume_payload_error_is_value_error(self):
        """ResumePayloadError 兼容 ValueError（既有异常契约不破坏）"""
        assert issubclass(resume_service.ResumePayloadError, ValueError)

    def test_midway_image_failure_cleans_saved_files(
        self, temp_tenant_with_user, temp_storage_dir
    ):
        """中途失败（第 2 张非法 mime）：第 1 张已落盘文件被清理，禁止磁盘残留 + 无库记录"""
        ctx = temp_tenant_with_user
        with pytest.raises(resume_service.ResumePayloadError):
            resume_service.create_resume_record_from_tool_result(
                ctx["tenant_id"], ctx["user_id"],
                {
                    "candidate_name": "孙九",
                    "name_source": "param",
                    "images": [
                        # 第 1 张合法，先真实落盘；第 2 张非法 mime 触发中途失败
                        {"base64": _TINY_PNG_BASE64, "mime_type": "image/png"},
                        {"base64": _TINY_PNG_BASE64, "mime_type": "text/plain"},
                    ],
                },
            )
        assert _count_resumes(ctx["tenant_id"]) == 0
        # 半截入库清理：已落盘的第 1 张文件不残留
        assert not list(temp_storage_dir.iterdir())


# ============== 2b. 简历-职位关联解析（Phase 1，设计 §4.1） ==============


class TestJobLinkResolution:
    """落库关联解析：payload 带 job_id 直用（校验租户）/ job_name 精确匹配 / 绝不自动创建职位"""

    def test_job_name_exact_match_links_job_id(self, temp_tenant_with_user, temp_storage_dir):
        """job_name 精确命中租户内唯一职位 → 关联 job_id，无 warning"""
        from src.services import recruiting_job_service

        ctx = temp_tenant_with_user
        job = recruiting_job_service.create_job(ctx["tenant_id"], job_name="PHP后端工程师")
        record = resume_service.create_resume_record_from_tool_result(
            ctx["tenant_id"], ctx["user_id"],
            {"candidate_name": "张三", "name_source": "param", "job_name": "PHP后端工程师",
             "images": [{"base64": _TINY_PNG_BASE64, "mime_type": "image/png"}]},
        )
        assert record["job_id"] == job["id"]
        assert record["job_name"] == "PHP后端工程师"
        assert "job_warning" not in record

    def test_job_name_zero_match_keeps_null_with_warning(self, temp_tenant_with_user, temp_storage_dir):
        """job_name 0 命中 → job_id=NULL、job_name 原文保留 + warning；绝不自动创建职位"""
        ctx = temp_tenant_with_user
        record = resume_service.create_resume_record_from_tool_result(
            ctx["tenant_id"], ctx["user_id"],
            {"candidate_name": "李四", "name_source": "param", "job_name": "不存在的职位",
             "images": [{"base64": _TINY_PNG_BASE64, "mime_type": "image/png"}]},
        )
        assert record["job_id"] is None
        assert record["job_name"] == "不存在的职位"  # 原文保留
        assert "未关联职位" in record["job_warning"]
        assert "请在职位管理核对" in record["job_warning"]
        # 无自动建职位副作用
        assert _count_jobs(ctx["tenant_id"]) == 0

    def test_payload_job_id_direct_use_and_name_backfill(self, temp_tenant_with_user, temp_storage_dir):
        """payload 带 job_id → 校验本租户后直接用；job_name 缺省回填职位规范名"""
        from src.services import recruiting_job_service

        ctx = temp_tenant_with_user
        job = recruiting_job_service.create_job(ctx["tenant_id"], job_name="资深 Laravel 工程师")
        record = resume_service.create_resume_record_from_tool_result(
            ctx["tenant_id"], ctx["user_id"],
            {"candidate_name": "王五", "name_source": "param", "job_id": job["id"],
             "images": [{"base64": _TINY_PNG_BASE64, "mime_type": "image/png"}]},
        )
        assert record["job_id"] == job["id"]
        assert record["job_name"] == "资深 Laravel 工程师"  # 回填显示冗余
        assert "job_warning" not in record

    def test_payload_job_id_cross_tenant_rejected(self, temp_tenant_with_user, temp_storage_dir):
        """payload 带 job_id 但职位属另一租户 → ResumePayloadError 拒绝入库，不落库不建职位"""
        from src.db.database import get_db_connection
        from src.saas.db.tenant_db import TenantDB
        from src.services import recruiting_job_service

        ctx_a = temp_tenant_with_user
        job_a = recruiting_job_service.create_job(ctx_a["tenant_id"], job_name="A租户职位")

        tenant_b = TenantDB.create(
            company_name=f"简历关联B租户-{uuid.uuid4().hex[:6].upper()}",
            tenant_code=f"T{uuid.uuid4().hex[:6].upper()}",
            contact_name="测试B", contact_phone="13800000001",
        )
        if not tenant_b:
            pytest.skip("无法创建测试租户B")
        tenant_id_b = tenant_b["tenant_id"]
        try:
            with pytest.raises(resume_service.ResumePayloadError, match="不属于本租户"):
                resume_service.create_resume_record_from_tool_result(
                    tenant_id_b, "u_b",
                    {"candidate_name": "赵六", "name_source": "param", "job_id": job_a["id"],
                     "images": [{"base64": _TINY_PNG_BASE64, "mime_type": "image/png"}]},
                )
            assert _count_resumes(tenant_id_b) == 0
            assert _count_jobs(tenant_id_b) == 0  # 无自动建职位
        finally:
            TenantDB.delete(tenant_id_b)
            try:
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("DELETE FROM tenants WHERE tenant_id = %s", (tenant_id_b,))
                    conn.commit()
            except Exception:
                pass

    def test_payload_job_id_bad_format_rejected(self, temp_tenant_with_user, temp_storage_dir):
        """payload job_id 格式非法 → ResumePayloadError，不落库"""
        ctx = temp_tenant_with_user
        with pytest.raises(resume_service.ResumePayloadError, match="格式非法"):
            resume_service.create_resume_record_from_tool_result(
                ctx["tenant_id"], ctx["user_id"],
                {"candidate_name": "孙七", "name_source": "param", "job_id": "not-a-uuid",
                 "images": [{"base64": _TINY_PNG_BASE64, "mime_type": "image/png"}]},
            )
        assert _count_resumes(ctx["tenant_id"]) == 0

    def test_create_resume_record_job_id_param_validation(self, temp_tenant_with_user, temp_storage_dir):
        """create_resume_record 直传 job_id：合法关联 / 非法格式 / 非本租户职位三路"""
        from src.services import recruiting_job_service

        ctx = temp_tenant_with_user
        job = recruiting_job_service.create_job(ctx["tenant_id"], job_name="参数校验职位")

        ok = resume_service.create_resume_record(
            ctx["tenant_id"], ctx["user_id"],
            candidate_name="周八", job_id=job["id"], source="manual",
        )
        assert ok["job_id"] == job["id"]

        with pytest.raises(ValueError, match="格式非法"):
            resume_service.create_resume_record(
                ctx["tenant_id"], ctx["user_id"], candidate_name="吴九", job_id="xxx",
            )
        with pytest.raises(ValueError, match="不属于本租户"):
            resume_service.create_resume_record(
                ctx["tenant_id"], ctx["user_id"],
                candidate_name="郑十", job_id=str(uuid.uuid4()),  # 随机 UUID（非本租户职位）
            )
        assert _count_resumes(ctx["tenant_id"]) == 1  # 仅第一份入库


# ============== 3. 工具编排测试 ==============

def _cli_success_result(payload):
    """构造基类 execute 的假成功结果（对应 _map_terminal 的 succeeded 分支）"""
    return {
        "success": True,
        "code": None,
        "message": "读取简历详情执行成功",
        "effect": "none",
        "data": payload,
        "invocation_id": "inv-test-1",
    }


class TestBossResumeDetailToolOrchestration:
    """BossResumeDetailTool 编排（monkeypatch 基类 execute，不碰真设备/DB invocation）"""

    def test_success_stores_resume_and_returns_compact_summary(
        self, temp_tenant_with_user, temp_storage_dir
    ):
        """CLI 成功：落库 + 返回 data 只含紧凑摘要（不含 base64/OCR 全文），message 含姓名"""
        ctx = temp_tenant_with_user
        payload = {
            "candidate_name": "张三",
            "name_source": "param",
            "job_name": "后端开发",
            "basic_info": {"学历": "本科"},
            "ocr_text": "OCR_FULLTEXT_UNIQUE_XYZ" * 10,
            "images": [
                {"base64": _TINY_PNG_BASE64, "name": "s1.png", "mime_type": "image/png"},
                {"base64": _TINY_PNG_BASE64, "name": "s2.png", "mime_type": "image/png"},
                {"base64": _TINY_PNG_BASE64, "name": "s3.png", "mime_type": "image/png"},
            ],
        }
        fake = _cli_success_result(payload)
        with patch.object(LocalToolProxyTool, "execute", new=AsyncMock(return_value=fake)):
            result = _call(BossResumeDetailTool().execute(
                _trusted_tenant_id=ctx["tenant_id"], _trusted_user_id=ctx["user_id"],
            ))

        assert result["success"] is True
        assert result["invocation_id"] == "inv-test-1"
        # 紧凑摘要：基础字段集合 + 未关联职位 warning（该租户无「后端开发」职位）
        # + Phase 2 评分三键（job_name 存在 → 评分执行，stub 固定 82 分 → matched）
        assert set(result["data"].keys()) == _BASE_SUMMARY_KEYS | {"warning"} | _MATCH_SUMMARY_KEYS
        assert result["data"]["candidate_name"] == "张三"
        assert result["data"]["job_name"] == "后端开发"
        assert result["data"]["job_id"] is None
        assert "未关联职位" in result["data"]["warning"]
        assert result["data"]["image_count"] == 3
        assert result["data"]["summary_preview"].startswith("张三：全栈工程师")
        assert result["data"]["match_score"] == 82
        assert result["data"]["match_status"] == "matched"
        assert result["data"]["match_summary"]
        # 图片字节与 OCR 全文绝不进 LLM 上下文
        serialized = json.dumps(result, ensure_ascii=False)
        assert _TINY_PNG_BASE64 not in serialized
        assert "OCR_FULLTEXT_UNIQUE_XYZ" not in serialized
        # message 中文摘要含姓名 / 职位 / 截图张数
        assert "张三" in result["message"]
        assert "后端开发" in result["message"]
        assert "3" in result["message"]
        # 已真实落库（source=boss）且截图落盘；评分四列已回写（未关联职位 → 阈值默认 70）；
        # v2：payload 携带的 ocr_text 已被丢弃（文本唯一来源=服务端 VL），resume_summary 入库
        record = resume_service.get_resume(ctx["tenant_id"], result["data"]["resume_id"])
        assert record["source"] == "boss"
        assert record["ocr_text"] is None
        assert record["resume_summary"].startswith("张三：全栈工程师")
        assert len(record["images"]) == 3
        assert record["match_score"] == 82
        assert record["match_status"] == "matched"
        assert record["key_info"]["education"] == "本科"
        assert len(list(temp_storage_dir.iterdir())) == 3

    def test_success_keeps_unknown_effect_notice(self, temp_tenant_with_user, temp_storage_dir):
        """CLI 成功但 effect=unknown：摘要覆写 message 后仍保留「实际效果未知」提示（防 LLM 建议重试）"""
        from src.local_tools.proxy_tool import UNKNOWN_EFFECT_NOTICE

        ctx = temp_tenant_with_user
        payload = {"candidate_name": "李四", "name_source": "param",
                   "images": [
                       {"base64": _TINY_PNG_BASE64, "mime_type": "image/png"},
                   ]}
        fake = _cli_success_result(payload)
        fake["effect"] = "unknown"
        fake["message"] = f"读取简历详情执行成功；{UNKNOWN_EFFECT_NOTICE}"
        with patch.object(LocalToolProxyTool, "execute", new=AsyncMock(return_value=fake)):
            result = _call(BossResumeDetailTool().execute(
                _trusted_tenant_id=ctx["tenant_id"], _trusted_user_id=ctx["user_id"],
            ))

        assert result["success"] is True
        assert "李四" in result["message"]
        assert UNKNOWN_EFFECT_NOTICE in result["message"]

    def test_cli_failure_passthrough_no_store(self, temp_tenant_with_user, temp_storage_dir):
        """CLI 失败（success=False）：code/message 原样透传，不落库不落盘"""
        ctx = temp_tenant_with_user
        fake = {
            "success": False,
            "code": "NOT_LOGGED_IN",
            "message": "BOSS 直聘未登录",
            "effect": None,
            "data": None,
            "invocation_id": "inv-test-2",
        }
        with patch.object(LocalToolProxyTool, "execute", new=AsyncMock(return_value=fake)):
            result = _call(BossResumeDetailTool().execute(
                _trusted_tenant_id=ctx["tenant_id"], _trusted_user_id=ctx["user_id"],
            ))

        assert result == fake
        assert _count_resumes(ctx["tenant_id"]) == 0
        assert not list(temp_storage_dir.iterdir())

    def test_cli_failure_data_stripped_from_context(self, temp_tenant_with_user, temp_storage_dir):
        """CLI 失败时 data 里即使夹带部分截图 base64 也绝不进 LLM 上下文（data 置 None）"""
        ctx = temp_tenant_with_user
        fake = {
            "success": False,
            "code": "PAYWALL",
            "message": "本机执行失败：需会员才能查看完整简历",
            "effect": "none",
            "data": {"partial_screenshot": _TINY_PNG_BASE64},
            "invocation_id": "inv-test-3",
        }
        with patch.object(LocalToolProxyTool, "execute", new=AsyncMock(return_value=fake)):
            result = _call(BossResumeDetailTool().execute(
                _trusted_tenant_id=ctx["tenant_id"], _trusted_user_id=ctx["user_id"],
            ))

        # code/message 透传（message 已带失败原因），data 一律置 None
        assert result["success"] is False
        assert result["code"] == "PAYWALL"
        assert result["invocation_id"] == "inv-test-3"
        assert result["data"] is None
        serialized = json.dumps(result, ensure_ascii=False)
        assert _TINY_PNG_BASE64 not in serialized
        assert _count_resumes(ctx["tenant_id"]) == 0
        assert not list(temp_storage_dir.iterdir())

    def test_invalid_payload_returns_resume_payload_invalid_no_store(
        self, temp_tenant_with_user, temp_storage_dir
    ):
        """payload 非法（缺 candidate_name）：RESUME_PAYLOAD_INVALID + 中文指引，不落库不落盘"""
        ctx = temp_tenant_with_user
        fake = _cli_success_result({"images": [{"base64": _TINY_PNG_BASE64}]})
        with patch.object(LocalToolProxyTool, "execute", new=AsyncMock(return_value=fake)):
            result = _call(BossResumeDetailTool().execute(
                _trusted_tenant_id=ctx["tenant_id"], _trusted_user_id=ctx["user_id"],
            ))

        assert result["success"] is False
        assert result["code"] == "RESUME_PAYLOAD_INVALID"
        assert result["invocation_id"] == "inv-test-1"
        # v2：缺 candidate_name 在姓名门前即拦截（比较基准缺失，不浪费一次评估调用）
        assert "candidate_name" in result["message"]
        assert result["data"] is None
        assert _count_resumes(ctx["tenant_id"]) == 0
        assert not list(temp_storage_dir.iterdir())

    def test_unexpected_store_error_returns_store_failed_no_leak(
        self, temp_tenant_with_user, temp_storage_dir
    ):
        """入库意外异常（非契约问题，如 DB 故障）：RESUME_STORE_FAILED 固定文案，
        不泄漏 payload/base64/OCR 全文，不落库"""
        ctx = temp_tenant_with_user
        payload = {
            "candidate_name": "李雷",
            "name_source": "param",
            "ocr_text": "OCR_FULLTEXT_UNIQUE_XYZ",
            "images": [{"base64": _TINY_PNG_BASE64, "mime_type": "image/png"}],
        }
        fake = _cli_success_result(payload)
        with patch.object(LocalToolProxyTool, "execute", new=AsyncMock(return_value=fake)), \
             patch.object(resume_service, "create_resume_record_from_tool_result",
                          side_effect=RuntimeError("db down")):
            result = _call(BossResumeDetailTool().execute(
                _trusted_tenant_id=ctx["tenant_id"], _trusted_user_id=ctx["user_id"],
            ))

        assert result["success"] is False
        assert result["code"] == "RESUME_STORE_FAILED"
        assert result["invocation_id"] == "inv-test-1"
        assert result["data"] is None
        assert result["message"] == "简历读取成功但入库失败，请稍后重试或联系管理员"
        serialized = json.dumps(result, ensure_ascii=False)
        assert _TINY_PNG_BASE64 not in serialized
        assert "OCR_FULLTEXT_UNIQUE_XYZ" not in serialized
        assert _count_resumes(ctx["tenant_id"]) == 0
        assert not list(temp_storage_dir.iterdir())


# ============== 3b. 批量工具编排测试 ==============


class TestBossResumeBatchToolOrchestration:
    """BossResumeBatchTool 编排（monkeypatch 基类 execute，不碰真设备/DB invocation）"""

    @staticmethod
    def _batch_success_result(payloads, cli_failures=None):
        """构造基类 execute 的假批量成功结果（data.resumes + data.failures + attempted）"""
        return _cli_success_result({
            "resumes": payloads,
            "failures": cli_failures or [],
            "attempted": len(payloads),
        })

    def test_two_valid_payloads_stored_and_compact_summaries(
        self, temp_tenant_with_user, temp_storage_dir
    ):
        """CLI 成功（2 份合法 payload）：2 条入库 + summaries 正确 + 返回 data 无 base64/OCR 全文"""
        ctx = temp_tenant_with_user
        payloads = [
            {
                "candidate_name": "刘草威",
                "name_source": "dom",
                "job_name": "PHP开发工程师",
                "ocr_text": "BATCH_OCR_FULLTEXT_UNIQUE_XYZ_1" * 10,
                "images": [{"base64": _TINY_PNG_BASE64, "name": "s1.png", "mime_type": "image/png"}],
            },
            {
                "candidate_name": "张三丰",
                "name_source": "dom",
                "ocr_text": "BATCH_OCR_FULLTEXT_UNIQUE_XYZ_2",
                "images": [{"base64": _TINY_PNG_BASE64, "name": "s2.png", "mime_type": "image/png"}],
            },
        ]
        fake = self._batch_success_result(payloads)
        with patch.object(LocalToolProxyTool, "execute", new=AsyncMock(return_value=fake)):
            result = _call(BossResumeBatchTool().execute(
                _trusted_tenant_id=ctx["tenant_id"], _trusted_user_id=ctx["user_id"],
            ))

        assert result["success"] is True
        assert result["invocation_id"] == "inv-test-1"
        assert result["data"]["failures"] == []
        summaries = result["data"]["resumes"]
        assert len(summaries) == 2
        # 每份摘要 = 基础字段（与 BossResumeDetailTool 同款紧凑摘要）+ Phase 2 评分三键，
        # 至多附带一个 warning（未关联职位）与 match_note（评分失败/跳过）；
        # 第 1 份有 job_name → 评分执行（stub 82 分 matched）；
        # 第 2 份无 job_name → skipped（match_score=None + match_note「未评分」）
        for s in summaries:
            assert _BASE_SUMMARY_KEYS <= set(s.keys())
            extra = set(s.keys()) - _BASE_SUMMARY_KEYS
            if s.get("match_score") is None:
                assert extra <= {"warning", "match_score", "match_status", "match_summary", "match_note"}
                assert s["match_note"] == "未评分"
            else:
                assert extra <= {"warning", "match_score", "match_status", "match_summary"}
                assert "match_note" not in s
        assert [s["candidate_name"] for s in summaries] == ["刘草威", "张三丰"]
        assert summaries[0]["match_score"] == 82
        assert summaries[0]["match_status"] == "matched"
        assert summaries[1]["match_score"] is None
        assert summaries[1]["match_note"] == "未评分"
        assert summaries[0]["job_name"] == "PHP开发工程师"
        assert summaries[0]["job_id"] is None
        assert "未关联职位" in summaries[0]["warning"]
        assert "warning" not in summaries[1]
        assert summaries[0]["summary_preview"].startswith("刘草威：全栈工程师")
        assert summaries[0]["image_count"] == 1
        # 图片字节与 OCR 全文绝不进 LLM 上下文
        serialized = json.dumps(result, ensure_ascii=False)
        assert _TINY_PNG_BASE64 not in serialized
        assert "BATCH_OCR_FULLTEXT_UNIQUE_XYZ_1" not in serialized
        assert "BATCH_OCR_FULLTEXT_UNIQUE_XYZ_2" not in serialized
        # message 含入库份数与姓名
        assert "2 份" in result["message"]
        assert "刘草威" in result["message"] and "张三丰" in result["message"]
        # 已真实落库（2 条，source=boss）且截图落盘
        assert _count_resumes(ctx["tenant_id"]) == 2
        for s in summaries:
            record = resume_service.get_resume(ctx["tenant_id"], s["resume_id"])
            assert record["source"] == "boss"
        assert len(list(temp_storage_dir.iterdir())) == 2

    def test_failed_payload_does_not_block_valid_one(
        self, temp_tenant_with_user, temp_storage_dir
    ):
        """单份 payload 非法（缺 candidate_name）：记入 failures 不中断循环，成功份照常入库"""
        ctx = temp_tenant_with_user
        payloads = [
            {"images": [{"base64": _TINY_PNG_BASE64}]},  # 第 1 份缺 candidate_name
            {
                "candidate_name": "康嘉润",
                "name_source": "dom",
                "job_name": "后端开发",
                "ocr_text": "BATCH_OCR_FULLTEXT_UNIQUE_XYZ_3",
                "images": [{"base64": _TINY_PNG_BASE64, "mime_type": "image/png"}],
            },
        ]
        fake = self._batch_success_result(payloads)
        with patch.object(LocalToolProxyTool, "execute", new=AsyncMock(return_value=fake)):
            result = _call(BossResumeBatchTool().execute(
                _trusted_tenant_id=ctx["tenant_id"], _trusted_user_id=ctx["user_id"],
            ))

        # 部分成功 → success=True；成功份入库、失败份在 failures
        assert result["success"] is True
        summaries = result["data"]["resumes"]
        assert [s["candidate_name"] for s in summaries] == ["康嘉润"]
        failures = result["data"]["failures"]
        assert len(failures) == 1
        assert failures[0]["name"] is None
        assert "候选人姓名" in failures[0]["error"]
        assert "1 份失败" in result["message"]
        assert _count_resumes(ctx["tenant_id"]) == 1
        assert len(list(temp_storage_dir.iterdir())) == 1

    def test_empty_resumes_returns_payload_invalid_no_store(
        self, temp_tenant_with_user, temp_storage_dir
    ):
        """CLI 成功但 resumes 为空（一份都没读到）：RESUME_PAYLOAD_INVALID，
        failures 现场随 message/data 透出（2026-09-10 整改：不再置 None 吞证据），不落库不落盘"""
        ctx = temp_tenant_with_user
        fake = _cli_success_result({"resumes": [], "failures": [
            {"name": None, "error": "未能确定候选人姓名（卡片 DOM 配对失败）：已跳过不入库"},
        ], "attempted": 1})
        with patch.object(LocalToolProxyTool, "execute", new=AsyncMock(return_value=fake)):
            result = _call(BossResumeBatchTool().execute(
                _trusted_tenant_id=ctx["tenant_id"], _trusted_user_id=ctx["user_id"],
            ))

        assert result["success"] is False
        assert result["code"] == "RESUME_PAYLOAD_INVALID"
        # 紧凑失败现场：attempted + failures 摘要（纯文本，绝无 base64/OCR 全文）
        assert result["data"]["attempted"] == 1
        assert result["data"]["failures"][0]["error"].startswith("未能确定候选人姓名")
        assert "未能确定候选人姓名" in result["message"]
        assert "尝试 1 张卡片" in result["message"]
        data_dump = json.dumps(result["data"], ensure_ascii=False)
        assert "base64" not in data_dump
        assert "OCR_FULLTEXT" not in data_dump
        assert _count_resumes(ctx["tenant_id"]) == 0
        assert not list(temp_storage_dir.iterdir())

    def test_empty_resumes_data_missing_still_invalid(
        self, temp_tenant_with_user, temp_storage_dir
    ):
        """CLI 结果缺 data（旧客户端/异常路径）：仍 RESUME_PAYLOAD_INVALID，data 为空摘要不炸"""
        ctx = temp_tenant_with_user
        fake = _cli_success_result({})
        with patch.object(LocalToolProxyTool, "execute", new=AsyncMock(return_value=fake)):
            result = _call(BossResumeBatchTool().execute(
                _trusted_tenant_id=ctx["tenant_id"], _trusted_user_id=ctx["user_id"],
            ))

        assert result["success"] is False
        assert result["code"] == "RESUME_PAYLOAD_INVALID"
        assert result["data"] == {"attempted": None, "failures": []}
        assert "客户端未返回尝试数" in result["message"]
        assert _count_resumes(ctx["tenant_id"]) == 0
        assert not list(temp_storage_dir.iterdir())

    def test_all_store_failures_returns_store_failed(
        self, temp_tenant_with_user, temp_storage_dir
    ):
        """全部入库失败（DB 故障）：RESUME_STORE_FAILED，不泄漏 base64/OCR 全文"""
        ctx = temp_tenant_with_user
        payloads = [
            {"candidate_name": "李雷", "name_source": "dom", "ocr_text": "BATCH_OCR_X",
             "images": [{"base64": _TINY_PNG_BASE64, "mime_type": "image/png"}]},
            {"candidate_name": "韩梅梅", "name_source": "dom", "ocr_text": "BATCH_OCR_Y",
             "images": [{"base64": _TINY_PNG_BASE64, "mime_type": "image/png"}]},
        ]
        fake = self._batch_success_result(payloads)
        with patch.object(LocalToolProxyTool, "execute", new=AsyncMock(return_value=fake)), \
             patch.object(resume_service, "create_resume_record_from_tool_result",
                          side_effect=RuntimeError("db down")):
            result = _call(BossResumeBatchTool().execute(
                _trusted_tenant_id=ctx["tenant_id"], _trusted_user_id=ctx["user_id"],
            ))

        assert result["success"] is False
        assert result["code"] == "RESUME_STORE_FAILED"
        assert result["data"] is None
        serialized = json.dumps(result, ensure_ascii=False)
        assert _TINY_PNG_BASE64 not in serialized
        assert "BATCH_OCR_X" not in serialized
        assert _count_resumes(ctx["tenant_id"]) == 0
        assert not list(temp_storage_dir.iterdir())


# ============== 3c. 入库后自动评分接入（Phase 2，设计 §3） ==============


class TestMatchEvaluationIntegration:
    """v2 评分合并进 VL 评估：无职位 → score=null 摘要注明未评分；
    单份 VL 评估失败该份进 failures（不入库不扣费），不影响其余份"""

    def test_detail_summary_carries_match_note_when_not_scored(
        self, temp_tenant_with_user, temp_storage_dir
    ):
        """detail：无职位上下文（payload 无 job_id/job_name）→ score=null，
        摘要 match_score=None + match_note「未评分」，简历照常入库（v1「未关联职位跳过评分」语义沿袭）"""
        ctx = temp_tenant_with_user

        payload = {
            "candidate_name": "评分失败者",
            "name_source": "param",
            "images": [{"base64": _TINY_PNG_BASE64, "mime_type": "image/png"}],
        }
        fake = _cli_success_result(payload)
        with patch.object(LocalToolProxyTool, "execute", new=AsyncMock(return_value=fake)):
            result = _call(BossResumeDetailTool().execute(
                _trusted_tenant_id=ctx["tenant_id"], _trusted_user_id=ctx["user_id"],
            ))

        assert result["success"] is True
        assert result["data"]["match_score"] is None
        assert result["data"]["match_status"] is None
        assert result["data"]["match_summary"] is None
        assert result["data"]["match_note"] == "未评分"
        # 简历本体照常入库，评分列留 NULL；总结已入库（VL stub）
        record = resume_service.get_resume(ctx["tenant_id"], result["data"]["resume_id"])
        assert record["candidate_name"] == "评分失败者"
        assert record["match_score"] is None
        assert record["resume_summary"]

    def test_batch_single_vl_failure_goes_to_failures_others_stored(
        self, temp_tenant_with_user, temp_storage_dir, monkeypatch
    ):
        """batch：单份 VL 评估抛异常 → 该份 RESUME_VL_FAILED 进 failures（不入库不扣费），
        其余份照常评分入库；工具成功返回"""
        ctx = temp_tenant_with_user

        async def fake_evaluate(bands, candidate_name, job_ctx=None, model_param=None):
            if candidate_name == "评估失败者":
                raise resume_vl_service.ResumeVLError("简历 VL 评估失败（重试后仍失败）：api down")
            return {
                "name_seen": candidate_name,
                "resume_summary": f"{candidate_name}：后端开发 5 年（VL stub）",
                "score": 90, "match_summary": "高度匹配", "key_info": None,
                "model": "GLM-5.3-Flash", "usage": None,
            }

        monkeypatch.setattr("src.services.resume_vl_service.evaluate_resume", fake_evaluate)
        # 覆写 autouse 的识别费 0：本用例验证「姓名门通过即扣」不影响返回（落账打桩）
        monkeypatch.setattr("src.local_tools.proxy_tool.resume_recognition_price", lambda: 1.0)
        monkeypatch.setattr(
            "src.local_tools.proxy_tool.ClientUsageLogDB.record_tool_usage",
            staticmethod(lambda **k: {"credit_cost": k["credit_cost"], "balance_after": 1.0}),
        )

        payloads = [
            {"candidate_name": "评估失败者", "name_source": "param", "job_name": "后端开发",
             "images": [{"base64": _TINY_PNG_BASE64, "mime_type": "image/png"}]},
            {"candidate_name": "评估成功者", "name_source": "param", "job_name": "后端开发",
             "images": [{"base64": _TINY_PNG_BASE64, "mime_type": "image/png"}]},
        ]
        fake = TestBossResumeBatchToolOrchestration._batch_success_result(payloads)
        with patch.object(LocalToolProxyTool, "execute", new=AsyncMock(return_value=fake)):
            result = _call(BossResumeBatchTool().execute(
                _trusted_tenant_id=ctx["tenant_id"], _trusted_user_id=ctx["user_id"],
            ))

        assert result["success"] is True
        # v2 语义：VL 评估失败 = 该份失败（不入库不扣费），进 failures 而非静默跳过
        assert len(result["data"]["failures"]) == 1
        assert result["data"]["failures"][0]["name"] == "评估失败者"
        assert "简历 VL 评估失败" in result["data"]["failures"][0]["error"]
        summaries = {s["candidate_name"]: s for s in result["data"]["resumes"]}
        assert summaries["评估成功者"]["match_score"] == 90
        assert summaries["评估成功者"]["match_status"] == "matched"
        assert "match_note" not in summaries["评估成功者"]
        assert _count_resumes(ctx["tenant_id"]) == 1  # 失败份不入库


# ============== 4. import 安全 / 注册 ==============

class TestImportSafety:
    """import 安全：proxy_tool ↔ services 无循环依赖"""

    def test_imports_and_registration(self):
        """工具类进 LOCAL_PROXY_TOOL_CLASSES，受信清单放行 boss_resume_detail / boss_resume_batch"""
        from src.local_tools import catalog

        assert BossResumeDetailTool in LOCAL_PROXY_TOOL_CLASSES
        assert BossResumeDetailTool.name == "boss_resume_detail"
        assert catalog.is_tool_allowed("boss-recruiting", "boss_resume_detail") is True
        assert BossResumeBatchTool in LOCAL_PROXY_TOOL_CLASSES
        assert BossResumeBatchTool.name == "boss_resume_batch"
        assert catalog.is_tool_allowed("boss-recruiting", "boss_resume_batch") is True

    def test_services_do_not_import_local_tools(self):
        """子进程验证：先 import 服务层不拉起 src.local_tools（防循环依赖回归）"""
        repo_root = Path(__file__).resolve().parents[2]
        code = (
            "import sys; import src.services.recruiting_resume_service as svc; "
            "leaked = [m for m in sys.modules if m == 'src.local_tools' or m.startswith('src.local_tools.')]; "
            "assert not leaked, f'services 不应 import local_tools: {leaked}'; "
            "from src.services.recruiting_resume_service import create_resume_record; "
            "from src.local_tools.proxy_tool import BossResumeDetailTool; "
            "assert callable(create_resume_record) and BossResumeDetailTool.name == 'boss_resume_detail'; "
            "print('import ok')"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=str(repo_root),
            capture_output=True, text=True, timeout=120,
        )
        assert result.returncode == 0, result.stderr
        assert "import ok" in result.stdout
