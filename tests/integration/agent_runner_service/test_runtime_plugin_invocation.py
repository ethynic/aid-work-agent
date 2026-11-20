"""A logged-in web turn uses a signed MCP definition through the real worker and PostgreSQL."""

import json
import uuid

import pytest

from tests.fixtures.runtime_plugin_registry import PROVIDER, TOOL, definition, signed_registry

from .conftest import wait_for
from .provider import Reply, tool_reply
from .test_local_invocation_recovery import local_diagnosis
from .test_worker import accept, api_pair, decoded, prices, terminal, workers
from .test_worker_recovery import logs_schema


pytestmark = pytest.mark.integration

_RUNTIME = r'''
import json
import sys
import time
from pathlib import Path
from uuid import uuid4

from src.db.database import init_postgres_pool, close_postgres_pool
from src.local_tools import repository

tenant, user, folder, provider, tool = sys.argv[1:]
root = Path(folder)
init_postgres_pool()
try:
    capabilities = {'capabilities': ['tool_declarations_v1', 'runtime_execution_receipt_v1_1'],
                    'declared_tools': {provider: [tool]}}
    devices = [repository.create_device(tenant, user, uuid4().hex, capabilities=capabilities)
               for _ in range(2)]
    selected, other = [str(device['id']) for device in devices]
    assert repository.select_device(tenant, user, selected)
    assert repository.touch_device_seen(selected)
    (root / 'devices.json').write_text(json.dumps([selected, other]))
    claim = uuid4().hex
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        assert not repository.claim_next(other, tenant, claim, 30, provider_keys=[provider])
        invocation = repository.claim_next(selected, tenant, claim, 30, provider_keys=[provider])
        if invocation:
            break
        time.sleep(0.1)
    else:
        raise AssertionError('Selected device did not receive the invocation')
    invocation_id = str(invocation['id'])
    assert repository.mark_started(invocation_id, tenant, claim)['state'] == 'running'
    repository.write_runtime_result(invocation_id, tenant, claim, False,
        code='PAGE_CLOSED', message='测试页面已关闭', effect='partial',
        data={'receipt_version': '1.1', 'step': 'batch', 'reason': 'page_closed',
              'known_effect': 'partial', 'partial_results': [{'success': True}]})
finally:
    close_postgres_pool()
'''


def test_worker_dispatches_native_tool_to_selected_device_and_returns_standard_receipt(
        workers, actors, service_database, logs_schema):
    actor = actors['a']
    root = workers.root / 'plugin-registration'
    root.mkdir()
    runtime = workers.processes.start(['-c', _RUNTIME, actor.tenant_id, actor.user_id,
                                      str(root), PROVIDER, TOOL], environment=workers.environment)
    try:
        device_ids = root / 'devices.json'
        wait_for(device_ids.exists, timeout=15)
        selected, other = json.loads(device_ids.read_text())
        register = signed_registry(root)
        chosen_definition = definition('target')
        register({'id': selected}, chosen_definition)
        register({'id': other}, definition('other_target'))
        workers.environment['AGENT_RUNNER_PLUGIN_MANIFEST_DIR'] = str(root)

        value = 'device-only-value-fixture'
        arguments = {'target': value}
        marker = workers.provider.register(tool_reply(TOOL, arguments, call_id='plugin-call'),
                                           Reply(content='Received the original device result.'))
        accepted = accept(workers.api, actor, marker)
        worker, _ = workers.start()
        finished = terminal(service_database, accepted['runner_id'])
        assert finished['status'] == 'completed', json.dumps({
            'code': decoded(finished['result']).get('error_code'),
            **local_diagnosis(workers, service_database, accepted['runner_id']),
        })
        workers.assert_clean_exit(worker)
        requests = workers.provider.requests(marker)
        assert len(requests) == 2 and not workers.provider.errors
        advertised = [tool['function'] for tool in requests[0]['tools'] if tool['function']['name'] == TOOL]
        assert advertised == [{'name': TOOL, 'description': chosen_definition['description'],
                               'parameters': chosen_definition['inputSchema']}]
        context = requests[1]['messages']
        call_id = next(call['id'] for message in context for call in message.get('tool_calls', [])
                       if call['function']['name'] == TOOL)
        receipt = next(json.loads(message['content']) for message in context
                       if message.get('tool_call_id') == call_id)
        assert receipt.get('code') == 'PAGE_CLOSED' and receipt['message'] == '测试页面已关闭', receipt
        assert receipt['effect'] == 'partial' and receipt['data']['partial_results'] == [{'success': True}]
        assert runtime.wait(timeout=5) == 0
        rows = service_database.rows('''SELECT id, provider_key, device_id, tool_name, arguments_json, state
            FROM local_tool_invocations WHERE tenant_id=%s AND user_id=%s''', (actor.tenant_id, actor.user_id))
        assert len(rows) == 1
        row = rows[0]
        assert row['provider_key'] == PROVIDER and row['tool_name'] == TOOL
        assert str(row['device_id']) == selected and str(row['id']) == receipt['invocation_id']
        assert decoded(row['arguments_json']) == arguments and row['state'] == 'failed'

        public = decoded(finished['public_snapshot'])
        start = next(event for event in public['progressMessages']
                     if event['type'] == 'tool_start' and event['toolName'] == TOOL)
        assert start['toolArgs'] == {} and value not in json.dumps(public)
        details = service_database.rows('SELECT execution_details FROM chat_records WHERE record_id=%s',
                                        (finished['record_id'],))[0]['execution_details']
        stats = next(item for item in decoded(details)['tool_executions'] if item['tool_name'] == TOOL)
        assert stats['tool_args'] == {} and value not in json.dumps(details)
        spans = service_database.rows('SELECT name, input FROM obs_spans WHERE trace_id=%s OR trace_id=%s',
                                      (f'tr_{accepted["runner_id"]}', f'tr_{accepted["runner_id"]}:1'))
        assert any(span['name'] == f'tool:{TOOL}' for span in spans), [span['name'] for span in spans]
        assert any(span['name'] == 'llm_call' for span in spans)
        assert value not in json.dumps(spans)
        assert value in json.dumps(requests[1]['messages'])

        workers.environment['AGENT_RUNNER_PLUGIN_MANIFEST_DIR'] = ''
        followup = workers.provider.register(Reply(content='Plugin disabled.'))
        second = accept(workers.api, actor, followup)
        second_worker, _ = workers.start()
        workers.assert_clean_exit(second_worker)
        assert terminal(service_database, second['runner_id'])['status'] == 'completed'
        second_request, = workers.provider.requests(followup)
        assert all(tool['function']['name'] != TOOL for tool in second_request['tools'])
        assert value in json.dumps(second_request['messages'])
        followup_spans = service_database.rows('SELECT input FROM obs_spans WHERE trace_id IN (%s, %s) AND name=%s',
                                               (f'tr_{second["runner_id"]}', f'tr_{second["runner_id"]}:1', 'llm_call'))
        assert len(followup_spans) == 1 and value not in json.dumps(followup_spans)
    finally:
        workers.processes.stop(runtime)
        for table in ('local_tool_events', 'local_tool_invocations', 'local_tool_devices', 'client_usage_logs'):
            service_database.rows(f'DELETE FROM {table} WHERE tenant_id=%s', (actor.tenant_id,))
