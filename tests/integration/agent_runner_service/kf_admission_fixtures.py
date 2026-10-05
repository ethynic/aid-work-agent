"""New Text-only fixtures; platform receipt setup is an explicit real-PG port.

The service, original SDK, authorizer and repositories are never replaced.
Production imports are delayed until the frozen behavioral window.
"""
import asyncio
from dataclasses import dataclass, field

import pytest

from .kf_ingress_fixtures import kf_scope, text_message
from .kf_admission_peer import KfAdmissionPeer, StateReply
from .kf_admission_service import KfSourceApi


@dataclass(repr=False)
class TextScope:
    scope: object = field(repr=False)
    platform: object = field(repr=False)
    api: object = field(repr=False)
    config: object = field(repr=False)
    account: dict = field(repr=False)
    repository: object = field(repr=False)
    serial: int = 0

    def receive(self, *messages):
        from src.channels.wecom_kf.ingress_auth import bounded_page
        from src.services.agent_runner.source_receipts import SourceLocator
        self.serial += 1
        query, body = self.scope.material.encrypted_callback(self.scope.open_kfid)
        account = self.repository.accept_callback(self.scope.tenant_id, self.scope.config_id, query, body)
        assert account['account_id'] == self.account['account_id']
        lease, _ = self.repository.claim('text_receipt_setup_' + self.scope.marker)
        assert lease is not None and lease.proof.config_id == self.scope.config_id
        try:
            result = self.repository.commit_page(lease, bounded_page({'errcode': 0,
                'has_more': 0, 'next_cursor': 'text_scope_' + str(self.serial),
                'msg_list': list(messages)}, lease.proof))
            assert result == {'received': len(messages), 'has_more': False}
        finally:
            self.repository.release(lease)
        return [SourceLocator('wecom_kf', account['account_id'], 'sync', m['msgid']) for m in messages]

    def text(self, suffix, content='Fictional text'):
        return self.receive(text_message(self.scope, suffix + self.scope.marker, content=content))[0]

    def accept(self, locator):
        from src.services.agent_runner.source_client import SourceClient
        async def request():
            client = SourceClient(self.config, token=self.api._credential)
            try:
                return await client.accept(locator)
            finally:
                await client.close()
        return asyncio.run(request())

    def post(self, locator, *, headers=None):
        return self.api.call('POST', '/v1/source-inputs', headers=headers,
            json={**locator.value(), 'client_request_id': locator.stable_key})

    def facts(self):
        return self.scope.rows("SELECT * FROM agent_runner_inputs WHERE provenance->>'config_id'=%s ORDER BY ordinal", (self.scope.config_id,))


def cleanup_text_runners(scope):
    """Only the fixture's owned tenant/SID, including new private input facts."""
    rows = scope.rows('SELECT runner_id FROM agent_runners WHERE tenant_id=%s AND session_id=%s',
        (scope.tenant_id, scope.legacy_sid))
    ids = [row['runner_id'] for row in rows]
    if ids:
        scope.rows('DELETE FROM agent_runner_inputs WHERE accepted_runner_id=ANY(%s) OR current_runner_id=ANY(%s)', (ids, ids))
        scope.rows('DELETE FROM agent_runner_usage_receipts WHERE runner_id=ANY(%s)', (ids,))
        scope.rows('DELETE FROM agent_runner_session_claims WHERE owner_runner_id=ANY(%s)', (ids,))
        scope.rows('DELETE FROM agent_runner_controls WHERE runner_id=ANY(%s)', (ids,))
        scope.rows('DELETE FROM agent_runners WHERE runner_id=ANY(%s)', (ids,))
    scope.rows('DELETE FROM chat_records WHERE tenant_id=%s AND session_id=%s', (scope.tenant_id, scope.legacy_sid))


@pytest.fixture
def text_scope(kf_scope, service_processes, provider_peer):
    from src.channels.wecom_kf.ingress_repository import KfIngressRepository
    from src.config.settings import settings
    scope = kf_scope
    repository = KfIngressRepository(scope.database.connect)
    query, body = scope.material.encrypted_callback(scope.open_kfid)
    account = repository.accept_callback(scope.tenant_id, scope.config_id, query, body)
    platform = KfAdmissionPeer(scope.peer, scope.actor_id)
    platform.states.extend(StateReply() for _ in range(32))
    api = None
    try:
        api = KfSourceApi(service_processes, platform, provider_environment=provider_peer.environment)
        config = settings.agent_runner.model_copy(deep=True)
        config.enabled = True
        config.wecom_kf.enabled = True
        config.api_url = api.url
        yield TextScope(scope, platform, api, config, account, repository)
    finally:
        if api is not None:
            api.close()
        platform.close()
        cleanup_text_runners(scope)
