"""Real worker with one post-commit timing gate; no business-result substitutes.

Used only for a domain boundary that has no external IO gate after commit. The
next attempt always runs the undecorated production CLI. Invoke as a test script
with worker id, expected branch, ordinal and a task-owned gate directory.
"""
import asyncio
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace


def run():
    from src.services.agent_runner.local_phases import LocalPhaseRepository
    from src.services.agent_runner.worker import run_worker

    worker_id, branch, ordinal_text, directory = sys.argv[1:5]
    ordinal = int(ordinal_text)
    gate = Path(directory)
    assert gate.is_absolute() and gate.is_dir()
    original = LocalPhaseRepository.commit
    observed = False

    def commit(repository, attempt, revision, phase, intent, writer, **options):
        nonlocal observed
        result = original(repository, attempt, revision, phase, intent, writer, **options)
        row, fact = result
        if not observed and phase.branch == branch and phase.ordinal == ordinal and fact['phase'] == 'completed':
            observed = True
            # Only public linkage/phase metadata. Never serialize intent/result.
            report = {'runner_id':row['runner_id'], 'revision':row['revision'],
                'execution_id':phase.execution_id, 'tool_call_id':phase.tool_call_id,
                'branch':phase.branch, 'ordinal':phase.ordinal, 'phase':fact['phase']}
            (gate / 'committed.json').write_text(json.dumps(report))
            deadline = time.monotonic() + 45
            while not (gate / 'release').exists():
                if time.monotonic() >= deadline:
                    raise AssertionError('DOMAIN_COMMIT_PROBE_GATE_TIMEOUT')
                time.sleep(.025)
        return result

    LocalPhaseRepository.commit = commit
    try:
        # Production operator bootstrap owns PG/log pools, schema assertion,
        # signal handlers and shutdown. Do not preinitialize a parallel service.
        asyncio.run(run_worker(SimpleNamespace(worker_id=worker_id, once=True, max_tasks=None)))
    finally:
        LocalPhaseRepository.commit = original


if __name__ == '__main__':
    run()
