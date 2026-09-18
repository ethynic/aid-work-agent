"""DDL 幂等（空库/既有库重复运行的一半：init 重复执行必须无错且结构不变）。"""

import pytest

from src.session_tasks.init_tables import init_session_task_tables
from src.weixin_conversation.init_tables import init_weixin_conversation_tables


def test_init_tables_idempotent():
    """init_session_task_tables 连续执行两次无错（CREATE IF NOT EXISTS 幂等）。"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        init_session_task_tables(conn)
        init_session_task_tables(conn)
        init_weixin_conversation_tables(conn)
        init_weixin_conversation_tables(conn)
        with conn.cursor() as cursor:
            cursor.execute("SELECT count(*) AS c FROM session_tasks WHERE tenant_id='__none__'")
            assert cursor.fetchone()["c"] == 0


def test_expected_columns_present():
    """关键契约列存在（占用索引/双唯一/合成批次列等）。"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_name='session_tasks' AND column_name IN
                ('draft_digest','control_epoch','server_control_seq','spec_revision')
                """
            )
            cols = {r["column_name"] for r in cursor.fetchall()}
            assert cols == {"draft_digest", "control_epoch", "server_control_seq", "spec_revision"}
            cursor.execute(
                """
                SELECT indexname FROM pg_indexes
                WHERE tablename='session_tasks' AND indexname='idx_session_tasks_occupancy'
                """
            )
            assert cursor.fetchone() is not None


def test_control_requests_table_frozen_schema():
    """B1.2 控制请求表（设计 §5.5.5 冻结 schema）：关键列/唯一约束/索引在位，
    且重复初始化幂等（增量升级与全新初始化双路径同构）。"""
    from src.db.database import get_db_connection

    from src.session_tasks.init_tables import init_session_task_tables

    with get_db_connection() as conn:
        init_session_task_tables(conn)  # 重复执行必须无错
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_name='session_task_control_requests'
                """
            )
            cols = {r["column_name"] for r in cursor.fetchall()}
            expected = {
                "id", "tenant_id", "task_id", "expected_control_epoch", "expected_block_epoch",
                "reason", "source_type", "source_ref", "status", "processing_owner",
                "processing_lease_expires_at", "retry_count", "next_retry_at",
                "created_at", "updated_at",
            }
            assert expected <= cols
            cursor.execute(
                """
                SELECT pg_get_constraintdef(oid) AS def FROM pg_constraint
                WHERE conrelid = 'session_task_control_requests'::regclass
                  AND contype IN ('u','c')
                """
            )
            constraints = {r["def"] for r in cursor.fetchall()}
            assert any("CHECK" in d and "status" in d for d in constraints)
            assert any("CHECK" in d and "retry_count" in d for d in constraints)
            # CR 三审 P1-1：幂等键含 expected_block_epoch（唯一索引形态）
            cursor.execute(
                """
                SELECT indexdef FROM pg_indexes
                WHERE tablename='session_task_control_requests'
                  AND indexname='uq_session_task_control_requests_idem'
                """
            )
            idx_def = cursor.fetchone()
            assert idx_def is not None, "缺幂等唯一索引 uq_session_task_control_requests_idem"
            assert "expected_block_epoch" in idx_def["indexdef"]
            assert "reason" in idx_def["indexdef"]
            cursor.execute(
                """
                SELECT indexname FROM pg_indexes
                WHERE tablename='session_task_control_requests'
                  AND indexname='idx_session_task_control_requests_scan'
                """
            )
            assert cursor.fetchone() is not None


def test_control_requests_check_constraints_enforced():
    """CHECK 约束生效：非法 status / 负 retry_count 拒绝；探测行不残留（异常回滚）。"""
    import psycopg2

    from src.db.database import get_db_connection

    base = (
        "INSERT INTO session_task_control_requests "
        "(tenant_id, task_id, expected_control_epoch, expected_block_epoch, "
        "reason, source_type, source_ref{extra}) VALUES "
        "('__mig_probe__', gen_random_uuid(), 0, 0, 'r', 'permit_denied', 'ref'{val})"
    )
    with pytest.raises(psycopg2.errors.CheckViolation):
        with get_db_connection() as conn:
            conn.cursor().execute(base.format(extra=", status", val=", 'bogus'"))
    with pytest.raises(psycopg2.errors.CheckViolation):
        with get_db_connection() as conn:
            conn.cursor().execute(base.format(extra=", retry_count", val=", -1"))
    from src.db.database import get_db_connection as _g

    with _g() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) AS n FROM session_task_control_requests WHERE tenant_id='__mig_probe__'")
        assert cursor.fetchone()["n"] == 0


