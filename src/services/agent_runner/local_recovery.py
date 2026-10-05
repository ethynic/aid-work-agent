"""Proof for original invocations; no tool heuristics or device re-selection."""

import psycopg2
import copy
import hashlib
from datetime import datetime
from loguru import logger

from src.core.agent_engine.contracts import CheckpointFailure, Identity
from src.local_tools import repository
from .contracts import RunnerError,canonical_json
from .local_invocations import LocalPhase
from .local_owner import RunnerLocalLifecycle
from .ownership import lock_runner
from .persistence_limits import capped_checkpoint_dumps


def owned_invocations(node, runner_id):
    """The original execution/call/branch/ordinal must agree with its map key."""
    refs = node.get('resources', {}).get('local_invocations') or {}
    ids = set()
    for key, fact in refs.items():
        phase = LocalPhase(node['execution_id'], fact['tool_call_id'], fact['branch'], fact['ordinal'])
        tool = node.get('tools', {}).get(phase.tool_call_id) or {}
        if key != phase.key(runner_id) or (tool.get('call') or {}).get('id') != phase.tool_call_id:
            raise CheckpointFailure('LOCAL_INVOCATION_OWNER_VERIFICATION_REQUIRED')
        ids.add(fact['invocation_id'])
        yield Identity(**node['identity']), phase, fact
    for fact in node.get('tools', {}).values():
        if fact.get('invocation_id') and fact.get('phase') != 'completed' and fact['invocation_id'] not in ids:
            # Only the old registered override's original first dispatch can
            # use this cancellation proof. It grants no recovery permission.
            yield Identity(**node['identity']), None, fact
    for call_id, child in node.get('children', {}).items():
        checkpoint = child.get('checkpoint')
        if checkpoint is None and child.get('unstarted') is True:
            task = child.get('task_record') or {}
            parent_call = (node.get('tools', {}).get(call_id) or {}).get('call') or {}
            if (child.get('status') == 'unstarted' and child.get('execution_id')
                    and task.get('execution_id') == child['execution_id'] and task.get('task_id')
                    and task.get('status') in {'pending', 'running'}
                    and not task.get('completed_at') and task.get('result') is None
                    and parent_call.get('id') == call_id
                    and task.get('subagent_name')
                    and task['subagent_name'] == (parent_call.get('arguments') or {}).get('subagent_name')
                    and child.get('profile_id') and child.get('profile_fingerprint')):
                continue
            raise CheckpointFailure('LOCAL_CHILD_OWNER_MISMATCH')
        if not isinstance(checkpoint, dict) or checkpoint.get('execution_id') != child.get('execution_id'):
            raise CheckpointFailure('LOCAL_CHILD_OWNER_MISMATCH')
        yield from owned_invocations(checkpoint, runner_id)


def validate_legacy_cancel(identity, fact, invocation):
    from src.local_tools.proxy_tool import LOCAL_PROXY_TOOL_CLASSES, LocalToolProxyTool
    from src.tools.executor import normalize_provided_parameters
    from pydantic import TypeAdapter
    call = fact.get('call') or {}
    cls = next((cls for cls in LOCAL_PROXY_TOOL_CLASSES if cls.name == call.get('name')), None)
    if cls is None or cls.execute is LocalToolProxyTool.execute:
        raise CheckpointFailure('LOCAL_INVOCATION_OWNER_VERIFICATION_REQUIRED')
    try:
        arguments = TypeAdapter(dict).dump_python(
            normalize_provided_parameters(cls, call.get('arguments') or {}), mode='json')
    except Exception as error:
        raise CheckpointFailure('LOCAL_INVOCATION_OWNER_VERIFICATION_REQUIRED') from error
    expected = dict(tenant_id=identity.tenant_id, user_id=identity.user_id,
        session_id=identity.session_id, tool_name=call['name'], arguments_json=arguments)
    if (str(invocation.get('id')) != fact['invocation_id']
            or any(invocation.get(key) != value for key, value in expected.items())
            or invocation.get('business_kind') is not None or invocation.get('business_ref')
            or invocation.get('dedupe_key')):
        raise CheckpointFailure('LOCAL_INVOCATION_OWNER_VERIFICATION_REQUIRED')


