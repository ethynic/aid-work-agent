"""Count original native automatic authorization and real sampler invocations."""
import json
from pathlib import Path
import sys


def install_auto_observer(destination):
    from src.services.agent_runner.browser_owner import RunnerBrowserOwner
    from src.services.agent_runner.browser_human_actions import BrowserHumanActions
    from src.tools.browser.page_ops import PageOps
    report = Path(destination).with_suffix('.auto.json')
    counts = {'sample_calls': 0, 'sample_success': 0, 'guard_calls': 0, 'guard_denied': 0,
              'fields_calls': 0, 'same_page_ops': True}
    page_ops_id = None
    def publish():
        temporary = report.with_suffix('.pending')
        temporary.write_text(json.dumps(counts))
        temporary.replace(report)
    sample = RunnerBrowserOwner.sample_human_completion
    guard = BrowserHumanActions.automatic_guard
    fields = PageOps._fields
    def observed_fields(self):
        counts['fields_calls'] += 1
        return fields(self)
    async def observed_sample(self, assistance_id, page_ops):
        nonlocal page_ops_id
        counts['sample_calls'] += 1
        if page_ops_id is None:
            page_ops_id = id(page_ops)
        counts['same_page_ops'] = counts['same_page_ops'] and page_ops_id == id(page_ops)
        result = await sample(self, assistance_id, page_ops)
        counts['sample_success'] += int(bool(result.get('success')))
        publish()
        return result
    async def observed_guard(self, assistance_id):
        counts['guard_calls'] += 1
        try:
            return await guard(self, assistance_id)
        except Exception as error:
            if getattr(error, 'code', None) == 'TENANT_UNAVAILABLE':
                counts['guard_denied'] += 1
                counts['denied_sample_calls'] = counts['sample_calls']
                counts['denied_fields_calls'] = counts['fields_calls']
            raise
        finally:
            publish()
    RunnerBrowserOwner.sample_human_completion = observed_sample
    BrowserHumanActions.automatic_guard = observed_guard
    PageOps._fields = observed_fields


if __name__ == '__main__':
    report, *arguments = sys.argv[1:]
    from tests.integration.agent_runner_service.browser_human_service_probe import install
    install(report)
    install_auto_observer(report)
    sys.argv = ['worker', *arguments]
    from src.services.agent_runner.worker import main
    main()
