"""Fixed KF receipt classification; current SDK observations are not history."""

import hashlib
from dataclasses import dataclass, field
from datetime import datetime

from src.db.database import get_db_connection
from .channel_session_repository import read_bound_in_tx
from .ingress_auth import KfIngressError, current_config_in_tx, encoded
from .ingress_repository import KfIngressRepository


@dataclass(frozen=True)
class StateObservation:
    state: int
    observed_at: datetime
    config_version: datetime
    receipt_digest: str
    scope: dict = field(repr=False)

    def __post_init__(self):
        if (type(self.state) is not int or self.state not in range(5)
                or not isinstance(self.observed_at, datetime) or self.observed_at.tzinfo is None
                or not isinstance(self.config_version, datetime)
                or not isinstance(self.receipt_digest,str) or len(self.receipt_digest)!=64
                or not isinstance(self.scope,dict)):
            raise KfIngressError('KF_CONTEXT_OBSERVATION_INVALID')


def receipt_key(locator):
    return locator.account_id, locator.namespace, locator.message_id


def route_scope(route):
    return {key: route[key] for key in ('tenant_id','source','config_id','corp_id','open_kfid',
        'actor_id','chat_kind','chat_id','profile_id','session_id','user_id','route_id')}


def read_receipt_in_tx(cursor, locator, *, lock=False, allow_unbound=False):
    """Locate without locks, then CFG -> route/session -> original inbox winner."""
    cursor.execute('SELECT * FROM wecom_kf_account_sync WHERE account_id=%s', (locator.account_id,))
    account = cursor.fetchone()
    if account is None:
        raise KfIngressError('KF_CONTEXT_RECEIPT_NOT_FOUND',404)
    current = current_config_in_tx(cursor,account['tenant_id'],account['config_id'],account['open_kfid'],lock=lock)
    proof=current.proof
    if (proof.account_id!=account['account_id'] or proof.corp_id!=account['corp_id']
            or proof.profile_id!=account['profile_id']):
        raise KfIngressError('KF_CONTEXT_BINDING_CHANGED')
    cursor.execute('SELECT * FROM wecom_kf_inbox WHERE account_id=%s AND namespace=%s AND message_id=%s',receipt_key(locator))
    located=cursor.fetchone()
    if located is None:
        raise KfIngressError('KF_CONTEXT_RECEIPT_NOT_FOUND',404)
    route=None
    if located['actor_id'] and located['route_id']:
        route=read_bound_in_tx(cursor,proof,located['actor_id'],located['route_id'],lock=lock)
    elif not allow_unbound:
        raise KfIngressError('KF_CONTEXT_ROUTE_UNAVAILABLE')
    cursor.execute('SELECT * FROM wecom_kf_inbox WHERE account_id=%s AND namespace=%s AND message_id=%s'
                   + (' FOR UPDATE' if lock else ''),receipt_key(locator))
    inbox=cursor.fetchone()
    if (inbox is None or inbox['actor_id']!=located['actor_id'] or inbox['route_id']!=located['route_id']
            or tuple(inbox[k] for k in ('tenant_id','config_id','corp_id','open_kfid')) !=
            (proof.tenant_id,proof.config_id,proof.corp_id,proof.open_kfid)
            or hashlib.sha256(encoded(inbox['payload']).encode()).hexdigest()!=inbox['payload_digest']):
        raise KfIngressError('KF_CONTEXT_BINDING_CHANGED')
    return current,dict(inbox),dict(route) if route else None


