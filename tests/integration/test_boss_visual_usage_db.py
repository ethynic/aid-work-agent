"""显式测试库的 PostgreSQL 并发账务验证，不使用项目默认 DATABASE_URL。

运行前设置 BOSS_VISUAL_TEST_DATABASE_URL 指向独立测试 PG；每次只创建/删除
本测试随机 schema，所有账务表均在该 schema 内，无业务表访问。
"""

import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from decimal import Decimal

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.db]


@pytest.fixture
def visual_pg(monkeypatch):
    dsn = os.getenv("BOSS_VISUAL_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("需要显式独立 BOSS_VISUAL_TEST_DATABASE_URL，不连接默认数据库")
    import psycopg2
    from psycopg2 import sql
    from psycopg2.extras import RealDictCursor
    from src.db import client_binding_db as db

    schema = f"boss_visual_test_{uuid.uuid4().hex}"
    admin = psycopg2.connect(dsn)
    admin.autocommit = True
    try:
        with admin.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            cursor.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            cursor.execute("""
                CREATE TABLE tenants (tenant_id TEXT PRIMARY KEY, credit_balance NUMERIC(12,2));
                CREATE TABLE client_usage_logs (
                    id SERIAL PRIMARY KEY, tenant_id TEXT NOT NULL, binding_id TEXT NOT NULL,
                    client_name TEXT, client_ref_id TEXT, stage TEXT, status TEXT, model TEXT,
                    provider TEXT, detail TEXT, prompt_tokens INTEGER DEFAULT 0,
                    completion_tokens INTEGER DEFAULT 0, cached_tokens INTEGER DEFAULT 0,
                    total_tokens INTEGER DEFAULT 0, raw_credit_cost NUMERIC(12,2) DEFAULT 0,
                    credit_cost NUMERIC(12,2) DEFAULT 0);
                CREATE UNIQUE INDEX tenant_ref ON client_usage_logs (tenant_id, client_ref_id)
                    WHERE client_ref_id IS NOT NULL;
                INSERT INTO tenants VALUES ('tenant', 1);
            """)

        @contextmanager
        def connection():
            conn = psycopg2.connect(dsn, cursor_factory=RealDictCursor)
            try:
                with conn.cursor() as cursor:
                    cursor.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
                yield conn
            finally:
                conn.rollback()
                conn.close()

        monkeypatch.setattr(db, "get_db_connection", connection)
        monkeypatch.setattr(db, "invalidate_tenant_cache", lambda _: None)
        yield db.BossVisualUsageDB, connection
    finally:
        with admin.cursor() as cursor:
            cursor.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
        admin.close()


def test_concurrent_requests_win_one_dispatch_and_one_atomic_settlement(visual_pg):
    dao, connection = visual_pg

    def dispatch(_):
        dao.reserve("tenant", "binding", "request", "digest", "zhipu", "GLM-5.3-Flash", {"input": "1"})
        return dao.dispatch("tenant", "binding", "request")

    with ThreadPoolExecutor(max_workers=8) as workers:
        assert sum(workers.map(dispatch, range(8))) == 1

    def settle(_):
        return dao.settle("tenant", "binding", "request", {"prompt_tokens": 20},
                          Decimal("0.01"), Decimal("0.20"), {"diagnosis": None})

    with ThreadPoolExecutor(max_workers=8) as workers:
        responses = list(workers.map(settle, range(8)))
    assert all(response == responses[0] for response in responses)
    assert responses[0]["receipt"]["credit_cost"] == "0.20"
    with connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT credit_balance FROM tenants WHERE tenant_id = 'tenant'")
            assert cursor.fetchone()["credit_balance"] == Decimal("0.80")
            cursor.execute("SELECT COUNT(*) AS n FROM client_usage_logs")
            assert cursor.fetchone()["n"] == 1


def test_completed_result_stays_readable_when_balance_reaches_zero(visual_pg):
    dao, connection = visual_pg
    dao.reserve("tenant", "binding", "request", "digest", "zhipu", "GLM-5.3-Flash", {})
    assert dao.dispatch("tenant", "binding", "request")
    first = dao.settle("tenant", "binding", "request", {}, Decimal("0.05"), Decimal("1.00"), {})
    assert first["receipt"]["balance_after"] == "0.00"
    assert dao.get("tenant", "request")["detail"]["response"] == first
    assert dao.settle("tenant", "binding", "request", {}, Decimal("0.05"), Decimal("1.00"), {}) == first
    with connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT credit_balance FROM tenants WHERE tenant_id = 'tenant'")
            assert cursor.fetchone()["credit_balance"] == Decimal("0.00")



@pytest.mark.parametrize("status", ["reserved", "dispatched"])
def test_historical_in_flight_rows_keep_50_and_completed_receipt_does_not_change(visual_pg, status):
    dao, connection = visual_pg
    with connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""INSERT INTO client_usage_logs
                (tenant_id, binding_id, client_ref_id, status, provider, model, detail)
                VALUES ('tenant','binding','boss.visual:legacy',%s,'zhipu','GLM-5.3-Flash',%s)""",
                (status, json.dumps({"input_digest": "legacy-digest", "price_snapshot": {"input": "1"}})))
        conn.commit()
    if status == "reserved":
        assert dao.dispatch("tenant", "binding", "legacy")
        assert dao.get("tenant", "legacy")["detail"]["billing_factor"] == 50
    else:
        assert not dao.dispatch("tenant", "binding", "legacy")
    first = dao.settle("tenant", "binding", "legacy", {}, Decimal("0.01"), Decimal("0.50"), {"api_version": 1})
    assert first["receipt"]["factor"] == 50
    assert first["receipt"]["credit_cost"] == "0.50"
    assert dao.settle("tenant", "binding", "legacy", {}, Decimal("0.01"), Decimal("0.20"), {}) == first
    with connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT credit_balance FROM tenants WHERE tenant_id='tenant'")
            assert cursor.fetchone()["credit_balance"] == Decimal("0.50")


def test_reserved_factor_remains_20_after_another_process_changes_policy(visual_pg, monkeypatch):
    from src.db import client_binding_db as db
    dao, _ = visual_pg
    first = dao.reserve("tenant", "binding", "frozen", "digest", "zhipu", "model", {})
    assert first["detail"]["billing_factor"] == 20
    monkeypatch.setattr(db, "BOSS_VISUAL_BILLING_FACTOR", 50)
    duplicate = dao.reserve("tenant", "binding", "frozen", "digest", "zhipu", "different", {})
    assert duplicate["detail"]["billing_factor"] == 20
    assert dao.dispatch("tenant", "binding", "frozen")
    result = dao.settle("tenant", "binding", "frozen", {}, Decimal("0.01"), Decimal("0.20"), {})
    assert result["receipt"]["factor"] == 20
    assert result["receipt"]["credit_cost"] == "0.20"


@pytest.mark.parametrize("status", ["reserved", "dispatched", "completed", "model_unknown"])
def test_runtime_device_namespace_preserves_audit_and_rejects_other_callers_in_every_state(visual_pg, status):
    """同租户另一个device/旧client不能续接、派发、置未知或结算这笔实际账务。"""
    dao, connection = visual_pg
    owner = "runtime-device:11111111-1111-4111-8111-111111111111"
    caller = {"kind": "runtime_device", "device_id": owner.split(":", 1)[1], "user_id": "user-a"}
    dao.reserve("tenant", owner, "paired-request", "digest", "zhipu", "model", {}, caller_detail=caller)
    if status != "reserved":
        assert dao.dispatch("tenant", owner, "paired-request")
    if status == "completed":
        dao.settle("tenant", owner, "paired-request", {}, Decimal("0.01"), Decimal("0.20"), {})
    elif status == "model_unknown":
        dao.mark_unknown("tenant", owner, "paired-request")
    original = dao.get("tenant", "paired-request")
    assert original["detail"]["caller"] == caller
    assert original["detail"]["billing_factor"] == 20

    for outsider in ["runtime-device:22222222-2222-4222-8222-222222222222", "legacy-client-binding"]:
        with pytest.raises(PermissionError):
            dao.reserve("tenant", outsider, "paired-request", "digest", "zhipu", "model", {})
        with pytest.raises(PermissionError):
            dao.dispatch("tenant", outsider, "paired-request")
        with pytest.raises(PermissionError):
            dao.mark_unknown("tenant", outsider, "paired-request")
        with pytest.raises(PermissionError):
            dao.settle("tenant", outsider, "paired-request", {}, Decimal("0.01"), Decimal("0.20"), {})
    with pytest.raises(PermissionError):
        dao.reserve("tenant", owner, "paired-request", "digest", "zhipu", "model", {},
                    caller_detail={**caller, "user_id": "user-b"})
    assert dao.get("another-tenant", "paired-request") is None
    assert dao.get("tenant", "paired-request") == original
    with connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT credit_balance FROM tenants WHERE tenant_id='tenant'")
            assert cursor.fetchone()["credit_balance"] == (Decimal("0.80") if status == "completed" else Decimal("1.00"))
            cursor.execute("SELECT COUNT(*) AS n FROM client_usage_logs")
            assert cursor.fetchone()["n"] == 1
