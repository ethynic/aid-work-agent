"""A logged-in web turn uses a signed MCP definition through the real worker and PostgreSQL."""

import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.config.settings import settings
from src.local_tools import repository as devices
from src.local_tools.api import router as device_router
from src.local_tools.plugin_manifests import (
    MANIFEST_DIGESTS, PLUGIN_CHANGED_CODE, PLUGIN_PHASE, VERIFIED_MANIFESTS, load_device_tools,
)
from src.local_tools.security import sha256_hex

from tests.fixtures.runtime_plugin_registry import PROVIDER, TOOL, definition, signed_registry

from .conftest import wait_for
from .provider import Reply, tool_reply
from .test_local_invocation_recovery import local_diagnosis
from .test_worker import accept, api_pair, decoded, prices, terminal, workers
from .test_worker_recovery import logs_schema


pytestmark = pytest.mark.integration


@pytest.fixture
def opted_in_profile(workers, actors, service_database):
    actor = actors['a']
    identifier = 'after-sales-runtime-' + uuid.uuid4().hex
    prompt_id = str(uuid.uuid4())
    tools = {'inherit': False, 'allowed': ['http_api', 'boss_send_to'], 'additional': ['read'],
             'allow_local_runtime_tools': True}
    with service_database.connect() as connection, connection.cursor() as cursor:
        cursor.execute('''INSERT INTO subagent_definitions(agent_id, name, description, tools, llm_provider)
            VALUES(%s, '在线电商客服', 'Opted-in Runtime fixture', %s::jsonb, %s::jsonb)''',
            (identifier, json.dumps(tools), json.dumps({'provider': 'qwen', 'model_codes': {'qwen': workers.models[0]}})))
        cursor.execute("INSERT INTO prompt_registry(id, scope, scope_id, latest_version) VALUES(%s,'subagent',%s,1)",
                       (prompt_id, identifier))
        cursor.execute("INSERT INTO prompt_versions(prompt_id, version, content) VALUES(%s,1,'Test after-sales profile.')",
                       (prompt_id,))
        cursor.execute('''INSERT INTO subscriptions(subscription_id, tenant_id, subagent_type, status, payment_status)
            VALUES(%s,%s,%s,'active','paid')''', ('subscription-' + identifier, actor.tenant_id, identifier))
        cursor.execute('INSERT INTO user_agent_permissions(user_id, tenant_id, agent_id) VALUES(%s,%s,%s)',
                       (actor.user_id, actor.tenant_id, identifier))
        cursor.execute('UPDATE chat_sessions SET subagent_id=%s WHERE session_id=%s', (identifier, actor.session_id))
    try:
        yield identifier
    finally:
        with service_database.connect() as connection, connection.cursor() as cursor:
            cursor.execute('UPDATE chat_sessions SET subagent_id=NULL WHERE session_id=%s', (actor.session_id,))
            cursor.execute('DELETE FROM user_agent_permissions WHERE agent_id=%s', (identifier,))
            cursor.execute('DELETE FROM subscriptions WHERE subagent_type=%s', (identifier,))
            cursor.execute('DELETE FROM prompt_versions WHERE prompt_id=%s', (prompt_id,))
            cursor.execute('DELETE FROM prompt_registry WHERE id=%s', (prompt_id,))
            cursor.execute('DELETE FROM subagent_definitions WHERE agent_id=%s', (identifier,))


