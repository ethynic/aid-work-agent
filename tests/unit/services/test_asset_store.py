"""asset_store 文件元数据持久化测试（file_assets 表读写 + name 保护）"""

from unittest.mock import MagicMock, patch

import pytest

from src.services.files.asset_store import (
    delete_file_asset,
    get_file_asset,
    upsert_file_asset,
)

pytestmark = [pytest.mark.tools]


def _make_conn(rows=None):
    """构造 mock 连接：cursor.execute 记录 SQL/参数，fetchone 返回 rows[0]。"""
    conn = MagicMock()
    cursor = MagicMock()
    cursor.fetchone.return_value = (rows or (None,))[0] if rows else None
    cursor.__enter__ = MagicMock(return_value=cursor)
    cursor.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = cursor
    conn._mock_cursor = cursor
    return conn


class TestUpsertFileAsset:
    @patch("src.db.database.return_pooled_connection")
    @patch("src.db.database.get_pooled_connection")
    def test_upsert_full_metadata(self, mock_get_conn, mock_return):
        conn = _make_conn()
        mock_get_conn.return_value = conn

        upsert_file_asset(
            {"file_id": "file_abc123def456", "name": "清单.xlsx", "path": "/tmp/file_abc123def456.xlsx",
             "size": 123, "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
             "type": "file"},
            tenant_id="tenant_1", user_id="user_1", source="upload", extra={"width": 10},
        )

        sql, params = conn._mock_cursor.execute.call_args[0]
        assert "ON CONFLICT (file_id) DO UPDATE" in sql
        assert params["file_id"] == "file_abc123def456"
        assert params["name"] == "清单.xlsx"
        assert params["tenant_id"] == "tenant_1"
        assert params["source"] == "upload"
        assert '"width": 10' in params["extra"]
        conn.commit.assert_called_once()

    @patch("src.db.database.return_pooled_connection")
    @patch("src.db.database.get_pooled_connection")
    def test_upsert_skips_empty_file_id(self, mock_get_conn, mock_return):
        upsert_file_asset({"file_id": "", "name": "x.xlsx"})
        mock_get_conn.assert_not_called()

    @patch("src.db.database.return_pooled_connection")
    @patch("src.db.database.get_pooled_connection")
    def test_upsert_db_failure_swallows(self, mock_get_conn, mock_return):
        """DB 故障不抛异常（上传/下载主流程不能被 DB 挂掉阻断）。"""
        mock_get_conn.side_effect = RuntimeError("连接池未初始化")
        upsert_file_asset({"file_id": "file_abc123def456", "name": "x.xlsx"})  # 不抛
        delete_file_asset("file_abc123def456")  # 不抛
        assert get_file_asset("file_abc123def456") is None  # 不抛

    @patch("src.db.database.return_pooled_connection")
    @patch("src.db.database.get_pooled_connection")
    def test_sql_name_guard_keeps_visible_name(self, mock_get_conn, mock_return):
        """name 保护：SQL 必须包含 CASE——旧名为用户可见名时保留旧值。"""
        conn = _make_conn()
        mock_get_conn.return_value = conn
        upsert_file_asset({"file_id": "file_abc123def456", "name": "磁盘名.xlsx"})
        sql, params = conn._mock_cursor.execute.call_args[0]
        assert "WHEN file_assets.name IS NULL OR file_assets.name = ''" in sql
        # 存储名形态才允许覆盖（正则经参数传入，与 download_registry 口径一致）
        assert "%(storage_name_re)s THEN EXCLUDED.name" in sql
        assert params["storage_name_re"] == r"^file_[0-9a-f]{12}(\.[^.]+)?$"

    @patch("src.db.database.return_pooled_connection")
    @patch("src.db.database.get_pooled_connection")
    def test_name_only_upsert_does_not_null_path(self, mock_get_conn, mock_return):
        """消息补录只传 {file_id, name} 时，已有 path/size 不被 NULL 冲掉（COALESCE）。"""
        conn = _make_conn()
        mock_get_conn.return_value = conn
        upsert_file_asset({"file_id": "file_abc123def456", "name": "04_标准客房卫生间.xlsx"})
        sql = conn._mock_cursor.execute.call_args[0][0]
        assert "path = COALESCE(EXCLUDED.path, file_assets.path)" in sql
        assert "size = COALESCE(EXCLUDED.size, file_assets.size)" in sql


class TestGetFileAsset:
    @patch("src.db.database.return_pooled_connection")
    @patch("src.db.database.get_pooled_connection")
    def test_returns_meta_dict(self, mock_get_conn, mock_return):
        conn = _make_conn()
        conn._mock_cursor.fetchone.return_value = (
            "file_abc123def456", "04_标准客房卫生间_洁具与五金.xlsx",
            "/app/storage/tenants/t1/conversation/file_abc123def456.xlsx",
            764455, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "file",
        )
        mock_get_conn.return_value = conn
        info = get_file_asset("file_abc123def456")
        assert info["file_id"] == "file_abc123def456"
        assert info["name"] == "04_标准客房卫生间_洁具与五金.xlsx"
        assert info["size"] == 764455  # int 而非 Decimal/str
        assert isinstance(info["size"], int)
        assert info["type"] == "file"

    @patch("src.db.database.return_pooled_connection")
    @patch("src.db.database.get_pooled_connection")
    def test_miss_returns_none(self, mock_get_conn, mock_return):
        conn = _make_conn()
        conn._mock_cursor.fetchone.return_value = None
        mock_get_conn.return_value = conn
        assert get_file_asset("file_nosuch000000") is None


class TestDeleteFileAsset:
    @patch("src.db.database.return_pooled_connection")
    @patch("src.db.database.get_pooled_connection")
    def test_delete_executes(self, mock_get_conn, mock_return):
        conn = _make_conn()
        mock_get_conn.return_value = conn
        delete_file_asset("file_abc123def456")
        sql, params = conn._mock_cursor.execute.call_args[0]
        assert sql.startswith("DELETE FROM file_assets")
        assert params == ("file_abc123def456",)
        conn.commit.assert_called_once()
