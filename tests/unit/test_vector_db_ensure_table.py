"""VectorDBPostgreSQL._ensure_table 进程级 once 缓存单测

覆盖 2026-11-24 agent2「保存 schema 失败」（事务内 DDL 并发锁竞争）修复：
- 表与索引已存在时不执行任何 DDL（避免业务事务内反复 DDL 取锁）
- 外部连接路径（随调用方事务提交）不置位 once 缓存
- 池连接路径（DDL 即时提交）置位 once 缓存
- 缓存命中后完全短路，不触碰数据库
"""

import importlib.util
import sys
from pathlib import Path

import pytest

# 根 conftest 用 MagicMock 壳替换了 src.knowledge.vector_db.vector_db（防止 agent
# 初始化拉起），本测试需要真实实现：按文件路径重新加载真实模块，覆盖注册
# sys.modules 后再导入，避免污染其他用例的 mock 语义
_real_mod_path = (
    Path(__file__).resolve().parents[2] / "src" / "knowledge" / "vector_db" / "vector_db.py"
)
_spec = importlib.util.spec_from_file_location(
    "src.knowledge.vector_db.vector_db_real", _real_mod_path
)
vector_db_module = importlib.util.module_from_spec(_spec)
sys.modules["src.knowledge.vector_db.vector_db_real"] = vector_db_module
_spec.loader.exec_module(vector_db_module)

VectorDBPostgreSQL = vector_db_module.VectorDBPostgreSQL


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.executed = []

    def execute(self, sql, *args, **kwargs):
        self.executed.append(sql)

    def fetchone(self):
        return self.conn.probe_result


class FakeConn:
    def __init__(self, probe_result):
        self.probe_result = probe_result
        self.commits = 0
        self.cursor_obj = None

    def cursor(self, *args, **kwargs):
        if self.cursor_obj is None:
            self.cursor_obj = FakeCursor(self)
        return self.cursor_obj

    def commit(self):
        self.commits += 1


@pytest.fixture(autouse=True)
def reset_flag():
    vector_db_module._ensured_dimensions.clear()
    yield
    vector_db_module._ensured_dimensions.clear()


def _make_instance(conn, external=True):
    obj = object.__new__(VectorDBPostgreSQL)
    obj.dimension = 1024
    obj._external_conn = external
    obj._pool_conn = conn
    return obj


def test_ensure_table_skips_ddl_when_objects_exist():
    conn = FakeConn((True, True))
    obj = _make_instance(conn, external=True)
    obj._ensure_table(conn)
    executed = "".join(conn.cursor_obj.executed)
    # 探测命中：不执行任何 DDL，只做只读探测
    assert "CREATE" not in executed
    assert 1024 in vector_db_module._ensured_dimensions


def test_ensure_table_external_conn_ddl_does_not_set_flag():
    conn = FakeConn((False, False))
    obj = _make_instance(conn, external=True)
    obj._ensure_table(conn)
    executed = "".join(conn.cursor_obj.executed)
    assert "CREATE TABLE IF NOT EXISTS chunks_vec" in executed
    # 外部连接路径 DDL 随调用方事务提交，回滚时表可能未建成，不置位
    assert 1024 not in vector_db_module._ensured_dimensions


def test_ensure_table_pool_conn_ddl_sets_flag_after_commit():
    conn = FakeConn((False, False))
    obj = _make_instance(conn, external=False)
    obj._ensure_table(conn)
    assert conn.commits == 1
    assert 1024 in vector_db_module._ensured_dimensions


def test_ensure_table_flag_short_circuits():
    vector_db_module._ensured_dimensions.add(1024)
    conn = FakeConn((False, False))
    obj = _make_instance(conn, external=True)
    obj._ensure_table(conn)
    # 缓存命中：连 cursor 都不创建
    assert conn.cursor_obj is None
