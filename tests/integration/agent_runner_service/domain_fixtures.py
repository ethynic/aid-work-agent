"""Disposable real recruiting data for forthcoming Local domain-flow tests."""
from io import BytesIO
import base64
import secrets
import uuid

import pytest


def fictional_resume_image():
    """Valid tiny PNG, containing no real resume/person or embedded metadata."""
    from PIL import Image
    stream = BytesIO()
    Image.new('RGB', (64, 96), 'white').save(stream, format='PNG')
    return base64.b64encode(stream.getvalue()).decode()


@pytest.fixture
def recruiting_domain(workers, actors, service_database, monkeypatch):
    from src.db.database import get_db_connection
    from src.services.recruiting_job_service import init_recruiting_job_tables
    from src.services.recruiting_resume_service import init_recruiting_operator_tables
    from src.services.recruiting_resume_timeline_service import init_recruiting_timeline_tables
    from src.services.recruiting_notify_service import init_recruiting_notify_tables
    from src.core import secret_crypto

    # Normal lazy domain schema initialization, not bespoke acceptance tables.
    # Check the real domain pool target as well as the fixture connection.
    with get_db_connection() as connection:
        cursor = connection.cursor()
        cursor.execute('SELECT current_database() AS name')
        assert cursor.fetchone()['name'] == service_database.name
    with service_database.connect() as connection:
        init_recruiting_job_tables(connection)
        init_recruiting_operator_tables(connection)
        init_recruiting_timeline_tables(connection)
        init_recruiting_notify_tables(connection)
        connection.commit()

    # A fictional key shared only between this fixture's parent and subprocess.
    # The real settings owner still encrypts/decrypts the localhost webhook.
    fixture_key = secrets.token_urlsafe(32)
    monkeypatch.setenv('RPA_SECRET_KEY', fixture_key)
    monkeypatch.setenv('APP_SECRET_KEY', fixture_key)
    # Reset only this process's memoized real encryptor. monkeypatch restores it;
    # encryption/decryption themselves and all child code remain unchanged.
    monkeypatch.setattr(secret_crypto, '_fernet', None)
    workers.environment.update(RPA_SECRET_KEY=fixture_key, APP_SECRET_KEY=fixture_key)
    profile = 'recruiting-operator'
    subscription = 'fixture-local-domain-' + uuid.uuid4().hex
    actor = actors['a']
    service_database.rows("INSERT INTO subscriptions(subscription_id,tenant_id,subagent_type,status,payment_status) VALUES(%s,%s,%s,'active','paid')",
                          (subscription, actor.tenant_id, profile))
    service_database.rows('INSERT INTO user_agent_permissions(user_id,tenant_id,agent_id) VALUES(%s,%s,%s)',
                          (actor.user_id, actor.tenant_id, profile))
    try:
        yield actor, profile
    finally:
        tenant_ids = [actors[label].tenant_id for label in ('a', 'b')]
        # Foreign-scope fixture rows are included, never wildcard tenant names.
        for table in ('bs_recruiting_notify_logs', 'bs_recruiting_notify_settings',
                      'bs_recruiting_operator_resume_comm_logs',
                      'bs_recruiting_operator_resume_invitations',
                      'bs_recruiting_operator_resumes',
                      'bs_recruiting_operator_job_scripts', 'bs_recruiting_operator_jobs',
                      'client_usage_logs'):
            service_database.rows(f'DELETE FROM {table} WHERE tenant_id=ANY(%s)', (tenant_ids,))
        service_database.rows('DELETE FROM local_tool_events WHERE invocation_id IN (SELECT id FROM local_tool_invocations WHERE tenant_id=ANY(%s))', (tenant_ids,))
        service_database.rows('DELETE FROM local_tool_invocations WHERE tenant_id=ANY(%s)', (tenant_ids,))
        service_database.rows('DELETE FROM local_tool_devices WHERE tenant_id=ANY(%s)', (tenant_ids,))
        service_database.rows('DELETE FROM subscriptions WHERE subscription_id=%s', (subscription,))
        service_database.rows('DELETE FROM user_agent_permissions WHERE user_id=%s AND tenant_id=%s AND agent_id=%s',
                              (actor.user_id, actor.tenant_id, profile))
