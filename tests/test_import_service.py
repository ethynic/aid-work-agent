"""UUID 智能导入服务（import_service）单元测试

覆盖四种导入模式：
1. 同租户覆盖 —— UUID + tenant_id 命中 → UPDATE
2. 跨租户复制 —— UUID 命中其他租户 → 审计日志 + 新生 UUID → INSERT
3. 首次入库 —— UUID 合法但未命中 → 沿用 Excel UUID → INSERT
4. 全新数据 —— UUID 为空 / 不合法 → 新生 UUID → INSERT
"""
import io
import unittest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import openpyxl

from src.services.import_service import (
    ImportTableConfig,
    generate_uuid,
    import_table_by_uuid,
    is_valid_uuid,
)


# ============================================================
# 测试夹具
# ============================================================

def make_excel(rows: list) -> bytes:
    """把行数据列表写成 Excel 字节流，第一行是表头。"""
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()


@contextmanager
def mock_db(existing_rows: dict = None):
    """模拟 get_db_connection，支持 uuid → {id, tenant_id} 查询。

    Args:
        existing_rows: dict，{uuid: {"id": int, "tenant_id": str}}
    """
    existing_rows = existing_rows or {}
    captured = {"inserts": [], "updates": [], "selects": [], "last_select_result": None}

    def execute(sql, params=None):
        sql_stripped = sql.lstrip()
        sql_upper = sql_stripped.upper()
        if sql_upper.startswith(("SAVEPOINT", "RELEASE SAVEPOINT", "ROLLBACK TO SAVEPOINT")):
            return
        if sql_upper.startswith("SELECT"):
            captured["selects"].append((sql, params))
            if "WHERE uuid" in sql and params:
                captured["last_select_result"] = existing_rows.get(params[0])
            else:
                captured["last_select_result"] = None
        elif sql_upper.startswith("UPDATE"):
            captured["updates"].append((sql, params))
        elif sql_upper.startswith("INSERT"):
            captured["inserts"].append((sql, params))

    cursor = MagicMock()
    cursor.execute = MagicMock(side_effect=execute)
    cursor.fetchone = MagicMock(side_effect=lambda: captured["last_select_result"])

    conn = MagicMock()
    conn.cursor = MagicMock(return_value=cursor)
    conn.commit = MagicMock()

    @contextmanager
    def _ctx():
        try:
            yield conn
        finally:
            pass

    with patch("src.services.import_service.get_db_connection", return_value=_ctx()):
        yield cursor, captured


def _build_cfg(**overrides) -> ImportTableConfig:
    defaults = dict(
        table="test_items",
        columns=["uuid", "name", "price"],
        numeric_cols={"price"},
        bool_cols=set(),
        uuid_prefix="tst",
    )
    defaults.update(overrides)
    return ImportTableConfig(**defaults)


def _parse_insert_columns(sql: str) -> list:
    """从 INSERT INTO t (col1, col2) 解析列名列表。"""
    after_paren = sql.split("(", 1)[1]
    cols_part = after_paren.split(")", 1)[0]
    return [c.strip().strip('"') for c in cols_part.split(",")]


# ============================================================
# 模式 1：同租户覆盖
# ============================================================

class TestSameTenantOverwrite(unittest.TestCase):
    """UUID 命中当前租户 → UPDATE。"""

    def test_update_when_uuid_matches_same_tenant(self):
        existing = {"tst_aaaa1111bbbb": {"id": 42, "tenant_id": "tenant-A"}}
        excel = make_excel([
            ["uuid", "name", "price"],
            ["tst_aaaa1111bbbb", "updated-name", 99.5],
        ])

        with mock_db(existing_rows=existing) as (cursor, captured):
            result = import_table_by_uuid(_build_cfg(), "tenant-A", excel)

        self.assertEqual(result.updated, 1)
        self.assertEqual(result.imported, 0)
        self.assertEqual(result.cross_tenant, 0)
        self.assertEqual(len(captured["updates"]), 1)
        self.assertEqual(len(captured["inserts"]), 0)
        update_sql = captured["updates"][0][0]
        self.assertIn("UPDATE test_items", update_sql)
        self.assertIn("name", update_sql)


