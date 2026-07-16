"""RPA 会话绑定数据库层的名称回填与安全删除测试。"""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from src.channels.wecom_personal_rpa import db


def _connection(row=None, rowcount=1):
    cursor = MagicMock()
    cursor.fetchone.return_value = row
    cursor.rowcount = rowcount
    conn = MagicMock()
    conn.cursor.return_value = cursor

    @contextmanager
    def fake_connection():
        yield conn

    return fake_connection, conn, cursor


def test_binding_real_name_can_replace_stable_id_placeholder():
    fake_connection, _conn, cursor = _connection({"id": "rpa_bind_1"})
    with patch.object(db, "get_db_connection", fake_connection), patch.object(
        db, "get_binding", return_value={"id": "rpa_bind_1", "display_name": "陆伟@微信"},
    ):
        result = db.get_or_create_binding(
            "tenant_1", "account_1", "external_user", "陆伟@微信",
            "wm_external_1", stable_id="wm_external_1",
        )

    assert result["display_name"] == "陆伟@微信"
    sql, params = cursor.execute.call_args.args
    assert "WHEN %s = FALSE" in sql
    assert params[-1] is False


def test_binding_placeholder_does_not_overwrite_existing_name():
    fake_connection, _conn, cursor = _connection({"id": "rpa_bind_1"})
    with patch.object(db, "get_db_connection", fake_connection), patch.object(
        db, "get_binding", return_value={"id": "rpa_bind_1", "display_name": "陆伟@微信"},
    ):
        db.get_or_create_binding(
            "tenant_1", "account_1", "external_user", "wm_external_1",
            "wm_external_1", stable_id="wm_external_1",
        )

    assert cursor.execute.call_args.args[1][-1] is True


def test_binding_keeps_real_incoming_name_without_channel_session_lookup():
    fake_connection, _conn, cursor = _connection({"id": "rpa_bind_1"})
    with patch.object(db, "get_db_connection", fake_connection), patch.object(
        db, "get_binding", return_value={"id": "rpa_bind_1", "display_name": "人工名称"},
    ):
        db.get_or_create_binding(
            "tenant_1", "account_1", "external_user", "人工名称",
            "wm_external_1", stable_id="wm_external_1",
        )

    assert cursor.execute.call_count == 1


def test_delete_unconfirmed_binding_has_tenant_and_status_guards():
    fake_connection, conn, cursor = _connection(rowcount=1)
    with patch.object(db, "get_db_connection", fake_connection):
        assert db.delete_unconfirmed_binding("tenant_1", "rpa_bind_1") is True

    sql, params = cursor.execute.call_args.args
    assert "tenant_id = %s" in sql
    assert "status IN ('pending', 'needs_review', 'invalid')" in sql
    assert params == ("rpa_bind_1", "tenant_1")
    conn.commit.assert_called_once()
