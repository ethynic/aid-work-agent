"""Same-loop native human actions on one original manager and IPC sequence."""

import asyncio
from datetime import datetime,timedelta,timezone

from src.tools.browser.owner_port import HumanActionOperation, HumanActionRejected, browser_human_action_scope
from src.tools.browser.human_control import HumanControlCoordinator
from src.tools.browser.executor.models import KeyboardCommand, PointerCommand,CommandResult
from .browser_completion import BrowserRecoveryUnavailable
from .browser_human_repository import BrowserHumanRepository
from .browser_web_auth import BrowserWebAuth
from .contracts import RunnerError
from .repository import RunnerRepository


async def accepted_completion_response(known):
    """Original accepted fact only; never selects or acts on a later wait."""
    row,wait,fact=known
    record=await HumanControlCoordinator().store.get_assistance(wait['tenant_id'],wait['assistance_id'])
    if (record is None or not record.continuation_id
            or any(getattr(record,key)!=wait[key] for key in ('tenant_id','user_id','run_id','assistance_id',
                'agent_execution_id','tool_call_id','runner_id'))
            or record.session_id!=row['session_id']):
        # Durable bac/TTL reconstruction is a separate migration boundary.
        raise HumanActionRejected('HUMAN_STATE_PROJECTION_UNAVAILABLE')
    phase=fact['continuation']['phase']
    return dict(success=True,state='resumed' if phase=='completed' else 'resume_queued',
        continuation_id=record.continuation_id)


