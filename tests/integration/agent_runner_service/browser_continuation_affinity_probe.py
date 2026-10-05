"""Observe original bounded acquire cursors; never replace their selection."""
import json
from pathlib import Path
import sys

from tests.integration.agent_runner_service.browser_continuation_scenarios_probe import install


def observe_acquisition(destination):
    from src.services.agent_runner.worker import RunnerWorker
    original = RunnerWorker._acquire_next
    path = Path(destination)
    observations = []

    async def acquire(self):
        before = self._recovery_scan_after
        row = await original(self)
        observations.append({'before': before, 'after': self._recovery_scan_after,
                             'picked_attempt': row.get('attempt') if row else None})
        temporary = path.with_suffix('.pending')
        temporary.write_text(json.dumps(observations))
        temporary.replace(path)
        return row

    RunnerWorker._acquire_next = acquire


if __name__ == '__main__':
    command, report, entered, release, mode, affinity, *arguments = sys.argv[1:]
    install(command, report, entered, release, mode)
    observe_acquisition(affinity)
    sys.argv = ['worker', *arguments]
    from src.services.agent_runner.worker import main
    main()
