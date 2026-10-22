"""The real TEXT history metadata and tenant guards must work with PostgreSQL."""

import json
import uuid
from pathlib import Path

import pytest

from src.db.message_share_db import MessageShareDB
from src.services.message_sharing import service


pytestmark = pytest.mark.integration


def test_file_only_runner_shares_visible_final_products_without_a_history_reply(service_database, actors):
    from src.services.agent_runner.transaction_repository import write_history
    actor = actors['a']
    suffix = uuid.uuid4().hex
    runner_id = 'share_runner_' + suffix
    product = {'file_id': 'report_' + suffix, 'file_name': 'report.docx'}
    result = {'status': 'completed', 'output': '', 'images': [], 'messages': [],
        'assistant_metadata': {'downloadableFiles': [product]}}
    with service_database.connect() as connection, connection.cursor() as cursor:
        cursor.execute('''INSERT INTO agent_runners(runner_id,tenant_id,scope_key,session_kind,session_id,
            actor_kind,actor_id,user_id,service_id,source,client_request_id,input_digest,input,
            profile_id,profile_fingerprint,record_id,status,result)
            VALUES (%s,%s,%s,'web',%s,'user',%s,%s,'share-test','chat',%s,'digest',%s,'main','profile',%s,'completed',%s)
            RETURNING *''', (runner_id, actor.tenant_id, 'tenant:' + actor.tenant_id, actor.session_id,
                actor.user_id, actor.user_id, suffix, json.dumps({'text': 'Produce report'}), 'record_' + suffix, json.dumps(result)))
        write_history(cursor, dict(cursor.fetchone()), result)
    assert not service_database.rows("SELECT 1 FROM chat_messages WHERE session_id=%s AND role='assistant'", (actor.session_id,))
    session = {'session_id': actor.session_id, 'user_id': actor.user_id, 'tenant_id': actor.tenant_id}
    assert MessageShareDB.source_message({**session, 'user_id': actors['a_other'].user_id}, runner_id + ':assistant') is None
    created = service.create_share(session, runner_id + ':assistant')
    try:
        shared = MessageShareDB.get(created['share_key'])
        message = service.view_share(shared)['message']
        assert message['content'] == ''
        assert message['downloadableFiles'][0]['file_name'] == 'report.docx'
    finally:
        service_database.rows('DELETE FROM chat_message_shares WHERE share_key=%s', (created['share_key'],))


def test_reply_share_round_trip_and_owned_source_boundary(service_database, actors):
    actor = actors['a']
    message_id = 'share_msg_' + uuid.uuid4().hex
    service_database.rows('''INSERT INTO chat_messages(message_id,session_id,role,content,metadata)
        VALUES (%s,%s,'assistant','Selected answer',%s)''',
        (message_id, actor.session_id, json.dumps({'images': [{'file_id': 'file_picture', 'download_url': '/api/files/file_picture/download'}]})))
    session = {'session_id': actor.session_id, 'tenant_id': actor.tenant_id, 'user_id': actor.user_id}
    assert MessageShareDB.source_message(session, message_id)['content'] == 'Selected answer'
    assert MessageShareDB.source_message({**session, 'user_id': actors['a_other'].user_id}, message_id) is None
    assert MessageShareDB.source_message({**session, 'tenant_id': actors['b'].tenant_id}, message_id) is None
    result = service.create_share(session, message_id)
    try:
        stored = MessageShareDB.get(result['share_key'])
        assert stored['message_id'] == message_id
        assert service.view_share(stored)['message']['images'][0]['file_id'] == 'asset_0'
    finally:
        service_database.rows('DELETE FROM chat_message_shares WHERE share_key=%s', (result['share_key'],))


def test_share_migration_block_is_repeatable(service_database):
    from src.db.database import _load_db_update_blocks
    blocks = _load_db_update_blocks(Path(__file__).resolve().parents[3] / 'deploy/db_update.yaml')
    block = next(block for block in blocks if 'CREATE TABLE IF NOT EXISTS chat_message_shares' in block['statements'])
    for _ in range(2):
        service_database.rows(block['statements'])
    columns = service_database.rows('''SELECT column_name FROM information_schema.columns
        WHERE table_schema='public' AND table_name='chat_message_shares' ''')
    assert {item['column_name'] for item in columns} == {
        'share_key', 'tenant_id', 'user_id', 'session_id', 'message_id', 'reply_data', 'password_hash', 'created_at'}


def test_shared_kb_resources_follow_the_existing_two_part_download_grant(service_database, actors):
    reader, owner = actors['a'], actors['b']
    file_id = 'share_kb_' + uuid.uuid4().hex
    agent = 'share-test-' + uuid.uuid4().hex
    document = service_database.rows('''INSERT INTO documents(tenant_id,source_type,title)
        VALUES (%s,'shared-manual','Shared image source') RETURNING id''', (owner.tenant_id,))[0]['id']
    service_database.rows('''INSERT INTO file_assets(file_id,tenant_id,source,extra)
        VALUES (%s,%s,'knowledge_base',%s::jsonb)''',
        (file_id, owner.tenant_id, json.dumps({'linked_doc_id': document})))
    try:
        assert not MessageShareDB.resource_grant(file_id, reader.tenant_id)['allowed']
        service_database.rows('INSERT INTO tenant_knowledge_shares(from_tenant_id,to_tenant_id) VALUES (%s,%s)',
            (owner.tenant_id, reader.tenant_id))
        assert not MessageShareDB.resource_grant(file_id, reader.tenant_id)['allowed']
        service_database.rows('''INSERT INTO subagent_knowledge_sources(tenant_id,subagent_name,sources)
            VALUES (%s,%s,%s::jsonb)''', (reader.tenant_id, agent,
            json.dumps([{'owner_tenant_id': owner.tenant_id, 'source_type': 'shared-manual'}])))
        grant = MessageShareDB.resource_grant(file_id, reader.tenant_id)
        assert grant['allowed'] and grant['tenant_id'] == owner.tenant_id
        service_database.rows("UPDATE documents SET status='deleted' WHERE id=%s", (document,))
        assert not MessageShareDB.resource_grant(file_id, reader.tenant_id)['allowed']
    finally:
        service_database.rows('DELETE FROM subagent_knowledge_sources WHERE tenant_id=%s AND subagent_name=%s', (reader.tenant_id, agent))
        service_database.rows('DELETE FROM tenant_knowledge_shares WHERE from_tenant_id=%s AND to_tenant_id=%s', (owner.tenant_id, reader.tenant_id))
        service_database.rows('DELETE FROM file_assets WHERE file_id=%s', (file_id,))
        service_database.rows('DELETE FROM documents WHERE id=%s', (document,))
