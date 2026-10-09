"""Independent durable worker; API processes never own its execution tasks."""

import argparse
import asyncio
import copy
import json
import os
from pathlib import Path
import signal
import time
import uuid
from loguru import logger
from src.core.agent_engine.contracts import AgentMode, CheckpointFailure, ExecutionState, Outcome
from .authorization import RunnerAuthorizer
from .contracts import RunnerError
from .durable_control import DurableControl, DurableObserver
from .execution_repository import ExecutionRepository
from .finalizer import RunnerFinalizer
from .ownership import Attempt, LeaseLost, StopRequested
from .profiles import MainProfileCatalog
from .repository import RunnerRepository
from .usage_context import UsageScope, runner_usage_scope
from .usage_repository import UsageRepository
from .source_receipts import SourceUnavailable


class RuntimeFactory:
    def __init__(self, resource_directory=None, profiles=None, browser_process=None):
        self.resource_directory = Path(resource_directory or os.environ.get('AGENT_RUNNER_RESOURCE_DIR', 'storage/agent_runner')).resolve()
        self.profiles = profiles or MainProfileCatalog()
        self.browser_process=browser_process
        self._completion_scan_at=0.0
        self._completion_scan_offset=0

    def recovery_port(self,connection_factory):
        """Install this application's finite capabilities for the scheduler."""
        from .browser_recovery import ApplicationToolRecovery
        return ApplicationToolRecovery(self.browser_process,connection_factory)

    def source_capabilities(self,config,connection_factory):
        from .application_sources import build_source_capabilities
        return build_source_capabilities(config,connection_factory)

    async def open(self):
        """Start installed application observers before any task acquisition."""
        process = self.browser_process
        if process is not None and process['config'].view_enabled:
            from .browser_sidecar import BrowserViewSidecar
            if process.get('sidecar') is not None:
                raise RuntimeError('BROWSER_VIEW_LISTENER_ALREADY_INSTALLED')
            process['sidecar'] = BrowserViewSidecar(process)
            await process['sidecar'].open()

    async def close(self):
        """Close resources installed by this application composition only."""
        if self.browser_process is not None:
            from src.tools.browser.run_manager import close_worker_browser_managers
            sidecar = self.browser_process.get('sidecar')
            if sidecar is not None:
                await sidecar.stop_observations()
            commands=tuple(self.browser_process.get('human_commands',set()))
            if commands:
                _,pending=await asyncio.wait(commands,timeout=35)
                for task in pending:
                    task.cancel()
                await asyncio.gather(*commands,return_exceptions=True)
            try:
                await close_worker_browser_managers(self.browser_process['boot'])
            finally:
                if sidecar is not None:
                    await sidecar.close()

    async def prepare_acquisition(self):
        """Bounded application observations; no execution task or second queue."""
        process=self.browser_process
        if not process or time.monotonic()<self._completion_scan_at:
            return
        self._completion_scan_at=time.monotonic()+1.0
        managers=sorted(process.get('retained_managers',set()),
            key=lambda manager:getattr(getattr(manager,'execution_owner',None),'record',None).run_id)
        start=self._completion_scan_offset
        chosen=managers[start:start+16]
        self._completion_scan_offset=start+len(chosen) if start+len(chosen)<len(managers) else 0
        for manager in chosen:
            owner=getattr(manager,'execution_owner',None)
            if owner and owner.process_resources is process:
                await owner.bridge_completions()

    async def after_park(self,row):
        await self.prepare_acquisition()

    async def request_resource_cancellation(self,attempt):
        """Application capabilities close only original resources of this claim."""
        process=self.browser_process
        if not process:
            return
        from .browser_human_repository import BrowserHumanRepository
        from .contracts import RunnerError
        from .ownership import LeaseLost
        from src.tools.browser.owner_port import BrowserOwnerFailure,HumanActionRejected,browser_resource_close_scope
        for manager in tuple(process.get('retained_managers',set())):
            owner=getattr(manager,'execution_owner',None)
            if (not owner or owner.process_resources is not process or owner.worker_boot!=process['boot']
                    or owner.runner_id!=attempt.runner_id):
                continue
            try:
                repository=BrowserHumanRepository(owner.repository.connection_factory)
                binding=copy.deepcopy(owner._binding(owner.record.tenant_id,owner.record.run_id))
                owner_root,owner_state=owner.root,owner.state
                tenant_id,user_id,run_id=owner.record.tenant_id,owner.record.user_id,owner.record.run_id
                needed=await asyncio.to_thread(repository.assert_cancel_owner,attempt,binding)
                if needed:
                    async def guard(repository=repository,owner=owner,binding=binding,
                                    owner_root=owner_root,owner_state=owner_state,manager=manager):
                        if (owner.root is not owner_root or owner.state is not owner_state
                                or owner.worker_boot!=binding['owner_boot_id']
                                or owner.browser_epoch!=binding['browser_epoch']
                                or owner.state.execution_id!=binding['runner_execution_id']
                                or owner.call_id!=binding['runner_tool_call_id']
                                or manager.execution_owner is not owner):
                            raise HumanActionRejected('BROWSER_CANCEL_OWNER_CHANGED')
                        try:
                            await asyncio.to_thread(repository.assert_cancel_owner,attempt,binding)
                        except (RunnerError,LeaseLost) as error:
                            raise HumanActionRejected('BROWSER_CANCEL_OWNER_CHANGED') from error
                    with browser_resource_close_scope(guard):
                        await manager.request_cancel(tenant_id,user_id,run_id)
            except (RunnerError,LeaseLost,CheckpointFailure,BrowserOwnerFailure,HumanActionRejected):
                # The existing all-tree cancellation proof preserves unknown
                # resources/claim. A requested close is never its own proof.
                continue

    async def create(self, row, principal, control):
        control.browser_process=self.browser_process
        from .runtime.profile import RuntimeResources
        from .runtime.executor import RuntimeExecution
        from .runtime.history_repository import HistoryRepository
        config, fingerprint = await asyncio.to_thread(self.profiles.resolve,row['profile_id'])
        if fingerprint != row['profile_fingerprint']:
            raise RunnerError('PROFILE_CHANGED_AFTER_ACCEPT',409)
        from src.core.plan_facts import PlanFacts
        plan_scope = json.dumps([row['scope_key'],row['session_kind'],row['session_id'],
            row['actor_kind'],row['actor_id'],row['user_id'],row['source']],separators=(',',':'))
        resources = await asyncio.to_thread(RuntimeResources,
            is_master=config is None, subagent_config=config, session_id=row['session_id'],
            execution_id=row['runner_id'], mode=AgentMode.MASTER if config is None else AgentMode.STANDALONE,
            tenant_id=principal.identity.tenant_id,user_id=principal.identity.user_id,
            resource_directory=self.resource_directory,isolate_plan=True,session_kind=principal.identity.session_kind,
            plan_store=PlanFacts(),plan_scope=plan_scope)
        from .business_plans import BusinessPlanRepository
        own_plan = (row.get('checkpoint') or {}).get('business_plan')
        if (row.get('checkpoint') or {}).get('execution'):
            # Recovery restores this execution's facts, including a deliberately
            # deleted plan. Earlier session plans must not replace explicit None.
            if own_plan is not None:
                from src.models.plan import ExecutionPlan
                plan = ExecutionPlan.model_validate(own_plan)
                await asyncio.to_thread(resources.plan_manager._save_plan,row['session_id'],plan)
        else:
            await asyncio.to_thread(BusinessPlanRepository().prepare,resources.plan_manager,row)
        control.plan_manager = resources.plan_manager
        history = HistoryRepository(principal.identity)
        observer = DurableObserver(control,history,getattr(control,'trace_collector',None))
        runtime = RuntimeExecution(resources,principal.identity,control=control,observer=observer,history_reader=history,
            initial_followup_inputs=(row.get('checkpoint') or {}).get('pending_root_inputs') or [])
        from .runtime.child_recovery import ChildRecoveryAdapter
        runtime.child_recovery = ChildRecoveryAdapter(runtime,self.profiles)
        return runtime

    @staticmethod
    def attachments(row):
        # Inline content is already persisted with the input. File IDs require a
        # scoped upload projection rather than the legacy global disk scan.
        from src.core.redis_client import redis_client
        from src.core.storage import resolve_scoped_uploaded_file, configured_storage_root
        attachments = copy.deepcopy(row['input'].get('attachments') or [])
        for item in attachments:
            if not item.get('file_id'):
                continue
            info = redis_client.hgetall(redis_client.make_key('uploaded_file',item['file_id']))
            storage_root = configured_storage_root()
            resolved = resolve_scoped_uploaded_file(item['file_id'],row['tenant_id'],
                storage_root=storage_root,metadata=info)
            if not resolved:
                raise RunnerError('ATTACHMENT_NOT_FOUND',404)
            item['url'] = resolved['path']
            for key in ('name','mime_type','type'):
                if not item.get(key) or (key=='name' and item[key]=='unknown'):
                    item[key] = resolved.get(key)
            if item.get('type')=='file' and resolved.get('type')=='image':
                item['type'] = 'image'
        return attachments


