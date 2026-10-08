"""Cursor-only accepted inputs, checkpoint application and final cutoff."""

import copy
import hashlib
import json

from src.db.database import get_db_connection
from .contracts import RunnerError, RunnerSubmit, Principal
from .source_receipts import SourceLocator, SourceUnavailable
from .ownership import LeaseLost
from .public_view import decoded


def input_message(fact, projection):
    return {'role':'user','content':projection['model_text'],
            'metadata':{'input_ref':fact['input_ref'],'submitted_text':fact['intent']['text'],
                        'accepted_at':fact['accepted_at'].isoformat(),
                        'preparation_ref':projection.get('preparation_ref')}}


def source_message_refs(message):
    metadata = message.get('metadata') or {}
    ref = metadata.get('input_ref')
    refs = metadata.get('input_refs')
    if refs is None:
        return (ref,) if ref else ()
    if (not isinstance(refs, list) or not 1 <= len(refs) <= 32 or refs[0] != ref
            or any(not isinstance(item, str) for item in refs) or len(set(refs)) != len(refs)):
        raise SourceUnavailable('SOURCE_INPUT_APPLICATION_UNKNOWN')
    return tuple(refs)


def source_group_in_tx(cursor, fact, projector):
    """Derive the exact immutable group; no source/platform SQL here."""
    cursor.execute('SELECT * FROM agent_runner_input_batch_members WHERE input_ref=%s', (fact['input_ref'],))
    membership = cursor.fetchone()
    if membership is None:
        projection = projector(cursor, fact)
        return [fact], projection
    cursor.execute('''SELECT i.*,m.history_group_ref,m.ordinal AS batch_ordinal FROM agent_runner_input_batch_members m
        JOIN agent_runner_inputs i USING(input_ref) WHERE m.batch_ref=%s ORDER BY m.ordinal''',
        (membership['batch_ref'],))
    facts = cursor.fetchall()
    if (not facts or len(facts) > 32 or facts[0]['input_ref'] != fact['input_ref']
            or any(member['batch_ordinal'] != ordinal
                   or member['current_runner_id'] != fact['current_runner_id']
                   or member['provenance']['execution_binding'] != fact['provenance']['execution_binding']
                   for ordinal, member in enumerate(facts))):
        raise SourceUnavailable('SOURCE_BATCH_BINDING_CHANGED')
    projections = [projector(cursor, member) for member in facts]
    if any(projection is None for projection in projections):
        return facts, None
    if any(projection.get('preparation_ref') or projection.get('attachments') for projection in projections):
        # Paid/media preparation is always a single-member barrier.
        raise SourceUnavailable('SOURCE_BATCH_UNSUPPORTED_PREPARATION')
    segments = [{'input_ref': member['input_ref'], 'message_id': member['locator']['message_id'],
                 'text': projection['history_text'], 'accepted_at': member['accepted_at'].isoformat()}
                for member, projection in zip(facts, projections)]
    return facts, {'model_text': '\n'.join(projection['model_text'] for projection in projections),
                   'history_text': '\n'.join(projection['history_text'] for projection in projections),
                   'attachments': [], 'preparation_ref': None, 'batch_ref': membership['batch_ref'],
                   'history_group_ref': membership['history_group_ref'],
                   'input_refs': [member['input_ref'] for member in facts], 'merged_segments': segments}


def group_input_message(facts, projection):
    message = input_message(facts[0], projection)
    if projection.get('batch_ref'):
        message['metadata'].update({key: copy.deepcopy(projection[key]) for key in
            ('batch_ref', 'history_group_ref', 'input_refs', 'merged_segments')})
    return message


