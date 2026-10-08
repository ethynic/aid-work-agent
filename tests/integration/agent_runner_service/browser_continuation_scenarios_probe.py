"""Finite trusted triggers and timing around original continuation methods.

No return value, authorization, owner, sampler or Browser action is fabricated.
Optional park gates hold the original call before commit or its after-park hook.
All work stays in the real resident Worker and original native process group.
"""
import asyncio
import json
from pathlib import Path
import sys
import time


def install(command_file, report_file, entered_file, release_file, mode):
    from src.services.agent_runner.worker import RuntimeFactory
    from src.services.agent_runner.execution_repository import ExecutionRepository
    from src.services.agent_runner.browser_completion import BrowserCompletionRepository
    from src.tools.browser.resume_store import ResumeStore
    from src.tools.browser.human_control import get_owned_runtime
    from src.tools.browser.orchestrator import BrowserOrchestrator

    command, report, entered, release = map(Path, (command_file, report_file, entered_file, release_file))
    original_open, original_close = RuntimeFactory.open, RuntimeFactory.close
    original_after_park, original_park = RuntimeFactory.after_park, ExecutionRepository.park
    original_start = BrowserCompletionRepository.start
    original_finish, original_clear = BrowserCompletionRepository.finish, ResumeStore.clear
    original_resume, original_decision = BrowserOrchestrator.resume_from_human, BrowserOrchestrator._get_decision
    observations = {'completion_calls': 0, 'resume_calls': 0, 'resumed_model_entries': 0, 'completions': []}
    identities = {}
    parked_once = False
    start_gated = False
    result_saved = False
    projection_failed = False

    def publish():
        temporary = report.with_suffix('.pending')
        temporary.write_text(json.dumps(observations))
        temporary.replace(report)

    def park(self, *args, **kwargs):
        nonlocal parked_once
        if mode == 'early' and not parked_once:
            parked_once = True
            entered.touch()
            while not release.exists():
                time.sleep(.01)
        return original_park(self, *args, **kwargs)

    async def after_park(self, row):
        nonlocal parked_once
        if mode in {'pending', 'owner_lost'} and not parked_once:
            parked_once = True
            entered.touch()
            while not release.exists():
                await asyncio.sleep(.01)
        return await original_after_park(self, row)

    def start(self, *args, **kwargs):
        nonlocal start_gated
        if mode == 'consume_pause' and not start_gated:
            # Real control consumption has already committed. Hold before the
            # original start transaction; no result or authority is fabricated.
            start_gated = True
            entered.touch()
            while not release.exists():
                time.sleep(.01)
        return original_start(self, *args, **kwargs)

    def finish(self, *args, **kwargs):
        nonlocal result_saved
        result = original_finish(self, *args, **kwargs)
        result_saved = True
        return result

    async def clear(self, *args, **kwargs):
        nonlocal projection_failed
        if mode == 'projection_failure' and result_saved and not projection_failed:
            # External cache-port failure after the original finish transaction
            # really committed; never replace its saved Browser result.
            projection_failed = True
            observations['postcommit_projection_failure'] = True
            publish()
            raise ConnectionError('FIXTURE_CACHE_PORT_UNAVAILABLE')
        return await original_clear(self, *args, **kwargs)

    async def decision(self, *args, **kwargs):
        if id(self) in identities:
            observations['resumed_model_entries'] += 1
            observations['confirmations_at_model_dispatch'] = sum(step.get('action') == 'human_confirmation' for step in self.steps)
            publish()
        return await original_decision(self, *args, **kwargs)

    async def resume(self, **kwargs):
        observations['resume_calls'] += 1
        identity = identities.get(id(self))
        observations['original_objects_preserved'] = bool(identity and self.page_ops is identity['page_ops']
            and identity['manager'].human_executor(identity['tenant'], identity['run']) is identity['human_executor'])
        observations['last_resume_seq_before'] = self.page_ops._seq
        publish()
        try:
            result = await original_resume(self, **kwargs)
        except BaseException as error:
            observations['resume_exception_class'] = type(error).__name__
            publish()
            raise
        observations['last_resume_seq_after'] = self.page_ops._seq
        observations['last_confirmation_count'] = sum(step.get('action') == 'human_confirmation' for step in self.steps)
        observations['last_resume_status'] = result.get('status')
        observations['last_resume_success'] = bool(result.get('success'))
        publish()
        return result

    async def opened(self):
        await original_open(self)

        async def trigger_original_completions():
            previous = 0
            while True:
                if not command.exists():
                    await asyncio.sleep(.02)
                    continue
                request = json.loads(command.read_text())
                if request['sequence'] <= previous:
                    await asyncio.sleep(.02)
                    continue
                previous = request['sequence']
                owners = [manager.execution_owner for manager in (self.browser_process or {}).get('retained_managers', ())
                    if getattr(manager, 'execution_owner', None) is not None
                    and manager.execution_owner.runner_id == request['runner_id']
                    and manager.execution_owner.record.run_id == request['run_id']]
                event = {'sequence': previous, 'owner_matches': len(owners)}
                observations['completion_calls'] += 1
                if len(owners) == 1:
                    owner = owners[0]
                    runtime = await get_owned_runtime(owner.record.tenant_id, owner.record.run_id)
                    if runtime is not None:
                        identities.setdefault(id(runtime.orchestrator), {'page_ops': runtime.orchestrator.page_ops,
                            'manager': owner.manager, 'human_executor': owner.manager.human_executor(owner.record.tenant_id, owner.record.run_id),
                            'tenant': owner.record.tenant_id, 'run': owner.record.run_id})
                        before = runtime.orchestrator.page_ops._seq
                        try:
                            result = await owner.complete_assistance(request['assistance_id'], automatic=False)
                            event.update(ref_present=bool(result.get('completion_ref')), bridged=bool(result.get('bridged')),
                                missing_count=len(result.get('missing_conditions', [])), sampled=runtime.orchestrator.page_ops._seq > before)
                        except BaseException as error:
                            event['exception_class'] = type(error).__name__
                observations['completions'].append(event)
                publish()

        self._fixture_continuation_task = asyncio.create_task(trigger_original_completions())

    async def closed(self):
        task = getattr(self, '_fixture_continuation_task', None)
        try:
            await original_close(self)
        finally:
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    RuntimeFactory.open, RuntimeFactory.close, RuntimeFactory.after_park = opened, closed, after_park
    ExecutionRepository.park = park
    BrowserCompletionRepository.start = start
    BrowserCompletionRepository.finish, ResumeStore.clear = finish, clear
    BrowserOrchestrator.resume_from_human, BrowserOrchestrator._get_decision = resume, decision


if __name__ == '__main__':
    command, report, entered, release, mode, *arguments = sys.argv[1:]
    install(command, report, entered, release, mode)
    sys.argv = ['worker', *arguments]
    from src.services.agent_runner.worker import main
    main()
