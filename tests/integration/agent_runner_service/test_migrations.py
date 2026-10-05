"""Fresh-schema vs actual incremental migration on the disposable database."""
from pathlib import Path

from psycopg2 import sql
import pytest

pytestmark = pytest.mark.integration
RUNNER_TABLES = ("agent_runners", "agent_runner_session_claims", "agent_runner_usage_receipts")
TABLES = (*RUNNER_TABLES, "agent_runner_controls")


def schema_snapshot(database):
    return {
        "columns": database.rows("""SELECT table_name,column_name,data_type,is_nullable,column_default
            FROM information_schema.columns WHERE table_schema='public' AND table_name=ANY(%s)
            ORDER BY table_name,ordinal_position""", (list(TABLES),)),
        "constraints": database.rows("""SELECT c.relname AS table_name,pg_get_constraintdef(p.oid) AS definition
            FROM pg_constraint p JOIN pg_class c ON c.oid=p.conrelid
            WHERE c.relname=ANY(%s) ORDER BY c.relname,definition""", (list(TABLES),)),
        "indexes": database.rows("""SELECT tablename,indexname,indexdef FROM pg_indexes
            WHERE schemaname='public' AND tablename=ANY(%s) ORDER BY tablename,indexname""", (list(TABLES),)),
    }


def test_actual_incremental_migration_matches_fresh_init_and_is_repeatable(service_database):
    from src.db.database import _apply_db_updates, _load_db_update_blocks
    path = Path(__file__).resolve().parents[3] / "deploy" / "db_update.yaml"
    blocks = _load_db_update_blocks(path)
    runner_blocks = [index for index, block in enumerate(blocks) if all(table in block["statements"] for table in RUNNER_TABLES)]
    assert len(runner_blocks) == 1
    index = runner_blocks[0]
    assert index > 0
    fresh = schema_snapshot(service_database)
    assert {row["table_name"] for row in fresh["columns"]} == set(TABLES)
    with service_database.connect() as connection, connection.cursor() as cursor:
        # Destroy only the runner tables in an explicitly isolated DB,
        # simulating the prior schema. No live service process exists in this module.
        # The private control ledger holds no SQL FK to its runner (referential
        # integrity is application-checked per database rules); removing
        # dependent tables before their owner stays the ordered-cleanup
        # convention the fixtures follow.
        for table in reversed(TABLES):
            cursor.execute(sql.SQL("DROP TABLE {}").format(sql.Identifier(table)))
        cursor.execute("ALTER TABLE _db_update_applied ADD COLUMN IF NOT EXISTS last_datetime TEXT")
        cursor.execute("""INSERT INTO _db_update_applied(id,file_hash,last_datetime) VALUES ('db_update','',%s)
            ON CONFLICT(id) DO UPDATE SET last_datetime=EXCLUDED.last_datetime""", (blocks[index - 1]["datetime"],))
    with service_database.connect() as connection:
        _apply_db_updates(connection, path)
    assert schema_snapshot(service_database) == fresh
    with service_database.connect() as connection:
        _apply_db_updates(connection, path)
    assert schema_snapshot(service_database) == fresh
    # Force the same actual logical block to execute twice, validating SQL
    # idempotence rather than only the migration watermark's skip branch.
    service_database.rows("UPDATE _db_update_applied SET last_datetime=%s WHERE id='db_update'", (blocks[index - 1]["datetime"],))
    with service_database.connect() as connection:
        _apply_db_updates(connection, path)
    assert schema_snapshot(service_database) == fresh
    assert service_database.rows("SELECT last_datetime FROM _db_update_applied WHERE id='db_update'")[0]["last_datetime"] == blocks[-1]["datetime"]
