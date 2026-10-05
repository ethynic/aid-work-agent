"""Real terminal owner + original SDK send facts, localhost platform boundary."""
import asyncio
from decimal import Decimal
import threading

import pytest

from .kf_completion_terminal import completion_terminal, completion_scope, kf_scope, completion_profile_price
from .kf_completion_peer import CompletionWireReply
from .kf_completion_delivery_assertions import finish_original_delivery_and_assert_no_fee_or_history_replay
from .kf_completion_wire_observation import ObserveOriginalWireOwner
from .kf_completion_fixtures import fresh_customer_text
from .kf_completion_orchestration import original_admission, accept_through_original_admission
from .kf_completion_batch_assertions import run_original_batch_to_terminal
from .provider import Reply
from .test_usage_storage import prices

pytestmark = pytest.mark.integration


def test_actual_lost_customer_send_response_preserves_unknown_and_same_actor_next_chat_continues(
        completion_terminal):
    from src.channels.wecom_kf.delivery import KfDeliveryWorker
    from src.services.agent_runner.source_client import SourceClient
    from .kf_completion_orchestration import deliver_through_original_admission
    t, c = completion_terminal, completion_terminal.completion
    s = c.scope
    before = t.immutable_execution_facts()
    c.platform.script('/cgi-bin/kf/send_msg', CompletionWireReply(drop_response=True))

    async def actual_loss():
        client = SourceClient(c.config, token=t.api._credential)
        worker = KfDeliveryWorker(c.config, client, s.database.connect,
            adapter_factory=c.original_adapter)
        try:
            # Unknown transport is durably recorded and drained; this return
            # does not claim that the customer received the message.
            await worker.observe(t.locators[0])
            assert not worker.tasks
        finally:
            await worker.close()
            await client.close()
    asyncio.run(actual_loss())
    assert c.platform.count('/cgi-bin/kf/send_msg') == 1 and not c.platform.errors
    deliveries = s.rows('SELECT delivery_id FROM wecom_kf_deliveries WHERE runner_id=%s',
        (t.identifier,))
    assert len(deliveries) == 1
    old_delivery_id = deliveries[0]['delivery_id']
    old_wire = s.rows('SELECT operation_ref,phase,response_origin,errcode FROM wecom_kf_wire_operations '
        'WHERE delivery_id=%s', (old_delivery_id,))
    assert len(old_wire) == 1
    assert {key: old_wire[0][key] for key in ('phase', 'response_origin', 'errcode')} == {
        'phase': 'unknown', 'response_origin': 'local', 'errcode': None}
    assert t.immutable_execution_facts() == before
    assert len(t.provider.requests(t.marker)) == 1
    assert s.rows('SELECT 1 FROM wecom_kf_context_task_intents WHERE account_id=%s',
        (c.account['account_id'],)) == []

    # HEAD send_response finally released conversation processing even when
    # delivery failed. The same real actor/SID must accept the next chat;
    # the old uncertain operation remains unknown and is never posted again.
    c.platform.script('/cgi-bin/kf/customer/batchget', *(CompletionWireReply(payload={
        'errcode': 0, 'customer_list': [{'external_userid': s.actor_id,
            'nickname': 'Fictional isolated customer'}]}) for _ in range(4)))
    c.platform.script('/cgi-bin/kf/service_state/get', *(CompletionWireReply(payload={
        'errcode': 0, 'service_state': 1}) for _ in range(64)))
    c.platform.script('/cgi-bin/kf/send_msg', CompletionWireReply())
    admission = original_admission(c, t.api)
    later_reply = Reply(content='Fictional same-conversation next result', release=threading.Event())
    try:
        assert asyncio.run(admission.run_once()) is None
        assert c.platform.count('/cgi-bin/kf/send_msg') == 1
        marker = t.provider.register(later_reply)
        texts = [marker, 'Fictional same-conversation adjacent segment']
        locators = c.receive(*(fresh_customer_text(s,
            'same_route_' + str(index) + '_' + s.marker, text)
            for index, text in enumerate(texts)))
        routes = s.rows('SELECT r.session_id,r.actor_id,r.user_id FROM wecom_kf_inbox i '
            'JOIN channel_session_routes r USING(route_id) WHERE i.account_id=%s AND i.message_id=ANY(%s) '
            'ORDER BY i.receive_seq', (c.account['account_id'], [value.message_id for value in locators]))
        assert len(routes) == 2 and routes[0] == routes[1]
        assert routes[0] == {'actor_id': s.actor_id, 'user_id': None, 'session_id': s.legacy_sid}
        snapshots = s.rows('SELECT message_id,payload,payload_digest,accepted_input_ref FROM wecom_kf_inbox '
            'WHERE account_id=%s AND message_id=ANY(%s) ORDER BY receive_seq',
            (c.account['account_id'], [value.message_id for value in locators]))
        accepted = asyncio.run(accept_through_original_admission(c, admission, locators))
        assert accepted['current_runner_id'] != t.identifier
        _, finished, _ = run_original_batch_to_terminal(c, t.api, t.fleet,
            t.provider, locators, texts, later_reply, marker,
            accepted_response=accepted, receipt_snapshots=snapshots)
        assert finished['status'] == 'completed' and len(t.provider.requests(marker)) == 1
        asyncio.run(deliver_through_original_admission(c, admission, locators[0], accepted['current_runner_id']))
        assert c.platform.count('/cgi-bin/kf/send_msg') == 2
        assert s.rows('SELECT operation_ref,phase,response_origin,errcode FROM wecom_kf_wire_operations '
            'WHERE delivery_id=%s', (old_delivery_id,)) == old_wire
        assert s.rows('SELECT 1 FROM wecom_kf_context_task_intents WHERE account_id=%s',
            (c.account['account_id'],)) == []
        after = t.immutable_execution_facts()
        for key in ('runner', 'receipts', 'record', 'inputs'):
            assert after[key] == before[key]
        old_history_ids = {row['message_id'] for row in before['history']}
        assert [row for row in after['history'] if row['message_id'] in old_history_ids] == before['history']
        assert after['balance'][0]['credit_balance'] == before['balance'][0]['credit_balance'] - Decimal('0.01')
        assert not admission.tasks and not c.platform.errors
    finally:
        later_reply.release.set()
        asyncio.run(admission.close())


