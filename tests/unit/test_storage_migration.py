"""storage_migration 单元测试

覆盖：
- 5 种旧路径变体推算新路径
- 幂等（目标已存在 + 大小相同 -> 跳过）
- 文件冲突（目标已存在 + 大小不同 -> 加后缀）
- Redis 元数据同步
- 渠道目录跳过
- advisory lock 获取失败时跳过迁移
- data_sources 误搬文件 relocate 修复（幂等）
- documents.metadata.source.file_path 旧路径前缀重写（仅动 metadata 列）
"""

import json

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from src.core.storage_migration import (
    _resolve_new_path,
    _migrate_file,
    _build_redis_path_index,
    migrate_uploads_to_tenants,
    rewrite_legacy_data_source_path,
    _fix_documents_metadata_paths,
)


class TestResolveNewPath:
    """5 种旧路径变体推算新路径"""

    def test_anonymous_conversation(self, tmp_path):
        """storage/uploads/conversation/{file} -> storage/tenants/_anonymous/conversation/{file}"""
        uploads = tmp_path / "storage" / "uploads"
        tenants = tmp_path / "storage" / "tenants"
        uploads.mkdir(parents=True)
        (uploads / "conversation").mkdir()
        old = uploads / "conversation" / "file_abc.docx"
        old.write_text("x")

        new = _resolve_new_path(old, uploads, tenants)
        assert new == tenants / "_anonymous" / "conversation" / "file_abc.docx"

    def test_anonymous_knowledge(self, tmp_path):
        """storage/uploads/knowledge/{file} -> storage/tenants/_anonymous/knowledge/{file}

        c3f749b 之前的无租户知识库路径。
        """
        uploads = tmp_path / "storage" / "uploads"
        tenants = tmp_path / "storage" / "tenants"
        old = uploads / "knowledge" / "kb_d5776386fd50.docx"
        old.parent.mkdir(parents=True)
        old.write_text("x")

        new = _resolve_new_path(old, uploads, tenants)
        assert new == tenants / "_anonymous" / "knowledge" / "kb_d5776386fd50.docx"

    def test_bare_tenant_knowledge(self, tmp_path):
        """storage/uploads/{tid}/knowledge/{file} -> storage/tenants/{tid}/knowledge/{file}

        c3f749b 风格的租户路径，tid 不带 tenant_ 前缀。
        """
        uploads = tmp_path / "storage" / "uploads"
        tenants = tmp_path / "storage" / "tenants"
        old = uploads / "1dc997a1806b" / "knowledge" / "kb_abc.md"
        old.parent.mkdir(parents=True)
        old.write_text("x")

        new = _resolve_new_path(old, uploads, tenants)
        assert new == tenants / "1dc997a1806b" / "knowledge" / "kb_abc.md"

    def test_bare_tenant_data_sources(self, tmp_path):
        """storage/uploads/{tid}/data_sources/{file} -> storage/tenants/{tid}/data_sources/{file}

        Phase 3 数据分析源文件，tid 不带 tenant_ 前缀。
        """
        uploads = tmp_path / "storage" / "uploads"
        tenants = tmp_path / "storage" / "tenants"
        old = uploads / "1dc997a1806b" / "data_sources" / "file_abc.xlsx"
        old.parent.mkdir(parents=True)
        old.write_text("x")

        new = _resolve_new_path(old, uploads, tenants)
        assert new == tenants / "1dc997a1806b" / "data_sources" / "file_abc.xlsx"

    def test_global_data_sources(self, tmp_path):
        """storage/uploads/_global/data_sources/{file} -> storage/tenants/_anonymous/data_sources/{file}

        Phase 3 无租户回退 _global 统一为 _anonymous。
        """
        uploads = tmp_path / "storage" / "uploads"
        tenants = tmp_path / "storage" / "tenants"
        old = uploads / "_global" / "data_sources" / "file_def.csv"
        old.parent.mkdir(parents=True)
        old.write_text("x")

        new = _resolve_new_path(old, uploads, tenants)
        assert new == tenants / "_anonymous" / "data_sources" / "file_def.csv"

    def test_bare_tenant_templates(self, tmp_path):
        """storage/uploads/{tid}/templates/{file} -> storage/tenants/{tid}/templates/{file}

        Phase 4 子智能体模板文件，tid 不带 tenant_ 前缀。
        """
        uploads = tmp_path / "storage" / "uploads"
        tenants = tmp_path / "storage" / "tenants"
        old = uploads / "1dc997a1806b" / "templates" / "file_abc.docx"
        old.parent.mkdir(parents=True)
        old.write_text("x")

        new = _resolve_new_path(old, uploads, tenants)
        assert new == tenants / "1dc997a1806b" / "templates" / "file_abc.docx"

    def test_tenant_user(self, tmp_path):
        """storage/uploads/tenant_{tid}/user_{uid}/{file} -> conversation"""
        uploads = tmp_path / "storage" / "uploads"
        tenants = tmp_path / "storage" / "tenants"
        old = uploads / "tenant_t1" / "user_u1" / "file_x.pdf"
        old.parent.mkdir(parents=True)
        old.write_text("x")

        new = _resolve_new_path(old, uploads, tenants)
        assert new == tenants / "t1" / "conversation" / "file_x.pdf"

    def test_tenant_knowledge(self, tmp_path):
        """storage/uploads/tenant_{tid}/knowledge/{file} -> knowledge"""
        uploads = tmp_path / "storage" / "uploads"
        tenants = tmp_path / "storage" / "tenants"
        old = uploads / "tenant_t1" / "knowledge" / "kb_doc.md"
        old.parent.mkdir(parents=True)
        old.write_text("x")

        new = _resolve_new_path(old, uploads, tenants)
        assert new == tenants / "t1" / "knowledge" / "kb_doc.md"

    def test_tenant_data_sources(self, tmp_path):
        """storage/uploads/tenant_{tid}/data_sources/{file} -> data_sources

        Phase 3 数据分析源文件，tid 带 tenant_ 前缀。
        历史曾因 _resolve_new_path 缺 data_sources 分支被误搬到
        tenants/{tid}/conversation/data_sources/，本测试覆盖修复后的正确路径推算。
        """
        uploads = tmp_path / "storage" / "uploads"
        tenants = tmp_path / "storage" / "tenants"
        old = uploads / "tenant_t1" / "data_sources" / "650280730142.xlsx"
        old.parent.mkdir(parents=True)
        old.write_text("x")

        new = _resolve_new_path(old, uploads, tenants)
        assert new == tenants / "t1" / "data_sources" / "650280730142.xlsx"

    def test_tenant_direct_file(self, tmp_path):
        """storage/uploads/tenant_{tid}/{file} -> conversation"""
        uploads = tmp_path / "storage" / "uploads"
        tenants = tmp_path / "storage" / "tenants"
        old = uploads / "tenant_t1" / "report.xlsx"
        old.parent.mkdir(parents=True)
        old.write_text("x")

        new = _resolve_new_path(old, uploads, tenants)
        assert new == tenants / "t1" / "conversation" / "report.xlsx"

    @pytest.mark.parametrize("channel", ["dingtalk", "wecom_kf"])
    def test_channel_dirs_skipped(self, tmp_path, channel):
        """storage/uploads/dingtalk/、wecom_kf/ -> None（跳过）"""
        uploads = tmp_path / "storage" / "uploads"
        tenants = tmp_path / "storage" / "tenants"
        old = uploads / channel / "msg_file.txt"
        old.parent.mkdir(parents=True)
        old.write_text("x")

        new = _resolve_new_path(old, uploads, tenants)
        assert new is None

    def test_nested_tenant_knowledge(self, tmp_path):
        """storage/uploads/storage/uploads/tenant_{tid}/knowledge/{file} -> storage/tenants/{tid}/knowledge/{file}

        历史运维误操作（如 cp -r storage storage/uploads/）会产生自嵌套路径，
        剥离多余的 storage/uploads/ 前缀后按正常 tenant_{tid}/knowledge 分支处理。
        """
        uploads = tmp_path / "storage" / "uploads"
        tenants = tmp_path / "storage" / "tenants"
        old = uploads / "storage" / "uploads" / "tenant_ea24cd1a1097" / "knowledge" / "kb_11300a9aaaf5.xlsx"
        old.parent.mkdir(parents=True)
        old.write_text("x")

        new = _resolve_new_path(old, uploads, tenants)
        assert new == tenants / "ea24cd1a1097" / "knowledge" / "kb_11300a9aaaf5.xlsx"

    def test_nested_bare_tenant_knowledge(self, tmp_path):
        """storage/uploads/storage/uploads/{tid}/knowledge/{file} -> storage/tenants/{tid}/knowledge/{file}

        剥离后命中 bare tid/knowledge 分支（c3f749b 风格）。
        """
        uploads = tmp_path / "storage" / "uploads"
        tenants = tmp_path / "storage" / "tenants"
        old = uploads / "storage" / "uploads" / "ea24cd1a1097" / "knowledge" / "kb_abc.md"
        old.parent.mkdir(parents=True)
        old.write_text("x")

        new = _resolve_new_path(old, uploads, tenants)
        assert new == tenants / "ea24cd1a1097" / "knowledge" / "kb_abc.md"

    def test_nested_conversation(self, tmp_path):
        """storage/uploads/storage/uploads/conversation/{file} -> storage/tenants/_anonymous/conversation/{file}

        剥离后命中全局 conversation 分支。
        """
        uploads = tmp_path / "storage" / "uploads"
        tenants = tmp_path / "storage" / "tenants"
        old = uploads / "storage" / "uploads" / "conversation" / "anon.docx"
        old.parent.mkdir(parents=True)
        old.write_text("x")

        new = _resolve_new_path(old, uploads, tenants)
        assert new == tenants / "_anonymous" / "conversation" / "anon.docx"

    def test_nested_double_wrapping(self, tmp_path):
        """多层嵌套 storage/uploads/storage/uploads/storage/uploads/... 也应被全部剥离"""
        uploads = tmp_path / "storage" / "uploads"
        tenants = tmp_path / "storage" / "tenants"
        old = (
            uploads / "storage" / "uploads" / "storage" / "uploads"
            / "tenant_t1" / "knowledge" / "kb.md"
        )
        old.parent.mkdir(parents=True)
        old.write_text("x")

        new = _resolve_new_path(old, uploads, tenants)
        assert new == tenants / "t1" / "knowledge" / "kb.md"

    def test_nested_channel_skipped(self, tmp_path):
        """嵌套的渠道目录 storage/uploads/storage/uploads/dingtalk/... -> None（剥离后跳过）"""
        uploads = tmp_path / "storage" / "uploads"
        tenants = tmp_path / "storage" / "tenants"
        old = uploads / "storage" / "uploads" / "dingtalk" / "msg.txt"
        old.parent.mkdir(parents=True)
        old.write_text("x")

        new = _resolve_new_path(old, uploads, tenants)
        assert new is None

    def test_unknown_structure_skipped(self, tmp_path):
        """无法识别的顶层目录 -> None + warning"""
        uploads = tmp_path / "storage" / "uploads"
        tenants = tmp_path / "storage" / "tenants"
        old = uploads / "unknown_dir" / "file.txt"
        old.parent.mkdir(parents=True)
        old.write_text("x")

        new = _resolve_new_path(old, uploads, tenants)
        assert new is None