class LocalRecoveryProof:
    def can_restore(self, row, state, fact):
        from src.local_tools.proxy_tool import LOCAL_PROXY_TOOL_CLASSES, LocalToolProxyTool
        from src.local_tools.domain_flow import supports_owned_device_class, cloud_read_branch, domain_classes
        cls = next((cls for cls in LOCAL_PROXY_TOOL_CLASSES if cls.name == fact.call.name
                    and (supports_owned_device_class(cls) or cls in domain_classes())), None)
        if cls is None:
            return False
        from src.local_tools.proxy_tool import BossInterviewNotifyTool
        if cls is BossInterviewNotifyTool:
            from .notify_facts import delivery_facts
            return delivery_facts(row,state.checkpoint(),fact.call.id)['known']
        from src.tools.executor import normalize_provided_parameters
        arguments = normalize_provided_parameters(cls,fact.call.arguments)
        cloud_branch = cloud_read_branch(cls,arguments)
        if cloud_branch is not None:
            from .domain_recovery import cloud_read_proof
            return cloud_read_proof(row,state,fact,cloud_branch,arguments)
        refs = state.resources.get('local_invocations') or {}
        phase = LocalPhase(state.execution_id, fact.call.id, 'main', 0)
        ref = refs.get(phase.key(row['runner_id']))
        if ref is None:
            return False
        from src.tools.executor import normalize_provided_parameters
        if (ref.get('request') or {}).get('arguments')!=normalize_provided_parameters(cls,fact.call.arguments):
            raise RunnerError('LOCAL_PHASE_INTENT_CHANGED',409)
        original_device = ref['request']['device_id']
        from src.local_tools.proxy_tool import BossOverlayInspectTool,BossOverlayDismissTool
        allowed = {'main':cls.name,'heal.inspect':BossOverlayInspectTool.name,
            'heal.dismiss':BossOverlayDismissTool.name,'heal.retry':cls.name}
        for key,ref in refs.items():
            if ref.get('tool_call_id')!=fact.call.id:
                continue
            phase = LocalPhase(state.execution_id,fact.call.id,ref['branch'],ref['ordinal'])
            request = ref.get('request') or {}
            if (key!=phase.key(row['runner_id']) or ref['ordinal']!=0
                    or request.get('tool_name')!=allowed.get(ref['branch'])
                    or request.get('device_id')!=original_device
                    or ref['branch'] in {'main','heal.retry'} and request.get('arguments')!=normalize_provided_parameters(cls,fact.call.arguments)
                    or ref['branch']=='heal.inspect' and request.get('arguments')!={}):
                raise RunnerError('LOCAL_INVOCATION_OWNER_VERIFICATION_REQUIRED',409)
            invocation = repository.get_invocation(ref['invocation_id'],row['tenant_id'])
            try:
                RunnerLocalLifecycle.validate_invocation(state.identity,row['runner_id'],phase,ref,invocation)
            except CheckpointFailure as error:
                raise RunnerError('LOCAL_INVOCATION_OWNER_VERIFICATION_REQUIRED',409) from error
            if invocation['state']=='unknown' or invocation.get('effect')=='unknown':
                return False
            if ref['branch']=='main' and invocation['state']=='failed':
                from src.local_tools.durable_flow import healing_requires_verification
                if healing_requires_verification(cls,invocation.get('error_code'),invocation.get('effect')):
                    return False
            deadline = invocation.get('deadline_at')
            if not deadline:
                return False
            if invocation['state'] not in repository.TERMINAL_STATES and datetime.now(deadline.tzinfo)>=deadline:
                raise RunnerError('LOCAL_DEADLINE_VERIFICATION_REQUIRED',409)
        from .domain_recovery import validate_domain_phases
        if not validate_domain_phases(row,state,fact,cls,arguments):
            return False
        for key,phase_fact in (state.resources.get('local_domain_phases') or {}).items():
            if phase_fact.get('tool_call_id')!=fact.call.id or not phase_fact['branch'].startswith('heal.'):
                continue
            phase = LocalPhase(state.execution_id,fact.call.id,phase_fact['branch'],phase_fact['ordinal'])
            expected = dict(runner_id=row['runner_id'],execution_id=state.execution_id,tool_call_id=fact.call.id)
            request = phase_fact.get('request') or {}
            if (key!=phase.key(row['runner_id']) or any(phase_fact.get(k)!=v for k,v in expected.items())
                    or phase.branch not in {'heal.policy','heal.choice','heal.fee'}
                    or phase.ordinal not in ({0,1} if phase.branch=='heal.choice' else {0})
                    or phase_fact.get('intent_digest')!=hashlib.sha256(canonical_json(request).encode()).hexdigest()):
                raise RunnerError('LOCAL_PHASE_OWNER_VERIFICATION_REQUIRED',409)
            if phase.branch=='heal.choice':
                if (request.get('model_purpose')!='llm' or request.get('model_phase_version')!=1
                        or type(phase_fact.get('authorized_attempt')) is not int
                        or not 0<phase_fact['authorized_attempt']<=row['attempt']):
                    raise RunnerError('LOCAL_PHASE_OWNER_VERIFICATION_REQUIRED',409)
                if phase_fact.get('phase')=='started':
                    with repository.get_db_connection() as connection:
                        cursor = connection.cursor()
                        cursor.execute('''SELECT 1 FROM agent_runner_usage_receipts WHERE runner_id=%s
                            AND execution_id=%s AND tool_call_id=%s AND purpose=%s LIMIT 1''',
                            (row['runner_id'],state.execution_id,fact.call.id,'domain:'+key+':llm'))
                        if cursor.fetchone():
                            return False
                    continue
                if phase_fact.get('phase')!='completed':
                    return False
                result = phase_fact.get('result') or {}
                with repository.get_db_connection() as connection:
                    cursor = connection.cursor()
                    cursor.execute('SELECT * FROM agent_runner_usage_receipts WHERE runner_id=%s AND receipt_id=%s',
                        (row['runner_id'],result.get('_runner_receipt_id')))
                    receipt = dict(cursor.fetchone() or {})
                expected = dict(execution_id=state.execution_id,tool_call_id=fact.call.id,owner='llm',
                    provider=result.get('provider'),model=result.get('model'),purpose='domain:'+key+':llm',
                    authorized_attempt=phase_fact.get('authorized_attempt'))
                if (any(receipt.get(k)!=v for k,v in expected.items())
                        or receipt.get('phase') not in {'observed','unknown'}):
                    raise RunnerError('LOCAL_MODEL_RECEIPT_OWNER_MISMATCH',409)
            elif phase_fact.get('phase')!='completed':
                return False
        return True


