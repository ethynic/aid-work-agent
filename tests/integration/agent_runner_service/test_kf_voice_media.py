"""Prepared actual SDK bytes/files and original owned codec process contracts.

The second node has explicit native SIGSTOP/caller-cancellation timing DI on
only the child returned by the original codec spawn. It proves reap/drain on
that controlled process, not general production codec capacity or valid SILK
recognition. No codec dependency is installed or decoder result substituted.
"""
import asyncio
import hashlib
import os
from pathlib import Path
import signal

import pytest

from .kf_ingress_fixtures import kf_scope
from .kf_voice_fixtures import voice_scope
from .kf_voice_peer import wav_bytes

pytestmark = pytest.mark.integration


def test_actual_sdk_media_bytes_cap_and_original_artifact_hash_symlink_scope_are_enforced(
        voice_scope, tmp_path, monkeypatch):
    from src.channels.wecom_kf.api_client import WeComKfApiClient
    from src.channels.wecom_kf.voice_media import VoiceMedia, MAX_AUDIO_BYTES
    from src.core.storage import get_conversation_dir
    from src.services.agent_runner.source_receipts import SourceUnavailable, LocalPreparationFailed
    v, s = voice_scope, voice_scope.scope
    monkeypatch.setenv('AGENT_RUNNER_STORAGE_ROOT', str(tmp_path / 'voice_artifacts'))
    preparation_ref = 'voice_' + hashlib.sha256(b'fictional media contract').hexdigest()
    good = wav_bytes()
    ids = {'good': 'good_' + s.marker, 'large': 'large_' + s.marker,
        'unproven': 'unproven_' + s.marker}
    v.peer.media[ids['good']] = (good, 'audio/wav')
    v.peer.media[ids['large']] = (b'X' * (MAX_AUDIO_BYTES + 1), 'audio/wav')
    v.peer.media[ids['unproven']] = (b'not a known audio signature', 'application/octet-stream')
    clients = []

    async def download():
        media = VoiceMedia(None)  # Only original SDK-to-artifact boundary here.
        for name in ('large', 'unproven', 'good'):
            client = WeComKfApiClient(s.corp_id, v.state_peer.foundation.secret)
            client.BASE_URL = v.peer.base_url
            client.enable_ingress_mode(65536)
            clients.append(client)
            try:
                if name == 'good':
                    artifact = await media._download(client, s.tenant_id, preparation_ref, ids[name])
                else:
                    with pytest.raises(LocalPreparationFailed) as rejected:
                        await media._download(client, s.tenant_id, preparation_ref, ids[name])
                    assert str(rejected.value) == ('SOURCE_MEDIA_DOWNLOAD_FAILED' if name == 'large'
                        else 'KF_MEDIA_AUDIO_FORMAT_UNPROVEN')
            finally:
                await client.close()
        return artifact

    artifact = asyncio.run(download())
    assert artifact['audio_format'] == 'wav' and artifact['audio_sample_rate'] == 16000
    assert artifact['file_size'] == len(good) and artifact['sha256'] == hashlib.sha256(good).hexdigest()
    assert VoiceMedia.read(s.tenant_id, artifact) == good
    assert all(client._http_client is not None and client._http_client.is_closed for client in clients)
    directory = get_conversation_dir(s.tenant_id)
    assert list(directory.iterdir()) == [Path(artifact['local_path'])]
    path = Path(artifact['local_path'])
    path.write_bytes(b'X' * len(good))
    with pytest.raises(SourceUnavailable):
        VoiceMedia.read(s.tenant_id, artifact)
    path.unlink()
    unrelated = tmp_path / 'unrelated_owned_sentinel.wav'
    unrelated.write_bytes(good)
    path.symlink_to(unrelated)
    with pytest.raises(SourceUnavailable) as rejected_read:
        VoiceMedia.read(s.tenant_id, artifact)
    assert str(rejected_read.value) == 'SOURCE_MEDIA_STORAGE_UNAVAILABLE'
    with pytest.raises(SourceUnavailable) as rejected_store:
        VoiceMedia.store(s.tenant_id, preparation_ref, good, 'wav', 16000)
    assert str(rejected_store.value) == 'SOURCE_MEDIA_STORAGE_UNAVAILABLE'
    assert path.is_symlink() and unrelated.read_bytes() == good
    path.unlink()
    other = VoiceMedia.store(s.tenant_id + '_other', preparation_ref, good, 'wav', 16000)
    with pytest.raises(SourceUnavailable):
        VoiceMedia.read(s.tenant_id, other)
    assert unrelated.read_bytes() == good
    assert [call['kind'] for call in v.peer.calls] == ['media'] * 3
    assert not v.peer.errors


def test_original_codec_bad_input_exits_and_cancelled_owned_child_is_killed_waited_and_temp_files_removed(
        tmp_path, monkeypatch):
    from src.channels.wecom_kf import voice_media
    from src.services.agent_runner.source_receipts import LocalPreparationFailed
    monkeypatch.setenv('AGENT_RUNNER_STORAGE_ROOT', str(tmp_path / 'codec_owned'))
    tenant = 'codec_fixture_tenant'
    reference = 'voice_' + hashlib.sha256(b'codec fixture reference').hexdigest()
    malformed = b'\x02#!SILK_V3' + b'\x00' * 5
    original_spawn = asyncio.create_subprocess_exec
    children = []
    held = asyncio.Event()
    hold_next = [False]

    async def observed_original_spawn(*arguments, **options):
        process = await original_spawn(*arguments, **options)
        children.append(process)
        if hold_next[0]:
            # Controlled timing IO only, not a fabricated worker/process return.
            os.kill(process.pid, signal.SIGSTOP)
            assert process.returncode is None
            held.set()
        return process

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', observed_original_spawn)

    async def original_decode():
        media = voice_media.VoiceMedia(None)
        try:
            with pytest.raises(LocalPreparationFailed) as rejected:
                await media._decode(tenant, reference, malformed)
            assert str(rejected.value) == 'SOURCE_MEDIA_CODEC_FAILED'
            assert len(children) == 1 and children[0].returncode is not None
            hold_next[0] = True
            task = asyncio.create_task(media._decode(tenant, reference, malformed))
            await asyncio.wait_for(held.wait(), 4)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 4)
            assert len(children) == 2 and children[1].returncode is not None
        finally:
            # Only unreaped handles returned by this exact original spawn.
            for process in children:
                if process.returncode is None:
                    process.kill()
                await process.wait()
        directory, descriptor = media._directory(tenant)
        os.close(descriptor)
        assert list(directory.iterdir()) == []

    asyncio.run(original_decode())
    assert all(process.returncode is not None for process in children)
