"""Observe original typed bridge failures, not fabricated owner/error results."""
import json
import asyncio
from pathlib import Path
import sys

from tests.integration.agent_runner_service.browser_continuation_scenarios_probe import install


def observe_bridge(destination, entered, release):
    from src.services.agent_runner.browser_owner import RunnerBrowserOwner
    from src.services.agent_runner.browser_completion import BrowserCompletionRepository, BrowserRecoveryUnavailable
    from src.services.agent_runner.worker import RunnerWorker
    original_bridge, original_scope = RunnerBrowserOwner.bridge_completions, BrowserCompletionRepository.scope
    path = Path(destination)
    observations = {'original_bridge_returned': 0, 'typed_scope_failures': 0}
    original_acquire = RunnerWorker._acquire_next

    async def acquire(self):
        # The real scheduler otherwise may consume the legitimate resume while
        # the first execution is still held in its original after-park hook.
        # Delay only scheduling; the actual acquisition and authority remain.
        while Path(entered).exists() and not Path(release).exists():
            await asyncio.sleep(.01)
        return await original_acquire(self)

    def publish():
        temporary = path.with_suffix('.pending')
        temporary.write_text(json.dumps(observations))
        temporary.replace(path)

    def scope(*args, **kwargs):
        try:
            return original_scope(*args, **kwargs)
        except BrowserRecoveryUnavailable as error:
            if error.code == 'BROWSER_COMPLETION_OWNER_MISMATCH':
                observations['typed_scope_failures'] += 1
                publish()
            raise

    async def bridge(self):
        result = await original_bridge(self)
        observations['original_bridge_returned'] += 1
        publish()
        return result

    BrowserCompletionRepository.scope = staticmethod(scope)
    RunnerBrowserOwner.bridge_completions = bridge
    RunnerWorker._acquire_next = acquire


if __name__ == '__main__':
    command, report, entered, release, mode, bridge, *arguments = sys.argv[1:]
    install(command, report, entered, release, mode)
    observe_bridge(bridge, entered, release)
    sys.argv = ['worker', *arguments]
    from src.services.agent_runner.worker import main
    main()
