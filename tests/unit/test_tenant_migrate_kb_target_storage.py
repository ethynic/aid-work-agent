"""
租户数据迁移工具 target_storage 改造测试（Phase 7）

覆盖：新规范目标存储根 + 知识库文档目标路径构造。
"""
import importlib.util
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

# scripts/ 是无 __init__.py 的命名空间包；全量回归下 sys.path 可能被前置用例污染
# （表现为 ModuleNotFoundError: No module named 'scripts.tenant_migrate_kb'），
# 故按文件路径显式加载，不依赖 sys.path 状态
_tmkb_spec = importlib.util.spec_from_file_location(
    "_tenant_migrate_kb",
    Path(__file__).resolve().parents[2] / "scripts" / "tenant_migrate_kb.py",
)
_tenant_migrate_kb = importlib.util.module_from_spec(_tmkb_spec)
_tmkb_spec.loader.exec_module(_tenant_migrate_kb)
_build_target_knowledge_path = _tenant_migrate_kb._build_target_knowledge_path


class TestGetTenantsStorageRoot:
    def test_returns_tenants_root(self):
        from src.core.storage import get_tenants_storage_root

        assert get_tenants_storage_root() == os.path.join("storage", "tenants")


class TestBuildTargetKnowledgePath:
    def test_build_new_spec_relative_path(self):
        tgt_rel, full_tgt = _build_target_knowledge_path(
            target_tenant="tenant_b",
            src_path="storage/uploads/tenant_a/user_x/file_abc123.pdf",
            target_storage=os.path.join("storage", "tenants"),
        )
        # 按 storage.normalize_tenant_id 规范，磁盘目录剥离 tenant_ 前缀
        assert tgt_rel == os.path.join("storage", "tenants", "b", "knowledge", "file_abc123.pdf")
        assert full_tgt == os.path.join("storage", "tenants", "b", "knowledge", "file_abc123.pdf")

    def test_build_with_absolute_target_storage(self):
        tgt_rel, full_tgt = _build_target_knowledge_path(
            target_tenant="tenant_b",
            src_path="/app/source_storage/storage/uploads/tenant_a/file_abc.pdf",
            target_storage="/app/storage/tenants",
        )
        # tgt_rel 始终是相对路径（新规范，与知识库 API 存储格式一致）
        # 磁盘目录按 normalize_tenant_id 规范剥离 tenant_ 前缀
        assert tgt_rel == os.path.join("storage", "tenants", "b", "knowledge", "file_abc.pdf")
        # full_tgt 基于 target_storage 绝对路径
        assert full_tgt == os.path.join("/app/storage/tenants", "b", "knowledge", "file_abc.pdf")

    def test_build_keeps_basename_only(self):
        """目标路径只保留源文件 basename，不再保留旧目录结构"""
        tgt_rel, _ = _build_target_knowledge_path(
            target_tenant="tenant_b",
            src_path="storage/uploads/tenant_a/user_x/report.pdf",
            target_storage=os.path.join("storage", "tenants"),
        )
        assert tgt_rel == os.path.join("storage", "tenants", "b", "knowledge", "report.pdf")

    def test_build_renames_tenant(self):
        """源租户 tenant_a 迁移到 target tenant_b，路径不再包含 source_tenant"""
        tgt_rel, _ = _build_target_knowledge_path(
            target_tenant="tenant_b",
            src_path="storage/tenants/tenant_a/knowledge/file_xyz.pdf",
            target_storage=os.path.join("storage", "tenants"),
        )
        assert "tenant_a" not in tgt_rel
        assert tgt_rel == os.path.join("storage", "tenants", "b", "knowledge", "file_xyz.pdf")


class TestGetTargetStorage:
    def test_returns_tenants_root(self):
        from src.saas.api.tenant_migration import _get_target_storage

        assert _get_target_storage() == os.path.join("storage", "tenants")
