"""Original expired-account Voice: actual recognition/history, no AI charge.

Only local external media/ASR/SDK responses and the token getter are fictional.
Original Speech, original account gate and Admission run_once remain intact.
No automatic keyword transfer is expected: the old adapter disabled it.
"""
import asyncio
from datetime import datetime, timedelta
import json
import shutil
import time
from urllib.parse import urlsplit

import pytest

from .kf_completion_fixtures import completion_scope, kf_scope
from .kf_completion_peer import CompletionWireReply
from .kf_completion_orchestration import original_admission
from .kf_admission_service import KfSourceApi
from .kf_admission_fixtures import cleanup_text_runners
from .kf_voice_peer import KfVoicePeer, AsrReply, wav_bytes
from .provider import Reply
from .test_worker import decoded

pytestmark = pytest.mark.integration


def test_actual_expired_account_voice_recognizes_once_keeps_original_history_and_has_no_ai_record(
        completion_scope, provider_peer, monkeypatch):
    from src.config.settings import settings
    from src.tools.asr.speech_to_text_tool import SpeechToTextTool
    from src.channels.wecom_kf.prompts import MSG_EXPIRED
    c, s = completion_scope, completion_scope.scope
    original_config = s.rows('SELECT config FROM tenant_channel_configs WHERE config_id=%s',
        (s.config_id,))[0]['config']
    payload = json.loads(json.dumps(decoded(original_config)))
    payload['kf_account'][0]['expire_at'] = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    s.rows('UPDATE tenant_channel_configs SET config=%s,updated_at=clock_timestamp() WHERE config_id=%s',
        (json.dumps(payload), s.config_id))
    c.platform.script('/cgi-bin/kf/service_state/get',
        *(CompletionWireReply(payload={'errcode': 0, 'service_state': 1}) for _ in range(32)))
    c.platform.script('/cgi-bin/kf/send_msg', CompletionWireReply())
    peer = KfVoicePeer(c.platform)
    # One local peer combines unchanged media/ASR IO with the original scripted
    # platform HTTP. This changes network routing, never SDK results or methods.
    handler = peer._server.RequestHandlerClass
    original_post = handler.do_POST

    def route_original_platform(self):
        if urlsplit(self.path).path in ('/cgi-bin/kf/send_msg', '/cgi-bin/kf/customer/batchget'):
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 2 * 1024 * 1024:
                return self.respond(400, b'{}', 'application/json')
            return self.forward(self.rfile.read(size))
        return original_post(self)
    handler.do_POST = route_original_platform
    transcript = 'Fictional expired account recognized customer sentence'
    peer.replies.append(AsrReply(payload={'status': 20000000, 'result': transcript}))
    media_id = 'expired_account_voice_media_' + s.marker
    peer.media[media_id] = (wav_bytes(), 'audio/wav')
    monkeypatch.setattr(settings.tools.asr, 'endpoint', peer.base_url)
    monkeypatch.setattr(settings.tools.asr, 'aliyun_access_key_id', 'fictional_blocked_voice_key')
    monkeypatch.setattr(settings.tools.asr, 'aliyun_access_key_secret', 'fictional_blocked_voice_secret')
    monkeypatch.setattr(settings.tools.asr, 'aliyun_appkey', peer._appkey)

    async def fictional_external_token(self, access_key_id, access_key_secret):
        return peer._asr_token
    monkeypatch.setattr(SpeechToTextTool, '_get_or_refresh_token', fictional_external_token)
    storage = c.processes.root / 'owned-expired-account-voice-storage'
    storage.mkdir()
    monkeypatch.setenv('AGENT_RUNNER_STORAGE_ROOT', str(storage))
    c.allocation['artifact_root'] = str(storage)
    c.record_resources('expired_voice_storage_allocated')

    def original_client(corp, secret):
        client = c.original_client(corp, secret)
        client.BASE_URL = peer.base_url
        return client

    def original_adapter(corp, secret):
        adapter = c.original_adapter(corp, secret)
        adapter.api_client.BASE_URL = peer.base_url
        return adapter

    marker = provider_peer.register(Reply(content='Expired voice must not call the model'))
    api = admission = None
    try:
        balance = s.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s', (s.tenant_id,))
        locator = c.receive({'msgid': 'expired_voice_' + s.marker, 'external_userid': s.actor_id,
            'open_kfid': s.open_kfid, 'origin': 3, 'send_time': int(time.time()),
            'msgtype': 'voice', 'voice': {'media_id': media_id}})[0]
        api = KfSourceApi(c.processes, peer)
        c.config.api_url = api.url
        from src.channels.wecom_kf.admission_worker import KfAdmissionWorker
        from src.services.agent_runner.source_client import SourceClient
        client = SourceClient(c.config, token=api._credential)
        admission = KfAdmissionWorker(c.config, s.database.connect, client=client,
            client_factory=original_client, adapter_factory=original_adapter)
        c.record_resources('expired_voice_actual_api_started', [api.child])
        for _ in range(6):
            assert asyncio.run(admission.run_once()) is None
        history = s.rows('SELECT role,content FROM channel_messages WHERE tenant_id=%s '
            'AND session_id=%s AND content=ANY(%s)',
            (s.tenant_id, s.legacy_sid, ['[ASR识别结果] ' + transcript, MSG_EXPIRED]))
        assert sorted((row['role'], row['content']) for row in history) == sorted([
            ('user', '[ASR识别结果] ' + transcript), ('assistant', MSG_EXPIRED)])
        assert sum(call['kind'] == 'asr' for call in peer.calls) == 1
        assert c.platform.count('/cgi-bin/kf/send_msg') == 1
        assert provider_peer.requests(marker) == []
        assert s.rows('SELECT accepted_input_ref FROM wecom_kf_inbox WHERE account_id=%s AND message_id=%s',
            (locator.account_id, locator.message_id)) == [{'accepted_input_ref': None}]
        assert s.rows('SELECT 1 FROM agent_runners WHERE tenant_id=%s AND session_id=%s',
            (s.tenant_id, s.legacy_sid)) == []
        assert s.rows('SELECT 1 FROM chat_records WHERE tenant_id=%s AND session_id=%s',
            (s.tenant_id, s.legacy_sid)) == []
        assert s.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s', (s.tenant_id,)) == balance
        assert not peer.errors and not c.platform.errors and not provider_peer.errors
    finally:
        if admission is not None:
            asyncio.run(admission.close())
        if api is not None:
            api.close()
        peer.close()
        shutil.rmtree(storage)
        assert not storage.exists()
        cleanup_text_runners(s)
        s.rows('UPDATE tenant_channel_configs SET config=%s,updated_at=clock_timestamp() WHERE config_id=%s',
            (json.dumps(decoded(original_config)), s.config_id))
        c.record_resources('expired_voice_owned_artifact_removed')
