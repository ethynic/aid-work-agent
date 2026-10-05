"""Delegated execution inherits identity, durable ports and the single kernel."""

from src.core.agent_engine.contracts import AgentMode, CheckpointFailure, Identity, Outcome
import time
import asyncio


class ChildControl:
    def __init__(self, parent_state, parent_control, call_id):
        self.parent_state, self.parent_control, self.call_id = parent_state, parent_control, call_id
        self.started_at = time.monotonic()
        self.previous_duration_ms = int((parent_state.children.get(call_id) or {}).get('duration_ms') or 0)
        self.profile_id = None
        self.plan_manager = None

    def command(self):
        return self.parent_control.command()

    @property
    def durable_owner(self):
        return getattr(self.parent_control, "durable_owner", False)

    def decorate_wait(self, state):
        decorate = getattr(self.parent_control,'decorate_wait',None)
        if decorate is not None:
            decorate(state)

    async def authorize_dispatch(self, profile_id=None):
        authorize = getattr(self.parent_control, "authorize_dispatch", None)
        if authorize is not None:
            if self.durable_owner:
                await authorize(profile_id or self.profile_id)
            else:
                await authorize()

    async def save(self, state, boundary):
        self.profile_id = state.profile_id
        stopped = None
        if boundary in {'before_model','before_tool'}:
            try:
                await self.authorize_dispatch()
            except Exception as error:
                from ..ownership import StopRequested
                if not isinstance(error,StopRequested):
                    raise
                stopped = error
                state.outcome = Outcome.PAUSED if error.command=='pause' else Outcome.CANCELLED
                if boundary=='before_tool' and state.pending:
                    state.tools[state.pending[0].id].phase = 'prepared'
                if boundary=='before_model' and state.model_calls:
                    state.model_calls[-1]['phase'] = 'not_started'
        decorate = getattr(self.parent_control,'decorate_wait',None)
        if decorate is not None:
            decorate(state)
        plan = await asyncio.to_thread(self.plan_manager.get_plan,state.identity.session_id) if self.plan_manager is not None else None
        state.plan_ref = plan.plan_id if plan is not None else None
        self.parent_state.children[self.call_id] = {
            "execution_id": state.execution_id, "status": state.outcome.value,
            "checkpoint": state.checkpoint(), "waiting": state.waiting,
            "usage_watermark": state.usage_watermark,'profile_id':state.profile_id,
            'profile_fingerprint':getattr(self,'profile_fingerprint',None),
            'task_record': self.task_record.model_dump(mode='json') if getattr(self,'task_record',None) else None,
            'business_plan':plan.model_dump(mode='json') if plan is not None else None,
            'duration_ms':self.previous_duration_ms+int((time.monotonic()-self.started_at)*1000)}
        try:
            await self.parent_control.save(self.parent_state, f"child.{stopped.command if stopped else boundary}")
            if stopped:
                raise stopped
        except Exception as error:
            if getattr(error,'authoritative_storage_failure',False):
                raise
            raise CheckpointFailure("CHILD_CHECKPOINT_FAILED") from error


class ChildObserver:
    def __init__(self, parent_state, parent_control, parent_observer, task_record=None):
        self.parent_state, self.parent_control, self.parent_observer = parent_state, parent_control, parent_observer
        self.task_record = task_record

    async def model_usage(self, state, fact):
        # ChildControl already persisted this child's own model fact. Forward an
        # accounting observation without copying its response into parent steps.
        fact = {**fact, 'child_execution_id': fact.get('execution_id') or fact.get('child_execution_id') or state.execution_id}
        try:
            await self.parent_observer.model_usage(self.parent_state, fact)
        except Exception as error:
            if getattr(error, 'authoritative_storage_failure', False):
                raise
            raise CheckpointFailure("CHILD_USAGE_CHECKPOINT_FAILED") from error
        state.usage_watermark = len(state.model_calls)
        if self.task_record:
            self.task_record.update_progress(min(90, state.iteration * 5), f"Processing iteration {state.iteration}")

    async def event(self, state, event):
        await self.parent_observer.event(self.parent_state, {**event, "child_execution_id": state.execution_id})


