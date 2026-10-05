"""Owned real-PG KF scopes; imports of mutable ingress occur only in test calls."""
from dataclasses import dataclass, field
import json
import uuid

import pytest

from .kf_ingress_peer import CallbackMaterial, KfProviderPeer


@dataclass(repr=False)
class KfScope:
    database: object = field(repr=False)
    tenant_id: str
    config_id: str
    corp_id: str
    open_kfid: str
    actor_id: str
    legacy_sid: str
    marker: str
    material: CallbackMaterial = field(repr=False)
    peer: KfProviderPeer = field(repr=False)

    def rows(self, statement, parameters=()):
        return self.database.rows(statement, parameters)

    def original_client(self, corp_id, secret):
        # External endpoint only: original SDK methods/token cache/HTTP/close.
        from src.channels.wecom_kf.api_client import WeComKfApiClient
        client = WeComKfApiClient(corp_id, secret)
        client.BASE_URL = self.peer.base_url
        return client


@pytest.fixture
def kf_scope(service_database, actors):
    marker = uuid.uuid4().hex
    corp, account = 'kf_corp_' + marker, 'kf_open_' + marker
    material = CallbackMaterial.fresh(corp)
    peer = KfProviderPeer(corp_id=corp, open_kfid=account)
    scope = KfScope(service_database, actors['a'].tenant_id, 'kf_config_' + marker,
        corp, account, 'external_full_actor_' + marker, 'legacy_kf_sid_' + marker,
        marker, material, peer)
    try:
        configuration = {'enabled': True, 'corp_id': corp, 'secret': peer.secret,
            'token': material.token, 'encoding_aes_key': material.encoding_aes_key,
            # Missing per-account profile is the actual legacy main selector.
            'kf_account': [{'open_kfid': account}]}
        with service_database.connect() as connection, connection.cursor() as cursor:
            cursor.execute("""INSERT INTO tenant_channel_configs(config_id,tenant_id,
                channel_type,name,config,verified,subagent_type,updated_at)
                VALUES(%s,%s,'wecom_kf','Fictional KF acceptance',%s,1,
                'different_top_level_profile',clock_timestamp())""",
                (scope.config_id, scope.tenant_id, json.dumps(configuration)))
            cursor.execute("""INSERT INTO channel_sessions(session_id,tenant_id,channel_type,
                channel_user_id,subagent_id,channel_chat_id,user_id,title)
                VALUES(%s,%s,'wecom_kf',%s,'',%s,NULL,'Original KF history')""",
                (scope.legacy_sid, scope.tenant_id, scope.actor_id, scope.open_kfid))
            cursor.execute("""INSERT INTO channel_messages(message_id,session_id,tenant_id,
                role,content) VALUES(%s,%s,%s,'user','Original fictional history')""",
                ('kf_history_' + marker, scope.legacy_sid, scope.tenant_id))
        yield scope
    finally:
        # Close every external HTTP thread before transaction resource cleanup.
        peer.close()
        with service_database.connect() as connection, connection.cursor() as cursor:
            cursor.execute('DELETE FROM wecom_kf_inbox WHERE config_id=%s', (scope.config_id,))
            cursor.execute('DELETE FROM channel_session_routes WHERE config_id=%s', (scope.config_id,))
            cursor.execute('DELETE FROM wecom_kf_account_sync WHERE config_id=%s', (scope.config_id,))
            cursor.execute("""DELETE FROM channel_messages WHERE session_id IN (
                SELECT session_id FROM channel_sessions WHERE tenant_id=%s
                AND channel_type='wecom_kf' AND channel_chat_id=%s)""",
                (scope.tenant_id, scope.open_kfid))
            cursor.execute("""DELETE FROM channel_sessions WHERE tenant_id=%s
                AND channel_type='wecom_kf' AND channel_chat_id=%s""",
                (scope.tenant_id, scope.open_kfid))
            cursor.execute('DELETE FROM tenant_channel_configs WHERE config_id=%s', (scope.config_id,))


def text_message(scope, message_id, *, content='Fictional incoming text', send_time=100):
    return {'msgid': message_id, 'external_userid': scope.actor_id,
        'open_kfid': scope.open_kfid, 'origin': 3, 'send_time': send_time,
        'msgtype': 'text', 'text': {'content': content}}


def seed_intent(scope, repository):
    query, body = scope.material.encrypted_callback(scope.open_kfid)
    return repository.accept_callback(scope.tenant_id, scope.config_id, query, body)