class InputRepository:
    def __init__(self, connection_factory=get_db_connection):
        self.connection_factory=connection_factory

    @staticmethod
    def source_key(locator):
        return locator.stable_key

    @staticmethod
    def response(fact, created=False):
        return {'success':True,'input_ref':fact['input_ref'],
            'accepted_runner_id':fact['accepted_runner_id'],'current_runner_id':fact['current_runner_id'],
            'disposition':fact['phase'],'created':created}

    def find_in_tx(self,cursor,locator):
        cursor.execute('SELECT * FROM agent_runner_inputs WHERE source_key=%s',(self.source_key(locator),))
        return cursor.fetchone()

    @staticmethod
    def lock_claim(cursor,row):
        cursor.execute('''SELECT * FROM agent_runner_session_claims WHERE scope_key=%s
            AND session_kind=%s AND session_id=%s FOR UPDATE''',
            (row['scope_key'],row['session_kind'],row['session_id']))
        claim=cursor.fetchone()
        if not claim or claim['owner_runner_id']!=row['runner_id']:
            raise LeaseLost('SOURCE_INPUT_CLAIM_CHANGED')
        return claim

    def accept_in_tx(self,cursor,locator,request,provenance,receipt_seq,principal,
                     repository,fingerprint,execution_context,owner=None,source_control_id=None):
        existing=self.find_in_tx(cursor,locator)
        if existing:
            if existing['provenance']!=provenance or existing['intent_digest']!=request.digest():
                raise RunnerError('SOURCE_RECEIPT_CONFLICT',409)
            return self.response(existing)
        # source 入口字节闸：与 SOURCE_INPUT_BACKLOG 计数闸并行的新单输入背压。
        from .persistence_limits import assert_intent_within_request_limit
        assert_intent_within_request_limit(request.intent(), code='REQUEST_TOO_LARGE')
        # Serializes only admission for this actual conversation. Acquiring a
        # normal Runner takes its root then claim; no claim->root upgrade here.
        scope=[principal.scope_key,principal.identity.session_id]
        cursor.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
            (json.dumps(['source-admission',*scope]),))
        if owner is not None:
            claim=self.lock_claim(cursor,owner)
            cursor.execute('SELECT provenance FROM agent_runner_inputs WHERE input_ref=%s',
                ((owner.get('checkpoint') or {}).get('source_initial_ref'),))
            original=cursor.fetchone()
            same_binding=(original is not None and
                original['provenance'].get('execution_binding')==provenance.get('execution_binding'))
            if not same_binding:
                owner=None
        if owner is not None:
            if owner['status'] in {'waiting','paused','interrupted'} and source_control_id is None:
                raise RunnerError('SOURCE_WAITING_INPUT_NOT_SUPPORTED',409)
            if source_control_id is None and (claim['gate']!='execution' or owner['status']!='running'
                    or owner['cancel_requested'] or owner.get('pause_requested')
                    or (owner.get('checkpoint') or {}).get('source_input_gate')=='closed'
                    or not (owner.get('checkpoint') or {}).get('source_initial_ref')
                    or owner['profile_id']!=request.profile_id):
                owner=None
        if owner is None:
            checkpoint={'source_initial_ref':locator.stable_key,'source_input_gate':'open',
                        'execution_context':execution_context}
            owner,_=repository.submit_in_tx(cursor,principal,request,fingerprint,
                resolved_profile_id=request.profile_id,execution_context=execution_context,
                initial_checkpoint=checkpoint)
        cursor.execute("SELECT count(*) AS count FROM agent_runner_inputs WHERE current_runner_id=%s AND phase IN ('accepted','attached','deferred')",(owner['runner_id'],))
        if cursor.fetchone()['count'] >= 128:
            raise RunnerError('SOURCE_INPUT_BACKLOG',409)
        cursor.execute('''INSERT INTO agent_runner_inputs(input_ref,source,source_key,locator,provenance,
            intent,intent_digest,receipt_seq,accepted_runner_id,current_runner_id,phase)
            VALUES(%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s,%s,%s,'accepted')
            ON CONFLICT(source_key) DO NOTHING RETURNING *''',
            (locator.stable_key,locator.source,self.source_key(locator),json.dumps(locator.value()),
             json.dumps(provenance),json.dumps(request.intent(),ensure_ascii=False),request.digest(),
             receipt_seq,owner['runner_id'],owner['runner_id']))
        fact=cursor.fetchone()
        if fact is None:
            fact=self.find_in_tx(cursor,locator)
            if fact['provenance']!=provenance or fact['intent_digest']!=request.digest():
                raise RunnerError('SOURCE_RECEIPT_CONFLICT',409)
            return self.response(fact)
        if source_control_id is not None:
            cursor.execute('UPDATE agent_runner_inputs SET source_control_id=%s WHERE input_ref=%s AND source_control_id IS NULL',(source_control_id,fact['input_ref']))
        return self.response(fact,True)

    def accept_batch_in_tx(self, cursor, batch, values, principal, repository,
                           fingerprint, execution_context, owner=None,source_control_id=None):
        """Accept every immutable member on one root in the same transaction."""
        if len(values) != len(batch.members):
            raise RunnerError('INVALID_SOURCE_BATCH', 422)
        binding = values[0][1]['execution_binding']
        if any(proof['execution_binding'] != binding for _, proof, _ in values):
            raise RunnerError('SOURCE_BATCH_BINDING_CHANGED', 409)
        existing = [self.find_in_tx(cursor, locator) for locator in batch.members]
        if any(existing):
            if not all(existing):
                raise RunnerError('SOURCE_BATCH_CONFLICT', 409)
            result = [self.response(fact) for fact in existing]
            self._assert_batch_members_in_tx(cursor, batch, existing)
            return {'success': True, 'batch_ref': batch.stable_key,
                    'current_runner_id': result[0]['current_runner_id'], 'members': result, 'created': False}
        request, provenance, seq = values[0]
        first = self.accept_in_tx(cursor, batch.members[0], request, provenance, seq,
                                  principal, repository, fingerprint, execution_context, owner,source_control_id)
        runner_id = first['current_runner_id']
        results = [first]
        from .persistence_limits import assert_intent_within_request_limit
        for locator, (request, provenance, seq) in zip(batch.members[1:], values[1:]):
            # 首成员已过 accept_in_tx 字节闸；其余成员同闸，避免批量旁路。
            assert_intent_within_request_limit(request.intent(), code='REQUEST_TOO_LARGE')
            cursor.execute('''INSERT INTO agent_runner_inputs(input_ref,source,source_key,locator,provenance,
                intent,intent_digest,receipt_seq,accepted_runner_id,current_runner_id,phase)
                VALUES(%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s,%s,%s,'accepted') RETURNING *''',
                (locator.stable_key, locator.source, locator.stable_key, json.dumps(locator.value()),
                 json.dumps(provenance), json.dumps(request.intent(), ensure_ascii=False), request.digest(),
                 seq, runner_id, runner_id))
            results.append(self.response(cursor.fetchone(), True))
        digest = hashlib.sha256(json.dumps(batch.value(), sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        history_ref = results[0]['input_ref'] + ':user' if len(results) == 1 else batch.stable_key + ':user'
        for ordinal, result in enumerate(results):
            cursor.execute('''INSERT INTO agent_runner_input_batch_members(input_ref,batch_ref,ordinal,
                members_digest,history_group_ref) VALUES(%s,%s,%s,%s,%s)''',
                (result['input_ref'], batch.stable_key, ordinal, digest, history_ref))
        return {'success': True, 'batch_ref': batch.stable_key, 'current_runner_id': runner_id,
                'members': results, 'created': True}

    @staticmethod
    def _assert_batch_members_in_tx(cursor, batch, facts):
        cursor.execute('''SELECT * FROM agent_runner_input_batch_members WHERE batch_ref=%s ORDER BY ordinal''',
                       (batch.stable_key,))
        stored = cursor.fetchall()
        if (len(stored) != len(facts) or len({fact['current_runner_id'] for fact in facts}) != 1
                or any(row['ordinal'] != ordinal or row['input_ref'] != fact['input_ref']
                       for ordinal, (row, fact) in enumerate(zip(stored, facts)))):
            raise RunnerError('SOURCE_BATCH_CONFLICT', 409)

    @staticmethod
    def supersede_reply_in_tx(cursor,row,checkpoint,marker):
        """A real later pause withdraws only the original unused reply group."""
        cursor.execute('SELECT * FROM agent_runner_controls WHERE runner_id=%s AND control_id=%s FOR UPDATE',
                       (row['runner_id'],marker['control_id']))
        control=cursor.fetchone()
        if not control or control['status']!='rejected' or control['error_code']!='RESUME_SUPERSEDED_BY_PAUSE':
            raise SourceUnavailable('SOURCE_REPLY_PREPARATION_CHANGED')
        cursor.execute('SELECT * FROM agent_runner_inputs WHERE source_control_id=%s FOR UPDATE',(marker['control_id'],))
        anchor=cursor.fetchone()
        if (not anchor or anchor['current_runner_id']!=row['runner_id'] or anchor['input_ref']!=marker['input_ref']):
            raise SourceUnavailable('SOURCE_REPLY_PREPARATION_CHANGED')
        from .application_sources import project_input_in_tx
        members,_=source_group_in_tx(cursor,anchor,project_input_in_tx)
        refs={fact['input_ref'] for fact in members if fact['phase'] in {'accepted','attached','deferred'}}
        nodes=[]
        def visit(node):
            if not isinstance(node,dict):return
            nodes.append(node)
            for child in node.get('children',{}).values():visit(child.get('checkpoint'))
        visit(checkpoint.get('execution'))
        used={ref for node in nodes for message in node.get('messages',[]) for ref in source_message_refs(message)}
        refs-=used
        for node in nodes:
            node['followup_messages']=[message for message in node.get('followup_messages',[])
                if not refs.intersection(source_message_refs(message))]
            for ref in refs:node.get('resources',{}).get('continuation_inputs',{}).pop(ref,None)
        if refs:
            cursor.execute("""UPDATE agent_runner_inputs SET phase='cancelled'
                WHERE current_runner_id=%s AND input_ref=ANY(%s) AND phase IN ('accepted','attached','deferred')""",
                (row['runner_id'],sorted(refs)))
        root=InputRepository._root(checkpoint)
        if root:InputRepository._sync_continuation_intent(checkpoint,root)
        checkpoint.pop('source_reply_preparation',None)
        return checkpoint

    @staticmethod
    def _root(checkpoint):
        state=checkpoint.get('execution')
        if not isinstance(state,dict): return None
        if (type(state.get('iteration')) is not int or state['iteration']<0
                or type(state.get('max_iterations')) is not int or state['max_iterations']<1):
            raise SourceUnavailable('SOURCE_INPUT_CHECKPOINT_INVALID')
        for name in ('messages','followup_messages'):
            if not isinstance(state.get(name),list): raise SourceUnavailable('SOURCE_INPUT_CHECKPOINT_INVALID')
        if not isinstance(state.get('resources'),dict): raise SourceUnavailable('SOURCE_INPUT_CHECKPOINT_INVALID')
        for message in state['messages']+state['followup_messages']:
            if not isinstance(message,dict) or not isinstance(message.get('metadata') or {},dict):
                raise SourceUnavailable('SOURCE_INPUT_CHECKPOINT_INVALID')
        return state

    @staticmethod
    def _sync_continuation_intent(checkpoint,state):
        """Own only a queued source request to leave the completed-parent mirror.

        A generic resume may install its own flag later. Its applied-control
        identity transfers ownership, so source cleanup must leave that flag.
        """
        resources=state['resources']
        queued=sorted({(m.get('metadata') or {}).get('input_ref')
                       for m in state['followup_messages']
                       if (m.get('metadata') or {}).get('input_ref') and not (m.get('metadata') or {}).get('control_id')})
        needed=(bool(queued) and resources.get('root_execution_outcome')=='completed'
                and state.get('outcome') in {'running','waiting','paused','completed'})
        owner=resources.get('source_continuation_intent')
        control_id=checkpoint.get('applied_control_id')
        if owner is not None:
            if (not isinstance(owner,dict) or set(owner)!={'input_refs','applied_control_id'}
                    or not isinstance(owner['input_refs'],list)
                    or any(not isinstance(ref,str) for ref in owner['input_refs'])):
                raise SourceUnavailable('SOURCE_INPUT_CHECKPOINT_INVALID')
            if owner['applied_control_id']!=control_id:
                generic_input=(control_id is not None
                    and resources.get('continuation_inputs',{}).get(control_id) in {'queued','appended'}
                    and any(m.get('role')=='user'
                    and (m.get('metadata') or {}).get('control_id')==control_id
                    for m in state['messages']+state['followup_messages']))
                if generic_input:
                    # The root's resume-input projection proves its own input;
                    # a mirrored child reply/empty resume does not transfer it.
                    resources.pop('source_continuation_intent',None)
                    return
                owner['applied_control_id']=control_id
            if not needed or resources.get('root_continuation_requested') is not True:
                resources.pop('root_continuation_requested',None)
                resources.pop('source_continuation_intent',None)
                return
            owner['input_refs']=queued
        elif needed and resources.get('root_continuation_requested') is not True:
            resources['root_continuation_requested']=True
            resources['source_continuation_intent']={
                'input_refs':queued,'applied_control_id':control_id}

    def merge_in_tx(self,cursor,row,checkpoint,*,before_model=False):
        """Attach and acknowledge only references in the actual CAS checkpoint."""
        checkpoint=copy.deepcopy(checkpoint)
        if not (row.get('checkpoint') or {}).get('source_initial_ref'): return checkpoint
        self.lock_claim(cursor,row)
        state=self._root(checkpoint)
        if state is None: return checkpoint
        cursor.execute('''SELECT * FROM agent_runner_inputs WHERE current_runner_id=%s
            ORDER BY ordinal FOR UPDATE''',(row['runner_id'],))
        facts=cursor.fetchall()
        by_ref={fact['input_ref']:fact for fact in facts}
        controlled=set()
        for fact in facts:
            if fact.get('source_control_id'):
                from .application_sources import project_input_in_tx
                members,_=source_group_in_tx(cursor,fact,project_input_in_tx)
                controlled.update(member['input_ref'] for member in members)
        nodes=[]
        def visit(node):
            if not isinstance(node,dict):return
            nodes.append(node)
            for child in (node.get('children') or {}).values():visit(child.get('checkpoint'))
        visit(state)
        all_messages=[message for node in nodes for message in node['messages']+node['followup_messages']]
        for message in all_messages:
            metadata=message.get('metadata') or {}
            ref=metadata.get('input_ref')
            if ref and (ref not in by_ref or message.get('role')!='user'
                        or metadata.get('submitted_text')!=by_ref[ref]['intent']['text']):
                raise SourceUnavailable('SOURCE_INPUT_APPLICATION_UNKNOWN')
            refs=source_message_refs(message)
            if len(refs)>1 or metadata.get('batch_ref'):
                from .application_sources import project_input_in_tx
                members,projection=source_group_in_tx(cursor,by_ref[ref],project_input_in_tx)
                if (projection is None or tuple(member['input_ref'] for member in members)!=refs
                        or any(metadata.get(key)!=projection[key] for key in
                               ('batch_ref','history_group_ref','merged_segments'))):
                    raise SourceUnavailable('SOURCE_INPUT_APPLICATION_UNKNOWN')
        messages={ref for node in nodes for m in node['messages'] for ref in source_message_refs(m)}
        queued={ref for node in nodes for m in node['followup_messages'] for ref in source_message_refs(m)}
        if checkpoint.get('source_initial_ref') not in messages:
            raise SourceUnavailable('SOURCE_INITIAL_INPUT_NOT_ATTACHED')
        tracking=state['resources'].setdefault('continuation_inputs',{})
        if not isinstance(tracking,dict): raise SourceUnavailable('SOURCE_INPUT_CHECKPOINT_INVALID')
        can_attach=(row['status']=='running' and not row['cancel_requested'] and not row.get('pause_requested')
                    and checkpoint.get('source_input_gate')!='closed'
                    and state.get('terminal_directive')!='stop_execution')
        for fact in facts:
            ref=fact['input_ref']
            if ref in messages:
                cursor.execute('''UPDATE agent_runner_inputs SET phase='appended',attached_revision=%s
                    WHERE input_ref=%s AND phase IN ('accepted','attached','deferred','appended')''',
                    (row['revision']+1,ref))
                tracking[ref]='appended'
            elif ref in queued:
                cursor.execute('''UPDATE agent_runner_inputs SET phase='attached',attached_revision=%s
                    WHERE input_ref=%s AND phase IN ('accepted','attached','deferred')''',(row['revision']+1,ref))
                tracking[ref]='queued'
            elif ref in controlled:
                continue
            elif fact['phase'] in ('attached','appended','applied'):
                raise SourceUnavailable('SOURCE_INPUT_APPLICATION_UNKNOWN')
            elif can_attach and fact['phase'] in ('accepted','deferred'):
                from .application_sources import project_input_in_tx
                members,projection=source_group_in_tx(cursor,fact,project_input_in_tx)
                # A new voice received during this CAS remains accepted until
                # its actual preparation completes; no raw placeholder enters LLM.
                if projection is None: continue
                state['followup_messages'].append(group_input_message(members,projection))
                for member in members:
                    member_ref=member['input_ref'];tracking[member_ref]='queued';queued.add(member_ref)
                    cursor.execute("UPDATE agent_runner_inputs SET phase='attached',attached_revision=%s WHERE input_ref=%s",
                                   (row['revision']+1,member_ref))
        if before_model and state.get('execution_id')==row['runner_id']:
            # This is the persisted pre-IO model boundary. Pending source user
            # messages enter the original model input before its physical call.
            moved=[m for m in state['followup_messages'] if (m.get('metadata') or {}).get('input_ref')]
            state['messages'].extend(moved)
            state['followup_messages']=[m for m in state['followup_messages'] if m not in moved]
            for message in moved:
                for ref in source_message_refs(message):
                    tracking[ref]='appended'
                    cursor.execute("UPDATE agent_runner_inputs SET phase='appended' WHERE input_ref=%s",(ref,))
        self._sync_continuation_intent(checkpoint,state)
        return checkpoint

    @staticmethod
    def unstarted_cancel_in_tx(cursor,row,checkpoint,result):
        """The original acknowledged unstarted pause, followed by cancel-only.

        This is acceptance/history cancellation, never model application proof.
        Missing execution without this durable proof remains unknown.
        """
        saved=row.get('checkpoint') or {}
        proof=saved.get('released_attempt') or {}
        if (result.get('status')!='cancelled' or row.get('cancel_requested') is not True
                or row['status']!='finalizing' or saved.get('finalization_intent')!='cancel_only'
                or saved.get('unstarted') is not True or checkpoint.get('unstarted') is not True
                or any(saved.get(k) or checkpoint.get(k) for k in ('execution','children','tools'))
                or not isinstance(proof,dict) or proof.get('status')!='paused'
                or proof.get('reason')!='unstarted_pause'
                or type(proof.get('attempt')) is not int or proof['attempt']!=row['attempt']-1):
            return False
        cursor.execute('SELECT 1 FROM agent_runner_usage_receipts WHERE runner_id=%s LIMIT 1',
                       (row['runner_id'],))
        return cursor.fetchone() is None

    def finish_in_tx(self,cursor,row,checkpoint,result,repository):
        """Final pending/cutoff decision shares the original stage transaction."""
        checkpoint=self.merge_in_tx(cursor,row,checkpoint)
        if not checkpoint.get('source_initial_ref'): return checkpoint,False
        state=self._root(checkpoint)
        self.lock_claim(cursor,row)
        cursor.execute('''SELECT * FROM agent_runner_inputs WHERE current_runner_id=%s
            AND phase IN ('accepted','attached','deferred') ORDER BY ordinal FOR UPDATE''',(row['runner_id'],))
        pending=cursor.fetchall()
        prepared_cancel=None
        if result.get('status')=='cancelled':
            from .application_sources import preparation_cancel_proof_in_tx
            # Every cancel cutoff checks unresolved physical preparation first,
            # including late input on a root that already has execution state.
            prepared_cancel=preparation_cancel_proof_in_tx(cursor,row,checkpoint)
        if pending and state is None:
            if not prepared_cancel and not self.unstarted_cancel_in_tx(cursor,row,checkpoint,result):
                raise SourceUnavailable('SOURCE_INPUT_APPLICATION_UNKNOWN')
            initial=checkpoint['source_initial_ref']
            if initial not in {fact['input_ref'] for fact in pending}:
                raise SourceUnavailable('SOURCE_INPUT_APPLICATION_UNKNOWN')
            for fact in pending:
                cursor.execute("UPDATE agent_runner_inputs SET phase='cancelled' WHERE input_ref=%s",(fact['input_ref'],))
            checkpoint['source_input_gate']='closed'
            if prepared_cancel:
                checkpoint['source_prepared_cancel']=prepared_cancel
            else:
                checkpoint['source_unstarted_cancel']={'version':1,'initial_ref':initial}
            return checkpoint,False
        if (pending and result['status']=='completed' and state is not None
                and state['iteration']<state['max_iterations'] and state.get('terminal_directive')!='stop_execution'):
            state['outcome']='running'
            return checkpoint,True
        if pending:
            # Only unappended inputs may move, including CP followup references.
            refs={f['input_ref'] for f in pending}
            used={ref for m in (state or {}).get('messages',[]) for ref in source_message_refs(m)}
            if refs & used: raise SourceUnavailable('SOURCE_INPUT_ALREADY_APPENDED')
            if result['status']=='cancelled':
                for ref in refs:
                    cursor.execute("UPDATE agent_runner_inputs SET phase='cancelled' WHERE input_ref=%s",(ref,))
                if state is not None:
                    state['followup_messages']=[m for m in state['followup_messages'] if not refs.intersection(source_message_refs(m))]
                    for ref in refs: state['resources'].get('continuation_inputs',{}).pop(ref,None)
                    self._sync_continuation_intent(checkpoint,state)
                checkpoint['source_input_gate']='closed'
                return checkpoint,False
            budget_exhausted=(state is not None and state.get('outcome')=='iteration_limit'
                              and state['iteration']>=state['max_iterations'])
            if result['status']!='completed' and not budget_exhausted:
                raise SourceUnavailable('SOURCE_INPUT_CUTOFF_UNRESOLVED')
            first=pending[0]
            request=RunnerSubmit(client_request_id='deferred_'+hashlib.sha256(
                json.dumps([row['runner_id'],*sorted(refs)],separators=(',',':')).encode()).hexdigest(),**first['intent'])
            from src.core.agent_engine.contracts import Identity
            principal=Principal(Identity(row['tenant_id'],row['user_id'],row['session_id'],row['source'],row['session_kind']),
                                row['actor_kind'],row['actor_id'],row['service_id'])
            next_cp={'source_initial_ref':first['input_ref'],'source_input_gate':'open',
                     'execution_context':checkpoint.get('execution_context')}
            next_row,_=repository.submit_in_tx(cursor,principal,request,row['profile_fingerprint'],
                resolved_profile_id=row['profile_id'],execution_context=checkpoint.get('execution_context'),initial_checkpoint=next_cp)
            for fact in pending:
                cursor.execute('''UPDATE agent_runner_inputs SET phase='deferred',current_runner_id=%s,
                    deferred_to_runner=%s WHERE input_ref=%s''',(next_row['runner_id'],next_row['runner_id'],fact['input_ref']))
            if state is not None:
                state['followup_messages']=[m for m in state['followup_messages'] if not refs.intersection(source_message_refs(m))]
                for ref in refs: state['resources'].get('continuation_inputs',{}).pop(ref,None)
                self._sync_continuation_intent(checkpoint,state)
        checkpoint['source_input_gate']='closed'
        return checkpoint,False