class TestMigrateFile:
    """_migrate_file 单文件迁移逻辑"""

    def test_move_to_new_path(self, tmp_path):
        """目标不存在：直接 move"""
        src = tmp_path / "src.docx"
        src.write_text("hello")
        dst = tmp_path / "storage" / "tenants" / "t1" / "conversation" / "src.docx"

        migrated, redis_key = _migrate_file(src, dst, {})
        assert migrated == dst
        assert migrated.exists()
        assert not src.exists()
        assert migrated.read_text() == "hello"
        assert redis_key is None

    def test_idempotent_same_size(self, tmp_path):
        """目标已存在 + 大小相同：跳过移动，src 保留"""
        src = tmp_path / "src.docx"
        src.write_text("hello")
        dst = tmp_path / "dst.docx"
        dst.write_text("hello")  # 同内容同大小

        migrated, redis_key = _migrate_file(src, dst, {})
        assert migrated == dst
        # src 未被移动（幂等跳过）
        assert src.exists()
        assert dst.read_text() == "hello"

    def test_conflict_different_size(self, tmp_path):
        """目标已存在 + 大小不同：加 _migrated_xxx 后缀"""
        src = tmp_path / "src.docx"
        src.write_text("new content longer")
        dst = tmp_path / "dst.docx"
        dst.write_text("old")

        migrated, redis_key = _migrate_file(src, dst, {})
        assert migrated != dst
        assert migrated.exists()
        assert migrated.name.startswith("dst_migrated_")
        assert migrated.suffix == ".docx"
        assert not src.exists()
        # 原目标保留
        assert dst.read_text() == "old"

    def test_redis_index_hit(self, tmp_path):
        """Redis 反向索引命中：迁移后更新 path 字段"""
        src = tmp_path / "src.docx"
        src.write_text("hello")
        dst = tmp_path / "dst.docx"
        redis_key = "test:uploaded_file:file_abc"

        index = {str(src.absolute()): redis_key}
        with patch(
            "src.core.storage_migration._update_redis_path"
        ) as mock_update:
            migrated, hit_key = _migrate_file(src, dst, index)
            assert hit_key == redis_key
            mock_update.assert_called_once_with(
                redis_key, str(migrated.absolute())
            )


