"""
LongTermMemory 存储路径单测

覆盖（2026-09 第二阶段存储整改）：
- 新路径：storage/tenants/{tenant_id}/memory/memory_{user_id}.md
- tenant_ 前缀自动剥离
- 旧目录（storage/memory/{tid}/、storage/memory/tenant_{tid}/）首次访问自动迁移
- 迁移幂等 + 新文件已存在时不迁移
"""

from pathlib import Path

import pytest

from src.memory.long_term import LongTermMemory

pytestmark = pytest.mark.tools

VALID_CONTENT = "# 用户记忆\n\n> 最后更新: 2026-09-05\n> 更新来源: 用户\n\n## 个人介绍\n\n- 姓名：张三\n"


@pytest.fixture
def isolated_tenants_root(tmp_path: Path, monkeypatch) -> Path:
    """把 storage._TENANTS_ROOT 重定向到 tmp_path，避免污染项目目录"""
    from src.core import storage as storage_mod
    monkeypatch.setattr(storage_mod, "_TENANTS_ROOT", str(tmp_path / "tenants"))
    return tmp_path


@pytest.fixture
def legacy_storage_dir(tmp_path: Path) -> Path:
    """旧记忆根目录（LongTermMemory 构造参数 storage_dir）"""
    return tmp_path / "legacy_memory"


@pytest.fixture
def memory(isolated_tenants_root, legacy_storage_dir) -> LongTermMemory:
    return LongTermMemory(storage_dir=str(legacy_storage_dir))


class TestNewStoragePath:
    """新路径写入与读取：storage/tenants/{tenant_id}/memory/"""

    def test_get_file_path_with_tenant(self, memory, isolated_tenants_root):
        p = memory._get_file_path("tenant_abc123", "user_1")
        expected = isolated_tenants_root / "tenants" / "abc123" / "memory" / "memory_user_1.md"
        assert p == expected

    def test_get_file_path_tenant_prefix_stripped(self, memory, isolated_tenants_root):
        """带 tenant_ 前缀与不带前缀落到同一目录"""
        p1 = memory._get_file_path("tenant_abc123", "user_1")
        p2 = memory._get_file_path("abc123", "user_1")
        assert p1 == p2

    def test_get_file_path_none_tenant_uses_default(self, memory, isolated_tenants_root):
        p = memory._get_file_path(None, "user_1")
        assert p == isolated_tenants_root / "tenants" / "default" / "memory" / "memory_user_1.md"

    def test_save_creates_new_path(self, memory, isolated_tenants_root):
        memory.save_memory("tenant_t1", "user_1", VALID_CONTENT)
        target = isolated_tenants_root / "tenants" / "t1" / "memory" / "memory_user_1.md"
        assert target.exists()
        assert "姓名：张三" in target.read_text(encoding="utf-8")

    def test_get_memory_reads_new_path(self, memory, isolated_tenants_root):
        memory.save_memory("tenant_t1", "user_1", VALID_CONTENT)
        assert "姓名：张三" in memory.get_memory("tenant_t1", "user_1")

    def test_memory_exists(self, memory, isolated_tenants_root):
        assert memory.memory_exists("tenant_t1", "user_1") is False
        memory.save_memory("tenant_t1", "user_1", VALID_CONTENT)
        assert memory.memory_exists("tenant_t1", "user_1") is True

    def test_get_memory_missing_returns_empty_template(self, memory):
        content = memory.get_memory("tenant_no", "user_x")
        assert content.startswith("# 用户记忆")
        assert "姓名" not in content


