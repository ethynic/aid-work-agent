"""Trusted human voice orchestration; one Speech POST and actual owned drain."""

import asyncio
import base64

from src.services.agent_runner.source_receipts import LocalPreparationFailed
from src.services.agent_runner.usage_pricing import freeze_price
from src.tools.asr.call_observer import asr_call_observer, AsrCallUnknown
from src.tools.asr.speech_to_text_tool import SpeechToTextTool
from .api_client import WeComKfApiClient
from .ingress_auth import KfIngressError
from .ingress_worker import _thread, _drain
from .lifecycle_repository import route_scope
from .voice_media import VoiceMedia
from .context_voice_repository import ContextVoiceRepository, artifact_ref, operation_ref


class ContextVoice:
    def __init__(self,config,repository=None,client_factory=WeComKfApiClient,
                 *,history_observer=None,stopping=None):
        self.config=config
        self.repository=repository or ContextVoiceRepository()
        self.client_factory=client_factory
        self.history_observer=history_observer
        self.parent_stopping=stopping
        self.stopping=False
        self.tasks=set()

    def _stopped(self):
        return self.stopping or bool(self.parent_stopping and self.parent_stopping())

    async def process(self,locator):
        if self._stopped(): return None
        task=asyncio.create_task(self._process(locator))
        self.tasks.add(task)
        try: return await _drain(task)
        finally: self.tasks.discard(task)

    async def _process(self,locator):
        current,inbox,route,_,prep=await _thread(self.repository.read,locator)
        expected={'scope':route_scope(route),'payload_digest':inbox['payload_digest'],
                  'config_version':current.proof.config_version}
        if prep is None:
            recognition=inbox['payload']['voice'].get('recognition')
            if isinstance(recognition,str) and recognition.strip():
                prep=await _thread(self.repository.remember,locator,expected,recognition=recognition)
            else:
                client=self.client_factory(current.proof.corp_id,current.secret)
                client.enable_ingress_mode(self.config.page_bytes)
                artifact=None
                try:
                    if self._stopped(): return None
                    artifact=await VoiceMedia.download_trusted(client,route['tenant_id'],
                        artifact_ref(operation_ref(inbox,route)),inbox['payload']['voice']['media_id'])
                except LocalPreparationFailed:
                    # Deterministic, drained preflight failures have zero POST.
                    pass
                finally:
                    await _drain(asyncio.create_task(client.close()))
                if self._stopped(): return None
                prep=await _thread(self.repository.remember,locator,expected,
                                   artifact=artifact,failed=artifact is None)
        if prep['phase']=='media_ready' and not self._stopped():
            # Native admission flag only gates new classification; accepted
            # fixed context work can make its first ASR, including at balance0.
            data=await _thread(VoiceMedia.read,route['tenant_id'],prep['artifact'])
            price=await _thread(freeze_price,'asr','aliyun-nls-asr','main')
            owner=self
            class Observer:
                operation=None
                async def before_post(self,**metadata):
                    if owner._stopped(): raise KfIngressError('KF_CONTEXT_VOICE_STOPPED')
                    if (metadata['audio_size']!=len(data)
                            or metadata['audio_format']!=prep['artifact']['audio_format']
                            or metadata['sample_rate']!=prep['artifact']['audio_sample_rate']):
                        raise KfIngressError('KF_CONTEXT_VOICE_MEDIA_CHANGED')
                    self.operation=await _thread(owner.repository.start,locator,expected,prep['artifact'],price)
                    if owner._stopped():
                        # This callback has not returned to Speech; zero POST is
                        # known even when shutdown happened during the SQL wait.
                        await _thread(owner.repository.unwritten,self.operation)
                        raise KfIngressError('KF_CONTEXT_VOICE_STOPPED')
                async def known(self,**result):
                    await _thread(owner.repository.known,self.operation,**result)
                async def unknown(self,code):
                    await _thread(owner.repository.uncertain,self.operation,code)
            observer=Observer()
            try:
                with asr_call_observer(observer):
                    await SpeechToTextTool().execute(audio_content=base64.b64encode(data).decode('ascii'),
                        format=prep['artifact']['audio_format'],sample_rate=prep['artifact']['audio_sample_rate'])
                if observer.operation is None and not self._stopped():
                    await _thread(self.repository.preflight_failed,locator)
            except AsrCallUnknown:
                # Original observer already retained the unknown operation.
                # It is displayed, never issued as another physical POST.
                pass
            except KfIngressError as error:
                if error.code not in {'KF_CONTEXT_ASR_NOT_DISPATCHABLE','KF_CONTEXT_VOICE_STOPPED'}:
                    raise
        if self._stopped(): return None
        if self.history_observer:
            await self.history_observer(locator)
        return await _thread(self.repository.project,locator)

    async def close(self):
        self.stopping=True
        for task in tuple(self.tasks): await _drain(task)
