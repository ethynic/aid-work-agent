"""Original screenshot/item and frozen VL target proof, inside the owner cursor."""

import hashlib

from src.core.agent_engine.contracts import CheckpointFailure, Identity
from src.local_tools.proxy_tool import BossResumeDetailTool, BossResumeBatchTool
from src.tools.executor import normalize_provided_parameters
from .contracts import canonical_json
from .local_invocations import LocalPhase


def assert_resume_model(cursor,row,node,call_id,ordinal,origin,provider,model):
    from .local_owner import RunnerLocalLifecycle
    call = ((node.get('tools') or {}).get(call_id) or {}).get('call') or {}
    cls = next((value for value in (BossResumeDetailTool,BossResumeBatchTool)
        if value.name==call.get('name')),None)
    if (cls is None or type(ordinal) is not int or ordinal<0
            or not isinstance(provider,str) or not provider or not isinstance(model,str) or not model):
        raise CheckpointFailure('COVERED_USAGE_OWNER_REQUIRED')
    item = ordinal//2
    if cls is BossResumeDetailTool and item!=0:
        raise CheckpointFailure('LOCAL_RESUME_ITEM_OWNER_MISMATCH')
    expected_identity = {key:row[key] for key in ('tenant_id','user_id','session_id','source','session_kind')}
    if node.get('identity')!=expected_identity:
        raise CheckpointFailure('LOCAL_RESUME_ITEM_OWNER_MISMATCH')
    refs = node.get('resources',{}).get('local_invocations') or {}
    ref = next((ref for ref in refs.values() if ref.get('tool_call_id')==call_id
        and ref.get('branch') in {'main','heal.retry'} and ref.get('ordinal')==0
        and ref.get('invocation_id')==origin.get('invocation_id')),None)
    arguments = normalize_provided_parameters(cls,call.get('arguments') or {})
    if (ref is None or ref.get('request',{}).get('arguments')!=arguments
            or ref.get('request',{}).get('tool_name')!=cls.name):
        raise CheckpointFailure('LOCAL_RESUME_ITEM_OWNER_MISMATCH')
    cursor.execute('SELECT * FROM local_tool_invocations WHERE id=%s AND tenant_id IS NOT DISTINCT FROM %s',
        (ref['invocation_id'],row['tenant_id']))
    invocation = dict(cursor.fetchone() or {})
    RunnerLocalLifecycle.validate_invocation(Identity(**node['identity']),row['runner_id'],
        LocalPhase(node['execution_id'],call_id,ref['branch'],ref['ordinal']),ref,invocation)
    if invocation.get('state')!='succeeded' or invocation.get('effect')=='unknown':
        raise CheckpointFailure('LOCAL_RESUME_ITEM_OWNER_MISMATCH')
    data = (invocation.get('result_json') or {}).get('data')
    payloads = data.get('resumes') if cls is BossResumeBatchTool and isinstance(data,dict) else [data]
    if not isinstance(payloads,list) or not 0<=item<len(payloads) or not isinstance(payloads[item],dict):
        raise CheckpointFailure('LOCAL_RESUME_ITEM_OWNER_MISMATCH')
    payload = payloads[item]
    images = payload.get('images') or []
    if (not isinstance(images,list) or not images or not isinstance(images[0],dict)
            or not isinstance(images[0].get('base64'),str)):
        raise CheckpointFailure('LOCAL_RESUME_ITEM_OWNER_MISMATCH')
    expected_origin = dict(invocation_id=str(invocation['id']),item_ordinal=item,
        candidate_name=str(payload.get('candidate_name') or '').strip(),
        image_digest=hashlib.sha256(images[0]['base64'].encode()).hexdigest())
    if any(origin.get(key)!=value for key,value in expected_origin.items()):
        raise CheckpointFailure('LOCAL_RESUME_ITEM_OWNER_MISMATCH')
    phase = LocalPhase(node['execution_id'],call_id,'resume.policy',item)
    policy = (node.get('resources',{}).get('local_domain_phases') or {}).get(phase.key(row['runner_id'])) or {}
    expected_policy = dict(runner_id=row['runner_id'],execution_id=node['execution_id'],
        tool_call_id=call_id,branch=phase.branch,ordinal=item)
    request = {**expected_origin,'resume_policy_version':1}
    if (any(policy.get(key)!=value for key,value in expected_policy.items())
            or policy.get('phase')!='completed' or policy.get('request')!=request
            or policy.get('intent_digest')!=hashlib.sha256(canonical_json(request).encode()).hexdigest()
            or (policy.get('result') or {}).get('model_spec')!=provider+'/'+model):
        raise CheckpointFailure('LOCAL_RESUME_MODEL_TARGET_MISMATCH')
