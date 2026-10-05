"""Original KF business functions behind durable receipt/wire facts.

This owns neither Agent execution nor a generic workflow. Execution route and
principal stay immutable; the registered customer is business context only.
"""

import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
import hashlib
import json
from datetime import datetime,timezone,timedelta
from src.db.database import get_db_connection
from src.services.agent_runner.source_receipts import SourceLocator,SourceUnavailable
from .ingress_auth import encoded,config_in_tx,KfIngressError
from .ingress_repository import KfIngressRepository
from .ingress_worker import _thread,_drain
from .lifecycle_repository import read_receipt_in_tx,route_scope,LifecycleRepository,StateObservation
from .delivery_repository import digest,DeliveryRepository
from .api_client import NativeWriteDecision,NativeWriteStopped
from .adapter import WeComKfAdapter


class BusinessRepository:
    def __init__(self,connection_factory=get_db_connection): self.connection_factory=connection_factory

    def read(self,locator):
        with self.connection_factory() as conn:
            cursor=KfIngressRepository._cursor(conn)
            current,inbox,route=read_receipt_in_tx(cursor,locator)
            _,cfg=config_in_tx(cursor,inbox['tenant_id'],inbox['config_id'])
            accounts=[item for item in cfg['kf_account'] if item.get('open_kfid')==inbox['open_kfid']]
            if len(accounts)!=1: raise SourceUnavailable('KF_BUSINESS_ACCOUNT_CHANGED')
            return current,inbox,route,accounts[0]

    def fact(self,locator,kind):
        with self.connection_factory() as conn:
            cursor=KfIngressRepository._cursor(conn)
            _,inbox,route=read_receipt_in_tx(cursor,locator)
            cursor.execute('SELECT * FROM wecom_kf_business_facts WHERE business_ref=%s',(self.ref(locator,kind),))
            row=cursor.fetchone()
            if row and (row['scope']!=route_scope(route) or row['payload_digest']!=inbox['payload_digest']):
                raise SourceUnavailable('KF_BUSINESS_BINDING_CHANGED')
            return dict(row) if row else None

    def known_exit(self,locator):
        with self.connection_factory() as conn:
            cursor=KfIngressRepository._cursor(conn)
            _,inbox,route=read_receipt_in_tx(cursor,locator)
            cursor.execute("""SELECT proof,result FROM wecom_kf_wire_operations WHERE locator=%s::jsonb
                AND scope=%s::jsonb AND payload_digest=%s AND purpose='human_exit'
                AND endpoint='/cgi-bin/kf/service_state/trans' AND phase='ack'
                AND response_origin='platform' AND errcode=0""",
                (encoded(locator.value()),encoded(route_scope(route)),inbox['payload_digest']))
            rows=cursor.fetchall()
            return len(rows)==1 and rows[0]['proof'].get('service_state')==4 and rows[0]['result'].get('response_origin')=='platform'

    def block_account(self,locator,route,config_version,kind,reply,user_content):
        from src.db.channel_message_repository import insert_message,touch_session
        with self.connection_factory() as conn:
            cursor=KfIngressRepository._cursor(conn)
            current,inbox,fresh=read_receipt_in_tx(cursor,locator,lock=True)
            if route_scope(fresh)!=route_scope(route) or current.proof.config_version!=config_version:
                raise SourceUnavailable('KF_BUSINESS_BINDING_CHANGED')
            if inbox.get('accepted_input_ref'):return None
            ref=self.ref(locator,'account_blocked')
            value={'kind':kind,'reply':reply,'user_content':user_content}
            cursor.execute('''INSERT INTO wecom_kf_business_facts(business_ref,tenant_id,account_id,namespace,message_id,
                route_id,scope,payload_digest,business_kind,phase,value,observed_at)
                VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb,%s,'account_blocked','known',%s::jsonb,clock_timestamp())
                ON CONFLICT(business_ref) DO NOTHING''',
                (ref,inbox['tenant_id'],locator.account_id,locator.namespace,locator.message_id,fresh['route_id'],
                 encoded(route_scope(fresh)),inbox['payload_digest'],encoded(value)))
            cursor.execute('SELECT * FROM wecom_kf_business_facts WHERE business_ref=%s FOR UPDATE',(ref,))
            fact=cursor.fetchone()
            if (not fact or fact['scope']!=route_scope(fresh) or fact['payload_digest']!=inbox['payload_digest']
                    or fact['phase']!='known' or fact['value']!=value):
                raise SourceUnavailable('KF_BUSINESS_BINDING_CHANGED')
            insert_message(cursor,message_id=ref+':user',session_id=fresh['session_id'],tenant_id=inbox['tenant_id'],
                role='user',content=user_content,metadata={**route_scope(fresh),'kind':'blocked_'+kind+'_user',
                    'msgid':inbox['message_id']},idempotent=True)
            touch_session(cursor,fresh['session_id'])
            conn.commit();return dict(fact)

    def block_reply_history(self,locator,fact):
        from src.db.channel_message_repository import insert_message,touch_session
        with self.connection_factory() as conn:
            cursor=KfIngressRepository._cursor(conn)
            _,inbox,route=read_receipt_in_tx(cursor,locator,lock=True)
            if fact['scope']!=route_scope(route) or fact['payload_digest']!=inbox['payload_digest']:
                raise SourceUnavailable('KF_BUSINESS_BINDING_CHANGED')
            cursor.execute('''SELECT 1 FROM wecom_kf_wire_operations WHERE locator=%s::jsonb AND scope=%s::jsonb
                AND payload_digest=%s AND purpose='account_blocked_reply' AND phase='ack'
                AND response_origin='platform' AND errcode=0 LIMIT 1''',
                (encoded(locator.value()),encoded(route_scope(route)),inbox['payload_digest']))
            if cursor.fetchone():
                insert_message(cursor,message_id=fact['business_ref']+':assistant',session_id=route['session_id'],
                    tenant_id=inbox['tenant_id'],role='assistant',content=fact['value']['reply'],
                    metadata={**route_scope(route),'kind':'blocked_'+fact['value']['kind']+'_reply',
                        'open_kfid':route['open_kfid']},idempotent=True)
                touch_session(cursor,route['session_id'])
            conn.commit()

    def ai_permitted(self,locator):
        with self.connection_factory() as conn:
            cursor=KfIngressRepository._cursor(conn)
            _,inbox,route=read_receipt_in_tx(cursor,locator)
            fixed=LifecycleRepository.classification_in_tx(cursor,inbox,route)
            return fixed is None or fixed['classification']=='ai' or LifecycleRepository.transition_permit_in_tx(cursor,inbox,route)

    def observe_zero(self,locator,observation):
        with self.connection_factory() as conn:
            cursor=KfIngressRepository._cursor(conn)
            current,inbox,route=read_receipt_in_tx(cursor,locator,lock=True)
            LifecycleRepository.assert_observation_in_tx(cursor,current,observation)
            row=LifecycleRepository.record_observation_in_tx(cursor,inbox,route,observation)
            allowed=bool(row and row['classification']=='unknown' and row['observed_state']==0
                         and not inbox.get('accepted_input_ref'))
            LifecycleRepository.assert_observation_in_tx(cursor,current,observation)
            conn.commit()
            return allowed

    @staticmethod
    def _transition_start(cursor,current,inbox,route,observation):
        if observation is None or observation.state!=0 or inbox.get('accepted_input_ref'):
            raise SourceUnavailable('KF_BUSINESS_TRANSITION_FORBIDDEN')
        LifecycleRepository.assert_observation_in_tx(cursor,current,observation)
        if observation.receipt_digest!=inbox['payload_digest'] or observation.scope!=route_scope(route):
            raise SourceUnavailable('KF_BUSINESS_BINDING_CHANGED')
        row=LifecycleRepository.classification_in_tx(cursor,inbox,route)
        if not row or row['classification']!='unknown' or row['observed_state']!=0:
            raise SourceUnavailable('KF_BUSINESS_TRANSITION_FORBIDDEN')
        cursor.execute("""SELECT 1 FROM wecom_kf_context_consumptions WHERE account_id=%s
            AND namespace=%s AND message_id=%s UNION ALL SELECT 1 FROM wecom_kf_business_facts
            WHERE account_id=%s AND namespace=%s AND message_id=%s UNION ALL
            SELECT 1 FROM wecom_kf_context_voice_preparations WHERE account_id=%s AND namespace=%s AND message_id=%s UNION ALL
            SELECT 1 FROM wecom_kf_wire_operations WHERE locator=%s::jsonb
              AND purpose<>'transition_to_ai' LIMIT 1""",
            (inbox['account_id'],inbox['namespace'],inbox['message_id'],
             inbox['account_id'],inbox['namespace'],inbox['message_id'],
             inbox['account_id'],inbox['namespace'],inbox['message_id'],
             encoded({'source':'wecom_kf','account_id':inbox['account_id'],
                      'namespace':inbox['namespace'],'message_id':inbox['message_id']})))
        if cursor.fetchone():raise SourceUnavailable('KF_BUSINESS_TRANSITION_FORBIDDEN')

    @staticmethod
    def _exit_start(cursor,current,inbox,route,observation):
        if observation is None or observation.state!=3 or inbox.get('accepted_input_ref'):
            raise SourceUnavailable('KF_BUSINESS_EXIT_FORBIDDEN')
        LifecycleRepository.assert_observation_in_tx(cursor,current,observation)
        fixed=LifecycleRepository.classification_in_tx(cursor,inbox,route)
        if (not fixed or fixed['classification']!='human' or observation.scope!=route_scope(route)
                or observation.receipt_digest!=inbox['payload_digest']):
            raise SourceUnavailable('KF_BUSINESS_EXIT_FORBIDDEN')

    @staticmethod
    def ref(locator,kind): return 'kf_business_'+digest([locator.value(),kind,1])

    def start(self,locator,kind,*,expected=None):
        with self.connection_factory() as conn:
            cursor=KfIngressRepository._cursor(conn)
            current,inbox,route=read_receipt_in_tx(cursor,locator,lock=True)
            if expected is not None and expected!={
                    'scope':route_scope(route),'config_version':current.proof.config_version,
                    'payload_digest':inbox['payload_digest']}:
                raise SourceUnavailable('KF_BUSINESS_BINDING_CHANGED')
            ref=self.ref(locator,kind)
            cursor.execute('SELECT * FROM wecom_kf_business_facts WHERE business_ref=%s FOR UPDATE',(ref,))
            row=cursor.fetchone()
            if row:
                if row['scope']!=route_scope(route) or row['payload_digest']!=inbox['payload_digest']:
                    raise SourceUnavailable('KF_BUSINESS_BINDING_CHANGED')
                conn.commit();return dict(row),False
            cursor.execute('''INSERT INTO wecom_kf_business_facts(business_ref,tenant_id,account_id,namespace,message_id,
                route_id,scope,payload_digest,business_kind,phase,value)
                VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,'unknown','{}'::jsonb) RETURNING *''',
                (ref,inbox['tenant_id'],locator.account_id,locator.namespace,locator.message_id,route['route_id'],
                 encoded(route_scope(route)),inbox['payload_digest'],kind))
            row=dict(cursor.fetchone());conn.commit();return row,True

    def remember(self,started,value,*,phase='known'):
        if phase not in {'known','suppressed'}:raise SourceUnavailable('KF_BUSINESS_RESULT_INVALID')
        with self.connection_factory() as conn:
            cursor=KfIngressRepository._cursor(conn)
            cursor.execute('SELECT * FROM wecom_kf_business_facts WHERE business_ref=%s FOR UPDATE',(started['business_ref'],))
            row=cursor.fetchone()
            if not row or any(row[key]!=started[key] for key in ('scope','payload_digest','route_id','business_kind')):
                raise SourceUnavailable('KF_BUSINESS_BINDING_CHANGED')
            if row['phase']!='unknown':
                if row['phase']!=phase or row['value']!=value:raise SourceUnavailable('KF_BUSINESS_RESULT_CONFLICT')
                return dict(row)
            cursor.execute('UPDATE wecom_kf_business_facts SET phase=%s,value=%s::jsonb,observed_at=clock_timestamp() WHERE business_ref=%s RETURNING *',
                (phase,encoded(value),started['business_ref']))
            row=dict(cursor.fetchone());conn.commit();return row

    def wire_start(self,locator,kind,endpoint,description,observation=None,*,binding=None):
        with self.connection_factory() as conn:
            cursor=KfIngressRepository._cursor(conn)
            current,inbox,route=read_receipt_in_tx(cursor,locator,lock=True)
            if kind=='transition_to_ai':
                if endpoint!='/cgi-bin/kf/service_state/trans' or description.get('service_state')!=1:
                    raise SourceUnavailable('KF_BUSINESS_TRANSITION_FORBIDDEN')
                self._transition_start(cursor,current,inbox,route,observation)
            if kind=='human_exit':
                if endpoint!='/cgi-bin/kf/service_state/trans' or description.get('service_state')!=4:
                    raise SourceUnavailable('KF_BUSINESS_EXIT_FORBIDDEN')
                self._exit_start(cursor,current,inbox,route,observation)
            ref='kf_wire_'+digest([locator.value(),kind,1])
            cursor.execute('SELECT * FROM wecom_kf_wire_operations WHERE operation_ref=%s FOR UPDATE',(ref,))
            row=cursor.fetchone()
            if row:
                if row['scope']!=route_scope(route) or row['payload_digest']!=inbox['payload_digest'] or row['request_digest']!=digest(description):
                    raise SourceUnavailable('KF_BUSINESS_BINDING_CHANGED')
                if row['phase'] in {'started','unknown'}:raise SourceUnavailable('KF_DELIVERY_WRITE_UNKNOWN')
                if row['phase'] in {'ack','reject'}:conn.commit();return dict(row)
                if row['phase']!='unwritten':raise SourceUnavailable('KF_BUSINESS_BINDING_CHANGED')
                cursor.execute("UPDATE wecom_kf_wire_operations SET phase='started',authorized_epoch=authorized_epoch+1,started_at=clock_timestamp(),observed_at=NULL WHERE operation_ref=%s RETURNING *",(ref,))
            else:
                cursor.execute('''INSERT INTO wecom_kf_wire_operations(operation_ref,tenant_id,locator,scope,payload_digest,
                    ordinal,purpose,endpoint,request_digest,phase,authorized_epoch,started_at,proof)
                    VALUES(%s,%s,%s::jsonb,%s::jsonb,%s,0,%s,%s,%s,'started',1,clock_timestamp(),%s::jsonb) RETURNING *''',
                    (ref,inbox['tenant_id'],encoded(locator.value()),encoded(route_scope(route)),inbox['payload_digest'],kind,
                     endpoint,digest(description),encoded({'service_state':description.get('service_state'),
                        'source_state':0 if kind=='transition_to_ai' else None,**(binding or {})})))
            row=dict(cursor.fetchone())
            if kind=='transition_to_ai':self._transition_start(cursor,current,inbox,route,observation)
            if kind=='human_exit':self._exit_start(cursor,current,inbox,route,observation)
            conn.commit();return row

    def wire_assert(self,locator,operation,observation=None):
        with self.connection_factory() as conn:
            cursor=KfIngressRepository._cursor(conn)
            current,inbox,route=read_receipt_in_tx(cursor,locator,lock=True)
            if operation['purpose']=='transition_to_ai':self._transition_start(cursor,current,inbox,route,observation)
            if operation['purpose']=='human_exit':self._exit_start(cursor,current,inbox,route,observation)
            cursor.execute('SELECT * FROM wecom_kf_wire_operations WHERE operation_ref=%s FOR UPDATE',(operation['operation_ref'],))
            row=cursor.fetchone()
            if (not row or row['phase']!='started' or row['authorized_epoch']!=operation['authorized_epoch']
                    or row['scope']!=route_scope(route) or row['payload_digest']!=inbox['payload_digest']):
                raise SourceUnavailable('KF_BUSINESS_BINDING_CHANGED')
            if operation['purpose']=='transition_to_ai':self._transition_start(cursor,current,inbox,route,observation)
            if operation['purpose']=='human_exit':self._exit_start(cursor,current,inbox,route,observation)

    @staticmethod
    def transfer_fact_in_tx(cursor,row,inbox,route):
        initial=(row.get('checkpoint') or {}).get('source_initial_ref')
        root=(row.get('checkpoint') or {}).get('execution')
        if not initial or not isinstance(root,dict):return None
        found=[]
        def visit(node):
            for call_id,fact in node.get('tools',{}).items():
                if ((fact.get('call') or {}).get('name')=='transfer_to_human'
                        and (fact.get('call') or {}).get('id')==call_id
                        and fact.get('phase') in {'dispatching','completed'}):
                    found.append((node.get('execution_id'),call_id))
            for child in node.get('children',{}).values():
                if isinstance(child.get('checkpoint'),dict):visit(child['checkpoint'])
        visit(root)
        for execution_id,call_id in found:
            kind='tool_transfer_'+digest([execution_id,call_id])
            ref='kf_wire_'+digest([{'source':'wecom_kf','account_id':inbox['account_id'],
                'namespace':inbox['namespace'],'message_id':inbox['message_id']},kind,1])
            cursor.execute('SELECT * FROM wecom_kf_wire_operations WHERE operation_ref=%s',(ref,))
            wire=cursor.fetchone()
            binding={'execution_id':execution_id,'tool_call_id':call_id,'source_runner_id':row['runner_id'],
                     'input_ref':initial}
            if (wire and wire['scope']==route_scope(route) and wire['payload_digest']==inbox['payload_digest']
                    and wire['purpose']==kind and wire['phase']=='ack' and wire['errcode']==0
                    and wire['response_origin']=='platform' and wire['endpoint']=='/cgi-bin/kf/service_state/trans'
                    and type(wire['proof'].get('service_state')) is int and wire['proof']['service_state']==3
                    and all(wire['proof'].get(k)==v for k,v in binding.items())
                    and wire['result'].get('response_origin')=='platform'
                    and type(wire['result'].get('errcode')) is int and wire['result']['errcode']==0):
                return {'execution_id':execution_id,'tool_call_id':call_id,'operation_ref':ref}
        return None

    def candidate(self,after,allow_new=True):
        with self.connection_factory() as conn:
            cursor=KfIngressRepository._cursor(conn)
            cursor.execute('''SELECT account_id,namespace,message_id,receipt_order FROM wecom_kf_inbox i
                WHERE receipt_order>%s AND route_id IS NOT NULL
                AND (%s OR EXISTS(SELECT 1 FROM wecom_kf_business_facts owned
                    WHERE owned.account_id=i.account_id AND owned.namespace=i.namespace AND owned.message_id=i.message_id)
                    OR EXISTS(SELECT 1 FROM wecom_kf_wire_operations w WHERE w.locator=jsonb_build_object('source','wecom_kf','account_id',i.account_id,'namespace',i.namespace,'message_id',i.message_id)))
                AND ((message_type='event' AND payload->'event'->>'event_type'='enter_session')
                  OR (namespace='sync' AND origin=3 AND message_type IN ('text','voice','image','file','video')))
                AND NOT EXISTS(SELECT 1 FROM wecom_kf_business_facts b WHERE b.account_id=i.account_id
                    AND b.namespace=i.namespace AND b.message_id=i.message_id
                    AND b.business_kind IN ('ready','stale','media_filtered') AND b.phase IN ('known','suppressed'))
                AND NOT EXISTS(SELECT 1 FROM wecom_kf_business_facts b WHERE b.account_id=i.account_id
                    AND b.namespace=i.namespace AND b.message_id=i.message_id AND b.business_kind='hidden_command'
                    AND b.phase='known' AND (COALESCE(b.value->>'reply','')='' OR EXISTS(
                        SELECT 1 FROM wecom_kf_wire_operations w WHERE w.locator=jsonb_build_object('source','wecom_kf',
                            'account_id',i.account_id,'namespace',i.namespace,'message_id',i.message_id)
                            AND w.scope=b.scope AND w.payload_digest=b.payload_digest AND w.purpose='hidden_reply'
                            AND w.phase IN ('ack','reject'))))
                AND NOT EXISTS(SELECT 1 FROM wecom_kf_business_facts b WHERE b.account_id=i.account_id
                    AND b.namespace=i.namespace AND b.message_id=i.message_id AND b.business_kind='account_blocked'
                    AND b.phase='known' AND EXISTS(SELECT 1 FROM wecom_kf_wire_operations w
                        WHERE w.locator=jsonb_build_object('source','wecom_kf','account_id',i.account_id,
                            'namespace',i.namespace,'message_id',i.message_id) AND w.scope=b.scope
                            AND w.payload_digest=b.payload_digest AND w.purpose='account_blocked_reply'
                            AND w.phase IN ('ack','reject')))
                ORDER BY receipt_order LIMIT 1''',(after,allow_new))
            return cursor.fetchone()


