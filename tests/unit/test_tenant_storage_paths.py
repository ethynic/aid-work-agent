"""
Phase 1 租户附件存储路径改造的单测

覆盖：
- src.main._get_tenant_upload_dir：有租户 / 无租户 / 演示用户
- src.tools.file.cp_tool._resolve_upload_dir
- src.tools.file.write_tool._resolve_upload_dir
- src.tools.excel.excel_lib.ExcelFileHandler.get_session_dir
- src.tools.excel.excel_process_tool._resolve_output_dir
- src.tools.word/pdf/excel.resolve_path：新路径命中 / 旧路径兜底 / 找不到
- Phase 8 normalize_tenant_id：统一剥离 tenant_ 前缀（storage / skill_resolver）
"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.tools]


@pytest.fixture
def isolated_tenants_root(tmp_path: Path, monkeypatch):
    """把 storage._TENANTS_ROOT 重定向到 tmp_path，避免污染项目目录"""
    from src.core import storage as storage_mod
    fake_root = str(tmp_path / "tenants")
    monkeypatch.setattr(storage_mod, "_TENANTS_ROOT", fake_root)
    return tmp_path


@pytest.fixture
def clear_tenant_context(monkeypatch):
    """清空 tenant_id / user_id ContextVar，每个测试独立"""
    from src.saas.context import current_tenant_id, current_user_id
    current_tenant_id.set(None)
    current_user_id.set(None)
    yield
    current_tenant_id.set(None)
    current_user_id.set(None)


def _set_tenant(tenant_id, user_id=None):
    """设置 ContextVar"""
    from src.saas.context import current_tenant_id, current_user_id
    current_tenant_id.set(tenant_id)
    current_user_id.set(user_id)


# ============================================================
# src.main._get_tenant_upload_dir
# ============================================================


class TestGetTenantUploadDir:
    def test_with_tenant(self, isolated_tenants_root, clear_tenant_context):
        """有租户: storage/tenants/{tenant}/conversation/（tenant_ 前缀被剥离）"""
        _set_tenant("tenant_abc", "user_xyz")
        from src.main import _get_tenant_upload_dir
        result = _get_tenant_upload_dir()
        assert result.name == "conversation"
        assert result.parent.name == "abc"  # tenant_abc -> abc（Phase 8 前缀治理）
        assert result.parent.parent.name == "tenants"
        assert result.is_dir()  # 目录已被 ensure_tenant_storage_dir 创建

    def test_without_tenant(self, isolated_tenants_root, clear_tenant_context):
        """无租户: storage/tenants/_anonymous/conversation/"""
        _set_tenant(None, None)
        from src.main import _get_tenant_upload_dir
        result = _get_tenant_upload_dir()
        assert result.name == "conversation"
        assert result.parent.name == "_anonymous"

    def test_demo_tenant(self, isolated_tenants_root, clear_tenant_context):
        """演示用户 tenant_id='demo': storage/tenants/demo/conversation/"""
        _set_tenant("demo", "demo_user")
        from src.main import _get_tenant_upload_dir
        result = _get_tenant_upload_dir()
        assert result.parent.name == "demo"
        assert result.name == "conversation"

    def test_user_id_not_in_path(self, isolated_tenants_root, clear_tenant_context):
        """user_id 不进入路径（避免目录碎片化）"""
        _set_tenant("tenant_a", "user_1")
        from src.main import _get_tenant_upload_dir
        d1 = _get_tenant_upload_dir()
        _set_tenant("tenant_a", "user_2")
        d2 = _get_tenant_upload_dir()
        # 同一 tenant 不同 user_id 应该返回同一目录
        assert d1 == d2


# ============================================================
# cp_tool._resolve_upload_dir / write_tool._resolve_upload_dir
# ============================================================


class TestResolveUploadDir:
    def test_cp_tool_with_tenant(self, isolated_tenants_root):
        from src.tools.file.cp_tool import _resolve_upload_dir
        result = _resolve_upload_dir("tenant_x", "user_y")
        assert result.parent.name == "x"  # tenant_x -> x（Phase 8 前缀治理）
        assert result.name == "conversation"

    def test_cp_tool_without_tenant(self, isolated_tenants_root):
        from src.tools.file.cp_tool import _resolve_upload_dir
        result = _resolve_upload_dir(None, None)
        assert result.parent.name == "_anonymous"

    def test_write_tool_with_tenant(self, isolated_tenants_root):
        from src.tools.file.write_tool import _resolve_upload_dir
        result = _resolve_upload_dir("tenant_z", None)
        assert result.parent.name == "z"  # tenant_z -> z（Phase 8 前缀治理）
        assert result.name == "conversation"

    def test_write_tool_without_tenant(self, isolated_tenants_root):
        from src.tools.file.write_tool import _resolve_upload_dir
        result = _resolve_upload_dir(None, "user_y")
        assert result.parent.name == "_anonymous"


# ============================================================
# ExcelFileHandler.get_session_dir
# ============================================================


class TestExcelGetSessionDir:
    def test_with_tenant(self, isolated_tenants_root, clear_tenant_context):
        _set_tenant("tenant_excel", "user_e")
        from src.tools.excel.excel_lib import ExcelFileHandler
        result = ExcelFileHandler.get_session_dir()
        assert result.parent.name == "excel"  # tenant_excel -> excel（Phase 8 前缀治理）
        assert result.name == "conversation"

    def test_without_tenant(self, isolated_tenants_root, clear_tenant_context):
        _set_tenant(None, None)
        from src.tools.excel.excel_lib import ExcelFileHandler
        result = ExcelFileHandler.get_session_dir()
        assert result.parent.name == "_anonymous"


# ============================================================
# ExcelProcessTool._resolve_output_dir
# ============================================================


class TestExcelProcessResolveOutputDir:
    def test_with_tenant(self, isolated_tenants_root):
        from src.tools.excel.excel_process_tool import ExcelProcessTool
        tool = ExcelProcessTool()
        tool._tenant_id = "tenant_ep"
        tool._user_id = "user_ep"
        result = tool._resolve_output_dir()
        assert result is not None
        p = Path(result)
        assert p.parent.name == "ep"  # tenant_ep -> ep（Phase 8 前缀治理）
        assert p.name == "conversation"

    def test_without_tenant_id_returns_none(self, isolated_tenants_root):
        """未注入 tenant_id 和 user_id 时返回 None（走 ContextVar 兜底）"""
        from src.tools.excel.excel_process_tool import ExcelProcessTool
        tool = ExcelProcessTool()
        tool._tenant_id = None
        tool._user_id = None
        assert tool._resolve_output_dir() is None


# ============================================================
# resolve_path 兜底（word / pdf / excel）
# ============================================================


class TestResolvePathFallback:
    def test_resolve_path_new_tenant_path(self, isolated_tenants_root, monkeypatch):
        """新路径 storage/tenants/{tenant}/conversation/{file} 命中"""
        from src.tools.word.word_lib import WordFileHandler
        # 在 tmp_path/tenants/{tenant}/conversation/ 下造一个文件
        tenant_dir = Path(isolated_tenants_root) / "tenants" / "tenant_w" / "conversation"
        tenant_dir.mkdir(parents=True)
        target = tenant_dir / "report.docx"
        target.write_text("fake")

        result = WordFileHandler.resolve_path("report.docx")
        assert Path(result).resolve() == target.resolve()

    def test_resolve_path_legacy_uploads(self, isolated_tenants_root, monkeypatch, tmp_path):
        """旧路径 storage/uploads/{file} 兜底命中（迁移期兼容）"""
        from src.tools.pdf.pdf_lib import PdfFileHandler
        # mock settings.storage.uploads_dir 指向 tmp_path 下的假 uploads
        fake_uploads = tmp_path / "uploads"
        fake_uploads.mkdir()
        target = fake_uploads / "legacy.pdf"
        target.write_text("legacy")

        with patch("src.config.settings.settings") as mock_settings:
            mock_settings.storage.uploads_dir = str(fake_uploads)
            result = PdfFileHandler.resolve_path("legacy.pdf")
        assert Path(result).resolve() == target.resolve()

    def test_resolve_path_not_found_returns_original(self, isolated_tenants_root, tmp_path):
        """找不到时返回原路径的 absolute()"""
        from src.tools.excel.excel_lib import ExcelFileHandler
        result = ExcelFileHandler.resolve_path("nonexistent_file.xlsx")
        # 返回的是 absolute path，但不一定存在
        assert Path(result).name == "nonexistent_file.xlsx"

    def test_resolve_path_absolute_exists(self, isolated_tenants_root, tmp_path):
        """绝对路径直接命中时不进入兜底扫描"""
        from src.tools.word.word_lib import WordFileHandler
        target = tmp_path / "abs.docx"
        target.write_text("abs")
        result = WordFileHandler.resolve_path(str(target))
        assert Path(result).resolve() == target.resolve()

    @pytest.mark.parametrize("handler_module,handler_class,file_name", [
        ("src.tools.word.word_lib", "WordFileHandler", "trav.docx"),
        ("src.tools.pdf.pdf_lib", "PdfFileHandler", "trav.pdf"),
        ("src.tools.excel.excel_lib", "ExcelFileHandler", "trav.xlsx"),
    ])
    def test_resolve_path_traversal_blocked(
        self, isolated_tenants_root, tmp_path, handler_module, handler_class, file_name
    ):
        """file_path 含 .. 时不得通过新路径扫描穿越到外部文件

        漏洞：candidate = d1 / "conversation" / file_path，若 file_path 含 ..，
        candidate.exists() 会自动 resolve 后命中系统文件或新路径下的越界文件。
        修复：入口处对含 .. 的相对路径直接返回，不进行任何 exists 检查或扫描。
        """
        import importlib
        mod = importlib.import_module(handler_module)
        handler = getattr(mod, handler_class)
        # 在 tmp_path/tenants/tenant_x/conversation/ 下造一个"诱饵"文件
        # 攻击者用 file_path="../conversation/<file_name>" 试图越界命中
        tenant_dir = Path(isolated_tenants_root) / "tenants" / "tenant_x" / "conversation"
        tenant_dir.mkdir(parents=True)
        bait = tenant_dir / file_name
        bait.write_text("secret")
        # 含 .. 的 payload：理论上若新路径扫描无守卫，
        # d1 / "conversation" / "../conversation/<file_name>" 会 resolve 后命中 bait
        attack = f"../conversation/{file_name}"
        result = handler.resolve_path(attack)
        # 守卫生效时：不会命中 bait，返回的路径 resolve 后不应等于 bait
        assert Path(result).resolve() != bait.resolve()
        # 返回路径不应真实存在（即没有读到任何文件）
        assert not Path(result).exists()


# ============================================================
# Phase 2: knowledge 场景上传路径
# ============================================================


class TestKnowledgeUploadPath:
    """知识库文档上传路径：storage/tenants/{tenant}/knowledge/"""

    def test_knowledge_service_with_tenant(self, isolated_tenants_root):
        """有租户: storage/tenants/{tenant}/knowledge/（tenant_ 前缀被剥离）"""
        from src.knowledge.service import knowledge_service
        result = knowledge_service._get_upload_path("tenant_kb")
        assert result.name == "knowledge"
        assert result.parent.name == "kb"  # tenant_kb -> kb（Phase 8 前缀治理）
        assert result.parent.parent.name == "tenants"
        assert result.is_dir()  # 目录已被 ensure_tenant_storage_dir 创建

    def test_knowledge_service_without_tenant(self, isolated_tenants_root):
        """无租户: storage/tenants/_anonymous/knowledge/"""
        from src.knowledge.service import knowledge_service
        result = knowledge_service._get_upload_path(None)
        assert result.name == "knowledge"
        assert result.parent.name == "_anonymous"
        assert result.is_dir()

    def test_knowledge_service_empty_tenant(self, isolated_tenants_root):
        """空字符串 tenant_id: 走 _anonymous 兜底"""
        from src.knowledge.service import knowledge_service
        result = knowledge_service._get_upload_path("")
        assert result.parent.name == "_anonymous"

    def test_travel_quote_save_upload(self, isolated_tenants_root):
        """travel_quote._save_upload_to_storage 落到新路径"""
        from src.api.travel_quote import _save_upload_to_storage
        content = b"hello knowledge"
        rel_path = _save_upload_to_storage(content, "report.md", "tenant_tq")
        # 返回的是相对路径，校验路径结构（tenant_ 前缀被剥离）
        p = Path(rel_path)
        assert p.parent.name == "knowledge"
        assert p.parent.parent.name == "tq"  # tenant_tq -> tq（Phase 8 前缀治理）
        # 文件确实写入且内容一致
        assert p.exists()
        assert p.read_bytes() == content
        # 文件名带 kb_ 前缀 + uuid12 + .md 后缀
        assert p.name.startswith("kb_")
        assert p.suffix == ".md"

    def test_travel_quote_save_upload_creates_dir(self, isolated_tenants_root):
        """目录不存在时自动创建（tenant_ 前缀被剥离）"""
        from src.api.travel_quote import _save_upload_to_storage
        # 先确认目录不存在
        tenants_root = Path(isolated_tenants_root) / "tenants"
        assert not (tenants_root / "new" / "knowledge").exists()
        _save_upload_to_storage(b"x", "f.txt", "tenant_new")
        assert (tenants_root / "new" / "knowledge").is_dir()


# ============================================================
# Phase 8: normalize_tenant_id 前缀治理
# ============================================================


class TestNormalizeTenantId:
    """normalize_tenant_id 统一剥离 tenant_ 前缀，保证存储目录无前缀"""

    def test_normalize_hex_tenant(self):
        from src.core.storage import normalize_tenant_id
        assert normalize_tenant_id("tenant_ea24cd1a1097") == "ea24cd1a1097"

    def test_normalize_already_normalized(self):
        """不带前缀的 tenant_id 原样返回"""
        from src.core.storage import normalize_tenant_id
        assert normalize_tenant_id("ea24cd1a1097") == "ea24cd1a1097"

    def test_anonymous_untouched(self):
        """_anonymous 特殊值不被误剥离"""
        from src.core.storage import normalize_tenant_id
        assert normalize_tenant_id("_anonymous") == "_anonymous"

    def test_demo_untouched(self):
        from src.core.storage import normalize_tenant_id
        assert normalize_tenant_id("demo") == "demo"

    def test_tenant_default(self):
        """tenant_default 统一剥离为 default（保持读写一致）"""
        from src.core.storage import normalize_tenant_id
        assert normalize_tenant_id("tenant_default") == "default"

    def test_empty_string(self):
        from src.core.storage import normalize_tenant_id
        assert normalize_tenant_id("") == ""

    def test_get_tenant_storage_dir_normalizes(self, isolated_tenants_root):
        """get_tenant_storage_dir 自动剥离 tenant_ 前缀"""
        from src.core.storage import get_tenant_storage_dir
        assert get_tenant_storage_dir("tenant_abc", "conversation") == (
            f"{isolated_tenants_root}/tenants/abc/conversation"
        )

    def test_skill_resolver_dir_normalizes(self, tmp_path, monkeypatch):
        """SkillResolver.get_tenant_skills_dir 自动剥离 tenant_ 前缀"""
        from src.config.settings import settings
        fake_tenants = str(tmp_path / "storage" / "tenants")
        monkeypatch.setattr(settings.saas, "tenant_skills_dir", fake_tenants)
        from src.saas.services.skill_resolver import SkillResolver
        d = SkillResolver.get_tenant_skills_dir("tenant_abc")
        assert d == Path(fake_tenants) / "abc" / "skills"
        assert d.parent.name == "abc"
