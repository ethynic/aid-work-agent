"""Versioned cloud sleep facts, shared by park, recovery and timer claims."""

import copy
from datetime import datetime

from .contracts import RunnerError
from .wait_identity import owned_wait_id


def nodes(root):
    pending, seen = [root], set()
    while pending:
        node = pending.pop()
        if not isinstance(node,dict) or node.get('execution_id') in seen or len(seen)>=1000:
            raise RunnerError('SLEEP_CHECKPOINT_INVALID',409)
        seen.add(node.get('execution_id'))
        yield node
        for child in node.get('children',{}).values():
            saved = child.get('checkpoint')
            if isinstance(saved,dict):
                pending.append(saved)
            elif not child.get('unstarted'):
                raise RunnerError('SLEEP_UNSAFE_BOUNDARY',409)


def sleep_leaf(checkpoint, *, safe=False):
    root = checkpoint.get('execution')
    if not isinstance(root,dict):
        return None
    tree = list(nodes(root))
    leaves = [node for node in tree if (node.get('waiting') or {}).get('kind')=='timer']
    if not leaves:
        return None
    if len(leaves)!=1:
        raise RunnerError('SLEEP_UNSAFE_BOUNDARY',409)
    leaf = leaves[0]
    wait = leaf['waiting']
    call_id = wait.get('tool_call_id')
    fact = leaf.get('tools',{}).get(call_id) or {}
    call = fact.get('call') or {}
    if (wait.get('version')!=1 or call.get('name')!='agent_sleep' or call.get('id')!=call_id
            or fact.get('phase')!='waiting' or fact.get('invocation_id') is not None
            or not leaf.get('pending') or leaf['pending'][0].get('id')!=call_id
            or type(wait.get('seconds')) is not int or not 1<=wait['seconds']<=604800
            or wait.get('seconds')!=(call.get('arguments') or {}).get('seconds')
            or wait.get('wait_id')!=owned_wait_id(root['execution_id'],leaf['execution_id'],wait)
            or wait.get('target_execution_id')!=leaf['execution_id']):
        raise RunnerError('SLEEP_CHECKPOINT_INVALID',409)
    if safe:
        for node in tree:
            waiting = node.get('waiting') or {}
            if node['execution_id']==leaf['execution_id']:
                pass
            elif waiting:
                child = node.get('children',{}).get(waiting.get('tool_call_id')) or {}
                if (waiting.get('kind') not in {'child_wait','child_clarification'}
                        or child.get('execution_id')!=waiting.get('execution_id')
                        or not isinstance(child.get('checkpoint'),dict)):
                    raise RunnerError('SLEEP_UNSAFE_BOUNDARY',409)
            elif node.get('outcome') not in {'completed','failed','cancelled','iteration_limit'}:
                raise RunnerError('SLEEP_UNSAFE_BOUNDARY',409)
            for tool_id,item in node.get('tools',{}).items():
                if item.get('phase') in {'dispatching','waiting'}:
                    if node is leaf and tool_id==call_id:
                        continue
                    child = node.get('children',{}).get(tool_id)
                    if not child or (waiting.get('tool_call_id')!=tool_id):
                        raise RunnerError('SLEEP_UNSAFE_BOUNDARY',409)
                if item.get('phase')=='prepared' and (item.get('invocation_id') or tool_id in node.get('children',{})):
                    raise RunnerError('SLEEP_UNSAFE_BOUNDARY',409)
            if any(item.get('phase')=='started' for item in node.get('model_calls',[])):
                raise RunnerError('SLEEP_UNSAFE_BOUNDARY',409)
    return leaf


def _refresh_children(root):
    for child in root.get('children',{}).values():
        saved = child.get('checkpoint')
        if isinstance(saved,dict):
            _refresh_children(saved)
            child.update(status=saved['outcome'],waiting=copy.deepcopy(saved.get('waiting')))
    wait = root.get('waiting') or {}
    if wait.get('kind') in {'child_wait','child_clarification'}:
        child = root.get('children',{}).get(wait.get('tool_call_id')) or {}
        wait['child_wait'] = copy.deepcopy((child.get('checkpoint') or {}).get('waiting'))


def arm(checkpoint, database_now, *, existing=None):
    """Called only under the original attempt lock, in the park transaction."""
    from datetime import timedelta
    leaf = sleep_leaf(checkpoint,safe=True)
    if leaf is None:
        return None
    wait = leaf['waiting']
    previous = existing or {}
    if previous.get('version')==1 and previous.get('wait_id')==wait['wait_id'] and previous.get('phase')=='armed':
        if any(previous.get(key)!=value for key,value in
            (('seconds',wait['seconds']),('execution_id',leaf['execution_id']),('tool_call_id',wait['tool_call_id']))):
            raise RunnerError('SLEEP_TIMER_MISMATCH',409)
        deadline = datetime.fromisoformat(previous['wake_at'])
    else:
        deadline = database_now+timedelta(seconds=wait['seconds'])
    wait['wake_at'] = deadline.isoformat()
    checkpoint['durable_sleep'] = {'version':1,'phase':'armed','wait_id':wait['wait_id'],
        'execution_id':leaf['execution_id'],'tool_call_id':wait['tool_call_id'],
        'seconds':wait['seconds'],'wake_at':deadline.isoformat()}
    resources = leaf.setdefault('resources',{})
    round_ids=resources.get('round_tool_ids') or []
    round_facts=[leaf.get('tools',{}).get(call_id) or {} for call_id in round_ids]
    pure_sleep=bool(round_facts) and all((item.get('call') or {}).get('name')=='agent_sleep'
        and item.get('phase') in {'prepared','waiting','completed'}
        and (item.get('phase')!='completed' or item.get('success') is True) for item in round_facts)
    if pure_sleep:
        from src.tools.agent.sleep_tool import SleepInput
        from pydantic import ValidationError
        try:
            for item in round_facts:
                SleepInput.model_validate(item['call']['arguments'])
        except ValidationError:
            pure_sleep=False
    if pure_sleep and resources.get('sleep_credited_iteration')!=leaf['iteration']:
        resources['business_iterations'] = max(0,resources.get('business_iterations',leaf['iteration'])-1)
        resources['sleep_credited_iteration'] = leaf['iteration']
    _refresh_children(checkpoint['execution'])
    return deadline


