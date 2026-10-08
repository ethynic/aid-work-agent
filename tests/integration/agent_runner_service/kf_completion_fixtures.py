"""Owned completion preparation: original Crypto/pull SDK/PG, fictional HTTP.

Business constructors and table cleanup await the final source handoff. Nothing
imports production, allocates DB/storage or dispatches network at module load.
Existing accepted fixture files are reused unchanged.
"""
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import secrets
import time

import pytest

from .kf_completion_peer import KfCompletionPeer
from .kf_ingress_fixtures import kf_scope, seed_intent, text_message
from .kf_ingress_peer import PullPage
from .test_usage_storage import prices


def fresh_customer_text(scope, message_id, content, *, send_time=None):
    return text_message(scope, message_id, content=content,
        send_time=int(time.time()) if send_time is None else send_time)


def fresh_enter_session(scope, message_id, welcome_code, *, scene='', send_time=None):
    return {'msgid': message_id, 'external_userid': scope.actor_id,
        'open_kfid': scope.open_kfid, 'origin': 0,
        'send_time': int(time.time()) if send_time is None else send_time,
        'msgtype': 'event', 'event': {'event_type': 'enter_session',
            'external_userid': scope.actor_id, 'open_kfid': scope.open_kfid,
            'scene': scene, 'welcome_code': welcome_code}}


@dataclass(repr=False)
class CompletionScope:
    scope: object = field(repr=False)
    processes: object = field(repr=False)
    repository: object = field(repr=False)
    account: dict = field(repr=False)
    platform: object = field(repr=False)
    config: object = field(repr=False)
    welcome_code: str = field(repr=False)
    scene: str
    referrer_user_id: str
    profile_id: str
    allocation: dict = field(repr=False)
    evidence_path: Path = field(repr=False)
    serial: int = 0

    def original_client(self, corp_id, secret):
        from src.channels.wecom_kf.api_client import WeComKfApiClient
        client = WeComKfApiClient(corp_id, secret)
        client.BASE_URL = self.platform.base_url
        return client

    def original_adapter(self, corp_id, secret):
        """Original renderer/SDK/owner; only the platform HTTP address changes."""
        from src.channels.wecom_kf.adapter import WeComKfAdapter
        adapter = WeComKfAdapter(corp_id, secret)
        adapter.api_client.BASE_URL = self.platform.base_url
        return adapter

    def receive(self, *messages, expected_received=None):
        """Original signed callback -> original HTTP pull -> original page TX."""
        from src.channels.wecom_kf.ingress_worker import KfIngressWorker
        from src.services.agent_runner.source_receipts import SourceLocator
        self.serial += 1
        query, body = self.scope.material.encrypted_callback(self.scope.open_kfid)
        accepted = self.repository.accept_callback(self.scope.tenant_id,
            self.scope.config_id, query, body)
        assert accepted['account_id'] == self.account['account_id']
        self.scope.peer.script(PullPage({'errcode': 0, 'has_more': 0,
            'next_cursor': 'completion_' + str(self.serial), 'msg_list': list(messages)}))

        async def pull():
            worker = KfIngressWorker(self.config.wecom_kf, self.repository,
                client_factory=self.original_client)
            try:
                return await worker.run_once()
            finally:
                await worker.close()
        expected_count = len(messages) if expected_received is None else expected_received
        assert asyncio.run(pull()) == {'received': expected_count, 'has_more': False}
        return [SourceLocator('wecom_kf', self.account['account_id'], 'sync',
            message['msgid']) for message in messages]

    def record_resources(self, stage, children=()):
        """Actual already allocated names/handles only; not teardown proof."""
        owned = []
        for child in children:
            stat = Path('/proc') / str(child.pid) / 'stat'
            birth = stat.read_text().rsplit(')', 1)[1].split()[19] if stat.exists() else None
            owned.append({'pid': child.pid, 'start_ticks': birth,
                'actual_exit': child.poll()})
        if not children and self.evidence_path.exists():
            owned = json.loads(self.evidence_path.read_text()).get('owned_children', [])
        current = {**self.allocation, 'observation_utc': datetime.now(timezone.utc).isoformat(),
            'stage': stage, 'owned_children': owned,
            'boundary': 'Fixture observation; DB/root absence established after actual pytest teardown.'}
        self.evidence_path.write_text(json.dumps(current, indent=2) + '\n')