@pytest.mark.parametrize('completion_scope', ['pre-sales'], indirect=True)
@pytest.mark.parametrize('completion_terminal', ['file_asset'], indirect=True)
def test_actual_explicit_platform_reject_finishes_known_failure_without_successful_recap_or_fee_replay(
        completion_terminal):
    from src.channels.wecom_kf.delivery import KfDeliveryWorker
    from src.services.agent_runner.source_client import SourceClient
    t, c = completion_terminal, completion_terminal.completion
    before = t.immutable_execution_facts()
    # 原 adapter 发送任何可下载文件前先取默认缩略图（adapter.
    # _get_default_thumb_media_id），真实环境该 media_id 有 1 小时缓存且常热；
    # 预热缓存使文件按原路径走 link 卡片回发，否则会先发起本套件假平台
    # 不提供的 /cgi-bin/media/upload 并以 unknown 中断。
    from src.core.redis_client import redis_client
    redis_client.set(redis_client.make_key('wecom_kf',
        f"default_thumb_media_id:{c.scope.corp_id}"), 'fictional_default_thumb', ex=3500)
    # Actual cp/Runtime publishes a required file. Body ACK cannot substitute
    # for its later explicit file-link reject or justify AI-success recap.
    c.platform.script('/cgi-bin/kf/send_msg', CompletionWireReply(payload={'errcode': 0}),
        CompletionWireReply(payload={'errcode': 45015}))

    async def known_reject():
        client = SourceClient(c.config, token=t.api._credential)
        worker = KfDeliveryWorker(c.config, client, c.scope.database.connect,
            adapter_factory=c.original_adapter)
        try:
            result = await worker.observe(t.locators[0])
            assert result['success'] is True and result['outcome'] == 'closed_failed_known'
            assert await worker.observe(t.locators[0]) == result
            return result
        finally:
            await worker.close()
            await client.close()
    result = asyncio.run(known_reject())
    finish_original_delivery_and_assert_no_fee_or_history_replay(c, t.api, t.locators[0],
        result['delivery_id'], t.identifier, expected_outcome='closed_failed_known')
    assert c.platform.count('/cgi-bin/kf/send_msg') == 2 and not c.platform.errors
    assert c.scope.rows('SELECT phase,response_origin,errcode,purpose FROM wecom_kf_wire_operations '
        'WHERE delivery_id=%s ORDER BY ordinal', (result['delivery_id'],)) == [
            {'phase': 'ack', 'response_origin': 'platform', 'errcode': 0, 'purpose': 'body'},
            {'phase': 'reject', 'response_origin': 'platform', 'errcode': 45015, 'purpose': 'asset'}]
    assert c.scope.rows('SELECT 1 FROM wecom_kf_context_task_intents WHERE account_id=%s',
        (c.account['account_id'],)) == []
    assert t.immutable_execution_facts() == before