class TestBuildRedisPathIndex:
    """Redis 反向索引构建"""

    def test_returns_dict_when_redis_unavailable(self):
        """Redis 不可用时返回空 dict，不抛异常"""
        with patch(
            "src.core.redis_client.redis_client.scan",
            side_effect=Exception("redis down"),
        ):
            with patch(
                "src.core.redis_client.redis_client.hgetall",
                side_effect=Exception("redis down"),
            ):
                # _build_redis_path_index 内部 import redis_client
                index = _build_redis_path_index()
                assert index == {}

    def test_builds_index_from_scan(self):
        """正常扫描 uploaded_file:* 键，建 path -> key 索引"""
        fake_keys = ["app:uploaded_file:file_1", "app:uploaded_file:file_2"]
        fake_data = {
            "app:uploaded_file:file_1": {"path": "/old/path/a.docx", "name": "a"},
            "app:uploaded_file:file_2": {"path": "/old/path/b.docx", "name": "b"},
        }

        def mock_scan(cursor, match, count):
            if cursor == 0:
                return 1, fake_keys  # 第一批
            return 0, []  # 结束

        with patch(
            "src.core.redis_client.redis_client.scan", side_effect=mock_scan
        ), patch(
            "src.core.redis_client.redis_client.hgetall",
            side_effect=lambda k: fake_data.get(k, {}),
        ):
            index = _build_redis_path_index()
            assert index == {
                "/old/path/a.docx": "app:uploaded_file:file_1",
                "/old/path/b.docx": "app:uploaded_file:file_2",
            }


