"""Trusted owner invocation in the actual resident Worker process.

The only added activity is a task-owned file-trigger for the internal owner
completion method, plus observations around the original orchestrator method.
It never substitutes owner, sampler, continuation, browser or tool results.
Run only after the writer declares the production window stable.
"""
import asyncio
import json
from pathlib import Path
import sys


def install(command_file, report_file):
    from src.services.agent_runner.worker import RuntimeFactory
    from src.tools.browser.human_control import get_owned_runtime
    from src.tools.browser.orchestrator import BrowserOrchestrator

    command, report = Path(command_file), Path(report_file)
    original_open, original_close = RuntimeFactory.open, RuntimeFactory.close
    original_resume = BrowserOrchestrator.resume_from_human
    observations = {'completion_calls': 0, 'resume_calls': 0}
    identities = {}

    def publish():
        temporary = report.with_suffix('.pending')
        temporary.write_text(json.dumps(observations))
        temporary.replace(report)

    async def resume(self, **kwargs):
        observations['resume_calls'] += 1
        previous = identities.get(id(self))
        observations['same_original_orchestrator'] = previous is not None
        observations['same_original_page_ops'] = previous is not None and self.page_ops is previous['page_ops']
        observations['same_original_executor'] = previous is not None and previous['manager'].human_executor(
            previous['tenant_id'], previous['run_id']) is previous['executor']
        observations['resume_seq_before'] = self.page_ops._seq
        observations['confirmation_count_before'] = sum(step.get('action') == 'human_confirmation' for step in self.steps)
        publish()
        try:
            result = await original_resume(self, **kwargs)
        except BaseException as error:
            observations['resume_exception_class'] = type(error).__name__
            publish()
            raise
        observations['resume_seq_after'] = self.page_ops._seq
        observations['confirmation_count_after'] = sum(step.get('action') == 'human_confirmation' for step in self.steps)
        observations['resume_result_success'] = bool(result.get('success'))
        observations['resume_result_status'] = result.get('status')
        publish()
        return result

    async def opened(self):
        await original_open(self)

        async def complete_original():
            while not command.exists():
                await asyncio.sleep(.02)
            request = json.loads(command.read_text())
            process = self.browser_process or {}
            matches = [manager for manager in process.get('retained_managers', ())
                       if getattr(manager, 'execution_owner', None) is not None
                       and manager.execution_owner.runner_id == request['runner_id']
                       and manager.execution_owner.record.run_id == request['run_id']]
            if len(matches) != 1:
                observations['completion_owner_matches'] = len(matches)
                publish()
                return
            manager = matches[0]
            owner = manager.execution_owner
            runtime = await get_owned_runtime(owner.record.tenant_id, owner.record.run_id)
            if runtime is None:
                observations['completion_runtime_present'] = False
                publish()
                return
            identities[id(runtime.orchestrator)] = {'page_ops': runtime.orchestrator.page_ops,
                'executor': manager.human_executor(owner.record.tenant_id, owner.record.run_id),
                'manager': manager, 'tenant_id': owner.record.tenant_id, 'run_id': owner.record.run_id}
            observations['sample_seq_before'] = runtime.orchestrator.page_ops._seq
            observations['completion_calls'] += 1
            try:
                result = await owner.complete_assistance(request['assistance_id'], automatic=False)
                observations['completion_ref_present'] = isinstance(result.get('completion_ref'), str) and bool(result['completion_ref'])
                observations['completion_missing_count'] = len(result.get('missing_conditions', []))
                observations['sample_seq_after'] = runtime.orchestrator.page_ops._seq
                observations['completion_returned'] = True
            except BaseException as error:
                observations['completion_exception_class'] = type(error).__name__
            publish()

        self._fixture_completion_task = asyncio.create_task(complete_original())

    async def closed(self):
        task = getattr(self, '_fixture_completion_task', None)
        try:
            await original_close(self)
        finally:
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    RuntimeFactory.open, RuntimeFactory.close = opened, closed
    BrowserOrchestrator.resume_from_human = resume


if __name__ == '__main__':
    command, report, *arguments = sys.argv[1:]
    install(command, report)
    sys.argv = ['worker', *arguments]
    # Reuse the module whose actual Factory was observed above. runpy under
    # __main__ would define a second Factory and silently bypass this hook.
    from src.services.agent_runner.worker import main
    main()
