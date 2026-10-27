"""Run only through explicit task-owned dev_test.sh --isolated-db."""
from dataclasses import replace
import os
from pathlib import Path
import uuid

import pytest

from scripts.testing_db_guard import TestDatabaseSafetyError as DatabaseSafetyError, target_url

pytestmark = pytest.mark.integration


def test_real_business_and_trace_connections_use_same_exact_selected_database():
    from src.db import database
    target = target_url(os.environ['DATABASE_URL'])
    assert target_url(os.environ['LOGS_DATABASE_URL']) == target
    with database.get_db_connection() as connection:
        connection.execute('SELECT pg_catalog.current_database() AS database')
        assert connection.fetchone()['database'] == target.database
    owned = database._logs_connection_pool is None
    try:
        if owned:
            assert database.init_logs_pool()
        with database.get_logs_connection() as cursor:
            cursor.execute('SELECT pg_catalog.current_database() AS database')
            assert cursor.fetchone()['database'] == target.database
    finally:
        if owned:
            database.close_logs_pool()


def test_actual_database_mismatch_cannot_delete_an_owned_test_tenant(request, monkeypatch):
    from src.db.database import get_db_connection
    root = Path(__file__).resolve().parents[2]
    conftest = next(plugin for _, plugin in request.config.pluginmanager.list_name_plugin()
                    if getattr(plugin, '__file__', None) and Path(plugin.__file__).resolve() == root/'tests/conftest.py')
    tenant = 'guard-'+uuid.uuid4().hex
    with get_db_connection() as connection:
        connection.execute("INSERT INTO tenants(tenant_id,tenant_code,company_name) VALUES(%s,%s,%s)",
                           (tenant, 'G'+uuid.uuid4().hex[:7], '测试租户-guard-owned'))
        connection.commit()
    try:
        with monkeypatch.context() as context:
            context.setattr(conftest, '_TEST_DATABASE', replace(conftest._TEST_DATABASE, database='aid_test_wrong_actual'))
            with pytest.raises(DatabaseSafetyError, match='ACTUAL_TARGET_MISMATCH'):
                conftest._purge_test_tenants()
        with get_db_connection() as connection:
            connection.execute('SELECT tenant_id FROM tenants WHERE tenant_id=%s', (tenant,))
            assert connection.fetchone()['tenant_id'] == tenant
    finally:
        with get_db_connection() as connection:
            connection.execute('DELETE FROM tenants WHERE tenant_id=%s', (tenant,))
            connection.commit()
