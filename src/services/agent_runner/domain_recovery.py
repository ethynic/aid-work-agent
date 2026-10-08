"""Registered domain proof over original invocation, item and provider facts."""

import hashlib

from .contracts import RunnerError, canonical_json
from .local_invocations import LocalPhase
from src.local_tools import repository


def _owned(row,state,fact,key,phase):
    expected = dict(runner_id=row['runner_id'],execution_id=state.execution_id,
        tool_call_id=fact.call.id,branch=phase['branch'],ordinal=phase['ordinal'])
    if (key != LocalPhase(state.execution_id,fact.call.id,phase['branch'],phase['ordinal']).key(row['runner_id'])
            or any(phase.get(k)!=v for k,v in expected.items())
            or phase.get('intent_digest') != hashlib.sha256(canonical_json(phase.get('request') or {}).encode()).hexdigest()):
        raise RunnerError('LOCAL_PHASE_OWNER_VERIFICATION_REQUIRED',409)


def cloud_read_proof(row,state,fact,branch,arguments):
    if any(ref.get('tool_call_id')==fact.call.id for ref in (state.resources.get('local_invocations') or {}).values()):
        return False
    for key,phase in (state.resources.get('local_domain_phases') or {}).items():
        if phase.get('tool_call_id')!=fact.call.id:
            continue
        _owned(row,state,fact,key,phase)
        if (phase['branch']!=branch or phase['ordinal']!=0 or phase.get('phase')!='completed'
                or phase.get('request')!=arguments):
            return False
    # Only these exact cloud reads may repeat when no completion was saved.
    return True


def _model_proof(row,state,fact,key,phase):
    request = phase['request']
    purpose = request.get('model_purpose')
    if (request.get('model_phase_version')!=1 or purpose!='resume_recognition_covered'
            or type(phase.get('authorized_attempt')) is not int
            or not 0<phase['authorized_attempt']<=row['attempt']):
        return False
    with repository.get_db_connection() as connection:
        cursor = connection.cursor()
        if phase.get('phase')=='started':
            policy_key = LocalPhase(state.execution_id,fact.call.id,'resume.policy',phase['ordinal']//2).key(row['runner_id'])
            policy = (state.resources.get('local_domain_phases') or {}).get(policy_key) or {}
            target = (policy.get('result') or {}).get('model_spec') or ''
            if '/' not in target:
                return False
            from .resume_model_proof import assert_resume_model
            from src.core.agent_engine.contracts import CheckpointFailure
            try:
                assert_resume_model(cursor,row,state.checkpoint(),fact.call.id,phase['ordinal'],request,*target.split('/',1))
            except CheckpointFailure:
                return False
            cursor.execute('''SELECT 1 FROM agent_runner_usage_receipts WHERE runner_id=%s
                AND execution_id=%s AND tool_call_id=%s AND purpose=%s LIMIT 1''',
                (row['runner_id'],state.execution_id,fact.call.id,'domain:'+key+':'+purpose))
            return cursor.fetchone() is None
        if phase.get('phase')!='completed':
            return False
        result = phase.get('result') or {}
        cursor.execute('SELECT * FROM agent_runner_usage_receipts WHERE runner_id=%s AND receipt_id=%s',
            (row['runner_id'],result.get('_runner_receipt_id')))
        receipt = dict(cursor.fetchone() or {})
        from .resume_model_proof import assert_resume_model
        try:
            assert_resume_model(cursor,row,state.checkpoint(),fact.call.id,phase['ordinal'],request,
                receipt.get('provider') or '',receipt.get('model') or '')
        except Exception as error:
            from src.core.agent_engine.contracts import CheckpointFailure
            if isinstance(error,CheckpointFailure):
                return False
            raise
    expected = dict(execution_id=state.execution_id,tool_call_id=fact.call.id,owner='llm',
        provider=result.get('provider'),model=result.get('model'),purpose='domain:'+key+':'+purpose,
        authorized_attempt=phase['authorized_attempt'])
    return (all(receipt.get(k)==v for k,v in expected.items()) and receipt.get('phase') in {'observed','unknown'}
        and receipt.get('billing_boundary','').startswith('covered:resume-recognition:'))


def validate_domain_phases(row,state,fact,cls,arguments):
    from src.local_tools.proxy_tool import BossResumeDetailTool,BossResumeBatchTool,BossSendToTool,BossSendCurrentTool
    phases = state.resources.get('local_domain_phases') or {}
    relevant = [(key,phase) for key,phase in phases.items() if phase.get('tool_call_id')==fact.call.id]
    if cls not in (BossResumeDetailTool,BossResumeBatchTool,BossSendToTool,BossSendCurrentTool):
        return all(phase['branch'].startswith('heal.') for _,phase in relevant)
    refs = state.resources.get('local_invocations') or {}
    originals = {}
    for ref in refs.values():
        if ref.get('tool_call_id')==fact.call.id and ref.get('branch') in {'main','heal.retry'}:
            originals[ref['invocation_id']] = repository.get_invocation(ref['invocation_id'],row['tenant_id'])
    for key,phase in relevant:
        _owned(row,state,fact,key,phase)
        branch,ordinal,request = phase['branch'],phase['ordinal'],phase['request']
        if branch.startswith('heal.'):
            continue  # Original common proof validates these branches too.
        invocation = originals.get(request.get('invocation_id'))
        if not invocation or invocation['state']!='succeeded' or invocation.get('effect')=='unknown':
            return False
        data = (invocation.get('result_json') or {}).get('data') or {}
        if cls in (BossSendToTool,BossSendCurrentTool):
            if (cls is not BossSendToTool or branch not in {'send.comm_binding','send.comm_log'}
                    or ordinal!=0 or phase.get('phase')!='completed'
                    or request.get('candidate_name')!=str(arguments.get('to') or '').strip()
                    or request.get('message')!=str(arguments.get('message') or '').strip()
                    or not isinstance(data,dict) or data.get('sent') is not True or data.get('dry_run')):
                return False
            continue
        if branch not in {'resume.policy','resume.evaluate','resume.evaluation','resume.fee','resume.insert','resume.match'}:
            return False
        item = ordinal//2 if branch=='resume.evaluate' else ordinal
        payloads = data.get('resumes') if cls is BossResumeBatchTool and isinstance(data,dict) else [data]
        if not isinstance(payloads,list) or not 0<=item<len(payloads) or not isinstance(payloads[item],dict):
            return False
        payload = payloads[item]
        images = payload.get('images') or []
        if not images or not isinstance(images[0],dict) or not isinstance(images[0].get('base64'),str):
            return False
        origin = dict(invocation_id=str(invocation['id']),item_ordinal=item,
            candidate_name=str(payload.get('candidate_name') or '').strip(),
            image_digest=hashlib.sha256(images[0]['base64'].encode()).hexdigest())
        if any(request.get(k)!=v for k,v in origin.items()):
            return False
        if branch=='resume.evaluate':
            if not _model_proof(row,state,fact,key,phase):
                return False
        elif phase.get('phase')!='completed':
            return False
    return True
