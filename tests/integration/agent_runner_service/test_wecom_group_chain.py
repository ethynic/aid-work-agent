"""Actual Worker/Gateway/SQL queue/Runtime/stdio MCP with fictional encrypted groups.

Only the remote model and desktop send driver are substituted. The model peer
derives its text from the HTTP prompt and observed group result, not Runner DB.
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import uuid

import pytest

from src.local_tools import repository as local
from src.services.agent_runner.wecom_device_binding import RESOURCE
from .conftest import wait_for
from .provider import Reply, tool_reply
from .test_worker import workers, prices, accept, runner, decoded
from .test_durable_sleep_worker_independent import service_processes, api_pair
from .test_worker_recovery import control, logs_schema

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures('logs_schema')]
ROOT = Path(__file__).resolve().parents[3]
BRIDGE = ROOT / 'clients/shared/local-tool-host-core/tests/fixtures/groupMonitorBridge.mjs'
GROUP_FIXTURE = ROOT / 'clients/wecom-cli/tests/py/group_monitor_fixture.py'
GROUP = '合成群甲'


def change_group(base, action, item=None):
    arguments = [sys.executable, str(GROUP_FIXTURE), str(base), action]
    if item is not None:
        arguments.append(json.dumps(item, ensure_ascii=False))
    subprocess.run(arguments, cwd=ROOT, check=True, capture_output=True, timeout=20)


@pytest.fixture
def configured_monitor(workers, actors, service_database, tmp_path):
    actor = actors['a']
    workers.environment['AGENT_RUNNER_DURABLE_SLEEP_ENABLED'] = 'true'
    profiles = []
    prompt_ids = []
    for index, style in enumerate(('BRIEF', 'WARM')):
        identifier = 'monitor-profile-'+uuid.uuid4().hex
        prompt_id = str(uuid.uuid4())
        prompt_ids.append(prompt_id)
        tools = ['wecom_group_poll', 'wecom_message_send'] + (['wecom_probe'] if index else [])
        service_database.rows('''INSERT INTO subagent_definitions(agent_id,name,description,tools,llm_provider)
            VALUES(%s,%s,'Synthetic configured profile',%s::jsonb,%s::jsonb)''',
            (identifier, identifier, json.dumps({'allowed': tools}),
             json.dumps({'provider': 'qwen', 'model_codes': {'qwen': workers.models[index]}})))
        service_database.rows('''INSERT INTO prompt_registry(id,scope,scope_id,latest_version)
            VALUES(%s,'subagent',%s,1)''', (prompt_id, identifier))
        service_database.rows('''INSERT INTO prompt_versions(prompt_id,version,content)
            VALUES(%s,1,%s)''', (prompt_id, 'PROFILE_REPLY_STYLE='+style+'; use group context and latest question.'))
        service_database.rows('''INSERT INTO subscriptions(subscription_id,tenant_id,subagent_type,status,payment_status)
            VALUES(%s,%s,%s,'active','paid')''', ('subscription-'+identifier, actor.tenant_id, identifier))
        service_database.rows('INSERT INTO user_agent_permissions(user_id,tenant_id,agent_id) VALUES(%s,%s,%s)',
                              (actor.user_id, actor.tenant_id, identifier))
        profiles.append(identifier)
    base = tmp_path / 'synthetic-wecom'
    change_group(base, 'setup')
    device = local.create_device(actor.tenant_id, actor.user_id, uuid.uuid4().hex,
                                capabilities={'provider_id': 'ai.aidwork.wecom'})
    assert local.select_device(actor.tenant_id, actor.user_id, str(device['id']))
    stop = threading.Event()
    results = []
    errors = []
    force_unknown = threading.Event()

    def desktop_peer():
        try:
            while not stop.is_set():
                local.touch_device_seen(str(device['id']))
                claim = uuid.uuid4().hex
                invocation = local.claim_next(str(device['id']), actor.tenant_id, claim, 120)
                if not invocation:
                    stop.wait(.05)
                    continue
                assert local.mark_started(str(invocation['id']), actor.tenant_id, claim)['state'] == 'running'
                response = subprocess.run([shutil.which('node'), str(BRIDGE)], input=json.dumps({
                    'tool_name': invocation['tool_name'], 'arguments': invocation['arguments_json']}, ensure_ascii=False),
                    capture_output=True, text=True, encoding='utf-8', cwd=ROOT,
                    env={**os.environ, 'WECOM_TEST_HOME': str(base)}, timeout=45, check=True)
                result = json.loads(response.stdout.splitlines()[-1])
                if force_unknown.is_set() and invocation['tool_name'] == 'wecom_message_send':
                    # The actual send already ran once; transport uncertainty must
                    # not grant a second physical attempt even with a receipt.
                    result.update(success=False, code='EXECUTION_UNKNOWN', effect='unknown')
                arguments = dict(code=result.get('code'), message=result.get('message'),
                                 effect=result.get('effect'), data=result.get('data'))
                first = local.write_result(str(invocation['id']), actor.tenant_id, claim, result['success'], **arguments)
                duplicate = local.write_result(str(invocation['id']), actor.tenant_id, claim, result['success'], **arguments)
                assert first['state'] == duplicate['state']
                results.append((invocation, result))
        except BaseException as error:
            errors.append(type(error).__name__)

    thread = threading.Thread(target=desktop_peer, daemon=True)
    thread.start()
    try:
        yield actor, profiles, base, device, results, errors, force_unknown
    finally:
        stop.set()
        thread.join(timeout=50)
        assert not thread.is_alive(), 'owned fixture device did not stop'
        assert not errors, 'synthetic Runtime boundary failed: '+','.join(errors)
        for table in ('local_tool_events', 'local_tool_invocations', 'local_tool_devices', 'client_usage_logs'):
            service_database.rows(f'DELETE FROM {table} WHERE tenant_id=%s', (actor.tenant_id,))
        service_database.rows('DELETE FROM user_agent_permissions WHERE agent_id=ANY(%s)', (profiles,))
        service_database.rows('DELETE FROM subscriptions WHERE subagent_type=ANY(%s)', (profiles,))
        service_database.rows('DELETE FROM subagent_definitions WHERE agent_id=ANY(%s)', (profiles,))
        service_database.rows('DELETE FROM prompt_versions WHERE prompt_id=ANY(%s::uuid[])', (prompt_ids,))
        service_database.rows('DELETE FROM prompt_registry WHERE id=ANY(%s::uuid[])', (prompt_ids,))


def poll_results(request):
    values = []
    for message in request['messages']:
        if message.get('role') != 'tool':
            continue
        try:
            value = json.loads(message['content'])
        except (ValueError, TypeError):
            continue
        if isinstance(value.get('data'), dict) and 'context_messages' in value['data']:
            values.append(value['data'])
    return values


def derived_reply(stage, style, model, generated):
    def produce(request):
        system = '\n'.join(item.get('content', '') for item in request['messages'] if item['role'] == 'system')
        if 'PROFILE_REPLY_STYLE='+style not in system or request['model'] != model:
            raise ValueError('selected profile/model did not reach real HTTP provider')
        tool_names = {item['function']['name'] for item in request.get('tools', [])}
        if not {'agent_sleep', 'wecom_group_poll', 'wecom_message_send'} <= tool_names:
            raise ValueError('configured tool surface missing')
        if ('wecom_probe' in tool_names) != (style == 'WARM'):
            raise ValueError('tool policy does not match selected profile')
        polls = poll_results(request)
        if stage in {'context', 'poll', 'send'} and not polls:
            codes = []
            for message in request['messages']:
                if message.get('role') == 'tool':
                    value = json.loads(message['content'])
                    codes.append({key: value.get(key) for key in ('success', 'code', 'error')})
            raise ValueError('poll-result-missing:'+json.dumps(codes))
        if stage == 'context':
            if polls[-1]['new_messages'] or 'ORCHID' not in json.dumps(polls[-1]['context_messages']):
                raise ValueError('baseline was treated as new input or lost context')
            return tool_reply('agent_sleep', {'seconds': 1})
        if stage == 'poll':
            return tool_reply('wecom_group_poll', {'target_name': GROUP, 'cursor': polls[-1]['cursor']})
        if stage == 'send':
            if 'ORCHID' not in json.dumps(polls[0]['context_messages']):
                raise ValueError('model lost initial group context')
            known_sent = set()
            for message in request['messages']:
                if message.get('role') == 'tool':
                    value = json.loads(message['content'])
                    detail = (value.get('data') or {}).get('verification_detail') or {}
                    if detail.get('method') == 'local-record':
                        known_sent.add(detail['message_id'])
            if known_sent and not any(item['msg_id'] in known_sent for item in polls[-1]['new_messages']):
                raise ValueError('fixture did not exercise own-record echo')
            inbound = [item for item in polls[-1]['new_messages'] if item['msg_id'] not in known_sent]
            if not inbound or not inbound[-1].get('mentions'):
                raise ValueError('new structured mention did not reach model')
            text = style+'::ORCHID::'+inbound[-1]['text']
            generated.append(text)
            return tool_reply('wecom_message_send', {'target_name': GROUP, 'text': text,
                                                    'group_cursor': polls[-1]['cursor']})
        return tool_reply('agent_sleep', {'seconds': 1 if stage == 'short-sleep' else 60})
    return Reply(produce=produce)


def start_and_wait(workers, database, accepted, *, expected='waiting'):
    child, _ = workers.start()
    workers.assert_clean_exit(child)
    current = runner(database, accepted['runner_id'])
    facts = decoded(current['checkpoint']).get('execution', {}).get('tools', {})
    safe_tools = [{key: (fact.get('result') or {}).get(key) for key in ('success', 'code', 'error')}
                  for fact in facts.values() if isinstance(fact.get('result'), dict)]
    assert current['status'] == expected, {'status': current['status'], 'error': current.get('error_code'),
                                         'tools': safe_tools, 'provider_errors': workers.provider.errors}
    return current


@pytest.mark.parametrize('variant', [0, 1])
def test_configured_profile_drives_actual_runtime_send_and_fresh_worker_retains_cursor(
        workers, configured_monitor, service_database, variant):
    actor, profiles, base, device, results, errors, _ = configured_monitor
    style = ('BRIEF', 'WARM')[variant]
    generated = []
    replies = [tool_reply('wecom_group_poll', {'target_name': GROUP})]
    for stage in ('context', 'poll', 'send', 'short-sleep', 'poll', 'send', 'sleep'):
        replies.append(derived_reply(stage, style, workers.models[variant], generated))
    marker = workers.provider.register(*replies)
    accepted = accept(workers.api, actor, marker+' User authorizes continuous text replies only to '+GROUP,
                      profile_id=profiles[variant], execution_mode='continuous')
    first = start_and_wait(workers, service_database, accepted)
    assert not (base/'sent.jsonl').exists()
    assert decoded(first['checkpoint'])['execution']['resources'][RESOURCE]['device_id'] == str(device['id'])
    change_group(base, 'append', {'text': 'question-one', 'mention_self': True})
    wait_for(lambda: service_database.rows('SELECT wake_at<=clock_timestamp() AS due FROM agent_runners WHERE runner_id=%s',
                                          (accepted['runner_id'],))[0]['due'])
    start_and_wait(workers, service_database, accepted)
    assert len(generated) == 1
    # Current global selection changes while this durable task sleeps.
    other = local.create_device(actor.tenant_id, actor.user_id, uuid.uuid4().hex,
                               capabilities={'provider_id': 'ai.aidwork.wecom'})
    assert local.select_device(actor.tenant_id, actor.user_id, str(other['id']))
    # The logged-in person may speak manually; self direction alone cannot be
    # treated as proof this is the Agent's own already-answered record.
    change_group(base, 'append', {'text': 'question-two', 'mention_self': True, 'self': True})
    wait_for(lambda: service_database.rows('SELECT wake_at<=clock_timestamp() AS due FROM agent_runners WHERE runner_id=%s',
                                          (accepted['runner_id'],))[0]['due'])
    # Competing fresh timer claimers must still produce one response only.
    children = [workers.start()[0], workers.start()[0]]
    for child in children:
        workers.assert_clean_exit(child)
    current = runner(service_database, accepted['runner_id'])
    assert current['status'] == 'waiting' and len(generated) == 2 and not errors
    sent = [json.loads(line) for line in (base/'sent.jsonl').read_text(encoding='utf-8').splitlines()]
    assert [item['text'] for item in sent] == generated
    assert generated == [style+'::ORCHID::@FixtureSelfquestion-one', style+'::ORCHID::@FixtureSelfquestion-two']
    polls = [item for item, result in results if item['tool_name'] == 'wecom_group_poll']
    assert len(polls) == 3 and 'cursor' not in polls[0]['arguments_json']
    assert polls[1]['arguments_json']['cursor'] != polls[2]['arguments_json']['cursor']
    assert all(str(item['device_id']) == str(device['id']) for item, result in results)
    count = len(workers.provider.requests(marker))
    require_cancel = workers.api.call('POST', '/v1/runners/'+accepted['runner_id']+'/cancel', actor=actor)
    assert require_cancel.status_code == 200
    start_and_wait(workers, service_database, accepted, expected='cancelled')
    assert len(workers.provider.requests(marker)) == count == 8
    assert len(service_database.rows('SELECT id FROM local_tool_invocations WHERE session_id=%s', (actor.session_id,))) == 5
    receipts = service_database.rows('SELECT receipt_id FROM agent_runner_usage_receipts WHERE runner_id=%s', (accepted['runner_id'],))
    assert len(receipts) == count


def test_unknown_actual_send_waits_without_model_or_desktop_replay(workers, configured_monitor, service_database):
    actor, profiles, base, _, results, errors, force_unknown = configured_monitor
    generated = []
    marker = workers.provider.register(tool_reply('wecom_group_poll', {'target_name': GROUP}),
        derived_reply('context', 'BRIEF', workers.models[0], generated),
        derived_reply('poll', 'BRIEF', workers.models[0], generated),
        derived_reply('send', 'BRIEF', workers.models[0], generated))
    accepted = accept(workers.api, actor, marker+' Authorized continuous group text replies to '+GROUP,
                      profile_id=profiles[0], execution_mode='continuous')
    start_and_wait(workers, service_database, accepted)
    change_group(base, 'append', {'text': 'question-unknown', 'mention_self': True})
    force_unknown.set()
    wait_for(lambda: service_database.rows('SELECT wake_at<=clock_timestamp() AS due FROM agent_runners WHERE runner_id=%s',
                                          (accepted['runner_id'],))[0]['due'])
    current = start_and_wait(workers, service_database, accepted)
    assert current['public_snapshot']['waiting']['kind'] == 'verification'
    assert len((base/'sent.jsonl').read_text(encoding='utf-8').splitlines()) == 1
    count = len(results)
    start_and_wait(workers, service_database, accepted)
    assert len(results) == count and len(workers.provider.requests(marker)) == 4 and not errors
    source = [item for item, result in results if item['tool_name'] == 'wecom_message_send'][0]
    assert local.get_invocation(str(source['id']), actor.tenant_id)['state'] == 'unknown'
    # Existing explicit stop/end-wait remains the only control exit; no implicit
    # send-status result or user text is manufactured by this fixture.
    response = workers.api.call('POST', '/v1/runners/'+accepted['runner_id']+'/cancel', actor=actor)
    assert response.status_code == 200
