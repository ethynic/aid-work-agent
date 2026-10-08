from __future__ import annotations
import asyncio
from typing import Any, Optional, Dict, Callable, AsyncGenerator
from loguru import logger

from src.tools.context import ExecutionContextFactory, tool_execution_scope


class ProgressQueue(asyncio.Queue):
    """Bound transient progress without failing an already dispatched tool."""
    def put_nowait(self, item):
        if self.full():
            self.get_nowait()
        super().put_nowait(item)
class LocalToolAdapter:
    def __init__(self, *, session_id, execution_id, subagent_config, llm, tool_executor, cancel_on_detach=True,
                 control=None, state=None):
        self.session_id, self.execution_id = session_id, execution_id
        self.subagent_config, self.llm, self.tool_executor = subagent_config, llm, tool_executor
        self.cancel_on_detach = cancel_on_detach
        self.control, self.state = control, state
    async def _run_local_required_tool(
        self,
        tool_name: str,
        tool_args: Dict[str, Any],
        tenant_id: Optional[str],
        user_id: Optional[str],
        cancel_check: Optional[Callable[[], bool]] = None,
        context=None,
        recover=False,
    ) -> AsyncGenerator[tuple, None]:
        """执行 LOCAL_REQUIRED 本地工具并流式产出进度（m05-implementation-spec §5）

        依次产出 ("progress", text)（本机执行事件），最后产出一次 ("result", result_dict)。
        取消时只 request_cancel，不中断等待——等 proxy 自身到终态，
        保证 tool_call 有配对结果。
        """
        from src.local_tools import repository

        context = context or ExecutionContextFactory.for_agent_call(
            tenant_id=tenant_id, user_id=user_id, session_id=self.session_id,
            subagent_id=(self.subagent_config.dir_name if self.subagent_config else None),
            agent_execution_id=self.execution_id, llm_gateway=self.llm,
        )
        if context.session_id != self.session_id:
            raise ValueError("LOCAL_TOOL_SESSION_MISMATCH")
        execution_args = dict(tool_args)
        execution_args.update(_trusted_tenant_id=context.tenant_id,
                              _trusted_user_id=context.user_id, _session_id=context.session_id)
        progress_queue: asyncio.Queue = ProgressQueue(maxsize=256)
        execution_args["_progress_queue"] = progress_queue
        from src.local_tools.durable_flow import LocalContinuationRequired
        from src.local_tools.lifecycle import local_operation_scope, current_local_operation
        tool = self.tool_executor.registry.get_tool(tool_name)
        owner = None
        from src.local_tools.domain_flow import supports_owned_tool, recover_owned_tool
        if getattr(self.control, 'durable_owner', False) and supports_owned_tool(tool):
            from ..local_owner import RunnerLocalLifecycle
            owner = RunnerLocalLifecycle(self.control, self.state, context.tool_call_id)
        async def execute():
            if recover:
                if owner is None:
                    raise LocalContinuationRequired('LOCAL_DOMAIN_CONTINUATION_REQUIRED')
                with tool_execution_scope(context):
                    return await recover_owned_tool(tool, current_local_operation(), progress_queue,
                        arguments=tool_args)
            return await self.tool_executor.execute(tool_name, execution_args, context=context)
        if owner is not None:
            with local_operation_scope(owner):
                task = asyncio.create_task(execute())
        else:
            task = asyncio.create_task(execute())
        current_invocation_id = None
        cancel_requested = False
        try:
            while not task.done():
                evt = None
                try:
                    evt = await asyncio.wait_for(progress_queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    pass
                if evt is not None:
                    if evt.get("invocation_id") and current_invocation_id is None:
                        current_invocation_id = evt.get("invocation_id")
                        yield ("invocation", current_invocation_id)
                    text = evt.get("text")
                    if text:
                        yield ("progress", text)
                if (
                    not cancel_requested
                    and cancel_check
                    and cancel_check()
                    and current_invocation_id
                    and tenant_id
                ):
                    cancel_requested = True
                    if getattr(self.control, 'durable_owner', False):
                        from ..local_recovery import cancel_owned_invocations
                        root = self.control
                        while getattr(root, 'parent_control', None) is not None:
                            root = root.parent_control
                        async with root.lock:
                            facts = await asyncio.to_thread(cancel_owned_invocations,
                                root.repository.connection_factory, root.attempt)
                            if getattr(facts,'row',None) is not None:
                                from ..local_owner import RunnerLocalLifecycle
                                RunnerLocalLifecycle(self.control,self.state,context.tool_call_id)._accept(facts.row)
                    else:
                        await asyncio.to_thread(repository.request_cancel, current_invocation_id, tenant_id)
            # 队列可能还有 proxy 收尾前推入的事件，排空
            while not progress_queue.empty():
                evt = progress_queue.get_nowait()
                if evt.get("invocation_id") and current_invocation_id is None:
                    current_invocation_id = evt["invocation_id"]
                    yield ("invocation", current_invocation_id)
                text = evt.get("text")
                if text:
                    yield ("progress", text)
            try:
                yield ("result", await task)
            except LocalContinuationRequired as error:
                yield ("verification", {"kind":"verification", "error_code":error.code,
                    "invocation_id":error.invocation_id, "tool_call_id":context.tool_call_id})
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                # 防止孤儿 invocation：生成器被提前关闭（如客户端断开）时 proxy 被取消，
                # 但 invocation 仍为 queued/running，设备稍后会领取并执行
                # 用户已看不到结果的写动作——必须请求取消
                if self.cancel_on_detach and current_invocation_id and tenant_id:
                    try:
                        await asyncio.to_thread(
                            repository.request_cancel, current_invocation_id, tenant_id
                        )
                        logger.info(
                            f"后端日志：本地工具生成器提前关闭，已请求取消孤儿 invocation "
                            f"id={current_invocation_id} tool={tool_name}"
                        )
                    except Exception as e:
                        logger.warning(
                            f"后端日志：取消孤儿 invocation 失败 id={current_invocation_id}: {e}"
                        )
