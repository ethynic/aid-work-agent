"""Finite original-notification audit for the cancellation composition.

These functions never send HTTP, select a configuration, or permit another
operation. The caller owns the root checkpoint
lock and the original immutable attempt's transaction.
"""

import copy

from src.core.agent_engine.contracts import CheckpointFailure
from src.local_tools.proxy_tool import BossInterviewNotifyTool
from src.local_tools.recruiting_writers import write_notify_log
from .local_invocations import LocalPhase
from .notify_facts import delivery_facts, digest, original_log_intent
from .ownership import lock_runner


def notification_nodes(row):
    expected = {key:row[key] for key in (
        'tenant_id','user_id','session_id','source','session_kind')}
    root = (row.get('checkpoint') or {}).get('execution')
    if root is None:
        return
    if root.get('execution_id')!=row['runner_id']:
        raise CheckpointFailure('LOCAL_ROOT_OWNER_MISMATCH')

    def visit(node):
        if node.get('identity')!=expected:
            raise CheckpointFailure('LOCAL_EXECUTION_IDENTITY_MISMATCH')
        for call_id,tool in (node.get('tools') or {}).items():
            call = tool.get('call') or {}
            if call.get('name')==BossInterviewNotifyTool.name:
                if call.get('id')!=call_id:
                    raise CheckpointFailure('LOCAL_NOTIFY_OWNER_MISMATCH')
                yield node,call_id
        for fact in (node.get('children') or {}).values():
            child = fact.get('checkpoint')
            # The shared local owner walker has already verified the exact
            # native unstarted task/profile/parent-call proof before this port.
            if child is None and fact.get('unstarted') is True:
                continue
            if not isinstance(child,dict) or child.get('execution_id')!=fact.get('execution_id'):
                raise CheckpointFailure('LOCAL_CHILD_OWNER_MISMATCH')
            yield from visit(child)
    yield from visit(root)


def cancellation_facts(row):
    """Unknown HTTP cannot be hidden behind an empty desktop-invocation list."""
    return [dict(kind='domain',known=report['known'],
                 error_code=report['error_code'])
        for node,call_id in notification_nodes(row)
        for report in [delivery_facts(row,node,call_id)]]


def assert_notification_audit(cursor,row,node,call_id,intent):
    # Current configuration is intentionally not read: this is the original
    # known send's audit, not permission to POST to a new/current target.
    if original_log_intent(row,node,call_id)!=intent:
        raise CheckpointFailure('LOCAL_NOTIFY_LOG_OWNER_MISMATCH')


def complete_cancellation_audits(cursor,row,attempt):
    """Append known original delivery logs and private phase facts in this TX.

    Return a patched checkpoint only; the common cancellation repository owns
    its one CAS/revision update and transaction commit. Unknown delivery has no
    audit completion and must keep the claim/resources for verification.
    """
    lock_runner(cursor,attempt.runner_id,attempt)
    if not row['cancel_requested']:
        raise CheckpointFailure('LOCAL_USER_CANCEL_NOT_REQUESTED')
    checkpoint = copy.deepcopy(row.get('checkpoint') or {})
    working = {**row,'checkpoint':checkpoint}
    changed = False
    for node,call_id in notification_nodes(working):
        report = delivery_facts(working,node,call_id)
        if not report['known'] or not report['attempted']:
            continue
        intent = original_log_intent(working,node,call_id)
        phase = LocalPhase(node['execution_id'],call_id,'notify.log',0)
        key = phase.key(row['runner_id'])
        facts = node['resources']['local_domain_phases']
        previous = facts.get(key)
        if previous is not None:
            if previous.get('phase')!='completed' or previous.get('request')!=intent:
                raise CheckpointFailure('LOCAL_NOTIFY_LOG_OWNER_MISMATCH')
            continue
        assert_notification_audit(cursor,working,node,call_id,intent)
        result = write_notify_log(cursor,working,intent)
        lock_runner(cursor,attempt.runner_id,attempt)
        assert_notification_audit(cursor,working,node,call_id,intent)
        facts[key] = dict(runner_id=row['runner_id'],execution_id=node['execution_id'],
            tool_call_id=call_id,branch=phase.branch,ordinal=phase.ordinal,
            intent_digest=digest(intent),request=intent,authorized_attempt=attempt.number,
            phase='completed',result=result)
        changed = True
    lock_runner(cursor,attempt.runner_id,attempt)
    return checkpoint if changed else None