@pytest.fixture
def completion_scope(kf_scope, service_processes, actors, request, monkeypatch):
    from src.channels.wecom_kf.ingress_repository import KfIngressRepository
    from src.config.settings import settings
    from src.core import secret_crypto
    # Original crypto primitive with a temporary fictional fixture key. Neither
    # the encrypt/decrypt implementation nor the receipt grant is substituted.
    monkeypatch.setenv('APP_SECRET_KEY', secrets.token_urlsafe(40))
    monkeypatch.setattr(settings, 'app', settings.app.model_copy(update={'secret_key': ''}))
    monkeypatch.setattr(secret_crypto, '_fernet', None)
    # Original product configuration only, before any callback/route proof.
    # Referrer is an actual same-tenant fixture user; customer registration is
    # subsequently performed by production and never becomes Runner authority.
    scene = 'kf_' + secrets.token_hex(8)
    stored = kf_scope.rows('SELECT config FROM tenant_channel_configs WHERE config_id=%s',
        (kf_scope.config_id,))[0]['config']
    payload = json.loads(stored) if isinstance(stored, str) else stored
    profile = getattr(request, 'param', 'main')
    assert profile in {'main', 'pre-sales'}
    payload['kf_account'][0].update(scene=scene, tenant_user_id=actors['a'].user_id,
        welcome_message='Fictional original configured welcome')
    subscription = None
    if profile != 'main':
        payload['kf_account'][0]['subagent_type'] = profile
        subscription = 'completion_subscription_' + kf_scope.marker
        kf_scope.rows("INSERT INTO subscriptions(subscription_id,tenant_id,subagent_type,status,payment_status) "
            "VALUES(%s,%s,%s,'active','paid')", (subscription, kf_scope.tenant_id, profile))
        kf_scope.rows('UPDATE channel_sessions SET subagent_id=%s WHERE session_id=%s AND tenant_id=%s',
            (profile, kf_scope.legacy_sid, kf_scope.tenant_id))
    kf_scope.rows('UPDATE tenant_channel_configs SET config=%s,updated_at=clock_timestamp() WHERE config_id=%s',
        (json.dumps(payload), kf_scope.config_id))
    repository = KfIngressRepository(kf_scope.database.connect)
    account = seed_intent(kf_scope, repository)
    code = 'fictional_welcome_' + secrets.token_hex(16)
    platform = KfCompletionPeer(kf_scope.peer, kf_scope.actor_id, welcome_code=code)
    config = settings.agent_runner.model_copy(deep=True)
    config.enabled = True
    config.wecom_kf.enabled = True
    directory = Path(__file__).resolve().parents[3] / 'tmp/agent-runner-evidence/m6-kf-completion'
    directory.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256((request.node.nodeid + ':' + kf_scope.database.name + ':' + kf_scope.marker).encode()).hexdigest()[:32]
    record = {'allocated_utc': datetime.now(timezone.utc).isoformat(),
        'node': request.node.nodeid, 'database_name': kf_scope.database.name,
        'process_root': str(service_processes.root), 'allocation_id': key}
    scope = CompletionScope(kf_scope, service_processes, repository, account,
        platform, config, code, scene, actors['a'].user_id, profile,
        record, directory / ('owned-' + key + '.json'))
    scope.record_resources('allocated_before_business')
    try:
        yield scope
    finally:
        platform.close()
        # Original process and DB fixtures remain outer owners. Final schema
        # decides only this scope's extra row cleanup, not shared resources.
        scope.record_resources('platform_closed_before_outer_fixture_teardown')
        if subscription:
            kf_scope.rows('DELETE FROM subscriptions WHERE subscription_id=%s', (subscription,))


@pytest.fixture
def completion_profile_price(completion_scope, prices):
    """Real isolated PG price for the original pre-sales physical model."""
    scope = completion_scope.scope
    model = 'qwen3.8-flash'
    previous = scope.rows('SELECT * FROM token_cost_prices WHERE model_name=%s', (model,))
    if previous:
        scope.rows('UPDATE token_cost_prices SET input_price_per_m=1,output_price_per_m=2,'
            'cached_input_price_per_m=0.2,tiered_pricing=NULL WHERE model_name=%s', (model,))
    else:
        scope.rows('INSERT INTO token_cost_prices(model_name,input_price_per_m,output_price_per_m,'
            'cached_input_price_per_m) VALUES(%s,1,2,0.2)', (model,))
    try:
        yield prices
    finally:
        if previous:
            before = previous[0]
            scope.rows('UPDATE token_cost_prices SET input_price_per_m=%s,output_price_per_m=%s,'
                'cached_input_price_per_m=%s,tiered_pricing=%s::jsonb WHERE model_name=%s',
                (before['input_price_per_m'], before['output_price_per_m'],
                 before['cached_input_price_per_m'], json.dumps(before['tiered_pricing'])
                 if before['tiered_pricing'] is not None else None, model))
        else:
            scope.rows('DELETE FROM token_cost_prices WHERE model_name=%s', (model,))