class TestMigrateUploadsToTenantsIntegration:
    """端到端迁移流程（mock 掉 advisory lock 和 Redis）"""

    def test_full_migration_with_mixed_paths(self, tmp_path):
        """混合 5 种路径变体，一次性迁移"""
        uploads = tmp_path / "storage" / "uploads"
        tenants = tmp_path / "storage" / "tenants"

        # 构造 5 种变体
        (uploads / "conversation").mkdir(parents=True)
        (uploads / "conversation" / "anon.docx").write_text("anon")

        (uploads / "tenant_t1" / "user_u1").mkdir(parents=True)
        (uploads / "tenant_t1" / "user_u1" / "u1file.pdf").write_text("u1")

        (uploads / "tenant_t1" / "knowledge").mkdir(parents=True)
        (uploads / "tenant_t1" / "knowledge" / "kb.md").write_text("kb")

        (uploads / "tenant_t2").mkdir(parents=True)
        (uploads / "tenant_t2" / "direct.xlsx").write_text("direct")

        (uploads / "dingtalk").mkdir(parents=True)
        (uploads / "dingtalk" / "msg.txt").write_text("channel")

        # 旧 knowledge 路径（c3f749b 之前 / 之中遗留）
        (uploads / "knowledge").mkdir(parents=True)
        (uploads / "knowledge" / "kb_anon.docx").write_text("anon-kb")

        (uploads / "1dc997a1806b" / "knowledge").mkdir(parents=True)
        (uploads / "1dc997a1806b" / "knowledge" / "kb_tenant.md").write_text("tenant-kb")

        # mock advisory lock + Redis + documents metadata 修复（避免连真实 DB）
        with patch(
            "src.core.storage_migration._acquire_advisory_lock", return_value=(True, None)
        ), patch(
            "src.core.storage_migration._release_advisory_lock"
        ), patch(
            "src.core.storage_migration._build_redis_path_index", return_value={}
        ), patch(
            "src.core.storage_migration._fix_documents_metadata_paths", return_value=0
        ):
            stats = migrate_uploads_to_tenants(project_root=tmp_path)

        assert stats["scanned"] == 7
        assert stats["migrated"] == 6  # dingtalk 跳过
        assert stats["skipped"] == 1
        assert stats["errors"] == 0
        assert stats["docs_updated"] == 0

        # 验证新路径下文件存在
        assert (tenants / "_anonymous" / "conversation" / "anon.docx").exists()
        assert (tenants / "t1" / "conversation" / "u1file.pdf").exists()
        assert (tenants / "t1" / "knowledge" / "kb.md").exists()
        assert (tenants / "t2" / "conversation" / "direct.xlsx").exists()
        assert (tenants / "_anonymous" / "knowledge" / "kb_anon.docx").exists()
        assert (tenants / "1dc997a1806b" / "knowledge" / "kb_tenant.md").exists()

        # 渠道文件未迁移
        assert (uploads / "dingtalk" / "msg.txt").exists()

    def test_lock_not_acquired_skips_migration(self, tmp_path):
        """advisory lock 获取失败：跳过迁移，返回零统计"""
        uploads = tmp_path / "storage" / "uploads"
        uploads.mkdir(parents=True)
        (uploads / "conversation").mkdir()
        (uploads / "conversation" / "a.docx").write_text("a")

        with patch(
            "src.core.storage_migration._acquire_advisory_lock", return_value=(False, None)
        ):
            stats = migrate_uploads_to_tenants(project_root=tmp_path)

        # 未迁移
        assert stats == {
            "scanned": 0,
            "migrated": 0,
            "skipped": 0,
            "redis_updated": 0,
            "errors": 0,
            "docs_updated": 0,
        }
        assert (uploads / "conversation" / "a.docx").exists()

    def test_idempotent_second_run(self, tmp_path):
        """第二次运行：所有文件已迁移，统计全 0"""
        uploads = tmp_path / "storage" / "uploads"
        uploads.mkdir(parents=True)
        # uploads 下已无文件（都被迁走了）

        with patch(
            "src.core.storage_migration._acquire_advisory_lock", return_value=(True, None)
        ), patch(
            "src.core.storage_migration._release_advisory_lock"
        ), patch(
            "src.core.storage_migration._build_redis_path_index", return_value={}
        ), patch(
            "src.core.storage_migration._fix_documents_metadata_paths", return_value=0
        ):
            stats = migrate_uploads_to_tenants(project_root=tmp_path)

        assert stats["scanned"] == 0
        assert stats["migrated"] == 0
        assert stats["docs_updated"] == 0

    def test_uploads_root_not_exists(self, tmp_path):
        """storage/uploads/ 不存在：跳过 uploads 扫描，但仍跑 relocate 和 metadata 修复"""
        with patch(
            "src.core.storage_migration._acquire_advisory_lock", return_value=(True, None)
        ), patch(
            "src.core.storage_migration._release_advisory_lock"
        ), patch(
            "src.core.storage_migration._fix_documents_metadata_paths", return_value=0
        ):
            stats = migrate_uploads_to_tenants(project_root=tmp_path)

        assert stats == {
            "scanned": 0,
            "migrated": 0,
            "skipped": 0,
            "redis_updated": 0,
            "errors": 0,
            "docs_updated": 0,
        }


