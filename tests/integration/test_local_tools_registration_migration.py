"""Watermark-skipped protocol-3 repair, using real SQL in a disposable DB only."""
import os
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

import psycopg2
from psycopg2 import extras, extensions, sql
import pytest
import yaml

from src.db.database import _apply_db_updates, _load_db_update_blocks, _split_sql_statements

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]
UPDATE = ROOT / "deploy/db_update.yaml"
REPAIR_TIME = "2026-10-14 22:45:00"
OLD_WATERMARK = "2026-10-14 18:00:00"
ORIGINAL_TIME = "2026-10-14 17:00:00"
FIELDS = {"registration_id": "uuid", "registration_request_hash": "text", "registration_code_hash": "text"}


class TracingCursor(extras.RealDictCursor):
    def execute(self, query, vars=None):
        self.connection.trace.append(query if isinstance(query, str) else query.as_string(self))
        return super().execute(query, vars)


class TracingConnection(extensions.connection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.trace = []


@pytest.fixture
def registration_updates(tmp_path):
    blocks = [b for b in _load_db_update_blocks(UPDATE)
              if b["datetime"] in {ORIGINAL_TIME, REPAIR_TIME}]
    assert {b["datetime"] for b in blocks} == {ORIGINAL_TIME, REPAIR_TIME}
    scoped = tmp_path / "registration-db-update.yaml"
    scoped.write_text(yaml.safe_dump(blocks), encoding="utf-8")
    return scoped


@pytest.fixture
def legacy_schema(monkeypatch):
    target = os.environ.get("DATABASE_URL", "")
    parsed = urlsplit(target)
    name = parsed.path.lstrip("/")
    # The unified helper may use the developer PostgreSQL service, rather than
    # a loopback daemon; database ownership, not host placement, is the boundary.
    if parsed.query or parsed.fragment:
        pytest.skip("Disposable test URL must not override dbname/search_path")
    if not name.startswith("aid_test_") or not name[9:].isalnum():
        pytest.skip("Requires dev_test.sh --isolated-db nonce database")
    monkeypatch.delenv("DB_UPDATE_SKIP", raising=False)
    schema = "registration_repair_" + uuid.uuid4().hex
    conn = psycopg2.connect(target, connection_factory=TracingConnection, cursor_factory=TracingCursor)
    cur = conn.cursor()
    cur.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    cur.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
    for table in ("local_tool_devices", "local_tool_pairing_tickets", "local_tool_invocations"):
        cur.execute(sql.SQL("CREATE TABLE {} (LIKE public.{} INCLUDING ALL)").format(sql.Identifier(table), sql.Identifier(table)))
    for column in FIELDS:
        cur.execute(sql.SQL("ALTER TABLE local_tool_devices DROP COLUMN {} CASCADE").format(sql.Identifier(column)))
    cur.execute("CREATE TABLE _db_update_applied (id TEXT PRIMARY KEY, file_hash TEXT, last_datetime TEXT, applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    cur.execute("INSERT INTO _db_update_applied (id,file_hash,last_datetime) VALUES ('db_update','old-observed-file',%s)", (OLD_WATERMARK,))
    tenant, user = "migration-tenant-" + uuid.uuid4().hex, "migration-user-" + uuid.uuid4().hex
    device_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    for i, identifier in enumerate(device_ids):
        cur.execute("""INSERT INTO local_tool_devices (id,tenant_id,user_id,name,platform,runtime_version,token_hash,selected,capabilities_json)
            VALUES (%s,%s,%s,%s,'win32-x64','protocol-2',%s,%s,'{"legacy":true}')""",
            (identifier, tenant, user, "old-" + str(i), uuid.uuid4().hex + uuid.uuid4().hex, i == 0))
    cur.execute("""INSERT INTO local_tool_invocations (tenant_id,user_id,device_id,tool_name,arguments_json,state,effect)
        VALUES (%s,%s,%s,'migration-fixture','{}','running','unknown')""", (tenant, user, device_ids[0]))
    conn.commit()  # Real updater begins with rollback; persist fixture and search_path.
    try:
        yield conn, schema, tenant, user, device_ids, target
    finally:
        conn.rollback()
        cur.execute("SET search_path TO public")
        cur.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
        conn.commit()
        conn.close()


def snapshots(conn):
    cur = conn.cursor()
    cur.execute("SELECT * FROM local_tool_devices ORDER BY id")
    devices = [{k: v for k, v in row.items() if k not in FIELDS} for row in cur.fetchall()]
    cur.execute("SELECT * FROM local_tool_invocations ORDER BY id")
    return devices, [dict(row) for row in cur.fetchall()]


def apply_repair(conn, update_file):
    conn.trace.clear()
    _apply_db_updates(conn, update_file=update_file)
    # The production runner, not a manual SQL execution, selects pending blocks.
    block = next(b for b in _load_db_update_blocks(update_file) if b["datetime"] == REPAIR_TIME)
    actual = [q.strip() for q in conn.trace if q.strip().startswith(("ALTER TABLE", "CREATE UNIQUE INDEX"))]
    assert actual == _split_sql_statements(block["statements"])
    assert len(actual) == 4
    cur = conn.cursor()
    cur.execute("SELECT last_datetime FROM _db_update_applied WHERE id='db_update'")
    assert cur.fetchone()["last_datetime"] == REPAIR_TIME
    conn.commit()


def test_original_17_block_is_skipped_at_18_watermark_then_appended_repair_succeeds(legacy_schema, registration_updates, tmp_path):
    conn, schema, _, _, _, _ = legacy_schema
    before = snapshots(conn)
    original = tmp_path / "pre-compensation-db-update.yaml"
    original.write_text(yaml.safe_dump([b for b in _load_db_update_blocks(registration_updates)
                                       if b["datetime"] <= OLD_WATERMARK]), encoding="utf-8")
    conn.trace.clear()
    _apply_db_updates(conn, update_file=original)
    assert not any(q.strip().startswith(("ALTER TABLE", "CREATE UNIQUE INDEX")) for q in conn.trace)
    cur = conn.cursor()
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name='local_tool_devices' AND column_name=ANY(%s)", (schema, list(FIELDS)))
    assert cur.fetchall() == []
    cur.execute("SELECT last_datetime FROM _db_update_applied WHERE id='db_update'")
    assert cur.fetchone()["last_datetime"] == OLD_WATERMARK
    assert snapshots(conn) == before
    conn.commit()
    apply_repair(conn, registration_updates)
    assert snapshots(conn) == before


@pytest.mark.parametrize("partially_present", [False, True])
def test_watermark_above_original_block_repairs_schema_without_changing_existing_facts(legacy_schema, registration_updates, partially_present):
    conn, schema, _, _, identifiers, _ = legacy_schema
    cur = conn.cursor()
    if partially_present:
        cur.execute("ALTER TABLE local_tool_devices ADD COLUMN registration_id UUID")
        cur.execute("UPDATE local_tool_devices SET registration_id=%s WHERE id=%s", (str(uuid.uuid4()), identifiers[1]))
        conn.commit()
    before = snapshots(conn)
    cur.execute("SELECT registration_id FROM local_tool_devices WHERE id=%s", (identifiers[1],)) if partially_present else None
    preserved_id = cur.fetchone()["registration_id"] if partially_present else None
    apply_repair(conn, registration_updates)
    assert snapshots(conn) == before
    cur.execute("SELECT column_name,data_type,is_nullable FROM information_schema.columns WHERE table_schema=%s AND table_name='local_tool_devices' AND column_name=ANY(%s)", (schema, list(FIELDS)))
    rows = cur.fetchall()
    assert {r["column_name"]: r["data_type"] for r in rows} == FIELDS
    assert all(r["is_nullable"] == "YES" for r in rows)
    cur.execute("SELECT registration_id,registration_request_hash,registration_code_hash FROM local_tool_devices WHERE id=%s", (identifiers[1],))
    row = cur.fetchone()
    assert row["registration_id"] == preserved_id
    assert row["registration_request_hash"] is None and row["registration_code_hash"] is None
    cur.execute("SELECT i.indexrelid, i.indisunique, pg_get_expr(i.indpred,i.indrelid) predicate FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND c.relname='uq_lt_devices_registration'", (schema,))
    index = cur.fetchone()
    assert index["indisunique"] and index["predicate"] == "(registration_id IS NOT NULL)"
    # Null registrations remain valid; duplicate nonnull identity must be rejected.
    cur.execute("SAVEPOINT index_behavior")
    same_id = str(uuid.uuid4())
    cur.execute("UPDATE local_tool_devices SET registration_id=%s WHERE id=%s", (same_id, identifiers[0]))
    with pytest.raises(psycopg2.errors.UniqueViolation):
        cur.execute("UPDATE local_tool_devices SET registration_id=%s WHERE id=%s", (same_id, identifiers[1]))
    cur.execute("ROLLBACK TO SAVEPOINT index_behavior")
    conn.commit()
    cur.execute("SELECT last_datetime,applied_at FROM _db_update_applied WHERE id='db_update'")
    metadata = dict(cur.fetchone())
    conn.trace.clear()
    _apply_db_updates(conn, update_file=registration_updates)
    assert not any(q.strip().startswith(("ALTER TABLE", "CREATE UNIQUE INDEX")) for q in conn.trace)
    cur.execute("SELECT last_datetime,applied_at FROM _db_update_applied WHERE id='db_update'")
    assert dict(cur.fetchone()) == metadata
    conn.commit()
    # Force another pending repair in this owned schema to exercise IF NOT EXISTS.
    cur.execute("UPDATE _db_update_applied SET last_datetime=%s WHERE id='db_update'", (OLD_WATERMARK,))
    conn.commit()
    apply_repair(conn, registration_updates)
    assert snapshots(conn) == before
    cur.execute("SELECT 'uq_lt_devices_registration'::regclass::oid AS oid")
    assert cur.fetchone()["oid"] == index["indexrelid"]


def test_protocol3_pair_and_lost_response_retry_work_after_real_repair(legacy_schema, registration_updates, monkeypatch):
    from src.local_tools import pairing, repository
    from src.local_tools.security import generate_device_token, sha256_hex
    conn, schema, tenant, user, identifiers, target = legacy_schema
    before = snapshots(conn)
    apply_repair(conn, registration_updates)

    @contextmanager
    def scoped_connection():
        scoped = psycopg2.connect(target, cursor_factory=extras.RealDictCursor,
                                  options="-c search_path=" + schema + ",public")
        try:
            yield scoped
        finally:
            scoped.close()

    monkeypatch.setattr(repository, "get_db_connection", scoped_connection)
    code = pairing.create_pairing_ticket(tenant, user)["code"]
    values = dict(code=code, pairing_protocol=3, registration_id=str(uuid.uuid4()),
                  registration_token=generate_device_token(), name="repaired", platform="win32-x64", runtime_version="fixture")
    first = pairing.pair(**values)
    assert first["registration_completed"] and first["pairing_protocol"] == 3
    assert repository.get_device_by_token_hash(sha256_hex(values["registration_token"])) is not None
    cur = conn.cursor()
    cur.execute("UPDATE local_tool_pairing_tickets SET expires_at=NOW()-INTERVAL '1 day'")
    conn.commit()
    assert repository.select_device(tenant, user, identifiers[0])
    values["code"] = None
    assert pairing.pair(**values) == first
    devices = repository.list_devices(tenant, user)
    assert len(devices) == 3
    assert next(row for row in devices if str(row["id"]) == identifiers[0])["selected"]
    assert not next(row for row in devices if str(row["id"]) == first["device_id"])["selected"]
    assert snapshots(conn)[1] == before[1]
