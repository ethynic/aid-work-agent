"""Restore an existing delegated execution, preserving its original task IDs."""

import asyncio

from src.core.agent_engine.contracts import DispatchResult, ExecutionState, Outcome
from ..contracts import RunnerError
from .child import ChildControl, ChildObserver, ChildExecution


class ChildRecoveryAdapter:
    def __init__(self, runtime, profiles, owned_executor=None):
        self.runtime, self.profiles = runtime, profiles
        self.owned_executor = owned_executor or runtime.resources.subagent_executor

    async def recover(self, parent, call):
        fact = parent.children.get(call.id)
        if not fact or (not isinstance(fact.get('checkpoint'),dict) and fact.get('unstarted') is not True):
            raise RunnerError('CHECKPOINT_CHILD_INCOMPLETE',409)
        restored = ExecutionState.restore(fact['checkpoint']) if fact.get('checkpoint') else None
        from ..aggregate_tree import child_tree_finished
        stopped = {Outcome.COMPLETED,Outcome.FAILED,Outcome.CANCELLED,Outcome.ITERATION_LIMIT}
        if restored is None or restored.outcome not in stopped or not child_tree_finished(restored.checkpoint()):
            config, fingerprint = await asyncio.to_thread(self.profiles.resolve,fact['profile_id'])
            if fingerprint != fact.get('profile_fingerprint'):
                raise RunnerError('RECOVERY_PROFILE_CHANGED',409)
            def ports(record):
                return (ChildControl(parent,self.runtime.control,call.id),
                        ChildObserver(parent,self.runtime.control,self.runtime.observer,record))
            execution_id = fact['execution_id']
            child = await asyncio.to_thread(ChildExecution,parent.identity,config,execution_id,
                self.runtime.resources.plan_manager,ports,self.runtime.history.reader,
                self.runtime.resources.resource_directory,True,restored,fact.get('business_plan'))
            child.runtime.child_recovery = ChildRecoveryAdapter(child.runtime,self.profiles,self.owned_executor)
            task_record = fact.get('task_record')
            if not task_record:
                raise RunnerError('CHECKPOINT_CHILD_TASK_MISSING',409)
            from src.subagents.protocol import SubagentTaskRecord
            child.runtime.control.task_record = SubagentTaskRecord.model_validate(task_record)
            # Restore descendants before acquiring a parent execution slot. This
            # keeps the same owned-task registry without semaphore deadlock when
            # a completed parent owns an unfinished leaf and concurrency is one.
            if restored is not None:
                for descendant_call, descendant in list(restored.children.items()):
                    checkpoint = descendant.get('checkpoint') or {}
                    if checkpoint.get('outcome') in {item.value for item in stopped} and child_tree_finished(checkpoint):
                        continue
                    descendant_fact = restored.tools.get(descendant_call)
                    if descendant_fact is None:
                        raise RunnerError('CHECKPOINT_CHILD_CALL_MISMATCH',409)
                    result = await child.runtime.child_recovery.recover(restored,descendant_fact.call)
                    if result.wait:
                        if restored.outcome==Outcome.COMPLETED:
                            restored.resources['root_execution_outcome']='completed'
                        restored.outcome,restored.waiting = Outcome.WAITING,result.wait
                        await child.runtime.control.save(restored,'descendant_waiting')
                        return DispatchResult(None,success=False,wait={'kind':'child_wait',
                            'tool_call_id':call.id,'execution_id':execution_id,'child_wait':result.wait,
                            'child_status':'waiting'})
            if self.owned_executor is None:
                raise RunnerError('RECOVERY_CHILD_OWNER_UNAVAILABLE',409)
            if restored is not None and restored.outcome in stopped and child_tree_finished(restored.checkpoint()):
                await child.runtime.control.save(restored,'descendants_restored')
            else:
                await self.owned_executor.restore_owned_task(child,task_record,
                    parent.identity.session_id)
                record = await self.owned_executor.wait_for_result(execution_id,timeout=7200)
                if record is None:
                    raise RunnerError('RECOVERY_CHILD_RESULT_LOST',409)
                parent.children[call.id]['task_record'] = record.model_dump(mode='json')
            # The child's durable port saved the actual restored leaf state.
            restored = ExecutionState.restore(parent.children[call.id]['checkpoint'])
        if restored.outcome == Outcome.WAITING:
            return DispatchResult(None,success=False,wait={
                'kind':'child_wait','tool_call_id':call.id,'execution_id':restored.execution_id,
                'child_wait':restored.waiting,'child_status':'waiting'})
        if restored.outcome == Outcome.PAUSED:
            return DispatchResult(None,success=False,wait={
                'kind':'child_wait','tool_call_id':call.id,'execution_id':restored.execution_id,
                'child_status':'paused'})
        if restored.outcome != Outcome.COMPLETED:
            return DispatchResult({'success':False,'error':restored.error_code or restored.outcome.value},success=False)
        return DispatchResult({'success':True,'execution_id':restored.execution_id,
            'result':{'content':restored.output},'summary':restored.output[:500]})

    async def restore_orphaned_children(self, state):
        """An already answered parent can still own unfinished delegated work."""
        pending = {call.id for call in state.pending}
        from ..aggregate_tree import child_tree_finished
        for call_id, child in list(state.children.items()):
            if call_id in pending or ((child.get('checkpoint') or {}).get('outcome') in {'completed','failed','cancelled','iteration_limit'}
                                     and child_tree_finished(child['checkpoint'])):
                continue
            fact = state.tools.get(call_id)
            if fact is None:
                raise RunnerError('CHECKPOINT_CHILD_CALL_MISMATCH',409)
            if state.outcome==Outcome.COMPLETED:
                state.resources['root_execution_outcome']='completed'
            result = await self.recover(state,fact.call)
            if result.wait:
                state.outcome, state.waiting = Outcome.WAITING, result.wait
                return False
            if not result.success:
                state.outcome, state.error_code = Outcome.FAILED,'RESTORED_CHILD_FAILED'
                return False
        if state.resources.get('root_continuation_requested'):
            # Original descendants and tool facts have settled. New root input
            # must reach a new iteration, while completed children stay closed.
            state.resources.pop('root_execution_outcome',None)
            state.resources.pop('root_continuation_requested',None)
            state.outcome = Outcome.RUNNING
        elif state.resources.get('root_execution_outcome')=='completed':
            # All original calls are already paired. Persist user supplements
            # without invoking a second parent model solely to write history.
            state.messages.extend(state.followup_messages)
            state.followup_messages = []
            state.outcome = Outcome.COMPLETED
        return True
