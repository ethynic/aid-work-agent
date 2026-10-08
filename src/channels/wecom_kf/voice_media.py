"""Bounded native media and fixed codec; trusted tenant artifacts, never prompts."""

import asyncio
import hashlib
import os
from pathlib import Path
import sys
import uuid
from src.core.storage import get_conversation_dir,configured_storage_root
from src.services.agent_runner.source_receipts import source_offload, SourceUnavailable, SourceLocator, LocalPreparationFailed
from src.utils.audio_format import detect_audio_format, wrap_pcm_as_wav


MAX_AUDIO_BYTES=10*1024*1024
MAX_DOWNLOAD_BYTES=20*1024*1024
_CODEC = """import resource,sys,os
resource.setrlimit(resource.RLIMIT_FSIZE,(10485760,10485760))
from src.utils.audio_format import decode_silk_to_wav
directory=int(sys.argv[1])
source=os.open(sys.argv[2],os.O_RDONLY|os.O_NOFOLLOW,dir_fd=directory)
with os.fdopen(source,'rb') as f: data=f.read(20971521)
if len(data)>20971520: raise ValueError('AUDIO_TOO_LARGE')
result=decode_silk_to_wav(data,sample_rate=16000)
if len(result)>10485760: raise ValueError('AUDIO_TOO_LARGE')
target=os.open(sys.argv[3],os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=directory)
with os.fdopen(target,'wb') as f: f.write(result)
"""