_RUNTIME = r'''
import json
import sys
import time
from pathlib import Path
from uuid import uuid4

from src.db.database import init_postgres_pool, close_postgres_pool
from src.local_tools import repository
from src.local_tools.security import sha256_hex

tenant, user, folder, provider, tool = sys.argv[1:]
root = Path(folder)
init_postgres_pool()
try:
    capabilities = {'capabilities': ['tool_declarations_v1', 'runtime_execution_receipt_v1_1'],
                    'declared_tools': {provider: [tool]}}
    tokens = [uuid4().hex for _ in range(2)]
    devices = [repository.create_device(tenant, user, sha256_hex(token), capabilities=capabilities)
               for token in tokens]
    selected, other = [str(device['id']) for device in devices]
    assert repository.select_device(tenant, user, selected)
    assert repository.touch_device_seen(selected)
    (root / 'devices.json').write_text(json.dumps([[selected, tokens[0]], [other, tokens[1]]]))
    claim = uuid4().hex
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        assert not repository.claim_next(other, tenant, claim, 30, provider_keys=[provider])
        current = repository.get_device_by_token_hash(sha256_hex(tokens[0]))
        digests = current['capabilities_json'].get('plugin_manifest_digests')
        invocation = repository.claim_next(selected, tenant, claim, 30, provider_keys=[provider],
                                           plugin_manifest_digests=digests)
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


@pytest.mark.parametrize('window', ['before-enqueue', 'before-claim', 'legacy-claim', 'matching-claim', 'disable-queued'])
def test_invocation_uses_the_release_the_model_saw(window, workers, actors, service_database, monkeypatch,
                                                opted_in_profile):
    actor = actors['a']
    root = workers.root / 'plugin-update'
    root.mkdir()
    sign = signed_registry(root)
    original = sign(definition('target'))
    updated = sign(definition('new_target'))
    digest_a = original['envelope']['manifest_digest']
    digest_b = updated['envelope']['manifest_digest']
    path = str(root / 'trust-roots.json')
    monkeypatch.setattr(settings.agent_runner, 'plugin_trust_roots', path)
    workers.environment['AGENT_RUNNER_PLUGIN_TRUST_ROOTS'] = path

    app = FastAPI()
    app.include_router(device_router)
    token = uuid.uuid4().hex
    headers = {'Authorization': 'Bearer ' + token}
    client = TestClient(app)
    capabilities = {'capabilities': ['tool_declarations_v1'], 'declared_tools': {PROVIDER: [TOOL]}}
    device = devices.create_device(actor.tenant_id, actor.user_id, sha256_hex(token), capabilities=capabilities)
    device_id = str(device['id'])
    assert devices.select_device(actor.tenant_id, actor.user_id, device_id)
    arguments = {'target': 'original-argument'}

    def sync(bundle, digest):
        reply = client.post('/api/local-tools/runtime/heartbeat', headers=headers, json={
            'capabilities': {**capabilities, MANIFEST_DIGESTS: {PROVIDER: digest}},
            'plugin_manifests': {PROVIDER: bundle},
        })
        assert reply.status_code == 200 and 'missing_plugin_manifests' not in reply.json()

    def invocation_rows():
        return service_database.rows('''SELECT id, state, effect, started_at, arguments_json, business_ref, error_code
            FROM local_tool_invocations WHERE device_id=%s AND arguments_json=%s::jsonb''',
            (device_id, json.dumps(arguments)))

    try:
        sync(original, digest_a)
        if window == 'disable-queued':
            claimed_before_disable = devices.create_invocation(actor.tenant_id, actor.user_id, device_id,
                TOOL, {'target': 'already-claimed'}, provider_key=PROVIDER, business_kind='agent_runner',
                business_ref={'branch': PLUGIN_PHASE, 'plugin_manifest_digest': digest_a})
            claim = client.post('/api/local-tools/runtime/claim?wait=0', headers=headers,
                                json={MANIFEST_DIGESTS: {PROVIDER: digest_a}})
            assert claim.json()['invocation_id'] == claimed_before_disable
            claimed_token = claim.json()['claim_token']
            static = devices.create_invocation(actor.tenant_id, actor.user_id, device_id,
                                               'boss_jobs_list', {}, provider_key='boss-recruiting')
        first = tool_reply(TOOL, arguments)
        if window == 'before-enqueue':
            def update_after_advertising(_request):
                sync(updated, digest_b)
                return first
            first.produce = update_after_advertising
        marker = workers.provider.register(first, Reply(content='Tool outcome received.'))
        accepted = accept(workers.api, actor, marker, profile_id=opted_in_profile)
        worker, _ = workers.start()

        if window == 'disable-queued':
            queued, = wait_for(invocation_rows, timeout=20)
            assert queued['state'] == 'queued'
            for body in ({}, {'capabilities': {}}, {
                    'capabilities': {**capabilities, MANIFEST_DIGESTS: {PROVIDER: digest_a}}}):
                assert client.post('/api/local-tools/runtime/heartbeat', headers=headers, json=body).status_code == 200
                assert invocation_rows()[0]['state'] == 'queued'
            disabled = {'capabilities': {'capabilities': ['tool_declarations_v1'], MANIFEST_DIGESTS: {}}}
            for _ in range(2):
                assert client.post('/api/local-tools/runtime/heartbeat', headers=headers, json=disabled).status_code == 200
            events = service_database.rows('SELECT stage FROM local_tool_events WHERE invocation_id=%s',
                                           (queued['id'],))
            assert [event['stage'] for event in events].count('failed') == 1
            unaffected = service_database.rows('SELECT id, state FROM local_tool_invocations WHERE id IN (%s,%s)',
                                               (static, claimed_before_disable))
            assert {str(row['id']): row['state'] for row in unaffected} == {
                static: 'queued', claimed_before_disable: 'claimed'}
            claim_hash = sha256_hex(claimed_token)
            devices.write_runtime_result(claimed_before_disable, actor.tenant_id, claim_hash,
                True, message='Already claimed invocation returned its original receipt', effect='none')
        elif window != 'before-enqueue':
            queued, = wait_for(invocation_rows, timeout=20)
            assert queued['state'] == 'queued' and queued['business_ref']['plugin_manifest_digest'] == digest_a
            claim_body = {} if window == 'legacy-claim' else {'json': {
                MANIFEST_DIGESTS: {PROVIDER: digest_a if window == 'matching-claim' else digest_b}}}
            claim = client.post('/api/local-tools/runtime/claim?wait=0', headers=headers, **claim_body)
            assert claim.status_code == 200
            if window == 'matching-claim':
                payload = claim.json()
                assert payload['tool_name'] == TOOL and payload['arguments'] == arguments
                claim_hash = sha256_hex(payload['claim_token'])
                assert devices.mark_started(payload['invocation_id'], actor.tenant_id, claim_hash)['state'] == 'running'
                devices.write_runtime_result(payload['invocation_id'], actor.tenant_id,
                    claim_hash, True, message='Original release executed', effect='none')
            else:
                assert claim.json()['invocation'] is None
                assert invocation_rows()[0]['state'] == 'queued'
                if window == 'legacy-claim':
                    # A full older Runtime heartbeat replaces its reported capabilities.
                    assert client.post('/api/local-tools/runtime/heartbeat', headers=headers,
                                       json={'capabilities': capabilities}).status_code == 200
                else:
                    # The freshly restarted Runtime synchronizes after its first claim.
                    sync(updated, digest_b)

        finished = terminal(service_database, accepted['runner_id'])
        workers.assert_clean_exit(worker)
        assert finished['status'] == 'completed', local_diagnosis(workers, service_database, accepted['runner_id'])
        first_request, second_request = workers.provider.requests(marker)
        advertised = next(tool['function'] for tool in first_request['tools'] if tool['function']['name'] == TOOL)
        assert advertised['parameters']['required'] == ['target']
        receipt = next(json.loads(message['content']) for message in second_request['messages'] if message['role'] == 'tool')
        if window == 'matching-claim':
            assert receipt['success'] is True and receipt['message'] == 'Original release executed'
        else:
            assert receipt['success'] is False and receipt['effect'] == 'none' and receipt['code'] == PLUGIN_CHANGED_CODE
            if window == 'before-enqueue':
                assert invocation_rows() == []
            else:
                declined, = invocation_rows()
                assert declined['state'] == 'failed' and declined['started_at'] is None
                assert declined['error_code'] == PLUGIN_CHANGED_CODE and declined['effect'] == 'none'
    finally:
        for table in ('local_tool_events', 'local_tool_invocations', 'local_tool_devices', 'client_usage_logs'):
            service_database.rows(f'DELETE FROM {table} WHERE tenant_id=%s', (actor.tenant_id,))


def test_pending_claim_uses_current_declarations_after_plugin_sync(workers, actors, service_database, monkeypatch,
                                                                  opted_in_profile):
    actor = actors['a']
    root = workers.root / 'plugin-claim-sync'
    root.mkdir()
    sign = signed_registry(root)
    old = sign(definition('target'))
    new_tool = 'sample_mcp_new'
    new_definition = definition('target', name=new_tool)
    new = sign(new_definition)
    path = str(root / 'trust-roots.json')
    monkeypatch.setattr(settings.agent_runner, 'plugin_trust_roots', path)
    workers.environment['AGENT_RUNNER_PLUGIN_TRUST_ROOTS'] = path
    app = FastAPI()
    app.include_router(device_router)
    token = uuid.uuid4().hex
    headers = {'Authorization': 'Bearer ' + token}
    client = TestClient(app)
    device = devices.create_device(actor.tenant_id, actor.user_id, sha256_hex(token))
    device_id = str(device['id'])
    assert devices.select_device(actor.tenant_id, actor.user_id, device_id)

    def heartbeat(bundle, tool, *, supply_bundle=False):
        capabilities = {'capabilities': ['tool_declarations_v1'], 'declared_tools': {PROVIDER: [tool]},
                        'parallel_tools': {PROVIDER: [tool]},
                        MANIFEST_DIGESTS: {PROVIDER: bundle['envelope']['manifest_digest']}}
        body = {'capabilities': capabilities}
        if supply_bundle:
            body['plugin_manifests'] = {PROVIDER: bundle}
        reply = client.post('/api/local-tools/runtime/heartbeat', headers=headers, json=body)
        assert reply.status_code == 200
        return reply.json()

    old_digest = old['envelope']['manifest_digest']
    current_digest = new['envelope']['manifest_digest']
    old_waiting = Event()
    current_waiting = Event()
    current_queued = Event()
    old_checked_new_queue = Event()
    allow_current_claim = Event()
    claim_next = devices.claim_next

    def observe_claim(*args, **kwargs):
        digest = (kwargs.get('plugin_manifest_digests') or {}).get(PROVIDER)
        if digest == current_digest and current_waiting.is_set():
            assert allow_current_claim.wait(20)
        attempts_current_queue = digest == old_digest and current_queued.is_set()
        row = claim_next(*args, **kwargs)
        if digest == old_digest:
            old_waiting.set()
            if attempts_current_queue:
                old_checked_new_queue.set()
        if digest == current_digest:
            current_waiting.set()
        return row

    monkeypatch.setattr(devices, 'claim_next', observe_claim)
    try:
        assert 'missing_plugin_manifests' not in heartbeat(old, TOOL, supply_bundle=True)
        arguments = {'target': 'from-the-current-manifest'}
        marker = workers.provider.register(tool_reply(new_tool, arguments), Reply(content='New tool receipt.'))
        with ThreadPoolExecutor(max_workers=2) as executor:
            stale = executor.submit(client.post, '/api/local-tools/runtime/claim?wait=15',
                                    headers=headers, json={MANIFEST_DIGESTS: {PROVIDER: old_digest}})
            pending = executor.submit(client.post, '/api/local-tools/runtime/claim?wait=25',
                                      headers=headers, json={MANIFEST_DIGESTS: {PROVIDER: current_digest}})
            try:
                assert old_waiting.wait(5) and current_waiting.wait(5)
                assert heartbeat(new, new_tool)['missing_plugin_manifests'] == [PROVIDER]
                assert 'missing_plugin_manifests' not in heartbeat(new, new_tool, supply_bundle=True)
                accepted = accept(workers.api, actor, marker, profile_id=opted_in_profile)
                worker, _ = workers.start()

                def queued_calls():
                    return service_database.rows('SELECT state FROM local_tool_invocations WHERE device_id=%s',
                                                 (device_id,))

                assert wait_for(queued_calls, timeout=12) == [{'state': 'queued'}]
                current_queued.set()
                assert old_checked_new_queue.wait(3)
                legacy = client.post('/api/local-tools/runtime/claim?wait=0', headers=headers)
                assert legacy.status_code == 200 and legacy.json()['invocation'] is None
                assert queued_calls() == [{'state': 'queued'}]
            finally:
                allow_current_claim.set()
            claim = pending.result(timeout=15)
            assert claim.status_code == 200
            payload = claim.json()
            assert payload['tool_name'] == new_tool and payload['arguments'] == arguments
            stale_response = stale.result(timeout=15)
            assert stale_response.status_code == 200 and stale_response.json()['invocation'] is None
        claim_hash = sha256_hex(payload['claim_token'])
        assert devices.mark_started(payload['invocation_id'], actor.tenant_id, claim_hash)['state'] == 'running'
        devices.write_runtime_result(payload['invocation_id'], actor.tenant_id, claim_hash, True,
                                     message='New release executed', effect='none')
        assert terminal(service_database, accepted['runner_id'])['status'] == 'completed'
        workers.assert_clean_exit(worker)
        first_request, second_request = workers.provider.requests(marker)
        assert any(tool['function']['name'] == new_tool for tool in first_request['tools'])
        receipt = next(json.loads(message['content']) for message in second_request['messages'] if message['role'] == 'tool')
        assert receipt['success'] is True and receipt['message'] == 'New release executed'
    finally:
        for table in ('local_tool_events', 'local_tool_invocations', 'local_tool_devices', 'client_usage_logs'):
            service_database.rows(f'DELETE FROM {table} WHERE tenant_id=%s', (actor.tenant_id,))


def test_worker_dispatches_native_tool_to_selected_device_and_returns_standard_receipt(
        workers, actors, service_database, logs_schema, monkeypatch, opted_in_profile):
    actor = actors['a']
    root = workers.root / 'plugin-sync'
    root.mkdir()
    runtime = workers.processes.start(['-c', _RUNTIME, actor.tenant_id, actor.user_id,
                                      str(root), PROVIDER, TOOL], environment=workers.environment)
    try:
        device_ids = root / 'devices.json'
        wait_for(device_ids.exists, timeout=15)
        (selected, selected_token), (other, other_token) = json.loads(device_ids.read_text())
        sign = signed_registry(root)
        path = str(root / 'trust-roots.json')
        monkeypatch.setattr(settings.agent_runner, 'plugin_trust_roots', path)
        workers.environment['AGENT_RUNNER_PLUGIN_TRUST_ROOTS'] = path

        app = FastAPI()
        app.include_router(device_router)
        client = TestClient(app)

        def heartbeat(body, token=selected_token):
            response = client.post('/api/local-tools/runtime/heartbeat', json=body,
                                   headers={'Authorization': f'Bearer {token}'})
            assert response.status_code == 200
            return response.json()

        def stored(device_id):
            return service_database.rows('SELECT capabilities_json FROM local_tool_devices WHERE id=%s',
                                         (device_id,))[0]['capabilities_json']

        legacy = {'capabilities': ['tool_declarations_v1', 'runtime_execution_receipt_v1_1'],
                  'declared_tools': {PROVIDER: [TOOL]}}
        assert 'missing_plugin_manifests' not in heartbeat({'capabilities': legacy})

        def report(bundle, **extra):
            return {'capabilities': {**legacy, MANIFEST_DIGESTS: {PROVIDER: bundle['envelope']['manifest_digest']}}, **extra}

        old = sign(definition('old_target'))
        assert heartbeat(report(old))['missing_plugin_manifests'] == [PROVIDER]
        assert 'missing_plugin_manifests' not in heartbeat(report(old, plugin_manifests={PROVIDER: old}))
        assert 'missing_plugin_manifests' not in heartbeat(report(old))

        chosen_definition = definition('target')
        chosen = sign(chosen_definition)
        assert heartbeat(report(chosen, plugin_manifests={PROVIDER: old}))['missing_plugin_manifests'] == [PROVIDER]
        assert VERIFIED_MANIFESTS not in stored(selected)
        bad = {**chosen, 'envelope': {**chosen['envelope'], 'signature': 'base64:AAAA'}}
        assert heartbeat(report(chosen, plugin_manifests={PROVIDER: bad}))['missing_plugin_manifests'] == [PROVIDER]
        forged = report(chosen)
        forged['capabilities'][VERIFIED_MANIFESTS] = {'forged': True}
        assert heartbeat(forged, other_token)['missing_plugin_manifests'] == [PROVIDER]
        assert VERIFIED_MANIFESTS not in stored(other)
        assert 'missing_plugin_manifests' not in heartbeat(report(chosen, plugin_manifests={PROVIDER: chosen}))
        assert 'missing_plugin_manifests' not in heartbeat({})
        assert load_device_tools(path, stored(selected), legacy['declared_tools']) == [(PROVIDER, chosen_definition)]
        assert heartbeat(report(chosen), other_token)['missing_plugin_manifests'] == [PROVIDER]

        value = 'device-only-value-fixture'
        arguments = {'target': value}
        marker = workers.provider.register(tool_reply(TOOL, arguments, call_id='plugin-call'),
                                           Reply(content='Received the original device result.'))
        accepted = accept(workers.api, actor, marker, profile_id=opted_in_profile)
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
        names = {tool['function']['name'] for tool in requests[0]['tools']}
        assert {'http_api', 'boss_send_to'} <= names and 'read' not in names
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

        service_database.rows("""UPDATE subagent_definitions SET updated_at=NOW(),
            tools=jsonb_set(tools, '{allow_local_runtime_tools}', 'false') WHERE agent_id=%s""", (opted_in_profile,))
        assert load_device_tools(path, stored(selected), legacy['declared_tools']) == [(PROVIDER, chosen_definition)]
        followup = workers.provider.register(Reply(content='Profile opted out.'))
        second = accept(workers.api, actor, followup, profile_id=opted_in_profile)
        second_worker, _ = workers.start()
        workers.assert_clean_exit(second_worker)
        assert terminal(service_database, second['runner_id'])['status'] == 'completed'
        second_request, = workers.provider.requests(followup)
        assert all(tool['function']['name'] != TOOL for tool in second_request['tools'])
        assert {'http_api', 'boss_send_to'} <= {tool['function']['name'] for tool in second_request['tools']}
        assert value in json.dumps(second_request['messages'])
        followup_spans = service_database.rows('SELECT input FROM obs_spans WHERE trace_id IN (%s, %s) AND name=%s',
                                               (f'tr_{second["runner_id"]}', f'tr_{second["runner_id"]}:1', 'llm_call'))
        assert len(followup_spans) == 1 and value not in json.dumps(followup_spans)
        assert 'missing_plugin_manifests' not in heartbeat({'capabilities': {**legacy, MANIFEST_DIGESTS: {}}})
        assert VERIFIED_MANIFESTS not in stored(selected)
    finally:
        workers.processes.stop(runtime)
        for table in ('local_tool_events', 'local_tool_invocations', 'local_tool_devices', 'client_usage_logs'):
            service_database.rows(f'DELETE FROM {table} WHERE tenant_id=%s', (actor.tenant_id,))