def arm_in_tx(cursor,row,checkpoint):
    from .recovery_repository import lock_original_claim
    lock_original_claim(cursor,row)
    from src.core.agent_engine.contracts import CheckpointFailure
    from .local_recovery import _cancel_facts,cancel_completion_status
    try:
        facts=_cancel_facts(cursor,{**row,'checkpoint':checkpoint},request=False)
        safe,_=cancel_completion_status(facts)
    except CheckpointFailure as error:
        raise RunnerError('SLEEP_UNSAFE_BOUNDARY',409) from error
    if not safe:
        raise RunnerError('SLEEP_UNSAFE_BOUNDARY',409)
    cursor.execute('SELECT clock_timestamp() AS database_now')
    return arm(checkpoint,cursor.fetchone()['database_now'],
        existing=(row.get('checkpoint') or {}).get('durable_sleep'))


def validate_armed(row):
    checkpoint = row.get('checkpoint') or {}
    timer = checkpoint.get('durable_sleep') or {}
    leaf = sleep_leaf(checkpoint,safe=True)
    wait = (leaf or {}).get('waiting') or {}
    if (timer.get('version')!=1 or timer.get('phase')!='armed' or leaf is None
            or any(timer.get(key)!=wait.get(key) for key in ('wait_id','tool_call_id','seconds','wake_at'))
            or timer.get('execution_id')!=leaf['execution_id']):
        raise RunnerError('SLEEP_TIMER_MISMATCH',409)
    try:
        deadline = datetime.fromisoformat(timer['wake_at'])
    except (TypeError,ValueError,KeyError) as error:
        raise RunnerError('SLEEP_TIMER_MISMATCH',409) from error
    if deadline.tzinfo is None or (row.get('wake_at') is not None and row['wake_at']!=deadline):
        raise RunnerError('SLEEP_TIMER_MISMATCH',409)
    return timer,leaf


def complete_sleep(row, states, checkpoint, *, trigger):
    """Complete the fact once; Engine alone pairs the original tool result."""
    timer,leaf = validate_armed(row)
    state = states[leaf['execution_id']]
    fact = state.tools[timer['tool_call_id']]
    fact.phase, fact.success = 'completed',True
    fact.result = {'success':True,'wait_id':timer['wait_id'],'wake_at':timer['wake_at'],
                   'trigger':trigger}
    fact.preserve_content = True
    state.waiting = None
    checkpoint['durable_sleep'] = {**timer,'phase':'consumed','trigger':trigger}
    for current in states.values():
        resources=current.resources
        if resources.get('continuous_enabled') is True:
            resources['continuous_segment_start']=resources.get('business_iterations',current.iteration)


def recover_uncommitted(state, fact):
    if fact.call.name!='agent_sleep' or fact.invocation_id is not None:
        return False
    fact.phase,fact.success = 'completed',False
    fact.result = {'success':False,'error_code':'SLEEP_NOT_COMMITTED'}
    fact.preserve_content = True
    state.waiting = None
    return True


def replayable_activation(row):
    receipt = (row.get('checkpoint') or {}).get('timer_activation') or {}
    timer = (row.get('checkpoint') or {}).get('durable_sleep') or {}
    proven=(receipt.get('version')==1 and receipt.get('attempt')==row.get('attempt')
            and receipt.get('action_started') is False and bool(receipt.get('wait_id'))
            and timer.get('phase')=='consumed' and timer.get('version')==1
            and timer.get('trigger')=='timer' and timer.get('wait_id')==receipt['wait_id'])
    if not proven:
        return False
    root=(row.get('checkpoint') or {}).get('execution')
    if not isinstance(root,dict):
        return False
    found=False
    try:
        for node in nodes(root):
            if any(fact.get('phase') in {'started','unknown'} for fact in node.get('model_calls',[])):
                return False
            for call_id,fact in node.get('tools',{}).items():
                if node['execution_id']==timer.get('execution_id') and call_id==timer.get('tool_call_id'):
                    result=fact.get('result') or {}
                    found=(fact.get('phase')=='completed' and fact.get('success') is True
                        and result.get('wait_id')==timer['wait_id'] and result.get('trigger')=='timer')
                if fact.get('phase') in {'dispatching','waiting','unknown'}:
                    # Ancestor delegation is the original suspended child call,
                    # not a newly dispatched external action in this activation.
                    child=node.get('children',{}).get(call_id)
                    if not isinstance(child,dict) or not isinstance(child.get('checkpoint'),dict):
                        return False
    except RunnerError:
        return False
    return found


def preserve_activation_fence(row, checkpoint):
    """A physical receipt can fence dispatch between two owner checkpoints."""
    previous = (row.get('checkpoint') or {}).get('timer_activation') or {}
    current = checkpoint.get('timer_activation') or {}
    if (previous.get('version')==1 and previous.get('attempt')==row.get('attempt')
            and previous.get('action_started') is True):
        if (current.get('attempt') not in (None,previous['attempt'])
                or current.get('wait_id') not in (None,previous.get('wait_id'))):
            raise RunnerError('SLEEP_ACTIVATION_MISMATCH',409)
        checkpoint = {**checkpoint,'timer_activation':{**previous,**current,'action_started':True}}
    return checkpoint
