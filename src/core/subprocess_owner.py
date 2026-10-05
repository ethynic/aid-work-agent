"""Stop the owned process group before its application task exits."""

import os
import signal


def stop_process_group(process):
    if process is None:
        return
    try:
        if os.name == 'posix':
            os.killpg(process.pid,signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        pass


async def stop_process_group_async(process):
    stop_process_group(process)
    if process is not None:
        await process.wait()
