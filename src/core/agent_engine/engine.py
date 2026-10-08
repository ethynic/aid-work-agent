"""One model/tool state machine for master, standalone and delegated execution.

Persistence, authentication, tool policy and presentation are ports. A consumer
closing this iterator is an execution-owner action; subscribers never own it.
"""

import asyncio
import json
import time
import uuid
from typing import AsyncIterator

from src.core.agent_events import (
    _truncate_tool_content, make_event, make_image_event, mask_tool_args,
)
from .contracts import DispatchResult, ExecutionState, Outcome, ToolCall, ToolFact, TerminalDirective
from .ports import ControlPort, ModelPort, ObserverPort, ToolPort


def is_tool_result_echo(content) -> bool:
    return (isinstance(content, str) and content.lstrip().startswith('[{"id"')
            and '"tool_result"' in content.lstrip()[:300])


def normalize_calls(raw_calls: list) -> list[ToolCall]:
    calls = []
    ids = set()
    for raw in raw_calls:
        function = raw.get("function", raw)
        name = function.get("name")
        if not name:
            continue
        arguments = function.get("arguments", {})
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments or "{}")
            except (ValueError, TypeError) as error:
                raise ValueError("INVALID_TOOL_ARGUMENTS") from error
        if not isinstance(arguments, dict):
            raise ValueError("INVALID_TOOL_ARGUMENTS")
        call_id = str(raw.get("id") or f"call_{uuid.uuid4().hex}")
        if call_id in ids:
            raise ValueError("DUPLICATE_TOOL_CALL_ID")
        ids.add(call_id)
        calls.append(ToolCall(call_id, name, arguments))
    return calls


