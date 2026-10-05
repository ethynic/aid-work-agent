"""Context-only preparation helpers; original accepted fixtures stay unchanged.

No production import, SDK dispatch, database connection or worker is created at
module load. Tests will use the original Crypto/page/SDK/PG implementations once
the Context source is frozen. Local HTTP replies are external IO fixtures.
"""
from dataclasses import dataclass, field
import asyncio
import json

import pytest

from .kf_ingress_fixtures import kf_scope, seed_intent, text_message
from .kf_ingress_peer import PullPage
from .kf_admission_peer import KfAdmissionPeer, StateReply


def human_text(scope, message_id, content, *, employee=False, send_time=100):
    value = text_message(scope, message_id, content=content, send_time=send_time)
    if employee:
        value['origin'] = 5
        value['servicer_userid'] = 'fictional_servicer_' + scope.marker
    return value


def recall_event(scope, message_id, target_id, *, send_time=101):
    return {'msgid': message_id, 'external_userid': scope.actor_id,
        'open_kfid': scope.open_kfid, 'origin': 0, 'send_time': send_time,
        'msgtype': 'event', 'event': {'event_type': 'user_recall_msg',
            'external_userid': scope.actor_id, 'open_kfid': scope.open_kfid,
            'recall_msgid': target_id}}


@dataclass(repr=False)
class ContextReceiptScope:
    scope: object = field(repr=False)
    repository: object = field(repr=False)
    account: dict = field(repr=False)
    platform: object = field(repr=False)
    config: object = field(repr=False)
    serial: int = 0

    def original_client(self, corp_id, secret):
        from src.channels.wecom_kf.api_client import WeComKfApiClient
        client = WeComKfApiClient(corp_id, secret)
        client.BASE_URL = self.platform.base_url
        return client

    def receive(self, *messages):
        """Original callback + pull HTTP + original inbox commit, no fake proof."""
        from src.channels.wecom_kf.ingress_worker import KfIngressWorker
        from src.services.agent_runner.source_receipts import SourceLocator
        self.serial += 1
        query, body = self.scope.material.encrypted_callback(self.scope.open_kfid)
        account = self.repository.accept_callback(self.scope.tenant_id,
            self.scope.config_id, query, body)
        assert account['account_id'] == self.account['account_id']
        self.scope.peer.script(PullPage({'errcode': 0, 'has_more': 0,
            'next_cursor': 'context_scope_' + str(self.serial),
            'msg_list': list(messages)}))

        async def pull():
            worker = KfIngressWorker(self.config.wecom_kf, self.repository,
                client_factory=self.original_client)
            try:
                return await worker.run_once()
            finally:
                await worker.close()
        assert asyncio.run(pull()) == {'received': len(messages), 'has_more': False}
        return [SourceLocator('wecom_kf', self.account['account_id'], 'sync',
            message['msgid']) for message in messages]

    def original_history(self):
        """Read the real persisted history and its original servicer role mapping.

        The isolated wrapper installs the default DB factory. Neither history
        rows nor the reader/get_messages method are substituted.
        """
        from src.channels.session import channel_session_manager
        return channel_session_manager.get_conversation_context(
            self.scope.legacy_sid, max_messages=100)

    def stored_history(self):
        return self.scope.rows('SELECT message_id,role,content,metadata,is_recalled '
            'FROM channel_messages WHERE session_id=%s AND tenant_id=%s ORDER BY id',
            (self.scope.legacy_sid, self.scope.tenant_id))

    def runtime_history(self):
        """Original Runtime SessionHistory + authorized real PG reader, no fake row."""
        from src.core.agent_engine.contracts import Identity
        from src.memory.manager import MemoryManager
        from src.services.agent_runner.runtime.history import SessionHistory
        from src.services.agent_runner.runtime.history_repository import HistoryRepository
        identity = Identity(tenant_id=self.scope.tenant_id, user_id=None,
            session_id=self.scope.legacy_sid, source='wecom_kf',
            session_kind='channel')
        reader = HistoryRepository(identity)
        reader.assert_authorized()
        history = SessionHistory(MemoryManager(max_short_term_messages=100),
            'wecom_kf', reader, session_kind='channel')
        return history._load_channel_history(self.scope.legacy_sid, '')

    def receipt(self, locator):
        rows = self.scope.rows('SELECT * FROM wecom_kf_inbox WHERE account_id=%s '
            'AND namespace=%s AND message_id=%s',
            (locator.account_id, locator.namespace, locator.message_id))
        assert len(rows) == 1
        return rows[0]

    def stored_config(self):
        return self.scope.rows('SELECT config,updated_at FROM tenant_channel_configs '
            'WHERE config_id=%s', (self.scope.config_id,))[0]

    def restore_config(self, original):
        # Original TEXT bytes and timestamp, not reserialized JSON.
        self.scope.rows('UPDATE tenant_channel_configs SET config=%s,updated_at=%s '
            'WHERE config_id=%s', (original['config'], original['updated_at'],
            self.scope.config_id))
        assert self.stored_config() == original


def assert_full_history_binding(row, receipt, scope):
    metadata = row['metadata']
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    assert isinstance(metadata, dict)
    assert metadata['route_id'] == receipt['route_id']
    assert metadata['account_id'] == receipt['account_id']
    assert metadata['msgid'] == receipt['message_id']
    assert metadata['open_kfid'] == scope.open_kfid
    return metadata


@pytest.fixture
def context_receipts(kf_scope):
    """Only receipt/history setup is stable; Context application is test-owned.

    New table cleanup is finalized after the actual frozen schema is handed
    off. The isolated wrapper is the outer unconditional owned-DB cleanup.
    """
    from src.channels.wecom_kf.ingress_repository import KfIngressRepository
    from src.config.settings import settings
    repository = KfIngressRepository(kf_scope.database.connect)
    account = seed_intent(kf_scope, repository)
    platform = KfAdmissionPeer(kf_scope.peer, kf_scope.actor_id)
    config = settings.agent_runner.model_copy(deep=True)
    config.enabled = True
    config.wecom_kf.enabled = True
    try:
        yield ContextReceiptScope(kf_scope, repository, account, platform, config)
    finally:
        platform.close()
        for table in ('wecom_kf_context_task_intents', 'wecom_kf_context_consumptions',
                'wecom_kf_receipt_classifications'):
            kf_scope.rows('DELETE FROM ' + table + ' WHERE account_id=%s AND tenant_id=%s',
                (account['account_id'], kf_scope.tenant_id))
