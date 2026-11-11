"""AI 获客邮件打开记录合并任务（src/services/lead_opens_sync.py）单元测试。

覆盖：水位读写与降级、新增事件扫描聚合合并、批溢出不推水位、无新记录不动水位、
值未变化不产生无效 UPDATE（IS DISTINCT 守卫）。数据库与 Redis 一律 mock。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.services import lead_opens_sync  # noqa: E402


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))
        sql_norm = " ".join(sql.split())
        if sql_norm.startswith("SELECT id, message_id"):
            self._rows = self.conn.scan_rows
        elif sql_norm.startswith("SELECT COUNT(*)"):
            self._agg = self.conn.agg_by_mid.pop(self.executed[-1][1][0],
                                                  {"cnt": 0, "last_at": None})
        else:
            self.conn.updates.append(self.executed[-1])
            self.rowcount = self.conn.rowcount_by_mid.pop(self.executed[-1][1][2], 0)

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._agg

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeConn:
    """模拟扫描/聚合/更新三段式交互。"""

    def __init__(self, scan_rows=None, agg_by_mid=None, rowcount_by_mid=None):
        self.scan_rows = scan_rows or []
        self.agg_by_mid = dict(agg_by_mid or {})
        self.rowcount_by_mid = dict(rowcount_by_mid or {})
        self.updates = []
        self.commits = 0
        self.cur = None

    def cursor(self, cursor_factory=None):
        self.cur = FakeCursor(self)
        return self.cur

    def commit(self):
        self.commits += 1

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


@pytest.fixture()
def env(monkeypatch):
    """隔离 Redis 与 DB：水位存内存 dict，连接用注入的 FakeConn。"""
    store = {}

    class FakeRedis:
        def get(self, key):
            return store.get(key)

        def set(self, key, value, ex=None):
            store[key] = str(value)

    monkeypatch.setattr(lead_opens_sync, "redis_client", FakeRedis())
    holder = {"conn": None}

    def _fake_get_db_connection():
        return holder["conn"]

    monkeypatch.setattr(lead_opens_sync, "get_db_connection", _fake_get_db_connection)
    return {"store": store, "holder": holder}


class TestNormMessageId:
    def test_norm(self):
        assert lead_opens_sync._norm_message_id("<abc@x.com>") == "abc@x.com"
        assert lead_opens_sync._norm_message_id("  <a@b> ") == "a@b"
        assert lead_opens_sync._norm_message_id(None) == ""
        assert lead_opens_sync._norm_message_id("") == ""


class TestSyncOpensOnce:
    def test_merge_new_opens_and_advance_watermark(self, env):
        conn = FakeConn(
            scan_rows=[
                {"id": 3, "message_id": "<a@x>"},
                {"id": 7, "message_id": "b@x"},
                {"id": 9, "message_id": ""},  # 空 message_id 不进候选，但计入水位
            ],
            agg_by_mid={"a@x": {"cnt": 2, "last_at": "2026-11-10 10:00:00"}},
            rowcount_by_mid={"a@x": 1},
        )
        env["holder"]["conn"] = conn
        result = lead_opens_sync.sync_opens_once()

        assert result == {"scanned": 3, "candidates": 2, "updated": 1, "has_more": False}
        # 水位推进到已扫描最大 id（含空 message_id 行）
        assert env["store"]["ailg_opens_sync:watermark"] == "9"
        # 聚合与 UPDATE 按 btrim(message_id,'<>') 同口径匹配
        updates = {params[2]: (sql, params) for sql, params in conn.updates}
        assert set(updates) == {"a@x", "b@x"}
        for sql, _ in conn.updates:
            assert "btrim(message_id, '<>')" in sql
            assert "IS DISTINCT FROM" in sql
        # a@x：opens 全量聚合 2 次、最近 2026-11-10 10:00:00
        assert updates["a@x"][1] == ("2026-11-10 10:00:00", 2, "a@x",
                                     "2026-11-10 10:00:00", 2)
        # b@x：无打开事件（空 message_id 不进候选），聚合为 0 次
        assert updates["b@x"][1] == (None, 0, "b@x", None, 0)
        assert conn.commits == 1

    def test_no_new_rows_keeps_watermark(self, env):
        env["store"]["ailg_opens_sync:watermark"] = "42"
        conn = FakeConn(scan_rows=[])
        env["holder"]["conn"] = conn
        result = lead_opens_sync.sync_opens_once()

        assert result["scanned"] == 0 and result["candidates"] == 0
        assert result["updated"] == 0 and result["has_more"] is False
        assert env["store"]["ailg_opens_sync:watermark"] == "42"
        assert conn.updates == []

    def test_batch_overflow_does_not_advance_watermark(self, env, monkeypatch):
        monkeypatch.setattr(lead_opens_sync, "SCAN_BATCH", 2)
        conn = FakeConn(
            scan_rows=[
                {"id": 1, "message_id": "<a@x>"},
                {"id": 2, "message_id": "<b@x>"},
                {"id": 3, "message_id": "<c@x>"},  # 溢出行，本批不处理
            ],
        )
        env["holder"]["conn"] = conn
        result = lead_opens_sync.sync_opens_once()

        assert result["scanned"] == 2 and result["has_more"] is True
        # 溢出时水位不推进，下轮从原水位续扫
        assert "ailg_opens_sync:watermark" not in env["store"]
        # 溢出行不进入候选
        assert result["candidates"] == 2

    def test_value_unchanged_counts_as_not_updated(self, env):
        # opens_at/open_count 均未变化时 UPDATE 命中 0 行（IS DISTINCT 守卫）
        conn = FakeConn(
            scan_rows=[{"id": 5, "message_id": "<a@x>"}],
            agg_by_mid={"a@x": {"cnt": 1, "last_at": "2026-11-10 09:00:00"}},
            rowcount_by_mid={"a@x": 0},
        )
        env["holder"]["conn"] = conn
        result = lead_opens_sync.sync_opens_once()
        assert result["updated"] == 0
        assert env["store"]["ailg_opens_sync:watermark"] == "5"


class TestWatermarkDegradation:
    def test_redis_failure_scans_from_zero(self, env, monkeypatch):
        class BrokenRedis:
            def get(self, key):
                raise RuntimeError("redis down")

            def set(self, key, value, ex=None):
                raise RuntimeError("redis down")

        monkeypatch.setattr(lead_opens_sync, "redis_client", BrokenRedis())
        assert lead_opens_sync._load_watermark() == 0
        lead_opens_sync._save_watermark(9)  # 不抛异常
