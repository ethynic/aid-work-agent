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
    控制请求增量块（2026-09-17 20:30:00）两遍——旧四元约束被删除、五元唯一索引
    建立、相同 control/reason 不同 block epoch 两行可共存；重复执行幂等。
    临时 schema 名用 UUID（并行测试互不干扰）。

    B2 起 db_update.yaml 追加 BOSS 场景块，本用例不再指向 blocks[-1]（改为按
    datetime 定位本块），B2 块的真旧库迁移由 test_boss_tables_apply_from_old_schema
    承载（同一范式）。"""
    import uuid as _uuid
    from pathlib import Path

    from src.config.settings import load_yaml_config
    from src.db.database import get_db_connection

    project_root = Path(__file__).resolve().parents[3]
    blocks = load_yaml_config(project_root / "deploy" / "db_update.yaml")
    assert blocks, "db_update.yaml 为空"
    datetimes = [str(b["datetime"]) for b in blocks]
    assert datetimes == sorted(datetimes), "db_update.yaml datetime 必须严格递增"
    latest = next(b for b in blocks if str(b["datetime"]) == "2026-09-17 20:30:00")
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


def test_boss_tables_frozen_schema_and_module_idempotent():
    """B2（设计 §5 冻结 schema）：BOSS 场景 5 表关键列/约束/部分唯一索引在位，
    且模块 init 连续执行两次无错（幂等）。

    六审非阻断 f：information_schema/pg_indexes 查询限定 current_schema()；明确
    断言 decision/delivery 双唯一（rate_slots）、projection/anomaly 唯一、CHECK
    约束清单。"""
    from src.db.database import get_db_connection

    from src.boss_conversation.init_tables import init_boss_conversation_tables

    with get_db_connection() as conn:
        # 镜像生产顺序（database.py 启动链）：recruiting resumes → recruiting
        # timeline（自建 comm_logs 目标表）→ BOSS init 的探测式 ALTER 才有目标表。
        # 本测试自给自足，不依赖同会话其他测试先跑过启动链（否则隔离库/单跑场景
        # 下 comm_logs 不存在，投影列断言空集误报）。
        from src.services.recruiting_resume_service import init_recruiting_operator_tables
        from src.services.recruiting_resume_timeline_service import init_recruiting_timeline_tables

        init_recruiting_operator_tables(conn)
        init_recruiting_timeline_tables(conn)
        init_boss_conversation_tables(conn)
        init_boss_conversation_tables(conn)  # 重复执行必须无错
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = current_schema() AND table_name IN (
                    'bs_boss_conversation_bindings', 'bs_boss_reply_script_versions',
                    'bs_boss_conversation_rate_slots', 'bs_boss_rate_settlement_anomalies',
                    'bs_boss_comm_log_projection_queue'
                )
                """
            )
            tables = {r["table_name"] for r in cursor.fetchall()}
            assert tables == {
                "bs_boss_conversation_bindings", "bs_boss_reply_script_versions",
                "bs_boss_conversation_rate_slots", "bs_boss_rate_settlement_anomalies",
                "bs_boss_comm_log_projection_queue",
            }
            # 绑定表：频控触发计数列 + 同步阻断列 + 部分唯一索引（verified 有效唯一）
            cursor.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = current_schema() "
                "AND table_name='bs_boss_conversation_bindings'"
            )
            cols = {r["column_name"] for r in cursor.fetchall()}
            assert {
                "account_scope_id", "candidate_name", "job_id", "resume_id", "identity_version",
                "verification_status", "login_fingerprint_hash", "encrypted_identity_evidence",
                "verified_at", "expires_at", "rate_trigger_date", "rate_trigger_count",
                "last_rate_decision_id", "automation_blocked", "automation_block_reason",
                "automation_block_epoch", "automation_blocked_at",
            } <= cols
            cursor.execute(
                "SELECT indexdef FROM pg_indexes "
                "WHERE schemaname = current_schema() AND indexname='uq_boss_conv_bindings_verified'"
            )
            idx = cursor.fetchone()
            assert idx is not None and "verified" in idx["indexdef"] and "WHERE" in idx["indexdef"]
            # 频控账本：窗口部分索引 + decision/delivery 双唯一（§5.5.4 冻结）
            cursor.execute(
                """
                SELECT indexname FROM pg_indexes
                WHERE schemaname = current_schema() AND tablename='bs_boss_conversation_rate_slots'
                  AND indexname IN ('idx_boss_rate_windows','bs_boss_conversation_rate_slots_tenant_id_delivery_id_key')
                """
            )
            names = {r["indexname"] for r in cursor.fetchall()}
            assert "idx_boss_rate_windows" in names
            cursor.execute(
                """
                SELECT indexdef FROM pg_indexes
                WHERE schemaname = current_schema() AND tablename='bs_boss_conversation_rate_slots'
                  AND indexdef LIKE '%%UNIQUE%%'
                """
            )
            unique_defs = {r["indexdef"] for r in cursor.fetchall()}
            assert any("tenant_id" in d and "delivery_id" in d for d in unique_defs)
            assert any("tenant_id" in d and "decision_id" in d for d in unique_defs)
            # CHECK 约束在位（status 枚举 / settlement_effect 跨态 / 三态时间一致性）
            cursor.execute(
                """
                SELECT pg_get_constraintdef(oid) AS def FROM pg_constraint
                WHERE conrelid = (current_schema() || '.bs_boss_conversation_rate_slots')::regclass
                  AND contype = 'c'
                """
            )
            check_text = " ".join(r["def"] for r in cursor.fetchall())
            for fragment in ("'reserved'", "'settled'", "'released'", "not_started", "settled_at", "released_at"):
                assert fragment in check_text, fragment
            # 异常队列/投影队列：UNIQUE (tenant_id, delivery_id) + CHECK 逐表断言
            #（P2-3 八审：合并断言会互相掩盖——缺一张表的约束仍可能通过）
            for table, status_values in (
                ("bs_boss_rate_settlement_anomalies", ("'pending'", "'processing'", "'resolved'", "'ignored'")),
                ("bs_boss_comm_log_projection_queue", ("'pending'", "'processing'", "'done'", "'failed'")),
            ):
                cursor.execute(
                    """
                    SELECT indexdef FROM pg_indexes
                    WHERE schemaname = current_schema() AND tablename=%s
                      AND indexdef LIKE '%%UNIQUE%%'
                    """,
                    (table,),
                )
                defs = {r["indexdef"] for r in cursor.fetchall()}
                assert any("tenant_id" in d and "delivery_id" in d for d in defs), table
                cursor.execute(
                    """
                    SELECT pg_get_constraintdef(oid) AS def FROM pg_constraint
                    WHERE conrelid = (current_schema() || %s)::regclass AND contype = 'c'
                    """,
                    (f".{table}",),
                )
                table_checks = " ".join(r["def"] for r in cursor.fetchall())
                for fragment in status_values:
                    assert fragment in table_checks, (table, fragment)
                assert "retry_count" in table_checks, table
            # 绑定表关键 CHECK（验证状态枚举/计数非负/block epoch 非负）+ verified
            # 部分唯一索引定义精确断言（P2-3 八审）
            cursor.execute(
                """
                SELECT pg_get_constraintdef(oid) AS def FROM pg_constraint
                WHERE conrelid = (current_schema() || '.bs_boss_conversation_bindings')::regclass
                  AND contype = 'c'
                """
            )
            bindings_checks = " ".join(r["def"] for r in cursor.fetchall())
            for fragment in ("'pending'", "'verified'", "'invalid'", "'expired'",
                             "rate_trigger_count", "automation_block_epoch"):
                assert fragment in bindings_checks, fragment
            cursor.execute(
                """
                SELECT indexdef FROM pg_indexes
                WHERE schemaname = current_schema() AND indexname='uq_boss_conv_bindings_verified'
                """
            )
            verified_idx = cursor.fetchone()
            assert verified_idx is not None
            assert "UNIQUE" in verified_idx["indexdef"] and "verification_status" in verified_idx["indexdef"] \
                and "= 'verified'" in verified_idx["indexdef"].replace('"verification_status"', "verification_status")
            # 话术版本表（P2-3 八审/九审非阻断 2）：CHECK 单独 contype='c' 查询并
            # 规范化空白后精确断言 version_no >= 1（不被 UNIQUE 文本掩盖）；UNIQUE
            # (tenant,lineage,version_no) 继续走 pg_indexes 单独验证
            cursor.execute(
                """
                SELECT pg_get_constraintdef(oid) AS def FROM pg_constraint
                WHERE conrelid = (current_schema() || '.bs_boss_reply_script_versions')::regclass
                  AND contype = 'c'
                """
            )
            sv_checks = " ".join(" ".join(r["def"] for r in cursor.fetchall()).split())
            assert "version_no >= 1" in sv_checks, sv_checks
            assert "UNIQUE" not in sv_checks, sv_checks  # CHECK 查询确不含唯一约束文本
            cursor.execute(
                """
                SELECT indexdef FROM pg_indexes
                WHERE schemaname = current_schema() AND tablename='bs_boss_reply_script_versions'
                  AND indexdef LIKE '%%UNIQUE%%'
                """
            )
            sv_uniques = {r["indexdef"] for r in cursor.fetchall()}
            assert any(
                all(col in d for col in ("tenant_id", "lineage_id", "version_no")) for d in sv_uniques
            ), sv_uniques
            # comm_logs 补列与唯一索引（目标表存在于共享库）
            cursor.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = current_schema() AND table_name='bs_recruiting_operator_resume_comm_logs'
                  AND column_name IN ('source_delivery_id', 'source_message_id')
                """
            )
            comm_cols = {r["column_name"] for r in cursor.fetchall()}
            assert comm_cols == {"source_delivery_id", "source_message_id"}
            cursor.execute(
                "SELECT indexname FROM pg_indexes "
                "WHERE schemaname = current_schema() AND indexname='uq_boss_comm_logs_source_delivery'"
            )
            assert cursor.fetchone() is not None


def test_boss_comm_logs_alter_two_phase_recovery():
    """P1-8（六审）+ P1-C（七审）：两阶段恢复——首轮 comm_logs 目标表缺失时 BOSS
    init 幂等通过（ALTER 条件跳过、不报错、boss 表建成）；目标表恢复后再次初始化
    补齐投影列与唯一索引。对应 init_database 启动链接线（recruiting 失败场景下次
    启动自愈）。

    P1-C（七审）：全程在 UUID 临时 schema + 独立 search_path 内执行，**禁止触碰
    public/共享 schema 的任何业务表**（原实现直接 DROP 共享库 comm_logs 表，误在
    共享开发库执行会删整张沟通日志表且 finally 只能重建空表）；保留自给自足语义
    ——临时 schema 内镜像生产初始化顺序 init_recruiting_job_tables →
    init_recruiting_operator_tables → init_recruiting_timeline_tables →
    init_boss_conversation_tables。"""
    import uuid as _uuid

    from src.boss_conversation.init_tables import init_boss_conversation_tables
    from src.db.database import get_db_connection
    from src.services.recruiting_job_service import init_recruiting_job_tables
    from src.services.recruiting_resume_service import init_recruiting_operator_tables
    from src.services.recruiting_resume_timeline_service import init_recruiting_timeline_tables

    schema = f"st_mig_{_uuid.uuid4().hex[:12]}"
    conn_ctx = get_db_connection()
    conn = conn_ctx.__enter__()
    try:
        cursor = conn.cursor()
        cursor.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        cursor.execute(f"CREATE SCHEMA {schema}")
        cursor.execute(f"SET search_path = {schema}")
        conn.commit()

        # 阶段 1（目标表缺失）：recruiting job/operator（无 timeline 建表）→ BOSS init
        # ——模拟生产首启 recruiting timeline 建表失败：ALTER 条件跳过不报错，boss 表建成
        init_recruiting_job_tables(conn)
        init_recruiting_operator_tables(conn)
        init_boss_conversation_tables(conn)
        cursor.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema=%s "
            "AND table_name='bs_boss_conversation_rate_slots'",
            (schema,),
        )
        assert cursor.fetchone() is not None  # BOSS 表已建（临时 schema 内）
        cursor.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema=%s "
            "AND table_name='bs_recruiting_operator_resume_comm_logs'",
            (schema,),
        )
        assert cursor.fetchone() is None  # 目标表缺失（首轮失败形态）

        # 阶段 2（目标表恢复）：recruiting timeline 建表 → 再次 BOSS init 补齐列+索引
        init_recruiting_timeline_tables(conn)
        init_boss_conversation_tables(conn)
        cursor.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema=%s AND table_name='bs_recruiting_operator_resume_comm_logs' "
            "AND column_name IN ('source_delivery_id', 'source_message_id')",
            (schema,),
        )
        cols = {r["column_name"] for r in cursor.fetchall()}
        assert cols == {"source_delivery_id", "source_message_id"}
        cursor.execute(
            "SELECT indexdef FROM pg_indexes WHERE schemaname=%s "
            "AND indexname='uq_boss_comm_logs_source_delivery'",
            (schema,),
        )
        row = cursor.fetchone()
        assert row is not None and "UNIQUE" in row["indexdef"]
        conn.commit()
    finally:
        # P2-4（八审，落实上轮 CR）：先回滚可能 aborted 的事务——前序 SQL 失败时
        # 事务处于 aborted 状态，直接执行清理 SQL 会再次失败并残留临时 schema
        conn.rollback()
        cursor.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        cursor.execute("SET search_path = public")
        conn.commit()
        conn_ctx.__exit__(None, None, None)


def test_init_database_boss_init_after_recruiting_timeline(monkeypatch):
    """P2-4（七审）：锁定 init_database 幂等启动链调用顺序——决策冻结为
    init_boss_conversation_tables 必须在 init_recruiting_timeline_tables **之后**
    被调用（comm_logs 投影补列/唯一索引的目标表由 timeline 模块自建；错序会让
    BOSS 启动永久跳过 ALTER）。运行时记录调用序：monkeypatch 全部场景 init 函数
    为记录器、get_db_connection/_apply_db_updates 置为哑实现，真实执行
    _init_postgresql 主链（零 DB 写入）。"""
    from src.db import database

    order: list = []

    def _recorder(name):
        def _fn(conn):  # noqa: ANN001
            order.append(name)
        return _fn

    targets = [
        ("src.saas.db.tables", "init_saas_tables"),
        ("src.social_media.db", "init_social_media_tables"),
        ("src.social_media.outbound.db", "init_outbound_tables"),
        ("src.social_media.outbound.account_session_store", "init_outbound_account_sessions_table"),
        ("src.video_gen.db", "init_video_gen_tables"),
        ("src.video_agent.db", "init_video_agent_tables"),
        ("src.services.recruiting_job_service", "init_recruiting_job_tables"),
        ("src.services.recruiting_resume_service", "init_recruiting_operator_tables"),
        ("src.services.recruiting_resume_timeline_service", "init_recruiting_timeline_tables"),
        ("src.boss_conversation.init_tables", "init_boss_conversation_tables"),
        ("src.services.recruiting_notify_service", "init_recruiting_notify_tables"),
        ("src.desktop_automation.init_tables", "init_desktop_automation_tables"),
        ("src.weixin_marketing.init_tables", "init_weixin_marketing_tables"),
        ("src.session_tasks.init_tables", "init_session_task_tables"),
        ("src.weixin_conversation.init_tables", "init_weixin_conversation_tables"),
        ("src.wechat_mp.db", "init_wechat_mp_tables"),
    ]
    import importlib

    for module_name, fn_name in targets:
        module = importlib.import_module(module_name)
        monkeypatch.setattr(module, fn_name, _recorder(fn_name), raising=False)

    class _DummyCursor:
        def execute(self, *a, **k):
            return None

        def fetchone(self):
            return None

        def fetchall(self):
            return []

        def close(self):
            return None

    class _DummyConn:
        def commit(self):
            return None

        def rollback(self):
            return None

        def cursor(self, *a, **k):
            return _DummyCursor()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _DummyCtx:
        def __enter__(self):
            return _DummyConn()

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(database, "get_db_connection", lambda: _DummyCtx())
    monkeypatch.setattr(database, "_apply_db_updates", lambda conn: order.append("_apply_db_updates"))

    database._init_postgresql()

    assert "init_recruiting_timeline_tables" in order, f"启动链未执行 timeline init: {order}"
    assert "init_boss_conversation_tables" in order, f"boss init 未接入启动链: {order}"
    assert order.index("init_recruiting_timeline_tables") < order.index("init_boss_conversation_tables"), (
        f"boss init 必须晚于 recruiting timeline（目标表依赖）：{order}"
    )
    # 投影目标表的直接前置依赖：recruiting operator 简历库表先于 timeline
    assert order.index("init_recruiting_operator_tables") < order.index("init_recruiting_timeline_tables")
    # 九审非阻断 6：完整调用集合断言——启动链被改删任一场景 init 时失败（说明
    # 不再只是关键顺序的三点抽查，而是 16 个 init 全量在链上且恰好各一次）
    expected_names = {name for _, name in targets}
    recorded = [x for x in order if x in expected_names]
    assert set(recorded) == expected_names, f"启动链缺失 init: {expected_names - set(recorded)}"
    assert len(recorded) == len(expected_names), f"init 被重复调用: {recorded}"


def test_boss_tables_check_constraints_enforced():
    """B2 频控账本 CHECK 约束生效：非法 status / 跨态 settlement_effect 拒绝；
    探测行不残留（异常回滚）。"""
    import psycopg2

    from src.db.database import get_db_connection

    slot_base = (
        "INSERT INTO bs_boss_conversation_rate_slots "
        "(tenant_id, binding_id, decision_id, delivery_id, status, reserved_at{extra}) VALUES "
        "('__mig_probe__', gen_random_uuid(), gen_random_uuid(), gen_random_uuid(), {status}, NOW(){val})"
    )
    with pytest.raises(psycopg2.errors.CheckViolation):
        with get_db_connection() as conn:
            conn.cursor().execute(slot_base.format(extra="", status="'bogus'", val=""))
    # released + settlement_effect=submitted → 跨态组合拒绝（§5.5.4 冻结 CHECK）
    with pytest.raises(psycopg2.errors.CheckViolation):
        with get_db_connection() as conn:
            conn.cursor().execute(
                slot_base.format(
                    extra=", settlement_effect, released_at",
                    status="'released'", val=", 'submitted', NOW()",
                )
            )
    with pytest.raises(psycopg2.errors.CheckViolation):
        with get_db_connection() as conn:
            conn.cursor().execute(
                "INSERT INTO bs_boss_rate_settlement_anomalies "
                "(tenant_id, task_id, binding_id, delivery_id, invocation_id, error_code, status) VALUES "
                "('__mig_probe__', gen_random_uuid(), gen_random_uuid(), gen_random_uuid(), "
                "gen_random_uuid(), 'rate_slot_missing', 'bogus')"
            )
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) AS n FROM bs_boss_conversation_rate_slots WHERE tenant_id='__mig_probe__'"
        )
        assert int(cursor.fetchone()["n"]) == 0
        cursor.execute(
            "SELECT COUNT(*) AS n FROM bs_boss_rate_settlement_anomalies WHERE tenant_id='__mig_probe__'"
        )
        assert int(cursor.fetchone()["n"]) == 0


def test_boss_tables_apply_from_old_schema_and_is_repeatable():
    """B2：BOSS 增量块从**真旧 schema**（5 表不存在、投影目标表为旧列形态）执行
    两遍——5 表建成、约束/索引在位、目标表补列+唯一索引判重生效、无目标表时
    DO 块条件跳过不报错；重复执行幂等。临时 schema 名 UUID（并行互不干扰）。"""
    import uuid as _uuid
    from pathlib import Path

    from src.config.settings import load_yaml_config
    from src.db.database import _split_sql_statements, get_db_connection

    project_root = Path(__file__).resolve().parents[3]
    blocks = load_yaml_config(project_root / "deploy" / "db_update.yaml")
    boss_block = next(b for b in blocks if str(b["datetime"]) == "2026-09-18 23:00:00")
    statements = _split_sql_statements(str(boss_block["statements"]))
    assert statements, "BOSS 增量块无语句"

    legacy_comm_logs = """
        CREATE TABLE {schema}.bs_recruiting_operator_resume_comm_logs (
            id BIGSERIAL PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            resume_id BIGINT NOT NULL,
            direction TEXT NOT NULL,
            channel TEXT NOT NULL DEFAULT 'boss',
            content TEXT NOT NULL,
            user_id TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """
    schema_a = f"st_mig_{_uuid.uuid4().hex[:12]}"  # 有目标表（旧列形态）
    schema_b = f"st_mig_{_uuid.uuid4().hex[:12]}"  # 无目标表（全新库形态）
    conn_ctx = get_db_connection()
    conn = conn_ctx.__enter__()
    try:
        cursor = conn.cursor()
        for schema, with_comm in ((schema_a, True), (schema_b, False)):
            cursor.execute(f'DROP SCHEMA IF EXISTS {schema} CASCADE')
            cursor.execute(f'CREATE SCHEMA {schema}')
            if with_comm:
                cursor.execute(legacy_comm_logs.format(schema=schema))
            cursor.execute(f'SET search_path = {schema}')
            conn.commit()
            for _round in (1, 2):  # 首遍升级 + 二遍重复执行必须无错
                for stmt in statements:
                    cursor.execute(stmt)
                conn.commit()
            cursor.execute(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema=%s AND table_name LIKE 'bs_boss_%%'
                """,
                (schema,),
            )
            tables = {r["table_name"] for r in cursor.fetchall()}
            assert len(tables) == 5, tables
            # 绑定表部分唯一索引（verified 有效唯一）在位
            cursor.execute(
                """
                SELECT indexdef FROM pg_indexes
                WHERE schemaname=%s AND indexname='uq_boss_conv_bindings_verified'
                """,
                (schema,),
            )
            row = cursor.fetchone()
            assert row is not None and "WHERE" in row["indexdef"]
        # schema_a：目标表补列生效 + 唯一索引判重（同 (tenant, source_delivery_id) 二插拒绝）
        import psycopg2 as _psycopg2

        cursor.execute(
            f"INSERT INTO {schema_a}.bs_recruiting_operator_resume_comm_logs "
            f"(tenant_id, resume_id, direction, content, source_delivery_id) VALUES ('t', 1, 'out', 'x', %s)",
            (str(_uuid.uuid4()),),
        )
        conn.commit()
        cursor.execute(
            f"SELECT source_delivery_id FROM {schema_a}.bs_recruiting_operator_resume_comm_logs"
        )
        first_delivery = cursor.fetchone()["source_delivery_id"]
        with pytest.raises(_psycopg2.errors.UniqueViolation):
            cursor.execute(
                f"INSERT INTO {schema_a}.bs_recruiting_operator_resume_comm_logs "
                f"(tenant_id, resume_id, direction, content, source_delivery_id) VALUES ('t', 1, 'out', 'y', %s)",
                (first_delivery,),
            )
        conn.rollback()
        # schema_b：无目标表 → DO 块条件跳过（无 comm_logs 表、无报错）
        cursor.execute(
            "SELECT COUNT(*) AS n FROM information_schema.tables WHERE table_schema=%s "
            "AND table_name='bs_recruiting_operator_resume_comm_logs'",
            (schema_b,),
        )
        assert int(cursor.fetchone()["n"]) == 0
    finally:
        # 同 P2-4（八审）：先回滚可能 aborted 的事务，避免清理 SQL 再次失败残留临时 schema
        conn.rollback()
        cursor.execute(f'DROP SCHEMA IF EXISTS {schema_a} CASCADE')
        cursor.execute(f'DROP SCHEMA IF EXISTS {schema_b} CASCADE')
        cursor.execute('SET search_path = public')
        conn.commit()
        conn_ctx.__exit__(None, None, None)