class BusinessWireObserver:
    def __init__(self,repository,locator,kind,stopping=lambda:False,dispatch_check=None,observation=None):
        self.repository,self.locator,self.kind=repository,locator,kind
        self.stopping,self.dispatch_check=stopping,dispatch_check
        self.observation=observation
        self.last=None

    async def before_post(self,endpoint,description):
        if self.stopping():raise NativeWriteStopped('KF_NATIVE_STOPPED')
        if self.dispatch_check:await self.dispatch_check()
        operation=await _thread(self.repository.wire_start,self.locator,self.kind,endpoint,description,self.observation)
        self.last=operation
        if operation['phase'] in {'ack','reject'}:return NativeWriteDecision(operation,replay_result=operation['result'],dispatch=False)
        return NativeWriteDecision(operation)

    async def assert_dispatch(self,operation):
        if self.stopping():raise NativeWriteStopped('KF_NATIVE_STOPPED')
        if self.dispatch_check:await self.dispatch_check()
        await _thread(self.repository.wire_assert,self.locator,operation,self.observation)
        if self.stopping():raise NativeWriteStopped('KF_NATIVE_STOPPED')

    async def known(self,operation,result):await _thread(DeliveryRepository(self.repository.connection_factory).observe,operation,result=result)
    async def unknown(self,operation,code):await _thread(DeliveryRepository(self.repository.connection_factory).observe,operation,unknown_code=code)
    async def unwritten(self,operation,code):await _thread(DeliveryRepository(self.repository.connection_factory).observe,operation,unwritten_code=code)


