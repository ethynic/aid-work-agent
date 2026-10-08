"""KF text history, scoped recall and pending recap intents on one cursor."""

import hashlib
from datetime import datetime, timezone

from src.db.database import get_db_connection
from src.db.channel_message_repository import insert_message, touch_session
from .ingress_auth import KfIngressError, encoded, text, current_config_in_tx
from .ingress_repository import KfIngressRepository
from .lifecycle_repository import LifecycleRepository, read_receipt_in_tx, route_scope


def stable_history_id(inbox):
    return 'kf_context_' + hashlib.sha256(encoded([
        inbox['account_id'],inbox['namespace'],inbox['message_id']]).encode()).hexdigest()


class ContextRepository:
    def __init__(self, connection_factory=get_db_connection):
        self.connection_factory=connection_factory
        self.lifecycle=LifecycleRepository(connection_factory)

    def read(self,locator):
        with self.connection_factory() as conn:
            return read_receipt_in_tx(KfIngressRepository._cursor(conn),locator,allow_unbound=True)

    @staticmethod
    def _consumption(cursor,inbox):
        cursor.execute('SELECT * FROM wecom_kf_context_consumptions WHERE account_id=%s AND namespace=%s AND message_id=%s',
                       (inbox['account_id'],inbox['namespace'],inbox['message_id']))
        result=cursor.fetchone()
        if result and (result['payload_digest']!=inbox['payload_digest'] or result['route_id']!=inbox['route_id']):
            raise KfIngressError('KF_CONTEXT_CONSUMPTION_CONFLICT')
        return dict(result) if result else None

    @staticmethod
    def _put_consumption(cursor,inbox,route,disposition,*,history_id=None,target=None):
        cursor.execute('''INSERT INTO wecom_kf_context_consumptions(account_id,namespace,message_id,
            tenant_id,route_id,session_id,payload_digest,history_id,target_message_id,disposition)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(account_id,namespace,message_id) DO UPDATE SET disposition=EXCLUDED.disposition,
              history_id=EXCLUDED.history_id,updated_at=clock_timestamp()
            WHERE wecom_kf_context_consumptions.payload_digest=EXCLUDED.payload_digest
              AND wecom_kf_context_consumptions.route_id IS NOT DISTINCT FROM EXCLUDED.route_id
              AND (wecom_kf_context_consumptions.history_id IS NOT DISTINCT FROM EXCLUDED.history_id
                   OR (wecom_kf_context_consumptions.disposition='pending_history'
                       AND wecom_kf_context_consumptions.history_id IS NULL))
              AND wecom_kf_context_consumptions.target_message_id IS NOT DISTINCT FROM EXCLUDED.target_message_id
            RETURNING *''',(inbox['account_id'],inbox['namespace'],inbox['message_id'],inbox['tenant_id'],
                inbox['route_id'],route['session_id'] if route else None,inbox['payload_digest'],history_id,target,disposition))
        result=cursor.fetchone()
        if result is None: raise KfIngressError('KF_CONTEXT_CONSUMPTION_CONFLICT')
        return dict(result)

    @staticmethod
    def pending_sources_in_tx(cursor,inbox,route):
        # Bounded public-source observations replace all history-as-status inference.
        cursor.execute("""SELECT 1 FROM wecom_kf_inbox i LEFT JOIN channel_session_routes r ON r.route_id=i.route_id
            WHERE i.tenant_id=%s AND i.receipt_order<%s AND i.accepted_input_ref IS NOT NULL
              AND ((r.route_id IS NULL AND i.actor_id=%s AND i.open_kfid=%s)
                OR (r.session_id=%s AND (r.tenant_id IS DISTINCT FROM i.tenant_id
                    OR r.source IS DISTINCT FROM 'wecom_kf' OR r.config_id IS DISTINCT FROM i.config_id
                    OR r.corp_id IS DISTINCT FROM i.corp_id OR r.open_kfid IS DISTINCT FROM i.open_kfid
                    OR r.actor_id IS DISTINCT FROM i.actor_id OR r.chat_kind IS DISTINCT FROM 'kf_direct'
                    OR r.chat_id IS DISTINCT FROM i.open_kfid))) LIMIT 1""",
            (inbox['tenant_id'],inbox['receipt_order'],route['actor_id'],route['open_kfid'],route['session_id']))
        if cursor.fetchone(): raise KfIngressError('KF_CONTEXT_BINDING_CHANGED')
        cursor.execute("""SELECT i.*,r.session_id AS bound_session_id,r.profile_id AS bound_profile_id,row_to_json(r) AS bound_route
            FROM wecom_kf_inbox i JOIN channel_session_routes r ON r.route_id=i.route_id
            LEFT JOIN wecom_kf_context_consumptions c USING(account_id,namespace,message_id)
            LEFT JOIN wecom_kf_receipt_classifications cl USING(account_id,namespace,message_id)
            WHERE i.tenant_id=%s AND r.tenant_id=i.tenant_id AND r.session_id=%s
              AND r.source='wecom_kf' AND r.config_id=i.config_id AND r.corp_id=i.corp_id
              AND r.open_kfid=i.open_kfid AND r.actor_id=i.actor_id
              AND r.chat_kind='kf_direct' AND r.chat_id=i.open_kfid
              AND i.receipt_order<%s
              AND (i.accepted_input_ref IS NOT NULL OR (i.namespace='sync' AND i.receive_seq IS NOT NULL
                AND i.origin=3 AND i.message_type IN ('text','voice')
                AND (cl.classification IS NULL OR cl.classification='ai'
                     OR (cl.classification='unknown' AND cl.observed_state=0 AND NOT EXISTS(SELECT 1 FROM wecom_kf_wire_operations w
                         WHERE w.locator=jsonb_build_object('source','wecom_kf','account_id',i.account_id,
                           'namespace',i.namespace,'message_id',i.message_id) AND w.scope=cl.scope
                           AND w.payload_digest=i.payload_digest AND w.purpose='transition_to_ai'
                           AND w.endpoint='/cgi-bin/kf/service_state/trans'
                           AND w.phase='reject' AND w.response_origin='platform' AND w.errcode<>0
                           AND w.proof->'source_state'='0'::jsonb AND w.proof->'service_state'='1'::jsonb))
                     OR cl.payload_digest IS DISTINCT FROM i.payload_digest
                     OR cl.scope IS DISTINCT FROM jsonb_build_object('tenant_id',r.tenant_id,'source',r.source,'config_id',r.config_id,'corp_id',r.corp_id,'open_kfid',r.open_kfid,'actor_id',r.actor_id,'chat_kind',r.chat_kind,'chat_id',r.chat_id,'profile_id',r.profile_id,'session_id',r.session_id,'user_id',r.user_id,'route_id',r.route_id))
                AND NOT EXISTS(SELECT 1 FROM wecom_kf_business_facts handled WHERE handled.account_id=i.account_id
                    AND handled.namespace=i.namespace AND handled.message_id=i.message_id AND handled.scope=jsonb_build_object('tenant_id',r.tenant_id,'source',r.source,'config_id',r.config_id,'corp_id',r.corp_id,'open_kfid',r.open_kfid,'actor_id',r.actor_id,'chat_kind',r.chat_kind,'chat_id',r.chat_id,'profile_id',r.profile_id,'session_id',r.session_id,'user_id',r.user_id,'route_id',r.route_id)
                    AND handled.payload_digest=i.payload_digest AND handled.business_kind IN ('stale','hidden_command','media_filtered','account_blocked')
                    AND (handled.phase IN ('known','suppressed') OR handled.business_kind='hidden_command'))
                AND NOT EXISTS (SELECT 1 FROM wecom_kf_context_consumptions recall
                  JOIN wecom_kf_inbox event ON event.account_id=recall.account_id
                    AND event.namespace=recall.namespace AND event.message_id=recall.message_id
                    AND event.payload_digest=recall.payload_digest AND event.route_id=recall.route_id
                    AND event.tenant_id=i.tenant_id AND event.actor_id=i.actor_id
                    AND event.corp_id=i.corp_id AND event.config_id=i.config_id AND event.open_kfid=i.open_kfid
                  WHERE recall.account_id=i.account_id AND recall.route_id=i.route_id
                    AND recall.target_message_id=i.message_id
                    AND recall.disposition IN ('pending_target','recalled','accepted_target'))))
              AND NOT COALESCE((c.disposition='source_terminal'
                AND c.accepted_input_ref IS NOT DISTINCT FROM i.accepted_input_ref AND c.accepted_input_ref IS NOT NULL AND c.payload_digest=i.payload_digest
                AND c.route_id=i.route_id AND c.session_id=r.session_id
                AND c.completion_observation->'scope'=jsonb_build_object('tenant_id',r.tenant_id,'source',r.source,'config_id',r.config_id,'corp_id',r.corp_id,'open_kfid',r.open_kfid,'actor_id',r.actor_id,'chat_kind',r.chat_kind,'chat_id',r.chat_id,'profile_id',r.profile_id,'session_id',r.session_id,'user_id',r.user_id,'route_id',r.route_id)
                AND c.completion_observation->>'input_ref'=i.accepted_input_ref
                AND c.completion_observation->'locator'=jsonb_build_object('source','wecom_kf',
                    'account_id',i.account_id,'namespace',i.namespace,'message_id',i.message_id)
                AND c.completion_observation->>'current_runner_id'=c.completion_observation->'runner'->>'runner_id'
                AND length(c.completion_observation->>'current_runner_id')>0
                AND c.completion_observation->'runner'->>'status' IN ('completed','failed','cancelled')
                AND c.completion_observation->'runner'->'resume_requested'='false'::jsonb
                AND c.completion_observation->'runner'->'session'=jsonb_build_object('kind','channel','session_id',r.session_id)
                AND c.completion_observation->'runner'->>'profile_id'=r.profile_id
                AND jsonb_typeof(c.completion_observation->'observed_at')='string'
                AND length(c.completion_observation->>'observed_at')>0
                AND c.completion_observation->>'disposition' IN ('applied','cancelled')),FALSE)
            ORDER BY i.receipt_order LIMIT 5""",(inbox['tenant_id'],route['session_id'],inbox['receipt_order']))
        rows=[dict(item) for item in cursor.fetchall()]
        for item in rows:
            if hashlib.sha256(encoded(item['payload']).encode()).hexdigest()!=item['payload_digest']:
                raise KfIngressError('KF_CONTEXT_BINDING_CHANGED')
        return rows

    def pending_sources(self,locator):
        with self.connection_factory() as conn:
            cursor=KfIngressRepository._cursor(conn)
            _,inbox,route=read_receipt_in_tx(cursor,locator)
            return self.pending_sources_in_tx(cursor,inbox,route)

    @staticmethod
    def observe_sources_in_tx(cursor,inbox,route,observations):
        from src.services.agent_runner.source_receipts import SourceLocator
        # The full candidate set is re-read under this session/inbox winner.
        # Concurrent admission also takes that session lock, so no new older
        # accepted link can slip into this same commit after the recheck.
        candidates=ContextRepository.pending_sources_in_tx(cursor,inbox,route)
        pending=False
        for candidate in candidates[:4]:
            if not candidate['accepted_input_ref']:
                pending=True;continue
            item=next((value for value in observations if value['account_id']==candidate['account_id']
                and value['namespace']==candidate['namespace'] and value['message_id']==candidate['message_id']),None)
            if item is None or item['payload_digest']!=candidate['payload_digest'] or item['accepted_input_ref']!=candidate['accepted_input_ref']:
                pending=True;continue
            value=item.get('observation')
            locator=SourceLocator('wecom_kf',candidate['account_id'],candidate['namespace'],candidate['message_id'])
            if not value or value.get('locator')!=locator.value() or value.get('input_ref')!=candidate['accepted_input_ref']:
                pending=True;continue
            state=value['runner']
            observed=datetime.fromisoformat(value['observed_at'])
            cursor.execute('SELECT clock_timestamp() AS now')
            age=(cursor.fetchone()['now']-observed).total_seconds()
            if (not 0<=age<=10 or state['session']['session_id']!=candidate['bound_session_id']
                    or state['profile_id']!=candidate['bound_profile_id'] or state['runner_id']!=value['current_runner_id']
                    or state['status'] not in ('completed','failed','cancelled') or state['resume_requested']
                    or value['disposition'] not in ('applied','cancelled')):
                pending=True;continue
            # applied/cancelled inputs cannot be deferred again. This small
            # domain completion witness does not copy CP/output or SDK state.
            cursor.execute("""INSERT INTO wecom_kf_context_consumptions(account_id,namespace,message_id,
                tenant_id,route_id,session_id,payload_digest,accepted_input_ref,completion_observation,disposition)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,'source_terminal')
                ON CONFLICT(account_id,namespace,message_id) DO NOTHING""",
                (candidate['account_id'],candidate['namespace'],candidate['message_id'],candidate['tenant_id'],
                 candidate['route_id'],candidate['bound_session_id'],candidate['payload_digest'],candidate['accepted_input_ref'],
                 encoded({'input_ref':value['input_ref'],'scope':route_scope(candidate['bound_route']),
                          'locator':locator.value(),'current_runner_id':value['current_runner_id'],
                          'disposition':value['disposition'],'runner':state,'observed_at':value['observed_at']})))
            cursor.execute('SELECT * FROM wecom_kf_context_consumptions WHERE account_id=%s AND namespace=%s AND message_id=%s',
                           (candidate['account_id'],candidate['namespace'],candidate['message_id']))
            cached=cursor.fetchone()
            if (not cached or cached['disposition']!='source_terminal' or cached['accepted_input_ref']!=candidate['accepted_input_ref']
                    or cached['payload_digest']!=candidate['payload_digest'] or cached['route_id']!=candidate['route_id']
                    or cached['completion_observation'].get('scope')!=route_scope(candidate['bound_route'])
                    or cached['completion_observation'].get('current_runner_id')!=value['current_runner_id']):
                raise KfIngressError('KF_CONTEXT_COMPLETION_CONFLICT')
            cursor.execute('SELECT clock_timestamp() AS now')
            if not 0<=(cursor.fetchone()['now']-observed).total_seconds()<=10:
                raise KfIngressError('KF_CONTEXT_OBSERVATION_EXPIRED')
        return pending or len(candidates)>4

    @staticmethod
    def create_recap_intents_in_tx(cursor,inbox,route,classification,history_id):
        if classification['observed_state'] not in (3,4):
            return
        for name in ('lead_refresh','external_push_human'):
            task_id='kf_context_task_' + hashlib.sha256(encoded([
                route_scope(route),inbox['account_id'],inbox['namespace'],inbox['message_id'],name,1]).encode()).hexdigest()
            cursor.execute('''INSERT INTO wecom_kf_context_task_intents(task_id,account_id,namespace,message_id,
                tenant_id,route_id,session_id,history_id,task_name,operation_version,scope,state)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,1,%s::jsonb,'pending_adapter')
                ON CONFLICT(task_id) DO NOTHING''',
                (task_id,inbox['account_id'],inbox['namespace'],inbox['message_id'],inbox['tenant_id'],
                 route['route_id'],route['session_id'],history_id,name,encoded(route_scope(route))))
            cursor.execute('SELECT * FROM wecom_kf_context_task_intents WHERE task_id=%s',(task_id,))
            stored=cursor.fetchone()
            if (not stored or stored['scope']!=route_scope(route) or stored['history_id']!=history_id
                    or stored['state'] not in {'pending_adapter','claimed','started','dispatch_returned','unknown','suppressed'}):
                raise KfIngressError('KF_CONTEXT_TASK_CONFLICT')

    @classmethod
    def history_projection(cls,inbox,route,classification,content,*,servicer_name=''):
        """One domain display shape for ordinary text and prepared voice."""
        employee=inbox['origin']==5
        source='servicer' if employee else 'customer_human' if classification['classification']=='human' else 'customer_ended'
        history_id=stable_history_id(inbox)
        metadata={**route_scope(route),'source':source,'msgid':inbox['message_id'],
            'account_id':inbox['account_id'],'namespace':inbox['namespace'],'origin':inbox['origin'],
            'send_time':inbox['send_time'],'receive_seq':inbox.get('receive_seq'),
            'context_ref':history_id,'servicer_userid':inbox['payload'].get('servicer_userid',''),
            'servicer_name':text(servicer_name,optional=True)}
        return history_id,('[人工客服] '+content if employee else content),metadata

    @classmethod
    def commit_text_in_tx(cls,cursor,inbox,route,classification,*,servicer_name='',observations=(),media_projection=None):
        if inbox.get('accepted_input_ref'):
            return {'disposition':'accepted_source','history_id':None}
        if not route or inbox['namespace']!='sync' or inbox['message_type'] not in {'text','image','video','file'}:
            raise KfIngressError('KF_CONTEXT_TEXT_UNSUPPORTED')
        if classification['classification'] not in ('human','ended','employee'):
            raise KfIngressError('KF_CONTEXT_NOT_CONTEXT')
        if inbox['message_type']=='text':
            content=text(inbox['payload'].get('text',{}).get('content'),limit=65536)
        else:
            if media_projection is None:raise KfIngressError('KF_CONTEXT_MEDIA_REQUIRED')
            content=text(media_projection.get('content'),limit=65536)
        history_id,content,metadata=cls.history_projection(inbox,route,classification,content,servicer_name=servicer_name)
        if media_projection is not None:metadata['attachments']=media_projection['attachments']
        prior=cls._consumption(cursor,inbox)
        if prior and prior['disposition']!='pending_history':
            if prior['disposition']=='persisted':
                if prior['history_id']!=history_id:
                    raise KfIngressError('KF_CONTEXT_HISTORY_CONFLICT')
                cursor.execute('SELECT * FROM channel_messages WHERE message_id=%s FOR UPDATE',(history_id,))
                history=cursor.fetchone()
                if history is None:
                    # An explicit history clear preserves the original consumed
                    # fact. It cannot authorize new effects or recreate history.
                    return prior
                if (history['tenant_id']!=inbox['tenant_id'] or history['session_id']!=route['session_id']
                        or history['metadata']!=metadata or history['content']!=content or history['role']!='user'):
                    raise KfIngressError('KF_CONTEXT_HISTORY_CONFLICT')
                # Another consumer may have persisted employee history while
                # the first optional SDK call was in flight. Its real 3/4 fact
                # can now project the original stable intents exactly once.
                cls.create_recap_intents_in_tx(cursor,inbox,route,classification,history_id)
            return prior
        if cls.observe_sources_in_tx(cursor,inbox,route,observations):
            return cls._put_consumption(cursor,inbox,route,'pending_history')
        cursor.execute('''SELECT 1 FROM wecom_kf_context_consumptions WHERE account_id=%s
            AND route_id=%s AND target_message_id=%s AND disposition IN ('pending_target','recalled','accepted_target') LIMIT 1''',
            (inbox['account_id'],route['route_id'],inbox['message_id']))
        recalled=cursor.fetchone() is not None
        # A stable ID collision is checked, never silently treated as idempotence.
        cursor.execute('SELECT * FROM channel_messages WHERE message_id=%s',(history_id,))
        old=cursor.fetchone()
        if old and (old['tenant_id']!=inbox['tenant_id'] or old['session_id']!=route['session_id']
                    or old['metadata']!=metadata or old['content']!=content or old['role']!='user'):
            raise KfIngressError('KF_CONTEXT_HISTORY_CONFLICT')
        if not old:
            try:
                created=datetime.fromtimestamp(inbox['send_time'],timezone.utc).replace(tzinfo=None)
            except (ValueError,OverflowError,OSError):
                raise KfIngressError('KF_CONTEXT_SEND_TIME_INVALID') from None
            insert_message(cursor,message_id=history_id,session_id=route['session_id'],tenant_id=inbox['tenant_id'],
                role='user',content=content,metadata=metadata,attachments=(media_projection or {}).get('attachments'),created_at=created,is_recalled=recalled)
        touch_session(cursor,route['session_id'])
        result=cls._put_consumption(cursor,inbox,route,'persisted',history_id=history_id)
        cls.create_recap_intents_in_tx(cursor,inbox,route,classification,history_id)
        cursor.execute('''UPDATE wecom_kf_context_consumptions SET disposition='recalled',updated_at=clock_timestamp()
            WHERE account_id=%s AND route_id=%s AND target_message_id=%s AND disposition='pending_target' ''',
            (inbox['account_id'],route['route_id'],inbox['message_id']))
        return result

    @classmethod
    def commit_recall_in_tx(cls,cursor,inbox,route):
        if not route:
            return cls._put_consumption(cursor,inbox,route,'pending_scope')
        event=inbox['payload'].get('event') or {}
        target=text(event.get('recall_msgid'))
        prior=cls._consumption(cursor,inbox)
        if prior and prior['disposition']!='pending_target': return prior
        # No other target route can be acquired after the event inbox lock.
        # Its immutable scope must match this already locked route/session.
        cursor.execute('''SELECT * FROM wecom_kf_inbox WHERE account_id=%s AND namespace='sync'
            AND message_id=%s''',(inbox['account_id'],target))
        target_row=cursor.fetchone()
        if target_row is None:
            return cls._put_consumption(cursor,inbox,route,'pending_target',target=target)
        if any(target_row[key]!=inbox[key] for key in ('tenant_id','config_id','corp_id','open_kfid','actor_id','route_id')):
            raise KfIngressError('KF_CONTEXT_RECALL_SCOPE_CONFLICT')
        if hashlib.sha256(encoded(target_row['payload']).encode()).hexdigest()!=target_row['payload_digest']:
            raise KfIngressError('KF_CONTEXT_RECALL_SCOPE_CONFLICT')
        if target_row.get('accepted_input_ref'):
            # Original execution/fee facts are not rewritten or deleted.
            return cls._put_consumption(cursor,inbox,route,'accepted_target',target=target)
        history_id=stable_history_id(target_row)
        cursor.execute('SELECT * FROM channel_messages WHERE message_id=%s FOR UPDATE',(history_id,))
        history=cursor.fetchone()
        if history is not None:
            metadata=history['metadata']
            scope={key:value for key,value in route_scope(route).items() if key!='source'}
            expected={**scope,'account_id':target_row['account_id'],'namespace':target_row['namespace'],
                'msgid':target_row['message_id'],'context_ref':history_id,'origin':target_row['origin']}
            allowed_source={'servicer'} if target_row['origin']==5 else {'customer_human','customer_ended'}
            if (history['tenant_id']!=target_row['tenant_id'] or history['session_id']!=route['session_id']
                    or history['role']!='user' or not isinstance(metadata,dict)
                    or metadata.get('source') not in allowed_source
                    or any(key not in metadata or metadata[key]!=value for key,value in expected.items())):
                raise KfIngressError('KF_CONTEXT_RECALL_HISTORY_CONFLICT')
            cursor.execute("""UPDATE channel_messages SET is_recalled=TRUE,
                recalled_at=COALESCE(recalled_at,clock_timestamp()) WHERE message_id=%s
                AND tenant_id=%s AND session_id=%s RETURNING message_id""",
                (history_id,target_row['tenant_id'],route['session_id']))
            if cursor.fetchone() is None:
                raise KfIngressError('KF_CONTEXT_RECALL_HISTORY_CONFLICT')
        # A genuinely absent target projection is a valid recall-before-history
        # fact. The original target insert later projects is_recalled once.
        return cls._put_consumption(cursor,inbox,route,'recalled',target=target)

    def classify(self,locator,observation):
        """Persist the reliable SDK fact before independent history projection."""
        with self.connection_factory() as conn:
            cursor=KfIngressRepository._cursor(conn)
            current,inbox,route=read_receipt_in_tx(cursor,locator,lock=True)
            if inbox.get('accepted_input_ref'): return {'disposition':'accepted_source'}
            self.lifecycle.assert_observation_in_tx(cursor,current,observation)
            result=self.lifecycle.record_observation_in_tx(cursor,inbox,route,observation)
            self.lifecycle.assert_observation_in_tx(cursor,current,observation)
            conn.commit()
            return result

    def classify_employee(self,locator,receipt_digest,scope):
        """Employee identity is original receipt proof, independent of SDK state."""
        with self.connection_factory() as conn:
            cursor=KfIngressRepository._cursor(conn)
            _,inbox,route=read_receipt_in_tx(cursor,locator,lock=True)
            if inbox.get('accepted_input_ref'): return {'disposition':'accepted_source'}
            result=self.lifecycle.record_employee_in_tx(cursor,inbox,route,receipt_digest,scope)
            conn.commit()
            return result

    def remember_source(self,locator,entry):
        """Cache one already authenticated public observation while still fresh."""
        with self.connection_factory() as conn:
            cursor=KfIngressRepository._cursor(conn)
            # Capture every CFG share lock before acquiring any route/session.
            # The observation may belong to a different config sharing this SID.
            cursor.execute('SELECT * FROM wecom_kf_inbox WHERE account_id=%s AND namespace=%s AND message_id=%s',
                           (locator.account_id,locator.namespace,locator.message_id))
            located=cursor.fetchone()
            cursor.execute('SELECT i.*,r.profile_id AS bound_profile_id FROM wecom_kf_inbox i LEFT JOIN channel_session_routes r ON r.route_id=i.route_id WHERE i.account_id=%s AND i.namespace=%s AND i.message_id=%s',
                           (entry['account_id'],entry['namespace'],entry['message_id']))
            prior=cursor.fetchone()
            if not located or not prior: raise KfIngressError('KF_CONTEXT_BINDING_CHANGED')
            scopes={(item['tenant_id'],item['config_id'],item['open_kfid']) for item in (located,prior)}
            proofs={key:current_config_in_tx(cursor,*key,lock=True).proof for key in sorted(scopes)}
            previous=proofs[(prior['tenant_id'],prior['config_id'],prior['open_kfid'])]
            if previous.account_id!=prior['account_id'] or previous.corp_id!=prior['corp_id'] or previous.profile_id!=prior['bound_profile_id']:
                raise KfIngressError('KF_CONTEXT_BINDING_CHANGED')
            _,inbox,route=read_receipt_in_tx(cursor,locator,lock=True)
            if inbox.get('accepted_input_ref'): return
            self.observe_sources_in_tx(cursor,inbox,route,(entry,))
            conn.commit()

    def commit(self,locator,*,media_projection=None):
        with self.connection_factory() as conn:
            cursor=KfIngressRepository._cursor(conn)
            _,inbox,route=read_receipt_in_tx(cursor,locator,lock=True,allow_unbound=True)
            if inbox.get('accepted_input_ref'):
                return {'disposition':'accepted_source','history_id':None}
            if inbox['message_type']=='event':
                classification=self.lifecycle.resolve_event_in_tx(cursor,inbox,route)
                event=inbox['payload'].get('event') or {}
                if classification['classification_resolved'] and event.get('event_type')=='user_recall_msg':
                    result=self.commit_recall_in_tx(cursor,inbox,route)
                else:
                    result=self._put_consumption(cursor,inbox,route,'pending_business' if classification['classification_resolved'] else 'pending_scope')
            else:
                classification=self.lifecycle.classification_in_tx(cursor,inbox,route)
                if classification is None: raise KfIngressError('KF_CONTEXT_OBSERVATION_REQUIRED')
                if inbox['message_type'] in {'text','image','video','file'} and classification['classification'] in ('human','ended','employee'):
                    result=self.commit_text_in_tx(cursor,inbox,route,classification,media_projection=media_projection)
                else:
                    result={'disposition':'ai' if classification['classification']=='ai' else 'pending_domain','history_id':None}
            conn.commit()
            return result