# ============================================================
# 模式 2：跨租户复制
# ============================================================

class TestCrossTenantCopy(unittest.TestCase):
    """UUID 命中其他租户 → 审计 + 新生 UUID + INSERT。"""

    def test_copy_when_uuid_belongs_to_other_tenant(self):
        existing = {"tst_aabb00112233": {"id": 7, "tenant_id": "tenant-A"}}
        excel = make_excel([
            ["uuid", "name", "price"],
            ["tst_aabb00112233", "copied-name", 50.0],
        ])

        with mock_db(existing_rows=existing) as (cursor, captured):
            result = import_table_by_uuid(_build_cfg(), "tenant-B", excel, operator="alice")

        self.assertEqual(result.updated, 0)
        self.assertEqual(result.imported, 1)
        self.assertEqual(result.cross_tenant, 1)
        self.assertEqual(len(captured["inserts"]), 1)
        # 校验 INSERT 中的 uuid 已被替换
        insert_sql, insert_params = captured["inserts"][0]
        cols = _parse_insert_columns(insert_sql)
        uuid_idx = cols.index("uuid")
        new_uuid = insert_params[uuid_idx]
        self.assertNotEqual(new_uuid, "tst_aabb00112233")
        self.assertTrue(is_valid_uuid(new_uuid, "tst"))


# ============================================================
# 模式 3：首次入库
# ============================================================

class TestFirstInsert(unittest.TestCase):
    """UUID 合法但未命中 → 沿用 Excel UUID + INSERT。"""

    def test_insert_with_excel_uuid_when_not_found(self):
        excel = make_excel([
            ["uuid", "name", "price"],
            ["tst_abcd12340000", "new-item", 12.0],
        ])

        with mock_db(existing_rows={}) as (cursor, captured):
            result = import_table_by_uuid(_build_cfg(), "tenant-A", excel)

        self.assertEqual(result.updated, 0)
        self.assertEqual(result.imported, 1)
        self.assertEqual(result.cross_tenant, 0)
        self.assertEqual(len(captured["inserts"]), 1)
        insert_params = captured["inserts"][0][1]
        self.assertIn("tst_abcd12340000", insert_params)


# ============================================================
# 模式 4：全新数据
# ============================================================

class TestFreshInsert(unittest.TestCase):
    """UUID 为空 / 不合法 → 新生 UUID + INSERT。"""

    def test_empty_uuid_gets_new_one(self):
        excel = make_excel([
            ["uuid", "name", "price"],
            [None, "fresh-1", 10.0],
        ])

        with mock_db(existing_rows={}) as (cursor, captured):
            result = import_table_by_uuid(_build_cfg(), "tenant-A", excel)

        self.assertEqual(result.imported, 1)
        insert_params = captured["inserts"][0][1]
        new_uuid = next(p for p in insert_params if isinstance(p, str) and p.startswith("tst_"))
        self.assertTrue(is_valid_uuid(new_uuid, "tst"))

    def test_invalid_short_uuid_gets_new_one(self):
        excel = make_excel([
            ["uuid", "name", "price"],
            ["x_short", "fresh-2", 20.0],
        ])

        with mock_db(existing_rows={}) as (cursor, captured):
            result = import_table_by_uuid(_build_cfg(), "tenant-A", excel)

        self.assertEqual(result.imported, 1)
        insert_params = captured["inserts"][0][1]
        self.assertNotIn("x_short", insert_params)
        new_uuid = next(p for p in insert_params if isinstance(p, str) and p.startswith("tst_"))
        self.assertTrue(is_valid_uuid(new_uuid, "tst"))

    def test_wrong_prefix_uuid_gets_new_one(self):
        excel = make_excel([
            ["uuid", "name", "price"],
            ["oth_zzzzzzzzzzzz", "fresh-3", 30.0],
        ])

        with mock_db(existing_rows={}) as (cursor, captured):
            result = import_table_by_uuid(_build_cfg(), "tenant-A", excel)

        self.assertEqual(result.imported, 1)
        insert_params = captured["inserts"][0][1]
        self.assertNotIn("oth_zzzzzzzzzzzz", insert_params)


