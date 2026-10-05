"""Actual inbox consumer/HTTP/SDK filtering and finite keyset progression."""
import asyncio

import pytest

from .kf_admission_fixtures import text_scope, kf_scope
from .test_kf_admission_authority import rejection
from .kf_admission_peer import StateReply
from .kf_ingress_fixtures import text_message

pytestmark = pytest.mark.integration


def test_actual_consumer_skips_noncustomer_nontext_and_advances_past_held_state_without_autoanswering_lifecycle(text_scope):
    from src.channels.wecom_kf.admission_worker import KfAdmissionWorker
    from src.services.agent_runner.source_client import SourceClient
    from src.services.agent_runner.contracts import RunnerError
    t, s = text_scope, text_scope.scope
    human = text_message(s, 'human_origin_' + s.marker)
    human['origin'] = 5
    voice = {**text_message(s, 'voice_' + s.marker), 'msgtype': 'voice', 'voice': {'media_id': 'fictional_media'}}
    unsupported = {**text_message(s, 'emoji_' + s.marker), 'msgtype': 'emoji', 'emoji': {}}
    blocked = text_message(s, 'state_not1_' + s.marker)
    healthy = text_message(s, 'healthy_' + s.marker)
    locators = t.receive(human, voice, unsupported, blocked, healthy)
    for locator in locators[:3]:
        rejection(t.post(locator), 409, 'SOURCE_INPUT_UNSUPPORTED')
    assert not t.platform.calls and not t.facts()
    t.platform.states[:] = [StateReply({'errcode': 0, 'service_state': 3}), StateReply()]

    async def consume_actual():
        config = t.config.model_copy(deep=True)
        client = SourceClient(config, token=t.api._credential)
        consumer = KfAdmissionWorker(config, connection_factory=s.database.connect, client=client)
        try:
            config.wecom_kf.enabled = False
            assert await consumer.run_once() is None
            assert consumer.after == 0 and not consumer.tasks and not t.platform.calls
            config.wecom_kf.enabled = True
            with pytest.raises(RunnerError, match='SOURCE_INPUT_NOT_ACCEPTED'):
                await consumer.run_once()
            blocked_after = consumer.after
            assert blocked_after > 0 and not t.facts()
            accepted = await consumer.run_once()
            assert consumer.after > blocked_after
            assert await consumer.run_once() is None  # End reached; wraps on the next poll, no busy loop.
            assert consumer.after == 0
            return accepted
        finally:
            await consumer.close()
            assert not consumer.tasks
    accepted = asyncio.run(consume_actual())
    assert accepted['created'] is True and accepted['input_ref'] == locators[4].stable_key
    rows = s.rows('SELECT message_id,accepted_input_ref FROM wecom_kf_inbox WHERE config_id=%s ORDER BY receipt_order', (s.config_id,))
    assert [r['accepted_input_ref'] for r in rows] == [None, None, None, None, accepted['input_ref']]
    assert len(t.facts()) == 1 and len(t.platform.calls) == 2
    event = {**text_message(s, 'lifecycle_' + s.marker), 'msgtype': 'event',
        'event': {'event_type': 'session_status_change', 'change_type': 'session_status_change', 'session_status': 3}}
    _, lifecycle_text = t.receive(event, text_message(s, 'post_lifecycle_' + s.marker))
    t.platform.states.append(StateReply())
    before = t.facts()
    rejection(t.post(lifecycle_text), 409, 'SOURCE_LIFECYCLE_PENDING')
    assert t.facts() == before
    assert s.rows('SELECT accepted_input_ref FROM wecom_kf_inbox WHERE account_id=%s AND message_id=%s',
        (lifecycle_text.account_id, lifecycle_text.message_id)) == [{'accepted_input_ref': None}]
    assert s.rows('SELECT 1 FROM agent_runner_controls WHERE runner_id=%s', (accepted['current_runner_id'],)) == []
    assert not t.platform.errors and not s.peer.errors
