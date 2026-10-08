"""Compose shared cursor-only DAL operations; caller owns commit and rollback."""

import json
from datetime import timedelta
from decimal import Decimal
from src.core.agent_engine.contracts import CheckpointFailure
from src.db.models import MessageDB, ChatRecordDB
from src.db.channel_message_repository import insert_message, touch_session
from src.core.agent_events import mask_tool_args


def write_history(cursor, runner, result):
    session_id = runner['session_id']
    table = 'chat_sessions' if runner['session_kind'] == 'web' else 'channel_sessions'
    cursor.execute(f'SELECT * FROM {table} WHERE session_id=%s FOR UPDATE', (session_id,))
    session = cursor.fetchone()
    if (not session or session['tenant_id'] != runner['tenant_id']
            or session.get('user_id') != runner['user_id']):
        raise CheckpointFailure('FINALIZE_SESSION_OWNER_CHANGED')
    cursor.execute('SELECT clock_timestamp() AS now')
    now = cursor.fetchone()['now']
    messages = [{'role':'user','content':runner['input']['text'],
                 'attachments':runner['input'].get('attachments',[]),
                 'metadata':{'attachments':runner['input'].get('attachments',[]),
                             'accepted_at':runner['accepted_at'].isoformat()}}]
    messages.extend(result.get('messages') or [])
    # Domain preparation is a local presentation copy, not a mutation of the
    # submitted intent or immutable pending finalization result.
    import copy
    from .application_sources import project_ref_in_tx
    from .input_repository import source_group_in_tx, source_message_refs
    from .application_sources import project_input_in_tx

    def project_group(ref):
        # The existing ref projector validates the original runner/provenance.
        projected = project_ref_in_tx(cursor, runner, ref)
        cursor.execute('SELECT * FROM agent_runner_inputs WHERE input_ref=%s', (ref,))
        fact = cursor.fetchone()
        if fact is None:
            raise CheckpointFailure('SOURCE_HISTORY_APPLICATION_CHANGED')
        _, group = source_group_in_tx(cursor, fact, project_input_in_tx)
        return group if group is not None else projected
    messages=copy.deepcopy(messages)
    recorded_controls = {(message.get('metadata') or {}).get('control_id')
                         for message in messages if message.get('role')=='user'}
    # Accepted controls are display facts, not proof of model application. A
    # completed parent may consume only the child's reply, so inspect the whole
    # private tree for the actually paired/applied supplemental user message.
    def applied_controls(state):
        for message in state.get('messages',[]):
            if message.get('role')=='user':
                yield (message.get('metadata') or {}).get('control_id')
        for child in state.get('children',{}).values():
            yield from applied_controls(child.get('checkpoint') or {})
    applied = set(applied_controls((runner.get('checkpoint') or {}).get('execution') or {}))
    supplements = (runner.get('checkpoint') or {}).get('supplemental_inputs') or []
    for item in supplements:
        control_id = item.get('control_id')
        if not control_id or control_id not in applied or control_id in recorded_controls:
            continue
        recorded_controls.add(control_id)
        messages.append({'role':'user','content':item.get('text',''),
            'metadata':{'control_id':control_id,'accepted_at':item.get('accepted_at'),
                        'attachments':item.get('attachments') or [],**(item.get('source_metadata') or {})}})
    for index,message in enumerate(messages):
        ref=(message.get('metadata') or {}).get('input_ref')
        if index==0: ref=(runner.get('checkpoint') or {}).get('source_initial_ref') or ref
        if ref and message.get('role')=='user':
            projected=project_group(ref)
            if projected is not None:
                message['content']=projected['history_text']
                message['attachments']=projected['attachments']
                message.setdefault('metadata',{})['attachments']=projected['attachments']
                message['metadata']['preparation_ref']=projected.get('preparation_ref')
                cursor.execute('SELECT provenance FROM agent_runner_inputs WHERE input_ref=%s',(ref,))
                source=cursor.fetchone()
                if source:
                    message['metadata'].update({key:value for key,value in source['provenance'].items()
                        if key in {'tenant_id','source','config_id','corp_id','open_kfid','actor_id','chat_kind','chat_id','profile_id','session_id','user_id','route_id','account_id','namespace','message_id'}})
                    message['metadata']['msgid']=source['provenance']['message_id']
                message['metadata']['input_ref']=ref
                if projected.get('batch_ref'):
                    message['metadata'].update({key:copy.deepcopy(projected[key]) for key in
                        ('batch_ref','history_group_ref','input_refs','merged_segments')})
    if result.get('output') or result.get('images') or result.get('status') == 'cancelled':
        metadata = {**(result.get('assistant_metadata') or {}),'images':result.get('images') or []}
        if result.get('status') == 'cancelled':
            metadata['cancelled'] = True
        messages.append({'role':'assistant','content':result.get('output',''),'metadata':metadata})
    normalized = []
    for index,message in enumerate(messages):
        control_id = (message.get('metadata') or {}).get('control_id')
        input_ref=(message.get('metadata') or {}).get('input_ref')
        if index==0 and message['role']=='user':
            input_ref=(runner.get('checkpoint') or {}).get('source_initial_ref') or input_ref
        message_id = input_ref+':user' if input_ref and message['role']=='user' else (control_id+':user' if control_id and message['role']=='user' else (
            runner['runner_id'] + (':user' if index == 0 else f':message:{index}'))
        )
        if input_ref and message['role']=='user' and (message.get('metadata') or {}).get('history_group_ref'):
            message_id=message['metadata']['history_group_ref']
        metadata = {**(message.get('metadata') or {}),'runner_id':runner['runner_id'],
                    'runner_queue_order':runner['queue_order']}
        if input_ref and message['role']=='user':
            metadata['input_ref']=input_ref
        for key in ('tool_calls','tool_call_id','reasoning_content'):
            if key in message:
                metadata[key] = message[key]
        content = '' if message.get('tool_calls') else message.get('content','')
        if not isinstance(content,str):
            content = json.dumps(content,ensure_ascii=False)
        created = now.replace(tzinfo=None)+timedelta(microseconds=index)
        normalized.append({'message_id':message_id,'role':message['role'],'content':content,
                           'metadata':metadata,'created_at':created,'idempotent':True})
        if runner['session_kind'] == 'channel':
            insert_message(cursor,message_id=message_id,session_id=session_id,tenant_id=runner['tenant_id'],
                role=message['role'],content=content,message_type=message.get('message_type','text'),
                attachments=message.get('attachments'),metadata=metadata,created_at=created,idempotent=True,
                is_recalled=bool(message.get('is_recalled')))
    source_refs={ref for message in normalized if message['role']=='user'
                 for ref in source_message_refs(message)}
    if source_refs:
        from .input_repository import InputRepository
        checkpoint=runner.get('checkpoint') or {}
        proof=checkpoint.get('source_unstarted_cancel')
        unstarted=(isinstance(proof,dict) and set(proof)=={'version','initial_ref'}
                   and type(proof['version']) is int and proof['version']==1
                   and proof['initial_ref']==checkpoint.get('source_initial_ref')
                   and InputRepository.unstarted_cancel_in_tx(cursor,runner,checkpoint,result))
        from .application_sources import preparation_cancel_proof_in_tx
        saved_prepared=checkpoint.get('source_prepared_cancel')
        prepared_cancel=(saved_prepared is not None and result.get('status')=='cancelled'
            and saved_prepared==preparation_cancel_proof_in_tx(cursor,runner,checkpoint))
        if unstarted or prepared_cancel:
            # Stable accepted-user history and cancellation are truthful, while
            # the source remains cancelled, never appended/applied to a model.
            cursor.execute("SELECT input_ref FROM agent_runner_inputs WHERE current_runner_id=%s AND input_ref=ANY(%s) AND phase='cancelled'",
                           (runner['runner_id'],sorted(source_refs)))
        else:
            cursor.execute("""UPDATE agent_runner_inputs SET phase='applied' WHERE current_runner_id=%s
                AND input_ref=ANY(%s) AND phase='appended' RETURNING input_ref""",
                (runner['runner_id'],sorted(source_refs)))
        if {item['input_ref'] for item in cursor.fetchall()} != source_refs:
            raise CheckpointFailure('SOURCE_HISTORY_APPLICATION_CHANGED')
    if runner['session_kind'] == 'web':
        MessageDB.create_batch_in_tx(cursor,session_id,normalized)
        cursor.execute('UPDATE chat_sessions SET updated_at=clock_timestamp() WHERE session_id=%s',(session_id,))
    else:
        touch_session(cursor,session_id)


