"""Human/employee ASR facts and their original independent fee owner."""

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal

from src.db.database import get_db_connection
from src.db.models import ChatRecordDB, invalidate_tenant_cache
from loguru import logger
from src.db.channel_message_repository import insert_message, touch_session
from src.services.agent_runner.usage_pricing import grouped_cost
from .ingress_auth import KfIngressError, encoded
from .ingress_repository import KfIngressRepository
from .lifecycle_repository import LifecycleRepository, read_receipt_in_tx, route_scope
from .context_repository import ContextRepository, stable_history_id


OPERATION_VERSION=1


def operation_ref(inbox,route):
    return 'kf_context_asr_' + hashlib.sha256(encoded([
        inbox['account_id'],inbox['namespace'],inbox['message_id'],inbox['payload_digest'],
        route_scope(route),OPERATION_VERSION]).encode()).hexdigest()


def artifact_ref(operation):
    return 'voice_' + hashlib.sha256(operation.encode()).hexdigest()


class ContextVoiceRepository:
    def __init__(self,connection_factory=get_db_connection):
        self.connection_factory=connection_factory

    @staticmethod
    def _prep(cursor,inbox,route,*,lock=False):
        cursor.execute('''SELECT * FROM wecom_kf_context_voice_preparations
            WHERE account_id=%s AND namespace=%s AND message_id=%s AND operation_version=1'''
            + (' FOR UPDATE' if lock else ''),
            (inbox['account_id'],inbox['namespace'],inbox['message_id']))
        result=cursor.fetchone()
        if result and (result['operation_ref']!=operation_ref(inbox,route)
                or result['scope']!=route_scope(route) or result['payload_digest']!=inbox['payload_digest']
                or result['media_id']!=inbox['payload']['voice']['media_id']):
            raise KfIngressError('KF_CONTEXT_VOICE_BINDING_CHANGED')
        return dict(result) if result else None

    @classmethod
    def _current(cls,cursor,locator):
        current,inbox,route=read_receipt_in_tx(cursor,locator,lock=True)
        if inbox.get('accepted_input_ref'):
            raise KfIngressError('KF_CONTEXT_VOICE_ACCEPTED_SOURCE')
        classification=LifecycleRepository.classification_in_tx(cursor,inbox,route)
        if (inbox['namespace']!='sync' or inbox['message_type']!='voice' or not classification
                or classification['classification'] not in {'human','ended','employee'}
                or (inbox['origin']==5)!=(classification['classification']=='employee')):
            raise KfIngressError('KF_CONTEXT_VOICE_CLASSIFICATION_REQUIRED')
        return current,inbox,route,classification

    def read(self,locator):
        with self.connection_factory() as conn:
            cursor=KfIngressRepository._cursor(conn)
            current,inbox,route,classification=self._current(cursor,locator)
            return current,inbox,route,classification,self._prep(cursor,inbox,route)

    def remember(self,locator,expected,*,artifact=None,recognition=None,failed=False):
        """Capture media/preflight once; no new AI permission or credit gate."""
        with self.connection_factory() as conn:
            cursor=KfIngressRepository._cursor(conn)
            current,inbox,route,_=self._current(cursor,locator)
            if (expected['scope']!=route_scope(route) or expected['payload_digest']!=inbox['payload_digest']
                    or expected['config_version']!=current.proof.config_version):
                raise KfIngressError('KF_CONTEXT_VOICE_BINDING_CHANGED')
            stored=self._prep(cursor,inbox,route,lock=True)
            if stored: return stored
            if recognition is not None and recognition!=inbox['payload']['voice'].get('recognition'):
                raise KfIngressError('KF_CONTEXT_VOICE_RECOGNITION_CHANGED')
            known=recognition is not None or failed
            ref=operation_ref(inbox,route)
            cursor.execute('''INSERT INTO wecom_kf_context_voice_preparations
                (operation_ref,account_id,namespace,message_id,operation_version,tenant_id,
                 payload_digest,scope,io_config_version,media_id,phase,artifact,success,transcript,result_kind,
                 cost,finance_pending,record_id,history_projection,observed_at)
                VALUES(%s,%s,%s,%s,1,%s,%s,%s::jsonb,%s,%s,%s,%s::jsonb,%s,%s,%s,
                    %s,%s,%s,'pending',CASE WHEN %s THEN clock_timestamp() ELSE NULL END) RETURNING *''',
                (ref,inbox['account_id'],inbox['namespace'],inbox['message_id'],inbox['tenant_id'],
                 inbox['payload_digest'],encoded(route_scope(route)),current.proof.config_version,inbox['payload']['voice']['media_id'],
                 'known' if known else 'media_ready',encoded(artifact) if artifact else None,
                 recognition is not None if known else None,recognition if recognition is not None else '' if failed else None,
                 'recognition' if recognition is not None else 'preflight' if failed else None,
                 Decimal('0') if known else None,False,ref+'_record',known))
            result=dict(cursor.fetchone())
            conn.commit()
            return result

    def start(self,locator,expected,artifact,price_snapshot):
        if (not isinstance(price_snapshot,dict) or type(price_snapshot.get('version')) is not int
                or price_snapshot['version']!=1 or not isinstance(price_snapshot.get('price'),dict)
                or type(price_snapshot.get('usage_factor')) is not int):
            raise KfIngressError('KF_CONTEXT_ASR_PRICE_UNAVAILABLE')
        with self.connection_factory() as conn:
            cursor=KfIngressRepository._cursor(conn)
            current,inbox,route,_=self._current(cursor,locator)
            if (expected['scope']!=route_scope(route) or expected['payload_digest']!=inbox['payload_digest']
                    or expected['config_version']!=current.proof.config_version):
                raise KfIngressError('KF_CONTEXT_VOICE_BINDING_CHANGED')
            prep=self._prep(cursor,inbox,route,lock=True)
            if not prep or prep['phase']!='media_ready' or prep['artifact']!=artifact:
                raise KfIngressError('KF_CONTEXT_ASR_NOT_DISPATCHABLE')
            # The operation has one physical dispatch. No expired started owner
            # can become media_ready or obtain a second epoch for another POST.
            cursor.execute('''UPDATE wecom_kf_context_voice_preparations SET phase='started',
                authorized_epoch=authorized_epoch+1,io_config_version=%s,price_snapshot=%s::jsonb,finance_pending=TRUE,
                started_at=clock_timestamp(),lease_expires_at=clock_timestamp()+interval '45 seconds',
                updated_at=clock_timestamp() WHERE operation_ref=%s AND phase='media_ready' RETURNING *''',
                (current.proof.config_version,encoded(price_snapshot),prep['operation_ref']))
            result=cursor.fetchone()
            if not result: raise KfIngressError('KF_CONTEXT_ASR_NOT_DISPATCHABLE')
            conn.commit()
            return dict(result)

    @classmethod
    def _authorized(cls,cursor,operation):
        """Observe the original dispatched owner without new dispatch authority."""
        cursor.execute('SELECT * FROM channel_session_routes WHERE route_id=%s FOR UPDATE',
                       (operation['scope']['route_id'],))
        route=cursor.fetchone()
        if not route or route_scope(route)!=operation['scope']:
            raise KfIngressError('KF_CONTEXT_VOICE_BINDING_CHANGED')
        cursor.execute('''SELECT * FROM channel_sessions WHERE session_id=%s AND tenant_id=%s
            AND channel_type='wecom_kf' AND channel_user_id=%s AND channel_chat_id=%s FOR UPDATE''',
            (route['session_id'],route['tenant_id'],route['actor_id'],route['open_kfid']))
        session=cursor.fetchone()
        if (not session or session['user_id']!=route['user_id']
                or (session['subagent_id'] or 'main')!=route['profile_id']):
            raise KfIngressError('KF_CONTEXT_VOICE_BINDING_CHANGED')
        cursor.execute('SELECT * FROM wecom_kf_inbox WHERE account_id=%s AND namespace=%s AND message_id=%s FOR UPDATE',
                       (operation['account_id'],operation['namespace'],operation['message_id']))
        inbox=cursor.fetchone()
        if (not inbox or inbox.get('accepted_input_ref') or inbox['route_id']!=route['route_id']
                or tuple(inbox[k] for k in ('tenant_id','config_id','corp_id','open_kfid','actor_id'))!=
                   tuple(route[k] for k in ('tenant_id','config_id','corp_id','open_kfid','actor_id'))
                or hashlib.sha256(encoded(inbox['payload']).encode()).hexdigest()!=inbox['payload_digest']):
            raise KfIngressError('KF_CONTEXT_VOICE_BINDING_CHANGED')
        prep=cls._prep(cursor,inbox,route,lock=True)
        if (not prep or prep['authorized_epoch']!=operation['authorized_epoch']
                or prep['io_config_version']!=operation['io_config_version']
                or prep['price_snapshot']!=operation['price_snapshot'] or prep['artifact']!=operation['artifact']):
            raise KfIngressError('KF_CONTEXT_ASR_OWNER_CHANGED')
        return prep

    def known(self,operation,*,success,text,status):
        if (type(success) is not bool or type(status) is not int or not -(2**63)<=status<2**63
                or not isinstance(text,str) or len(text.encode())>32768 or '\x00' in text
                or success!=(status==20000000) or (success and not text.strip()) or (not success and text)):
            raise KfIngressError('KF_CONTEXT_ASR_RESULT_INVALID')
        with self.connection_factory() as conn:
            cursor=KfIngressRepository._cursor(conn)
            prep=self._authorized(cursor,operation)
            if prep['phase']=='known':
                if (prep['success'],prep['transcript'],prep['provider_status'])!=(success,text,status):
                    raise KfIngressError('KF_CONTEXT_ASR_RESULT_CONFLICT')
                return prep
            if prep['phase'] not in {'started','unknown'}:
                raise KfIngressError('KF_CONTEXT_ASR_OWNER_CHANGED')
            receipts=[{'phase':'observed','owner':'asr','provider':'aliyun','model':'aliyun-nls-asr',
                'billing_boundary':'main','price_snapshot':prep['price_snapshot'],'usage':{'calls':1 if success else 0}}]
            cost,_,breakdown=grouped_cost(receipts)
            # A stable original fee owner is created only for a known POST.
            cursor.execute('SELECT * FROM chat_records WHERE record_id=%s FOR UPDATE',(prep['record_id'],))
            if cursor.fetchone(): raise KfIngressError('KF_CONTEXT_ASR_RECORD_CONFLICT')
            ChatRecordDB.create_in_tx(cursor,prep['record_id'],tenant_id=prep['tenant_id'],
                session_id=prep['scope']['session_id'],user_id=prep['scope']['user_id'],
                user_message='[人工期语音识别]',model='aliyun-nls-asr',provider='aliyun',
                source_type='wecom_kf_human_asr',status='completed' if success else 'failed',
                asr_calls=1 if success else 0,credit_cost=cost,usage_breakdown=breakdown)
            if cost and not ChatRecordDB.debit_in_tx(cursor,prep['tenant_id'],cost):
                raise KfIngressError('KF_CONTEXT_ASR_TENANT_LOST')
            cursor.execute('''UPDATE wecom_kf_context_voice_preparations SET phase='known',
                success=%s,transcript=%s,provider_status=%s,result_kind='asr',cost=%s,
                finance_pending=FALSE,history_projection='pending',observed_at=clock_timestamp(),
                updated_at=clock_timestamp() WHERE operation_ref=%s RETURNING *''',
                (success,text,status,cost,prep['operation_ref']))
            result=dict(cursor.fetchone());conn.commit()
            if cost:
                try: invalidate_tenant_cache(prep['tenant_id'])
                except Exception as error:
                    logger.warning('Context ASR tenant cache invalidation failed type={}',type(error).__name__)
            return result

    def uncertain(self,operation,code):
        # Never persist arbitrary exception text/provider content as a code.
        allowed={'ASR_RESPONSE_TOO_LARGE','ASR_HTTP_RESULT_UNKNOWN','ASR_RESPONSE_INVALID',
                 'ASR_TRANSCRIPT_MISSING','ASR_RESULT_UNKNOWN'}
        with self.connection_factory() as conn:
            cursor=KfIngressRepository._cursor(conn)
            prep=self._authorized(cursor,operation)
            if prep['phase']=='known': return prep
            cursor.execute('''UPDATE wecom_kf_context_voice_preparations SET phase='unknown',
                failure_code=%s,finance_pending=TRUE,cost=NULL,updated_at=clock_timestamp()
                WHERE operation_ref=%s AND phase IN ('started','unknown') RETURNING *''',
                (code if code in allowed else 'ASR_RESULT_UNKNOWN',prep['operation_ref']))
            result=cursor.fetchone()
            if not result: raise KfIngressError('KF_CONTEXT_ASR_OWNER_CHANGED')
            conn.commit();return dict(result)

    def preflight_failed(self,locator):
        with self.connection_factory() as conn:
            cursor=KfIngressRepository._cursor(conn)
            _,inbox,route,_=self._current(cursor,locator)
            prep=self._prep(cursor,inbox,route,lock=True)
            if not prep or prep['phase']!='media_ready':
                raise KfIngressError('KF_CONTEXT_ASR_NOT_DISPATCHABLE')
            cursor.execute('''UPDATE wecom_kf_context_voice_preparations SET phase='known',
                success=FALSE,transcript='',result_kind='preflight',cost=0,finance_pending=FALSE,
                observed_at=clock_timestamp(),updated_at=clock_timestamp()
                WHERE operation_ref=%s RETURNING *''',(prep['operation_ref'],))
            result=dict(cursor.fetchone());conn.commit();return result

    def unwritten(self,operation):
        """Only the installed before_post owner can prove it has not returned."""
        with self.connection_factory() as conn:
            cursor=KfIngressRepository._cursor(conn)
            prep=self._authorized(cursor,operation)
            if prep['phase']!='started': raise KfIngressError('KF_CONTEXT_ASR_OWNER_CHANGED')
            cursor.execute('''UPDATE wecom_kf_context_voice_preparations SET phase='media_ready',
                price_snapshot=NULL,finance_pending=FALSE,started_at=NULL,lease_expires_at=NULL,
                history_projection=CASE WHEN history_projection IN ('cleared','recalled')
                    THEN history_projection ELSE 'pending' END,updated_at=clock_timestamp() WHERE operation_ref=%s RETURNING *''',(prep['operation_ref'],))
            # Preserve the epoch: the next first POST gets a new epoch and an
            # old callback cannot save a result for this undelivered dispatch.
            result=dict(cursor.fetchone());conn.commit();return result

    def project(self,locator):
        with self.connection_factory() as conn:
            cursor=KfIngressRepository._cursor(conn)
            _,inbox,route,classification=self._current(cursor,locator)
            prep=self._prep(cursor,inbox,route,lock=True)
            if not prep or prep['phase']=='media_ready':
                return {'disposition':'pending_asr','history_id':None}
            prior=ContextRepository._consumption(cursor,inbox)
            if ContextRepository.observe_sources_in_tx(cursor,inbox,route,()):
                result=ContextRepository._put_consumption(cursor,inbox,route,'pending_history',
                    history_id=prior['history_id'] if prior else None)
                conn.commit();return result
            known=prep['phase']=='known'
            content='[ASR识别结果] '+prep['transcript'] if known and prep['success'] else '[语音消息]'
            history_id,content,metadata=ContextRepository.history_projection(inbox,route,classification,content)
            metadata.update({'pending_asr':not known,'asr_phase':prep['phase'],'operation_ref':prep['operation_ref'],
                             'operation_version':OPERATION_VERSION})
            attachments=[{'type':'voice','media_id':prep['media_id'],**(prep['artifact'] or {})}]
            cursor.execute('SELECT * FROM channel_messages WHERE message_id=%s FOR UPDATE',(history_id,))
            history=cursor.fetchone()
            cursor.execute('''SELECT 1 FROM wecom_kf_context_consumptions WHERE account_id=%s AND route_id=%s
                AND target_message_id=%s AND disposition IN ('pending_target','recalled','accepted_target') LIMIT 1''',
                (inbox['account_id'],route['route_id'],inbox['message_id']))
            recalled=cursor.fetchone() is not None
            if history is None and prior and prior['history_id'] is not None:
                cursor.execute("UPDATE wecom_kf_context_voice_preparations SET history_projection='cleared' WHERE operation_ref=%s",
                               (prep['operation_ref'],));conn.commit();return dict(prior)
            if history:
                expected=[(content,metadata)]
                # Only the original exact pending projection may become known.
                for phase in ('started','unknown'):
                    pending={**metadata,'pending_asr':True,'asr_phase':phase}
                    placeholder='[人工客服] [语音消息]' if inbox['origin']==5 else '[语音消息]'
                    expected.append((placeholder,pending))
                if (history['tenant_id']!=inbox['tenant_id'] or history['session_id']!=route['session_id']
                        or history['role']!='user' or (history['content'],history['metadata']) not in expected
                        or (json.loads(history['attachments']) if history.get('attachments') else None)!=attachments):
                    raise KfIngressError('KF_CONTEXT_HISTORY_CONFLICT')
                if history['status']!='active':
                    cursor.execute("UPDATE wecom_kf_context_voice_preparations SET history_projection='cleared' WHERE operation_ref=%s",
                                   (prep['operation_ref'],));conn.commit();return dict(prior) if prior else {
                                       'disposition':'cleared','history_id':history_id}
                recalled=bool(recalled or history['is_recalled'])
                if not recalled:
                    cursor.execute('''UPDATE channel_messages SET content=%s,metadata=%s::jsonb
                        WHERE message_id=%s AND tenant_id=%s AND session_id=%s AND content=%s
                        AND metadata=%s::jsonb AND NOT is_recalled RETURNING message_id''',
                        (content,encoded(metadata),history_id,inbox['tenant_id'],route['session_id'],
                         history['content'],encoded(history['metadata'])))
                    if not cursor.fetchone(): raise KfIngressError('KF_CONTEXT_HISTORY_CONFLICT')
            elif not recalled:
                try: created=datetime.fromtimestamp(inbox['send_time'],timezone.utc).replace(tzinfo=None)
                except (ValueError,OverflowError,OSError): raise KfIngressError('KF_CONTEXT_SEND_TIME_INVALID') from None
                insert_message(cursor,message_id=history_id,session_id=route['session_id'],tenant_id=inbox['tenant_id'],
                    role='user',content=content,metadata=metadata,attachments=attachments,created_at=created)
                touch_session(cursor,route['session_id'])
            if recalled:
                cursor.execute("UPDATE wecom_kf_context_voice_preparations SET history_projection='recalled' WHERE operation_ref=%s",
                               (prep['operation_ref'],));conn.commit()
                return dict(prior) if prior else {'disposition':'recalled','history_id':history_id}
            result=ContextRepository._put_consumption(cursor,inbox,route,'persisted' if known else 'pending_asr',history_id=history_id)
            if known: ContextRepository.create_recap_intents_in_tx(cursor,inbox,route,classification,history_id)
            cursor.execute("UPDATE wecom_kf_context_voice_preparations SET history_projection='projected' WHERE operation_ref=%s",
                           (prep['operation_ref'],));conn.commit();return result
