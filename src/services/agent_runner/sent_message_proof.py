"""Finite original desktop ACK proof for sent-message audit postprocessing."""

from src.core.agent_engine.contracts import CheckpointFailure, Identity
from src.local_tools.proxy_tool import BossSendToTool
from src.tools.executor import normalize_provided_parameters
from .local_invocations import LocalPhase
from .local_owner import RunnerLocalLifecycle


def assert_sent_message(cursor, row, node, call_id, intent):
    call = ((node.get('tools') or {}).get(call_id) or {}).get('call') or {}
    args = normalize_provided_parameters(BossSendToTool, call.get('arguments') or {})
    if (call.get('name') != BossSendToTool.name or args.get('script_title')
            or args.get('dry_run') or str(args.get('to') or '').strip() != intent.get('candidate_name')
            or str(args.get('message') or '').strip() != intent.get('message')):
        raise CheckpointFailure('LOCAL_SENT_ACK_OWNER_MISMATCH')
    ref = next((ref for ref in (node.get('resources',{}).get('local_invocations') or {}).values()
        if ref.get('tool_call_id') == call_id and ref.get('branch') in {'main','heal.retry'}
        and ref.get('ordinal') == 0 and ref.get('invocation_id') == intent.get('invocation_id')),None)
    if ref is None or ref.get('request',{}).get('arguments') != args:
        raise CheckpointFailure('LOCAL_SENT_ACK_OWNER_MISMATCH')
    cursor.execute('SELECT * FROM local_tool_invocations WHERE id=%s AND tenant_id IS NOT DISTINCT FROM %s',
        (intent['invocation_id'],row['tenant_id']))
    invocation = dict(cursor.fetchone() or {})
    RunnerLocalLifecycle.validate_invocation(Identity(**node['identity']),row['runner_id'],
        LocalPhase(node['execution_id'],call_id,ref['branch'],ref['ordinal']),ref,invocation)
    data = (invocation.get('result_json') or {}).get('data') or {}
    if (invocation.get('state') != 'succeeded' or invocation.get('effect') == 'unknown'
            or not isinstance(data,dict) or data.get('sent') is not True or data.get('dry_run')):
        raise CheckpointFailure('LOCAL_SENT_ACK_NOT_KNOWN')