class TestLegacyMigration:
    """旧目录记忆文件首次访问自动迁移"""

    def test_migrate_from_prefixed_legacy_dir(self, memory, isolated_tenants_root, legacy_storage_dir):
        """旧路径 {storage_dir}/tenant_{tid}/memory_{uid}.md -> 新路径"""
        old_dir = legacy_storage_dir / "tenant_t1"
        old_dir.mkdir(parents=True)
        old_file = old_dir / "memory_user_1.md"
        old_file.write_text(VALID_CONTENT, encoding="utf-8")

        content = memory.get_memory("tenant_t1", "user_1")

        new_path = isolated_tenants_root / "tenants" / "t1" / "memory" / "memory_user_1.md"
        assert new_path.exists()
        assert "姓名：张三" in content
        assert not old_file.exists()  # move 而非 copy

    def test_migrate_from_unprefixed_legacy_dir(self, memory, isolated_tenants_root, legacy_storage_dir):
        """旧路径 {storage_dir}/{tid}/memory_{uid}.md -> 新路径"""
        old_dir = legacy_storage_dir / "t2"
        old_dir.mkdir(parents=True)
        (old_dir / "memory_user_2.md").write_text(VALID_CONTENT, encoding="utf-8")

        memory.get_memory("t2", "user_2")

        new_path = isolated_tenants_root / "tenants" / "t2" / "memory" / "memory_user_2.md"
        assert new_path.exists()

    def test_migrate_not_triggered_when_new_file_exists(
        self, memory, isolated_tenants_root, legacy_storage_dir
    ):
        """新文件已存在时不迁移（旧文件保留原位）"""
        memory.save_memory("tenant_t3", "user_3", VALID_CONTENT)

        old_dir = legacy_storage_dir / "tenant_t3"
        old_dir.mkdir(parents=True)
        old_file = old_dir / "memory_user_3.md"
        old_file.write_text(VALID_CONTENT, encoding="utf-8")

        content = memory.get_memory("tenant_t3", "user_3")
        assert old_file.exists()  # 未被 move
        assert "姓名：张三" in content

    def test_migrate_prefixed_dir_takes_precedence(
        self, memory, isolated_tenants_root, legacy_storage_dir
    ):
        """两个旧目录同时存在时，优先迁移带 tenant_ 前缀目录（生产实际形态）"""
        d_prefixed = legacy_storage_dir / "tenant_t4"
        d_plain = legacy_storage_dir / "t4"
        d_prefixed.mkdir(parents=True)
        d_plain.mkdir(parents=True)
        (d_prefixed / "memory_user_4.md").write_text(VALID_CONTENT, encoding="utf-8")
        (d_plain / "memory_user_4.md").write_text("# 用户记忆\n\n## 其他\n\n- 旧版本\n", encoding="utf-8")

        content = memory.get_memory("tenant_t4", "user_4")
        assert "姓名：张三" in content  # 前缀目录内容胜出
        assert not (d_prefixed / "memory_user_4.md").exists()
        assert (d_plain / "memory_user_4.md").exists()  # 另一个保留

    def test_migrate_idempotent(self, memory, isolated_tenants_root, legacy_storage_dir):
        """重复访问不重复迁移、不报错"""
        old_dir = legacy_storage_dir / "tenant_t5"
        old_dir.mkdir(parents=True)
        (old_dir / "memory_user_5.md").write_text(VALID_CONTENT, encoding="utf-8")

        memory.get_memory("tenant_t5", "user_5")
        memory.get_memory("tenant_t5", "user_5")
        memory.save_memory("tenant_t5", "user_5", VALID_CONTENT)

        new_path = isolated_tenants_root / "tenants" / "t5" / "memory" / "memory_user_5.md"
        assert new_path.exists()
        assert not old_dir.exists() or not list(old_dir.iterdir())

    def test_no_legacy_file_no_error(self, memory):
        """旧目录无文件时静默返回空模板，不抛异常"""
        assert memory.get_memory("tenant_none", "user_none").startswith("# 用户记忆")

    def test_save_after_migration_keeps_migrated_content(
        self, memory, isolated_tenants_root, legacy_storage_dir
    ):
        """迁移后 save 覆盖的是新路径文件"""
        old_dir = legacy_storage_dir / "tenant_t6"
        old_dir.mkdir(parents=True)
        (old_dir / "memory_user_6.md").write_text(VALID_CONTENT, encoding="utf-8")

        memory.get_memory("tenant_t6", "user_6")
        memory.save_memory("tenant_t6", "user_6", VALID_CONTENT)

        new_path = isolated_tenants_root / "tenants" / "t6" / "memory" / "memory_user_6.md"
        assert new_path.exists()
        assert "> 更新来源: 用户" in new_path.read_text(encoding="utf-8")
