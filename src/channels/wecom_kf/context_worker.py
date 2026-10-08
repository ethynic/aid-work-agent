"""Bounded KF context consumer: SDK outside SQL, history inside original TX."""

import asyncio
import httpx
from datetime import datetime, timezone

from .api_client import WeComKfApiClient
from .context_repository import ContextRepository
from .ingress_auth import KfIngressError
from .ingress_worker import _thread, _drain
from .lifecycle_repository import StateObservation, LifecycleRepository, route_scope
from .context_voice import ContextVoice
from .context_voice_repository import ContextVoiceRepository
from src.services.agent_runner.source_receipts import SourceLocator
from src.services.agent_runner.contracts import RunnerError


class ContextWorker:
    def __init__(self,config,repository=None,client_factory=WeComKfApiClient,source_client=None):
        self.config=config
        self.repository=repository or ContextRepository()
        self.client_factory=client_factory
        self.source_client=source_client
        self.after=0
        self.stopping=False
        self.tasks=set()
        self.voice=ContextVoice(config,ContextVoiceRepository(self.repository.connection_factory),
            client_factory,history_observer=self._source_observations,stopping=lambda:self.stopping)

    def _classification(self,locator):
        current,inbox,route=self.repository.read(locator)
        with self.repository.connection_factory() as conn:
            from .ingress_repository import KfIngressRepository
            row=LifecycleRepository.classification_in_tx(KfIngressRepository._cursor(conn),inbox,route) if route else None
        return current,inbox,route,row

    async def _source_observations(self,locator):
        candidates=await _thread(self.repository.pending_sources,locator)
        result=[]
        for candidate in candidates[:4]:
            value=None
            if self.source_client is not None and candidate['accepted_input_ref']:
                try:
                    value=await self.source_client.read(SourceLocator('wecom_kf',candidate['account_id'],candidate['namespace'],candidate['message_id']))
                except RunnerError:
                    # Service observation unavailable: do not infer completion.
                    pass
            result.append({key:candidate[key] for key in ('account_id','namespace','message_id','payload_digest','accepted_input_ref')})
            result[-1]['observation']=value
            if value is not None:
                await _thread(self.repository.remember_source,locator,result[-1])
        return result

    async def _observe_state(self,current,inbox,route,*,optional=False):
        # Only original platform IO and reply validation live in this method;
        # optional employee SDK failures never wrap SQL or history operations.
        client=self.client_factory(current.proof.corp_id,current.secret)
        client.enable_ingress_mode(self.config.page_bytes)
        try:
            try:
                async with asyncio.timeout(35):
                    reply=await client.get_service_state(current.proof.open_kfid,inbox['actor_id'])
                    observed_at=datetime.now(timezone.utc)
                if (not isinstance(reply,dict) or type(reply.get('errcode')) is not int or reply['errcode']!=0
                        or type(reply.get('service_state')) is not int or reply['service_state'] not in range(5)):
                    raise KfIngressError('KF_CONTEXT_STATE_UNOBSERVED')
            except (httpx.HTTPError,TimeoutError,RuntimeError,KfIngressError) as error:
                if not optional or getattr(error,'authoritative_storage_failure',False): raise
                if isinstance(error,KfIngressError) and error.code!='KF_CONTEXT_STATE_UNOBSERVED': raise
                return None
            return StateObservation(reply['service_state'],observed_at,current.proof.config_version,
                                    inbox['payload_digest'],route_scope(route))
        finally:
            # Actual transport drain is outside the ordinary SDK-error catch.
            await _drain(asyncio.create_task(client.close()))

    async def _commit_context(self,locator,inbox):
        if inbox['message_type'] in {'text','image','video','file'}:
            await self._source_observations(locator)
        media=None
        if inbox['message_type'] in {'image','video','file'}:
            from .completion_business import prepare_context_media
            media=await prepare_context_media(self.repository.connection_factory,self.client_factory,
                self.config.page_bytes,locator)
        return await _thread(self.repository.commit,locator,media_projection=media)

    async def run_once(self):
        if self.stopping: return None
        task=asyncio.current_task();self.tasks.add(task)
        try:
            row=await _thread(self.repository.lifecycle.read_candidate,self.after)
            if row is None:
                self.after=0;return None
            self.after=row['receipt_order']
            locator=SourceLocator('wecom_kf',row['account_id'],row['namespace'],row['message_id'])
            current,inbox,route,classification=await _thread(self._classification,locator)
            if inbox.get('accepted_input_ref'): return {'disposition':'accepted_source','history_id':None}
            if classification is None and not self.config.enabled: return None
            if inbox['message_type']=='event':
                return await _thread(self.repository.commit,locator)
            if classification is not None:
                if inbox['message_type']=='voice': return await self.voice.process(locator)
                return await self._commit_context(locator,inbox)
            employee=inbox['origin']==5
            if employee:
                classified=await _thread(self.repository.classify_employee,locator,
                    inbox['payload_digest'],route_scope(route))
                if classified.get('disposition')=='accepted_source': return classified
                # Receipt proof already fixed employee. A real state is optional
                # for first-round recap eligibility; failure leaves NULL facts.
                observation=None
                if not self.stopping and self.config.enabled:
                    observation=await self._observe_state(current,inbox,route,optional=True)
                if observation is not None and not self.stopping and self.config.enabled:
                    try:
                        classified=await _thread(self.repository.classify,locator,observation)
                    except KfIngressError as error:
                        if error.code!='KF_CONTEXT_OBSERVATION_EXPIRED': raise
                    else:
                        if classified.get('disposition')=='accepted_source': return classified
            else:
                observation=await self._observe_state(current,inbox,route)
                if self.stopping or not self.config.enabled: return None
                classified=await _thread(self.repository.classify,locator,observation)
                if classified.get('disposition')=='accepted_source': return classified
            if self.stopping: return None
            if inbox['message_type']=='voice': return await self.voice.process(locator)
            return await self._commit_context(locator,inbox)
        finally:
            self.tasks.discard(task)

    async def close(self):
        self.stopping=True
        for task in tuple(self.tasks-{asyncio.current_task()}):
            await _drain(task)
        await self.voice.close()
