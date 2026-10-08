"""Original Local cleanup distinguishes actual child cancellation from its caller."""
import asyncio

import pytest

from src.tools.browser.executor.local import LocalPlaywrightExecutor

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel_caller', [False, True], ids=['child_cancel_only', 'caller_cancel_propagates'])
async def test_original_finish_stderr_cancellation_preserves_the_correct_actual_task_owner(cancel_caller):
    executor = LocalPlaywrightExecutor()
    entered = asyncio.Event()
    async def original_pending_child():
        entered.set()
        await asyncio.Event().wait()
    child = asyncio.create_task(original_pending_child())
    await entered.wait()
    executor._stderr_task = child
    caller = None
    try:
        if cancel_caller:
            caller = asyncio.create_task(executor._finish_stderr())
            await asyncio.sleep(.05)
            assert not caller.done() and not child.done()
            caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller
            assert caller.cancelled() and child.cancelled()
        else:
            child.cancel()
            await asyncio.gather(child, return_exceptions=True)
            assert child.cancelled()
            await executor._finish_stderr()
            assert asyncio.current_task().cancelling() == 0
        assert executor._stderr_task is None and child.done()
    finally:
        for task in (caller, child):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(*[task for task in (caller, child) if task is not None], return_exceptions=True)