class TestAdvisoryLockContract:
    """advisory lock 接口契约：release 必须接收 acquire 返回的同一 conn

    防止回退到 acquire/release 各自独立 getconn 的旧实现--那会导致
    pg_advisory_unlock 在不同 session 上执行而失效（PostgreSQL advisory
    lock 是 session-level）。
    """

    def test_release_receives_acquired_conn(self, tmp_path):
        """acquire 返回的 conn 必须原样传给 release"""
        uploads = tmp_path / "storage" / "uploads"
        uploads.mkdir(parents=True)
        fake_conn = MagicMock(name="lock_conn")

        with patch(
            "src.core.storage_migration._acquire_advisory_lock",
            return_value=(True, fake_conn),
        ) as mock_acq, patch(
            "src.core.storage_migration._release_advisory_lock"
        ) as mock_rel, patch(
            "src.core.storage_migration._build_redis_path_index", return_value={}
        ):
            migrate_uploads_to_tenants(project_root=tmp_path)

        # acquire 与 release 各被调用一次
        mock_acq.assert_called_once()
        mock_rel.assert_called_once()
        # release 收到的就是 acquire 返回的 conn
        assert mock_rel.call_args.args[0] is fake_conn

    def test_release_not_called_when_lock_not_acquired(self, tmp_path):
        """lock 未获取时不应调用 release（没有 conn 需要 release）"""
        uploads = tmp_path / "storage" / "uploads"
        uploads.mkdir(parents=True)

        with patch(
            "src.core.storage_migration._acquire_advisory_lock",
            return_value=(False, None),
        ), patch(
            "src.core.storage_migration._release_advisory_lock"
        ) as mock_rel:
            migrate_uploads_to_tenants(project_root=tmp_path)

        mock_rel.assert_not_called()