def _cancel_facts(cursor, row, *, request):
    execution = (row.get('checkpoint') or {}).get('execution')
    if not execution:
        return []
    if execution.get('execution_id') != row['runner_id']:
        raise CheckpointFailure('LOCAL_ROOT_OWNER_MISMATCH')
    expected_identity = {key:row[key] for key in ('tenant_id','user_id','session_id','source','session_kind')}
    facts = []
    for identity, phase, fact in owned_invocations(execution, row['runner_id']):
        if vars(identity) != expected_identity:
            raise CheckpointFailure('LOCAL_EXECUTION_IDENTITY_MISMATCH')
        # Original bindings and terminal results are immutable. Observation
        # must not wait on a desktop's result transaction or stall other jobs.
        cursor.execute('SELECT * FROM local_tool_invocations WHERE id=%s AND tenant_id=%s'
            + (' FOR UPDATE' if request else ''),
            (fact['invocation_id'], row['tenant_id']))
        invocation = dict(cursor.fetchone() or {})
        if phase is None:
            validate_legacy_cancel(identity, fact, invocation)
        else:
            RunnerLocalLifecycle.validate_invocation(identity, row['runner_id'], phase, fact, invocation)
        if request:
            repository.request_cancel_in_tx(cursor, fact['invocation_id'], row['tenant_id'])
            cursor.execute('SELECT * FROM local_tool_invocations WHERE id=%s AND tenant_id=%s',
                (fact['invocation_id'], row['tenant_id']))
            invocation = dict(cursor.fetchone())
        facts.append({key:invocation.get(key) for key in ('id','state','effect','deadline_at')})
    from .notify_audit import cancellation_facts
    facts.extend(cancellation_facts(row))
    from .browser_facts import cancellation_facts as browser_cancellation_facts
    facts.extend(browser_cancellation_facts(cursor,row))
    return facts


def cancel_completion_status(facts):
    pending = False
    for fact in facts:
        if fact.get('kind')=='domain':
            if not fact['known']:
                # Keep the original domain reason in its private HTTP fact;
                # the shared cancellation hold/release protocol is neutral.
                return False,'LOCAL_CANCEL_EFFECT_VERIFICATION_REQUIRED'
            continue
        if fact['state'] == 'unknown' or fact['effect'] == 'unknown':
            return False, 'LOCAL_CANCEL_EFFECT_VERIFICATION_REQUIRED'
        if fact['state'] not in repository.TERMINAL_STATES:
            deadline = fact['deadline_at']
            if deadline is None or datetime.now(deadline.tzinfo) >= deadline:
                return False, 'LOCAL_CANCEL_DEADLINE_VERIFICATION_REQUIRED'
            pending = True
    return (False, 'LOCAL_CANCEL_ACK_PENDING') if pending else (True,None)


