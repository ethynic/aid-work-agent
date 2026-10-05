"""Original SDK/media/codec strong drain with fictional socket scheduling.

HTTP body release is an external server IO gate. Codec cancellation uses the
actual handle returned by original spawn, held with SIGSTOP then reaped by the
original cancellation path. No valid SILK quality/capacity conclusion is made.
"""
import asyncio
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import signal
import threading
from urllib.request import Request, urlopen
from urllib.parse import urlsplit

import pytest

from .kf_context_voice_fixtures import context_voice_receipts, context_receipts, voice_price, kf_scope
from .kf_admission_peer import StateReply
from .kf_voice_peer import wav_bytes

pytestmark=pytest.mark.integration


class HeldMediaWire:
    def __init__(self,upstream):
        self.upstream=upstream
        self.arrived=threading.Event();self.release=threading.Event();self.finished=threading.Event()
        self.errors=[]
        wire=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*_): pass
            def do_GET(self): self.forward(None)
            def do_POST(self):
                size=int(self.headers.get('Content-Length','0'))
                assert 0<size<=2*1024*1024
                self.forward(self.rfile.read(size))
            def forward(self,data):
                try:
                    request=Request(wire.upstream+self.path,data=data,
                        headers={'Content-Type':'application/json'} if data is not None else {},
                        method='POST' if data is not None else 'GET')
                    with urlopen(request,timeout=10) as response:
                        body=response.read();status=response.status;mime=response.headers.get('Content-Type')
                    self.send_response(status);self.send_header('Content-Type',mime)
                    self.send_header('Content-Length',str(len(body)));self.end_headers()
                    if urlsplit(self.path).path=='/cgi-bin/media/get':
                        wire.arrived.set()
                        assert wire.release.wait(15),'OWN_MEDIA_WIRE_RELEASE_TIMEOUT'
                    self.wfile.write(body)
                except (BrokenPipeError,ConnectionResetError): pass
                except Exception as error:
                    wire.errors.append(type(error).__name__)
                finally:
                    if urlsplit(self.path).path=='/cgi-bin/media/get': wire.finished.set()
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.server.daemon_threads=False
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    @property
    def url(self): return 'http://127.0.0.1:'+str(self.server.server_port)
    def close(self):
        self.release.set();self.server.shutdown();self.server.server_close();self.thread.join(2)
        assert not self.thread.is_alive()


