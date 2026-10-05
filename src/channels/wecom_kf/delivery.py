"""Captured-route presentation using the original KF renderer and wire client."""

import asyncio
import uuid
from src.db.database import get_db_connection
from src.models.message import UnifiedResponse,DownloadableFileInfo
from src.services.agent_runner.source_receipts import SourceLocator,SourceUnavailable
from .api_client import NativeWriteDecision,NativeWriteStopped,NativeWritePreparationFailed,NativeWriteUnknown
from .adapter import WeComKfAdapter
from .budget import WeComKfReplyBudget
from .delivery_repository import DeliveryRepository
from .ingress_repository import KfIngressRepository
from .ingress_worker import _thread,_drain
from .lifecycle_repository import read_receipt_in_tx


class DeliveryObserver:
    def __init__(self,repository,owner,*,stopping=lambda:False):
        self.repository,self.owner=repository,owner
        self.stopping=stopping
        self.ordinal=0
        self.purpose='body'
        self.fallback_of=None
        self.identity=None
        self.representation_digest=None
        self.counts={}
        self.unknown_operations=[]

    async def before_post(self,endpoint,description):
        if self.stopping(): raise NativeWriteStopped('KF_NATIVE_STOPPED')
        purpose='upload' if endpoint=='/cgi-bin/media/upload' else self.purpose
        key=(self.identity,endpoint)
        index=self.counts.get(key,0);self.counts[key]=index+1
        description={**description,'representation_id':str(self.identity)+':'+endpoint+':'+str(index),
                     'representation_digest':self.representation_digest}
        operation=await _thread(self.repository.start,self.owner,self.ordinal,endpoint,description,
            purpose=purpose,fallback_of=self.fallback_of)
        self.ordinal+=1
        if operation['phase']=='suppressed': raise NativeWriteStopped('KF_DELIVERY_REPLY_BUDGET_EXHAUSTED')
        if operation['phase'] in {'ack','reject'}:
            return NativeWriteDecision(operation,replay_result=operation['result'],dispatch=False)
        return NativeWriteDecision(operation)

    async def assert_dispatch(self,operation):
        if self.stopping(): raise NativeWriteStopped('KF_NATIVE_STOPPED')
        await _thread(self.repository.assert_dispatch,self.owner,operation)
        if self.stopping(): raise NativeWriteStopped('KF_NATIVE_STOPPED')

    async def known(self,operation,result):
        await _thread(self.repository.observe,operation,result=result)

    async def unknown(self,operation,code):
        await _thread(self.repository.observe,operation,unknown_code=code)
        self.unknown_operations.append(operation)

    async def unwritten(self,operation,code):
        await _thread(self.repository.observe,operation,unwritten_code=code)

    async def suppressed(self,kind,description):
        await _thread(self.repository.suppress,self.owner,self.ordinal,kind,description)
        self.ordinal+=1