def execution_result(state, status=None, error_code=None):
    if state is None:
        return {'status':status or 'failed','output':'','images':[], 'messages':[], 'error_code':error_code}
    mapped = {'completed':'completed','cancelled':'cancelled'}
    status = status or mapped.get(state.outcome.value,'failed')
    messages = []
    for message in state.messages[state.initial_len:]:
        if message.get('role')=='user':
            metadata = message.get('metadata') or {}
            if metadata.get('control_id'):
                messages.append({'role':'user','content':metadata.get('submitted_text',''),
                                 'metadata':copy.deepcopy(metadata)})
        elif message.get('role')=='tool' or message.get('tool_calls'):
            messages.append(copy.deepcopy(message))
    # Cancellation may leave siblings unexecuted. History records a projection of
    # that fact, while the authoritative pending ledger remains unchanged.
    paired = {message.get('tool_call_id') for message in messages if message.get('role') == 'tool'}
    for message in list(messages):
        for call in message.get('tool_calls') or []:
            if call['id'] not in paired:
                messages.append({'role':'tool','tool_call_id':call['id'],
                                 'content':'{"success":false,"error_code":"EXECUTION_STOPPED"}'})
                paired.add(call['id'])
    return {'status':status,'output':state.output,'images':copy.deepcopy(state.images),
            'messages':messages,'error_code':error_code or state.error_code}