class TestRewriteLegacyDataSourcePath:
    """数据分析源文件旧路径前缀重写规则（4 条）"""

    def test_uploads_tenant_prefix(self):
        """uploads/tenant_{tid}/data_sources/{file} -> tenants/{tid}/data_sources/{file}"""
        assert rewrite_legacy_data_source_path(
            "storage/uploads/tenant_b586cc25f107/data_sources/650280730142.xlsx"
        ) == "storage/tenants/b586cc25f107/data_sources/650280730142.xlsx"

    def test_uploads_tenant_prefix_absolute(self):
        """绝对路径前缀（/app/...）也应被识别，输出保留前缀"""
        assert rewrite_legacy_data_source_path(
            "/app/storage/uploads/tenant_b586cc25f107/data_sources/650280730142.xlsx"
        ) == "/app/storage/tenants/b586cc25f107/data_sources/650280730142.xlsx"

    def test_uploads_global_prefix(self):
        """uploads/_global/data_sources/{file} -> tenants/_anonymous/data_sources/{file}"""
        assert rewrite_legacy_data_source_path(
            "storage/uploads/_global/data_sources/file_def.csv"
        ) == "storage/tenants/_anonymous/data_sources/file_def.csv"

    def test_uploads_bare_tenant_prefix(self):
        """uploads/{tid}/data_sources/{file} -> tenants/{tid}/data_sources/{file}"""
        assert rewrite_legacy_data_source_path(
            "storage/uploads/1dc997a1806b/data_sources/file_abc.xlsx"
        ) == "storage/tenants/1dc997a1806b/data_sources/file_abc.xlsx"

    def test_misplaced_tenants_conversation_data_sources(self):
        """tenants/{tid}/conversation/data_sources/{file} -> tenants/{tid}/data_sources/{file}

        _resolve_new_path 缺 data_sources 分支时历史误搬的位置。
        """
        assert rewrite_legacy_data_source_path(
            "storage/tenants/b586cc25f107/conversation/data_sources/650280730142.xlsx"
        ) == "storage/tenants/b586cc25f107/data_sources/650280730142.xlsx"

    def test_no_match_returns_none(self):
        """非旧路径前缀返回 None"""
        assert rewrite_legacy_data_source_path(
            "storage/tenants/b586cc25f107/data_sources/650280730142.xlsx"
        ) is None
        assert rewrite_legacy_data_source_path("") is None
        assert rewrite_legacy_data_source_path(
            "storage/uploads/tenant_t1/knowledge/kb.md"
        ) is None