def test_original_media_http_and_codec_owned_tasks_drain_on_stop_without_unconfirmed_zero_io(
        context_voice_receipts,monkeypatch):
    from src.channels.wecom_kf.context_repository import ContextRepository
    from src.channels.wecom_kf.context_voice_repository import ContextVoiceRepository
    from src.channels.wecom_kf.context_worker import ContextWorker
    from src.channels.wecom_kf.voice_media import VoiceMedia,MAX_AUDIO_BYTES
    from src.services.agent_runner.source_receipts import LocalPreparationFailed,SourceUnavailable
    v,c,s=context_voice_receipts,context_voice_receipts.context,context_voice_receipts.scope
    locator,_=v.receive_voice()
    c.platform.states[:]=[StateReply({'errcode':0,'service_state':3})]
    wire=HeldMediaWire(v.peer.base_url);clients=[]
    def original_client(corp,secret):
        client=v.original_client(corp,secret);client.BASE_URL=wire.url;clients.append(client);return client
    async def drain_http():
        worker=ContextWorker(c.config.wecom_kf,repository=ContextRepository(s.database.connect),client_factory=original_client)
        operation=asyncio.create_task(worker.run_once());closing=None
        try:
            assert await asyncio.to_thread(wire.arrived.wait,10)
            assert v.post_count()==0
            closing=asyncio.create_task(worker.close())
            await asyncio.sleep(.1)
            assert not closing.done() and not operation.done()
            assert v.post_count()==0
            wire.release.set()
            assert await asyncio.wait_for(operation,10) is None
            await asyncio.wait_for(closing,10)
            assert wire.finished.is_set() and not worker.tasks and not worker.voice.tasks
        finally:
            wire.release.set()
            await asyncio.gather(operation,return_exceptions=True)
            if closing is not None: await closing
            await worker.close()
    try:
        asyncio.run(drain_http())
        assert all(client._http_client.is_closed for client in clients)
        assert ContextVoiceRepository(s.database.connect).read(locator)[4] is None
        assert s.rows('SELECT 1 FROM chat_records WHERE session_id=%s',(s.legacy_sid,))==[]
        assert s.rows('SELECT 1 FROM wecom_kf_context_consumptions WHERE account_id=%s',(c.account['account_id'],))==[]
        assert v.post_count()==0 and not wire.errors
    finally:
        wire.close()
    # Real original SDK byte bound and media authority, using only dedicated
    # fixture storage; these are port contracts, not manufactured domain facts.
    reference='voice_'+hashlib.sha256(b'owned Context media boundary').hexdigest()
    good=wav_bytes();v.peer.media['owned_large_'+s.marker]=(b'X'*(MAX_AUDIO_BYTES+1),'audio/wav')
    client=v.original_client(s.corp_id,c.platform.foundation.secret);client.enable_ingress_mode(65536)
    async def large():
        try:
            with pytest.raises(LocalPreparationFailed) as reject:
                await VoiceMedia.download_trusted(client,s.tenant_id,reference,'owned_large_'+s.marker)
            assert str(reject.value)=='SOURCE_MEDIA_DOWNLOAD_FAILED'
        finally: await client.close()
    asyncio.run(large());assert client._http_client.is_closed
    artifact=VoiceMedia.store(s.tenant_id,reference,good,'wav',16000)
    assert VoiceMedia.read(s.tenant_id,artifact)==good
    path=Path(artifact['local_path']);path.write_bytes(b'X'*len(good))
    with pytest.raises(SourceUnavailable) as wronghash: VoiceMedia.read(s.tenant_id,artifact)
    assert str(wronghash.value)=='SOURCE_MEDIA_HASH_CONFLICT'
    path.unlink();sentinel=v.storage_root/'own-unrelated-sentinel.wav';sentinel.write_bytes(good);path.symlink_to(sentinel)
    with pytest.raises(SourceUnavailable) as link: VoiceMedia.read(s.tenant_id,artifact)
    assert str(link.value)=='SOURCE_MEDIA_STORAGE_UNAVAILABLE'
    assert sentinel.read_bytes()==good and path.is_symlink();path.unlink()
    original_spawn=asyncio.create_subprocess_exec;children=[];held=asyncio.Event();stop_next=[False]
    async def observe_spawn(*args,**kwargs):
        child=await original_spawn(*args,**kwargs);children.append(child)
        stat=Path('/proc')/str(child.pid)/'stat'
        birth=stat.read_text().rsplit(')',1)[1].split()[19] if stat.exists() else None
        v.allocation.setdefault('owned_codec_children',[]).append({'pid':child.pid,'start_ticks':birth})
        v.persist_resource_observation('original_codec_child_allocated')
        if stop_next[0]:
            os.kill(child.pid,signal.SIGSTOP);held.set()
        return child
    monkeypatch.setattr(asyncio,'create_subprocess_exec',observe_spawn)
    async def codec():
        media=VoiceMedia(None);bad=b'\x02#!SILK_V3'+b'\x00'*5
        directory,descriptor=media._directory(s.tenant_id);os.close(descriptor)
        def directory_snapshot():
            snapshot={}
            for entry in directory.iterdir():
                assert not entry.is_symlink()
                kind='file' if entry.is_file() else 'directory'
                assert kind=='file'
                snapshot[entry.name]=(kind,hashlib.sha256(entry.read_bytes()).hexdigest())
            return snapshot
        baseline=directory_snapshot()
        try:
            with pytest.raises(LocalPreparationFailed) as reject:
                await media._decode(s.tenant_id,reference,bad)
            assert str(reject.value)=='SOURCE_MEDIA_CODEC_FAILED'
            assert len(children)==1 and children[0].returncode is not None
            assert directory_snapshot()==baseline
            stop_next[0]=True
            task=asyncio.create_task(media._decode(s.tenant_id,reference,bad))
            assert await asyncio.wait_for(held.wait(),4) is True
            task.cancel()
            with pytest.raises(asyncio.CancelledError): await asyncio.wait_for(task,4)
            assert len(children)==2 and children[1].returncode is not None
            assert directory_snapshot()==baseline
        finally:
            for child in children:
                if child.returncode is None: child.kill()
                await child.wait()
        assert directory_snapshot()==baseline
    asyncio.run(codec())
    for owned,child in zip(v.allocation['owned_codec_children'],children):
        owned['actual_exit']=child.returncode
    v.persist_resource_observation('media_http_codec_original_drained')
    assert all(child.returncode is not None for child in children)
    assert v.post_count()==0 and not v.peer.errors and not c.platform.errors
