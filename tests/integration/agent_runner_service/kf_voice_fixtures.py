"""Prepared original received-voice scope; production imports stay in fixtures.

No production module is imported while the implementation is mutable. Receipt
setup uses original Crypto/page/PG ports, service has its real native peer, and
all external audio/ASR/model IO is fictional localhost.
"""
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
import re
import secrets

import pytest

from .kf_ingress_fixtures import kf_scope, text_message
from .kf_admission_fixtures import TextScope, cleanup_text_runners
from .kf_admission_peer import KfAdmissionPeer, StateReply
from .kf_admission_service import KfSourceApi
from .kf_voice_peer import KfVoicePeer, wav_bytes


def original_timestamped_input(content, transcript):
    """Validate the original assembler's one timestamp header, then exact text."""
    assert isinstance(content, str)
    matched = re.fullmatch(r'\[当前时间: (\d{4}年\d{2}月\d{2}日 \d{2}:\d{2}:\d{2}), '
        r'(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday), '
        r'今年是(\d{4})年\]\n\n(.*)', content, flags=re.DOTALL)
    assert matched is not None
    instant = datetime.strptime(matched[1], '%Y年%m月%d日 %H:%M:%S')
    assert instant.strftime('%A') == matched[2] and instant.year == int(matched[3])
    assert matched[4] == transcript
    return content


@dataclass(repr=False)
class VoiceScope:
    text: object = field(repr=False)
    peer: object = field(repr=False)
    state_peer: object = field(repr=False)

    @property
    def scope(self):
        return self.text.scope

    @property
    def api(self):
        return self.text.api

    def receive_voice(self, *, recognition=None, audio=None, content_type='audio/wav'):
        identifier = 'voice_' + secrets.token_hex(12)
        media_id = 'fictional_media_' + secrets.token_hex(12)
        self.peer.media[media_id] = (wav_bytes() if audio is None else audio, content_type)
        message = text_message(self.scope, identifier)
        message.pop('text', None)
        message['msgtype'] = 'voice'
        message['voice'] = {'media_id': media_id}
        if recognition is not None:
            message['voice']['recognition'] = recognition
        return self.text.receive(message)[0], media_id

    def asr_environment(self):
        return {'ALIYUN_ASR_ACCESS_KEY_ID': 'fictional_voice_key',
            'ALIYUN_ASR_ACCESS_KEY_SECRET': 'fictional_voice_secret',
            'ALIYUN_ASR_APPKEY': self.peer._appkey,
            'KF_VOICE_ASR_FIXTURE_URL': self.peer.base_url,
            'KF_VOICE_ASR_FIXTURE_TOKEN': self.peer._asr_token}


@pytest.fixture
def voice_scope(kf_scope, service_processes, provider_peer):
    from src.channels.wecom_kf.ingress_repository import KfIngressRepository
    from src.config.settings import settings
    scope = kf_scope
    repository = KfIngressRepository(scope.database.connect)
    query, body = scope.material.encrypted_callback(scope.open_kfid)
    account = repository.accept_callback(scope.tenant_id, scope.config_id, query, body)
    state_peer = KfAdmissionPeer(scope.peer, scope.actor_id)
    state_peer.states.extend(StateReply() for _ in range(32))
    peer = KfVoicePeer(state_peer)
    api = None
    try:
        api = KfSourceApi(service_processes, peer, provider_environment=provider_peer.environment)
        config = settings.agent_runner.model_copy(deep=True)
        config.enabled = True
        config.wecom_kf.enabled = True
        config.api_url = api.url
        text = TextScope(scope, state_peer, api, config, account, repository)
        yield VoiceScope(text, peer, state_peer)
    finally:
        if api is not None:
            api.close()
        peer.close()
        state_peer.close()
        # This fixture owns these preparation rows; remove before their input
        # and Runner owners. Never broaden cleanup to another tenant or run.
        scope.rows('DELETE FROM wecom_kf_input_preparations WHERE tenant_id=%s',
            (scope.tenant_id,))
        cleanup_text_runners(scope)


@pytest.fixture
def voice_price(service_database, monkeypatch):
    """Original price DAL will read a real nonzero ASR row, never a fake resolver."""
    monkeypatch.setenv('ASR_USAGE_FACTOR', '100')
    model = 'aliyun-nls-asr'
    before = service_database.rows('SELECT asr_price_per_call FROM token_cost_prices WHERE model_name=%s', (model,))
    assert len(before) <= 1
    if before:
        service_database.rows('UPDATE token_cost_prices SET asr_price_per_call=%s WHERE model_name=%s', (Decimal('0.001'), model))
    else:
        service_database.rows('INSERT INTO token_cost_prices(model_name,asr_price_per_call) VALUES (%s,%s)', (model, Decimal('0.001')))
    try:
        yield {'model': model, 'price': Decimal('0.001'), 'factor': 100, 'successful_call_credit': Decimal('0.10')}
    finally:
        if before:
            service_database.rows('UPDATE token_cost_prices SET asr_price_per_call=%s WHERE model_name=%s', (before[0]['asr_price_per_call'], model))
        else:
            service_database.rows('DELETE FROM token_cost_prices WHERE model_name=%s', (model,))
