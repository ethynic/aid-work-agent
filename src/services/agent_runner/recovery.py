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

    def prepare(self, row, control, *, prepared_source=None):
        kwargs={'prepared_source':prepared_source} if prepared_source is not None else {}
        principal = self.authorizer.authorize_persisted(row,**kwargs)
        self.authorizer.assert_credit(principal)
        _, fingerprint = self.profiles.resolve(row['profile_id'])
        if fingerprint != row['profile_fingerprint']:
            raise RunnerError('RECOVERY_PROFILE_CHANGED',409)
        checkpoint = copy.deepcopy(row.get('checkpoint') or {})
        if checkpoint.get('pending_finalization'):
            raise RunnerError('RECOVERY_FINALIZATION_PENDING',409)
        states = {}
        from .runtime_manifest import configuration_fingerprint

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
                config, current = self.profiles.resolve(state.profile_id)
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
                if state.resources.get('configuration_fingerprint') != configuration_fingerprint(current):
                    raise RunnerError('RECOVERY_CONFIGURATION_CHANGED',409)
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
                    _, child_fingerprint = self.profiles.resolve(child.get('profile_id'))
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
            if control['action']=='reply':
                self._apply_reply(row,control,root,states,checkpoint)
            elif control['action']=='browser_complete':
                if self.tool_recovery is None:
                    raise RunnerError('RECOVERY_TOOL_VERIFICATION_REQUIRED',409)
                self.tool_recovery.apply_completion(row,control,states)
            elif self._has_resume_input(control):
                if root.outcome in {Outcome.FAILED,Outcome.CANCELLED,Outcome.ITERATION_LIMIT}:
                    raise RunnerError('RECOVERY_ROOT_STATE_VERIFICATION_REQUIRED',409)
                if root.iteration >= root.max_iterations:
                    raise RunnerError('CONTINUATION_LIMIT_REACHED',409)
                root.followup_messages.append(self._user_message(row,control))
                root.resources.setdefault('continuation_inputs',{})[control['control_id']] = 'queued'
                root.resources['root_continuation_requested'] = True
                self._remember_input(checkpoint,control)
            for state in states.values():
                from .aggregate_tree import child_tree_finished
                stopped = state.outcome in {Outcome.COMPLETED,Outcome.FAILED,Outcome.CANCELLED,Outcome.ITERATION_LIMIT}
                facts = list(state.tools.values()) if stopped else [state.tools.get(call.id) for call in state.pending]
                for fact in facts:
                    if fact is None:
                        raise RunnerError('CHECKPOINT_TOOL_FACT_MISSING',409)
                    if fact.phase in ('dispatching','waiting') and fact.call.id not in state.children:
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
            if self._has_resume_input(control) and root.outcome==Outcome.COMPLETED:
                # Keep completed steps and child facts; the restore-only adapter
                # drains original descendants before this root's next iteration.
                root.resources['root_execution_outcome'] = 'completed'
                root.outcome = Outcome.RUNNING
            self._capture(root,states)
            checkpoint['execution'] = root.checkpoint()
        checkpoint['applied_control_id'] = control['control_id']
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
