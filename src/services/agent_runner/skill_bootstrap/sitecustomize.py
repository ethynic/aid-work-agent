"""Trusted Python skill composition root, installed only by a Runner parent."""

import json
import os
from pathlib import Path
import time


if os.environ.get('AID_RUNNER_ID'):
    failure = Path(os.environ['AID_RUNNER_FAILURE_FILE'])
    registration = Path(os.environ['AID_RUNNER_PROCESS_FILE'])
    group = None
    try:
        deadline = time.monotonic()+10
        while not registration.stat().st_size:
            if time.monotonic()>deadline:
                raise RuntimeError('RUNNER_CHILD_NOT_REGISTERED')
            time.sleep(.01)
        registered = json.loads(registration.read_text())
        if os.name == 'posix':
            group = registered['group']
            if group != registered['pid'] or os.getpgrp() != group:
                raise RuntimeError('RUNNER_CHILD_GROUP_MISMATCH')
        from src.services.agent_runner.usage_context import install_subprocess_observer
        install_subprocess_observer(group)
    except BaseException:
        # Python suppresses sitecustomize exceptions. Explicitly report failure,
        # including shells which would otherwise hide the Python exit status.
        try:
            failure.write_text('RUNNER_CHILD_BOOTSTRAP_FAILED')
        except OSError:
            if os.name == 'posix' and group is not None and os.getpgrp()==group:
                import signal
                os.killpg(group,signal.SIGKILL)
        os._exit(87)
