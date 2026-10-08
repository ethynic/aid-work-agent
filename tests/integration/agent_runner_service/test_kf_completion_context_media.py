"""Real HTTP context media bounds; only the external media bytes are fictional.

Original Crypto/SDK classification, original context storage/history projection.
The final default10 check is a narrow SDK limit contract, not paid Voice ASR.
"""
import asyncio
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from .kf_completion_fixtures import completion_scope, kf_scope
from .kf_completion_peer import CompletionWireReply
from .kf_voice_peer import KfVoicePeer

pytestmark = pytest.mark.integration


def test_actual_human_file_eleven_mib_projects_once_over_twenty_rejects_and_voice_default_ten_remains(
        completion_scope, monkeypatch):
    from src.channels.wecom_kf.api_client import WeComKfApiClient
    from src.channels.wecom_kf.context_repository import ContextRepository
    from src.channels.wecom_kf.context_worker import ContextWorker
    from src.core.redis_client import redis_client
    from src.config.settings import settings
    c, s = completion_scope, completion_scope.scope
    # Existing isolated-test Redis-off policy; do not start a Redis container.
    assert not settings.redis.enabled and not redis_client._connected
    storage = c.processes.root / 'owned-context-media-storage'
    storage.mkdir()
    monkeypatch.setenv('AGENT_RUNNER_STORAGE_ROOT', str(storage))
    c.allocation['artifact_root'] = str(storage)
    c.record_resources('context_media_owned_storage_allocated')
    peer = KfVoicePeer(c.platform)
    good_id, huge_id = 'completion_human_file_good_' + s.marker, 'completion_human_file_huge_' + s.marker
    good = b'F' * (11 * 1024 * 1024)
    huge = b'G' * (20 * 1024 * 1024 + 1)
    peer.media[good_id] = (good, 'application/octet-stream')
    peer.media[huge_id] = (huge, 'application/octet-stream')
    c.platform.script('/cgi-bin/kf/service_state/get',
        CompletionWireReply(payload={'errcode': 0, 'service_state': 3}),
        CompletionWireReply(payload={'errcode': 0, 'service_state': 3}))

    def original_media_client(corp, secret):
        client = WeComKfApiClient(corp, secret)
        client.BASE_URL = peer.base_url
        return client

    def wire(message_id, media_id, filename):
        import time
        return {'msgid': message_id, 'external_userid': s.actor_id, 'open_kfid': s.open_kfid,
            'origin': 3, 'send_time': int(time.time()), 'msgtype': 'file',
            'file': {'media_id': media_id, 'file_name': filename}}

    worker = None
    before = s.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s', (s.tenant_id,))
    try:
        locators = c.receive(wire('context_file_good_' + s.marker, good_id, 'fictional-eleven-mib.bin'),
            wire('context_file_huge_' + s.marker, huge_id, 'fictional-too-large.bin'))

        async def process():
            nonlocal worker
            worker = ContextWorker(c.config.wecom_kf, repository=ContextRepository(s.database.connect),
                client_factory=original_media_client)
            first = await worker.run_once()
            second = await worker.run_once()
            assert first['disposition'] == second['disposition'] == 'persisted'
            assert first['history_id'] != second['history_id']
            assert await worker.run_once() is None
            return first, second
        first, second = asyncio.run(process())
        history = s.rows('SELECT message_id,role,content,metadata,attachments FROM channel_messages '
            'WHERE message_id=ANY(%s) ORDER BY id', ([first['history_id'], second['history_id']],))
        assert len(history) == 2 and all(row['role'] == 'user' and row['metadata']['source'] == 'customer_human'
            for row in history)
        by_id = {row['message_id']: row for row in history}
        attachments = json.loads(by_id[first['history_id']]['attachments'])
        assert len(attachments) == 1 and by_id[first['history_id']]['metadata']['attachments'] == attachments
        artifact = attachments[0]
        assert artifact['media_id'] == good_id and artifact['file_size'] == len(good)
        assert artifact['sha256'] == hashlib.sha256(good).hexdigest()
        actual_path = Path(artifact['local_path'])
        assert actual_path.is_relative_to(storage) and actual_path.is_file()
        assert actual_path.read_bytes() == good
        assert artifact['download_url'] in by_id[first['history_id']]['content']
        # Original context behavior preserves a visible placeholder after a
        # bounded readonly download failure, without a registered huge file.
        assert by_id[second['history_id']]['attachments'] in (None, [])
        assert by_id[second['history_id']]['metadata']['attachments'] == []
        assert by_id[second['history_id']]['content'] == '[文件] fictional-too-large.bin'
        published = [path for path in storage.rglob('*') if path.is_file()]
        assert published == [actual_path]
        assert sum(value['kind'] == 'media' for value in peer.calls) == 2
        facts = s.rows("SELECT phase,value FROM wecom_kf_business_facts WHERE account_id=%s AND business_kind='context_media'",
            (c.account['account_id'],))
        assert len(facts) == 2 and all(value['phase'] == 'known' for value in facts)

        async def default_voice_download_limit():
            client = original_media_client(s.corp_id, s.peer.secret)
            client.enable_ingress_mode(c.config.wecom_kf.page_bytes)  # Original Voice call form.
            try:
                with pytest.raises(RuntimeError, match='^KF_MEDIA_TOO_LARGE$'):
                    await client.download_media(good_id)
            finally:
                await client.close()
        asyncio.run(default_voice_download_limit())
        assert sum(value['kind'] == 'media' for value in peer.calls) == 3
        assert [path for path in storage.rglob('*') if path.is_file()] == published
        assert all(value['credential_valid'] and value['known_media'] for value in peer.calls)
        assert not peer.errors and not c.platform.errors
        assert s.rows('SELECT accepted_input_ref FROM wecom_kf_inbox WHERE account_id=%s AND message_id=ANY(%s)',
            (c.account['account_id'], [value.message_id for value in locators])) == [{'accepted_input_ref': None}] * 2
        assert s.rows('SELECT 1 FROM agent_runners WHERE tenant_id=%s AND session_id=%s', (s.tenant_id, s.legacy_sid)) == []
        assert s.rows('SELECT 1 FROM chat_records WHERE tenant_id=%s AND session_id=%s', (s.tenant_id, s.legacy_sid)) == []
        assert s.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s', (s.tenant_id,)) == before
    finally:
        if worker is not None:
            asyncio.run(worker.close())
        peer.close()
        if 'artifact' in locals():
            redis_client.delete(redis_client.make_key('uploaded_file', artifact['file_id']))
        shutil.rmtree(storage)
        assert not storage.exists()
        c.record_resources('context_media_body_owned_artifact_removed_before_outer_teardown')
