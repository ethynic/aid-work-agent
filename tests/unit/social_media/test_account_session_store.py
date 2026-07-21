"""外向账号托管登录态存储（B0.5）单元测试。

不依赖真实 PostgreSQL：``get_db_connection`` 被 mock 成返回 MagicMock cursor，
``encryption_manager`` 走真实 Fernet（项目默认 key）。重点验证：

- 加密/解密往返保真
- 租户隔离（SQL 必须含 ``tenant_id`` 过滤）
- 状态机：``expired`` / ``revoked`` 会话 ``load_storage_state`` 返回 ``None``
- cookie 明文绝不进入异常链（解密失败被收口为 ``RuntimeError(str)``）
- ``save`` 走 ON CONFLICT upsert，并清空 ``expired_reason``（重新激活）
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from src.social_media.outbound import account_session_store as store


pytestmark = [pytest.mark.unit]


# ---------------------------------------------------------------------------
# DB mock 工厂
# ---------------------------------------------------------------------------


def _connection(rows=None, fetchone_row=None, rowcount=1):
    """构造一个 mock 的 get_db_connection 上下文管理器。

    ``fetchone_row`` 控制 cursor.fetchone 返回值；``rows`` 控制 fetchall 返回值；
    每次 execute 的 SQL/params 都被记录在 cursor 上便于断言。
    """
    cursor = MagicMock()
    cursor.fetchone.return_value = fetchone_row
    cursor.fetchall.return_value = rows or []
    cursor.rowcount = rowcount
    conn = MagicMock()
    conn.cursor.return_value = cursor

    @contextmanager
    def fake_connection():
        yield conn

    return fake_connection, conn, cursor


_SAMPLE_STATE = {
    "cookies": [
        {"name": "session", "value": "secret-cookie-value", "domain": ".zhihu.com"},
        {"name": "z_c0", "value": "another-secret", "domain": ".zhihu.com"},
    ],
    "origins": [{"origin": "https://www.zhihu.com", "localStorage": []}],
}


# ---------------------------------------------------------------------------
# 表初始化
# ---------------------------------------------------------------------------


def test_init_table_is_idempotent_create_if_not_exists():
    """init 函数必须用 CREATE TABLE IF NOT EXISTS（幂等）。"""
    _fake, _conn, cursor = _connection()
    with patch.object(store, "get_db_connection", _fake):
        store.init_outbound_account_sessions_table(_conn := MagicMock(cursor=lambda: cursor))
    # 直接传 conn 的场景下 cursor 被取出
    sqls = [call.args[0] for call in cursor.execute.call_args_list]
    assert any("CREATE TABLE IF NOT EXISTS bs_outbound_account_sessions" in s for s in sqls)
    assert any("CREATE INDEX IF NOT EXISTS" in s for s in sqls)


# ---------------------------------------------------------------------------
# save_storage_state
# ---------------------------------------------------------------------------


def test_save_encrypts_state_and_upserts():
    """save 必须加密 storage_state 并走 ON CONFLICT upsert。"""
    _fake, _conn, cursor = _connection()
    with patch.object(store, "get_db_connection", _fake):
        store.save_storage_state(
            account_id="sacct_zhihu_abc",
            tenant_id="tenant_A",
            user_id="user_1",
            platform="zhihu",
            storage_state=_SAMPLE_STATE,
        )
    sql, params = cursor.execute.call_args.args
    assert "INSERT INTO bs_outbound_account_sessions" in sql
    assert "ON CONFLICT (account_id) DO UPDATE" in sql
    # 第二个参数（encrypted blob）不等于原始 cookie 明文
    encrypted = params[4]  # account_id, tenant_id, user_id, platform, encrypted, ...
    assert isinstance(encrypted, str)
    assert "secret-cookie-value" not in encrypted
    assert "another-secret" not in encrypted
    # cookie_count 元数据来自原始 state（非敏感）
    assert params[7] == 2  # cookie_count


def test_save_restores_active_status_and_clears_expired_reason():
    """重新保存（重新登录）后状态恢复 active，expired_reason 被清空。"""
    _fake, _conn, cursor = _connection()
    with patch.object(store, "get_db_connection", _fake):
        store.save_storage_state(
            account_id="a1", tenant_id="t1", user_id="u1",
            platform="xiaohongshu", storage_state=_SAMPLE_STATE,
        )
    sql, _params = cursor.execute.call_args.args
    assert "status = EXCLUDED.status" in sql
    assert "expired_reason = NULL" in sql


def test_save_rejects_non_dict_state():
    """非 dict 的 storage_state 直接抛 ValueError（不入库不加密）。"""
    _fake, _conn, _cursor = _connection()
    with patch.object(store, "get_db_connection", _fake):
        with pytest.raises(ValueError):
            store.save_storage_state(
                account_id="a1", tenant_id="t1", user_id="u1",
                platform="zhihu", storage_state="not-a-dict",  # type: ignore[arg-type]
            )


# ---------------------------------------------------------------------------
# load_storage_state — 加解密往返与租户隔离
# ---------------------------------------------------------------------------


def test_load_decrypts_active_session_roundtrip():
    """save 后 load 能完整还原原始 dict。"""
    # 先用真实 encryption_manager 跑一次 save，捕获加密后的值
    captured = {}

    def capturing_execute(sql, params):
        if sql.strip().startswith("INSERT"):
            captured["encrypted"] = params[4]

    _fake_save, _conn_save, cursor_save = _connection()
    cursor_save.execute.side_effect = capturing_execute
    with patch.object(store, "get_db_connection", _fake_save):
        store.save_storage_state(
            account_id="a_roundtrip", tenant_id="t_roundtrip", user_id="u",
            platform="zhihu", storage_state=_SAMPLE_STATE,
        )

    # 再用同一个加密值走 load
    _fake_load, _conn_load, cursor_load = _connection(
        fetchone_row={"storage_state_encrypted": captured["encrypted"], "status": "active"}
    )
    with patch.object(store, "get_db_connection", _fake_load):
        loaded = store.load_storage_state(account_id="a_roundtrip", tenant_id="t_roundtrip")

    assert loaded == _SAMPLE_STATE
    # 必须有 tenant_id 过滤
    load_sql = cursor_load.execute.call_args_list[0].args[0]
    assert "tenant_id = %s" in load_sql


def test_load_uses_is_null_clause_when_tenant_is_none():
    """非 SaaS（tenant_id=None）走 ``tenant_id IS NULL`` 分支。"""
    _fake, _conn, cursor = _connection(fetchone_row=None)
    with patch.object(store, "get_db_connection", _fake):
        assert store.load_storage_state(account_id="a1", tenant_id=None) is None
    sql, params = cursor.execute.call_args.args
    assert "tenant_id IS NULL" in sql
    assert params == ["a1"]


def test_load_tenant_isolation_different_tenant_returns_none():
    """A 租户保存的会话，B 租户查询必须读不到（fetchone 模拟 DB 命中空）。"""
    _fake, _conn, cursor = _connection(fetchone_row=None)
    with patch.object(store, "get_db_connection", _fake):
        result = store.load_storage_state(account_id="a1", tenant_id="tenant_B")
    assert result is None
    # SQL 必须强制 tenant_id 过滤（DB 层隔离）
    sql, params = cursor.execute.call_args.args
    assert "tenant_id = %s" in sql
    assert params == ["a1", "tenant_B"]


def test_load_returns_none_when_missing():
    _fake, _conn, _cursor = _connection(fetchone_row=None)
    with patch.object(store, "get_db_connection", _fake):
        assert store.load_storage_state(account_id="a1", tenant_id="t1") is None


def test_load_returns_none_for_expired_status():
    _fake, _conn, _cursor = _connection(
        fetchone_row={"storage_state_encrypted": "blob", "status": "expired"}
    )
    with patch.object(store, "get_db_connection", _fake):
        assert store.load_storage_state(account_id="a1", tenant_id="t1") is None


def test_load_returns_none_for_revoked_status():
    _fake, _conn, _cursor = _connection(
        fetchone_row={"storage_state_encrypted": "blob", "status": "revoked"}
    )
    with patch.object(store, "get_db_connection", _fake):
        assert store.load_storage_state(account_id="a1", tenant_id="t1") is None


def test_load_decrypt_failure_returns_none_without_leaking_cookie():
    """解密失败必须收口为 None，绝不抛含 cookie 的异常。"""
    _fake, _conn, _cursor = _connection(
        fetchone_row={"storage_state_encrypted": "garbage", "status": "active"}
    )
    with patch.object(store, "get_db_connection", _fake):
        # encryption_manager.decrypt 对 garbage 会抛 ValueError；模块必须吞掉。
        result = store.load_storage_state(account_id="a1", tenant_id="t1")
    assert result is None


def test_load_empty_account_id_returns_none_without_db_call():
    _fake, _conn, cursor = _connection()
    with patch.object(store, "get_db_connection", _fake):
        assert store.load_storage_state(account_id="", tenant_id="t1") is None
    cursor.execute.assert_not_called()


# ---------------------------------------------------------------------------
# mark_expired / mark_revoked
# ---------------------------------------------------------------------------


def test_mark_expired_sets_status_and_reason():
    _fake, _conn, cursor = _connection(rowcount=1)
    with patch.object(store, "get_db_connection", _fake):
        updated = store.mark_expired(
            account_id="a1", tenant_id="t1", reason="login_check_failed"
        )
    assert updated is True
    sql, params = cursor.execute.call_args.args
    assert "status = %s" in sql
    assert "expired_reason = %s" in sql
    assert params[0] == store.STATUS_EXPIRED
    assert params[1] == "login_check_failed"
    # tenant 强制过滤
    assert "tenant_id = %s" in sql
    assert params[-1] == "t1"


def test_mark_expired_truncates_long_reason():
    """失效原因截断到 500 字符，避免异常长文本入库。"""
    _fake, _conn, cursor = _connection(rowcount=1)
    with patch.object(store, "get_db_connection", _fake):
        store.mark_expired(
            account_id="a1", tenant_id="t1",
            reason="x" * 1000,
        )
    _sql, params = cursor.execute.call_args.args
    assert len(params[1]) == 500


def test_mark_expired_returns_false_when_row_missing():
    _fake, _conn, _cursor = _connection(rowcount=0)
    with patch.object(store, "get_db_connection", _fake):
        updated = store.mark_expired(account_id="a1", tenant_id="t1", reason="r")
    assert updated is False


def test_mark_revoked_sets_status():
    _fake, _conn, cursor = _connection(rowcount=1)
    with patch.object(store, "get_db_connection", _fake):
        updated = store.mark_revoked(account_id="a1", tenant_id="t1")
    assert updated is True
    sql, params = cursor.execute.call_args.args
    assert "status = %s" in sql
    assert params[0] == store.STATUS_REVOKED
    assert "tenant_id = %s" in sql


# ---------------------------------------------------------------------------
# get_session_meta — 不返回 storage_state 加密内容
# ---------------------------------------------------------------------------


def test_get_session_meta_excludes_storage_state_blob():
    """get_session_meta 返回元数据，SELECT 列表必须不含 storage_state_encrypted。"""
    _fake, _conn, cursor = _connection(
        fetchone_row={"account_id": "a1", "tenant_id": "t1", "status": "active"}
    )
    with patch.object(store, "get_db_connection", _fake):
        meta = store.get_session_meta(account_id="a1", tenant_id="t1")
    assert meta is not None
    sql = cursor.execute.call_args.args[0]
    # 关键安全断言：SELECT 列表绝不包含加密 blob
    assert "storage_state_encrypted" not in sql
    assert "account_id" in sql and "status" in sql


def test_get_session_meta_returns_none_when_missing():
    _fake, _conn, _cursor = _connection(fetchone_row=None)
    with patch.object(store, "get_db_connection", _fake):
        assert store.get_session_meta(account_id="a1", tenant_id="t1") is None
