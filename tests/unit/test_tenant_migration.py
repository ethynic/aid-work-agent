"""
租户代码数据库迁移测试
"""
import pytest
from src.db.database import get_db_connection


def test_tenant_code_column_exists():
    """测试 tenants 表是否包含 tenant_code 列"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'tenants'
            AND column_name = 'tenant_code'
        """)
        row = cursor.fetchone()
        assert row is not None
        assert row["column_name"] == "tenant_code"


def test_tenant_code_index_exists():
    """测试 tenant_code 索引是否存在"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT indexname
            FROM pg_indexes
            WHERE tablename = 'tenants'
            AND indexname = 'idx_tenants_tenant_code'
        """)
        row = cursor.fetchone()
        assert row is not None
        assert row["indexname"] == "idx_tenants_tenant_code"


def test_tenant_code_format():
    """测试现有租户代码格式（应为大写）"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT tenant_code FROM tenants WHERE tenant_code IS NOT NULL")
        rows = cursor.fetchall()
        for row in rows:
            code = row["tenant_code"]
            # 应为大写字母数字，长度4-8
            assert code.isupper()
            assert 4 <= len(code) <= 8
            assert code.isalnum()


if __name__ == "__main__":
    pytest.main([__file__])