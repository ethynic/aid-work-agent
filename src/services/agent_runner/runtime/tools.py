"""Tool policy and concrete tool capabilities; the kernel knows no tool names."""

import asyncio
from contextlib import asynccontextmanager
import json
from src.core.agent_engine.contracts import CheckpointFailure, DispatchResult, ToolCall
from src.core.agent_events import _extract_image_refs_from_tool_result, _normalize_image_placement, make_event
from src.core.tool_suspension import ToolSuspension
from src.tools.base import ExecutionTarget
from .history_projection import waiting_messages


def owns_plan_task(name):
    return name not in {'create_plan','clarify','use_skill','skill_complete'}


class ToolDispatcher:
    def __init__(self, *, catalog, registry, executor, control_tools, local, skills,
                 plan_manager, subagent_config, control, defer_names=None,
                 verbose_config=None, verbose_state=None, policy_source=None, child_recovery=None):
        self.catalog, self.registry, self.executor = catalog, registry, executor
        self.control_tools, self.local, self.skills = control_tools, local, skills
        self.plan_manager, self.subagent_config, self.control = plan_manager, subagent_config, control
        self.defer_names, self.policy_source = defer_names, policy_source
        self.verbose_config, self.verbose_state = verbose_config, verbose_state
        self.policy_iterations = set()
        self.child_recovery = child_recovery
        from .browser_tools import BrowserToolAdapter
        self.browser = BrowserToolAdapter(control,getattr(local,'state',None))

    def definitions(self):
        definitions = self.catalog._get_tools()
        if self.defer_names is not None:
            definitions = [tool for tool in definitions if tool.get("name") in self.defer_names]
        return definitions

    def display_name(self, call):
        return self.catalog._get_tool_display_name(call.name, call.arguments)

    async def recover_completed(self,state,call):
        """Read only a fully completed original child; never start a leaf."""
        child=state.children.get(call.id)
        checkpoint=(child or {}).get('checkpoint')
        from ..aggregate_tree import child_tree_finished
        if (self.child_recovery is None or not isinstance(checkpoint,dict)
                or checkpoint.get('outcome')!='completed' or not child_tree_finished(checkpoint)):
            return None
        result=await self.child_recovery.recover(state,call)
        await self._finish_plan_task(state,call,result)
        return result

    async def recover(self, state, call):
        if call.id in state.children and self.child_recovery is not None:
            result = await self.child_recovery.recover(state,call)
            await self._finish_plan_task(state,call,result)
            return result
        tool = self.registry.get_tool(call.name)
        from src.tools.browser.automation_tool import BrowserAutomationTool
        if type(tool) is BrowserAutomationTool and getattr(self.control,'durable_owner',False):
            context=self.control_tools.context.derive(tool_call_id=call.id,agent_execution_id=state.execution_id)
            result=await self.browser.recover(state,call,context)
            await self._finish_plan_task(state,call,result)
            return result
        if tool and getattr(tool, 'execution_target', None) == ExecutionTarget.LOCAL_REQUIRED:
            context = self.control_tools.context.derive(tool_call_id=call.id, agent_execution_id=state.execution_id)
            terminal = None
            async for kind, payload in self.local._run_local_required_tool(call.name, call.arguments,
                    state.identity.tenant_id, state.identity.user_id, context=context, recover=True,
                    cancel_check=lambda: self.control.command() == 'cancel'):
                if kind == 'verification':
                    terminal = DispatchResult(None, wait=payload)
                elif kind == 'result':
                    images = _extract_image_refs_from_tool_result(payload) if isinstance(payload,dict) else []
                    _normalize_image_placement(images)
                    preserve = bool(isinstance(payload,dict) and payload.pop('_no_truncate',False))
                    terminal = DispatchResult(payload, success=payload.get('success', True),
                        preserve_content=preserve, images=tuple(images))
            if terminal is None:
                raise CheckpointFailure('LOCAL_TOOL_MISSING_RESULT')
            await self._finish_plan_task(state,call,terminal)
            return terminal
        # Unknown side effects are never silently dispatched again. M4 recovery
        # adapters resolve known invocation/continuation IDs before re-entering.
        return DispatchResult(None, wait={"kind": "verification", "tool_call_id": call.id,
            "invocation_id": state.tools[call.id].invocation_id})

    async def _finish_plan_task(self, state, call, terminal):
        binding = (state.resources.get('plan_tasks') or {}).get(call.id)
        if not binding or terminal.wait:
            return
        plan = await asyncio.to_thread(self.plan_manager.get_plan,state.identity.session_id)
        if plan is None or plan.plan_id!=binding['plan_id']:
            raise CheckpointFailure('PLAN_BINDING_CHANGED')
        task = next((task for task in plan.tasks if task.task_id==binding['task_id']),None)
        if task is None:
            raise CheckpointFailure('PLAN_BINDING_CHANGED')
        if task.status in {'completed','failed'}:
            return
        if terminal.success:
            await asyncio.to_thread(self.plan_manager.mark_task_completed,state.identity.session_id,
                task.task_id,terminal.content)
        elif not (isinstance(terminal.content,dict) and terminal.content.get('status')=='clarifying'):
            await asyncio.to_thread(self.plan_manager.mark_task_failed,state.identity.session_id,
                task.task_id,terminal.content.get('error','Tool execution failed')
                    if isinstance(terminal.content,dict) else 'Tool execution failed')

    def before_tools(self, state):
        if self.verbose_config is None or self.verbose_state is None or state.iteration in self.policy_iterations:
            return []
        from src.core.verbose_feedback import build_policy_verbose_event
        self.policy_iterations.add(state.iteration)
        event = build_policy_verbose_event(self.policy_source, [
            {"id": pending.id, "name": pending.name, "arguments": pending.arguments}
            for pending in state.pending], self.verbose_config, self.verbose_state)
        return [event] if event else []

    async def dispatch(self,state,call):
        control=self.control
        source=None
        while control is not None:
            source=getattr(control,'source_tool_completion',None)
            if source is not None:break
            control=getattr(control,'parent_control',None)
        @asynccontextmanager
        async def scope():
            if source is None:yield
            else:
                async with source.tool_scope(state,call):yield
        async with scope():
            async for item in self._dispatch(state,call):
                if source is not None and isinstance(item,DispatchResult):
                    item=await source.complete(state,call,item)
                yield item

    async def _dispatch(self, state, call):
        authorize = getattr(self.control, "authorize_dispatch", None)
        if authorize is not None:
            await authorize()
        if self.defer_names is not None:
            yield make_event("tool_messages", messages=state.messages[state.initial_len:], suspended=True)
            yield make_event("desktop_remote_tool_call", toolName=call.name,
                             toolArgs=call.arguments, toolCallId=call.id)
            yield DispatchResult(None, wait={"kind": "remote_tool", "tool_call_id": call.id})
            return
        execution_call = call
        if call.name not in {definition["name"] for definition in self.definitions()}:
            skill = self.skills.skill_registry.get(call.name)
            if skill:
                execution_call = ToolCall(call.id, "use_skill", {"skill": call.name})
        plan_task = None
        if owns_plan_task(execution_call.name):
            binding = (state.resources.get('plan_tasks') or {}).get(call.id)
            plan = await asyncio.to_thread(self.plan_manager.get_plan,state.identity.session_id)
            if binding:
                if plan is None or plan.plan_id!=binding['plan_id']:
                    raise CheckpointFailure('PLAN_BINDING_CHANGED')
                plan_task = next((task for task in plan.tasks if task.task_id==binding['task_id']),None)
                if plan_task is None:
                    raise CheckpointFailure('PLAN_BINDING_CHANGED')
            else:
                plan_task = await asyncio.to_thread(self.plan_manager.get_next_pending_task,state.identity.session_id)
            if plan_task:
                if plan_task.status=='pending':
                    await asyncio.to_thread(self.plan_manager.mark_task_running,state.identity.session_id,plan_task.task_id)
                state.resources.setdefault('plan_tasks',{})[call.id] = {'plan_id':plan.plan_id,'task_id':plan_task.task_id}
                await self.control.save(state,'plan_binding')
        try:
            if self.control_tools.controls.get(execution_call.name) or execution_call.name == "skill_complete":
                terminal = None
                async for item in self.control_tools.execute(state, execution_call):
                    if terminal is not None:
                        raise RuntimeError("CONTROL_TOOL_PROTOCOL_MULTIPLE_TERMINALS")
                    if isinstance(item, DispatchResult):
                        terminal = item
                    else:
                        yield item
            else:
                tool = self.registry.get_tool(execution_call.name)
                context = self.control_tools.context.derive(tool_call_id=call.id, agent_execution_id=state.execution_id)
                if tool and getattr(tool, "execution_target", None) == ExecutionTarget.LOCAL_REQUIRED:
                    result = None
                    async for kind, payload in self.local._run_local_required_tool(
                        execution_call.name, json.loads(json.dumps(execution_call.arguments)),
                        state.identity.tenant_id, state.identity.user_id,
                        cancel_check=lambda: self.control.command() == "cancel", context=context,
                    ):
                        if kind == "invocation":
                            state.tools[call.id].invocation_id = payload
                            try:
                                await self.control.save(state, "tool_invocation")
                            except Exception as error:
                                raise CheckpointFailure("INVOCATION_CHECKPOINT_FAILED") from error
                        elif kind == "progress":
                            yield make_event("progress", data=payload)
                        elif kind == 'verification':
                            result = DispatchResult(None, wait=payload)
                        else:
                            result = payload
                    if result is None:
                        raise RuntimeError("LOCAL_TOOL_MISSING_RESULT")
                else:
                    result = await self.browser.execute(tool,self.executor,
                        execution_call,context)
                if isinstance(result, DispatchResult):
                    terminal = result
                elif isinstance(result, ToolSuspension):
                    from src.tools.browser.resume_store import ResumeStore
                    store = ResumeStore()
                    try:
                        assistance = await store.get_assistance(result.tenant_id, result.assistance_id)
                        if assistance is None or not await store.bind_agent(assistance,
                            self.subagent_config.dir_name if self.subagent_config else None):
                            raise RuntimeError("TOOL_SUSPEND_FAILED")
                    except CheckpointFailure:
                        raise
                    except Exception as error:
                        if getattr(self.control,'durable_owner',False):
                            # Mandatory wait is already committed and its live
                            # original runtime must retain the Runner claim.
                            raise CheckpointFailure('TOOL_SUSPENSION_STORAGE_FAILED') from error
                        raise
                    yield make_event("tool_messages", messages=waiting_messages(state), suspended=True)
                    yield result.event
                    yield make_event("progress", data="等待你的操作；完成后系统会自动继续")
                    terminal = DispatchResult(None, wait={"kind": "human_assistance",
                        "assistance_id": result.assistance_id, "tool_call_id": call.id})
                else:
                    success = result.get("success", True) if isinstance(result, dict) else True
                    images = _extract_image_refs_from_tool_result(result) if isinstance(result, dict) else []
                    _normalize_image_placement(images)
                    preserve = bool(isinstance(result, dict) and result.pop("_no_truncate", False))
                    terminal = DispatchResult(result, success=success, preserve_content=preserve, images=tuple(images))
            if terminal is None:
                raise RuntimeError("CONTROL_TOOL_MISSING_RESULT")
            await self._finish_plan_task(state,call,terminal)
            yield terminal
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # Storage failures are not tool failures: a failed checkpoint must stop
            # execution instead of being paired as a normal retryable tool result.
            if getattr(error, "authoritative_storage_failure", False):
                raise
            if plan_task:
                await self._finish_plan_task(state,call,DispatchResult({'error':type(error).__name__},success=False))
            yield DispatchResult({"success": False, "error": "Tool execution failed"}, success=False)
