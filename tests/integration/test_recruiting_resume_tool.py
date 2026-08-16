"""
简历库服务层 + boss_resume_detail 工具集成测试

分层升级（2026-08-16）新增覆盖：
- 服务层直测（src/services/recruiting_resume_service.py）：create（两路图片）/list/get/update/delete
- create_resume_record_from_tool_result 契约：合法 payload 入库（别名宽容解析）/ 缺 candidate_name /
  images 结构不符 → ResumePayloadError 且不落任何库/盘数据
- BossResumeDetailTool 编排（不碰真设备/DB invocation，monkeypatch 基类 execute）：
  成功落库+紧凑摘要（不含 base64/ocr 全文）/ CLI 失败原样透传不落库 / payload 非法 RESUME_PAYLOAD_INVALID
- import 安全：proxy_tool ↔ services 无循环依赖

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
    BossResumeDetailTool,
    LocalToolProxyTool,
)
from src.services import recruiting_resume_service as resume_service

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
    from src.services.recruiting_resume_service import init_recruiting_operator_tables

    with get_db_connection() as conn:
        init_recruiting_operator_tables(conn)
        conn.commit()


@pytest.fixture
def temp_tenant_with_user():
    """创建临时租户 + 测试用户，测试后清理"""
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
                {"candidate_name": "赵六", "images": images},
            )
        assert _count_resumes(ctx["tenant_id"]) == 0
        assert not list(temp_storage_dir.iterdir())

    def test_bad_basic_info_raises(self, temp_tenant_with_user):
        """basic_info 非对象抛 ResumePayloadError"""
        ctx = temp_tenant_with_user
        with pytest.raises(resume_service.ResumePayloadError, match="basic_info"):
            resume_service.create_resume_record_from_tool_result(
                ctx["tenant_id"], ctx["user_id"],
                {"candidate_name": "赵六", "basic_info": "不是对象"},
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
        # 紧凑摘要：字段集合精确匹配
        assert set(result["data"].keys()) == {
            "resume_id", "candidate_name", "job_name", "image_count", "ocr_char_count"
        }
        assert result["data"]["candidate_name"] == "张三"
        assert result["data"]["job_name"] == "后端开发"
        assert result["data"]["image_count"] == 3
        assert result["data"]["ocr_char_count"] == len(payload["ocr_text"])
        # 图片字节与 OCR 全文绝不进 LLM 上下文
        serialized = json.dumps(result, ensure_ascii=False)
        assert _TINY_PNG_BASE64 not in serialized
        assert "OCR_FULLTEXT_UNIQUE_XYZ" not in serialized
        # message 中文摘要含姓名 / 职位 / 截图张数
        assert "张三" in result["message"]
        assert "后端开发" in result["message"]
        assert "3" in result["message"]
        # 已真实落库（source=boss）且截图落盘
        record = resume_service.get_resume(ctx["tenant_id"], result["data"]["resume_id"])
        assert record["source"] == "boss"
        assert record["ocr_text"] == payload["ocr_text"]
        assert len(record["images"]) == 3
        assert len(list(temp_storage_dir.iterdir())) == 3

    def test_success_keeps_unknown_effect_notice(self, temp_tenant_with_user, temp_storage_dir):
        """CLI 成功但 effect=unknown：摘要覆写 message 后仍保留「实际效果未知」提示（防 LLM 建议重试）"""
        from src.local_tools.proxy_tool import UNKNOWN_EFFECT_NOTICE

        ctx = temp_tenant_with_user
        payload = {"candidate_name": "李四", "images": [
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
        assert "CLI 结果格式需对齐" in result["message"]
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


# ============== 4. import 安全 / 注册 ==============

class TestImportSafety:
    """import 安全：proxy_tool ↔ services 无循环依赖"""

    def test_imports_and_registration(self):
        """工具类进 LOCAL_PROXY_TOOL_CLASSES，受信清单放行 boss_resume_detail"""
        from src.local_tools import catalog

        assert BossResumeDetailTool in LOCAL_PROXY_TOOL_CLASSES
        assert BossResumeDetailTool.name == "boss_resume_detail"
        assert catalog.is_tool_allowed("boss-recruiting", "boss_resume_detail") is True

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
