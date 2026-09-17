"""租户物理删除服务单元测试

核心回归点：
- purge_tenant_core：核心表按依赖顺序删除（tokens 先于 users）、单事务提交、缓存失效、附件目录删除
- purge_expired_deleted_tenants：仅处理 deactivated 且超期租户
- purge_orphan_data：ctid 分批删除、排除 NULL 与 '' 哨兵、单表失败不中断
- cleanup_orphan_storage_dirs：存储目录名与 DB tenant_id 前缀差异（normalize_tenant_id）不误删、
  _anonymous 等特殊占位目录保留
"""

from unittest.mock import MagicMock, patch

import pytest

import src.saas.services.tenant_purge as tenant_purge
from src.saas.services.tenant_purge import (
    cleanup_orphan_storage_dirs,
    purge_expired_deleted_tenants,
    purge_orphan_data,
    purge_tenant_core,
)


_UNSET = object()


def _make_conn(rowcounts=None, fetchall=None, fetchone=_UNSET):
    """构造 mock 连接：cursor.rowcount 按 SQL 执行次数依次取值，fetchall/fetchone 可指定返回"""
    cursor = MagicMock()
    if rowcounts is not None:
        cursor.rowcount = None
        cursor.execute.side_effect = _bump_rowcount(cursor, list(rowcounts))
    if fetchall is not None:
        cursor.fetchall.side_effect = fetchall
    if fetchone is not _UNSET:
        cursor.fetchone.return_value = fetchone
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn, cursor


def _bump_rowcount(cursor, counts):
    """让 cursor.rowcount 依次取 counts 中的值（每个 execute 消耗一个）"""
    state = {"i": 0}

    def _side_effect(*args, **kwargs):
        i = state["i"]
        state["i"] += 1
        cursor.rowcount = counts[i] if i < len(counts) else 0
        return None

    return _side_effect


class TestPurgeTenantCore:
    def test_deletes_core_tables_in_order_and_commits(self, monkeypatch, tmp_path):
        # 10 张核心/关联表删除 + 1 条 tenants 复核删除
        conn, cursor = _make_conn(rowcounts=[1, 2, 0, 0, 1, 2, 1, 3, 1, 1, 1])
        monkeypatch.setattr(tenant_purge, "get_db_connection", lambda: _ctx(conn))
        monkeypatch.setattr(tenant_purge, "get_tenants_storage_root", lambda: str(tmp_path))
        invalidated = []
        monkeypatch.setattr(tenant_purge, "invalidate_tenant_cache", lambda tid: invalidated.append(tid))

        # 附件目录存在（使用规范化 ID，无 tenant_ 前缀）
        tenant_dir = tmp_path / "abc123"
        tenant_dir.mkdir()

        result = purge_tenant_core("tenant_abc123")

        assert result["success"] is True
        executed = [c.args[0] for c in cursor.execute.call_args_list]
        # 无 tenant_id 列的关联表 + 核心表 + tenants（复核删除），tokens/scheduled_tasks 在 users 之前
        tables = [e.split("FROM ")[1].split(" ")[0].split("(")[0] for e in executed]
        assert tables == [
            "chunks_vec", "chunks", "user_email_settings", "remote_credentials",
            "tokens", "scheduled_tasks", "users", "subscriptions",
            "user_agent_permissions", "tenant_channel_configs", "tenants",
        ]
        # tenants 行删除带 status 复核（防恢复竞态）
        assert "AND status = %s" in executed[-1]
        assert cursor.execute.call_args_list[-1].args[1] == ("tenant_abc123", "deactivated")
        conn.commit.assert_called_once()
        assert invalidated == ["tenant_abc123"]
        assert not tenant_dir.exists()

    def test_status_changed_during_purge_rolls_back(self, monkeypatch, tmp_path):
        # tenants 复核删除 rowcount=0（租户被恢复或不存在）-> 整体回滚，不删附件目录
        conn, cursor = _make_conn(rowcounts=[0] * 10 + [0])
        monkeypatch.setattr(tenant_purge, "get_db_connection", lambda: _ctx(conn))
        monkeypatch.setattr(tenant_purge, "get_tenants_storage_root", lambda: str(tmp_path))

        tenant_dir = tmp_path / "abc123"
        tenant_dir.mkdir()

        result = purge_tenant_core("tenant_abc123")

        assert result["success"] is False
        assert "已回滚" in result["error"]
        conn.commit.assert_not_called()
        conn.rollback.assert_called_once()
        assert tenant_dir.exists()

    def test_db_failure_returns_error_without_storage_removal(self, monkeypatch, tmp_path):
        conn = MagicMock()
        cursor = MagicMock()
        cursor.execute.side_effect = RuntimeError("db down")
        conn.cursor.return_value = cursor
        monkeypatch.setattr(tenant_purge, "get_db_connection", lambda: _ctx(conn))
        monkeypatch.setattr(tenant_purge, "get_tenants_storage_root", lambda: str(tmp_path))

        tenant_dir = tmp_path / "abc123"
        tenant_dir.mkdir()

        result = purge_tenant_core("tenant_abc123")

        assert result["success"] is False
        assert "db down" in result["error"]
        # DB 失败时附件目录保留（等夜间任务重试）
        assert tenant_dir.exists()

    def test_missing_storage_dir_no_error(self, monkeypatch, tmp_path):
        conn, _ = _make_conn(rowcounts=[0] * 10 + [1])
        monkeypatch.setattr(tenant_purge, "get_db_connection", lambda: _ctx(conn))
        monkeypatch.setattr(tenant_purge, "get_tenants_storage_root", lambda: str(tmp_path))

        result = purge_tenant_core("tenant_nodir")
        assert result["success"] is True


