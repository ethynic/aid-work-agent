"""One serialized checkpoint owner shared by a runner and all owned children."""

import asyncio
import copy
import time
from datetime import datetime, timezone
from .ownership import LeaseLost, StopRequested
from .wait_identity import owned_wait_id
from src.core.agent_engine.contracts import CheckpointFailure


class DurableControl:
    durable_owner = True

    def __init__(self, repository, attempt, row):
        self.repository, self.attempt = repository, attempt
        self.revision = row['revision']
        self.snapshot = copy.deepcopy(row['public_snapshot'])
        self.lock = asyncio.Lock()
        self.cancel_requested = row['cancel_requested']
        self.pause_requested = row.get('pause_requested',False)
        self.stopped = False
        self.state = None
        self.envelope = copy.deepcopy(row.get('checkpoint') or {})
        self.usage_scope = None
        self.plan_manager = None
        self.authorization_check = None
        self.prepared_source = None
        self.input_preparation = None
        self.tool_started = {}
        self.started_at = time.monotonic()
        self.active_attempt = row['status'] == 'running'

    def capture_duration(self, envelope=None):
        envelope = self.envelope if envelope is None else envelope
        if self.active_attempt:
            envelope.setdefault('attempt_durations_ms', {})[str(self.attempt.number)] = max(
                0, int((time.monotonic()-self.started_at)*1000))
        envelope['execution_duration_ms'] = sum(envelope.get('attempt_durations_ms', {}).values())
        return envelope['execution_duration_ms']

    async def _authorize_execution(self, profile_id=None):
        if self.authorization_check is not None:
            await self.authorization_check(profile_id)

    def _check_usage(self):
        if self.usage_scope is not None:
            self.usage_scope.check()

    def command(self):
        return 'cancel' if self.cancel_requested else 'pause' if self.pause_requested or self.stopped else None

    async def authorize_dispatch(self, profile_id=None):
        self._check_usage()
        if self.stopped:
            raise LeaseLost('RUNNER_WORKER_STOPPED')
        await self._authorize_execution(profile_id)
        try:
            kwargs={'source_prepared':self.prepared_source} if self.prepared_source is not None else {}
            row = await asyncio.to_thread(self.repository.assert_dispatch, self.attempt,**kwargs)
        except StopRequested as error:
            if error.command == 'pause':
                self.pause_requested = True
            else:
                self.cancel_requested = True
            raise
        self.pause_requested = row.get('pause_requested',False)

    def interrupt(self):
        # Internal shutdown/lease loss is not a user cancellation of a device job.
        self.stopped = True

    def _capture(self, state):
        self.capture_duration()
        self.decorate_wait(state)
        self.state = state
        self.envelope['version'] = 1
        self.envelope.pop('unstarted', None)
        pending_inputs = self.envelope.get('pending_root_inputs') or []
        queued_ids = {(message.get('metadata') or {}).get('control_id')
                      for message in state.followup_messages + state.messages if message.get('role')=='user'}
        if any((message.get('metadata') or {}).get('control_id') not in queued_ids for message in pending_inputs):
            raise CheckpointFailure('CONTINUATION_INPUT_NOT_ATTACHED')
        self.envelope.pop('pending_root_inputs', None)
        inputs = state.resources.get('continuation_inputs',{})
        for message in state.messages:
            if message.get('role') == 'user':
                control_id = (message.get('metadata') or {}).get('control_id')
                if control_id in inputs:
                    inputs[control_id] = 'appended'
        self.envelope['execution'] = state.checkpoint()
        self.snapshot.update(output=state.output, images=copy.deepcopy(state.images),
                             waiting=copy.deepcopy(state.waiting), iteration=state.iteration)
        return copy.deepcopy(self.envelope), copy.deepcopy(self.snapshot)

    def decorate_wait(self, state):
        if state.waiting and state.waiting.get('kind') in {'child_wait','child_clarification'}:
            child = state.children.get(state.waiting.get('tool_call_id')) or {}
            leaf = (child.get('checkpoint') or {}).get('waiting')
            if leaf:
                state.waiting['child_wait'] = copy.deepcopy(leaf)
        if state.waiting and not state.waiting.get('wait_id'):
            wait = state.waiting
            wait['wait_id'] = owned_wait_id(self.attempt.runner_id, state.execution_id, wait)
            wait['target_execution_id'] = state.execution_id
        if state.waiting and state.waiting.get('kind')=='clarification':
            questions = self.snapshot.setdefault('clarificationQuestions',[])
            if not any(item['wait_id']==state.waiting['wait_id'] for item in questions):
                questions.append({'message_id':state.waiting['wait_id']+':assistant',
                    'wait_id':state.waiting['wait_id'],'text':state.waiting.get('question',''),
                    'created_at':datetime.now(timezone.utc).isoformat()})

    async def _save_locked(self, state, boundary):
        self._check_usage()
        if self.stopped:
            raise LeaseLost('RUNNER_WORKER_STOPPED')
        try:
            if boundary=='before_model' and state.execution_id==self.attempt.runner_id and self.input_preparation is not None:
                await self.input_preparation()
            if boundary in {'before_model','before_tool','child.before_model','child.before_tool'}:
                await self._authorize_execution()
            if self.plan_manager is not None:
                plan = await asyncio.to_thread(self.plan_manager.get_plan,state.identity.session_id)
                self.envelope['business_plan'] = plan.model_dump(mode='json') if plan is not None else None
                state.plan_ref = plan.plan_id if plan is not None else None
            checkpoint, snapshot = self._capture(state)
            kwargs={'source_prepared':self.prepared_source,'input_boundary':boundary} if getattr(self.repository,'input_repository',None) is not None else {}
            row = await asyncio.to_thread(self.repository.save_checkpoint, self.attempt,
                self.revision, checkpoint, snapshot,
                dispatch=boundary in {'before_model', 'before_tool'},**kwargs)
        except StopRequested as error:
            self.pause_requested = error.command == 'pause'
            self.cancel_requested = error.command == 'cancel'
            # This boundary precedes all IO, so the dispatch intent was never
            # committed and remains safe to retry after an acknowledged pause.
            if boundary == 'before_tool' and state.pending:
                state.tools[state.pending[0].id].phase = 'prepared'
            if boundary == 'before_model' and state.model_calls:
                state.model_calls[-1]['phase'] = 'not_started'
            raise
        self.revision = row['revision']
        self.snapshot = copy.deepcopy(row['public_snapshot'])
        self.cancel_requested = row['cancel_requested']
        self.pause_requested = row.get('pause_requested',False)
        # The same CAS may have attached source inputs. Hydrate only the root
        # input projection, never a live child's messages/model/tool phases.
        if (row.get('checkpoint') or {}).get('source_initial_ref'):
            saved=row['checkpoint'].get('execution')
            if saved and state.execution_id==self.attempt.runner_id:
                state.messages=copy.deepcopy(saved['messages'])
                state.followup_messages=copy.deepcopy(saved['followup_messages'])
                state.resources['continuation_inputs']=copy.deepcopy(saved['resources'].get('continuation_inputs',{}))
                if saved['resources'].get('root_continuation_requested') is True:
                    state.resources['root_continuation_requested']=True
                else:
                    state.resources.pop('root_continuation_requested',None)
                if 'source_continuation_intent' in saved['resources']:
                    state.resources['source_continuation_intent']=copy.deepcopy(
                        saved['resources']['source_continuation_intent'])
                else:
                    state.resources.pop('source_continuation_intent',None)
            self.envelope=copy.deepcopy(row['checkpoint'])

    async def save(self, state, boundary):
        async with self.lock:
            # Capture inside the shared lock; a queued old child snapshot cannot
            # overwrite a newer root/child checkpoint or its event projection.
            await self._save_locked(state, boundary)

    async def project(self, state, event):
        async with self.lock:
            from src.core.agent_events import extract_downloadable_file
            kind = event.get('type')
            call_id = event.get('toolCallId')
            if kind == 'tool_start' and call_id:
                self.tool_started[call_id] = time.monotonic()
            elif kind == 'tool_result' and call_id and call_id in self.tool_started:
                self.envelope.setdefault('tool_durations',{})[call_id] = int(
                    (time.monotonic()-self.tool_started.pop(call_id))*1000)
            if kind in {'progress','tool_result','tool_start','thinking','clarification'}:
                self.envelope.setdefault('presentation',{}).setdefault('progressMessages',[]).append(copy.deepcopy(event))
                file = extract_downloadable_file(event)
                if file:
                    self.envelope['presentation'].setdefault('downloadableFiles',[]).append(file)
            elif kind == 'verbose':
                entries = self.envelope.setdefault('presentation',{}).setdefault('verboseMessages',[])
                if event.get('eventId') and not any(item['eventId']==event['eventId'] for item in entries):
                    entries.append({key:copy.deepcopy(event.get(key)) for key in ('eventId','data','source','timestamp','delivery')})
            # Public UI projection has no tool arguments, provider payloads,
            # model prompts or private execution resources.
            presentation = self.envelope.get('presentation') or {}
            from .presentation import progress_display, project_user_event
            self.snapshot['progressMessages'] = [
                progress_display(item) for item in presentation.get('progressMessages',[])]
            project_user_event(self.snapshot, event)
            self.snapshot['downloadableFiles'] = copy.deepcopy(presentation.get('downloadableFiles') or [])
            self.snapshot['verboseMessages'] = copy.deepcopy(presentation.get('verboseMessages') or [])
            if event.get('type') == 'progress':
                self.snapshot['progress'] = event.get('data')
            elif event.get('type') == 'response':
                self.snapshot['output'] = event.get('data', state.output)
            await self._save_locked(state, 'event')

    async def park(self, state, status):
        async with self.lock:
            self._check_usage()
            if self.stopped:
                raise LeaseLost('RUNNER_WORKER_STOPPED')
            checkpoint, snapshot = self._capture(state)
            if status == 'paused' and self.pause_requested:
                from .recovery_repository import RecoveryRepository
                row = await asyncio.to_thread(RecoveryRepository(self.repository.connection_factory).acknowledge_pause,
                    self.attempt,self.revision,checkpoint,snapshot)
            else:
                row = await asyncio.to_thread(self.repository.park, self.attempt,
                    self.revision, status, checkpoint, snapshot)
            self.revision = row['revision']
            return row

    async def stage(self, state, result):
        async with self.lock:
            self._check_usage()
            if state is not None:
                checkpoint, snapshot = self._capture(state)
            else:
                self.capture_duration()
                checkpoint, snapshot = copy.deepcopy(self.envelope), copy.deepcopy(self.snapshot)
            result = {**result, 'duration_ms': checkpoint.get('execution_duration_ms', 0)}
            row = await asyncio.to_thread(self.repository.stage_finalization, self.attempt,
                self.revision, checkpoint, result, snapshot)
            self.envelope = row['checkpoint']
            self.revision = row['revision']
            return row


class DurableObserver:
    def __init__(self, control, context_repository=None, trace_collector=None):
        self.control, self.context_repository = control, context_repository
        self.trace_collector = trace_collector

    async def model_usage(self, state, fact):
        # Actual provider facts were committed by the provider port. Engine usage
        # is an execution projection, not a second account/record owner.
        state.usage_watermark = len(state.model_calls)
        if self.context_repository is not None and not fact.get('child_execution_id'):
            count = int(fact['usage'].get('prompt_tokens', 0)) + int(fact['usage'].get('completion_tokens', 0))
            if count:
                # This is an observation of an already dispatched call, not
                # permission to start a new call after a user cancel request.
                if self.control.cancel_requested or self.control.pause_requested or self.control.stopped:
                    return
                await self.control.authorize_dispatch()
                await asyncio.to_thread(self.context_repository.update_context_tokens, count)

    async def event(self, state, event):
        if self.trace_collector is not None:
            self.trace_collector.on_event(event)
        await self.control.project(self.control.state or state, event)
