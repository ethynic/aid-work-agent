"""Observe immutable finalization intent around the original real transaction.

Hash-only observation DI; original Worker/Finalizer and their results are used.
"""
import hashlib
import json
from pathlib import Path
import sys

from .browser_human_service_probe import install


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


if __name__ == '__main__':
    report, *arguments = sys.argv[1:]
    install(report)
    from src.services.agent_runner.finalizer import RunnerFinalizer
    from src.services.agent_runner.repository import RunnerRepository
    original = RunnerFinalizer.finalize

    def observed(self, attempt):
        before = RunnerRepository(self.connection_factory).get(attempt.runner_id)['checkpoint']['pending_finalization']
        result = original(self, attempt)
        after = result['checkpoint']['pending_finalization']
        Path(report + '.intent.json').write_text(json.dumps({
            'intent_present': bool(before), 'before_digest': digest(before), 'after_digest': digest(after),
        }))
        return result

    RunnerFinalizer.finalize = observed
    sys.argv = ['worker', *arguments]
    from src.services.agent_runner.worker import main
    main()