def settle_record(cursor, runner, result, cost, totals, breakdown):
    """Late facts update the original stable record and debit cumulative delta."""
    cursor.execute('SELECT * FROM chat_records WHERE record_id=%s FOR UPDATE',(runner['record_id'],))
    record = cursor.fetchone()
    if record is not None and (record['tenant_id'] != runner['tenant_id'] or record['session_id'] != runner['session_id']):
        raise CheckpointFailure('RECORD_OWNER_MISMATCH')
    previous = Decimal(str(record['credit_cost'])) if record else Decimal('0')
    delta = cost-previous
    if delta<0:
        raise CheckpointFailure('USAGE_COST_REGRESSION')
    state = (runner.get('checkpoint') or {}).get('execution') or {}
    models = sorted({group['model'] for group in breakdown['groups'] if group.get('model')})
    providers = sorted({group['provider'] for group in breakdown['groups'] if group.get('provider')})
    if record is None:
        user_message=runner['input']['text']
        initial_ref=(runner.get('checkpoint') or {}).get('source_initial_ref')
        if initial_ref:
            from .application_sources import project_ref_in_tx
            projected=project_ref_in_tx(cursor,runner,initial_ref)
            cursor.execute('SELECT * FROM agent_runner_inputs WHERE input_ref=%s',(initial_ref,))
            fact=cursor.fetchone()
            if fact is not None:
                from .input_repository import source_group_in_tx
                from .application_sources import project_input_in_tx
                _,group=source_group_in_tx(cursor,fact,project_input_in_tx)
                if group is not None: projected=group
            if projected is not None: user_message=projected['history_text']
        details = execution_statistics(state, runner['runner_id'],runner.get('checkpoint') or {})
        ChatRecordDB.create_in_tx(cursor,runner['record_id'],session_id=runner['session_id'],
            tenant_id=runner['tenant_id'],user_id=runner['user_id'],user_message=user_message,
            assistant_message=result.get('output',''),model=','.join(models),provider=','.join(providers),
            agent_iterations=state.get('iteration',0),status=result['status'],error_message=result.get('error_code'),
            source_type=runner['source'],execution_details=details,
            subagent_calls=details['subagent_calls'] or None,duration_ms=result.get('duration_ms',0))
    cursor.execute('''UPDATE chat_records SET credit_cost=%s,prompt_tokens=%s,completion_tokens=%s,
        cached_input_tokens=%s,total_token_count=%s,embedding_tokens=%s,asr_calls=%s,
        usage_breakdown=%s::jsonb,model=%s,provider=%s WHERE record_id=%s''',
        (cost,totals['prompt_tokens'],totals['completion_tokens'],totals['cached_input_tokens'],
         totals['total_token_count'],totals['embedding_tokens'],totals['asr_calls'],
         json.dumps(breakdown,ensure_ascii=False,default=str),','.join(models),','.join(providers),runner['record_id']))
    if not ChatRecordDB.debit_in_tx(cursor,runner['tenant_id'],delta):
        raise CheckpointFailure('BILLING_TENANT_NOT_FOUND')
    return delta


