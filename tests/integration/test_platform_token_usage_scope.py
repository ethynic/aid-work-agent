"""平台积分消耗汇总口径测试

覆盖 get_platform_token_usage 的真实租户汇总口径：
- 测试租户（tenant_type=test）明细行保留、不进汇总
- 真实租户（tenant_type=real）计入汇总
"""

import uuid

import pytest

pytestmark = pytest.mark.integration


def _create_temp_tenant(tenant_type: str) -> dict:
    from src.saas.db.tenant_db import TenantDB

    tenant_code = f"T{uuid.uuid4().hex[:6].upper()}"
    tenant = TenantDB.create(
        company_name=f"口径测试租户-{tenant_code}",
        tenant_code=tenant_code,
        tenant_type=tenant_type,
    )
    if not tenant:
        pytest.skip("无法创建测试租户（DB 不可用）")
    return tenant


def _delete_tenant(tenant_id: str) -> None:
    from src.saas.db.tenant_db import TenantDB
    from src.db.database import get_db_connection
    from src.core.cache_utils import invalidate_tenant_cache

    TenantDB.delete(tenant_id)
    invalidate_tenant_cache(tenant_id)
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM tenants WHERE tenant_id = %s", (tenant_id,))
            conn.commit()
    except Exception:
        pass


def _insert_chat_record(tenant_id: str, prompt_tokens: int, completion_tokens: int, credit_cost: float) -> None:
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO chat_records
                (record_id, session_id, tenant_id, user_id, user_message, assistant_message,
                 prompt_tokens, completion_tokens, credit_cost, source_type, created_at)
            VALUES (%s, %s, %s, 'test_user', '测试消息', '测试回复', %s, %s, %s, 'chat', NOW())
            """,
            (
                f"rec_{uuid.uuid4().hex[:16]}",
                f"session_test_{uuid.uuid4().hex[:8]}",
                tenant_id,
                prompt_tokens,
                completion_tokens,
                credit_cost,
            ),
        )
        conn.commit()


def _delete_chat_records(tenant_id: str) -> None:
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM chat_records WHERE tenant_id = %s", (tenant_id,))
        conn.commit()


class TestPlatformTokenUsageScope:
    """get_platform_token_usage 汇总只算真实租户"""

    def test_summary_excludes_test_tenant(self):
        from datetime import datetime
        from src.db.models import ChatRecordDB
        from src.core.cache_utils import CacheKeys, delete_cached

        real_tenant = _create_temp_tenant("real")
        test_tenant = _create_temp_tenant("test")
        try:
            month_str = datetime.now().strftime("%Y-%m")
            # 先取基线（避免与其他 real 租户的存量消耗耦合），断言改为基线差值
            delete_cached(CacheKeys.PLATFORM_USAGE, month_str)
            baseline = ChatRecordDB.get_platform_token_usage(month_str)
            baseline_credit = baseline["summary"]["total_credit_cost"]

            _insert_chat_record(real_tenant["tenant_id"], 1000, 500, 12.5)
            _insert_chat_record(test_tenant["tenant_id"], 1000, 500, 88.0)

            delete_cached(CacheKeys.PLATFORM_USAGE, month_str)
            result = ChatRecordDB.get_platform_token_usage(month_str)

            detail = {row["tenant_id"]: row for row in result["data"]}
            # 明细行：全部租户保留，且带 tenant_type
            assert detail[real_tenant["tenant_id"]]["tenant_type"] == "real"
            assert detail[test_tenant["tenant_id"]]["tenant_type"] == "test"

            # 汇总：仅真实租户计入（增量恰为 real 租户的 12.5，test 租户的 88.0 不进汇总）
            assert result["summary"]["total_credit_cost"] == pytest.approx(baseline_credit + 12.5)
            assert result["summary"]["tenant_count"] >= 1
        finally:
            _delete_chat_records(real_tenant["tenant_id"])
            _delete_chat_records(test_tenant["tenant_id"])
            delete_cached(CacheKeys.PLATFORM_USAGE, datetime.now().strftime("%Y-%m"))
            _delete_tenant(real_tenant["tenant_id"])
            _delete_tenant(test_tenant["tenant_id"])