class TestRelocateMisplacedDataSources:
    """data_sources 误搬文件 relocate 修复 + 幂等"""

    def test_relocate_misplaced_file(self, tmp_path):
        """tenants/{tid}/conversation/data_sources/{file} 搬到 tenants/{tid}/data_sources/"""
        uploads = tmp_path / "storage" / "uploads"
        tenants = tmp_path / "storage" / "tenants"

        # 构造误搬场景：文件已在 conversation/data_sources/ 下
        misplaced = tenants / "b586cc25f107" / "conversation" / "data_sources" / "650280730142.xlsx"
        misplaced.parent.mkdir(parents=True)
        misplaced.write_text("data")

        with patch(
            "src.core.storage_migration._acquire_advisory_lock", return_value=(True, None)
        ), patch(
            "src.core.storage_migration._release_advisory_lock"
        ), patch(
            "src.core.storage_migration._build_redis_path_index", return_value={}
        ), patch(
            "src.core.storage_migration._fix_documents_metadata_paths", return_value=0
        ):
            stats = migrate_uploads_to_tenants(project_root=tmp_path)

        # 文件已搬到正确位置
        assert (tenants / "b586cc25f107" / "data_sources" / "650280730142.xlsx").exists()
        assert (tenants / "b586cc25f107" / "data_sources" / "650280730142.xlsx").read_text() == "data"
        # 原误搬位置已清空（目录被 rmdir）
        assert not misplaced.exists()
        assert not (tenants / "b586cc25f107" / "conversation" / "data_sources").exists()
        # stats 反映 relocate
        assert stats["migrated"] == 1
        assert stats["errors"] == 0

    def test_relocate_idempotent(self, tmp_path):
        """二次运行：误搬文件已搬走，stats.migrated 不再增加"""
        tenants = tmp_path / "storage" / "tenants"
        target = tenants / "b586cc25f107" / "data_sources" / "650280730142.xlsx"
        target.parent.mkdir(parents=True)
        target.write_text("data")

        with patch(
            "src.core.storage_migration._acquire_advisory_lock", return_value=(True, None)
        ), patch(
            "src.core.storage_migration._release_advisory_lock"
        ), patch(
            "src.core.storage_migration._build_redis_path_index", return_value={}
        ), patch(
            "src.core.storage_migration._fix_documents_metadata_paths", return_value=0
        ):
            stats = migrate_uploads_to_tenants(project_root=tmp_path)

        # 没有误搬文件可处理
        assert stats["migrated"] == 0
        assert stats["errors"] == 0
        # 原文件未动
        assert target.exists()

    def test_relocate_skips_when_tenants_root_missing(self, tmp_path):
        """tenants_root 不存在时 relocate 静默跳过"""
        with patch(
            "src.core.storage_migration._acquire_advisory_lock", return_value=(True, None)
        ), patch(
            "src.core.storage_migration._release_advisory_lock"
        ), patch(
            "src.core.storage_migration._fix_documents_metadata_paths", return_value=0
        ):
            stats = migrate_uploads_to_tenants(project_root=tmp_path)

        assert stats["migrated"] == 0
        assert stats["errors"] == 0


