"""共享控制/Browser 的存储错误与异步 SQL 收尾。"""

import asyncio
from src.core.agent_engine.contracts import CheckpointFailure


class SourceUnavailable(CheckpointFailure):
    """Lost source authority preserves the original execution and resources."""
    authoritative_storage_failure = True
    public_verification = '渠道来源或当前服务状态尚待核对，任务与原消息已保留。'


async def source_offload(function,*args,**kwargs):
    """A cancelled HTTP observer cannot abandon its actual owned SQL thread."""
    task=asyncio.create_task(asyncio.to_thread(function,*args,**kwargs))
    cancelled=False
    while not task.done():
        try: await asyncio.shield(task)
        except asyncio.CancelledError: cancelled=True
        except Exception: break
    if cancelled:
        task.exception()
        raise asyncio.CancelledError
    return task.result()