class TestPurgeExpiredDeletedTenants:
    def test_purges_each_expired_tenant(self, monkeypatch):
        rows = [{"tenant_id": "tenant_a"}, {"tenant_id": "tenant_b"}]
        conn, cursor = _make_conn(fetchall=[rows])
        monkeypatch.setattr(tenant_purge, "get_db_connection", lambda: _ctx(conn))
        purged = []
        monkeypatch.setattr(
            tenant_purge, "purge_tenant_core",
            lambda tid: purged.append(tid) or {"success": True, "deleted": {}},
        )

        result = purge_expired_deleted_tenants(days=7)

        assert result == ["tenant_a", "tenant_b"]
        assert purged == ["tenant_a", "tenant_b"]
        # 扫描条件：deactivated 状态（参数化）+ updated_at 超期
        sql, params = cursor.execute.call_args_list[0].args
        assert "status = %s" in sql
        assert "updated_at" in sql
        assert params[0] == "deactivated"

    def test_scan_failure_returns_empty(self, monkeypatch):
        conn = MagicMock()
        cursor = MagicMock()
        cursor.execute.side_effect = RuntimeError("db down")
        conn.cursor.return_value = cursor
        monkeypatch.setattr(tenant_purge, "get_db_connection", lambda: _ctx(conn))

        assert purge_expired_deleted_tenants() == []


