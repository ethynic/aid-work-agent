"""Observe original human methods; no trigger, authority or result replacement."""
from contextvars import ContextVar
import json
from pathlib import Path
import sys


def install(destination):
    from src.services.agent_runner.browser_human_actions import BrowserHumanActions
    from src.tools.browser.human_control import get_owned_runtime
    from src.tools.browser.orchestrator import BrowserOrchestrator
    from src.tools.browser.owner_port import HumanActionRejected
    from src.tools.browser.page_ops import PageOps

    report = Path(destination)
    stage = ContextVar('fixture_original_human_method', default=None)
    original_input, original_complete = BrowserHumanActions.input, BrowserHumanActions.complete
    original_fields, original_resume = PageOps._fields, BrowserOrchestrator.resume_from_human
    observations = {'input_calls': 0, 'input_accepted': 0, 'input_denied': 0,
                    'denied_without_fields': [], 'complete_calls': 0, 'resume_calls': 0,
                    'input_fields': 0, 'sample_fields': 0}
    identities = {}

    def publish():
        pending = report.with_suffix('.pending')
        pending.write_text(json.dumps(observations))
        pending.replace(report)

    def remember(runtime, owner):
        identities.setdefault(id(runtime.orchestrator), {
            'page_ops': runtime.orchestrator.page_ops, 'manager': owner.manager,
            'executor': owner.manager.human_executor(owner.record.tenant_id, owner.record.run_id),
            'tenant': owner.record.tenant_id, 'run': owner.record.run_id})

    def fields(self):
        current = stage.get()
        if current is not None:
            current['fields'] += 1
            observations[current['kind'] + '_fields'] += 1
        return original_fields(self)

    async def input(self, assertion, message, runtime):
        observations['input_calls'] += 1
        remember(runtime, self.owner)
        current = {'kind': 'input', 'fields': 0}
        token = stage.set(current)
        try:
            result = await original_input(self, assertion, message, runtime)
        except HumanActionRejected:
            observations['input_denied'] += 1
            observations['denied_without_fields'].append(current['fields'] == 0)
            raise
        else:
            observations['input_accepted'] += 1
            return result
        finally:
            stage.reset(token)
            publish()

    async def complete(self, assertion):
        runtime = await get_owned_runtime(self.owner.record.tenant_id, self.owner.record.run_id)
        if runtime is not None:
            remember(runtime, self.owner)
        observations['complete_calls'] += 1
        token = stage.set({'kind': 'sample', 'fields': 0})
        try:
            return await original_complete(self, assertion)
        finally:
            stage.reset(token)
            publish()

    async def resume(self, **kwargs):
        observations['resume_calls'] += 1
        previous = identities.get(id(self))
        observations['same_orchestrator'] = previous is not None
        observations['same_page_ops'] = bool(previous and previous['page_ops'] is self.page_ops)
        observations['same_raw_executor'] = bool(previous and previous['executor'] is
            previous['manager'].human_executor(previous['tenant'], previous['run']))
        observations['resume_seq_before'] = self.page_ops._seq
        before = sum(step.get('action') == 'human_confirmation' for step in self.steps)
        publish()
        try:
            result = await original_resume(self, **kwargs)
        except BaseException as error:
            observations['resume_exception_class'] = type(error).__name__
            raise
        else:
            observations['confirmation_added'] = sum(
                step.get('action') == 'human_confirmation' for step in self.steps) - before
            observations['resume_success'] = bool(result.get('success'))
            observations['resume_seq_after'] = self.page_ops._seq
            return result
        finally:
            publish()

    BrowserHumanActions.input, BrowserHumanActions.complete = input, complete
    PageOps._fields, BrowserOrchestrator.resume_from_human = fields, resume


if __name__ == '__main__':
    report, *arguments = sys.argv[1:]
    install(report)
    sys.argv = ['worker', *arguments]
    from src.services.agent_runner.worker import main
    main()
