"""Prepared Context Voice IO/resource scope, never mutable business evidence.

Original Context/Crypto/SDK/PG fixtures are unchanged. Only external ASR token
retrieval and platform/ASR socket replies are fictional. Production imports stay
inside fixtures until an authorized frozen test window invokes them.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import secrets
import shutil

import pytest

from .kf_context_fixtures import context_receipts, kf_scope, human_text
from .kf_voice_peer import KfVoicePeer, AsrReply, wav_bytes


@dataclass(repr=False)
class ContextVoiceScope:
    context: object = field(repr=False)
    peer: object = field(repr=False)
    storage_root: Path = field(repr=False)
    evidence_path: Path = field(repr=False)
    allocation: dict = field(repr=False)

    @property
    def scope(self):
        return self.context.scope

    def original_client(self, corp_id, secret):
        # Real two-argument SDK; media and state use the same localhost peer.
        from src.channels.wecom_kf.api_client import WeComKfApiClient
        client = WeComKfApiClient(corp_id, secret)
        client.BASE_URL = self.peer.base_url
        return client

    def receive_voice(self, *, employee=False, recognition=None, audio=None,
            content_type='audio/wav', send_time=100):
        identifier = 'context_voice_' + secrets.token_hex(12)
        media_id = 'fictional_context_media_' + secrets.token_hex(12)
        self.peer.media[media_id] = (wav_bytes() if audio is None else audio, content_type)
        message = human_text(self.scope, identifier, '', employee=employee,
            send_time=send_time)
        message.pop('text')
        message['msgtype'], message['voice'] = 'voice', {'media_id': media_id}
        if recognition is not None:
            message['voice']['recognition'] = recognition
        return self.context.receive(message)[0], media_id

    def post_count(self):
        return sum(call['kind'] == 'asr' for call in self.peer.calls)

    def persist_resource_observation(self, stage):
        # This directory was uniquely allocated by this fixture, never shared.
        # Capture filenames/hashes, never audio, text, credentials or raw logs.
        artifacts = []
        for path in sorted(self.storage_root.rglob('*')):
            if path.is_symlink():
                artifacts.append({'path': str(path), 'symlink': True})
            elif path.is_file():
                artifacts.append({'path': str(path), 'bytes': path.stat().st_size,
                    'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
        record = {**self.allocation, 'observation_utc': datetime.now(timezone.utc).isoformat(),
            'stage': stage, 'owned_artifacts': artifacts,
            'post_count': self.post_count(),
            'scope': 'Fixture observations; root/DB removal only established by post-teardown container audit.'}
        self.evidence_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n')


@pytest.fixture
def context_voice_receipts(context_receipts, service_processes, monkeypatch, request):
    from src.config.settings import settings
    from src.core.storage import normalize_tenant_id
    from src.tools.asr.speech_to_text_tool import SpeechToTextTool
    c = context_receipts
    peer = KfVoicePeer(c.platform)
    owned_storage = service_processes.root / ('context-voice-storage-' + secrets.token_hex(10))
    owned_storage.mkdir()
    monkeypatch.setenv('AGENT_RUNNER_STORAGE_ROOT', str(owned_storage))
    # Existing settings endpoint only. No new product configuration/default.
    monkeypatch.setattr(settings.tools.asr, 'endpoint', peer.base_url)
    monkeypatch.setattr(settings.tools.asr, 'aliyun_access_key_id', 'fictional_context_voice_key')
    monkeypatch.setattr(settings.tools.asr, 'aliyun_access_key_secret', 'fictional_context_voice_secret')
    monkeypatch.setattr(settings.tools.asr, 'aliyun_appkey', peer._appkey)

    async def fictional_external_token(self, access_key_id, access_key_secret):
        return peer._asr_token

    monkeypatch.setattr(SpeechToTextTool, '_get_or_refresh_token', fictional_external_token)
    root = Path(__file__).resolve().parents[3]
    private = root / 'tmp/agent-runner-evidence/m6-kf-context-voice'
    private.mkdir(parents=True, exist_ok=True)
    allocation_id = secrets.token_hex(16)
    allocation = {'allocated_utc': datetime.now(timezone.utc).isoformat(),
        'node': request.node.nodeid, 'allocation_id': allocation_id,
        'database_name': c.scope.database.name, 'process_root': str(service_processes.root),
        'storage_root': str(owned_storage), 'tenant_id': c.scope.tenant_id,
        'conversation_root': str(owned_storage / 'tenants' / normalize_tenant_id(c.scope.tenant_id) / 'conversation'),
        'ownership': 'Dedicated fixture storage root below the already allocated test process root; no shared tenant tree.'}
    path = private / ('owned-' + allocation_id + '.json')
    v = ContextVoiceScope(c, peer, owned_storage, path, allocation)
    v.persist_resource_observation('allocated_before_business')
    try:
        yield v
    finally:
        # Worker/ContextVoice.close is the test's preceding finally. The real
        # peer drains held replies/socket threads before owned file inspection.
        peer.close()
        v.persist_resource_observation('peer_closed_before_owned_storage_cleanup')
        shutil.rmtree(owned_storage)
        assert not owned_storage.exists()
        record = json.loads(path.read_text())
        record['owned_storage_cleanup_utc'] = datetime.now(timezone.utc).isoformat()
        record['owned_storage_exists_after_fixture_cleanup'] = False
        record['process_root_and_database_teardown_pending'] = True
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n')


@pytest.fixture
def voice_price(service_database, monkeypatch):
    """Original price DAL consumes this owned real PG row; no pricing stub.

    Local to Context Voice so its normal closure does not import the entire AI
    Voice/service fixture family. The original row is restored byte-for-value.
    """
    monkeypatch.setenv('ASR_USAGE_FACTOR', '100')
    model = 'aliyun-nls-asr'
    before = service_database.rows('SELECT asr_price_per_call FROM token_cost_prices WHERE model_name=%s', (model,))
    assert len(before) <= 1
    if before:
        service_database.rows('UPDATE token_cost_prices SET asr_price_per_call=%s WHERE model_name=%s',
            (Decimal('0.001'), model))
    else:
        service_database.rows('INSERT INTO token_cost_prices(model_name,asr_price_per_call) VALUES (%s,%s)',
            (model, Decimal('0.001')))
    try:
        yield {'model': model, 'price': Decimal('0.001'), 'factor': 100,
            'successful_call_credit': Decimal('0.10')}
    finally:
        if before:
            service_database.rows('UPDATE token_cost_prices SET asr_price_per_call=%s WHERE model_name=%s',
                (before[0]['asr_price_per_call'], model))
        else:
            service_database.rows('DELETE FROM token_cost_prices WHERE model_name=%s', (model,))