def execution_statistics(state, runner_id, envelope=None):
    """Existing public record shape, without serializing private child state."""
    children = []
    tools = []
    envelope = envelope or {}
    for call_id,fact in state.get('tools',{}).items():
        call = fact.get('call') or {}
        tools.append({'tool_name':call.get('name'),'tool_args':mask_tool_args(call.get('arguments') or {}),
                      'result':str(fact.get('result'))[:2000] if fact.get('result') is not None else None,
                      'success':fact.get('success',False),'error':None,
                      'duration_ms':(envelope.get('tool_durations') or {}).get(call_id,0)})
    for call_id,child in state.get('children',{}).items():
        checkpoint = child.get('checkpoint') or {}
        from src.core.execution_usage import execution_token_usage
        usage = execution_token_usage(checkpoint)
        call = ((state.get('tools',{}).get(call_id) or {}).get('call') or {}).get('arguments') or {}
        children.append({'execution_id':child.get('execution_id'),'status':child.get('status'),
            'subagent_name':child.get('profile_id') or checkpoint.get('profile_id'),
            'task_description':str(call.get('task_description',''))[:200],
            'success':child.get('status')=='completed','result_summary':str(checkpoint.get('output') or '')[:200],
            'token_usage':usage,'duration_ms':child.get('duration_ms',0)})
    return {'runner_id':runner_id,'tool_executions':tools,'total_iterations':state.get('iteration',0),
            'subagent_calls':children,'plan_id':state.get('plan_ref'),
            'plan_steps':[{key:step.get(key) for key in ('task_id','tool_name','description','status')}
                          for step in (envelope.get('business_plan') or {}).get('tasks',[])]}
