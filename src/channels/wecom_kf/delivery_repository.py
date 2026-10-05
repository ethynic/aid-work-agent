"""Concrete KF customer-write facts. No Runner reads or execution owner here."""

import hashlib
from dataclasses import dataclass, field

from src.db.database import get_db_connection
from src.services.agent_runner.contracts import RunnerError
from src.services.agent_runner.source_receipts import SourceUnavailable
from .ingress_auth import encoded
from .ingress_repository import KfIngressRepository
from .lifecycle_repository import read_receipt_in_tx, route_scope


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


@dataclass(frozen=True)
class DeliveryOwner:
    delivery_id: str
    owner_id: str
    epoch: int
    locator: object = field(repr=False)


class DeliveryRepository:
    def __init__(self, connection_factory=get_db_connection):
        self.connection_factory = connection_factory

    @staticmethod
    def _binding(cursor, locator):
        current, inbox, route = read_receipt_in_tx(cursor, locator, lock=True)
        if not inbox.get('accepted_input_ref'):
            raise SourceUnavailable('KF_DELIVERY_INPUT_NOT_ACCEPTED')
        return current, inbox, route

    def open(self, locator, observation, owner_id):
        """Capture a complete authoritative presentation on its original route."""
        view = observation['runner']
        presentation = self.presentation(view)
        if view['runner_id'] != observation['current_runner_id']:
            raise RunnerError('SOURCE_SERVICE_INVALID_RESPONSE', 502)
        delivery_id = 'kf_delivery_' + digest([observation['input_ref'], view['runner_id'], presentation])
        with self.connection_factory() as conn:
            cursor = KfIngressRepository._cursor(conn)
            _, inbox, route = self._binding(cursor, locator)
            if (inbox['accepted_input_ref'] != observation['input_ref']
                    or view['session']['session_id'] != route['session_id'] or view['profile_id'] != route['profile_id']):
                raise SourceUnavailable('KF_DELIVERY_BINDING_CHANGED')
            cursor.execute('''INSERT INTO wecom_kf_deliveries(delivery_id,input_ref,runner_id,tenant_id,locator,
                scope,payload_digest,presentation_digest,presentation,view_revision,control_revision)
                VALUES(%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s::jsonb,%s,%s)
                ON CONFLICT(delivery_id) DO NOTHING''',
                (delivery_id, observation['input_ref'], view['runner_id'], inbox['tenant_id'], encoded(locator.value()),
                 encoded(route_scope(route)), inbox['payload_digest'], digest(presentation), encoded(presentation),
                 view['view_revision'], view['control_revision']))
            cursor.execute('SELECT * FROM wecom_kf_deliveries WHERE delivery_id=%s FOR UPDATE', (delivery_id,))
            row = cursor.fetchone()
            self._same(row, inbox, route)
            if row['presentation'] != presentation:
                raise SourceUnavailable('KF_DELIVERY_PRESENTATION_CHANGED')
            operations = self._operations(cursor, row['runner_id'])
            uncertain = [op for op in operations if op['phase'] in {'started','unknown'}]
            unknown_only = bool(uncertain)
            if uncertain and (view['status'] not in {'completed','failed','cancelled'}
                    or any(not self._drained(op, row) for op in uncertain)):
                raise SourceUnavailable('KF_DELIVERY_WRITE_UNKNOWN')
            cursor.execute('''UPDATE wecom_kf_wire_operations w SET phase='suppressed',response_origin='local',
                observed_at=clock_timestamp(),proof=w.proof||%s::jsonb
                FROM wecom_kf_deliveries d WHERE d.delivery_id=w.delivery_id AND d.runner_id=%s
                AND d.delivery_id<>%s AND w.phase='unwritten' AND w.scope=%s::jsonb''',
                (encoded({'superseded_by':delivery_id,'no_customer_post':True}),view['runner_id'],delivery_id,encoded(row['scope'])))
            cursor.execute('SELECT clock_timestamp() AS now')
            now = cursor.fetchone()['now']
            if row['phase'] == 'closed':
                conn.commit()
                return None, dict(row)
            if row['lease_until'] is not None and row['lease_until'] > now and row['owner_id'] != owner_id:
                raise SourceUnavailable('KF_DELIVERY_OWNER_BUSY')
            cursor.execute('''UPDATE wecom_kf_deliveries SET owner_id=%s,claim_epoch=claim_epoch+1,
                lease_until=clock_timestamp()+interval '60 seconds' WHERE delivery_id=%s RETURNING *''',
                (owner_id, delivery_id))
            row = cursor.fetchone()
            conn.commit()
            return DeliveryOwner(delivery_id, owner_id, row['claim_epoch'], locator), {
                **dict(row), 'unknown_only': unknown_only}

    @staticmethod
    def _same(row, inbox, route):
        if (row is None or row['scope'] != route_scope(route) or row['tenant_id'] != inbox['tenant_id']
                or row['input_ref'] != inbox['accepted_input_ref'] or row['payload_digest'] != inbox['payload_digest']):
            raise SourceUnavailable('KF_DELIVERY_BINDING_CHANGED')

    def _owned(self, cursor, owner):
        _, inbox, route = self._binding(cursor, owner.locator)
        cursor.execute('SELECT * FROM wecom_kf_deliveries WHERE delivery_id=%s FOR UPDATE', (owner.delivery_id,))
        row = cursor.fetchone()
        self._same(row, inbox, route)
        cursor.execute('SELECT clock_timestamp() AS now')
        now = cursor.fetchone()['now']
        if (row['phase'] == 'closed' or row['owner_id'] != owner.owner_id or row['claim_epoch'] != owner.epoch
                or row['lease_until'] is None or row['lease_until'] <= now):
            raise SourceUnavailable('KF_DELIVERY_OWNER_CHANGED')
        return row

    @staticmethod
    def question_reply_in_tx(cursor,row,provenance):
        from src.services.agent_runner.control_repository import execution_waits
        waits=[(execution,wait) for execution,wait in execution_waits(row.get('checkpoint'))
               if isinstance(wait,dict) and wait.get('kind')=='clarification']
        if len(waits)!=1:raise RunnerError('SOURCE_QUESTION_REPLY_NOT_AVAILABLE',409)
        target,wait=waits[0]
        # The original chat answer does not depend on a transport ACK. The
        # manager verifies its source binding; ControlRepository verifies this
        # unique current execution/wait and its revision in the same cursor.
        return target,wait['wait_id']

    @staticmethod
    def clarification_wait(waiting):
        """Read the current displayed child wait, never an old question list."""
        while isinstance(waiting, dict):
            if waiting.get('kind') == 'clarification':
                return waiting
            if waiting.get('kind') not in {'child_wait','child_clarification'}:
                break
            waiting = waiting.get('child_wait')
        return None

    @staticmethod
    def _operations(cursor, runner_id):
        cursor.execute('''SELECT w.* FROM wecom_kf_wire_operations w
            JOIN wecom_kf_deliveries d USING(delivery_id)
            WHERE d.runner_id=%s ORDER BY d.created_at,w.ordinal FOR UPDATE OF w''', (runner_id,))
        return cursor.fetchall()

    @staticmethod
    def _drained(operation, row):
        proof = operation['proof']
        return (operation['phase'] == 'unknown' and operation['scope'] == row['scope']
            and operation['tenant_id'] == row['tenant_id']
            and proof.get('transport_drained') is True
            and proof.get('drained_delivery_id') == operation['delivery_id']
            and type(proof.get('drained_epoch')) is int
            and proof['drained_epoch'] == operation['authorized_epoch'])

    def transport_drained(self, owner, operations):
        """Called only after this owner's actual adapter close has returned."""
        if not operations:
            raise SourceUnavailable('KF_DELIVERY_TRANSPORT_NOT_OBSERVED')
        with self.connection_factory() as conn:
            cursor = KfIngressRepository._cursor(conn)
            cursor.execute('SELECT * FROM wecom_kf_deliveries WHERE delivery_id=%s FOR UPDATE', (owner.delivery_id,))
            row = cursor.fetchone()
            if not row or row['owner_id'] != owner.owner_id or row['claim_epoch'] != owner.epoch:
                raise SourceUnavailable('KF_DELIVERY_OWNER_CHANGED')
            for operation in operations:
                cursor.execute('SELECT * FROM wecom_kf_wire_operations WHERE operation_ref=%s FOR UPDATE', (operation['operation_ref'],))
                saved = cursor.fetchone()
                if (not saved or saved['delivery_id'] != owner.delivery_id
                        or saved['authorized_epoch'] != owner.epoch
                        or saved['phase'] != 'unknown'
                        or any(saved[key] != operation[key] for key in
                            ('tenant_id','scope','payload_digest','endpoint','request_digest'))):
                    raise SourceUnavailable('KF_DELIVERY_OPERATION_CHANGED')
                cursor.execute('''UPDATE wecom_kf_wire_operations SET proof=proof||%s::jsonb
                    WHERE operation_ref=%s''', (encoded({'transport_drained':True,
                        'drained_delivery_id':owner.delivery_id,'drained_epoch':owner.epoch}), saved['operation_ref']))
            conn.commit()

    def seal_unknown(self, owner):
        """Freeze the remaining unsent presentation without changing old wire facts."""
        with self.connection_factory() as conn:
            cursor = KfIngressRepository._cursor(conn)
            row = self._owned(cursor, owner)
            if row['presentation'].get('execution_status') not in {'completed','failed','cancelled'}:
                raise SourceUnavailable('KF_DELIVERY_EXECUTION_NOT_TERMINAL')
            operations = self._operations(cursor, row['runner_id'])
            uncertain = [op for op in operations if op['phase'] in {'started','unknown'}]
            if not uncertain or any(not self._drained(op, row) for op in uncertain):
                raise SourceUnavailable('KF_DELIVERY_WRITE_UNKNOWN')
            if any(op['phase']=='unwritten' for op in operations):
                raise SourceUnavailable('KF_DELIVERY_PRESENTATION_UNDECIDED')
            cursor.execute("UPDATE wecom_kf_deliveries SET phase='sealed',sealed_at=clock_timestamp() WHERE delivery_id=%s", (owner.delivery_id,))
            conn.commit()

    @staticmethod
    def _transfer_wire_in_tx(cursor, row):
        cursor.execute("""SELECT w.* FROM wecom_kf_wire_operations w
            WHERE w.proof->>'source_runner_id'=%s AND w.proof->>'input_ref'=%s
            AND w.scope=%s::jsonb AND w.payload_digest=%s
            AND w.purpose LIKE 'tool_transfer_%%'
            AND w.endpoint='/cgi-bin/kf/service_state/trans'""",
            (row['runner_id'], row['input_ref'], encoded(row['scope']), row['payload_digest']))
        operations = cursor.fetchall()
        if any(w['phase'] in {'started', 'unknown'} for w in operations):
            raise SourceUnavailable('KF_DELIVERY_WRITE_UNKNOWN')
        return next((w for w in operations if w['phase']=='ack'
            and w['response_origin']=='platform' and type(w['errcode']) is int and w['errcode']==0
            and type(w['proof'].get('service_state')) is int and w['proof']['service_state']==3
            and w['proof'].get('execution_id') and w['proof'].get('tool_call_id')), None)

    def transferred(self, owner):
        with self.connection_factory() as conn:
            cursor=KfIngressRepository._cursor(conn)
            row=self._owned(cursor, owner)
            return self._transfer_wire_in_tx(cursor, row) is not None

    def start(self, owner, ordinal, endpoint, request_description, *, purpose, fallback_of=None):
        """Commit once immediately before the actual POST; never retry unknown."""
        if type(ordinal) is not int or not 0 <= ordinal < 128:
            raise SourceUnavailable('KF_DELIVERY_ORDINAL_INVALID')
        request_digest = digest(request_description)
        operation_ref = 'kf_wire_' + digest([owner.delivery_id, ordinal])
        with self.connection_factory() as conn:
            cursor = KfIngressRepository._cursor(conn)
            row = self._owned(cursor, owner)
            if self._transfer_wire_in_tx(cursor,row) is not None and not purpose.startswith('suppression:'):
                raise SourceUnavailable('KF_DELIVERY_TRANSFERRED_NO_DISPATCH')
            cursor.execute('SELECT * FROM wecom_kf_wire_operations WHERE operation_ref=%s FOR UPDATE', (operation_ref,))
            existing = cursor.fetchone()
            cursor.execute('''SELECT 1 FROM wecom_kf_wire_operations w JOIN wecom_kf_deliveries d USING(delivery_id)
                WHERE d.runner_id=%s AND w.phase IN ('started','unknown') LIMIT 1''',(row['runner_id'],))
            if cursor.fetchone():raise SourceUnavailable('KF_DELIVERY_WRITE_UNKNOWN')
            if existing:
                if (existing['request_digest'] != request_digest or existing['endpoint'] != endpoint
                        or existing['purpose'] != purpose or existing['scope'] != row['scope']):
                    raise SourceUnavailable('KF_DELIVERY_OPERATION_CHANGED')
                if existing['phase'] in {'started', 'unknown'}:
                    raise SourceUnavailable('KF_DELIVERY_WRITE_UNKNOWN')
                if existing['phase'] in {'ack', 'reject', 'suppressed'}:
                    conn.commit()
                    return dict(existing)
                # Only a proven not-called operation may transfer to this epoch.
                cursor.execute('''UPDATE wecom_kf_wire_operations SET phase='started',authorized_epoch=%s,
                    started_at=clock_timestamp(),observed_at=NULL WHERE operation_ref=%s RETURNING *''',
                    (owner.epoch, operation_ref))
            else:
                cursor.execute('''SELECT w.* FROM wecom_kf_wire_operations w JOIN wecom_kf_deliveries d USING(delivery_id)
                    WHERE d.runner_id=%s AND w.scope=%s::jsonb AND w.endpoint=%s
                    AND w.proof->>'representation_id'=%s AND w.proof->>'representation_digest'=%s
                    AND w.purpose=%s AND w.phase IN ('ack','reject') ORDER BY w.observed_at LIMIT 1''',
                    (row['runner_id'],encoded(row['scope']),endpoint,request_description.get('representation_id'),
                     request_description.get('representation_digest'),purpose))
                prior=cursor.fetchone()
                if prior:
                    cursor.execute('''INSERT INTO wecom_kf_wire_operations(operation_ref,delivery_id,tenant_id,locator,
                        scope,payload_digest,ordinal,purpose,endpoint,request_digest,phase,authorized_epoch,started_at,
                        observed_at,response_origin,errcode,result,proof) VALUES(%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s,
                        %s,%s,clock_timestamp(),%s,'platform',%s,%s::jsonb,%s::jsonb) RETURNING *''',
                        (operation_ref,owner.delivery_id,row['tenant_id'],encoded(row['locator']),encoded(row['scope']),
                         row['payload_digest'],ordinal,purpose,endpoint,request_digest,prior['phase'],owner.epoch,
                         prior['observed_at'],prior['errcode'],encoded(prior['result']),encoded({'replay_of':prior['operation_ref'],'representation_id':request_description.get('representation_id'),
                             'representation_digest':request_description.get('representation_digest')})))
                    saved=dict(cursor.fetchone());conn.commit();return saved
                if purpose.startswith('suppression:'):
                    return self._suppressed(cursor,conn,owner,row,operation_ref,ordinal,endpoint,request_digest,purpose,purpose[12:])
                if row['phase'] != 'open':
                    raise SourceUnavailable('KF_DELIVERY_ALREADY_SEALED')
                if purpose in {'body', 'asset', 'verbose', 'question', 'fallback'}:
                    # Actual ACKs share the original consultation owner across
                    # presentations; platform reject does not consume a reply.
                    cursor.execute('''SELECT count(*) AS used FROM wecom_kf_wire_operations w
                        JOIN wecom_kf_deliveries d USING(delivery_id)
                        WHERE d.runner_id=%s AND w.phase IN ('ack','started','unknown') AND NOT COALESCE(w.proof ? 'replay_of',FALSE)
                        AND w.purpose IN ('body','asset','verbose','question','fallback')''', (row['runner_id'],))
                    if cursor.fetchone()['used'] >= (4 if purpose=='verbose' else 5):
                        return self._suppressed(cursor,conn,owner,row,operation_ref,ordinal,endpoint,request_digest,purpose,'reply_budget')
                cursor.execute('''INSERT INTO wecom_kf_wire_operations(operation_ref,delivery_id,tenant_id,
                    locator,scope,payload_digest,ordinal,purpose,endpoint,request_digest,fallback_of,
                    phase,authorized_epoch,started_at,proof) VALUES(%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s,%s,
                    'started',%s,clock_timestamp(),%s::jsonb) RETURNING *''',
                    (operation_ref, owner.delivery_id, row['tenant_id'], encoded(row['locator']), encoded(row['scope']),
                     row['payload_digest'], ordinal, purpose, endpoint, request_digest, fallback_of, owner.epoch,
                     encoded({'representation_id':request_description.get('representation_id'),
                              'representation_digest':request_description.get('representation_digest')})))
            operation = dict(cursor.fetchone())
            self._owned(cursor, owner)
            conn.commit()
            return operation

    @staticmethod
    def presentation(view):
        snapshot=view.get('snapshot') or {}
        result=view.get('result') or {}
        return {'output':result.get('output',snapshot.get('output','')),
            'images':result.get('images',snapshot.get('images',[])),
            'downloadableFiles':snapshot.get('downloadableFiles',[]),
            'verboseMessages':snapshot.get('verboseMessages',[]),
            'waiting':snapshot.get('waiting'),'clarificationQuestions':snapshot.get('clarificationQuestions',[]),
            'execution_status':view['status']}

    @staticmethod
    def _suppressed(cursor,conn,owner,row,ref,ordinal,endpoint,request_digest,purpose,reason):
        cursor.execute('''INSERT INTO wecom_kf_wire_operations(operation_ref,delivery_id,tenant_id,
            locator,scope,payload_digest,ordinal,purpose,endpoint,request_digest,phase,authorized_epoch,
            started_at,observed_at,response_origin,proof) VALUES(%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s,
            'suppressed',%s,clock_timestamp(),clock_timestamp(),'local',%s::jsonb) RETURNING *''',
            (ref,owner.delivery_id,row['tenant_id'],encoded(row['locator']),encoded(row['scope']),row['payload_digest'],
             ordinal,purpose,endpoint,request_digest,owner.epoch,encoded({'reason':reason,'no_customer_post':True,
                'presentation_digest':row['presentation_digest']})))
        saved=dict(cursor.fetchone());conn.commit();return saved

    def suppress(self,owner,ordinal,kind,description):
        # SDK v2 asset local facts are accepted only for captured stable assets.
        if kind=='image_ref':
            with self.connection_factory() as conn:
                cursor=KfIngressRepository._cursor(conn);row=self._owned(cursor,owner)
                assets=row['presentation'].get('images') or []
                if not any(digest(asset)==description.get('asset_digest')
                    and (asset.get('file_id') or '')==description.get('file_id','') for asset in assets):
                    raise SourceUnavailable('KF_DELIVERY_SUPPRESSION_CHANGED')
        if kind=='transferred':
            with self.connection_factory() as conn:
                cursor=KfIngressRepository._cursor(conn);row=self._owned(cursor,owner)
                if (description.get('presentation_digest')!=row['presentation_digest']
                        or self._transfer_wire_in_tx(cursor,row) is None):
                    raise SourceUnavailable('KF_DELIVERY_TRANSFER_CHANGED')
        return self.start(owner,ordinal,'local:suppressed',description,purpose='suppression:'+kind)

    def budget_remaining(self,owner):
        with self.connection_factory() as conn:
            cursor=KfIngressRepository._cursor(conn);row=self._owned(cursor,owner)
            cursor.execute('''SELECT count(*) AS used FROM wecom_kf_wire_operations w
                JOIN wecom_kf_deliveries d USING(delivery_id) WHERE d.runner_id=%s
                AND w.phase IN ('ack','started','unknown') AND NOT COALESCE(w.proof ? 'replay_of',FALSE)
                AND w.purpose IN ('body','asset','verbose','question','fallback')''',(row['runner_id'],))
            return max(0,5-cursor.fetchone()['used'])

    def assert_dispatch(self,owner,operation):
        with self.connection_factory() as conn:
            cursor=KfIngressRepository._cursor(conn);self._owned(cursor,owner)
            cursor.execute('SELECT * FROM wecom_kf_wire_operations WHERE operation_ref=%s FOR UPDATE',(operation['operation_ref'],))
            saved=cursor.fetchone()
            if (not saved or saved['phase']!='started' or saved['authorized_epoch']!=owner.epoch
                    or saved['request_digest']!=operation['request_digest']):
                raise SourceUnavailable('KF_DELIVERY_OWNER_CHANGED')
            self._owned(cursor,owner)

    def seal(self,owner,count):
        if type(count) is not int or not 0<=count<=128: raise SourceUnavailable('KF_DELIVERY_ORDINAL_INVALID')
        with self.connection_factory() as conn:
            cursor=KfIngressRepository._cursor(conn);row=self._owned(cursor,owner)
            cursor.execute('SELECT * FROM wecom_kf_wire_operations WHERE delivery_id=%s ORDER BY ordinal FOR UPDATE',(owner.delivery_id,))
            operations=cursor.fetchall()
            if len(operations)!=count or [r['ordinal'] for r in operations]!=list(range(count)):
                raise SourceUnavailable('KF_DELIVERY_PRESENTATION_UNDECIDED')
            if not operations or any(r['phase'] not in {'ack','reject','suppressed','unwritten'} for r in operations):
                raise SourceUnavailable('KF_DELIVERY_WRITE_UNKNOWN')
            if any(r['phase']=='unwritten' for r in operations):
                raise SourceUnavailable('KF_DELIVERY_PRESENTATION_UNDECIDED')
            cursor.execute("UPDATE wecom_kf_deliveries SET phase='sealed',sealed_at=clock_timestamp() WHERE delivery_id=%s",(owner.delivery_id,))
            conn.commit()

    @classmethod
    def finish_in_tx(cls,cursor,locator,fact,runner,delivery_id,*,recap_tasks=()):
        if runner['status'] not in {'completed','failed','cancelled'} or runner.get('resume_control_id'):
            raise SourceUnavailable('KF_DELIVERY_EXECUTION_NOT_TERMINAL')
        _,inbox,route=cls._binding(cursor,locator)
        cursor.execute('SELECT * FROM wecom_kf_deliveries WHERE delivery_id=%s FOR UPDATE',(delivery_id,))
        row=cursor.fetchone();cls._same(row,inbox,route)
        from src.services.agent_runner.public_view import public_runner
        presentation=cls.presentation(public_runner(runner))
        if (row['runner_id']!=runner['runner_id'] or fact['current_runner_id']!=runner['runner_id']
                or row['phase'] not in {'sealed','closed'} or row['presentation_digest']!=digest(presentation)
                or row['presentation']!=presentation or row['view_revision']>runner['view_revision']
                or row['control_revision']!=runner['control_revision']):
            raise SourceUnavailable('KF_DELIVERY_PRESENTATION_CHANGED')
        operations=cls._operations(cursor,runner['runner_id'])
        if not operations or any(op['phase'] in {'started','unwritten'} for op in operations):
            raise SourceUnavailable('KF_DELIVERY_WRITE_UNKNOWN')
        if row['phase']=='closed': return row['closed_outcome']
        uncertain=[op for op in operations if op['phase']=='unknown']
        if uncertain:
            if any(not cls._drained(op,row) for op in uncertain):
                raise SourceUnavailable('KF_DELIVERY_WRITE_UNKNOWN')
            # An ended but unacknowledged send is not a successful delivery.
            # Retain every old operation and forego unsent representations;
            # only the service's existing terminal claim transaction may close.
            cursor.execute("UPDATE wecom_kf_deliveries SET phase='closed',closed_at=clock_timestamp(),closed_outcome='closed_unknown',lease_until=NULL WHERE delivery_id=%s",(delivery_id,))
            return 'closed_unknown'
        final=[op for op in operations if op['delivery_id']==delivery_id]
        from .completion_business import BusinessRepository
        transfer=BusinessRepository.transfer_fact_in_tx(cursor,runner,inbox,route)
        transfer_completed=False
        def completed_transfer(node):
            if transfer is None or not isinstance(node,dict):return False
            tool=(node.get('tools') or {}).get(transfer['tool_call_id'])
            if (node.get('execution_id')==transfer['execution_id'] and tool
                    and tool.get('phase')=='completed' and tool.get('terminal_directive')=='stop_execution'):
                return True
            return any(completed_transfer(c.get('checkpoint')) for c in (node.get('children') or {}).values())
        transfer_completed=completed_transfer((runner.get('checkpoint') or {}).get('execution'))
        # The final customer response may be body-only, assets-only, or both.
        # A real later fallback ACK covers an earlier reject of that same
        # captured logical representation; verbose ACKs never prove success.
        representations={}
        for op in final:
            if op['purpose'] in {'body','asset'}:
                key=(op['purpose'],op['proof'].get('representation_digest'))
                if not key[1]:raise SourceUnavailable('KF_DELIVERY_REPRESENTATION_CHANGED')
                representations[key]=op
        complete_response=bool(representations) and all(op['phase']=='ack' for op in representations.values())
        complete_response=complete_response and not any(op['purpose'] in {'suppression:image_ref',
            'suppression:remaining_presentation','suppression:known_local_preparation'} for op in final)
        if complete_response:outcome='closed_accepted_known'
        elif all(op['phase']=='suppressed' for op in operations):outcome='closed_suppressed_known'
        else:outcome='closed_failed_known'
        recap_eligible=complete_response or transfer_completed
        if recap_eligible and runner['status']=='completed':
            cursor.execute('SELECT history_group_ref FROM agent_runner_input_batch_members WHERE input_ref=%s',(fact['input_ref'],))
            group=cursor.fetchone()
            history_id=group['history_group_ref'] if group else fact['input_ref']+':user'
            cursor.execute('SELECT metadata FROM channel_messages WHERE tenant_id=%s AND session_id=%s AND message_id=%s',
                (route['tenant_id'],route['session_id'],history_id))
            history=cursor.fetchone()
            if not history:
                cursor.execute('''SELECT b.value,i.payload FROM wecom_kf_business_facts b
                    JOIN wecom_kf_inbox i USING(account_id,namespace,message_id)
                    WHERE b.tenant_id=%s AND b.scope->>'session_id'=%s
                      AND b.business_kind='hidden_command' AND b.phase='known'
                      AND b.payload_digest=i.payload_digest AND b.route_id=i.route_id''',
                    (route['tenant_id'],route['session_id']))
                from src.core.hidden_commands import is_hidden_command
                cleared=any(is_hidden_command((item['payload'].get('text') or {}).get('content',''))
                    for item in cursor.fetchall())
                if not cleared:raise SourceUnavailable('KF_DELIVERY_HISTORY_CHANGED')
                recap_tasks=()
            elif fact['input_ref'] not in ((history['metadata'] or {}).get('input_refs') or [(history['metadata'] or {}).get('input_ref')]):
                raise SourceUnavailable('KF_DELIVERY_HISTORY_CHANGED')
            for name in recap_tasks:
                if name not in {'lead_refresh','external_push','external_push_human'}:continue
                task_id='kf_context_task_'+digest([route_scope(route),inbox['account_id'],inbox['namespace'],inbox['message_id'],name,1])
                cursor.execute('''INSERT INTO wecom_kf_context_task_intents(task_id,account_id,namespace,message_id,
                    tenant_id,route_id,session_id,history_id,task_name,operation_version,scope,state)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,1,%s::jsonb,'pending_adapter') ON CONFLICT(task_id) DO NOTHING''',
                    (task_id,inbox['account_id'],inbox['namespace'],inbox['message_id'],route['tenant_id'],route['route_id'],
                     route['session_id'],history_id,name,encoded(route_scope(route))))
        cursor.execute("UPDATE wecom_kf_deliveries SET phase='closed',closed_at=clock_timestamp(),closed_outcome=%s,lease_until=NULL WHERE delivery_id=%s",(outcome,delivery_id))
        return outcome

    def observe(self, operation, *, result=None, unknown_code=None, unwritten_code=None):
        """Store an actual old response under its original durable authorization.

        This intentionally does not reacquire new dispatch authority or reject an
        actual response because configuration changed after the customer POST.
        """
        ref = operation['operation_ref']
        if result is not None:
            if result.get('response_origin')!='platform': raise SourceUnavailable('KF_DELIVERY_REPLY_INVALID')
            if (not isinstance(result, dict) or type(result.get('errcode')) is not int
                    or not -(2**63) <= result['errcode'] < 2**63):
                raise SourceUnavailable('KF_DELIVERY_REPLY_INVALID')
            phase = 'ack' if result['errcode'] == 0 else 'reject'
            safe = {key: result[key] for key in ('errcode','media_id','msgid','response_origin') if key in result}
            if any(key != 'errcode' and (not isinstance(value, str) or len(value.encode()) > 4096)
                   for key, value in safe.items()):
                raise SourceUnavailable('KF_DELIVERY_REPLY_INVALID')
        else:
            phase, safe = ('unwritten' if unwritten_code else 'unknown'), None
        with self.connection_factory() as conn:
            cursor = KfIngressRepository._cursor(conn)
            cursor.execute('SELECT * FROM wecom_kf_wire_operations WHERE operation_ref=%s FOR UPDATE', (ref,))
            saved = cursor.fetchone()
            if (saved is None or any(saved[key] != operation[key] for key in
                    ('tenant_id','scope','payload_digest','endpoint','request_digest','authorized_epoch'))):
                raise SourceUnavailable('KF_DELIVERY_OPERATION_CHANGED')
            if saved['phase'] in {'ack','reject'}:
                if saved['phase'] != phase or saved['result'] != safe:
                    raise SourceUnavailable('KF_DELIVERY_RESULT_CONFLICT')
                return dict(saved)
            if saved['phase'] not in {'started','unknown'} or (phase == 'unwritten' and saved['phase'] != 'started'):
                raise SourceUnavailable('KF_DELIVERY_OPERATION_CHANGED')
            cursor.execute('''UPDATE wecom_kf_wire_operations SET phase=%s,observed_at=clock_timestamp(),
                response_origin=%s,errcode=%s,result=%s::jsonb WHERE operation_ref=%s RETURNING *''',
                (phase, 'platform' if result is not None else 'local', result['errcode'] if result is not None else None,
                 encoded(safe) if safe is not None else None, ref))
            saved = dict(cursor.fetchone())
            conn.commit()
            return saved
