"""Actual default CLI Browser availability and root/sibling/child ownership."""
import json
import subprocess

import pytest

from .browser_io import browser_page, browser_redis
from .browser_diagnostics import browser_diagnostics
from .provider import Reply, tool_reply
from .test_worker import workers, api_pair, prices, accept, runner, terminal, decoded
from .test_worker_controls import child_profile

pytestmark = [pytest.mark.integration, pytest.mark.real_browser]


def delete_browser_facts(database, identifier):
    database.rows('DELETE FROM bs_browser_resume_jobs WHERE run_id IN (SELECT run_id FROM bs_browser_runs WHERE runner_id=%s)',
                  (identifier,))
    database.rows('DELETE FROM bs_browser_assistance_requests WHERE runner_id=%s', (identifier,))
    database.rows('DELETE FROM bs_browser_runs WHERE runner_id=%s', (identifier,))


def test_disabled_browser_owner_refuses_before_browser_io_without_legacy_fallback(
        workers, actors, service_database, browser_page, browser_redis):
    client, prefix, environment = browser_redis
    workers.environment.update(environment)
    workers.environment['AGENT_RUNNER_BROWSER_OWNER_ENABLED'] = 'false'
    initial = tool_reply('browser_automation', {}, call_id='disabled-browser-call')
    marker = workers.provider.register(initial, Reply(content='fixture browser unavailable'))
    initial.tool_calls[0]['function']['arguments'] = json.dumps(
        {'task': marker + ' inspect fixture', 'url': browser_page.url, 'headless': True})
    accepted = accept(workers.api, actors['a'], marker)
    process, _ = workers.start()
    workers.assert_clean_exit(process)
    row = terminal(service_database, accepted['runner_id'])
    assert row['status'] == 'completed'
    call = decoded(row['checkpoint'])['execution']['tools']['disabled-browser-call']
    assert call['result_recorded'] and call['result']['success'] is False
    assert call['result']['error_code'] == 'BROWSER_PRODUCER_NOT_AVAILABLE'
    assert browser_page.requests == []
    assert service_database.rows('SELECT 1 FROM bs_browser_runs WHERE runner_id=%s', (accepted['runner_id'],)) == []
    # A fresh UserDB read legitimately populates this namespace's user cache.
    # No Browser run, lease, control, assistance or ticket may be created.
    assert list(client.scan_iter(match=prefix + ':browser_*')) == []
    from src.core.cache_utils import CacheKeys
    for key in (CacheKeys.AGENT_TOOL_SUSPENSION, CacheKeys.AGENT_SESSION_SUSPENSION,
                CacheKeys.AGENT_CONTINUATION_EVENTS):
        assert list(client.scan_iter(match=prefix + ':' + key + ':*')) == []
    requests = workers.provider.requests(marker)
    assert len(requests) == 2 and all(request.get('tools') for request in requests)