class VoiceMedia:
    def __init__(self,provider):
        self.provider=provider

    @classmethod
    async def download_trusted(cls,client,tenant_id,preparation_ref,media_id):
        """Context adapter owns this already authorized client, not an AI input."""
        client.enable_ingress_mode(client._ingress_bytes,media_max_bytes=MAX_DOWNLOAD_BYTES)
        try:
            async with asyncio.timeout(40):
                return await cls(None)._download(client,tenant_id,preparation_ref,media_id)
        except TimeoutError as error:
            raise LocalPreparationFailed('SOURCE_MEDIA_DOWNLOAD_TIMEOUT') from error

    async def download(self,fact,preparation_ref):
        from src.services.agent_runner.source_preparation import drain_owned
        current,inbox,_,_=await source_offload(self.provider._read,SourceLocator(**fact['locator']))
        client=self.provider.client_factory(current.proof.corp_id,current.secret)
        client.enable_ingress_mode(self.provider.config.wecom_kf.page_bytes,media_max_bytes=MAX_DOWNLOAD_BYTES)
        try:
            try:
                async with asyncio.timeout(40):
                    return await self._download(client,fact['provenance']['tenant_id'],preparation_ref,
                        inbox['payload']['voice']['media_id'])
            except TimeoutError as error:
                raise LocalPreparationFailed('SOURCE_MEDIA_DOWNLOAD_TIMEOUT') from error
        finally:
            await drain_owned(asyncio.create_task(client.close()))

    @staticmethod
    def _directory(tenant_id):
        if not tenant_id:
            raise SourceUnavailable('SOURCE_MEDIA_TENANT_REQUIRED')
        try:
            directory=get_conversation_dir(tenant_id)
            anchor=configured_storage_root()
            descriptor=os.open(anchor,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            try:
                for part in directory.relative_to(anchor).parts:
                    next_descriptor=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=descriptor)
                    os.close(descriptor);descriptor=next_descriptor
                return directory,descriptor
            except BaseException:
                os.close(descriptor)
                raise
        except (OSError,ValueError) as error:
            raise SourceUnavailable('SOURCE_MEDIA_STORAGE_UNAVAILABLE') from error

    @classmethod
    def store(cls,tenant_id,preparation_ref,data,audio_format,sample_rate):
        if not data or len(data)>MAX_AUDIO_BYTES:
            raise LocalPreparationFailed('SOURCE_MEDIA_TOO_LARGE')
        if not preparation_ref.startswith('voice_') or len(preparation_ref)!=70 or any(
                c not in '0123456789abcdef' for c in preparation_ref[6:]):
            raise SourceUnavailable('SOURCE_MEDIA_REF_INVALID')
        if audio_format not in {'wav','mp3','amr','amr-wb','opus','pcm','aac'}:
            raise LocalPreparationFailed('SOURCE_MEDIA_FORMAT_UNSUPPORTED')
        directory,descriptor=cls._directory(tenant_id)
        name=preparation_ref+'.'+audio_format
        temporary='.'+preparation_ref+'.'+uuid.uuid4().hex
        digest=hashlib.sha256(data).hexdigest()
        try:
            fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=descriptor)
            with os.fdopen(fd,'wb') as handle:
                handle.write(data);handle.flush();os.fsync(handle.fileno())
            try:
                # Exclusive atomic publish: another writer cannot replace a
                # different known artifact or turn a symlink into a read target.
                os.link(temporary,name,src_dir_fd=descriptor,dst_dir_fd=descriptor,follow_symlinks=False)
                os.fsync(descriptor)
            except FileExistsError:
                fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=descriptor)
                with os.fdopen(fd,'rb') as handle: previous=handle.read(MAX_AUDIO_BYTES+1)
                if hashlib.sha256(previous).hexdigest()!=digest:
                    raise SourceUnavailable('SOURCE_MEDIA_HASH_CONFLICT')
            return {'file_id':name,'file_name':name,'file_size':len(data),
                    'local_path':str(directory/name),'audio_format':audio_format,
                    'audio_sample_rate':sample_rate,'sha256':digest,'mime_type':'audio/'+audio_format}
        except OSError as error:
            raise SourceUnavailable('SOURCE_MEDIA_STORAGE_UNAVAILABLE') from error
        finally:
            try: os.unlink(temporary,dir_fd=descriptor)
            except FileNotFoundError: pass
            os.close(descriptor)

    @classmethod
    def read(cls,tenant_id,artifact):
        directory,descriptor=cls._directory(tenant_id)
        try:
            name=artifact['file_id']
            if Path(name).name!=name or artifact['local_path']!=str(directory/name):
                raise SourceUnavailable('SOURCE_MEDIA_SCOPE_INVALID')
            fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=descriptor)
            with os.fdopen(fd,'rb') as handle: data=handle.read(MAX_AUDIO_BYTES+1)
            if (len(data)!=artifact['file_size'] or len(data)>MAX_AUDIO_BYTES
                    or hashlib.sha256(data).hexdigest()!=artifact['sha256']):
                raise SourceUnavailable('SOURCE_MEDIA_HASH_CONFLICT')
            return data
        except OSError as error:
            raise SourceUnavailable('SOURCE_MEDIA_STORAGE_UNAVAILABLE') from error
        finally: os.close(descriptor)

    async def _download(self,client,tenant_id,preparation_ref,media_id):
        import httpx
        try:
            data,content_type=await client.download_media(media_id)
        except (httpx.HTTPError,RuntimeError,TimeoutError) as error:
            raise LocalPreparationFailed('SOURCE_MEDIA_DOWNLOAD_FAILED') from error
        known_magic=(data.startswith((b'#!AMR',b'\x02#!SILK',b'ID3',b'OggS',b'\x1a\x45\xdf\xa3'))
            or data[:2] in {b'\xff\xfb',b'\xff\xf3',b'\xff\xf2',b'\xff\xe3',b'\xff\xe2'}
            or (data[:4]==b'RIFF' and data[8:12]==b'WAVE') or data[4:8]==b'ftyp')
        mime=content_type.split(';',1)[0].strip().lower()
        declared=mime in {'audio/amr','audio/amr-wb','audio/wav','audio/x-wav',
            'audio/mpeg','audio/mp3','audio/ogg','audio/opus','audio/aac','audio/mp4',
            'audio/pcm','audio/x-pcm','audio/silk'}
        if not known_magic and not declared:
            raise LocalPreparationFailed('KF_MEDIA_AUDIO_FORMAT_UNPROVEN')
        audio_format,sample_rate=detect_audio_format(data,content_type)
        if audio_format.startswith('silk_'):
            data=await self._decode(tenant_id,preparation_ref,data)
            audio_format,sample_rate='wav',16000
        elif audio_format=='pcm':
            # PCM only follows a positive content-type proof from the original
            # media endpoint. Unknown magic's mp3 fallback is never PCM proof.
            data=await source_offload(wrap_pcm_as_wav,data,sample_rate=sample_rate)
            audio_format='wav'
        return await source_offload(self.store,tenant_id,preparation_ref,data,audio_format,sample_rate)

    async def _decode(self,tenant_id,preparation_ref,data):
        directory,descriptor=self._directory(tenant_id)
        suffix=uuid.uuid4().hex
        source='.'+preparation_ref+'.'+suffix+'.silk'
        target='.'+preparation_ref+'.'+suffix+'.wav'
        process=None
        try:
            def write():
                fd=os.open(source,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=descriptor)
                with os.fdopen(fd,'wb') as handle: handle.write(data)
            await source_offload(write)
            spawn=asyncio.create_task(asyncio.create_subprocess_exec(sys.executable,'-c',_CODEC,
                str(descriptor),source,target,pass_fds=(descriptor,),cwd=str(Path(__file__).resolve().parents[3]),
                stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL))
            cancelled=False
            while not spawn.done():
                try: await asyncio.shield(spawn)
                except asyncio.CancelledError: cancelled=True
            process=spawn.result()
            try:
                if cancelled: raise asyncio.CancelledError
                await asyncio.wait_for(process.wait(),10)
            except BaseException as error:
                if process.returncode is None: process.kill()
                task=asyncio.create_task(process.wait())
                while not task.done():
                    try: await asyncio.shield(task)
                    except asyncio.CancelledError: pass
                if isinstance(error,TimeoutError):
                    raise LocalPreparationFailed('SOURCE_MEDIA_CODEC_TIMEOUT') from error
                raise
            if process.returncode!=0:
                raise LocalPreparationFailed('SOURCE_MEDIA_CODEC_FAILED')
            def read():
                fd=os.open(target,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=descriptor)
                with os.fdopen(fd,'rb') as handle: return handle.read(MAX_AUDIO_BYTES+1)
            result=await source_offload(read)
            if len(result)>MAX_AUDIO_BYTES: raise LocalPreparationFailed('SOURCE_MEDIA_TOO_LARGE')
            return result
        except OSError as error:
            raise SourceUnavailable('SOURCE_MEDIA_STORAGE_UNAVAILABLE') from error
        finally:
            for name in (source,target):
                try: os.unlink(name,dir_fd=descriptor)
                except FileNotFoundError: pass
            os.close(descriptor)