class BrowserHumanActions:
    def __init__(self, owner):
        self.owner=owner
        self.repository=BrowserHumanRepository(owner.repository.connection_factory)
        self.auth=BrowserWebAuth(owner.process_resources['config'],owner.repository.connection_factory)

    def binding(self):
        owner=self.owner
        return owner._binding(owner.record.tenant_id,owner.record.run_id)

    async def guard(self,assertion,*,controlling=False,credit=False):
        try:
            await asyncio.to_thread(self.auth.authorize_action,assertion,credit=credit)
            await asyncio.to_thread(self.repository.check,self.binding(),assertion.assistance_id,
                controlling=controlling)
        except (RunnerError,BrowserRecoveryUnavailable) as error:
            raise HumanActionRejected(error.code) from None

    async def automatic_guard(self,assistance_id):
        """Current task permission; a page login is not the task's lifetime."""
        from .authorization import RunnerAuthorizer
        from .application_sources import build_source_capabilities
        from .source_receipts import source_offload,SourceUnavailable
        config=self.owner.process_resources['config']
        sources=build_source_capabilities(config.agent_runner,self.repository.connection_factory)
        auth=RunnerAuthorizer(config.agent_runner,self.repository.connection_factory,source_port=sources)
        try:
            row,_,_,_=await source_offload(self.repository.check,self.binding(),assistance_id)
            prepared=await sources.prepare_execution(row)
            def check():
                current,node,_,_=self.repository.check(self.binding(),assistance_id)
                if current['runner_id']!=row['runner_id'] or current['revision']!=row['revision']:
                    raise RunnerError('CHECKPOINT_REVISION_CHANGED',409)
                principal=auth.authorize_persisted(current,prepared_source=prepared)
                if node['profile_id']!=current['profile_id']:
                    auth.authorize_child_profile(principal,node['profile_id'])
            await source_offload(check)
        except (RunnerError,SourceUnavailable,BrowserRecoveryUnavailable) as error:
            raise HumanActionRejected(error.code) from None
        finally:
            await sources.close()

    async def _project(self,wait):
        """Redis is a retryable projection of the exact committed PG expiry."""
        coordinator=HumanControlCoordinator()
        wait=await asyncio.to_thread(self.repository.projection,self.binding(),wait['assistance_id'])
        return await coordinator.store.project_owned_assistance(self.owner,wait)

    async def take(self,assertion):
        await self.guard(assertion,credit=True)
        wait=await asyncio.to_thread(self.repository.take,self.binding(),assertion.assistance_id)
        record=await self._project(wait)
        from src.tools.browser.run_manager import RunState
        current=await self.owner.manager.store.get(record.tenant_id,record.run_id)
        if current.state=='WAITING_HUMAN':
            await self.owner.manager.transition(record.tenant_id,record.run_id,RunState.RUNNING_HUMAN)
        if record.completion_mode=='auto_or_confirm':
            await HumanControlCoordinator()._start_completion_monitor(record)
        return dict(success=True,state='controlling')

    async def extend(self,assertion):
        await self.guard(assertion,credit=True)
        from src.tools.browser.human_control import HUMAN_LEASE_SECONDS
        wait=await asyncio.to_thread(self.repository.extend,self.binding(),assertion.assistance_id,HUMAN_LEASE_SECONDS)
        await self._project(wait)
        return dict(success=True,expires_at=wait['expiry_instant'].isoformat())

    async def complete(self,assertion):
        known=await asyncio.to_thread(self.auth.completion_read_assertion,assertion)
        if known is not None:
            return await accepted_completion_response(known)
        await asyncio.to_thread(self.auth.authorize_action,assertion,credit=True)
        fact=await asyncio.to_thread(self.repository.completion,self.binding(),assertion.assistance_id)
        if fact is None:
            await self.guard(assertion)
            operation=HumanActionOperation(lambda:self.guard(assertion))
            async def sample():
                with browser_human_action_scope(operation):
                    return await self.owner.complete_assistance(assertion.assistance_id)
            result=await self.retain(sample(),operation)
        else:
            result=dict(completion_ref=fact['completion_ref'],missing_conditions=[])
        missing=result['missing_conditions']
        if missing:
            return dict(success=False,error_code='HUMAN_COMPLETION_NOT_MET',missing_conditions=missing,state='controlling')
        # This is accepted completion, not a claim of tool/action termination.
        record=await HumanControlCoordinator().store.get_assistance(assertion.tenant_id,assertion.assistance_id)
        if record is None:
            raise HumanActionRejected('HUMAN_STATE_PROJECTION_UNAVAILABLE')
        return dict(success=True,state='resume_queued',continuation_id=record.continuation_id)

    async def cancel(self,assertion):
        row,principal=await asyncio.to_thread(self.auth.authorize_action,assertion,execute=False)
        await asyncio.to_thread(RunnerRepository(self.repository.connection_factory).cancel,principal,row['runner_id'])
        return dict(success=True)

    async def input(self,assertion,message,runtime):
        await self.guard(assertion,controlling=True,credit=True)
        if not ((message.type=='keyboard' and message.key) or
                (message.type=='pointer' and message.action in {'click','move','down','up','wheel'})):
            raise HumanActionRejected('HUMAN_INPUT_INVALID')
        operation=HumanActionOperation(lambda:self.guard(assertion,controlling=True))
        async def dispatch():
            with browser_human_action_scope(operation):
                def builder():
                    fields=runtime.orchestrator.page_ops._fields()
                    if message.type=='keyboard':
                        return KeyboardCommand(**fields,key=message.key)
                    return PointerCommand(**fields,action=message.action,x=message.x,y=message.y,
                        delta_x=message.delta_x,delta_y=message.delta_y)
                deferred=getattr(runtime.executor,'execute_deferred',None)
                if deferred is None:
                    raise HumanActionRejected('HUMAN_DEFERRED_IPC_REQUIRED')
                from src.config.settings import settings
                return await deferred(builder,CommandResult,
                    deadline_at=datetime.now(timezone.utc)+timedelta(seconds=settings.tools.browser.command_timeout))
        return await self.retain(dispatch(),operation)

    async def retain(self,command,watermark=None):
        task=asyncio.create_task(command)
        tasks=self.owner.process_resources.setdefault('human_commands',set())
        tasks.add(task)
        def finish(completed):
            tasks.discard(completed)
            if not completed.cancelled():
                completed.exception()  # detached commands never log private results
        task.add_done_callback(finish)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if watermark is not None and not watermark.write_started:
                task.cancel()
                await asyncio.gather(task,return_exceptions=True)
            raise
