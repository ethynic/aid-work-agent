"""Original webhook ACKs and physical in-flight cancellation; await B2 freeze."""
from pathlib import Path
import json
import uuid

import pytest

from .conftest import wait_for
from .domain_fixtures import recruiting_domain
from .domain_io import WebhookPeer, WebhookReply
from .test_local_notify_flow import done_input, configure_notify, notify_request
from .test_local_invocation_recovery import interrupted
from .test_worker import workers, api_pair, prices, accept, runner, terminal, decoded
from .test_worker_recovery import control

pytestmark = pytest.mark.integration


def original_chunks(arguments):
    from src.services.recruiting_notify_service import format_done_content
    from src.services.wecom_bot import _split_markdown
    content = format_done_content(arguments['job_name'], arguments['candidates'])
    chunks = _split_markdown(content)
    assert len(chunks) >= 2
    return content, chunks


@pytest.mark.parametrize('boundary', ['first-ack-restart', 'first-ack-cancel', 'all-ack-cancel'])
def test_actual_committed_notification_ack_is_reused_without_new_delivery(
        workers, recruiting_domain, service_database, boundary):
    from src.services.recruiting_notify_service import upsert_settings
    actor, profile = recruiting_domain
    arguments = done_input()
    content, chunks = original_chunks(arguments)
    branch = 'notify.mention' if boundary == 'all-ack-cancel' else 'notify.markdown'
    with WebhookPeer(*[WebhookReply() for _ in range(len(chunks) + 1)]) as peer:
        configure_notify(actor, peer)
        marker = notify_request(workers, arguments)
        accepted = accept(workers.api, actor, marker, profile_id=profile)
        gate = workers.root / ('notify-commit-' + uuid.uuid4().hex)
        gate.mkdir()
        worker_id = 'fixture-notify-commit-' + uuid.uuid4().hex
        first = workers.processes.start([str(Path(__file__).with_name('domain_commit_probe.py')),
            worker_id, branch, '0', str(gate)], environment=workers.environment,
            private_working_directory=True)
        workers.children.append((first, worker_id))
        wait_for((gate / 'committed.json').exists, timeout=20)
        assert json.loads((gate / 'committed.json').read_text())['branch'] == branch
        before = runner(service_database, accepted['runner_id'])
        prior = decoded(before['checkpoint'])['execution']['resources']['local_domain_phases']
        assert service_database.rows('SELECT 1 FROM bs_recruiting_notify_logs WHERE tenant_id=%s', (actor.tenant_id,)) == []
        if boundary == 'first-ack-restart':
            stopped = interrupted(workers, service_database, accepted, first, hard=True)
            control(workers.api, actor, accepted['runner_id'], 'resume')
            second, _ = workers.start()
            workers.assert_clean_exit(second)
            assert runner(service_database, accepted['runner_id'])['attempt'] == stopped['attempt'] + 1
        else:
            assert workers.api.call('POST', f"/v1/runners/{accepted['runner_id']}/cancel", actor=actor).status_code == 200
            # New settings must not invalidate an already known original ACK
            # or suppress its required audit; no new POST is authorized.
            upsert_settings(actor.tenant_id, enabled=False)
            (gate / 'release').touch()
            workers.assert_clean_exit(first)
        finished = terminal(service_database, accepted['runner_id'])
        expected_count = 1 if boundary == 'first-ack-cancel' else len(chunks) + 1
        assert len(peer.requests) == expected_count and not peer.errors
        assert peer.requests[0] == {'msgtype': 'markdown', 'markdown': {'content': chunks[0]}}
        if expected_count > 1:
            assert [request['markdown']['content'] for request in peer.requests[:-1]] == chunks
            assert peer.requests[-1]['msgtype'] == 'text'
    assert finished['status'] == ('completed' if boundary == 'first-ack-restart' else 'cancelled')
    after = decoded(finished['checkpoint'])['execution']['resources']['local_domain_phases']
    for key, fact in prior.items():
        assert after[key] == fact
    logs = service_database.rows('SELECT status,content,error FROM bs_recruiting_notify_logs WHERE tenant_id=%s', (actor.tenant_id,))
    assert len(logs) == 1 and logs[0]['content'] == content
    assert logs[0]['status'] == ('failed' if boundary == 'first-ack-cancel' else 'sent')
    if boundary == 'first-ack-cancel':
        assert logs[0]['error'] and peer.url not in logs[0]['error']
    assert len(workers.provider.requests(marker)) == (2 if boundary == 'first-ack-restart' else 1)
    assert not workers.provider.errors
    assert service_database.rows('SELECT 1 FROM agent_runner_session_claims WHERE owner_runner_id=%s', (accepted['runner_id'],)) == []


def test_actual_inflight_http_cancel_keeps_claim_until_original_ack_and_sends_no_next_chunk(
        workers, recruiting_domain, service_database):
    import threading
    from .provider import Reply
    actor, profile = recruiting_domain
    arguments = done_input()
    _, chunks = original_chunks(arguments)
    release = threading.Event()
    pending = WebhookReply(release=release)
    with WebhookPeer(pending) as peer:
        configure_notify(actor, peer)
        marker = notify_request(workers, arguments)
        accepted = accept(workers.api, actor, marker, profile_id=profile)
        first, _ = workers.start()
        assert pending.arrived.wait(15), 'Original physical HTTP POST did not arrive'
        try:
            assert workers.api.call('POST', f"/v1/runners/{accepted['runner_id']}/cancel", actor=actor).status_code == 200
            waiting = runner(service_database, accepted['runner_id'])
            assert waiting['status'] not in {'completed', 'failed', 'cancelled'}
            assert service_database.rows('SELECT owner_runner_id FROM agent_runner_session_claims WHERE owner_runner_id=%s', (accepted['runner_id'],)) == [{'owner_runner_id': accepted['runner_id']}]
            next_marker = workers.provider.register(Reply(content='must-not-start-before-original-HTTP-ack'))
            queued = accept(workers.api, actor, next_marker, profile_id=profile)
            contender, _ = workers.start()
            workers.assert_clean_exit(contender)
            assert runner(service_database, queued['runner_id'])['status'] == 'queued'
            assert workers.provider.requests(next_marker) == []
            assert len(peer.requests) == 1
            assert runner(service_database, accepted['runner_id'])['status'] not in {'completed', 'failed', 'cancelled'}
            release.set()
            workers.assert_clean_exit(first)
            finished = terminal(service_database, accepted['runner_id'])
        finally:
            release.set()
        assert peer.requests == [{'msgtype': 'markdown', 'markdown': {'content': chunks[0]}}]
        assert not peer.errors
    assert finished['status'] == 'cancelled'
    logs = service_database.rows('SELECT status FROM bs_recruiting_notify_logs WHERE tenant_id=%s', (actor.tenant_id,))
    assert logs == [{'status': 'failed'}]
    assert len(workers.provider.requests(marker)) == 1 and not workers.provider.errors
    assert service_database.rows('SELECT 1 FROM agent_runner_session_claims WHERE owner_runner_id=%s', (accepted['runner_id'],)) == []
