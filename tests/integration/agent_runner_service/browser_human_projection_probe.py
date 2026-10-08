"""Delay one original Redis projection after PG commit; never replace its result."""
import asyncio
from pathlib import Path
import sys
import time


def install_projection_gate(entered, release):
    from src.tools.browser.resume_store import ResumeStore

    original = ResumeStore.project_owned_assistance
    extended_calls = 0

    async def project(self, owner, wait):
        nonlocal extended_calls
        if wait.get('extended_at') is not None:
            extended_calls += 1
            if extended_calls == 2:
                Path(entered).touch()
                until = time.monotonic() + 9
                while not Path(release).exists():
                    if time.monotonic() >= until:
                        raise TimeoutError('Fixture original projection gate not released')
                    await asyncio.sleep(.02)
        return await original(self, owner, wait)

    ResumeStore.project_owned_assistance = project


if __name__ == '__main__':
    report, entered, release, *arguments = sys.argv[1:]
    from tests.integration.agent_runner_service.browser_human_service_probe import install
    install(report)
    install_projection_gate(entered, release)
    sys.argv = ['worker', *arguments]
    from src.services.agent_runner.worker import main
    main()