class AgentEngine:
    async def _control(self, state, control):
        command = control.command()
        if command in {"cancel", "pause"}:
            state.outcome = Outcome.CANCELLED if command == "cancel" else Outcome.PAUSED
            await control.save(state, command)
            return make_event("cancelled" if command == "cancel" else "paused")
        return None

    @staticmethod
    def _directive(state):
        values=[state.terminal_directive]+[fact.terminal_directive for fact in state.tools.values()]
        def descendants(node):
            values.append(node.get('terminal_directive'))
            values.extend(fact.get('terminal_directive') for fact in node.get('tools',{}).values())
            for child in node.get('children',{}).values():
                if isinstance(child.get('checkpoint'),dict):descendants(child['checkpoint'])
        for child in state.children.values():
            if isinstance(child.get('checkpoint'),dict):descendants(child['checkpoint'])
        checked=[TerminalDirective(value) for value in values if value is not None]
        return TerminalDirective.STOP_EXECUTION in checked

    async def _stop_execution(self,state,control,tools):
        """Pair only proven undispatched calls; preserve every uncertain effect."""
        state.terminal_directive=TerminalDirective.STOP_EXECUTION
        unresolved=[]
        for call in tuple(state.pending):
            fact=state.tools[call.id]
            child=state.children.get(call.id)
            if fact.phase!='prepared' or child is not None:
                recover=getattr(tools,'recover_completed',None)
                result=await recover(state,call) if child is not None and callable(recover) else None
                if not isinstance(result,DispatchResult) or result.wait:
                    unresolved.append(call.id)
                    continue
                fact.phase='completed';fact.success=result.success;fact.result=result.content
                fact.preserve_content=result.preserve_content;fact.images=[dict(image) for image in result.images]
                fact.final_output=result.final_output
                fact.terminal_directive=(TerminalDirective(result.terminal_directive)
                    if result.terminal_directive is not None else None)
                if not fact.result_recorded:
                    text=result.content if isinstance(result.content,str) else json.dumps(result.content,ensure_ascii=False)
                    state.messages.append({'role':'tool','tool_call_id':call.id,
                        'content':text if result.preserve_content else _truncate_tool_content(text)})
                    fact.result_recorded=True
                state.pending.remove(call)
                continue
            fact.phase='not_dispatched'
            fact.success=False
            fact.result={'success':False,'error_code':'EXECUTION_TERMINATED_BEFORE_DISPATCH'}
            state.messages.append({'role':'tool','tool_call_id':call.id,
                                   'content':json.dumps(fact.result,separators=(',',':'))})
            fact.result_recorded=True
            state.pending.remove(call)
        def unresolved_child(child):
            node=child.get('checkpoint')
            return (not isinstance(node,dict) or node.get('outcome') not in
                    {'completed','failed','cancelled','iteration_limit'}
                    or any(f.get('phase') in {'dispatching','waiting'} for f in node.get('tools',{}).values())
                    or any(unresolved_child(c) for c in node.get('children',{}).values()))
        if unresolved or any(unresolved_child(child) for child in state.children.values()):
            state.outcome=Outcome.WAITING
            state.waiting={'kind':'verification','reason':'TERMINAL_EFFECT_PENDING_VERIFICATION',
                           'tool_call_ids':unresolved}
            await control.save(state,'terminal_effect_waiting')
            return make_event('waiting',reason=state.waiting)
        state.outcome=Outcome.COMPLETED
        state.waiting=None
        await control.save(state,'completed')
        return make_event('execution_completed')

    async def _model(self, state, model, tools, control, observer):
        call_id = f"model_{uuid.uuid4().hex}"
        # Retain a stable ID even when the provider response is interrupted.
        fact = {"call_id": call_id, "execution_id": state.execution_id,
                "iteration": state.iteration, "phase": "started"}
        state.model_calls.append(fact)
        await control.save(state, "before_model")
        started = time.monotonic()
        try:
            response = await model.complete(state, call_id, tools.definitions())
        except Exception as error:
            if getattr(error, "authoritative_storage_failure", False):
                raise
            # Identify provider I/O separately from authoritative checkpoint and
            # usage writes so a compatibility echo retry can preserve its first response.
            fact.update(phase="failed", error_code=type(error).__name__)
            await control.save(state, "model_failed")
            error.model_io_failure = True
            raise
        fact.update(phase="completed", request_id=response.get("request_id", ""),
                    usage=response.get("usage", {}) or {}, model=response.get("model", ""),
                    provider=response.get("provider", ""),
                    duration_ms=int((time.monotonic() - started) * 1000))
        # A checkpoint after provider IO but before message application must keep
        # the actual response. Recovery must not make a second physical call.
        fact['response'] = response
        fact['applied'] = False
        # Usage is a fact for EACH actual call, including an echo retry.
        await control.save(state, "model_usage")
        await observer.model_usage(state, dict(fact))
        if response.get("protocol_error"):
            raise RuntimeError(response["protocol_error"])
        event = make_event("llm_call", **{key: value for key, value in fact.items() if key != "phase"},
                           messages=state.messages, tools=tools.definitions(),
                           system_prompt=state.system_prompt,
                           response_content=response.get("content", ""))
        return response, event

    async def run(
        self, state: ExecutionState, model: ModelPort, tools: ToolPort,
        control: ControlPort, observer: ObserverPort,
    ) -> AsyncIterator[dict]:
        self._directive(state)  # Validate the entire tree even if this owner is already stopped.
        if state.outcome in {Outcome.COMPLETED, Outcome.CANCELLED, Outcome.ITERATION_LIMIT}:
            return
        state.outcome = Outcome.RUNNING
        state.waiting = None

        async def observed(event):
            await observer.event(state, event)
            return event

        try:
            def owned_model_fact(fact):
                # Old mixed checkpoints may retain foreign accounting facts.
                # Only this execution's response may advance its loop.
                owner = fact.get('execution_id') or fact.get('child_execution_id') or state.execution_id
                return owner == state.execution_id

            def unapplied_response():
                return next((fact for fact in reversed(state.model_calls)
                    if owned_model_fact(fact) and fact.get('iteration')==state.iteration and fact.get('phase')=='completed'
                    and fact.get('applied') is False and isinstance(fact.get('response'),dict)),None)

            if self._directive(state):
                yield await observed(await self._stop_execution(state,control,tools))
                return
            while state.pending or unapplied_response() or state.iteration < state.max_iterations:
                if self._directive(state):
                    yield await observed(await self._stop_execution(state,control,tools))
                    return
                stopped = await self._control(state, control)
                if stopped:
                    yield await observed(stopped)
                    return
                if not state.pending:
                    saved = unapplied_response()
                    # Finish the original model step (including its tool calls)
                    # before admitting a later user turn. A saved response was
                    # produced without that turn and cannot answer it.
                    if not saved and state.followup_messages:
                        state.messages.extend(state.followup_messages)
                        state.followup_messages = []
                        await control.save(state,'followup_messages')
                    if saved:
                        response = saved['response']
                    else:
                        state.iteration += 1
                        response, event = await self._model(state, model, tools, control, observer)
                        yield await observed(event)
                    if not saved and not response.get("tool_calls") and is_tool_result_echo(response.get("content")):
                        # A retry failure is not a storage failure: model IO may fail;
                        # checkpoint/usage failures must propagate, never be swallowed.
                        try:
                            retry, event = await self._model(state, model, tools, control, observer)
                        except Exception as error:
                            if not getattr(error, "model_io_failure", False):
                                raise
                        else:
                            response = retry
                            yield await observed(event)
                    if response.get('protocol_error'):
                        raise RuntimeError(response['protocol_error'])
                    calls = normalize_calls(response.get("tool_calls", []))
                    content = response.get("content") or ""
                    message = {"role": "assistant", "content": content}
                    if response.get("reasoning_content"):
                        message["reasoning_content"] = response["reasoning_content"]
                    if calls:
                        message["tool_calls"] = [call.message_call() for call in calls]
                    state.messages.append(message)
                    for fact in state.model_calls:
                        if owned_model_fact(fact) and fact.get('iteration')==state.iteration and fact.get('phase')=='completed':
                            fact['applied'] = True
                    if not calls:
                        state.output += content
                        if state.followup_messages:
                            await control.save(state,'model_completed')
                            if content:
                                yield await observed(make_event('response',data=content))
                            continue
                        state.outcome = Outcome.COMPLETED
                        await control.save(state, "completed")
                        yield await observed(make_event("execution_completed"))
                        if content:
                            yield await observed(make_event("response", data=content))
                        break
                    state.pending = calls
                    for call in calls:
                        if call.id in state.tools:
                            raise ValueError("TOOL_CALL_ID_ALREADY_USED")
                        state.tools[call.id] = ToolFact(call)
                    await control.save(state, "model_completed")

                presentation = getattr(tools, "before_tools", None)
                if callable(presentation):
                    for event in presentation(state):
                        yield await observed(event)
                while state.pending:
                    if self._directive(state):
                        yield await observed(await self._stop_execution(state,control,tools))
                        return
                    stopped = await self._control(state, control)
                    if stopped:
                        yield await observed(stopped)
                        return
                    call = state.pending[0]
                    fact = state.tools[call.id]
                    result = None
                    if fact.phase == "completed":
                        result = DispatchResult(fact.result, success=fact.success,
                            preserve_content=fact.preserve_content, images=tuple(fact.images), final_output=fact.final_output, terminal_directive=fact.terminal_directive)
                    elif fact.phase in {"dispatching", "waiting"}:
                        result = await tools.recover(state, call)
                    else:
                        # Persist dispatch intent BEFORE any external side effect.
                        fact.phase = "dispatching"
                        await control.save(state, "before_tool")
                        yield await observed(make_event("tool_start", toolName=call.name,
                            toolCallId=call.id, toolArgs=mask_tool_args(call.arguments),
                            displayName=tools.display_name(call)))
                        async for item in tools.dispatch(state, call):
                            if result is not None:
                                raise RuntimeError("TOOL_PROTOCOL_MULTIPLE_TERMINALS")
                            if isinstance(item, DispatchResult):
                                result = item
                            elif isinstance(item, dict):
                                yield await observed(item)
                            else:
                                raise RuntimeError("TOOL_PROTOCOL_INVALID_EVENT")
                    if not isinstance(result, DispatchResult):
                        raise RuntimeError("TOOL_PROTOCOL_MISSING_TERMINAL")
                    if result.wait:
                        state.outcome = Outcome.WAITING
                        state.waiting = result.wait
                        fact.phase = "waiting"
                        fact.result = result.content
                        fact.success = result.success
                        await control.save(state, "waiting")
                        yield await observed(make_event("waiting", reason=result.wait))
                        return
                    fact.phase = "completed"
                    fact.result = result.content
                    fact.success = result.success
                    fact.preserve_content = result.preserve_content
                    fact.images = [dict(image) for image in result.images]
                    fact.final_output = result.final_output
                    fact.terminal_directive = (TerminalDirective(result.terminal_directive)
                        if result.terminal_directive is not None else None)
                    if fact.terminal_directive is not None:state.terminal_directive=fact.terminal_directive
                    if not fact.result_recorded:
                        serialized = result.content if isinstance(result.content, str) else json.dumps(result.content, ensure_ascii=False)
                        state.messages.append({"role": "tool", "tool_call_id": call.id,
                            "content": serialized if result.preserve_content else _truncate_tool_content(serialized)})
                        fact.result_recorded = True
                    state.pending.pop(0)
                    for image in result.images:
                        if not any(existing.get("file_id") == image.get("file_id") for existing in state.images):
                            state.images.append(dict(image))
                    await control.save(state, "tool_completed")
                    yield await observed(make_event("tool_result", toolName=call.name,
                        toolCallId=call.id, displayName=tools.display_name(call),
                        result=result.content, success=result.success))
                    if result.images:
                        yield await observed(make_image_event(list(result.images), placement="after_text"))
                    if self._directive(state):
                        if result.final_output is not None:state.output+=result.final_output
                        yield await observed(await self._stop_execution(state,control,tools))
                        if result.final_output:yield await observed(make_event('response',data=result.final_output))
                        return
                    if result.final_output is not None:
                        if state.pending:
                            raise RuntimeError("TERMINAL_EFFECT_WITH_PENDING_TOOLS")
                        state.output += result.final_output
                        if state.followup_messages:
                            await control.save(state,'terminal_effect')
                            if result.final_output:
                                yield await observed(make_event('response',data=result.final_output))
                            continue
                        state.outcome = Outcome.COMPLETED
                        await control.save(state, "completed")
                        yield await observed(make_event("execution_completed"))
                        if result.final_output:
                            yield await observed(make_event("response", data=result.final_output))
                        break
                if state.outcome == Outcome.COMPLETED:
                    break

            if state.outcome == Outcome.RUNNING:
                state.outcome = Outcome.ITERATION_LIMIT
                state.error_code = "ITERATION_LIMIT"
                await control.save(state, "iteration_limit")
                yield await observed(make_event("iteration_limit", limit=state.max_iterations))
            tool_messages = [message for message in state.messages[state.initial_len:]
                             if message.get("role") == "tool" or message.get("tool_calls")]
            if tool_messages:
                yield await observed(make_event("tool_messages", messages=tool_messages))
        except asyncio.CancelledError:
            state.outcome = Outcome.CANCELLED
            await control.save(state, "cancelled")
            raise
        except Exception:
            state.outcome = Outcome.FAILED
            state.error_code = "EXECUTION_FAILED"
            # A failed durable write must not be followed by any more model/tool IO.
            # Owner handles durable failure; attempting another write here can hide it.
            raise