class LifecycleRepository:
    def __init__(self, connection_factory=get_db_connection):
        self.connection_factory=connection_factory

    def read_candidate(self, after=0):
        with self.connection_factory() as conn:
            cursor=KfIngressRepository._cursor(conn)
            cursor.execute('''SELECT i.account_id,i.namespace,i.message_id,i.receipt_order
                FROM wecom_kf_inbox i LEFT JOIN wecom_kf_receipt_classifications c
                  USING(account_id,namespace,message_id)
                LEFT JOIN wecom_kf_context_consumptions h USING(account_id,namespace,message_id)
                LEFT JOIN wecom_kf_context_voice_preparations p USING(account_id,namespace,message_id)
                WHERE i.receipt_order>%s AND i.accepted_input_ref IS NULL
                  AND ((i.message_type='event' AND (h.disposition IS NULL OR h.disposition='pending_target'))
                    OR (i.namespace='sync' AND i.origin IN (3,5) AND i.message_type IN ('text','voice','image','video','file')
                        AND (h.disposition IS NULL OR h.disposition='pending_history')
                        AND (i.message_type='text' OR p.operation_ref IS NULL OR p.history_projection NOT IN ('cleared','recalled'))
                        AND (c.classification IS NULL OR c.classification IN ('human','ended','employee')))
                    OR (i.namespace='sync' AND i.message_type='voice' AND i.origin IN (3,5)
                        AND c.classification IN ('human','ended','employee')
                        AND p.history_projection='pending'
                        AND (p.phase IN ('started','unknown','known')
                            OR (p.phase='media_ready' AND h.disposition='pending_asr')))
                    OR (i.namespace='sync' AND i.origin=5 AND i.message_type IN ('text','voice','image','video','file')
                        AND c.classification='employee' AND c.observed_state IN (3,4)
                        AND h.disposition='persisted'
                        AND EXISTS (SELECT 1 FROM channel_messages m WHERE m.message_id=h.history_id
                            AND m.tenant_id=i.tenant_id AND m.session_id=h.session_id)
                        AND (NOT EXISTS (SELECT 1 FROM wecom_kf_context_task_intents t
                            WHERE t.account_id=i.account_id AND t.namespace=i.namespace AND t.message_id=i.message_id
                              AND t.task_name='lead_refresh' AND t.operation_version=1)
                          OR NOT EXISTS (SELECT 1 FROM wecom_kf_context_task_intents t
                            WHERE t.account_id=i.account_id AND t.namespace=i.namespace AND t.message_id=i.message_id
                              AND t.task_name='external_push_human' AND t.operation_version=1))))
                ORDER BY i.receipt_order LIMIT 1''',(after,))
            return cursor.fetchone()

    @staticmethod
    def classification_in_tx(cursor,inbox,route):
        cursor.execute('SELECT * FROM wecom_kf_receipt_classifications WHERE account_id=%s AND namespace=%s AND message_id=%s',
                       (inbox['account_id'],inbox['namespace'],inbox['message_id']))
        row=cursor.fetchone()
        if row and (row['payload_digest']!=inbox['payload_digest'] or row['scope']!=route_scope(route)):
            raise KfIngressError('KF_CONTEXT_CLASSIFICATION_CONFLICT')
        return dict(row) if row else None

    @staticmethod
    def assert_observation_in_tx(cursor,current,observation):
        cursor.execute('SELECT clock_timestamp() AS now')
        age=(cursor.fetchone()['now']-observation.observed_at).total_seconds()
        if not 0<=age<=10 or observation.config_version!=current.proof.config_version:
            raise KfIngressError('KF_CONTEXT_OBSERVATION_EXPIRED')

    @classmethod
    def record_employee_in_tx(cls,cursor,inbox,route,receipt_digest,scope):
        if inbox.get('accepted_input_ref'):
            return None
        if (inbox['origin']!=5 or inbox['namespace']!='sync' or inbox['message_type'] not in {'text','voice','image','video','file'}
                or receipt_digest!=inbox['payload_digest'] or scope!=route_scope(route)):
            raise KfIngressError('KF_CONTEXT_OBSERVATION_CONFLICT')
        existing=cls.classification_in_tx(cursor,inbox,route)
        if existing:
            if existing['classification']!='employee':
                raise KfIngressError('KF_CONTEXT_CLASSIFICATION_CONFLICT')
            return existing
        cursor.execute("""INSERT INTO wecom_kf_receipt_classifications(account_id,namespace,message_id,
            tenant_id,route_id,payload_digest,scope,classification,classification_resolved,business_pending)
            VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb,'employee',TRUE,FALSE) RETURNING *""",
            (inbox['account_id'],inbox['namespace'],inbox['message_id'],inbox['tenant_id'],route['route_id'],
             inbox['payload_digest'],encoded(scope)))
        return dict(cursor.fetchone())

    @classmethod
    def record_observation_in_tx(cls,cursor,inbox,route,observation):
        # All callers already own the original inbox lock. Accepted is immutable
        # service provenance; no classification is minted for that old fact.
        if inbox.get('accepted_input_ref'):
            return None
        if observation.receipt_digest!=inbox['payload_digest'] or observation.scope!=route_scope(route):
            raise KfIngressError('KF_CONTEXT_OBSERVATION_CONFLICT')
        existing=cls.classification_in_tx(cursor,inbox,route)
        if existing:
            if (existing['classification']=='employee' and inbox['origin']==5
                    and existing['observed_state'] is None and existing['observed_at'] is None
                    and existing['io_config_version'] is None):
                cursor.execute("""UPDATE wecom_kf_receipt_classifications SET observed_state=%s,
                    observed_at=%s,io_config_version=%s WHERE account_id=%s AND namespace=%s AND message_id=%s
                    AND classification='employee' AND observed_state IS NULL
                    AND observed_at IS NULL AND io_config_version IS NULL RETURNING *""",
                    (observation.state,observation.observed_at,observation.config_version,
                     inbox['account_id'],inbox['namespace'],inbox['message_id']))
                updated=cursor.fetchone()
                if updated is None: raise KfIngressError('KF_CONTEXT_CLASSIFICATION_CONFLICT')
                return dict(updated)
            return existing
        kind='employee' if inbox['origin']==5 else {1:'ai',3:'human',4:'ended'}.get(observation.state,'unknown')
        if kind=='ai':
            cursor.execute('''SELECT 1 FROM wecom_kf_receipt_classifications c
                JOIN wecom_kf_inbox event USING(account_id,namespace,message_id)
                WHERE c.account_id=%s AND c.route_id=%s AND c.classification='event'
                  AND NOT c.classification_resolved AND c.event_state IS NOT NULL
                  AND event.receipt_order<=%s LIMIT 1''',
                (inbox['account_id'],route['route_id'],inbox['receipt_order']))
            if cursor.fetchone(): kind='unknown'
        cursor.execute('''INSERT INTO wecom_kf_receipt_classifications(account_id,namespace,message_id,
            tenant_id,route_id,payload_digest,scope,classification,observed_state,observed_at,io_config_version,
            classification_resolved,business_pending)
            VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,TRUE,FALSE) RETURNING *''',
            (inbox['account_id'],inbox['namespace'],inbox['message_id'],inbox['tenant_id'],route['route_id'],
             inbox['payload_digest'],encoded(route_scope(route)),kind,observation.state,observation.observed_at,observation.config_version))
        return dict(cursor.fetchone())

    @staticmethod
    def ai_candidate_sql(inbox='i',classification='c'):
        """Coarse selection only; the locked binding authorizer grants permission."""
        if inbox not in {'i','row'} or classification not in {'c','cl'}:
            raise ValueError('KF_CANDIDATE_ALIAS_INVALID')
        return f"""({classification}.classification IS NULL OR {classification}.classification='ai'
            OR ({classification}.classification='unknown' AND {classification}.observed_state=0
                AND {classification}.payload_digest={inbox}.payload_digest
                AND EXISTS(SELECT 1 FROM wecom_kf_wire_operations w
                  WHERE w.locator=jsonb_build_object('source','wecom_kf','account_id',{inbox}.account_id,
                    'namespace',{inbox}.namespace,'message_id',{inbox}.message_id)
                    AND w.scope={classification}.scope AND w.payload_digest={inbox}.payload_digest
                    AND w.purpose='transition_to_ai' AND w.endpoint='/cgi-bin/kf/service_state/trans'
                    AND w.phase='ack' AND w.response_origin='platform' AND w.errcode=0
                    AND w.proof->'source_state'='0'::jsonb AND w.proof->'service_state'='1'::jsonb)))
            AND NOT EXISTS(SELECT 1 FROM wecom_kf_business_facts handled
                WHERE handled.account_id={inbox}.account_id AND handled.namespace={inbox}.namespace
                  AND handled.message_id={inbox}.message_id AND handled.payload_digest={inbox}.payload_digest
                  AND handled.business_kind IN ('stale','hidden_command','media_filtered')
                  AND (handled.phase IN ('known','suppressed') OR handled.business_kind='hidden_command'))
            AND NOT EXISTS(SELECT 1 FROM wecom_kf_context_consumptions recall
                JOIN wecom_kf_inbox event USING(account_id,namespace,message_id)
                WHERE recall.account_id={inbox}.account_id AND recall.route_id={inbox}.route_id
                  AND recall.target_message_id={inbox}.message_id AND event.route_id={inbox}.route_id
                  AND event.tenant_id={inbox}.tenant_id AND event.payload_digest=recall.payload_digest
                  AND recall.disposition IN ('pending_target','recalled','accepted_target'))"""

    @classmethod
    def transition_permit_in_tx(cls,cursor,inbox,route):
        """A receipt's original state-0 fact stays immutable after its own ACK."""
        row=cls.classification_in_tx(cursor,inbox,route)
        if not row or row['classification']!='unknown' or row['observed_state']!=0:
            return False
        cursor.execute("""SELECT scope,payload_digest,proof,result FROM wecom_kf_wire_operations
            WHERE locator=%s::jsonb AND purpose='transition_to_ai'
              AND endpoint='/cgi-bin/kf/service_state/trans' AND phase='ack'
              AND response_origin='platform' AND errcode=0""",
            (encoded({'source':'wecom_kf','account_id':inbox['account_id'],
                      'namespace':inbox['namespace'],'message_id':inbox['message_id']}),))
        values=cursor.fetchall()
        return len(values)==1 and values[0]['scope']==route_scope(route) and (
            values[0]['payload_digest']==inbox['payload_digest']
            and type(values[0]['proof'].get('source_state')) is int
            and values[0]['proof'].get('source_state')==0
            and type(values[0]['proof'].get('service_state')) is int
            and values[0]['proof'].get('service_state')==1
            and values[0]['result'].get('response_origin')=='platform'
            and type(values[0]['result'].get('errcode')) is int
            and values[0]['result']['errcode']==0)

    @staticmethod
    def recalled_in_tx(cursor,inbox,route):
        cursor.execute("""SELECT e.* FROM wecom_kf_context_consumptions c JOIN wecom_kf_inbox e
            USING(account_id,namespace,message_id) WHERE c.account_id=%s AND c.route_id=%s
            AND c.target_message_id=%s AND c.disposition IN ('pending_target','recalled','accepted_target')""",
            (inbox['account_id'],route['route_id'],inbox['message_id']))
        rows=cursor.fetchall()
        for event in rows:
            if (any(event[k]!=inbox[k] for k in ('tenant_id','config_id','corp_id','open_kfid','actor_id','route_id'))
                    or hashlib.sha256(encoded(event['payload']).encode()).hexdigest()!=event['payload_digest']
                    or event['payload'].get('event',{}).get('event_type')!='user_recall_msg'
                    or event['payload'].get('event',{}).get('recall_msgid')!=inbox['message_id']):
                raise KfIngressError('KF_CONTEXT_RECALL_SCOPE_CONFLICT')
        return bool(rows)

    @classmethod
    def assert_ai_in_tx(cls,cursor,inbox,route):
        row=cls.classification_in_tx(cursor,inbox,route)
        if row and row['classification']!='ai' and not cls.transition_permit_in_tx(cursor,inbox,route):
            raise KfIngressError('KF_CONTEXT_NOT_AI')
        if cls.recalled_in_tx(cursor,inbox,route):
            raise KfIngressError('KF_CONTEXT_RECALLED')

    @staticmethod
    def resolve_event_in_tx(cursor,inbox,route):
        """Resolve classification only; welcome/registration/send remain pending."""
        event=inbox['payload'].get('event') or {}
        event_type=event.get('event_type')
        supported=event_type in {'user_recall_msg','session_status_change','change_type',
                                'enter_session','servicer_status_change','servicer_change'}
        event_state=event.get('session_status')
        if isinstance(event_state,str) and event_state.isascii() and event_state.isdecimal():
            event_state=int(event_state)
        if type(event_state) is not int or event_state not in range(5):
            event_state=None
        status_event=event_type in {'session_status_change','change_type','servicer_status_change'}
        if event_type=='change_type' and event.get('change_type')!='session_status_change':
            supported=False
        resolved=bool(route and supported and (not status_event or event_state is not None))
        if event_type=='user_recall_msg':
            resolved=bool(route and isinstance(event.get('recall_msgid'),str) and event['recall_msgid'])
        if route and status_event and event_state is not None:
            # Provider timestamps are only compared for proven conflicts;
            # receive order is never invented as a cross-namespace causal clock.
            cursor.execute('''SELECT i.send_time,c.event_state FROM wecom_kf_receipt_classifications c
                JOIN wecom_kf_inbox i USING(account_id,namespace,message_id)
                WHERE c.account_id=%s AND c.route_id=%s AND c.event_state IS NOT NULL
                  AND NOT (c.namespace=%s AND c.message_id=%s)
                  AND (i.send_time>%s OR (i.send_time=%s AND c.event_state<>%s)) LIMIT 1''',
                (inbox['account_id'],route['route_id'],inbox['namespace'],inbox['message_id'],
                 inbox['send_time'],inbox['send_time'],event_state))
            if cursor.fetchone(): resolved=False
        scope=route_scope(route) if route else {'tenant_id':inbox['tenant_id'],'config_id':inbox['config_id'],
            'corp_id':inbox['corp_id'],'open_kfid':inbox['open_kfid'],'actor_id':inbox['actor_id'],'route_id':None}
        cursor.execute('''INSERT INTO wecom_kf_receipt_classifications(account_id,namespace,message_id,
            tenant_id,route_id,payload_digest,scope,classification,event_state,classification_resolved,business_pending)
            VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb,'event',%s,%s,TRUE)
            ON CONFLICT(account_id,namespace,message_id) DO NOTHING''',
            (inbox['account_id'],inbox['namespace'],inbox['message_id'],inbox['tenant_id'],inbox['route_id'],
             inbox['payload_digest'],encoded(scope),event_state,resolved))
        cursor.execute('SELECT * FROM wecom_kf_receipt_classifications WHERE account_id=%s AND namespace=%s AND message_id=%s',
                       (inbox['account_id'],inbox['namespace'],inbox['message_id']))
        result=dict(cursor.fetchone())
        if result['scope']!=scope or result['payload_digest']!=inbox['payload_digest'] or result['classification']!='event':
            raise KfIngressError('KF_CONTEXT_CLASSIFICATION_CONFLICT')
        return result