class TestPurgeOrphanData:
    def test_batched_delete_until_exhausted(self, monkeypatch):
        # 表1 两批删完（5000 + 1200），表2 无孤儿（删除 0 行结束循环）
        conn, cursor = _make_conn(rowcounts=[5000, 1200, 0])
        monkeypatch.setattr(tenant_purge, "get_db_connection", lambda: _ctx(conn))
        monkeypatch.setattr(
            tenant_purge, "_list_orphan_scan_targets",
            lambda: [("chat_messages", "tenant_id"), ("subscriptions", "tenant_id")],
        )
        monkeypatch.setattr(tenant_purge, "_fetch_inbound_fks", lambda: {})

        result = purge_orphan_data(batch_size=5000)

        # 仅删除数 > 0 的表记录在结果中
        assert result == {"chat_messages.tenant_id": 6200}
        sqls = [c.args[0] for c in cursor.execute.call_args_list]
        # 分批用 ctid + LIMIT
        assert "ctid" in sqls[0] and "LIMIT 5000" in sqls[0]
        # 排除 NULL 与 '' 哨兵值
        assert "IS NOT NULL" in sqls[0] and "<> ''" in sqls[0]
        # 以 tenants 表为准判定孤儿
        assert "NOT IN (SELECT tenant_id FROM tenants)" in sqls[0]
        # 每批提交
        assert conn.commit.call_count == 3

    def test_single_table_failure_does_not_stop_others(self, monkeypatch):
        conn, cursor = _make_conn(rowcounts=[5, 0])
        monkeypatch.setattr(tenant_purge, "get_db_connection", lambda: _ctx(conn))
        monkeypatch.setattr(
            tenant_purge, "_list_orphan_scan_targets",
            lambda: [("bad_table", "tenant_id"), ("good_table", "tenant_id")],
        )
        monkeypatch.setattr(tenant_purge, "_fetch_inbound_fks", lambda: {})

        # bad_table 第一批执行报错，good_table 正常删除后以 0 行结束
        original_execute = cursor.execute.side_effect

        def _execute(*args, **kwargs):
            sql = args[0] if args else ""
            if "bad_table" in sql:
                raise RuntimeError("type mismatch")
            return original_execute(*args, **kwargs)

        cursor.execute.side_effect = _execute

        result = purge_orphan_data()

        assert result == {"good_table.tenant_id": 5}

    def test_target_discovery_failure_returns_empty(self, monkeypatch):
        monkeypatch.setattr(
            tenant_purge, "_list_orphan_scan_targets",
            lambda: (_ for _ in ()).throw(RuntimeError("db down")),
        )
        assert purge_orphan_data() == {}

    def test_fk_dependents_purged_before_parent(self, monkeypatch):
        # tokens（无 tenant_id 列）残留行通过 FK 挡住 users 孤儿删除：
        # 先预清理 tokens 中引用 users 孤儿行的数据，再删 users 本表
        conn, cursor = _make_conn(rowcounts=[3, 10, 0])
        monkeypatch.setattr(tenant_purge, "get_db_connection", lambda: _ctx(conn))
        monkeypatch.setattr(
            tenant_purge, "_list_orphan_scan_targets",
            lambda: [("users", "tenant_id")],
        )
        monkeypatch.setattr(
            tenant_purge, "_fetch_inbound_fks",
            lambda: {"users": [("tokens", "user_id", "user_id")]},
        )

        result = purge_orphan_data(batch_size=5000)

        assert result == {"tokens.user_id": 3, "users.tenant_id": 10}
        sqls = [c.args[0] for c in cursor.execute.call_args_list]
        # 预清理 SQL：JOIN 父表套用同一孤儿谓词，且先于父表删除执行
        assert 'FROM "tokens"' in sqls[0] and 'JOIN "users" t ON' in sqls[0]
        assert "NOT IN (SELECT tenant_id FROM tenants)" in sqls[0]
        assert "LIMIT 5000" in sqls[0]
        assert 'FROM "users"' in sqls[1] and 'JOIN "users"' not in sqls[1]
        # 每批提交：tokens 1 批（3<5000 即止）+ users 1 批（10<5000 即止）
        assert conn.commit.call_count == 2

    def test_fk_preclean_failure_skips_parent_table(self, monkeypatch):
        # FK 预清理失败 -> 该父表整体跳过（fail-soft），其他无 FK 表继续清理
        conn, cursor = _make_conn(rowcounts=[5, 0])
        monkeypatch.setattr(tenant_purge, "get_db_connection", lambda: _ctx(conn))
        monkeypatch.setattr(
            tenant_purge, "_list_orphan_scan_targets",
            lambda: [("bad_table", "tenant_id"), ("good_table", "tenant_id")],
        )
        monkeypatch.setattr(
            tenant_purge, "_fetch_inbound_fks",
            lambda: {"bad_table": [("tokens", "user_id", "user_id")]},
        )

        original_execute = cursor.execute.side_effect

        def _execute(*args, **kwargs):
            sql = args[0] if args else ""
            if 'JOIN "bad_table"' in sql:
                raise RuntimeError("fk preclean failed")
            return original_execute(*args, **kwargs)

        cursor.execute.side_effect = _execute

        result = purge_orphan_data()

        assert result == {"good_table.tenant_id": 5}

    def test_inbound_fk_discovery_failure_degrades_to_plain_scan(self, monkeypatch):
        # 入站外键发现失败 -> 退化为原行为（无预清理），孤儿扫描本身不受影响
        conn, cursor = _make_conn(rowcounts=[5, 0])
        monkeypatch.setattr(tenant_purge, "get_db_connection", lambda: _ctx(conn))
        monkeypatch.setattr(
            tenant_purge, "_list_orphan_scan_targets",
            lambda: [("good_table", "tenant_id")],
        )
        monkeypatch.setattr(
            tenant_purge, "_fetch_inbound_fks",
            lambda: (_ for _ in ()).throw(RuntimeError("db down")),
        )

        result = purge_orphan_data()

        assert result == {"good_table.tenant_id": 5}