# ============================================================
# 工具函数
# ============================================================

class TestIsValidUuid(unittest.TestCase):
    def test_valid_uuid_returns_true(self):
        self.assertTrue(is_valid_uuid("tst_abc123456789", "tst"))
        self.assertTrue(is_valid_uuid("tqv_0123456789ab", "tqv"))

    def test_invalid_uuid_returns_false(self):
        self.assertFalse(is_valid_uuid("", "tst"))
        self.assertFalse(is_valid_uuid(None, "tst"))
        self.assertFalse(is_valid_uuid("tst_short", "tst"))
        self.assertFalse(is_valid_uuid("tst_ABC123456789", "tst"))
        self.assertFalse(is_valid_uuid("xxx_abc123456789", "tst"))
        self.assertFalse(is_valid_uuid("tst_abc12345678!", "tst"))

    def test_generate_uuid_format(self):
        u = generate_uuid("tst")
        self.assertTrue(is_valid_uuid(u, "tst"))


# ============================================================
# 边界条件
# ============================================================

class TestEdgeCases(unittest.TestCase):
    def test_blank_rows_are_skipped(self):
        excel = make_excel([
            ["uuid", "name", "price"],
            ["tst_aaaa1111bbbb", "valid", 1.0],
            [None, None, None],
            [None, None, None],
        ])

        with mock_db() as (cursor, captured):
            result = import_table_by_uuid(_build_cfg(), "tenant-A", excel)

        self.assertEqual(result.imported, 1)
        # 空行应被跳过，不影响结果
        self.assertEqual(result.skipped, 0)

    def test_operator_is_inserted_as_user_id(self):
        excel = make_excel([
            ["uuid", "name", "price"],
            [None, "fresh-1", 10.0],
        ])

        with mock_db() as (cursor, captured):
            result = import_table_by_uuid(_build_cfg(), "tenant-A", excel, operator="user-1")

        self.assertEqual(result.imported, 1)
        insert_sql, insert_params = captured["inserts"][0]
        cols = _parse_insert_columns(insert_sql)
        self.assertEqual(insert_params[cols.index("user_id")], "user-1")

    def test_invalid_table_identifier_is_rejected(self):
        excel = make_excel([
            ["uuid", "name", "price"],
            [None, "fresh-1", 10.0],
        ])

        with self.assertRaises(ValueError):
            import_table_by_uuid(_build_cfg(table="test_items;drop"), "tenant-A", excel)

    def test_invalid_column_identifier_is_rejected(self):
        excel = make_excel([
            ["uuid", "name", "price"],
            [None, "fresh-1", 10.0],
        ])

        with self.assertRaises(ValueError):
            import_table_by_uuid(_build_cfg(columns=["uuid", "bad-name"]), "tenant-A", excel)

    def test_failed_row_does_not_stop_following_rows(self):
        excel = make_excel([
            ["uuid", "name", "price"],
            [None, "bad", 10.0],
            [None, "good", 20.0],
        ])

        with mock_db() as (cursor, captured):
            original_execute = cursor.execute.side_effect
            insert_count = {"value": 0}

            def execute(sql, params=None):
                if sql.lstrip().upper().startswith("INSERT"):
                    insert_count["value"] += 1
                    if insert_count["value"] == 1:
                        raise RuntimeError("insert failed")
                return original_execute(sql, params)

            cursor.execute.side_effect = execute
            result = import_table_by_uuid(_build_cfg(), "tenant-A", excel)

        self.assertEqual(result.imported, 1)
        self.assertEqual(result.skipped, 1)
        self.assertEqual(len(captured["inserts"]), 1)

    def test_result_to_dict_contains_all_keys(self):
        from src.services.import_service import ImportResult
        r = ImportResult()
        d = r.to_dict()
        for k in ("imported", "updated", "skipped", "cross_tenant", "errors"):
            self.assertIn(k, d)
