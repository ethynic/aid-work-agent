"""Validate and prepare a private execution tree before acquiring a new attempt.

Tool-specific recovery proof belongs to adapters. This coordinator owns neither
provider IO nor tool dispatch and never assembles a replacement conversation.
"""

import copy
from dataclasses import dataclass
from pathlib import Path

from src.core.agent_engine.contracts import AgentMode, ExecutionState, Outcome
from .adapter_contract import adapter_contract_mismatch
from .contracts import RunnerError


@dataclass
class PreparedRecovery:
    checkpoint: dict
    states: dict[str, ExecutionState]
    principal: object


class RecoveryCoordinator:
    def __init__(self, authorizer, profiles, *, tool_recovery=None, attachment_resolver=None,
                 resource_directory=None):
        self.authorizer, self.profiles = authorizer, profiles
        self.tool_recovery = tool_recovery
        self.attachment_resolver = attachment_resolver
        self.resource_directory = resource_directory

    def prepare(self, row, control=None, *, prepared_source=None, timer_trigger=False,
                continuous_trigger=False):
        manual = not (timer_trigger or continuous_trigger)
        if continuous_trigger:
            from .continuous import interrupted_candidate, assert_continuous_supported
            if timer_trigger or control is not None or not interrupted_candidate(row):
                raise RunnerError('CONTINUOUS_RECOVERY_SOURCE_INVALID',409)
            assert_continuous_supported(row['source'], row['session_kind'])
        elif timer_trigger:
            from .durable_sleep import replayable_activation,validate_armed
            if row['status']=='waiting':
                validate_armed(row)
            elif row['status']!='interrupted' or not replayable_activation(row):
                raise RunnerError('SLEEP_TIMER_MISMATCH',409)
        elif control is None:
            raise RunnerError('RECOVERY_CONTROL_REQUIRED',409)
        kwargs={'prepared_source':prepared_source} if prepared_source is not None else {}
        principal = self.authorizer.authorize_persisted(row,**kwargs)
        self.authorizer.assert_credit(principal)
        _, fingerprint = self.profiles.resolve(row['profile_id'], row.get('tenant_id'))
        if fingerprint != row['profile_fingerprint']:
            raise RunnerError('RECOVERY_PROFILE_CHANGED',409)
        checkpoint = copy.deepcopy(row.get('checkpoint') or {})
        policy=checkpoint.get('continuous')
        if policy is not None and (not isinstance(policy,dict)
                or type(policy.get('version')) is not int or policy.get('version')!=1):
            raise RunnerError('RECOVERY_CONTINUOUS_VERSION_UNSUPPORTED',409)
        timer=checkpoint.get('durable_sleep') or {}
        if timer:
            from src.config.settings import settings
            if type(timer.get('version')) is not int or timer.get('version')!=1:
                raise RunnerError('RECOVERY_SLEEP_VERSION_UNSUPPORTED',409)
            if not settings.agent_runner.durable_sleep_enabled:
                raise RunnerError('DURABLE_SLEEP_DISABLED',409)
        if checkpoint.get('pending_finalization'):
            raise RunnerError('RECOVERY_FINALIZATION_PENDING',409)
        states = {}
        from .runtime_manifest import configuration_fingerprint, configuration_matches

        if continuous_trigger and checkpoint.get('execution') is None:
            # The claim transaction separately proves there are no admitted IO
            # receipts. No execution existed to restore before initial prepare.
            checkpoint['continuous_recovery'] = {'version':1,'source_attempt':row['attempt']}
            return PreparedRecovery(checkpoint,states,principal)

        def validate(data, *, execution_id, fingerprint=None, active=True, business_plan=None):
            try:
                state = ExecutionState.restore(data)
            except (AttributeError,TypeError,ValueError,KeyError) as error:
                raise RunnerError('CHECKPOINT_TREE_INVALID',409) from error
            if state.execution_id != execution_id or state.identity != principal.identity or execution_id in states:
                raise RunnerError('CHECKPOINT_IDENTITY_MISMATCH',409)
            states[execution_id] = state
            expected_role = (AgentMode.MASTER if row['profile_id']=='main' else AgentMode.STANDALONE
                             ) if execution_id==row['runner_id'] else AgentMode.SUBAGENT
            if state.role != expected_role or (execution_id==row['runner_id'] and state.profile_id!=row['profile_id']):
                raise RunnerError('CHECKPOINT_PROFILE_MISMATCH',409)
            if len(states)>1000:
                raise RunnerError('CHECKPOINT_TREE_INVALID',409)
            if active:
                if not fingerprint:
                    raise RunnerError('RECOVERY_PROFILE_FINGERPRINT_MISSING',409)
                config, current = self.profiles.resolve(state.profile_id, row.get('tenant_id'))
                if current != fingerprint or (getattr(config,'version',None) != state.profile_version):
                    raise RunnerError('RECOVERY_PROFILE_CHANGED',409)
                # Unknown/retired adapter contract versions are rejected before
                # configuration drift: a semantic mismatch is more fundamental
                # than a fingerprint change and must never be re-executed by the
                # current adapters. Missing fields fall back to the declared
                # legacy baseline. Completed read-only subtrees keep the same
                # boundary as configuration_fingerprint and stay unvalidated.
                if adapter_contract_mismatch(state.resources):
                    raise RunnerError('RECOVERY_ADAPTER_VERSION_UNSUPPORTED',409)
                if not configuration_matches(state.resources.get('configuration_fingerprint'), current, config):
                    raise RunnerError('RECOVERY_CONFIGURATION_CHANGED',409)
                state.resources['configuration_fingerprint'] = configuration_fingerprint(current, config)
                if state.profile_id != row['profile_id']:
                    self.authorizer.authorize_child_profile(principal,state.profile_id)
                workspace = state.resources.get('workspace')
                if workspace and not Path(workspace).is_dir():
                    raise RunnerError('RECOVERY_RESOURCE_LOST',409)
                if self.resource_directory is not None:
                    from .resource_paths import validate_workspaces
                    validate_workspaces(self.resource_directory,state)
                if state.plan_ref:
                    if not isinstance(business_plan,dict) or business_plan.get('plan_id')!=state.plan_ref:
                        raise RunnerError('RECOVERY_PLAN_VERIFICATION_REQUIRED',409)
                    from src.models.plan import ExecutionPlan
                    try:
                        ExecutionPlan.model_validate(business_plan)
                    except ValueError as error:
                        raise RunnerError('RECOVERY_PLAN_VERIFICATION_REQUIRED',409) from error
                bindings = state.resources.get('plan_tasks') or {}
                pending_ids = {call.id for call in state.pending}
                for call_id,binding in bindings.items():
                    fact = state.tools.get(call_id)
                    if fact is None or not isinstance(binding,dict) or not binding.get('plan_id') or not binding.get('task_id'):
                        raise RunnerError('RECOVERY_PLAN_VERIFICATION_REQUIRED',409)
                    if fact.phase=='completed' and fact.result_recorded and call_id not in pending_ids:
                        continue  # Historical bindings may reference a replaced plan.
                    if (not isinstance(business_plan,dict) or binding['plan_id']!=state.plan_ref
                            or not any(task['task_id']==binding['task_id'] for task in business_plan.get('tasks',[]))):
                        raise RunnerError('RECOVERY_PLAN_VERIFICATION_REQUIRED',409)
                running = any(task.get('status')=='running' for task in (business_plan or {}).get('tasks',[]))
                if running:
                    from .runtime.tools import owns_plan_task
                    if any(state.tools[call.id].phase in {'dispatching','waiting'}
                           and owns_plan_task(call.name) and call.id not in bindings for call in state.pending):
                        raise RunnerError('RECOVERY_PLAN_VERIFICATION_REQUIRED',409)
            for call_id, child in state.children.items():
                if not isinstance(child,dict) or not child.get('execution_id'):
                    raise RunnerError('CHECKPOINT_CHILD_INCOMPLETE',409)
                parent_call = state.tools.get(call_id)
                if parent_call is None:
                    raise RunnerError('CHECKPOINT_CHILD_CALL_MISMATCH',409)
                task = child.get('task_record') or {}
                finished = (child.get('checkpoint') or {}).get('outcome')=='completed'
                if (task and (task.get('execution_id')!=child['execution_id'] or not task.get('task_id')
                        or task.get('subagent_name')!=parent_call.call.arguments.get('subagent_name'))
                        or not task and not finished):
                    raise RunnerError('CHECKPOINT_CHILD_TASK_MISMATCH',409)
                if child.get('unstarted') is True:
                    if child.get('checkpoint') or child.get('status')!='unstarted' or call_id not in state.tools:
                        raise RunnerError('CHECKPOINT_CHILD_STATUS_MISMATCH',409)
                    _, child_fingerprint = self.profiles.resolve(child.get('profile_id'), row.get('tenant_id'))
                    if child_fingerprint!=child.get('profile_fingerprint'):
                        raise RunnerError('RECOVERY_PROFILE_CHANGED',409)
                    self.authorizer.authorize_child_profile(principal,child['profile_id'])
                    continue
                if not isinstance(child.get('checkpoint'),dict):
                    raise RunnerError('CHECKPOINT_CHILD_INCOMPLETE',409)
                checkpoint_outcome = child['checkpoint'].get('outcome')
                if child.get('status')=='completed' and checkpoint_outcome!='completed':
                    raise RunnerError('CHECKPOINT_CHILD_STATUS_MISMATCH',409)
                from .aggregate_tree import child_tree_finished
                finished = checkpoint_outcome in {'completed','failed','cancelled','iteration_limit'} and child_tree_finished(child['checkpoint'])
                validate(child['checkpoint'],execution_id=child['execution_id'],
                         fingerprint=child.get('profile_fingerprint'),active=not finished,
                         business_plan=child.get('business_plan'))
                if call_id not in state.tools:
                    raise RunnerError('CHECKPOINT_CHILD_CALL_MISMATCH',409)
            return state

        if checkpoint.get('unstarted'):
            if timer_trigger:
                raise RunnerError('SLEEP_TIMER_MISMATCH',409)
            if checkpoint.get('execution'):
                raise RunnerError('CHECKPOINT_TREE_INVALID',409)
            if control['action'] != 'resume':
                raise RunnerError('CONTROL_WAIT_STALE',409)
            if self._has_resume_input(control):
                message = self._user_message(row,control)
                checkpoint.setdefault('pending_root_inputs',[]).append(message)
                self._remember_input(checkpoint,control)
        else:
            root = validate(checkpoint.get('execution'),execution_id=row['runner_id'],
                            fingerprint=row['profile_fingerprint'],business_plan=checkpoint.get('business_plan'))
            if timer_trigger and row['status']=='waiting':
                from .durable_sleep import complete_sleep
                complete_sleep(row,states,checkpoint,trigger='timer')
            elif manual and control['action']=='resume' and (checkpoint.get('durable_sleep') or {}).get('phase')=='armed':
                from .durable_sleep import complete_sleep
                from .control_repository import ControlRepository
                from types import SimpleNamespace
                ControlRepository.assert_wait(row,SimpleNamespace(action='resume',
                    wait_id=control['payload'].get('wait_id'),
                    target_execution_id=control['payload'].get('target_execution_id')))
                complete_sleep(row,states,checkpoint,trigger='user_resume')
            if manual and control['action']=='resume':
                guards = [state for state in states.values() if (state.waiting or {}).get('kind')=='continuous_guard']
                if guards:
                    from .control_repository import ControlRepository
                    from types import SimpleNamespace
                    request = SimpleNamespace(action='resume',wait_id=control['payload'].get('wait_id'),
                        target_execution_id=control['payload'].get('target_execution_id'))
                    ControlRepository.assert_wait(row,request)
                    target = next((state for state in guards if state.execution_id==request.target_execution_id
                        and state.waiting.get('wait_id')==request.wait_id),None)
                    if target is None:
                        raise RunnerError('CONTROL_WAIT_STALE',409)
                    target.resources['continuous_segment_start']=target.resources.get('business_iterations',target.iteration)
                    target.waiting=None
            if manual and control['action']=='reply':
                self._apply_reply(row,control,root,states,checkpoint)
            elif manual and control['action']=='browser_complete':
                if self.tool_recovery is None:
                    raise RunnerError('RECOVERY_TOOL_VERIFICATION_REQUIRED',409)
                self.tool_recovery.apply_completion(row,control,states)
            elif manual and self._has_resume_input(control):
                if root.outcome in {Outcome.FAILED,Outcome.CANCELLED,Outcome.ITERATION_LIMIT}:
                    raise RunnerError('RECOVERY_ROOT_STATE_VERIFICATION_REQUIRED',409)
                used=root.resources.get('business_iterations',root.iteration)
                if root.resources.get('continuous_enabled'):
                    used-=root.resources.get('continuous_segment_start',0)
                if used >= root.max_iterations:
                    raise RunnerError('CONTINUATION_LIMIT_REACHED',409)
                root.followup_messages.append(self._user_message(row,control))
                root.resources.setdefault('continuation_inputs',{})[control['control_id']] = 'queued'
                root.resources['root_continuation_requested'] = True
                self._remember_input(checkpoint,control)
            for state in states.values():
                from .aggregate_tree import child_tree_finished
                if continuous_trigger:
                    # This attempt has lost ownership. Its unobserved model IO
                    # remains uncertain usage, rather than a live operation that
                    # could prevent every future durable sleep.
                    for fact in state.model_calls:
                        if fact.get('phase') == 'started':
                            fact['phase'] = 'unknown'
                stopped = state.outcome in {Outcome.COMPLETED,Outcome.FAILED,Outcome.CANCELLED,Outcome.ITERATION_LIMIT}
                facts = list(state.tools.values()) if stopped else [state.tools.get(call.id) for call in state.pending]
                for fact in facts:
                    if fact is None:
                        raise RunnerError('CHECKPOINT_TOOL_FACT_MISSING',409)
                    if fact.phase in ('dispatching','waiting') and fact.call.id not in state.children:
                        from .durable_sleep import recover_uncommitted
                        if recover_uncommitted(state,fact):
                            continue
                        if self.tool_recovery is None or not self.tool_recovery.can_restore(row,state,fact):
                            raise RunnerError('RECOVERY_TOOL_VERIFICATION_REQUIRED',409)
                if stopped and child_tree_finished(state.checkpoint()):
                    continue
                if state.outcome == Outcome.COMPLETED:
                    # Restore-only child ports own unfinished descendants; the
                    # parent's original completed model steps stay completed.
                    continue
                if stopped:
                    # No retry semantics exist for a stopped failed/cancelled
                    # container with an unfinished descendant. Do not consume a
                    # command and silently restart its own model steps.
                    raise RunnerError('RECOVERY_CHILD_STATE_VERIFICATION_REQUIRED',409)
                state.outcome = Outcome.RUNNING
            if manual and self._has_resume_input(control) and root.outcome==Outcome.COMPLETED:
                # Keep completed steps and child facts; the restore-only adapter
                # drains original descendants before this root's next iteration.
                root.resources['root_execution_outcome'] = 'completed'
                root.outcome = Outcome.RUNNING
            self._capture(root,states)
            checkpoint['execution'] = root.checkpoint()
        if manual:
            checkpoint['applied_control_id'] = control['control_id']
            checkpoint.pop('continuous_recovery',None)
        elif continuous_trigger:
            checkpoint['continuous_recovery'] = {'version':1, 'source_attempt':row['attempt']}
        checkpoint.pop('sleep_blocked',None)
        checkpoint.pop('sleep_retry',None)
        return PreparedRecovery(checkpoint,states,principal)

    @staticmethod
    def _has_resume_input(control):
        payload = control.get('payload') or {}
        return control['action']=='resume' and bool(payload.get('answer','').strip() or payload.get('attachments'))



    def _user_message(self, row, control):
        payload = control['payload']
        attachments = payload.get('attachments') or []
        if attachments:
            if self.attachment_resolver is None:
                raise RunnerError('RECOVERY_ATTACHMENT_UNSUPPORTED',409)
            attachments = self.attachment_resolver({**row,'input':{'attachments':attachments}})
        message = {'role':'user','content':payload.get('answer',''),
                   'metadata':{'control_id':control['control_id'],
                               'submitted_text':payload.get('answer',''),
                               'accepted_at':str(control['accepted_at']),
                               'attachments':copy.deepcopy(payload.get('attachments') or [])}}
        if attachments:
            message['_attachments'] = attachments
        return message

    @staticmethod
    def _remember_input(checkpoint, control):
        payload = control['payload']
        checkpoint.setdefault('supplemental_inputs',[]).append({
            'control_id':control['control_id'],'message_id':control['control_id']+':user',
            'text':payload.get('answer',''),'attachments':copy.deepcopy(payload.get('attachments') or []),
            'accepted_at':str(control['accepted_at'])})

    def _apply_reply(self, row, control, root, states, checkpoint):
        payload = control['payload']
        target = states.get(payload.get('target_execution_id'))
        wait = target.waiting if target else None
        if not wait or wait.get('kind')!='clarification' or wait.get('wait_id')!=payload.get('wait_id'):
            raise RunnerError('CONTROL_WAIT_STALE',409)
        fact = target.tools.get(wait.get('tool_call_id'))
        if fact is None or fact.phase!='waiting':
            raise RunnerError('CONTROL_WAIT_STALE',409)
        from .continuous import apply_confirmation
        apply_confirmation(row,control,target,states,checkpoint,wait,fact)
        attachments = payload.get('attachments') or []
        if attachments:
            if self.attachment_resolver is None:
                raise RunnerError('RECOVERY_ATTACHMENT_UNSUPPORTED',409)
            attachments = self.attachment_resolver({**row,'input':{'attachments':attachments}})
        fact.phase, fact.success = 'completed', True
        fact.result = {'success':True,'answer':payload['answer'],'attachments':attachments,
                       'wait_id':payload['wait_id']}
        fact.preserve_content = True
        target.waiting = None
        # The application records the supplemental input once. The kernel only
        # exposes it after every sibling tool_call has a paired result.
        message = {'role':'user','content':payload['answer'],
                   'metadata':{'control_id':control['control_id'],
                               'submitted_text':payload['answer'],
                               'accepted_at':str(control['accepted_at']),
                               'attachments':copy.deepcopy(payload.get('attachments') or [])}}
        if attachments:
            message['_attachments'] = attachments
        target.followup_messages.append(copy.deepcopy(message))
        if target is not root:
            root.followup_messages.append(copy.deepcopy(message))
        checkpoint.setdefault('supplemental_inputs',[]).append({
            'control_id':control['control_id'],'message_id':control['control_id']+':user',
            'text':payload['answer'],'attachments':copy.deepcopy(payload.get('attachments') or []),
            'accepted_at':str(control['accepted_at'])})

    @staticmethod
    def _capture(state, states):
        for child in state.children.values():
            if child.get('unstarted') is True:
                continue
            restored = states[child['execution_id']]
            RecoveryCoordinator._capture(restored,states)
            child.update(checkpoint=restored.checkpoint(),status=restored.outcome.value,
                         waiting=copy.deepcopy(restored.waiting))
