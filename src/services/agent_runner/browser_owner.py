"""Application owner for the original Browser producer; no Browser loop here."""

import asyncio
import copy
import uuid

from src.core.agent_engine.contracts import CheckpointFailure
from .browser_binding import BrowserOwnerRepository, owned_browser_execution
from .browser_endpoint import trusted_browser_endpoint
from .ownership import LeaseLost, StopRequested


class RunnerBrowserOwner:
    @property
    def runner_id(self):
        return self.root.attempt.runner_id

    def __init__(self, control, state, call_id):
        self.control,self.state,self.call_id=control,state,call_id
        self.root=control
        while hasattr(self.root,'parent_control'):
            self.root=self.root.parent_control
        process=getattr(self.root,'browser_process',None)
        if not process or not process['config'].enabled or not process['boot']:
            raise CheckpointFailure('BROWSER_PRODUCER_NOT_AVAILABLE')
        if process['config'].view_enabled and not getattr(process.get('sidecar'),'accepting',False):
            raise CheckpointFailure('BROWSER_VIEW_LISTENER_NOT_READY')
        self.process_resources = process
        self.worker_boot=process['boot']
        self.browser_epoch=uuid.uuid4().hex
        self.endpoint=trusted_browser_endpoint(process['config'].endpoint,process['config'].allowed_endpoints)
        self.repository=BrowserOwnerRepository(self.root.repository.connection_factory)
        self.manager=None
        self.record=None

    def _accept(self,row):
        node,_,_=owned_browser_execution(row,row['checkpoint'],self.state.execution_id,self.call_id)
        self.state.resources.setdefault('browser_runs',{})[self.call_id]=copy.deepcopy(
            node['resources']['browser_runs'][self.call_id])
        state,control=self.state,self.control
        while hasattr(control,'parent_control'):
            fact=control.parent_state.children.get(control.call_id)
            if not fact or fact.get('execution_id')!=state.execution_id:
                raise CheckpointFailure('BROWSER_CHILD_OWNER_MISMATCH')
            fact['checkpoint']=state.checkpoint()
            state,control=control.parent_state,control.parent_control
        self.root.envelope=copy.deepcopy(row['checkpoint'])
        self.root.revision=row['revision']
        self.root.snapshot=copy.deepcopy(row['public_snapshot'])
        self.root.cancel_requested=row['cancel_requested']
        self.root.pause_requested=row.get('pause_requested',False)

    def _fail(self,error):
        if isinstance(error,(StopRequested,asyncio.CancelledError)):
            return
        if self.root.usage_scope is not None:
            self.root.usage_scope.fail(error)

    def record_owner_failure(self,error):
        self._fail(error)

    async def _root_transaction(self,method,*,accept=False,**kwargs):
        try:
            async with self.root.lock:
                self.root._check_usage()
                if self.root.stopped:
                    raise LeaseLost('RUNNER_WORKER_STOPPED')
                if 'revision' in kwargs:
                    kwargs['revision']=self.root.revision
                row=await asyncio.to_thread(method,self.root.attempt,**kwargs)
                if accept:
                    self._accept(row)
                return row
        except BaseException as error:
            if isinstance(error,(asyncio.CancelledError,CheckpointFailure)):
                self._fail(error)
                raise
            failure=CheckpointFailure('BROWSER_OWNER_STORAGE_FAILED')
            self._fail(failure)
            raise failure from error

    async def bind_run(self,manager,record):
        await self.control.authorize_dispatch(self.state.profile_id)
        await self.control.save(self.state,'browser_before_bind')
        await self._root_transaction(self.repository.bind_run,accept=True,revision=self.root.revision,
            execution_id=self.state.execution_id,call_id=self.call_id,record=record.model_dump(mode='json'),
            worker_boot=self.worker_boot,browser_epoch=self.browser_epoch,endpoint=self.endpoint,
            lease_seconds=manager.owner_lease_seconds)
        self.manager,self.record=manager,record
        # Process-owned handles are not checkpoint facts. Keep a native
        # manager discoverable by boot cleanup even after a failed renewal
        # task and its tool invocation have both ended.
        self.process_resources.setdefault('retained_managers', set()).add(manager)

    async def activate_run(self,manager,record):
        if manager is not self.manager or record.run_id!=self.record.run_id:
            raise CheckpointFailure('BROWSER_RUNTIME_OBJECT_MISMATCH')
        await self.control.authorize_dispatch(self.state.profile_id)
        await self._root_transaction(self.repository.activate_run,execution_id=self.state.execution_id,
            call_id=self.call_id,run_id=record.run_id,worker_boot=self.worker_boot,
            browser_epoch=self.browser_epoch,lease_seconds=manager.owner_lease_seconds)

    async def bind_wait(self,manager,record,assistance,suspension):
        if manager is not self.manager or assistance['run_id']!=self.record.run_id:
            raise CheckpointFailure('BROWSER_RUNTIME_OBJECT_MISMATCH')
        await self._root_transaction(self.repository.bind_wait,accept=True,revision=self.root.revision,
            execution_id=self.state.execution_id,call_id=self.call_id,assistance=assistance,
            worker_boot=self.worker_boot,browser_epoch=self.browser_epoch)

    async def authorize_agent_action(self):
        await self.control.authorize_dispatch(self.state.profile_id)
        await self._root_transaction(self.repository.authorize_action,execution_id=self.state.execution_id,
            call_id=self.call_id,run_id=self.record.run_id,worker_boot=self.worker_boot,browser_epoch=self.browser_epoch)

    async def renew_browser_owner(self,tenant_id,run_id):
        if not self.record or (tenant_id,run_id)!=(self.record.tenant_id,self.record.run_id):
            raise LeaseLost('BROWSER_RUNTIME_OBJECT_MISMATCH')
        return await self._observation(self.repository.renew_owner,tenant_id=tenant_id,run_id=run_id,
            worker_id=self.root.attempt.worker_id,worker_boot=self.worker_boot,
            browser_epoch=self.browser_epoch,lease_seconds=self.manager.owner_lease_seconds)

    async def _observation(self,method,**kwargs):
        try:
            return await asyncio.to_thread(method,**kwargs)
        except BaseException as error:
            if isinstance(error,(asyncio.CancelledError,CheckpointFailure)):
                self._fail(error)
                raise
            failure=CheckpointFailure('BROWSER_OWNER_STORAGE_FAILED')
            self._fail(failure)
            raise failure from error

    def _binding(self,tenant_id,run_id):
        if not self.record or (tenant_id,run_id)!=(self.record.tenant_id,self.record.run_id):
            raise LeaseLost('BROWSER_RUNTIME_OBJECT_MISMATCH')
        return dict(tenant_id=tenant_id,user_id=self.record.user_id,session_id=self.record.session_id,
            run_id=run_id,runner_id=self.root.attempt.runner_id,runner_execution_id=self.state.execution_id,
            runner_tool_call_id=self.call_id,owner_worker_id=self.root.attempt.worker_id,
            owner_boot_id=self.worker_boot,browser_epoch=self.browser_epoch,owner_endpoint=self.endpoint)

    async def record_state(self,tenant_id,run_id,state,**facts):
        return await self._observation(self.repository.record_runtime_state,
            binding=self._binding(tenant_id,run_id),state=state)

    async def close_browser_owner(self,tenant_id,run_id,state):
        if not self.manager or not self.manager.resource_close_confirmed(tenant_id,run_id):
            raise CheckpointFailure('BROWSER_CLOSE_VERIFICATION_REQUIRED')
        result = await self._observation(self.repository.record_runtime_state,
            binding=self._binding(tenant_id,run_id),state=state,closed=True)
        # An unknown close or failed durable observation retains the handle.
        # Release only this manager, after actual resources and PG agree.
        self.process_resources.get('retained_managers', set()).discard(self.manager)
        return result

    async def complete_assistance(self, assistance_id, *, automatic=False):
        """Same-loop trusted completion, not a public user completion assertion."""
        from src.tools.browser.human_control import HumanControlCoordinator
        return await HumanControlCoordinator().complete_owned(self,assistance_id,automatic=automatic)

    async def authorize_human_completion(self,assistance_id):
        from .browser_human_actions import BrowserHumanActions
        await BrowserHumanActions(self).automatic_guard(assistance_id)

    async def sample_human_completion(self,assistance_id,page_ops):
        from .browser_human_actions import BrowserHumanActions
        from src.tools.browser.human_control import get_owned_runtime
        from src.tools.browser.owner_port import HumanActionOperation,browser_human_action_scope,HumanActionRejected
        runtime=await get_owned_runtime(self.record.tenant_id,self.record.run_id)
        if not runtime or runtime.manager is not self.manager or runtime.orchestrator.page_ops is not page_ops:
            raise HumanActionRejected('HUMAN_RUNTIME_CHANGED')
        actions=BrowserHumanActions(self)
        operation=HumanActionOperation(lambda:actions.automatic_guard(assistance_id))
        async def sample():
            with browser_human_action_scope(operation):
                return await page_ops._human_snapshot(self.manager)
        return await actions.retain(sample(),operation)

    async def record_completion(self, assistance, *, completed_by_human):
        from .browser_completion import BrowserCompletionRepository
        repository = BrowserCompletionRepository(self.repository.connection_factory)
        binding = self._binding(assistance.tenant_id,assistance.run_id)
        fact = await self._observation(repository.record_fact,binding=binding,
            assistance_id=assistance.assistance_id,step_index=assistance.step_index,
            completed_by_human=completed_by_human)
        control_id = await self._observation(repository.bridge_parked,binding=binding,
            assistance_id=assistance.assistance_id)
        return dict(completion_ref=fact['completion_ref'],bridged=bool(control_id),missing_conditions=[])

    async def bridge_completions(self):
        """Finite current-process facts; never dispatches or rebuilds a Browser."""
        if self.record is None:
            return
        from .browser_completion import BrowserCompletionRepository, BrowserRecoveryUnavailable
        repository = BrowserCompletionRepository(self.repository.connection_factory)
        binding = self._binding(self.record.tenant_id,self.record.run_id)
        rows = await asyncio.to_thread(repository.unbridged,self.record.run_id,
            getattr(self,'_completion_scan_after',0))
        self._completion_scan_after = rows[-1]['id'] if rows else 0
        for row in rows:
            try:
                await asyncio.to_thread(repository.bridge_parked,binding,row['assistance_id'])
            except BrowserRecoveryUnavailable:
                continue
            except Exception as error:
                from .contracts import RunnerError
                if isinstance(error,RunnerError) and error.code=='RECOVERY_CLAIM_LOST':
                    continue
                raise

    async def rebind(self, control, state):
        """Short authority switch; no nested authorize/save under root.lock."""
        root = control
        while hasattr(root,'parent_control'):
            root = root.parent_control
        process = getattr(root,'browser_process',None)
        if (process is not self.process_resources or process['boot'] != self.worker_boot
                or state.execution_id != self.state.execution_id
                or root.attempt.worker_id != self.root.attempt.worker_id):
            raise CheckpointFailure('BROWSER_CONTINUATION_OWNER_CHANGED')
        async with root.lock:
            root._check_usage()
            if root.stopped:
                raise LeaseLost('RUNNER_WORKER_STOPPED')
            await asyncio.to_thread(self.repository.authorize_action,root.attempt,
                execution_id=state.execution_id,call_id=self.call_id,run_id=self.record.run_id,
                worker_boot=self.worker_boot,browser_epoch=self.browser_epoch)
            self.control,self.state,self.root=control,state,root