class ChildExecution:
    is_master = False

    def __init__(self, parent_identity, config, execution_id, parent_plan_manager=None,
                 ports_factory=None, history_reader=None, resource_directory=None, isolated_plan_facts=False,
                 restored_state=None, restored_plan=None):
        from .profile import RuntimeResources
        from .executor import RuntimeExecution
        self.subagent_config, self.ports_factory = config, ports_factory
        self.restored_state = restored_state
        import hashlib
        from ..contracts import canonical_json
        self.profile_fingerprint = hashlib.sha256(canonical_json(config.model_dump(mode='json')).encode()).hexdigest()
        identity = Identity(parent_identity.tenant_id, parent_identity.user_id,
            parent_identity.session_id, parent_identity.source, parent_identity.session_kind)
        from src.core.plan_facts import PlanFacts
        resources = RuntimeResources(is_master=False, subagent_config=config,
            session_id=identity.session_id, execution_id=execution_id,
            parent_plan_manager=parent_plan_manager, mode=AgentMode.SUBAGENT,
            tenant_id=identity.tenant_id, user_id=identity.user_id,
            resource_directory=resource_directory, isolate_plan=True,session_kind=identity.session_kind,
            plan_store=PlanFacts() if isolated_plan_facts else None)
        if restored_plan is not None:
            from src.models.plan import ExecutionPlan
            resources.plan_manager._save_plan(identity.session_id,ExecutionPlan.model_validate(restored_plan))
        control, observer = ports_factory(None) if ports_factory else (None, None)
        if isinstance(control,ChildControl):
            control.plan_manager = resources.plan_manager
            control.profile_id = config.dir_name
            control.profile_fingerprint = self.profile_fingerprint
        self.runtime = RuntimeExecution(resources, identity, control=control, observer=observer,
                                        history_reader=history_reader)

    async def execute_as_subagent(self, task_description, parent_session_id,
                                 task_record=None, progress_callback=None, image_paths=None):
        from .executor import CompatibilityObserver
        if parent_session_id != self.runtime.identity.session_id:
            raise ValueError("CHILD_SESSION_MISMATCH")
        if self.ports_factory:
            self.runtime.control, self.runtime.observer = self.ports_factory(task_record)
            if isinstance(self.runtime.control,ChildControl):
                self.runtime.control.plan_manager = self.runtime.resources.plan_manager
            if getattr(self.runtime.control,'durable_owner',False):
                self.runtime.control.profile_id = self.subagent_config.dir_name
                self.runtime.control.profile_fingerprint = self.profile_fingerprint
                self.runtime.control.task_record = task_record
                if self.restored_state is None:
                    control = self.runtime.control
                    control.parent_state.children[control.call_id] = {
                        'execution_id':self.runtime.resources.execution_id,'status':'unstarted','unstarted':True,
                        'profile_id':self.subagent_config.dir_name,'profile_fingerprint':self.profile_fingerprint,
                        'task_record':task_record.model_dump(mode='json') if task_record else None,
                        'checkpoint':None}
                    await control.parent_control.save(control.parent_state,'child.created')
                await self.runtime.control.authorize_dispatch()
        else:
            self.runtime.observer = CompatibilityObserver(task_record=task_record)
        events = []
        async for event in self.runtime.run(task_description, task_record=task_record, image_paths=image_paths,
                                           state=self.restored_state):
            events.append(event)
            if progress_callback:
                await progress_callback(event)
        state = self.runtime.state
        from src.core.execution_usage import execution_token_usage
        usage = execution_token_usage(state.checkpoint())
        if state.outcome == Outcome.WAITING:
            wait = state.waiting or {}
            if wait.get("kind") == "clarification":
                return {"result": {"content": wait.get("question", ""), "status": "clarifying", **wait},
                    "summary": f"需要补充信息: {wait.get('question', '')}", "status": "clarifying", "token_usage": usage}
            return {"result": None, "summary": "等待操作", "status": "waiting", "waiting": wait, "token_usage": usage}
        if state.outcome != Outcome.COMPLETED:
            return {"result": None, "summary": "任务未完成", "status": state.outcome.value,
                    "error": state.error_code or state.outcome.value, "token_usage": usage}
        return {"result": {"content": state.output}, "summary": state.output[:500] or "Task completed",
                "token_usage": usage, "events": events}


def child_factory(identity, ports_factory=None, history_reader=None, resource_directory=None,
                  isolated_plan_facts=False):
    def build(*, config, execution_id, parent_plan_manager=None, **kwargs):
        child_ports = ports_factory() if ports_factory else None
        return ChildExecution(identity, config, execution_id, parent_plan_manager, child_ports, history_reader,
                              resource_directory, isolated_plan_facts)
    return build