class KfCompletionBusiness:
    def __init__(self,config,connection_factory=get_db_connection,*,adapter_factory=WeComKfAdapter):
        self.config=config
        self.repository=BusinessRepository(connection_factory)
        self.adapter_factory=adapter_factory
        self.after=0;self.stopping=False;self.tasks=set()

    async def _hidden_reply(self,adapter,locator,route,fact):
        if fact['phase']!='known':raise SourceUnavailable('KF_BUSINESS_COMMAND_UNKNOWN')
        reply=fact['value']['reply']
        if reply:
            observer=BusinessWireObserver(self.repository,locator,'hidden_reply',stopping=lambda:self.stopping)
            adapter.enable_native_delivery(observer,open_kfid=route['open_kfid'],actor_id=route['actor_id'])
            await adapter.send_text(reply,route['actor_id'])
        return {'locator':locator.value(),'phase':'known','disposition':'hidden_command'}

    async def _blocked_reply(self,adapter,locator,route,fact):
        observer=BusinessWireObserver(self.repository,locator,'account_blocked_reply',stopping=lambda:self.stopping)
        adapter.enable_native_delivery(observer,open_kfid=route['open_kfid'],actor_id=route['actor_id'])
        await adapter.send_text(fact['value']['reply'],route['actor_id'])
        await _thread(self.repository.block_reply_history,locator,fact)
        return {'locator':locator.value(),'phase':'known','disposition':'account_blocked'}

    async def _account_limit(self,adapter,locator,current,inbox,route,kf_config):
        from .prompts import MSG_EXPIRED,MSG_CREDIT_EXHAUSTED
        kind,reply=None,None
        expire_at=kf_config.get('expire_at')
        if expire_at:
            try:
                expired=datetime.now()>datetime.strptime(expire_at,'%Y-%m-%d')+timedelta(hours=23,minutes=59,seconds=59)
            except ValueError:
                expired=False
            if expired:kind,reply='expired',MSG_EXPIRED
        limit=kf_config.get('credit_limit',0)
        if kind is None and limit and limit>0:
            from src.db.models import CustomerReferralDB
            used=await _thread(CustomerReferralDB.sum_kf_account_credit,route['tenant_id'],route['open_kfid'])
            if used>=float(limit):kind,reply='credit_exhausted',MSG_CREDIT_EXHAUSTED
        if kind is None:return None
        content=inbox['payload'].get('text',{}).get('content','') if inbox['message_type']=='text' else await self._blocked_voice_content(adapter,locator,current,inbox,route)
        fact=await _thread(self.repository.block_account,locator,route,current.proof.config_version,kind,reply,content)
        return await self._blocked_reply(adapter,locator,route,fact) if fact else None

    async def _blocked_voice_content(self,adapter,locator,current,inbox,route):
        # The old account gate runs after ASR and before start_record. Only this
        # blocked branch is unbilled; normal voice keeps the Runner's fee owner.
        recognition=inbox['payload']['voice'].get('recognition')
        if isinstance(recognition,str) and recognition.strip():return '[ASR识别结果] '+recognition
        saved=await _thread(self.repository.fact,locator,'blocked_voice_asr')
        if saved is not None:
            return '[ASR识别结果] '+saved['value']['text'] if saved['phase']=='known' and saved['value'].get('success') else '[语音消息]'
        from .voice_media import VoiceMedia
        from src.services.agent_runner.source_receipts import LocalPreparationFailed
        from src.tools.asr.call_observer import asr_call_observer,AsrCallUnknown
        from src.tools.asr.speech_to_text_tool import SpeechToTextTool
        import base64
        try:
            artifact=await VoiceMedia.download_trusted(adapter.api_client,route['tenant_id'],
                'voice_'+digest([locator.value(),'blocked_voice_asr',1]),inbox['payload']['voice']['media_id'])
        except LocalPreparationFailed:
            return '[语音消息]'
        data=await _thread(VoiceMedia.read,route['tenant_id'],artifact)
        expected={'scope':route_scope(route),'config_version':current.proof.config_version,
                  'payload_digest':inbox['payload_digest']}
        owner=self
        class Observer:
            operation=None
            observation_error=None
            async def before_post(self,**metadata):
                if owner.stopping:raise NativeWriteStopped('KF_NATIVE_STOPPED')
                if (metadata['audio_size']!=len(data) or metadata['audio_format']!=artifact['audio_format']
                        or metadata['sample_rate']!=artifact['audio_sample_rate']):
                    raise SourceUnavailable('KF_BUSINESS_BINDING_CHANGED')
                self.operation,new=await _thread(owner.repository.start,locator,'blocked_voice_asr',expected=expected)
                if not new:raise AsrCallUnknown('ASR_ALREADY_DISPATCHED')
                if owner.stopping:raise NativeWriteStopped('KF_NATIVE_STOPPED')
            async def known(self,**result):
                try:await _thread(owner.repository.remember,self.operation,result)
                except BaseException as error:
                    self.observation_error=error
                    raise
            async def unknown(self,code):
                pass  # The durable pre-POST fact remains unknown; never POST2.
        observer=Observer()
        try:
            with asr_call_observer(observer):
                result=await SpeechToTextTool().execute(audio_content=base64.b64encode(data).decode('ascii'),
                    format=artifact['audio_format'],sample_rate=artifact['audio_sample_rate'])
        except AsrCallUnknown:
            if observer.observation_error is not None:
                raise SourceUnavailable('KF_BUSINESS_VOICE_STORAGE_FAILED') from observer.observation_error
            return '[语音消息]'
        return '[ASR识别结果] '+result['text'] if result.get('success') else '[语音消息]'

    async def _exit_notice(self,locator,current,route):
        def ended():
            with self.repository.connection_factory() as conn:
                cursor=KfIngressRepository._cursor(conn)
                _,_,fresh_route=read_receipt_in_tx(cursor,locator,lock=True)
                cursor.execute("UPDATE channel_sessions SET metadata=COALESCE(metadata,'{}'::jsonb)||%s::jsonb WHERE session_id=%s AND tenant_id=%s",
                    (encoded({'service_state':4}),fresh_route['session_id'],fresh_route['tenant_id']))
                conn.commit()
        await _thread(ended)
        adapter=self.adapter_factory(current.proof.corp_id,current.secret)
        await adapter.set_tenant_id(route['tenant_id'])
        try:
            notice=BusinessWireObserver(self.repository,locator,'human_exit_notice',stopping=lambda:self.stopping)
            adapter.enable_native_delivery(notice,open_kfid=route['open_kfid'],actor_id=route['actor_id'])
            await adapter.send_text('已退出人工服务，本次会话已结束。如有新问题，请重新发送消息。',route['actor_id'])
            ready,new=await _thread(self.repository.start,locator,'ready')
            if new:ready=await _thread(self.repository.remember,ready,{'human_exit':True})
            return {'locator':locator.value(),'phase':ready['phase'],'disposition':'human_exit'}
        finally:await _drain(asyncio.create_task(adapter.close()))

    async def handle(self,locator):
        current,inbox,route,kf_config=await _thread(self.repository.read,locator)
        task=asyncio.current_task();self.tasks.add(task)
        adapter=self.adapter_factory(current.proof.corp_id,current.secret)
        await adapter.set_tenant_id(route['tenant_id'])
        adapter.current_open_kfid=route['open_kfid']
        try:
            if self.stopping:return None
            if await _thread(self.repository.known_exit,locator):
                return await self._exit_notice(locator,current,route)
            blocked=await _thread(self.repository.fact,locator,'account_blocked')
            if blocked is not None:
                return await self._blocked_reply(adapter,locator,route,blocked)
            saved_command=await _thread(self.repository.fact,locator,'hidden_command')
            if saved_command is not None:
                return await self._hidden_reply(adapter,locator,route,saved_command)
            if not self.config.wecom_kf.enabled:
                owned=await _thread(self.repository.fact,locator,'registration')
                if owned is None:return None
                if owned['phase']!='known':raise SourceUnavailable('KF_BUSINESS_REGISTRATION_UNKNOWN')
            if self.config.wecom_kf.enabled and inbox['namespace']=='sync' and inbox['origin']==3 and not inbox.get('accepted_input_ref'):
                from src.core.hidden_commands import is_hidden_command,execute_hidden_command
                body=inbox['payload'].get('text',{}).get('content','')
                if (__import__('time').time()-inbox['send_time'])>1800:
                    fact,new=await _thread(self.repository.start,locator,'stale')
                    if new:fact=await _thread(self.repository.remember,fact,{'reason':'older_than_30_minutes'},phase='suppressed')
                    return {'locator':locator.value(),'phase':fact['phase'],'disposition':'stale'}
            # State-0 transition precedes every registration/history side effect.
            if self.config.wecom_kf.enabled and inbox['namespace']=='sync' and inbox['origin']==3:
                adapter.api_client.enable_ingress_mode(self.config.wecom_kf.page_bytes)
                reply=await adapter.api_client.get_service_state(route['open_kfid'],route['actor_id'])
                observed_at=datetime.now(timezone.utc)
                if (type(reply.get('errcode')) is not int or reply['errcode']!=0
                        or type(reply.get('service_state')) is not int or reply['service_state'] not in range(5)):
                    raise SourceUnavailable('KF_BUSINESS_STATE_UNAVAILABLE')
                if reply['service_state']==0:
                    observation=StateObservation(0,observed_at,current.proof.config_version,
                        inbox['payload_digest'],route_scope(route))
                    if not await _thread(self.repository.observe_zero,locator,observation):
                        raise SourceUnavailable('KF_BUSINESS_TRANSITION_FORBIDDEN')
                    transition=BusinessWireObserver(self.repository,locator,'transition_to_ai',
                        stopping=lambda:self.stopping,observation=observation)
                    adapter.enable_native_delivery(transition,open_kfid=route['open_kfid'],actor_id=route['actor_id'])
                    result=await adapter.api_client.trans_service_state(route['open_kfid'],route['actor_id'],1)
                    if result['errcode']!=0:raise SourceUnavailable('KF_BUSINESS_TRANSITION_REJECTED')
                    # Each physical write owner is immutable. Subsequent business
                    # writes use a fresh client, preserving this transition's facts.
                    await _drain(asyncio.create_task(adapter.close()))
                    adapter=self.adapter_factory(current.proof.corp_id,current.secret)
                    await adapter.set_tenant_id(route['tenant_id'])
                    adapter.current_open_kfid=route['open_kfid']
                    adapter.api_client.enable_ingress_mode(self.config.wecom_kf.page_bytes)
            if self.config.wecom_kf.enabled and inbox['namespace']=='sync' and inbox['origin']==3:
                state=reply['service_state']
                if state==0:
                    reply=await adapter.api_client.get_service_state(route['open_kfid'],route['actor_id'])
                    if type(reply.get('errcode')) is not int or reply['errcode']!=0 or type(reply.get('service_state')) is not int:
                        raise SourceUnavailable('KF_BUSINESS_STATE_UNAVAILABLE')
                    state=reply['service_state'];observed_at=datetime.now(timezone.utc)
                def classification():
                    with self.repository.connection_factory() as conn:
                        cursor=KfIngressRepository._cursor(conn)
                        now_current,now_inbox,now_route=read_receipt_in_tx(cursor,locator,lock=True)
                        fixed=LifecycleRepository.classification_in_tx(cursor,now_inbox,now_route)
                        if state==1 and fixed and fixed['classification'] not in {'ai','unknown'}:return fixed
                        if state in (1,3,4):
                            observation=StateObservation(state,observed_at,current.proof.config_version,
                                inbox['payload_digest'],route_scope(route))
                            result=LifecycleRepository.record_observation_in_tx(cursor,now_inbox,now_route,observation)
                            LifecycleRepository.assert_observation_in_tx(cursor,now_current,observation)
                            conn.commit();return result
                        return fixed
                fixed=await _thread(classification)
                if (state==3 and fixed and fixed['classification']=='human' and inbox['message_type']=='text'
                        and adapter.should_exit_human(inbox['payload'].get('text',{}).get('content',''),kf_config)):
                    observation=StateObservation(3,observed_at,current.proof.config_version,
                        inbox['payload_digest'],route_scope(route))
                    observer=BusinessWireObserver(self.repository,locator,'human_exit',
                        stopping=lambda:self.stopping,observation=observation)
                    adapter.enable_native_delivery(observer,open_kfid=route['open_kfid'],actor_id=route['actor_id'])
                    result=await adapter.api_client.trans_service_state(route['open_kfid'],route['actor_id'],4)
                    if type(result.get('errcode')) is not int or result['errcode']!=0:
                        raise SourceUnavailable('KF_BUSINESS_EXIT_REJECTED')
                    await _drain(asyncio.create_task(adapter.close()))
                    return await self._exit_notice(locator,current,route)
                ai=state==1 and await _thread(self.repository.ai_permitted,locator)
                if ai and inbox['message_type']=='text':
                    from src.core.hidden_commands import is_hidden_command,execute_hidden_command
                    body=inbox['payload'].get('text',{}).get('content','')
                    if is_hidden_command(body):
                        fact,new=await _thread(self.repository.start,locator,'hidden_command')
                        if new:
                            command_reply=await _drain(asyncio.create_task(execute_hidden_command(body,route['session_id'],route['tenant_id'])))
                            fact=await _thread(self.repository.remember,fact,{'reply':command_reply or ''})
                        return await self._hidden_reply(adapter,locator,route,fact)
                if ai and inbox['message_type'] in {'image','file','video'}:
                    fact,new=await _thread(self.repository.start,locator,'media_filtered')
                    def recent_hint():
                        with self.repository.connection_factory() as conn:
                            cursor=KfIngressRepository._cursor(conn)
                            cursor.execute("SELECT 1 FROM wecom_kf_wire_operations WHERE scope=%s::jsonb AND purpose='filter_hint' AND phase IN ('ack','started','unknown') AND started_at>clock_timestamp()-interval '5 minutes' LIMIT 1",(encoded(route_scope(route)),))
                            return cursor.fetchone() is not None
                    if not await _thread(recent_hint):
                        from .message import KF_FILTER_HINT_MESSAGE
                        observer=BusinessWireObserver(self.repository,locator,'filter_hint',stopping=lambda:self.stopping)
                        adapter.enable_native_delivery(observer,open_kfid=route['open_kfid'],actor_id=route['actor_id'])
                        await adapter.send_text(KF_FILTER_HINT_MESSAGE,route['actor_id'])
                    if new:fact=await _thread(self.repository.remember,fact,{'reason':'ai_media_filter'},phase='suppressed')
                    return {'locator':locator.value(),'phase':fact['phase'],'disposition':'media_filtered'}
                if ai and inbox['message_type'] in {'text','voice'} and not inbox.get('accepted_input_ref'):
                    blocked=await self._account_limit(adapter,locator,current,inbox,route,kf_config)
                    if blocked:return blocked
            # Registration is the original business result, never a new execution principal.
            started,new=await _thread(self.repository.start,locator,'registration')
            if new:
                from src.saas.services.auto_register import ensure_user_registered
                adapter.api_client.enable_ingress_mode(self.config.wecom_kf.page_bytes)
                reply=await adapter.api_client.get_customer_info(route['actor_id'])
                info={}
                if type(reply.get('errcode')) is int and reply['errcode']==0:
                    rows=reply.get('customer_list') or []
                    matching=[item for item in rows if isinstance(item,dict) and item.get('external_userid')==route['actor_id']]
                    if len(matching)==1:
                        item=matching[0];info={'name':item.get('nickname',''),'avatar':item.get('avatar',''),'gender':item.get('gender',0),'wx_unionid':item.get('unionid','')}
                customer=await _drain(asyncio.create_task(ensure_user_registered('wecom_kf',route['actor_id'],route['tenant_id'],info,source='wecom_kf')))
                if not isinstance(customer,str) or not customer:raise SourceUnavailable('KF_BUSINESS_REGISTRATION_UNKNOWN')
                started=await _thread(self.repository.remember,started,{'customer_user_id':customer,'channel_user_info':info})
            if started['phase']!='known':raise SourceUnavailable('KF_BUSINESS_REGISTRATION_UNKNOWN')
            customer=started['value']['customer_user_id']
            event=inbox['payload'].get('event') or {}
            if event.get('event_type')=='enter_session':
                scene=event.get('scene','')
                if scene:
                    ref,new=await _thread(self.repository.start,locator,'referral')
                    if new:
                        from src.db.models import CustomerReferralDB
                        from src.saas.db.channel_config_db import ChannelConfigDB
                        def referral():
                            for row in ChannelConfigDB.list_by_tenant(route['tenant_id'],'wecom_kf'):
                                value=row.get('config') or {}
                                if isinstance(value,str):value=json.loads(value)
                                for account in value.get('kf_account') or []:
                                    if account.get('scene')==scene and account.get('tenant_user_id'):
                                        CustomerReferralDB.record(tenant_id=route['tenant_id'],referrer_user_id=account['tenant_user_id'],
                                            customer_user_id=customer,open_kfid=route['open_kfid'],scene=scene)
                                        return {'first_touch':True}
                            return {'first_touch':False}
                        result=await _thread(referral)
                        await _thread(self.repository.remember,ref,result)
                    elif ref['phase']=='unknown':raise SourceUnavailable('KF_BUSINESS_REFERRAL_UNKNOWN')
                if inbox.get('capability_ciphertext') and kf_config.get('welcome_message'):
                    from src.core.secret_crypto import decrypt_secret
                    grant=json.loads(decrypt_secret(inbox['capability_ciphertext']))['welcome_code']
                    observer=BusinessWireObserver(self.repository,locator,'welcome',stopping=lambda:self.stopping)
                    adapter.enable_native_delivery(observer,open_kfid=route['open_kfid'],actor_id=route['actor_id'])
                    await adapter.send_welcome_message(grant,kf_config['welcome_message'])
            ref,new=await _thread(self.repository.start,locator,'ready')
            if new:ref=await _thread(self.repository.remember,ref,{'customer_user_id':customer})
            return {'locator':locator.value(),'customer_user_id':customer,'business_ref':ref['business_ref'],'phase':ref['phase']}
        finally:
            try:await _drain(asyncio.create_task(adapter.close()))
            finally:self.tasks.discard(task)

    async def run_once(self):
        if self.stopping:return None
        row=await _thread(self.repository.candidate,self.after,self.config.wecom_kf.enabled)
        if row is None:self.after=0;return None
        self.after=row['receipt_order']
        return await self.handle(SourceLocator('wecom_kf',row['account_id'],row['namespace'],row['message_id']))

    def request_stop(self):self.stopping=True
    async def close(self):
        self.request_stop()
        for task in tuple(self.tasks):await _drain(task)