def test_actual_child_browser_binding_preserves_root_and_completed_sibling_facts(
        workers, actors, service_database, browser_page, browser_redis, child_profile):
    _, _, environment = browser_redis
    workers.environment.update(environment)
    workers.environment['AGENT_RUNNER_BROWSER_OWNER_ENABLED'] = 'true'
    first = Reply()
    sibling = Reply(content='fixture completed sibling')
    browser_call = tool_reply('browser_automation', {}, call_id='child-browser-original-call')
    decision = Reply(content=json.dumps({'action': 'done', 'summary': 'fixture browser done'}))
    marker = workers.provider.register(first, sibling, browser_call, decision,
        Reply(content='fixture browser child complete'), Reply(content='fixture parent complete'))
    first.content = ''
    first.tool_calls = tool_reply('delegate_to_subagent',
        {'subagent_name': child_profile[0], 'task_description': marker + ' first sibling'},
        call_id='browser-sibling-delegate').tool_calls + tool_reply('delegate_to_subagent',
        {'subagent_name': child_profile[0], 'task_description': marker + ' second browser child'},
        call_id='browser-child-delegate').tool_calls
    browser_call.tool_calls[0]['function']['arguments'] = json.dumps(
        {'task': marker + ' inspect fixture', 'url': browser_page.url, 'headless': True})
    accepted = accept(workers.api, actors['a'], marker)
    identifier = accepted['runner_id']
    process = None
    try:
        process, _ = workers.start()
        # Six physical calls plus two genuine child runtimes need a bound that
        # does not assume the single-root worker's usual 25-second duration.
        def diagnose():
            diagnosis = browser_diagnostics(workers, service_database, identifier, marker, browser_page, process)
            private_root = decoded(runner(service_database, identifier)['checkpoint']).get('execution', {})
            diagnosis['children'] = [
                {'outcome': (fact.get('checkpoint') or {}).get('outcome'),
                 'task_status': (fact.get('task_record') or {}).get('status'),
                 'tools': [{'phase': tool.get('phase'), 'result_recorded': tool.get('result_recorded')}
                           for tool in (fact.get('checkpoint') or {}).get('tools', {}).values()]}
                for fact in private_root.get('children', {}).values()]
            print('SAFE_BROWSER_CHILD_DIAG=' + json.dumps(diagnosis))
        try:
            try:
                code = process.wait(timeout=25)
            except subprocess.TimeoutExpired:
                # Preserve real progress at the original deadline, before any
                # additional bounded wait; this cannot hide a stuck child.
                diagnose()
                code = process.wait(timeout=65)
            assert code == 0
        except (subprocess.TimeoutExpired, AssertionError):
            diagnose()
            raise
        row = terminal(service_database, identifier)
        assert row['status'] == 'completed'
        root = decoded(row['checkpoint'])['execution']
        first_child = root['children']['browser-sibling-delegate']
        browser_child = root['children']['browser-child-delegate']
        assert first_child['execution_id'] != browser_child['execution_id']
        assert first_child['checkpoint']['outcome'] == browser_child['checkpoint']['outcome'] == 'completed'
        assert first_child['checkpoint']['output'] == sibling.content
        assert not root['resources'].get('browser_runs')
        assert not first_child['checkpoint']['resources'].get('browser_runs')
        fact = browser_child['checkpoint']['resources']['browser_runs']['child-browser-original-call']
        runs = service_database.rows('SELECT run_id,runner_execution_id,runner_tool_call_id,runtime_state,closed_at FROM bs_browser_runs WHERE runner_id=%s',
                                     (identifier,))
        assert len(runs) == 1 and runs[0]['run_id'] == fact['run_id']
        assert runs[0]['runner_execution_id'] == browser_child['execution_id']
        assert runs[0]['runner_tool_call_id'] == 'child-browser-original-call'
        assert runs[0]['runtime_state'] == 'closed' and runs[0]['closed_at'] is not None
        assert len(root['model_calls']) == 2 and len(first_child['checkpoint']['model_calls']) == 1
        assert len(browser_child['checkpoint']['model_calls']) == 2
        receipts = service_database.rows('SELECT execution_id,phase,applied FROM agent_runner_usage_receipts WHERE runner_id=%s',
                                         (identifier,))
        assert len(receipts) == 6 and all(r['phase'] == 'observed' and r['applied'] for r in receipts)
        assert {r['execution_id'] for r in receipts} == {identifier, first_child['execution_id'], browser_child['execution_id']}
        assert len(workers.provider.requests(marker)) == 6 and not workers.provider.errors
        assert browser_page.path in browser_page.requests and not browser_page.unexpected
        assert service_database.rows('SELECT owner_runner_id FROM agent_runner_session_claims WHERE owner_runner_id=%s',
                                     (identifier,)) == []
    finally:
        if process is not None:
            workers.processes.stop(process)
        delete_browser_facts(service_database, identifier)
