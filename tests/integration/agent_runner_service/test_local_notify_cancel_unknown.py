"""Unknown real HTTP outcome stays blocked after user cancellation."""
import pytest

from .domain_fixtures import recruiting_domain
from .domain_io import WebhookPeer, WebhookReply
from .test_local_notify_flow import configure_notify, done_input, notify_request
from .provider import Reply
from .test_worker import workers, api_pair, prices, accept, runner, decoded

pytestmark = pytest.mark.integration


def test_actual_unknown_ack_cancel_blocks_claim_and_cannot_hot_acquire_next_session_job(
        workers, recruiting_domain, service_database):
    actor, profile = recruiting_domain
    with WebhookPeer(WebhookReply(payload={'errmsg': 'fictional missing original acknowledgement'})) as peer:
        configure_notify(actor, peer)
        marker = notify_request(workers, done_input())
        accepted = accept(workers.api, actor, marker, profile_id=profile)
        first, _ = workers.start()
        workers.assert_clean_exit(first)
        original = runner(service_database, accepted['runner_id'])
        assert original['status'] == 'waiting'
        original_facts = decoded(original['checkpoint'])['execution']['resources']['local_domain_phases']
        assert len(peer.requests) == 1 and not peer.errors
        assert workers.api.call('POST', f"/v1/runners/{accepted['runner_id']}/cancel", actor=actor).status_code == 200
        cancel_worker, _ = workers.start()
        exit_code = cancel_worker.wait(timeout=25)
        blocked = runner(service_database, accepted['runner_id'])
        checkpoint = decoded(blocked['checkpoint'])
        claim_count = service_database.rows('SELECT count(*) AS n FROM agent_runner_session_claims WHERE owner_runner_id=%s', (accepted['runner_id'],))[0]['n']
        # Collect true safe facts before a regression assertion removes rows.
        diagnosis = {'cli_exit': exit_code, 'status': blocked['status'], 'attempt': blocked['attempt'],
                     'revision': blocked['revision'], 'claim_count': claim_count,
                     'blocked': bool(checkpoint.get('cancel_completion_blocked')),
                     'physical_http_count': len(peer.requests)}
        assert blocked['status'] == 'waiting' and claim_count == 1, diagnosis
        assert exit_code == 0, diagnosis
        assert blocked['worker_id'] is None and blocked['lease_until'] is None
        assert checkpoint['cancel_completion_blocked']['ready'] is False
        assert checkpoint['cancel_completion_blocked']['error_code'] == 'LOCAL_CANCEL_EFFECT_VERIFICATION_REQUIRED'
        assert checkpoint['execution']['resources']['local_domain_phases'] == original_facts
        next_marker = workers.provider.register(Reply(content='must-not-run-past-unknown-original-notification'))
        queued = accept(workers.api, actor, next_marker, profile_id=profile)
        contender, _ = workers.start()
        workers.assert_clean_exit(contender)
        after = runner(service_database, accepted['runner_id'])
        assert after['attempt'] == blocked['attempt'] and after['status'] == 'waiting'
        assert runner(service_database, queued['runner_id'])['status'] == 'queued'
        assert workers.provider.requests(next_marker) == []
        assert len(peer.requests) == 1 and not peer.errors
    assert len(workers.provider.requests(marker)) == 1 and not workers.provider.errors
    assert service_database.rows('SELECT 1 FROM bs_recruiting_notify_logs WHERE tenant_id=%s', (actor.tenant_id,)) == []
    assert service_database.rows('SELECT 1 FROM chat_records WHERE session_id=%s', (actor.session_id,)) == []
