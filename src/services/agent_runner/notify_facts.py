"""Original notification HTTP facts for the finite domain composition.

Frozen content and payload digests prove which original POST returned; current settings cannot redefine an in-flight operation.
"""

import hashlib

from src.core.agent_engine.contracts import CheckpointFailure
from src.local_tools.proxy_tool import BossInterviewNotifyTool
from src.services import recruiting_notify_service, wecom_bot
from src.tools.executor import normalize_provided_parameters
from pydantic import TypeAdapter
from .contracts import canonical_json
from .local_invocations import LocalPhase


def digest(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def original_arguments(call):
    if call.get('name') != BossInterviewNotifyTool.name:
        raise CheckpointFailure('LOCAL_NOTIFY_OWNER_MISMATCH')
    args = TypeAdapter(dict).dump_python(normalize_provided_parameters(
        BossInterviewNotifyTool,call.get('arguments') or {}),mode='json')
    return {'kind':args.get('kind'),'job_name':args.get('job_name') or '',
        'candidates':recruiting_notify_service._normalize_candidates(args.get('candidates') or []),
        'note':args.get('note')}


def _owned(row,node,call_id,key,fact):
    if not isinstance(fact,dict) or type(fact.get('ordinal')) is not int or fact['ordinal']<0:
        raise CheckpointFailure('LOCAL_NOTIFY_OWNER_MISMATCH')
    phase = LocalPhase(node['execution_id'],call_id,fact['branch'],fact['ordinal'])
    expected = dict(runner_id=row['runner_id'],execution_id=node['execution_id'],
        tool_call_id=call_id,branch=phase.branch,ordinal=phase.ordinal)
    if (key != phase.key(row['runner_id']) or any(fact.get(k)!=v for k,v in expected.items())
            or fact.get('intent_digest') != digest(fact.get('request') or {})
            or type(fact.get('authorized_attempt')) is not int
            or not 0 < fact['authorized_attempt'] <= row['attempt']):
        raise CheckpointFailure('LOCAL_NOTIFY_OWNER_MISMATCH')


def delivery_facts(row,node,call_id):
    """Return only original known ACKs; never authorize a new HTTP request."""
    if node.get('identity') != {key:row[key] for key in (
            'tenant_id','user_id','session_id','source','session_kind')}:
        raise CheckpointFailure('LOCAL_NOTIFY_OWNER_MISMATCH')
    tool = (node.get('tools') or {}).get(call_id) or {}
    original = original_arguments(tool.get('call') or {})
    all_facts = node.get('resources',{}).get('local_domain_phases') or {}
    relevant = {key:fact for key,fact in all_facts.items() if fact.get('tool_call_id')==call_id}
    for key,fact in relevant.items():
        _owned(row,node,call_id,key,fact)
        if (fact['branch'] not in {'notify.binding','notify.policy','notify.log','notify.markdown','notify.mention'}
                or fact['branch'] in {'notify.binding','notify.policy','notify.log'} and fact['ordinal']!=0):
            raise CheckpointFailure('LOCAL_NOTIFY_OWNER_MISMATCH')
    policy_key = LocalPhase(node['execution_id'],call_id,'notify.policy',0).key(row['runner_id'])
    policy = relevant.get(policy_key)
    http = [fact for fact in relevant.values() if fact['branch'] in {'notify.markdown','notify.mention'}]
    if policy is None:
        if http:
            raise CheckpointFailure('LOCAL_NOTIFY_POLICY_MISSING')
        # No proved HTTP dispatch exists. A legacy in-flight override without
        # any domain evidence cannot be inferred safe from absent device refs.
        binding = relevant.get(LocalPhase(node['execution_id'],call_id,'notify.binding',0).key(row['runner_id']))
        if binding is not None and (binding.get('phase')!='completed'
                or binding.get('request')!={**original,'notify_binding_version':1}):
            raise CheckpointFailure('LOCAL_NOTIFY_BINDING_MISMATCH')
        safe = (binding is not None or tool.get('phase')=='prepared'
            or tool.get('phase')=='completed' and tool.get('result_recorded'))
        return {'known':bool(safe),'attempted':0,'acknowledged':0,'complete':False,
            'error_code':None if safe else 'LOCAL_NOTIFY_DELIVERY_VERIFICATION_REQUIRED'}
    binding = relevant.get(LocalPhase(node['execution_id'],call_id,'notify.binding',0).key(row['runner_id']))
    if (not binding or binding.get('phase')!='completed'
            or binding.get('request')!={**original,'notify_binding_version':1}
            or set(binding.get('result') or {})!={'resume_id'}
            or binding['result']['resume_id'] is not None and (
                type(binding['result']['resume_id']) is not int or binding['result']['resume_id']<=0)):
        raise CheckpointFailure('LOCAL_NOTIFY_BINDING_MISMATCH')
    request,result = policy.get('request') or {},policy.get('result') or {}
    if (policy.get('phase')!='completed' or policy['ordinal']!=0
            or request!={**original,'resume_id':binding['result']['resume_id'],'notify_policy_version':1}
            or result.get('configuration',{}).get('tenant_id')!=row['tenant_id']
            or not isinstance(result.get('content'),str)):
        raise CheckpointFailure('LOCAL_NOTIFY_POLICY_MISMATCH')
    chunks = wecom_bot._split_markdown(result['content'])
    if (type(result.get('markdown_count')) is not int or not chunks
            or result['markdown_count']!=len(chunks) or type(result.get('mention_required')) is not bool
            or result['mention_required'] and original['kind']!='done'):
        raise CheckpointFailure('LOCAL_NOTIFY_POLICY_MISMATCH')
    configuration = result['configuration']
    if (set(configuration)!={'tenant_id','version','at_digest'}
            or not isinstance(configuration.get('version'),str) or not configuration['version']
            or not _hash(configuration.get('at_digest'))
            or not _hash(result.get('mention_payload_digest'))):
        raise CheckpointFailure('LOCAL_NOTIFY_POLICY_MISMATCH')
    outcomes = {}
    known = True
    acknowledged = 0
    for fact in http:
        branch,ordinal,intent = fact['branch'],fact['ordinal'],fact['request']
        chunk,index = ordinal//2,ordinal%2
        if branch=='notify.markdown':
            if not 0<=chunk<len(chunks):
                raise CheckpointFailure('LOCAL_NOTIFY_CHUNK_MISMATCH')
            expected_digest = digest({'msgtype':'markdown','markdown':{'content':chunks[chunk]}})
        else:
            if chunk!=0 or not result['mention_required']:
                raise CheckpointFailure('LOCAL_NOTIFY_CHUNK_MISMATCH')
            expected_digest = result.get('mention_payload_digest')
        expected = dict(configuration=configuration,payload_digest=expected_digest,
            chunk=chunk,attempt=index,notify_phase_version=1)
        if intent!=expected:
            raise CheckpointFailure('LOCAL_NOTIFY_CHUNK_MISMATCH')
        outcome = (fact.get('result') or {}).get('outcome') if fact.get('phase')=='completed' else None
        if outcome not in {'acknowledged','rejected'}:
            known = False
        else:
            provider_code = (fact.get('result') or {}).get('provider_code')
            if type(provider_code) is not int or (outcome=='acknowledged') != (provider_code==0):
                raise CheckpointFailure('LOCAL_NOTIFY_ACK_MISMATCH')
            acknowledged += outcome=='acknowledged'
        outcomes[(branch,chunk,index)] = outcome
    for branch,chunk,index in outcomes:
        if index==1 and outcomes.get((branch,chunk,0))!='rejected':
            raise CheckpointFailure('LOCAL_NOTIFY_SEQUENCE_MISMATCH')
        if branch=='notify.markdown' and any(not any(
                outcomes.get(('notify.markdown',previous,j))=='acknowledged' for j in range(2))
                for previous in range(chunk)):
            raise CheckpointFailure('LOCAL_NOTIFY_SEQUENCE_MISMATCH')
        if branch=='notify.mention' and any(not any(
                outcomes.get(('notify.markdown',previous,j))=='acknowledged' for j in range(2))
                for previous in range(len(chunks))):
            raise CheckpointFailure('LOCAL_NOTIFY_SEQUENCE_MISMATCH')
    complete = known and all(any(outcomes.get(('notify.markdown',i,j))=='acknowledged'
        for j in range(2)) for i in range(len(chunks)))
    if result['mention_required']:
        complete = complete and any(outcomes.get(('notify.mention',0,j))=='acknowledged' for j in range(2))
    return {'known':known,'attempted':len(http),'acknowledged':acknowledged,'complete':bool(complete),
        'error_code':None if known else 'LOCAL_NOTIFY_DELIVERY_VERIFICATION_REQUIRED'}


def _hash(value):
    return isinstance(value,str) and len(value)==64 and all(c in '0123456789abcdef' for c in value)


def original_log_intent(row,node,call_id):
    """A pure audit projection; it is not a dispatch or completion permission."""
    report = delivery_facts(row,node,call_id)
    if not report['known'] or not report['attempted']:
        raise CheckpointFailure('LOCAL_NOTIFY_DELIVERY_VERIFICATION_REQUIRED')
    phase = LocalPhase(node['execution_id'],call_id,'notify.policy',0)
    policy = node['resources']['local_domain_phases'][phase.key(row['runner_id'])]
    request,value = policy['request'],policy['result']
    complete = report['complete']
    return {'kind':request['kind'],'candidates':request['candidates'],
        'content':value['content'],'resume_id':request['resume_id'],
        'configuration':value['configuration'],'policy_digest':digest(request),
        'status':'sent' if complete else 'failed',
        'error':None if complete else '通知未完整送达，已确认的发送已留痕',
        'http_summary':{key:report[key] for key in ('attempted','acknowledged','complete')},
        'notify_log_version':1}


def assert_notification_dispatch(cursor,row,node,call_id,branch,ordinal,intent):
    """A new POST requires the frozen policy and current trusted configuration."""
    report = delivery_facts(row,node,call_id)
    if not report['known']:
        raise CheckpointFailure('LOCAL_NOTIFY_DELIVERY_VERIFICATION_REQUIRED')
    policy_key = LocalPhase(node['execution_id'],call_id,'notify.policy',0).key(row['runner_id'])
    facts = node['resources']['local_domain_phases']
    policy = facts.get(policy_key) or {}
    request,value = policy.get('request') or {},policy.get('result') or {}
    chunks = wecom_bot._split_markdown(value.get('content') or '')
    chunk,index = ordinal//2,ordinal%2
    if branch=='notify.markdown' and 0<=chunk<len(chunks):
        payload_digest = digest({'msgtype':'markdown','markdown':{'content':chunks[chunk]}})
        predecessors = [('notify.markdown',previous) for previous in range(chunk)]
    elif branch=='notify.mention' and chunk==0 and value.get('mention_required'):
        payload_digest = value.get('mention_payload_digest')
        predecessors = [('notify.markdown',previous) for previous in range(len(chunks))]
    else:
        raise CheckpointFailure('LOCAL_NOTIFY_CHUNK_MISMATCH')
    expected = dict(configuration=value['configuration'],payload_digest=payload_digest,
        chunk=chunk,attempt=index,notify_phase_version=1)
    if intent!=expected:
        raise CheckpointFailure('LOCAL_NOTIFY_CHUNK_MISMATCH')

    def outcome(previous_branch,previous_chunk,previous_index):
        fact = facts.get(LocalPhase(node['execution_id'],call_id,
            previous_branch,previous_chunk*2+previous_index).key(row['runner_id'])) or {}
        return (fact.get('result') or {}).get('outcome') if fact.get('phase')=='completed' else None
    if (index==1 and outcome(branch,chunk,0)!='rejected'
            or any(not any(outcome(b,c,j)=='acknowledged' for j in range(2)) for b,c in predecessors)):
        raise CheckpointFailure('LOCAL_NOTIFY_SEQUENCE_MISMATCH')
    cursor.execute('SELECT tenant_id,enabled,webhook_url,at_mobiles,pre_notify_enabled,updated_at '
        'FROM bs_recruiting_notify_settings WHERE tenant_id=%s FOR SHARE',(row['tenant_id'],))
    current = dict(cursor.fetchone() or {})
    version = current.get('updated_at')
    configuration = {'tenant_id':current.get('tenant_id'),
        'version':version.isoformat() if version is not None else None,
        'at_digest':digest(list(current.get('at_mobiles') or []))}
    if (not current.get('enabled') or not current.get('webhook_url')
            or request.get('kind')=='pre' and not current.get('pre_notify_enabled')
            or configuration!=value['configuration']):
        from src.local_tools.durable_flow import LocalContinuationRequired
        raise LocalContinuationRequired('LOCAL_NOTIFY_CONFIGURATION_CHANGED')
