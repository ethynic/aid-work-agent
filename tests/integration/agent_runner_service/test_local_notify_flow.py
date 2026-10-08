"""Actual registered notification, localhost HTTP and PG; await B2 freeze.

Only the physical webhook ACK and external model responses are fictional.
Normal CLI, Runtime, HTTP owner, phases, log writer and finalizer stay real.
"""
from decimal import Decimal

import pytest

from .domain_fixtures import recruiting_domain
from .domain_io import WebhookPeer, WebhookReply
from .provider import Reply, tool_reply
from .test_worker import workers, api_pair, prices, accept, terminal, decoded

pytestmark = pytest.mark.integration


def done_input():
    # Valid original InputModel lengths, genuinely exceeding one UTF-8 chunk.
    return {'kind': 'done', 'job_name': '虚构通知岗位', 'candidates': [
        {'name': '虚构候选人' + str(index) + '甲' * 22,
         'time': '虚构时间' + '乙' * 42} for index in range(10)]}


def configure_notify(actor, peer, **values):
    from src.services.recruiting_notify_service import upsert_settings
    return upsert_settings(actor.tenant_id, enabled=True, webhook_url=peer.url,
                           at_mobiles=['13800000000'], pre_notify_enabled=True,
                           **values)


def notify_request(workers, arguments):
    return workers.provider.register(
        tool_reply('boss_interview_notify', arguments, call_id='original-notify-call'),
        Reply(content='fictional-notification-explained'))


def assert_no_desktop_cost(database, actor, runner_id):
    assert database.rows('SELECT 1 FROM local_tool_invocations WHERE session_id=%s', (actor.session_id,)) == []
    assert database.rows('SELECT 1 FROM client_usage_logs WHERE tenant_id=%s', (actor.tenant_id,)) == []
    assert database.rows('SELECT total_token_count,credit_cost FROM chat_records WHERE session_id=%s',
                         (actor.session_id,)) == [{'total_token_count': 36, 'credit_cost': Decimal('0.01')}]
    receipts = database.rows('SELECT phase,applied FROM agent_runner_usage_receipts WHERE runner_id=%s', (runner_id,))
    assert receipts == [{'phase': 'observed', 'applied': True}, {'phase': 'observed', 'applied': True}]


def test_actual_done_notify_sends_each_utf8_chunk_then_mention_and_one_original_log(
        workers, recruiting_domain, service_database):
    from src.services.recruiting_notify_service import format_done_content
    from src.services.wecom_bot import _split_markdown
    actor, profile = recruiting_domain
    arguments = done_input()
    content = format_done_content(arguments['job_name'], arguments['candidates'])
    chunks = _split_markdown(content)
    assert len(chunks) >= 2 and all(len(chunk.encode('utf-8')) <= 2040 for chunk in chunks)
    # Exact script exhaustion makes a duplicate physical POST observable.
    with WebhookPeer(*[WebhookReply() for _ in range(len(chunks) + 1)]) as peer:
        configure_notify(actor, peer)
        marker = notify_request(workers, arguments)
        accepted = accept(workers.api, actor, marker, profile_id=profile)
        process, _ = workers.start()
        workers.assert_clean_exit(process)
        finished = terminal(service_database, accepted['runner_id'])
        requests = peer.requests
        assert requests[:-1] == [{'msgtype': 'markdown', 'markdown': {'content': chunk}} for chunk in chunks]
        assert requests[-1] == {'msgtype': 'text', 'text': {
            'content': '面试邀约已完成，请查看上条详情',
            'mentioned_mobile_list': ['13800000000']}}
        assert not peer.errors
        assert peer.url not in str(decoded(finished['checkpoint'])['execution']['resources'])
    assert finished['status'] == 'completed'
    execution = decoded(finished['checkpoint'])['execution']
    fact = execution['tools']['original-notify-call']['result']
    assert fact['success'] and fact['data']['pushed']
    logs = service_database.rows('SELECT id,kind,status,content,candidates,resume_id FROM bs_recruiting_notify_logs WHERE tenant_id=%s', (actor.tenant_id,))
    assert logs == [{'id': fact['data']['log_id'], 'kind': 'done', 'status': 'sent',
                     'content': content, 'candidates': arguments['candidates'], 'resume_id': None}]
    assert len(execution['model_calls']) == 2 and len(workers.provider.requests(marker)) == 2
    assert not workers.provider.errors
    assert_no_desktop_cost(service_database, actor, accepted['runner_id'])


@pytest.mark.parametrize('mode', ['disabled', 'pre-disabled', 'enabled-pre'])
def test_actual_notification_settings_keep_original_skip_and_pre_message_contract(
        workers, recruiting_domain, service_database, mode):
    from src.services.recruiting_notify_service import upsert_settings, format_pre_content
    actor, profile = recruiting_domain
    arguments = {'kind': 'pre', 'job_name': '虚构通知岗位', 'candidates': [
        {'name': '虚构短名单', 'score': 82, 'highlight': '虚构匹配说明', 'time': '虚构时间'}],
        'note': '虚构事前说明'}
    with WebhookPeer(*([WebhookReply()] if mode == 'enabled-pre' else [])) as peer:
        configure_notify(actor, peer)
        if mode == 'disabled':
            upsert_settings(actor.tenant_id, enabled=False)
        elif mode == 'pre-disabled':
            upsert_settings(actor.tenant_id, pre_notify_enabled=False)
        marker = notify_request(workers, arguments)
        accepted = accept(workers.api, actor, marker, profile_id=profile)
        process, _ = workers.start()
        workers.assert_clean_exit(process)
        finished = terminal(service_database, accepted['runner_id'])
        requests = peer.requests
        assert not peer.errors
    assert finished['status'] == 'completed'
    fact = decoded(finished['checkpoint'])['execution']['tools']['original-notify-call']['result']
    assert fact['success'] and fact['data']['pushed'] == (mode == 'enabled-pre')
    logs = service_database.rows('SELECT kind,status,content FROM bs_recruiting_notify_logs WHERE tenant_id=%s', (actor.tenant_id,))
    if mode == 'enabled-pre':
        content = format_pre_content(arguments['job_name'], arguments['candidates'], arguments['note'])
        assert requests == [{'msgtype': 'markdown', 'markdown': {'content': content}}]
        assert logs == [{'kind': 'pre', 'status': 'sent', 'content': content}]
    else:
        assert requests == [] and logs == [] and fact['data']['log_id'] is None
    assert len(workers.provider.requests(marker)) == 2 and not workers.provider.errors
    assert_no_desktop_cost(service_database, actor, accepted['runner_id'])