class RunnerWorker:
    def __init__(self, config, *, worker_id=None, execution_repository=None, repository=None,
                 authorizer=None, usage_repository=None, finalizer=None, runtime_factory=None,
                 tool_recovery=None):
        self.config = config
        self.worker_id = worker_id or 'worker_' + uuid.uuid4().hex
        self.boot_id=uuid.uuid4().hex
        self.executions = execution_repository or ExecutionRepository()
        self.repository = repository or RunnerRepository()
        self.authorizer = authorizer or RunnerAuthorizer(config)
        self.usage = usage_repository or UsageRepository()
        self.finalizer = finalizer or RunnerFinalizer()
        self.factory = runtime_factory or RuntimeFactory(browser_process={
            'boot':self.boot_id,'config':getattr(config,'browser_owner',None)})
        source_factory=getattr(self.factory,'source_capabilities',None)
        self.sources=source_factory(config,self.repository.connection_factory) if source_factory is not None else None
        if self.sources is not None:
            self.authorizer.source_port=self.sources
            self.executions.source_port=self.sources
        if tool_recovery is None:
            recovery_port=getattr(self.factory,'recovery_port',None)
            if recovery_port is not None:
                tool_recovery=recovery_port(self.repository.connection_factory)
            else:
                from .local_recovery import LocalRecoveryProof
                tool_recovery=LocalRecoveryProof()
        self.tool_recovery = tool_recovery
        self.stopping = asyncio.Event()
        self.jobs = {}
        self._cancel_ready_scan_at = 0.0
        self._cancel_scan_after = 0
        self._recovery_scan_after = 0

    async def _park(self,control,state,status):
        row=await control.park(state,status)
        hook=getattr(self.factory,'after_park',None)
        if hook is not None:
            await hook(row)

    def stop(self):
        self.stopping.set()
        for task, control in list(self.jobs.items()):
            control.interrupt()
            task.cancel()

    async def _heartbeat(self, attempt, control, owner_task):
        while True:
            await asyncio.sleep(self.config.heartbeat_seconds)
            try:
                flags = await asyncio.to_thread(self.executions.heartbeat,attempt,self.config.lease_seconds)
                if flags.get('closed',False):
                    return
                control.cancel_requested = flags['cancel_requested']
                control.pause_requested = flags.get('pause_requested',False)
            except BaseException as error:
                if isinstance(error, asyncio.CancelledError):
                    raise
                control.interrupt()
                owner_task.cancel()
                return

    @staticmethod
    async def _drain_children(runtime, control):
        executor = runtime.resources.subagent_executor if runtime else None
        active = executor is not None and executor.has_active_tasks()
        if executor is not None:
            if control.pause_requested and not control.cancel_requested and not control.stopped:
                # All leaves observe the shared user pause at their next safe
                # point. Cancelling asyncio tasks would destroy resumable state.
                await executor.drain_owned_tasks(stop=False)
                return active
            if active and not control.cancel_requested:
                from .aggregate_tree import child_tree_finished
                if (not control.stopped and runtime.state is not None
                        and child_tree_finished(runtime.state.checkpoint())):
                    # A completed leaf can still be closing its task record or
                    # workspace. Await that owned cleanup without cancelling it.
                    await executor.drain_owned_tasks(stop=False)
                    return False
                # A delegate timeout with a live child is an interrupted aggregate,
                # not permission to rewrite its unfinished child as cancelled and
                # announce successful completion.
                control.interrupt()
            await executor.drain_owned_tasks(stop=True)
        return active

    async def _cancel_saved_invocations(self, attempt, *, request=True, complete_audit=False):
        from .local_recovery import cancel_owned_invocations
        return await asyncio.to_thread(cancel_owned_invocations,self.repository.connection_factory,
            attempt,request=request,complete_audit=complete_audit)

    async def _await_cancel_completion(self, attempt, control, state=None):
        from .local_recovery import cancel_completion_status
        from .cancellation_projection import accept_cancellation_row
        resource_cancel=getattr(self.factory,'request_resource_cancellation',None)
        if resource_cancel is not None:
            await resource_cancel(attempt)
        async def collect(request):
            async with control.lock:
                facts = await self._cancel_saved_invocations(attempt,request=request,complete_audit=True)
                accept_cancellation_row(control,getattr(facts,'row',None),state)
                return facts
        facts = await collect(not control.envelope.get('cancel_completion_requested',False))
        until = time.monotonic()+5.0
        while True:
            ready, issue = cancel_completion_status(facts)
            if ready:
                return True
            if issue != 'LOCAL_CANCEL_ACK_PENDING' or time.monotonic() >= until:
                saved = await asyncio.to_thread(self.executions.block_cancel,attempt,issue)
                control.envelope, control.revision = saved['checkpoint'],saved['revision']
                control.snapshot = saved['public_snapshot']
                return False
            await asyncio.sleep(0.5)
            facts = await collect(False)

    async def execute(self, row):
        attempt = Attempt(row['runner_id'],self.worker_id,row['attempt'])
        control = DurableControl(self.executions,attempt,row)
        scope = UsageScope(attempt,self.usage,row['runner_id'])
        started = time.monotonic()
        guard = Path(self.factory.resource_directory)/'guards'/f"{row['runner_id']}.{row['attempt']}.failure"
        def create_guard():
            guard.parent.mkdir(parents=True,exist_ok=True)
            descriptor = os.open(guard,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            os.close(descriptor)
        control.usage_scope = scope
        async def authorize_current(profile_id=None):
            prepared=await self.sources.prepare_execution(row) if self.sources is not None else None
            control.prepared_source=prepared
            kwargs={'prepared_source':prepared} if prepared is not None else {}
            principal = await asyncio.to_thread(self.authorizer.authorize_persisted,row,**kwargs)
            if profile_id and profile_id != row['profile_id']:
                await asyncio.to_thread(self.authorizer.authorize_child_profile,principal,profile_id)
        control.authorization_check = authorize_current
        self.jobs[asyncio.current_task()] = control
        heartbeat = asyncio.create_task(self._heartbeat(attempt,control,asyncio.current_task()))
        runtime = None
        collector = None
        try:
            if row.get('pause_requested') and row['status']=='running' and not row['cancel_requested']:
                from .recovery_repository import RecoveryRepository
                await asyncio.to_thread(RecoveryRepository(self.executions.connection_factory).acknowledge_pause,
                    attempt,row['revision'],{**control.envelope,'unstarted':True},control.snapshot)
                return
            await asyncio.to_thread(create_guard)
            scope.failure_file = str(guard)
            from src.core.trace_collector import TraceCollector
            collector = TraceCollector(row['session_id'],row['tenant_id'] or '',row['user_id'] or '',
                row['input']['text'],source_type=row['source'],subagent_id=None if row['profile_id']=='main' else row['profile_id'])
            collector.trace.trace_id = 'tr_'+row['runner_id']
            control.trace_collector = collector
            # A committed finalization intent is immutable even if a user requests
            # cancellation while its transaction is retrying.
            if row['status'] == 'finalizing':
                if not (row.get('checkpoint') or {}).get('pending_finalization'):
                    if row.get('checkpoint',{}).get('finalization_intent') != 'cancel_only':
                        raise CheckpointFailure('FINALIZATION_INTENT_NOT_SAVED')
                    if not await self._await_cancel_completion(attempt, control):
                        return
                    state = control.envelope.get('execution')
                    state = ExecutionState.restore(state) if state else None
                    result = execution_result(state,'cancelled')
                    result['duration_ms'] = int((time.monotonic()-started)*1000)
                    await control.stage(state,result)
                scope.check()
                await asyncio.to_thread(self.finalizer.finalize,attempt)
                return
            with runner_usage_scope(scope):
                resume_state = (ExecutionState.restore(control.envelope['execution'])
                    if control.envelope.get('applied_control_id') and control.envelope.get('execution') else None)
                while True:
                    try:
                        if runtime is None:
                            await authorize_current()
                            principal = await asyncio.to_thread(self.authorizer.authorize_persisted,row,
                                **({'prepared_source':control.prepared_source} if control.prepared_source is not None else {}))
                            await asyncio.to_thread(self.authorizer.assert_credit,principal)
                            runtime = await self.factory.create(row,principal,control)
                            attachments = await asyncio.to_thread(self.factory.attachments,row)
                            from src.core.request_context import AgentRequestContext
                            projection = control.envelope.get('execution_context')
                            if projection is None:
                                # M2 accepted rows carry the original generic input contract.
                                projection = row['input']
                            elif (projection.get('version') != 1 or projection.get('profile_id') != row['profile_id']
                                  or projection.get('profile_fingerprint') != row['profile_fingerprint']):
                                raise CheckpointFailure('EXECUTION_CONTEXT_VERSION_MISMATCH')
                            request = AgentRequestContext(prompt_augmentations=tuple(projection.get('prompt_augmentations') or []),
                                                          request_data=projection.get('request_data') or {})
                            user = await asyncio.to_thread(self._agent_user,principal.identity.user_id,principal.identity.tenant_id)
                            from src.core.verbose_feedback import default_feedback_config, VerboseFeedbackState, iter_with_verbose_feedback
                            config, feedback = default_feedback_config(), VerboseFeedbackState()
                            if row['source'] == 'wecom_kf' and request.request_data.get('verbose_feedback'):
                                from dataclasses import replace
                                from src.core.verbose_feedback import VerboseFeedbackConfig
                                channel_feedback = VerboseFeedbackConfig(**request.request_data['verbose_feedback'])
                                config = replace(channel_feedback, force_disabled=config.force_disabled or channel_feedback.force_disabled)
                        events = runtime.run(row['input']['text'],user=user,attachments=attachments,request_context=request,
                            verbose_config=config,verbose_state=feedback,
                            state=resume_state)
                        from contextlib import asynccontextmanager
                        @asynccontextmanager
                        async def runtime_scope():
                            if self.sources is None:yield
                            else:
                                async with self.sources.runtime_scope(row,control):yield
                        async with runtime_scope():
                            async for _event in iter_with_verbose_feedback(events,surface=row['session_kind'] if row['session_kind']=='web' else 'channel',
                                config=config,state=feedback):
                                if _event.get('type') == 'verbose' and not any(
                                        item.get('eventId') == _event.get('eventId') for item in
                                        control.envelope.get('presentation',{}).get('verboseMessages',[])):
                                    await runtime.observer.event(runtime.state, _event)
                    except StopRequested as error:
                        control.cancel_requested = error.command == 'cancel'
                        control.pause_requested = error.command == 'pause'
                        if runtime and runtime.state:
                            runtime.state.outcome = Outcome.CANCELLED if control.cancel_requested else Outcome.PAUSED
                    except RunnerError as error:
                        await self._drain_children(runtime,control)
                        scope.check()
                        if control.stopped:
                            await self._preserve_duration(attempt, control)
                            await asyncio.to_thread(self.executions.interrupt_attempt,attempt)
                            return
                        result = execution_result(runtime.state if runtime else None,'failed',error.code)
                        result['duration_ms'] = int((time.monotonic()-started)*1000)
                        result['assistant_metadata'] = copy.deepcopy(control.envelope.get('presentation') or {})
                        await control.stage(runtime.state if runtime else None,result)
                        await asyncio.to_thread(self.finalizer.finalize,attempt)
                        return
                    # A timeout/failed delegate does not prove its child stopped. Drain
                    # every owned asyncio task, including those queued on a semaphore.
                    await self._drain_children(runtime,control)
                    scope.check()
                    state = runtime.state if runtime else None
                    if control.stopped:
                        await self._preserve_duration(attempt, control)
                        await asyncio.to_thread(self.executions.interrupt_attempt,attempt)
                        return
                    if control.pause_requested and not control.cancel_requested:
                        if state is None:
                            from .recovery_repository import RecoveryRepository
                            await asyncio.to_thread(RecoveryRepository(self.executions.connection_factory).acknowledge_pause,
                                attempt,control.revision,{**control.envelope,'unstarted':True},control.snapshot)
                        else:
                            state.outcome = Outcome.PAUSED
                            from .pause_protocol import safe_paused_tree
                            if not safe_paused_tree(state):
                                await self._preserve_duration(attempt, control)
                                await asyncio.to_thread(self.executions.interrupt_attempt,attempt)
                                return
                            await self._park(control,state,'paused')
                        return
                    if state and state.outcome in {Outcome.WAITING,Outcome.PAUSED} and not control.cancel_requested:
                        await self._park(control,state,state.outcome.value)
                        return
                    if state and not control.cancel_requested:
                        from .aggregate_tree import park_unfinished_tree
                        parked = park_unfinished_tree(state)
                        if parked == 'interrupted':
                            await self._preserve_duration(attempt, control)
                            await asyncio.to_thread(self.executions.interrupt_attempt,attempt)
                            return
                        if parked:
                            await self._park(control,state,parked)
                            return
                    if control.cancel_requested:
                        if not await self._await_cancel_completion(attempt, control, state):
                            return
                    result = execution_result(state,'cancelled' if control.cancel_requested else None)
                    result['duration_ms'] = int((time.monotonic()-started)*1000)
                    result['assistant_metadata'] = copy.deepcopy(control.envelope.get('presentation') or {})
                    staged=await control.stage(state,result)
                    if staged['status'] == 'running':
                        # The final cutoff transaction admitted pending input.
                        # Reuse the same Runtime, budget and complete execution path.
                        resume_state = ExecutionState.restore(staged['checkpoint']['execution'])
                        continue
                    scope.check()
                    await asyncio.to_thread(self.finalizer.finalize,attempt)
                    return
        except LeaseLost:
            control.interrupt()
            await self._drain_children(runtime,control)
        except asyncio.CancelledError:
            control.interrupt()
            await self._drain_children(runtime,control)
            try:
                await self._preserve_duration(attempt, control)
            except LeaseLost:
                pass
            finally:
                # Statistics CAS is not interruption authority. A real storage
                # fault still propagates after the original interruption attempt.
                try:
                    await asyncio.to_thread(self.executions.interrupt_attempt,attempt)
                except LeaseLost:
                    pass
            raise
        except Exception as error:
            control.interrupt()
            had_active_children = await self._drain_children(runtime,control)
            # Storage/transport failures preserve the most recent committed facts;
            # a finalizing row retries its transaction and never reenters the Loop.
            try:
                if (getattr(error,'authoritative_storage_failure',False) or scope.fatal_error is not None
                        or row['status']=='finalizing' or had_active_children):
                    await self._preserve_duration(attempt, control)
                    await asyncio.to_thread(self.executions.interrupt_attempt,attempt,
                        **({'public_verification':error.public_verification} if getattr(error,'public_verification',None) else {}))
                else:
                    scope.check()
                    state = runtime.state if runtime else None
                    result = execution_result(state,'failed','EXECUTION_FAILED')
                    result['duration_ms'] = int((time.monotonic()-started)*1000)
                    result['assistant_metadata'] = copy.deepcopy(control.envelope.get('presentation') or {})
                    await control.stage(state,result)
                    scope.check()
                    await asyncio.to_thread(self.finalizer.finalize,attempt)
            except Exception:
                pass
            logger.error('AgentRunner attempt stopped runner={} kind={}',row['runner_id'],type(error).__name__)
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat,return_exceptions=True)
            self.jobs.pop(asyncio.current_task(),None)
            if collector is not None:
                await asyncio.to_thread(self._persist_trace,collector,row['runner_id'],control,
                                        row['status']=='finalizing')
            await asyncio.to_thread(self._cleanup_completed_resources,row['runner_id'])
            try:
                await asyncio.to_thread(guard.unlink,missing_ok=True)
            except OSError as error:
                logger.warning('AgentRunner guard cleanup unavailable kind={}',type(error).__name__)

    async def _preserve_duration(self, attempt, control):
        # Only add elapsed time to the last COMMITTED execution. The cancelled
        # in-memory state is not resumable authority. A lost lease rejects this
        # metadata write just like every other checkpoint write.
        async with control.lock:
            row = await asyncio.to_thread(self.repository.get, attempt.runner_id)
            if row['status'] != 'running':
                return
            envelope = copy.deepcopy(row.get('checkpoint') or {})
            control.capture_duration(envelope)
            saved = await asyncio.to_thread(self.executions.save_checkpoint, attempt,
                row['revision'], envelope, row['public_snapshot'])
            control.envelope, control.revision = saved['checkpoint'], saved['revision']

    def _cleanup_completed_resources(self, runner_id):
        # Execution finalization and owned-task drain precede resource cleanup.
        # A shutdown-mutated in-memory outcome is never a deletion authority.
        try:
            row = self.repository.get(runner_id)
            if row['status'] not in ('completed','failed','cancelled'):
                return
            from .resource_paths import cleanup_workspaces
            from src.core.agent_engine.contracts import Identity
            identity = Identity(row['tenant_id'],row['user_id'],row['session_id'],row['source'],row['session_kind'])
            def cleanup(state, expected_execution_id):
                if state.get('identity') != identity.__dict__ or state.get('execution_id')!=expected_execution_id:
                    logger.warning('AgentRunner resource cleanup skipped code=RESOURCE_IDENTITY_MISMATCH')
                    return
                cleanup_workspaces(self.factory.resource_directory, identity, state)
                for child in state.get('children',{}).values():
                    cleanup(child.get('checkpoint') or {},child.get('execution_id'))
            cleanup((row.get('checkpoint') or {}).get('execution') or {},row['runner_id'])
        except Exception as error:
            logger.warning('AgentRunner resource cleanup unavailable kind={}',type(error).__name__)

    @staticmethod
    def _agent_user(user_id, tenant_id):
        if user_id is None:
            return None
        from src.db.models import UserDB
        from src.models.user import User
        user = UserDB.get_by_id(user_id)
        if not user:
            raise RunnerError('USER_UNAUTHORIZED',401)
        return User(user_id=user_id,name=user.get('username') or user.get('phone') or user_id,
                    phone=user.get('phone'),tenant_id=tenant_id)

    def _persist_trace(self, collector, runner_id, control, finalization_retry=False):
        try:
            from src.db.models import ChatRecordDB
            from types import SimpleNamespace
            runner = self.repository.get(runner_id)
            collector.trace.status = runner['status']
            collector.trace.output = (runner.get('result') or {}).get('output') or control.snapshot.get('output') or ''
            if finalization_retry:
                from src.core.trace_persist import update_completion
                update_completion(collector.trace.trace_id,status=runner['status'],output=collector.trace.output,
                    duration_ms=((runner.get('checkpoint') or {}).get('pending_finalization') or {}).get('duration_ms',0))
                return
            envelope = runner.get('checkpoint') or {}
            from src.core.execution_usage import execution_token_usage
            usage = execution_token_usage(envelope.get('execution') or {})
            collector.trace.total_tokens = usage.get('input',0)+usage.get('output',0)
            collector.trace.agent_iterations = (envelope.get('execution') or {}).get('iteration',0)
            collector.trace.metadata['runner_summary'] = True
            collector.trace.metadata['runner_duration_ms'] = envelope.get('execution_duration_ms',0)
            record = ChatRecordDB.get_by_id(runner['record_id'])
            projection = None
            if record:
                collector.trace.total_cost = float(record.get('credit_cost') or 0)
                collector.trace.user_message_id = runner_id+':user'
                projection = SimpleNamespace(total_token_count=record.get('total_token_count') or 0,
                    model=record.get('model'),provider=record.get('provider'),agent_iterations=record.get('agent_iterations') or 0)
            collector.on_complete(projection,persist_sync=True)
        except Exception as error:
            logger.warning('AgentRunner trace flush unavailable kind={}',type(error).__name__)

    async def run(self, *, once=False, max_tasks=None):
        if not self.config.enabled:
            raise RuntimeError('AGENT_RUNNER_DISABLED')
        completed = 0
        tasks = set()
        try:
            open_resources = getattr(self.factory,'open',None)
            if open_resources is not None:
                await open_resources()
            while not self.stopping.is_set():
                await asyncio.to_thread(self.executions.reap_expired)
                await asyncio.to_thread(self.finalizer.settle_ready)
                while len(tasks) < self.config.concurrency and (max_tasks is None or completed+len(tasks)<max_tasks):
                    row = await self._acquire_next()
                    if row is None:
                        break
                    tasks.add(asyncio.create_task(self.execute(row)))
                    if once:
                        break
                if once:
                    await asyncio.gather(*tasks)
                    return len(tasks)
                if tasks:
                    done, tasks = await asyncio.wait(tasks,timeout=self.config.poll_seconds,return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        await task
                    completed += len(done)
                else:
                    try:
                        await asyncio.wait_for(self.stopping.wait(),timeout=self.config.poll_seconds)
                    except asyncio.TimeoutError:
                        pass
                if max_tasks is not None and completed>=max_tasks:
                    return completed
        except asyncio.CancelledError:
            if not self.stopping.is_set():
                raise
            # Only the service's explicit shutdown makes owned task cancellation
            # expected. Execution already preserved its latest safe checkpoint.
            return completed
        finally:
            self.stop()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
            try:
                close_sources=getattr(self.sources,'close',None)
                if close_sources is not None: await close_sources()
            finally:
                close = getattr(self.factory,'close',None)
                if close is not None: await close()

    async def _acquire_next(self):
        from .control_repository import ControlRepository
        from .recovery import RecoveryCoordinator
        from .recovery_repository import RecoveryRepository
        hook=getattr(self.factory,'prepare_acquisition',None)
        if hook is not None:
            await hook()
        if time.monotonic() >= self._cancel_ready_scan_at:
            from .local_recovery import release_ready_cancellations
            self._cancel_scan_after = await asyncio.to_thread(release_ready_cancellations,
                self.repository.connection_factory,after_queue_order=self._cancel_scan_after)
            self._cancel_ready_scan_at = time.monotonic()+10.0
        recovery = RecoveryRepository(self.repository.connection_factory)
        candidates=await asyncio.to_thread(recovery.next_candidates,after_queue_order=self._recovery_scan_after)
        self._recovery_scan_after=candidates[-1]['queue_order'] if candidates else 0
        for row in candidates:
            try:
                affinity=getattr(self.tool_recovery,'affinity',None)
                if affinity is not None and not await affinity(row):
                    continue
                principal = await asyncio.to_thread(self.authorizer.authorize_persisted,row,execute=False)
                control = await asyncio.to_thread(ControlRepository(self.repository.connection_factory).get,
                    principal,row['runner_id'],row['resume_control_id'])
                prepared_source=await self.sources.prepare_execution(row) if self.sources is not None else None
                prepared = await asyncio.to_thread(RecoveryCoordinator(self.authorizer,self.factory.profiles,
                    tool_recovery=self.tool_recovery,
                    attachment_resolver=self.factory.attachments,
                    resource_directory=self.factory.resource_directory).prepare,row,control,
                    prepared_source=prepared_source)
                resumed = await asyncio.to_thread(recovery.claim_resume,row['runner_id'],self.worker_id,
                    self.config.lease_seconds,revision=row['revision'],control_id=control['control_id'],
                    checkpoint=prepared.checkpoint,
                    recovery_port=self.tool_recovery if hasattr(self.tool_recovery,'assert_claim_in_tx') else None,
                    source_port=self.sources,prepared_source=prepared_source)
                if resumed is not None:
                    return resumed
            except SourceUnavailable as error:
                await asyncio.to_thread(recovery.hold_resume,row['runner_id'],revision=row['revision'],
                    control_id=row['resume_control_id'],public_verification=error.public_verification)
                continue
            except RunnerError as error:
                if getattr(error,'preserve_control',False):
                    await asyncio.to_thread(self.tool_recovery.hold,row,error)
                    continue
                await asyncio.to_thread(recovery.reject_resume,row['runner_id'],revision=row['revision'],
                    control_id=row['resume_control_id'],error_code=error.code)
        return await asyncio.to_thread(self.executions.acquire,self.worker_id,self.config.lease_seconds)


async def run_worker(arguments):
    from src.config.settings import settings
    from src.db.database import init_postgres_pool, close_postgres_pool, init_logs_pool, close_logs_pool
    config = settings.agent_runner
    if not config.enabled:
        raise RuntimeError('AGENT_RUNNER_DISABLED')
    if not config.peers or not config.web_service_token:
        raise RuntimeError('AGENT_RUNNER_SERVICE_AUTH_REQUIRED')
    await asyncio.to_thread(init_postgres_pool)
    try:
        await asyncio.to_thread(init_logs_pool)
        await asyncio.to_thread(RunnerRepository().assert_schema)
        worker = RunnerWorker(config,worker_id=arguments.worker_id)
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGTERM,signal.SIGINT):
            loop.add_signal_handler(signum,worker.stop)
        return await worker.run(once=arguments.once,max_tasks=arguments.max_tasks)
    finally:
        await asyncio.to_thread(close_logs_pool)
        await asyncio.to_thread(close_postgres_pool)


def main():
    parser = argparse.ArgumentParser(description='Run an independent AgentRunner durable worker')
    parser.add_argument('--worker-id',default=None)
    parser.add_argument('--once',action='store_true')
    parser.add_argument('--max-tasks',type=int,default=None)
    arguments = parser.parse_args()
    if arguments.max_tasks is not None and arguments.max_tasks<1:
        parser.error('--max-tasks must be positive')
    asyncio.run(run_worker(arguments))


if __name__ == '__main__':
    main()