class TestFixDocumentsMetadataPaths:
    """documents.metadata.source.file_path 旧路径前缀重写"""

    def test_rewrites_all_four_legacy_prefixes(self):
        """4 种旧前缀全部被正确重写，UPDATE 调用 4 次"""
        # 4 种旧前缀各一条记录
        rows = [
            {
                "id": 101,
                "metadata": json.dumps({
                    "source": {"type": "excel", "file_path": "storage/uploads/tenant_b586cc25f107/data_sources/f1.xlsx"}
                }),
            },
            {
                "id": 102,
                "metadata": json.dumps({
                    "source": {"type": "excel", "file_path": "storage/uploads/_global/data_sources/f2.csv"}
                }),
            },
            {
                "id": 103,
                "metadata": json.dumps({
                    "source": {"type": "excel", "file_path": "storage/uploads/1dc997a1806b/data_sources/f3.xlsx"}
                }),
            },
            {
                "id": 104,
                "metadata": json.dumps({
                    "source": {"type": "excel", "file_path": "storage/tenants/b586cc25f107/conversation/data_sources/f4.xlsx"}
                }),
            },
            # 不需重写的记录（已是新路径）
            {
                "id": 105,
                "metadata": json.dumps({
                    "source": {"type": "excel", "file_path": "storage/tenants/b586cc25f107/data_sources/f5.xlsx"}
                }),
            },
            # 缺 source 的记录（应跳过）
            {
                "id": 106,
                "metadata": json.dumps({"table_name": "t"}),
            },
        ]

        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = rows
        # 记录所有 UPDATE 调用的 SQL 和参数
        update_calls = []

        def _execute(sql, params=None):
            if sql.strip().startswith("UPDATE"):
                update_calls.append((sql, params))

        mock_cursor.execute.side_effect = _execute

        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.__exit__.return_value = False
        mock_conn.cursor.return_value = mock_cursor

        with patch("src.db.database.get_db_connection", return_value=mock_conn):
            updated = _fix_documents_metadata_paths()

        assert updated == 4  # 4 条旧前缀记录被重写
        assert len(update_calls) == 4

        # 关键断言：UPDATE SQL 不含 file_path= 列赋值（仅 metadata=%s）
        # 避免误改知识库 documents.file_path 字段
        for sql, _ in update_calls:
            assert "UPDATE documents SET metadata = %s WHERE id = %s" in sql
            assert "file_path=" not in sql.replace(" ", "")
            assert "file_path =" not in sql

        # 验证 4 条重写后的路径正确
        rewritten_paths = []
        for _, params in update_calls:
            meta = json.loads(params[0])
            rewritten_paths.append(meta["source"]["file_path"])
        assert rewritten_paths == [
            "storage/tenants/b586cc25f107/data_sources/f1.xlsx",
            "storage/tenants/_anonymous/data_sources/f2.csv",
            "storage/tenants/1dc997a1806b/data_sources/f3.xlsx",
            "storage/tenants/b586cc25f107/data_sources/f4.xlsx",
        ]

        # conn.commit 被调用
        mock_conn.commit.assert_called_once()

    def test_returns_zero_when_db_unavailable(self):
        """DB 模块不可用时返回 0，不抛异常"""
        with patch(
            "src.db.database.get_db_connection",
            side_effect=Exception("db down"),
        ):
            updated = _fix_documents_metadata_paths()
        assert updated == 0

    def test_skips_records_without_source_file_path(self):
        """source.file_path 缺失或 source 不是 dict 时跳过"""
        rows = [
            {"id": 1, "metadata": json.dumps({"source": {"type": "database"}})},  # 无 file_path
            {"id": 2, "metadata": json.dumps({"source": "not_a_dict"})},  # source 非 dict
            {"id": 3, "metadata": json.dumps({})},  # 无 source
        ]
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = rows
        mock_cursor.execute.side_effect = lambda sql, params=None: None

        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.__exit__.return_value = False
        mock_conn.cursor.return_value = mock_cursor

        with patch("src.db.database.get_db_connection", return_value=mock_conn):
            updated = _fix_documents_metadata_paths()

        assert updated == 0
        # SELECT 被调用，UPDATE 没被调用
        assert mock_cursor.execute.call_count == 1
