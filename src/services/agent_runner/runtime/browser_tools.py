"""Exact registered Browser capability -> neutral producer owner scope."""

from src.tools.browser.automation_tool import BrowserAutomationTool
from src.tools.browser.owner_port import browser_execution_owner_scope
from src.core.agent_engine.contracts import DispatchResult, CheckpointFailure


class BrowserToolAdapter:
    def __init__(self,control,state):
        self.control,self.state=control,state

    async def execute(self,tool,executor,call,context):
        if type(tool) is not BrowserAutomationTool or not getattr(self.control,'durable_owner',False):
            return await executor.execute(call.name,call.arguments,context=context)
        root=self.control
        while hasattr(root,'parent_control'):
            root=root.parent_control
        process=getattr(root,'browser_process',None)
        if not process or not process.get('config') or not process['config'].enabled:
            return {'success':False,'error_code':'BROWSER_PRODUCER_NOT_AVAILABLE',
                'error':'浏览器执行能力暂未启用'}
        from ..browser_owner import RunnerBrowserOwner
        owner=RunnerBrowserOwner(self.control,self.state,call.id)
        with browser_execution_owner_scope(owner):
            return await executor.execute(call.name,call.arguments,context=context)

    async def recover(self,state,call,context):
        import asyncio
        from src.tools.browser.human_control import get_owned_runtime, unregister_owned_runtime, HumanControlCoordinator
        from src.tools.browser.resume_store import ResumeStore
        from src.tools.browser.run_manager import RunState
        from src.tools.context import tool_execution_scope
        from src.tools.browser.owner_port import browser_agent_action_scope
        from ..browser_completion import BrowserCompletionRepository, BrowserRecoveryUnavailable
        from src.core.agent_events import _extract_image_refs_from_tool_result, _normalize_image_placement
        root=self.control
        while hasattr(root,'parent_control'):
            root=root.parent_control
        repository=BrowserCompletionRepository(root.repository.connection_factory)
        reference=state.resources.get('browser_completions',{}).get(call.id)
        if not reference:
            wait=state.resources.get('browser_waits',{}).get(call.id)
            if wait:
                return DispatchResult(None,wait=wait)
            return self.verification(call.id)
        try:
            fact=await asyncio.to_thread(repository.read_fact,root.attempt.runner_id,reference)
            phase=fact['continuation']['phase']
            if phase=='completed':
                saved=fact['continuation']['result']
                if saved.get('next_assistance_id'):
                    return DispatchResult(None,wait=saved['waiting'])
                result=saved['tool_result']
                await self._clear_completed_wait(state,fact)
            elif phase=='observed':
                binding=fact['binding']
                runtime=await get_owned_runtime(state.identity.tenant_id,binding['run_id'])
                owner=getattr(getattr(runtime,'manager',None),'execution_owner',None)
                if (not owner or owner.worker_boot!=binding['owner_boot_id']
                        or owner.browser_epoch!=binding['browser_epoch'] or owner.call_id!=call.id):
                    return self.verification(call.id)
                await owner.rebind(self.control,state)
                async with root.lock:
                    root._check_usage()
                    await asyncio.to_thread(repository.start,root.attempt,root.revision,binding,
                        fact['assistance_id'],reference)
                store=ResumeStore()
                record=await store.get_assistance(state.identity.tenant_id,fact['assistance_id'])
                if record is None or record.runner_id!=root.attempt.runner_id or record.step_index!=fact['step_index']:
                    return self.verification(call.id)
                # Continuation-started already committed: no crash/retry can
                # append confirmation or replay the original action again.
                queued=await store.cas_state(record.tenant_id,record.assistance_id,
                    {'pending','controlling'},'resume_queued',completed_by_human=fact['completed_by_human'])
                if queued is None:
                    return self.verification(call.id)
                current=await runtime.manager.store.get(record.tenant_id,record.run_id)
                if current and current.state==RunState.WAITING_HUMAN.value:
                    await runtime.manager.transition(record.tenant_id,record.run_id,RunState.RUNNING_HUMAN)
                await runtime.manager.transition(record.tenant_id,record.run_id,RunState.RESUMING)
                with tool_execution_scope(context),browser_execution_owner_scope(owner),browser_agent_action_scope(owner):
                    result=await runtime.orchestrator.resume_from_human(
                        completed_by_human=fact['completed_by_human'],step_index=fact['step_index'])
                    if result.get('status')=='ask_user':
                        suspension=await HumanControlCoordinator(store=store).suspend(
                            tenant_id=record.tenant_id,user_id=record.user_id,session_id=record.session_id,
                            agent_execution_id=record.agent_execution_id,tool_call_id=record.tool_call_id,
                            run_id=record.run_id,manager=runtime.manager,orchestrator=runtime.orchestrator,
                            executor=runtime.executor,reason_code=result.get('error_code','HUMAN_REQUIRED'),
                            step_index=len(runtime.orchestrator.steps),replaces=record)
                        waiting=dict(kind='human_assistance',tool_call_id=call.id,
                            assistance_id=suspension.assistance_id)
                        from ..wait_identity import owned_wait_id
                        waiting.update(wait_id=owned_wait_id(root.attempt.runner_id,state.execution_id,waiting),
                            target_execution_id=state.execution_id)
                        async with root.lock:
                            await asyncio.to_thread(repository.finish,root.attempt,binding,
                                record.assistance_id,reference,dict(next_assistance_id=suspension.assistance_id,waiting=waiting))
                        await store.cas_state(record.tenant_id,record.assistance_id,{'resume_queued'},'resumed')
                        project=getattr(self.control,'project',None)
                        if project is not None:
                            await project(state,suspension.event)
                        return DispatchResult(None,wait=waiting)
                async with root.lock:
                    await asyncio.to_thread(repository.finish,root.attempt,binding,
                        record.assistance_id,reference,dict(tool_result=result))
                await self._clear_completed_wait(state,fact)
            else:
                return self.verification(call.id)
            images=_extract_image_refs_from_tool_result(result) if isinstance(result,dict) else []
            _normalize_image_placement(images)
            return DispatchResult(result,success=result.get('success',True),images=tuple(images))
        except BrowserRecoveryUnavailable:
            return self.verification(call.id)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if getattr(error,'authoritative_storage_failure',False):
                raise
            raise CheckpointFailure('BROWSER_CONTINUATION_STORAGE_FAILED') from error

    async def _clear_completed_wait(self,state,fact):
        """Retry only the original terminal assistance's Redis projection."""
        from src.tools.browser.resume_store import ResumeStore
        from src.tools.browser.human_control import get_owned_runtime, unregister_owned_runtime
        binding=fact['binding']
        store=ResumeStore()
        record=await store.get_assistance(state.identity.tenant_id,fact['assistance_id'])
        if record is not None:
            if (record.runner_id!=binding['runner_id'] or record.run_id!=binding['run_id']
                    or record.agent_execution_id!=binding['runner_execution_id']
                    or record.tool_call_id!=binding['runner_tool_call_id']):
                raise CheckpointFailure('BROWSER_COMPLETION_OWNER_MISMATCH')
            await store.cas_state(record.tenant_id,record.assistance_id,{'resume_queued'},'resumed')
            await store.clear(record)
        runtime=await get_owned_runtime(state.identity.tenant_id,binding['run_id'])
        owner=getattr(getattr(runtime,'manager',None),'execution_owner',None)
        if (owner and owner.worker_boot==binding['owner_boot_id']
                and owner.browser_epoch==binding['browser_epoch']
                and runtime.manager.resource_close_confirmed(state.identity.tenant_id,binding['run_id'])):
            await unregister_owned_runtime(state.identity.tenant_id,binding['run_id'])

    @staticmethod
    def verification(call_id):
        return DispatchResult(None,wait=dict(kind='verification',tool_call_id=call_id,
            error_code='BROWSER_CONTINUATION_VERIFICATION_REQUIRED',
            question='原浏览器执行尚待核对，暂未继续原操作'))