def test_actual_send_ack_sql_rollback_reuses_only_original_parsed_tuple_without_second_post(
        completion_terminal):
    from psycopg2 import DatabaseError, sql
    from src.channels.wecom_kf.delivery import KfDeliveryWorker
    from src.channels.wecom_kf.delivery_repository import DeliveryRepository
    from src.services.agent_runner.source_client import SourceClient
    t, c = completion_terminal, completion_terminal.completion
    s = c.scope
    before = t.immutable_execution_facts()
    c.platform.script('/cgi-bin/kf/send_msg', CompletionWireReply(payload={'errcode': 0, 'msgid': 'fictional_sdk_ack'}))
    name = 'completion_wire_fault_' + s.marker
    observations = []

    def observing_original_adapter(corp, secret):
        adapter = c.original_adapter(corp, secret)
        original_enable = adapter.enable_native_delivery

        def enable(owner, *, open_kfid, actor_id):
            observed = ObserveOriginalWireOwner(owner)
            observations.append(observed)
            return original_enable(observed, open_kfid=open_kfid, actor_id=actor_id)
        adapter.enable_native_delivery = enable  # Original domain decision/result unchanged.
        return adapter

    s.rows(sql.SQL('CREATE FUNCTION {}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN '
        "IF NEW.tenant_id={} AND NEW.phase='ack' THEN RAISE EXCEPTION 'OWN_WIRE_ACK_STORAGE_FAILURE'; END IF; "
        'RETURN NEW; END $$').format(sql.Identifier(name), sql.Literal(s.tenant_id)))
    s.rows(sql.SQL('CREATE TRIGGER {} BEFORE UPDATE ON wecom_kf_wire_operations '
        'FOR EACH ROW EXECUTE FUNCTION {}()').format(sql.Identifier(name), sql.Identifier(name)))

    async def actual_failed_ack_and_retained_original_fact():
        client = SourceClient(c.config, token=t.api._credential)
        worker = KfDeliveryWorker(c.config, client, s.database.connect,
            adapter_factory=observing_original_adapter)
        try:
            with pytest.raises(DatabaseError) as rejected:
                await worker.observe(t.locators[0])
            assert rejected.value.pgcode == 'P0001'
            assert len(observations) == 1 and len(observations[0]._actual_results) == 1
            operation, actual_result = observations[0]._actual_results[0]
            assert actual_result == {'errcode': 0, 'msgid': 'fictional_sdk_ack', 'response_origin': 'platform'}
            unknown = await asyncio.to_thread(s.rows,
                'SELECT phase,response_origin,errcode FROM wecom_kf_wire_operations WHERE operation_ref=%s',
                (operation['operation_ref'],))
            assert unknown == [{'phase': 'unknown', 'response_origin': 'local', 'errcode': None}]
            assert c.platform.count('/cgi-bin/kf/send_msg') == 1
            assert await asyncio.to_thread(t.immutable_execution_facts) == before
            await asyncio.to_thread(s.rows, sql.SQL('DROP TRIGGER {} ON wecom_kf_wire_operations').format(sql.Identifier(name)))
            await asyncio.to_thread(s.rows, sql.SQL('DROP FUNCTION {}()').format(sql.Identifier(name)))
            # Explicit retained observer tuple redelivery, not fabricated ACK,
            # automatic HTTP retry or an implied postcrash memory recovery.
            repository = DeliveryRepository(s.database.connect)
            first = await asyncio.to_thread(repository.observe, operation, result=actual_result)
            duplicate = await asyncio.to_thread(repository.observe, operation, result=actual_result)
            assert first == duplicate and first['phase'] == 'ack'
            result = await worker.observe(t.locators[0])  # Same genuine owner replays durable ACK.
            assert result['outcome'] == 'closed_accepted_known'
            assert c.platform.count('/cgi-bin/kf/send_msg') == 1
            assert await asyncio.to_thread(t.immutable_execution_facts) == before
            return result
        finally:
            await worker.close()
            await client.close()
    try:
        result = asyncio.run(actual_failed_ack_and_retained_original_fact())
        finish_original_delivery_and_assert_no_fee_or_history_replay(c, t.api, t.locators[0],
            result['delivery_id'], t.identifier, expected_outcome='closed_accepted_known')
        assert not c.platform.errors
    finally:
        s.rows(sql.SQL('DROP TRIGGER IF EXISTS {} ON wecom_kf_wire_operations').format(sql.Identifier(name)))
        s.rows(sql.SQL('DROP FUNCTION IF EXISTS {}()').format(sql.Identifier(name)))