def test_latest_db_update_block_applies_from_old_schema_and_is_repeatable():
    """CR 四审补齐8：从**真正的上一版 schema**（旧四元 UNIQUE 表已存在）执行
    最新 db_update.yaml 增量块两遍——旧四元约束被删除、五元唯一索引建立、
    相同 control/reason 不同 block epoch 两行可共存；重复执行幂等。
    临时 schema 名用 UUID（并行测试互不干扰）。"""
    import uuid as _uuid
    from pathlib import Path

    from src.config.settings import load_yaml_config
    from src.db.database import get_db_connection

    project_root = Path(__file__).resolve().parents[3]
    blocks = load_yaml_config(project_root / "deploy" / "db_update.yaml")
    assert blocks, "db_update.yaml 为空"
    datetimes = [str(b["datetime"]) for b in blocks]
    assert datetimes == sorted(datetimes), "db_update.yaml datetime 必须严格递增"
    latest = blocks[-1]
    statements = [
        stmt.strip() for stmt in str(latest["statements"]).split(";") if stmt.strip()
    ]
    assert statements, "最新增量块无语句"
    schema = f"st_mig_{_uuid.uuid4().hex[:12]}"
    legacy_constraint = "session_task_control_requests_tenant_id_task_id_expected_co_key"
    legacy_ddl = f"""
        CREATE TABLE {schema}.session_task_control_requests (
            id BIGSERIAL PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            task_id UUID NOT NULL,
            expected_control_epoch INTEGER NOT NULL,
            expected_block_epoch INTEGER NOT NULL,
            reason TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_ref TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            processing_owner TEXT,
            processing_lease_expires_at TIMESTAMPTZ,
            retry_count INTEGER NOT NULL DEFAULT 0,
            next_retry_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW(),
            CONSTRAINT {legacy_constraint}
                UNIQUE (tenant_id, task_id, expected_control_epoch, reason),
            CHECK (status IN ('pending','processing','applied','stale','failed')),
            CHECK (retry_count >= 0)
        )
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            # 上一版 schema：表存在、带旧四元 UNIQUE（同当前共享库形态）
            cursor.execute(f'DROP SCHEMA IF EXISTS {schema} CASCADE')
            cursor.execute(f'CREATE SCHEMA {schema}')
            cursor.execute(legacy_ddl)
            cursor.execute(f'SET search_path = {schema}')
            conn.commit()
            for _round in (1, 2):  # 第一遍从旧 schema 升级；第二遍重复执行必须无错
                for stmt in statements:
                    cursor.execute(stmt)
                conn.commit()
            # 旧四元约束已被删除
            cursor.execute(
                """
                SELECT conname FROM pg_constraint
                WHERE conrelid = %(rel)s::regclass AND contype = 'u'
                """,
                {"rel": f"{schema}.session_task_control_requests"},
            )
            remaining = [r["conname"] for r in cursor.fetchall()]
            assert legacy_constraint not in remaining, remaining
            # 五元唯一索引在位且含 block epoch
            cursor.execute(
                """
                SELECT indexdef FROM pg_indexes
                WHERE schemaname=%s AND tablename='session_task_control_requests'
                  AND indexname='uq_session_task_control_requests_idem'
                """,
                (schema,),
            )
            idx = cursor.fetchone()
            assert idx is not None and "expected_block_epoch" in idx["indexdef"]
            # 同 control/reason 不同 block epoch 两行可共存（四元键吞行场景回归）
            task_id = str(_uuid.uuid4())
            cursor.execute(
                f"""
                INSERT INTO {schema}.session_task_control_requests
                    (tenant_id, task_id, expected_control_epoch, expected_block_epoch,
                     reason, source_type, source_ref)
                VALUES ('t', %s, 1, 1, 'fingerprint_mismatch', 'permit_denied', 'a'),
                       ('t', %s, 1, 2, 'fingerprint_mismatch', 'permit_denied', 'b')
                """,
                (task_id, task_id),
            )
            conn.commit()
            cursor.execute(
                f"SELECT COUNT(*) AS n FROM {schema}.session_task_control_requests WHERE task_id=%s",
                (task_id,),
            )
            assert int(cursor.fetchone()["n"]) == 2
        finally:
            cursor.execute(f'DROP SCHEMA IF EXISTS {schema} CASCADE')
            cursor.execute('SET search_path = public')
            conn.commit()
