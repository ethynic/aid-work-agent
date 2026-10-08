"""Attempt-owned local phase port; tool adapters select business branches.

This owner never chooses tools/devices, retries a proxy, or prices a domain
operation. All checkpoint mutations share the root runner's existing lock.
"""

import asyncio
import copy
from datetime import datetime

from src.core.agent_engine.contracts import CheckpointFailure
from src.local_tools import repository as invocation_repository
from .local_invocations import LocalInvocationRepository, LocalPhase, _execution
from .local_phases import LocalPhaseRepository
from .ownership import LeaseLost, StopRequested


class RunnerLocalLifecycle:
    def __init__(self, control, state, call_id, *, invocations=None, phases=None):
        if not getattr(control, 'durable_owner', False) or call_id not in state.tools:
            raise CheckpointFailure('LOCAL_EXECUTION_OWNER_REQUIRED')
        self.control, self.state, self.call_id = control, state, call_id
        self.root = control
        while hasattr(self.root, 'parent_control'):
            self.root = self.root.parent_control
        self.invocations = invocations or LocalInvocationRepository(self.root.repository.connection_factory)
        self.phases = phases or LocalPhaseRepository(self.root.repository.connection_factory)

    def _phase(self, branch, ordinal):
        return LocalPhase(self.state.execution_id, self.call_id, branch, ordinal)

    def _fail(self, error):
        if isinstance(error, (StopRequested, asyncio.CancelledError)) or getattr(error,'tool_continuation_required',False):
            return
        if self.root.usage_scope is not None:
            self.root.usage_scope.fail(error)

    def _accept(self, row):
        """Apply only our phase facts; do not hydrate other live child state."""
        execution = _execution(row['checkpoint'], self.state.execution_id, row['runner_id'])
        for key in ('local_invocations', 'local_domain_phases'):
            if key in execution.get('resources', {}):
                self.state.resources[key] = copy.deepcopy(execution['resources'][key])
        state, control = self.state, self.control
        while hasattr(control, 'parent_control'):
            fact = control.parent_state.children.get(control.call_id)
            if not fact or fact.get('execution_id') != state.execution_id:
                raise CheckpointFailure('LOCAL_CHILD_OWNER_MISMATCH')
            fact['checkpoint'] = state.checkpoint()
            state, control = control.parent_state, control.parent_control
        self.root.envelope = copy.deepcopy(row['checkpoint'])
        self.root.revision = row['revision']
        self.root.snapshot = copy.deepcopy(row['public_snapshot'])
        self.root.cancel_requested = row['cancel_requested']
        self.root.pause_requested = row.get('pause_requested', False)

    @staticmethod
    def validate_invocation(identity, runner_id, phase, fact, invocation):
        reference = dict(runner_id=runner_id,
            execution_id=phase.execution_id, tool_call_id=phase.tool_call_id,
            branch=phase.branch, ordinal=phase.ordinal)
        request = fact.get('request') or {}
        if (any(fact.get(key) != value for key, value in reference.items())
                or not invocation or str(invocation.get('id')) != fact.get('invocation_id')):
            raise CheckpointFailure('LOCAL_INVOCATION_OWNER_VERIFICATION_REQUIRED')
        expected = dict(tenant_id=identity.tenant_id, user_id=identity.user_id,
            session_id=identity.session_id, device_id=request.get('device_id'),
            tool_name=request.get('tool_name'), arguments_json=request.get('arguments'),
            provider_key=request.get('provider_key'), business_kind='agent_runner',
            business_ref=reference, dedupe_key=phase.key(runner_id),
            authorization_epoch=request.get('authorization_epoch'),
            execution_lane=request.get('execution_lane'))
        for key, value in expected.items():
            actual = invocation.get(key)
            if key in {'device_id', 'tenant_id', 'user_id', 'session_id'}:
                actual = str(actual) if actual is not None else None
            if actual != value:
                raise CheckpointFailure('LOCAL_INVOCATION_OWNER_VERIFICATION_REQUIRED')
        deadline = invocation.get('deadline_at')
        expected_deadline = datetime.fromisoformat(request['deadline_at']) if request.get('deadline_at') else None
        if deadline != expected_deadline:
            raise CheckpointFailure('LOCAL_INVOCATION_OWNER_VERIFICATION_REQUIRED')

    async def saved_phase(self, *, branch, ordinal):
        """Read original facts, even when the original device is now inactive."""
        phase = self._phase(branch, ordinal)
        try:
            async with self.root.lock:
                self.root._check_usage()
                if self.root.stopped:
                    raise LeaseLost('RUNNER_WORKER_STOPPED')
                fact = copy.deepcopy((self.state.resources.get('local_invocations') or {}).get(
                    phase.key(self.root.attempt.runner_id)))
                if fact is None:
                    return None
                invocation = await asyncio.to_thread(invocation_repository.get_invocation,
                    fact['invocation_id'], self.state.identity.tenant_id)
                self.validate_invocation(self.state.identity, self.root.attempt.runner_id,
                    phase, fact, invocation)
                return {**fact, 'invocation': invocation}
        except BaseException as error:
            self._fail(error)
            raise

    async def authorize_operation(self):
        """Check the current original owner before selecting a new operation."""
        await self.control.authorize_dispatch(self.state.profile_id)

    async def bind_invocation(self, *, branch, ordinal, tool_name, device, arguments,
                              provider_key=None, deadline_at=None, authorization_epoch=None,
                              execution_lane='standard'):
        """New IO uses fresh permission; existing phase never picks a new device."""
        phase = self._phase(branch, ordinal)
        try:
            original = await self.saved_phase(branch=branch, ordinal=ordinal)
            if original is not None:
                request = original['request']
                if request['tool_name'] != tool_name or request['arguments'] != arguments or request['provider_key'] != provider_key:
                    raise CheckpointFailure('LOCAL_PHASE_INTENT_CHANGED')
                return original['invocation']
            await self.control.authorize_dispatch(self.state.profile_id)
            # Commit this exact leaf before the cursor owner verifies its call.
            await self.control.save(self.state, 'local_before_bind')
            async with self.root.lock:
                self.root._check_usage()
                if self.root.stopped:
                    raise LeaseLost('RUNNER_WORKER_STOPPED')
                from src.local_tools.device_policy import device_ready_error
                row = await asyncio.to_thread(self.invocations.bind,
                    self.root.attempt, self.root.revision, phase,
                    device_id=str(device['id']), tool_name=tool_name, arguments=arguments,
                    provider_key=provider_key, deadline_at=deadline_at,
                    authorization_epoch=authorization_epoch, execution_lane=execution_lane,
                    device_policy=lambda current, now: device_ready_error(current, tool_name, now))
                self._accept(row)
            original = await self.saved_phase(branch=branch, ordinal=ordinal)
            return original['invocation']
        except BaseException as error:
            if isinstance(error, StopRequested) and not (self.state.resources.get('local_invocations') or {}).get(
                    phase.key(self.root.attempt.runner_id)):
                # The atomically fenced enqueue did not commit. A user pause
                # remains a prepared leaf rather than an unknown desktop write.
                self.state.tools[self.call_id].phase = 'prepared'
                await self.control.save(self.state, 'local_not_dispatched')
            self._fail(error)
            raise

    async def _domain(self, *, branch, ordinal, intent, writer, observation=False,rearm_model=False,completion_proof=None,dispatch_proof=None):
        """Domain adapter supplies a cursor-only writer for one business effect."""
        phase = self._phase(branch, ordinal)
        try:
            if not observation:
                previous = await self.saved_domain(branch=branch,ordinal=ordinal)
                # Reading a committed business effect grants no new dispatch.
                observation = previous is not None and previous.get('phase')=='completed'
            if not observation:
                await self.control.authorize_dispatch(self.state.profile_id)
                await self.control.save(self.state, 'local_before_domain')
            async with self.root.lock:
                self.root._check_usage()
                if self.root.stopped:
                    raise LeaseLost('RUNNER_WORKER_STOPPED')
                row, fact = await asyncio.to_thread(self.phases.commit,
                    self.root.attempt, self.root.revision, phase, intent, writer,
                    observation=observation,rearm_model=rearm_model,completion_proof=completion_proof,dispatch_proof=dispatch_proof)
                self._accept(row)
                return copy.deepcopy(fact['result'])
        except BaseException as error:
            self._fail(error)
            raise

    async def commit_domain(self, *, branch, ordinal, intent, writer):
        return await self._domain(branch=branch,ordinal=ordinal,intent=intent,writer=writer)

    async def commit_postprocess(self, *, branch, ordinal, intent, writer):
        """Only an original physical sent ACK permits this audit write."""
        if branch=='notify.log' and ordinal==0:
            from src.local_tools.recruiting_writers import write_notify_log
            if writer is not write_notify_log:
                raise CheckpointFailure('LOCAL_POSTPROCESS_NOT_AUTHORIZED')
            from .notify_audit import assert_notification_audit
            proof = assert_notification_audit
        elif branch in {'send.comm_binding','send.comm_log'} and ordinal==0:
            from .sent_message_proof import assert_sent_message
            proof = assert_sent_message
        else:
            raise CheckpointFailure('LOCAL_POSTPROCESS_NOT_AUTHORIZED')
        return await self._domain(branch=branch,ordinal=ordinal,intent=intent,
            writer=writer,observation=True,completion_proof=lambda cursor,row,node,request:
                proof(cursor,row,node,self.call_id,request))

    async def observe_domain(self, *, branch, ordinal, intent, writer):
        """Save the original started operation's outcome; grant no new IO."""
        return await self._domain(branch=branch,ordinal=ordinal,intent=intent,
            writer=writer,observation=True)

    async def start_domain(self, *, branch, ordinal, intent):
        proof = None
        if branch in {'notify.markdown','notify.mention'}:
            from .notify_facts import assert_notification_dispatch
            proof = lambda cursor,row,node,request: assert_notification_dispatch(
                cursor,row,node,self.call_id,branch,ordinal,request)
        return await self._domain(branch=branch,ordinal=ordinal,intent=intent,writer=None,
            dispatch_proof=proof)

    async def saved_domain(self, *, branch, ordinal):
        phase = self._phase(branch,ordinal)
        async with self.root.lock:
            self.root._check_usage()
            if self.root.stopped:
                raise LeaseLost('RUNNER_WORKER_STOPPED')
            fact = (self.state.resources.get('local_domain_phases') or {}).get(phase.key(self.root.attempt.runner_id))
            if fact is None:
                return None
            expected = dict(runner_id=self.root.attempt.runner_id,execution_id=self.state.execution_id,
                tool_call_id=self.call_id,branch=branch,ordinal=ordinal)
            if any(fact.get(key)!=value for key,value in expected.items()):
                error = CheckpointFailure('LOCAL_PHASE_OWNER_MISMATCH')
                self._fail(error)
                raise error
            return copy.deepcopy(fact)

    async def complete_phase(self, *, branch, ordinal, intent, result):
        # Observation only; actual domain effects use commit_domain's writer.
        previous = await self.saved_domain(branch=branch,ordinal=ordinal)
        method = self.observe_domain if previous is not None else self.commit_domain
        return await method(branch=branch,ordinal=ordinal,intent=intent,
            writer=lambda cursor,row,request: result)

    async def model_phase(self, *, branch, ordinal, intent, invoke, purpose='llm'):
        """Domain response ownership is separate from Engine model steps."""
        from src.local_tools.durable_flow import LocalContinuationRequired
        from src.llm.call_observer import provider_call_purpose
        from .domain_usage import domain_model_permit
        if purpose not in {'llm','resume_recognition_covered'}:
            raise CheckpointFailure('LOCAL_MODEL_PURPOSE_INVALID')
        intent = {**intent,'model_purpose':purpose,'model_phase_version':1}
        previous = await self.saved_domain(branch=branch,ordinal=ordinal)
        if previous is not None:
            if previous['request'] != intent:
                raise LocalContinuationRequired('LOCAL_PHASE_INTENT_CHANGED')
            if previous['phase']=='completed':
                return copy.deepcopy(previous['result'])
            def unissued():
                with self.root.repository.connection_factory() as connection:
                    cursor = connection.cursor()
                    cursor.execute('''SELECT 1 FROM agent_runner_usage_receipts WHERE runner_id=%s
                        AND execution_id=%s AND tool_call_id=%s AND purpose=%s LIMIT 1''',
                        (self.root.attempt.runner_id,self.state.execution_id,self.call_id,
                         'domain:'+self._phase(branch,ordinal).key(self.root.attempt.runner_id)+':'+purpose))
                    return cursor.fetchone() is None
            if not await asyncio.to_thread(unissued):
                raise LocalContinuationRequired('LOCAL_MODEL_RESPONSE_VERIFICATION_REQUIRED')
            # Receipt.start always precedes provider IO. Under the root lock a
            # second check proves this original phase has never dispatched.
            await self._domain(branch=branch,ordinal=ordinal,intent=intent,writer=None,rearm_model=True)
        else:
            await self.start_domain(branch=branch,ordinal=ordinal,intent=intent)
        try:
            with domain_model_permit(self.root.attempt,self._phase(branch,ordinal),purpose), provider_call_purpose(purpose):
                response = await invoke()
        except Exception as error:
            if getattr(error,'authoritative_storage_failure',False):
                raise
            # No response proof: a replay is a new physical call, not recovery.
            raise LocalContinuationRequired('LOCAL_MODEL_RESPONSE_VERIFICATION_REQUIRED') from error
        if not isinstance(response,dict):
            raise LocalContinuationRequired('LOCAL_MODEL_RESPONSE_VERIFICATION_REQUIRED')
        result = {key:response.get(key) for key in ('content','usage','provider','model','_runner_receipt_id')}
        if not result['_runner_receipt_id']:
            error = CheckpointFailure('LOCAL_MODEL_RECEIPT_VERIFICATION_REQUIRED')
            self._fail(error)
            raise error
        def writer(cursor,row,request):
            cursor.execute('SELECT * FROM agent_runner_usage_receipts WHERE receipt_id=%s AND runner_id=%s',
                (result['_runner_receipt_id'],row['runner_id']))
            receipt = dict(cursor.fetchone() or {})
            expected = dict(execution_id=self.state.execution_id,tool_call_id=self.call_id,
                owner='llm',authorized_attempt=self.root.attempt.number,provider=result['provider'],model=result['model'],
                purpose='domain:'+self._phase(branch,ordinal).key(row['runner_id'])+':'+purpose)
            if any(receipt.get(key)!=value for key,value in expected.items()):
                raise CheckpointFailure('LOCAL_MODEL_RECEIPT_OWNER_MISMATCH')
            if receipt.get('phase') not in {'observed','unknown'}:
                raise CheckpointFailure('LOCAL_MODEL_RECEIPT_OWNER_MISMATCH')
            if isinstance(result.get('usage'),dict) and result['usage'].get('_runner_receipt_id') not in {
                    None,result['_runner_receipt_id']}:
                raise CheckpointFailure('LOCAL_MODEL_RECEIPT_OWNER_MISMATCH')
            return result
        return await self.observe_domain(branch=branch,ordinal=ordinal,intent=intent,writer=writer)