def cancel_owned_invocations(connection_factory, attempt, *, request=True, complete_audit=False):
    """User cancellation, root fence and complete original bindings in one TX."""
    try:
        with connection_factory() as connection:
            cursor = connection.cursor()
            row = lock_runner(cursor, attempt.runner_id, attempt)
            if not row['cancel_requested']:
                raise CheckpointFailure('LOCAL_USER_CANCEL_NOT_REQUESTED')
            facts = _cancel_facts(cursor,row,request=request)
            from .notify_audit import complete_cancellation_audits
            checkpoint = complete_cancellation_audits(cursor,row,attempt) if complete_audit else None
            if checkpoint is not None:
                cursor.execute('UPDATE agent_runners SET checkpoint=%s::jsonb,revision=revision+1,updated_at=clock_timestamp() WHERE runner_id=%s RETURNING *',
                    (capped_checkpoint_dumps(checkpoint),attempt.runner_id))
                from .repository import decoded
                row = decoded(cursor.fetchone())
            # Lock waits consumed real time; a stale owner cannot commit a cancel.
            lock_runner(cursor, attempt.runner_id, attempt)
            connection.commit()
            return CancellationFacts(facts,row)
    except psycopg2.Error as error:
        raise CheckpointFailure('LOCAL_CANCEL_STORAGE_FAILED') from error


def release_ready_cancellations(connection_factory, *, limit=16, after_queue_order=0):
    """A bounded late-result sweep; never dispatch or request device cancellation."""
    from .repository import decoded
    with connection_factory() as connection:
        cursor = connection.cursor()
        cursor.execute("""SELECT runner_id,queue_order FROM agent_runners WHERE status='waiting'
            AND cancel_requested AND worker_id IS NULL AND lease_until IS NULL
            AND checkpoint ? 'cancel_completion_blocked'
            AND checkpoint->'cancel_completion_blocked'->>'ready'='false'
            AND queue_order>%s ORDER BY queue_order LIMIT %s""",(after_queue_order,limit))
        candidates = cursor.fetchall()
    for candidate in candidates:
        runner_id = candidate['runner_id']
        try:
            with connection_factory() as connection:
                cursor = connection.cursor()
                cursor.execute('SELECT * FROM agent_runners WHERE runner_id=%s FOR UPDATE SKIP LOCKED',(runner_id,))
                row = decoded(cursor.fetchone())
                if not row:
                    continue
                previous = copy.deepcopy(row)
                checkpoint = row.get('checkpoint') or {}
                blocked = checkpoint.get('cancel_completion_blocked') or {}
                if (row['status'] != 'waiting' or not row['cancel_requested']
                        or row['worker_id'] is not None or row['lease_until'] is not None
                        or checkpoint.get('pending_finalization')
                        or checkpoint.get('finalization_intent') != 'cancel_only'
                        or blocked.get('attempt') != row['attempt']
                        or blocked.get('revision') != row['revision']):
                    continue
                facts = _cancel_facts(cursor,row,request=False)
                if not cancel_completion_status(facts)[0]:
                    continue
                checkpoint['cancel_completion_blocked'] = {**blocked,'ready':True,'revision':row['revision']+1}
                cursor.execute("""UPDATE agent_runners SET checkpoint=%s::jsonb,revision=revision+1,
                    view_revision=view_revision+1,updated_at=clock_timestamp()
                    WHERE runner_id=%s AND status='waiting' AND attempt=%s AND revision=%s
                    AND worker_id IS NULL AND lease_until IS NULL""",
                    (capped_checkpoint_dumps(checkpoint),runner_id,row['attempt'],row['revision']))
                from .event_repository import EventRepository
                EventRepository.notify_in_tx(cursor,previous,cancellation_ready=True)
                connection.commit()
        except CheckpointFailure as error:
            logger.warning('Local cancellation remains blocked kind={}',type(error).__name__)
    return candidates[-1]['queue_order'] if len(candidates)==limit else 0


class CancellationFacts(list):
    """Original physical facts plus the same transaction's owner checkpoint."""
    def __init__(self,facts,row):
        super().__init__(facts)
        self.row = row
