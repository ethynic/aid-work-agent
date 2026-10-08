"""Thin PG intent handoff to the existing recap adapters in background_runner.

A void adapter return means dispatch_returned; its business effect is not known.
An already-dispatched whole job is never replayed after response/storage loss.
"""

import asyncio
import uuid
from datetime import datetime,timezone
from .ingress_auth import encoded
from src.db.database import get_db_connection
from src.services.agent_runner.source_receipts import SourceLocator,SourceUnavailable
from .completion_business import BusinessRepository
from .ingress_repository import KfIngressRepository
from .ingress_worker import _thread,_drain
from .lifecycle_repository import read_receipt_in_tx,route_scope


class KfRecapHandoff:
    def __init__(self,connection_factory=get_db_connection):
        self.connection_factory=connection_factory
        self.business=BusinessRepository(connection_factory)
        self.after='';self.stopping=False;self.tasks=set();self.owner_id='kf-recap-'+uuid.uuid4().hex

    def _candidate(self):
        with self.connection_factory() as conn:
            cursor=KfIngressRepository._cursor(conn)
            cursor.execute('''SELECT t.* FROM wecom_kf_context_task_intents t WHERE t.task_id>%s
                AND t.state IN ('pending_adapter','claimed') ORDER BY t.task_id LIMIT 1''',(self.after,))
            return cursor.fetchone()

    def _payload(self,intent):
        locator=SourceLocator('wecom_kf',intent['account_id'],intent['namespace'],intent['message_id'])
        with self.connection_factory() as conn:
            cursor=KfIngressRepository._cursor(conn)
            _,inbox,route=read_receipt_in_tx(cursor,locator,lock=True)
            if intent['scope']!=route_scope(route) or intent['session_id']!=route['session_id']:
                raise SourceUnavailable('KF_RECAP_BINDING_CHANGED')
            cursor.execute('SELECT * FROM channel_messages WHERE message_id=%s AND tenant_id=%s AND session_id=%s',
                (intent['history_id'],route['tenant_id'],route['session_id']))
            message=cursor.fetchone()
            if not message:return None
            metadata=message['metadata'] or {}
            if any(metadata.get(key)!=value for key,value in route_scope(route).items() if key!='source'):
                raise SourceUnavailable('KF_RECAP_HISTORY_CHANGED')
            cursor.execute("SELECT value FROM wecom_kf_business_facts WHERE tenant_id=%s AND route_id=%s AND business_kind='registration' AND phase='known' ORDER BY observed_at LIMIT 1",
                           (route['tenant_id'],route['route_id']))
            business=cursor.fetchone()
            user_id=(business['value'].get('customer_user_id') if business else route['user_id']) if metadata.get('input_ref') else None
            from src.services.recap.runner import RecapPayload
            return RecapPayload(tenant_id=route['tenant_id'],session_id=route['session_id'],
                subagent_name=route['profile_id'],round_message_id=intent['history_id'],
                user_content=message['content'],assistant_reply=self._assistant(cursor,route,message),user_id=user_id,
                task_config=[{'name':intent['task_name'],'when':'every_round','enabled':True}])

    @staticmethod
    def _assistant(cursor,route,message):
        runner=(message['metadata'] or {}).get('runner_id')
        if not (message['metadata'] or {}).get('input_ref') or not runner:return ''
        cursor.execute("SELECT content FROM channel_messages WHERE tenant_id=%s AND session_id=%s AND role='assistant' AND metadata->>'runner_id'=%s AND NOT(metadata ? 'tool_calls') ORDER BY id DESC LIMIT 1",
            (route['tenant_id'],route['session_id'],runner))
        row=cursor.fetchone();return row['content'] if row else ''

    def _claim(self,intent):
        locator=SourceLocator('wecom_kf',intent['account_id'],intent['namespace'],intent['message_id'])
        with self.connection_factory() as conn:
            cursor=KfIngressRepository._cursor(conn)
            _,inbox,route=read_receipt_in_tx(cursor,locator,lock=True)
            cursor.execute('SELECT * FROM wecom_kf_context_task_intents WHERE task_id=%s FOR UPDATE',(intent['task_id'],))
            current=cursor.fetchone()
            if not current or current['scope']!=route_scope(route):raise SourceUnavailable('KF_RECAP_BINDING_CHANGED')
            if current['state'] not in {'pending_adapter','claimed'}:return None
            ref=self.business.ref(locator,'recap:'+intent['task_name'])
            cursor.execute('SELECT * FROM wecom_kf_business_facts WHERE business_ref=%s FOR UPDATE',(ref,))
            fact=cursor.fetchone()
            cursor.execute('SELECT clock_timestamp() AS now');now=cursor.fetchone()['now']
            if fact:
                if fact['scope']!=route_scope(route) or fact['payload_digest']!=inbox['payload_digest']:
                    raise SourceUnavailable('KF_RECAP_BINDING_CHANGED')
                value=fact['value']
                if value.get('disposition')!='claimed':return None
                if datetime.fromisoformat(value['lease_until'])>now:return None
            from datetime import timedelta
            value={'disposition':'claimed','owner_id':self.owner_id,'lease_until':(now+timedelta(seconds=30)).isoformat(),'task_id':intent['task_id']}
            cursor.execute('''INSERT INTO wecom_kf_business_facts(business_ref,tenant_id,account_id,namespace,message_id,
                route_id,scope,payload_digest,business_kind,phase,value) VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,'unknown',%s::jsonb)
                ON CONFLICT(business_ref) DO UPDATE SET value=EXCLUDED.value,observed_at=clock_timestamp() RETURNING *''',
                (ref,inbox['tenant_id'],locator.account_id,locator.namespace,locator.message_id,route['route_id'],encoded(route_scope(route)),
                 inbox['payload_digest'],'recap:'+intent['task_name'],encoded(value)))
            fact=dict(cursor.fetchone())
            cursor.execute("UPDATE wecom_kf_context_task_intents SET state='claimed' WHERE task_id=%s",(intent['task_id'],))
            conn.commit();return fact

    def _mark(self,intent,claim,disposition):
        with self.connection_factory() as conn:
            cursor=KfIngressRepository._cursor(conn)
            cursor.execute('SELECT * FROM wecom_kf_context_task_intents WHERE task_id=%s FOR UPDATE',(intent['task_id'],))
            task=cursor.fetchone()
            cursor.execute('SELECT * FROM wecom_kf_business_facts WHERE business_ref=%s FOR UPDATE',(claim['business_ref'],))
            saved=cursor.fetchone()
            if (not task or not saved or saved['scope']!=claim['scope'] or saved['payload_digest']!=claim['payload_digest']
                    or saved['value'].get('owner_id')!=self.owner_id):raise SourceUnavailable('KF_RECAP_OWNER_CHANGED')
            expected='claimed' if disposition=='started' else 'started'
            if task['state']!=expected or saved['value'].get('disposition')!=expected:
                raise SourceUnavailable('KF_RECAP_OWNER_CHANGED')
            value={**saved['value'],'disposition':disposition}
            cursor.execute('UPDATE wecom_kf_business_facts SET phase=%s,value=%s::jsonb,observed_at=clock_timestamp() WHERE business_ref=%s',
                ('known' if disposition=='dispatch_returned' else 'unknown',encoded(value),claim['business_ref']))
            cursor.execute('UPDATE wecom_kf_context_task_intents SET state=%s WHERE task_id=%s',(disposition,intent['task_id']))
            conn.commit()

    async def run_once(self):
        if self.stopping:return None
        intent=await _thread(self._candidate)
        if not intent:self.after='';return None
        self.after=intent['task_id']
        payload=await _thread(self._payload,intent)
        if payload is None:return None
        from src.services.recap.runner import dispatch_persisted_task, _system_switch_enabled
        if not _system_switch_enabled(intent['task_name']):return None
        locator=SourceLocator('wecom_kf',intent['account_id'],intent['namespace'],intent['message_id'])
        task=asyncio.current_task();self.tasks.add(task)
        try:
            if self.stopping:return None
            claim=await _thread(self._claim,intent)
            if claim is None:return None
            if self.stopping:return None
            await _thread(self._mark,intent,claim,'started')
            # This durable unknown marker is the exact whole-job boundary.
            # The existing adapter owns its model/HTTP effects, fee and cooldown.
            await _drain(asyncio.create_task(dispatch_persisted_task(intent['task_name'],payload)))
            await _thread(self._mark,intent,claim,'dispatch_returned')
            return {'task_id':intent['task_id'],'disposition':'dispatch_returned'}
        finally:self.tasks.discard(task)

    async def run(self,stop):
        while not stop.is_set():
            try:await self.run_once()
            except Exception as error:
                from loguru import logger
                logger.warning('KF recap handoff unavailable kind={}',type(error).__name__)
            try:await asyncio.wait_for(stop.wait(),timeout=2)
            except TimeoutError:pass
        await self.close()

    async def close(self):
        self.stopping=True
        for task in tuple(self.tasks):await _drain(task)
