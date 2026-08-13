"""storage_migration 单元测试

覆盖：
- 5 种旧路径变体推算新路径
- 幂等（目标已存在 + 大小相同 -> 跳过）
- 文件冲突（目标已存在 + 大小不同 -> 加后缀）
- Redis 元数据同步
- 渠道目录跳过
- advisory lock 获取失败时跳过迁移
"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from src.core.storage_migration import (
    _resolve_new_path,
    _migrate_file,
    _build_redis_path_index,
    migrate_uploads_to_tenants,
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

        # mock advisory lock + Redis
        with patch(
            "src.core.storage_migration._acquire_advisory_lock", return_value=(True, None)
        ), patch(
            "src.core.storage_migration._release_advisory_lock"
        ), patch(
            "src.core.storage_migration._build_redis_path_index", return_value={}
        ):
            stats = migrate_uploads_to_tenants(project_root=tmp_path)

        assert stats["scanned"] == 7
        assert stats["migrated"] == 6  # dingtalk 跳过
        assert stats["skipped"] == 1
        assert stats["errors"] == 0

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
        ):
            stats = migrate_uploads_to_tenants(project_root=tmp_path)

        assert stats["scanned"] == 0
        assert stats["migrated"] == 0

    def test_uploads_root_not_exists(self, tmp_path):
        """storage/uploads/ 不存在：直接返回零统计"""
        with patch(
            "src.core.storage_migration._acquire_advisory_lock", return_value=(True, None)
        ), patch(
            "src.core.storage_migration._release_advisory_lock"
        ):
            stats = migrate_uploads_to_tenants(project_root=tmp_path)

        assert stats == {
            "scanned": 0,
            "migrated": 0,
            "skipped": 0,
            "redis_updated": 0,
            "errors": 0,
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
