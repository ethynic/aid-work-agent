"""One real updater fresh/old CHECK upgrade/replay contract in the own DB."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

pytestmark = pytest.mark.integration

OLD_CHECK = """CHECK(jsonb_typeof(locator)='object' AND jsonb_typeof(scope)='object'
 AND jsonb_typeof(presentation)='object' AND length(payload_digest)=64 AND length(presentation_digest)=64
 AND view_revision>=0 AND control_revision>=0 AND claim_epoch>=0 AND policy_version=1
 AND phase IN ('open','sealed','closed') AND (phase='open' OR sealed_at IS NOT NULL)
 AND ((phase='closed')=(closed_at IS NOT NULL))
 AND ((phase='closed')=(closed_outcome IS NOT NULL))
 AND (closed_outcome IS NULL OR closed_outcome IN
 ('closed_accepted_known','closed_failed_known','closed_suppressed_known')))"""


def _catalog(database):
    return {
        'columns': database.rows("SELECT column_name,data_type,is_nullable,column_default FROM "
            "information_schema.columns WHERE table_schema='public' AND table_name='wecom_kf_deliveries' "
            "ORDER BY ordinal_position"),
        'constraints': database.rows("SELECT conname,pg_get_constraintdef(oid) AS definition,convalidated "
            "FROM pg_constraint WHERE conrelid='wecom_kf_deliveries'::regclass ORDER BY conname"),
        'indexes': database.rows("SELECT indexname,indexdef FROM pg_indexes WHERE schemaname='public' "
            "AND tablename='wecom_kf_deliveries' ORDER BY indexname")}


def test_actual_delivery_fresh_old_outcome_upgrade_force_and_replay_preserve_known_rows(service_database, request):
    from src.db.database import _apply_db_updates, _load_db_update_blocks
    database = service_database
    root = Path(__file__).resolve().parents[3]
    path = root / 'deploy/db_update.yaml'
    blocks = _load_db_update_blocks(path)
    index = next(i for i, block in enumerate(blocks) if block['datetime'] == '2026-10-04 00:03:10')
    # Later appends may follow this KF block; locate it by its unique datetime
    # instead of asserting it is the file's last block.
    assert sum(1 for block in blocks if block['datetime'] == '2026-10-04 00:03:10') == 1
    fresh = _catalog(database)
    # Name allocated by the original isolated wrapper, never guessed afterward.
    target = root / 'tmp/agent-runner-evidence/m6-kf-completion/final-compatibility' / (
        'ddl-owned-' + hashlib.sha256((request.node.nodeid + database.name).encode()).hexdigest() + '.json')
    allocation = {'node': request.node.nodeid, 'database_name': database.name, 'processes_root': None,
        'allocated_utc': datetime.now(timezone.utc).isoformat(), 'boundary': 'Only original own isolated DB; no process/HTTP/media.'}
    target.write_text(json.dumps(allocation, indent=2) + '\n')

    def rewind():
        database.rows("UPDATE _db_update_applied SET last_datetime=%s WHERE id='db_update'",
            (blocks[index - 1]['datetime'],))

    def apply():
        with database.connect() as connection:
            _apply_db_updates(connection, path)

    try:
        # Fresh init may retain the original legacy migration metadata shape.
        # Let the original updater bootstrap it using only the exact canonical
        # affected block; no test ALTER or shadow schema initialization.
        with TemporaryDirectory(prefix='kf_completion_updater_') as directory:
            allocation['bootstrap_root'] = directory
            target.write_text(json.dumps(allocation, indent=2) + '\n')
            bootstrap = Path(directory) / 'canonical-last-block.yaml'
            bootstrap.write_text(json.dumps([blocks[index]]))
            with database.connect() as connection:
                _apply_db_updates(connection, bootstrap)
        assert _catalog(database) == fresh
        # The actual new updater creates the missing relation, matching fresh init.
        database.rows('DROP TABLE wecom_kf_deliveries')
        rewind()
        apply()
        assert _catalog(database) == fresh
        # Exact prior validated CHECK; all other schema fields stay original.
        database.rows('ALTER TABLE wecom_kf_deliveries DROP CONSTRAINT ck_kf_delivery_shape')
        database.rows('ALTER TABLE wecom_kf_deliveries ADD CONSTRAINT ck_kf_delivery_shape ' + OLD_CHECK)
        database.rows("""INSERT INTO wecom_kf_deliveries(delivery_id,input_ref,runner_id,tenant_id,locator,
            scope,payload_digest,presentation_digest,presentation,view_revision,control_revision,
            phase,sealed_at,closed_at,closed_outcome) VALUES('migration_known_delivery',
            'migration_input','migration_runner','migration_tenant','{}','{}',%s,%s,'{}',0,0,
            'closed',clock_timestamp(),clock_timestamp(),'closed_accepted_known')""", ('a' * 64, 'b' * 64))
        prior = database.rows('SELECT * FROM wecom_kf_deliveries')
        rewind()
        apply()
        assert _catalog(database) == fresh
        assert database.rows('SELECT * FROM wecom_kf_deliveries') == prior
        apply()  # Actual watermark skip branch.
        assert _catalog(database) == fresh
        rewind()
        apply()  # Actual DO idempotence, not just watermark skip.
        assert _catalog(database) == fresh
        assert database.rows('SELECT * FROM wecom_kf_deliveries') == prior
        assert database.rows("SELECT last_datetime FROM _db_update_applied WHERE id='db_update'") == [
            {'last_datetime': blocks[-1]['datetime']}]
        # Verify the one newly allowed outcome in a rolled-back own fixture TX.
        with database.connect() as connection, connection.cursor() as cursor:
            cursor.execute("UPDATE wecom_kf_deliveries SET closed_outcome='closed_unknown'")
            connection.rollback()
        assert database.rows('SELECT * FROM wecom_kf_deliveries') == prior
    finally:
        rewind()
        apply()
        database.rows("DELETE FROM wecom_kf_deliveries WHERE delivery_id='migration_known_delivery'")
        allocation['body_finally_utc'] = datetime.now(timezone.utc).isoformat()
        allocation['boundary'] += ' Teardown/drop verified only after pytest returns.'
        target.write_text(json.dumps(allocation, indent=2) + '\n')
