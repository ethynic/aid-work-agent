"""Real malformed provider ACK cannot authorize retry or release ownership."""
import pytest

from .domain_fixtures import recruiting_domain
from .domain_io import WebhookPeer, WebhookReply
from .test_local_notify_flow import done_input, configure_notify, notify_request
from .test_worker import workers, api_pair, prices, accept, runner, decoded
from .test_worker_recovery import control

pytestmark = pytest.mark.integration


def test_actual_unknown_http_ack_survives_disabled_configuration_and_rejects_replay(
        workers, recruiting_domain, service_database):
    from src.services.recruiting_notify_service import upsert_settings
    actor, profile = recruiting_domain
    with WebhookPeer(WebhookReply(payload={'errmsg': 'fictional missing acknowledgement'})) as peer:
        configure_notify(actor, peer)
        marker = notify_request(workers, done_input())
        accepted = accept(workers.api, actor, marker, profile_id=profile)
        first, _ = workers.start()
        workers.assert_clean_exit(first)
        stopped = runner(service_database, accepted['runner_id'])
        assert stopped['status'] == 'waiting'
        before_execution = decoded(stopped['checkpoint'])['execution']
        assert len(peer.requests) == 1 and not peer.errors
        assert service_database.rows('SELECT 1 FROM bs_recruiting_notify_logs WHERE tenant_id=%s', (actor.tenant_id,)) == []
        assert service_database.rows('SELECT owner_runner_id FROM agent_runner_session_claims WHERE owner_runner_id=%s', (accepted['runner_id'],)) == [{'owner_runner_id': accepted['runner_id']}]
        # Changing current configuration cannot hide an already started,
        # physically unknown POST behind a harmless disabled result.
        upsert_settings(actor.tenant_id, enabled=False)
        submitted, _ = control(workers.api, actor, accepted['runner_id'], 'resume')
        second, _ = workers.start()
        workers.assert_clean_exit(second)
        rejected = service_database.rows('SELECT status,consumed_attempt FROM agent_runner_controls WHERE control_id=%s',
                                         (submitted['control']['control_id'],))
        assert rejected == [{'status': 'rejected', 'consumed_attempt': None}]
        after = runner(service_database, accepted['runner_id'])
        assert after['status'] == 'waiting' and after['attempt'] == stopped['attempt']
        assert decoded(after['checkpoint'])['execution'] == before_execution
        assert len(peer.requests) == 1 and not peer.errors
    assert len(workers.provider.requests(marker)) == 1 and not workers.provider.errors
    assert service_database.rows('SELECT 1 FROM bs_recruiting_notify_logs WHERE tenant_id=%s', (actor.tenant_id,)) == []
    assert service_database.rows('SELECT 1 FROM client_usage_logs WHERE tenant_id=%s', (actor.tenant_id,)) == []
    assert service_database.rows('SELECT 1 FROM chat_records WHERE session_id=%s', (actor.session_id,)) == []