class KfDeliveryWorker:
    def __init__(self,config,source_client,connection_factory=get_db_connection,*,adapter_factory=WeComKfAdapter):
        self.config,self.source_client=config,source_client
        self.connection_factory=connection_factory
        self.repository=DeliveryRepository(connection_factory)
        self.adapter_factory=adapter_factory
        self.owner_id='kf-delivery-'+uuid.uuid4().hex
        self.after=0
        self.stopping=False
        self.tasks=set()

    def _candidate(self):
        with self.connection_factory() as conn:
            cursor=KfIngressRepository._cursor(conn)
            cursor.execute('''SELECT i.account_id,i.namespace,i.message_id,i.receipt_order FROM wecom_kf_inbox i
                WHERE i.accepted_input_ref IS NOT NULL AND i.receipt_order>%s
                AND NOT EXISTS(SELECT 1 FROM wecom_kf_deliveries d WHERE d.input_ref=i.accepted_input_ref AND d.phase='closed')
                AND NOT EXISTS(SELECT 1 FROM wecom_kf_input_batch_members m WHERE m.account_id=i.account_id
                    AND m.namespace=i.namespace AND m.message_id=i.message_id AND m.ordinal>0)
                ORDER BY i.receipt_order LIMIT 1''',(self.after,))
            return cursor.fetchone()

    def _adapter_values(self,locator):
        with self.connection_factory() as conn:
            cursor=KfIngressRepository._cursor(conn)
            current,inbox,route=read_receipt_in_tx(cursor,locator)
            return current,inbox,route

    async def observe(self,locator):
        task=asyncio.current_task();self.tasks.add(task)
        adapter=None
        owner=observer=value=None
        try:
            # The service alone reads execution facts. No local Runner table.
            value=await self.source_client.read(locator,presentation=True)
            view=value['runner']
            if view['status'] not in {'completed','failed','cancelled','waiting','paused','interrupted'}:
                return value
            owner,row=await _thread(self.repository.open,locator,value,self.owner_id)
            if owner is None:
                if view['status'] in {'completed','failed','cancelled'}:
                    return await self.source_client.finish_delivery(locator,row['delivery_id'])
                return value
            if row.get('unknown_only'):
                # Old HTTP operations have actually drained. This terminal
                # presentation gets no new customer POST, including old events.
                await _thread(self.repository.seal_unknown,owner)
                return await self.source_client.finish_delivery(locator,row['delivery_id'])
            current,inbox,route=await _thread(self._adapter_values,locator)
            adapter=self.adapter_factory(current.proof.corp_id,current.secret)
            await adapter.set_tenant_id(route['tenant_id'])
            observer=DeliveryObserver(self.repository,owner,stopping=lambda:self.stopping)
            adapter.enable_native_delivery(observer,open_kfid=route['open_kfid'],actor_id=route['actor_id'])
            budget=WeComKfReplyBudget(total=5,native_remaining=await _thread(self.repository.budget_remaining,owner))
            presentation=row['presentation']
            if await _thread(self.repository.transferred,owner):
                await observer.suppressed('transferred',{'presentation_digest':row['presentation_digest']})
                await _thread(self.repository.seal,owner,observer.ordinal)
                if view['status'] in {'completed','failed','cancelled'}:
                    return await self.source_client.finish_delivery(locator,row['delivery_id'])
                return value
            # Keep the original bounded verbose-before-final budget priority.
            for item in presentation.get('verboseMessages') or []:
                if self.stopping: return value
                observer.purpose='verbose'
                observer.identity='verbose:'+str(item.get('eventId') or '')
                if not item.get('eventId'):raise SourceUnavailable('KF_DELIVERY_EVENT_ID_INVALID')
                observer.representation_digest=__import__('hashlib').sha256(str(item.get('data') or '').encode()).hexdigest()
                message=UnifiedResponse(message_id=str(item.get('eventId') or row['delivery_id']),reply_to=route['actor_id'],
                    content={'text':str(item.get('data') or ''),'_kf_reply_budget':budget})
                try:await adapter.send_status_message(message,reserve_for_final=1)
                except NativeWriteStopped as error:
                    if error.code!='KF_DELIVERY_REPLY_BUDGET_EXHAUSTED':raise
            text=presentation.get('output') or ''
            waiting=self.repository.clarification_wait(presentation.get('waiting')) or {}
            if waiting.get('question'):
                text=waiting['question'];observer.purpose='question'
            else: observer.purpose='body'
            message=UnifiedResponse(message_id=row['delivery_id'],reply_to=route['actor_id'],
                content={'text':text,'_kf_reply_budget':budget})
            observer.representation_digest=__import__('hashlib').sha256(text.encode()).hexdigest()
            if observer.purpose=='question':
                if not waiting.get('wait_id'):raise SourceUnavailable('KF_DELIVERY_WAIT_ID_INVALID')
                observer.identity='question:'+str(waiting.get('target_execution_id'))+':'+waiting['wait_id']
            else:
                observer.identity=('final:' if view['status'] in {'completed','failed','cancelled'} else 'partial:'+str(view['view_revision'])+':')+view['runner_id']
            try:
                if text:await adapter.send_message(message)
                observer.purpose='asset'
                for asset in presentation.get('images') or []:
                    observer.representation_digest=__import__('hashlib').sha256(__import__('json').dumps(asset,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
                    observer.identity='image:'+observer.representation_digest
                    await adapter.send_message(UnifiedResponse(message_id=observer.identity,reply_to=route['actor_id'],
                        content={'text':'','images':[asset],'_kf_reply_budget':budget}))
                for asset in presentation.get('downloadableFiles') or []:
                    observer.representation_digest=__import__('hashlib').sha256(__import__('json').dumps(asset,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
                    observer.identity='file:'+str(asset.get('file_id') or '')+':'+observer.representation_digest
                    await adapter.send_message(UnifiedResponse(message_id=observer.identity,reply_to=route['actor_id'],
                        content={'text':'','_kf_reply_budget':budget},downloadable_files=[DownloadableFileInfo(**asset)]))
            except NativeWriteStopped as error:
                if error.code!='KF_DELIVERY_REPLY_BUDGET_EXHAUSTED': raise
                # A complete captured remainder is explicitly suppressed, never
                # mistaken for an ACK or dispatched on the next poll.
                await observer.suppressed('remaining_presentation',{'presentation_digest':row['presentation_digest'],'reason':'reply_budget'})
            except NativeWritePreparationFailed as error:
                await observer.suppressed('known_local_preparation',{'presentation_digest':row['presentation_digest'],'reason':error.code})
            if self.stopping: return value
            if observer.ordinal==0:
                await observer.suppressed('empty_presentation',{'presentation_digest':row['presentation_digest']})
            await _thread(self.repository.seal,owner,observer.ordinal)
            if view['status'] in {'completed','failed','cancelled'}:
                return await self.source_client.finish_delivery(locator,row['delivery_id'])
            return value
        except NativeWriteUnknown:
            if adapter is None or owner is None or observer is None:
                raise
            # SDK observation alone is insufficient: close and strongly wait
            # for the actual owned HTTP work before persisting this fact.
            await _drain(asyncio.create_task(adapter.close()))
            adapter=None
            await _thread(self.repository.transport_drained,owner,observer.unknown_operations)
            if value['runner']['status'] in {'completed','failed','cancelled'}:
                await _thread(self.repository.seal_unknown,owner)
                return await self.source_client.finish_delivery(locator,owner.delivery_id)
            return value
        finally:
            try:
                if adapter is not None: await _drain(asyncio.create_task(adapter.close()))
            finally: self.tasks.discard(task)

    async def run_once(self):
        if self.stopping:return None
        row=await _thread(self._candidate)
        if row is None:self.after=0;return None
        self.after=row['receipt_order']
        return await self.observe(SourceLocator('wecom_kf',row['account_id'],row['namespace'],row['message_id']))

    def request_stop(self): self.stopping=True

    async def close(self):
        self.request_stop()
        for task in tuple(self.tasks): await _drain(task)