async def prepare_context_media(connection_factory, client_factory, page_bytes, locator):
    """Original human media display; readonly download, one immutable artifact."""
    repository=BusinessRepository(connection_factory)
    current,inbox,route,_=await _thread(repository.read,locator)
    if inbox['message_type'] not in {'image','video','file'}:
        raise SourceUnavailable('KF_CONTEXT_MEDIA_UNSUPPORTED')
    fact,new=await _thread(repository.start,locator,'context_media')
    if fact['phase']=='known':return fact['value']
    # A failed readonly GET has no physical paid/customer-write effect. The
    # same immutable media reference can be downloaded again under fresh scope.
    client=client_factory(current.proof.corp_id,current.secret)
    client.enable_ingress_mode(page_bytes,media_max_bytes=20*1024*1024)
    kind=inbox['message_type']; block=inbox['payload'].get(kind) or {}
    name=block.get('file_name') or {'image':'图片','video':'视频','file':'文件'}[kind]
    placeholder={'image':'[图片]','video':'[视频]','file':'[文件]'}[kind]+(' '+name if kind=='file' else '')
    try:
        import httpx
        try:
            async with asyncio.timeout(40):data,mime=await client.download_media(block['media_id'])
        except (httpx.HTTPError,RuntimeError,TimeoutError):
            return (await _thread(repository.remember,fact,{'content':placeholder,'attachments':[]}))['value']
        from .voice_media import VoiceMedia
        from src.services.agent_runner.source_receipts import source_offload
        def store():
            import os,stat,uuid
            extension={'image':'.jpg','video':'.mp4','file':'.bin'}[kind]
            if kind=='file':
                from pathlib import Path
                candidate=Path(name).suffix.lower()
                if candidate and len(candidate)<=12 and candidate[1:].isalnum():extension=candidate
            file_id='file_'+digest([fact['business_ref'],block['media_id']])
            directory,fd=VoiceMedia._directory(route['tenant_id'])
            filename=file_id+extension;temporary='.kf_'+uuid.uuid4().hex
            try:
                try:
                    handle=os.open(filename,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=fd)
                    with os.fdopen(handle,'rb') as stream:
                        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode) or stream.read(20971521)!=data:
                            raise SourceUnavailable('KF_CONTEXT_MEDIA_HASH_CONFLICT')
                except FileNotFoundError:
                    handle=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
                    with os.fdopen(handle,'wb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
                    try:os.link(temporary,filename,src_dir_fd=fd,dst_dir_fd=fd,follow_symlinks=False)
                    except FileExistsError:raise SourceUnavailable('KF_CONTEXT_MEDIA_STORAGE_RACE') from None
                from src.channels.base import build_public_url,format_file_size
                artifact={'type':kind,'media_id':block['media_id'],'file_name':name,'mime_type':mime,
                    'file_size':len(data),'local_path':str(directory/filename),'file_id':file_id,
                    'download_url':build_public_url('/api/files/'+file_id+'/download'),
                    'sha256':hashlib.sha256(data).hexdigest()}
                from src.core.redis_client import redis_client
                key=redis_client.make_key('uploaded_file',file_id)
                for field,value in {'file_id':file_id,'name':name,'path':artifact['local_path'],'size':len(data),
                    'mime_type':mime,'type':kind,'tenant_id':route['tenant_id']}.items():redis_client.hset(key,field,value)
                redis_client.expire(key,86400)
                return {'content':placeholder+' ('+format_file_size(len(data))+')\n下载链接: '+artifact['download_url'],
                    'attachments':[artifact]}
            except OSError as error:raise SourceUnavailable('KF_CONTEXT_MEDIA_STORAGE_UNAVAILABLE') from error
            finally:
                try:os.unlink(temporary,dir_fd=fd)
                except FileNotFoundError:pass
                os.close(fd)
        value=await source_offload(store)
        return (await _thread(repository.remember,fact,value))['value']
    finally:await _drain(asyncio.create_task(client.close()))

class KfRuntimeScope:
    """Trusted application-installed context for the original KF tools."""
    def __init__(self,provider,row,control):
        self.provider,self.row,self.control=provider,row,control
        self.repository=BusinessRepository(provider.connection_factory)
        self.slot=ContextVar('kf_native_tool_binding',default=None)
        self.adapter=None

    def _binding(self):
        with self.provider.connection_factory() as conn:
            cursor=KfIngressRepository._cursor(conn)
            ref=(self.row.get('checkpoint') or {}).get('source_initial_ref')
            cursor.execute('SELECT * FROM agent_runner_inputs WHERE input_ref=%s',(ref,))
            fact=cursor.fetchone()
            if not fact or fact['current_runner_id']!=self.row['runner_id']:
                raise SourceUnavailable('SOURCE_INPUT_OWNER_CHANGED')
            locator=SourceLocator(**fact['locator'])
            current,inbox,route,provenance=self.provider.read_in_tx(cursor,locator)
            if provenance!=fact['provenance']:
                raise SourceUnavailable('SOURCE_INPUT_OWNER_CHANGED')
            _,cfg=config_in_tx(cursor,inbox['tenant_id'],inbox['config_id'])
            accounts=[a for a in cfg['kf_account'] if a.get('open_kfid')==inbox['open_kfid']]
            if len(accounts)!=1:raise SourceUnavailable('KF_BUSINESS_ACCOUNT_CHANGED')
            cursor.execute("""SELECT value FROM wecom_kf_business_facts WHERE scope=%s::jsonb
                AND business_kind='registration' AND phase='known' ORDER BY observed_at DESC""",(encoded(route_scope(route)),))
            registrations=cursor.fetchall()
            users={r['value'].get('customer_user_id') for r in registrations}
            if None in users or len(users)>1:raise SourceUnavailable('KF_BUSINESS_REGISTRATION_CONFLICT')
            cursor.execute('SELECT metadata FROM channel_sessions WHERE session_id=%s AND tenant_id=%s',
                           (route['session_id'],route['tenant_id']))
            session=cursor.fetchone()
            if session is None:raise SourceUnavailable('SOURCE_INPUT_OWNER_CHANGED')
            info=(registrations[0]['value'].get('channel_user_info') or {}) if registrations else {}
            return locator,current,route,accounts[0],next(iter(users),None),info,(session['metadata'] or {}).get('lead_capture')

    @asynccontextmanager
    async def enter(self):
        from .context import get_kf_context,set_kf_context,clear_kf_context
        self.locator,current,self.route,account,customer,info,lead=await _thread(self._binding)
        self.adapter=WeComKfAdapter(current.proof.corp_id,current.secret)
        await self.adapter.set_tenant_id(self.route['tenant_id'])
        self.adapter.current_open_kfid=self.route['open_kfid']
        self.adapter.enable_native_delivery(self,open_kfid=self.route['open_kfid'],actor_id=self.route['actor_id'])
        previous=get_kf_context()
        set_kf_context({'adapter':self.adapter,'open_kfid':self.route['open_kfid'],
            'external_userid':self.route['actor_id'],'kf_config':account,'session_id':self.route['session_id'],
            'tenant_id':self.route['tenant_id'],'user_id':customer,
            'channel_user_info':info,'lead_capture':lead})
        self.control.source_tool_completion=self
        try:yield self
        finally:
            if previous is None:clear_kf_context()
            else:set_kf_context(previous)
            self.control.source_tool_completion=None
            await _drain(asyncio.create_task(self.adapter.close()))

    @asynccontextmanager
    async def tool_scope(self,state,call):
        if state.tools[call.id].phase!='dispatching':raise SourceUnavailable('SOURCE_TOOL_BINDING_CHANGED')
        slot={'execution_id':state.execution_id,'call_id':call.id,'tool_name':call.name,'operation':None,'known':None}
        token=self.slot.set(slot)
        try:yield
        finally:self.slot.reset(token)

    async def before_post(self,endpoint,description):
        slot=self.slot.get()
        if (slot is None or slot['tool_name']!='transfer_to_human'
                or endpoint!='/cgi-bin/kf/service_state/trans' or description.get('service_state')!=3):
            raise NativeWriteStopped('SOURCE_TOOL_WRITE_UNSUPPORTED')
        await self.control.authorize_dispatch()
        kind='tool_transfer_'+digest([slot['execution_id'],slot['call_id']])
        operation=await _thread(self.repository.wire_start,self.locator,kind,endpoint,description,
            binding={'execution_id':slot['execution_id'],'tool_call_id':slot['call_id'],
                     'source_runner_id':self.row['runner_id'],
                     'input_ref':(self.row.get('checkpoint') or {}).get('source_initial_ref')})
        slot['operation']=operation
        if operation['phase'] in {'ack','reject'}:
            slot['known']=operation['result']
            return NativeWriteDecision(operation,replay_result=operation['result'],dispatch=False)
        return NativeWriteDecision(operation)

    async def assert_dispatch(self,operation):
        await self.control.authorize_dispatch()
        await _thread(self.repository.wire_assert,self.locator,operation)
        await self.control.authorize_dispatch()

    async def known(self,operation,result):
        await _thread(DeliveryRepository(self.provider.connection_factory).observe,operation,result=result)
        slot=self.slot.get()
        if slot is None or slot['operation']['operation_ref']!=operation['operation_ref']:
            raise SourceUnavailable('SOURCE_TOOL_BINDING_CHANGED')
        slot['known']=result

    async def unknown(self,operation,code):
        await _thread(DeliveryRepository(self.provider.connection_factory).observe,operation,unknown_code=code)

    async def unwritten(self,operation,code):
        await _thread(DeliveryRepository(self.provider.connection_factory).observe,operation,unwritten_code=code)

    async def complete(self,state,call,result):
        from dataclasses import replace
        from src.core.agent_engine.contracts import TerminalDirective
        slot=self.slot.get()
        known=(slot or {}).get('known')
        if call.name=='transfer_to_human' and result.success:
            if (not known or known.get('response_origin')!='platform' or type(known.get('errcode')) is not int
                    or known['errcode']!=0 or slot['execution_id']!=state.execution_id or slot['call_id']!=call.id):
                raise SourceUnavailable('SOURCE_TOOL_COMPLETION_UNKNOWN')
            return replace(result,final_output='',terminal_directive=TerminalDirective.STOP_EXECUTION)
        return result



def native_receipt_owned(tenant_id,config_id,corp_id,open_kfid,message):
    actor=message.get('external_userid') or (message.get('event') or {}).get('external_userid')
    mid=message.get('msgid')
    if not isinstance(mid,str) or not mid or not isinstance(actor,str):return False
    with get_db_connection() as conn:
        cursor=KfIngressRepository._cursor(conn)
        cursor.execute("""SELECT 1 FROM wecom_kf_inbox i JOIN channel_session_routes r ON r.route_id=i.route_id
            WHERE i.tenant_id=%s AND i.config_id=%s AND i.corp_id=%s AND i.open_kfid=%s
              AND i.actor_id=%s AND i.namespace='sync' AND i.message_id=%s
              AND r.tenant_id=i.tenant_id AND r.config_id=i.config_id AND r.corp_id=i.corp_id
              AND r.open_kfid=i.open_kfid AND r.actor_id=i.actor_id AND r.source='wecom_kf'
              AND r.chat_kind='kf_direct' AND r.chat_id=i.open_kfid
              AND (i.accepted_input_ref IS NOT NULL OR EXISTS(SELECT 1 FROM wecom_kf_receipt_classifications c
                  WHERE c.account_id=i.account_id AND c.namespace=i.namespace AND c.message_id=i.message_id
                    AND c.payload_digest=i.payload_digest AND c.route_id=i.route_id)
                OR EXISTS(SELECT 1 FROM wecom_kf_business_facts b WHERE b.account_id=i.account_id
                    AND b.namespace=i.namespace AND b.message_id=i.message_id AND b.route_id=i.route_id
                    AND b.payload_digest=i.payload_digest)) LIMIT 1""",
            (tenant_id,config_id,corp_id,open_kfid,actor,mid))
        return cursor.fetchone() is not None