class TestCleanupOrphanStorageDirs:
    @pytest.fixture
    def storage_root(self, monkeypatch, tmp_path):
        root = tmp_path / "tenants"
        root.mkdir()
        monkeypatch.setattr(tenant_purge, "get_tenants_storage_root", lambda: str(root))
        return root

    def _mock_tenants(self, monkeypatch, tenant_ids, recheck_hit=False):
        # recheck_hit：删除前逐目录复核是否命中租户（模拟清理窗口内租户新注册）
        conn, cursor = _make_conn(
            fetchall=[[{"tenant_id": t} for t in tenant_ids]],
            fetchone={"?column?": 1} if recheck_hit else None,
        )
        monkeypatch.setattr(tenant_purge, "get_db_connection", lambda: _ctx(conn))

    def test_removes_orphan_keeps_active_and_special(self, storage_root, monkeypatch):
        # DB 租户带 tenant_ 前缀；存储目录为规范化 ID（无前缀）
        self._mock_tenants(monkeypatch, ["tenant_ea24cd1a"])
        (storage_root / "ea24cd1a").mkdir()      # 活跃租户（规范化命名）-> 保留
        orphan = storage_root / "deadbeef"
        orphan.mkdir()                            # 孤儿 -> 删除
        special = storage_root / "_anonymous"
        special.mkdir()                           # 特殊占位 -> 保留
        legacy = storage_root / "tenant_ea24cd1a"
        legacy.mkdir()                            # 带前缀的历史命名 -> 保留

        removed = cleanup_orphan_storage_dirs()

        assert removed == ["deadbeef"]
        assert (storage_root / "ea24cd1a").exists()
        assert special.exists()
        assert legacy.exists()
        assert not orphan.exists()

    def test_recheck_tenant_registered_keeps_dir(self, storage_root, monkeypatch):
        # 清理窗口内租户注册：初次扫描判为孤儿，删除前复核命中 -> 保留
        self._mock_tenants(monkeypatch, ["tenant_other"], recheck_hit=True)
        new_tenant_dir = storage_root / "fresh123"
        new_tenant_dir.mkdir()

        removed = cleanup_orphan_storage_dirs()

        assert removed == []
        assert new_tenant_dir.exists()

    def test_empty_tenant_list_aborts(self, storage_root, monkeypatch):
        # 空集保护：租户列表为空（连错库/清库）时中止，不删任何目录
        self._mock_tenants(monkeypatch, [])
        keep = storage_root / "anything"
        keep.mkdir()

        assert cleanup_orphan_storage_dirs() == []
        assert keep.exists()

    def test_db_failure_keeps_all_dirs(self, storage_root, monkeypatch):
        conn = MagicMock()
        cursor = MagicMock()
        cursor.execute.side_effect = RuntimeError("db down")
        conn.cursor.return_value = cursor
        monkeypatch.setattr(tenant_purge, "get_db_connection", lambda: _ctx(conn))
        keep = storage_root / "whatever"
        keep.mkdir()

        assert cleanup_orphan_storage_dirs() == []
        assert keep.exists()

    def test_missing_root_returns_empty(self, monkeypatch, tmp_path):
        monkeypatch.setattr(tenant_purge, "get_tenants_storage_root", lambda: str(tmp_path / "nope"))
        assert cleanup_orphan_storage_dirs() == []


class _Ctx:
    """模拟 get_db_connection 上下文管理器"""

    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        if exc_type:
            self.conn.rollback()
        return False


def _ctx(conn):
    return _Ctx(conn)
