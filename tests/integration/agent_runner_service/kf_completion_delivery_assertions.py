"""Prepared normal delivery tail using actual SourceClient public/proof APIs.

The original domain delivery owner supplies delivery_id and makes the real SDK
POST. These assertions never manufacture a presentation, wire fact or boolean
permission; they verify service reads after that actual operation.
"""
import asyncio


async def deliver_original_terminal(completion, api, locator, identifier):
    """Real domain owner -> original SDK POST -> service-owned proof close."""
    from src.channels.wecom_kf.delivery import KfDeliveryWorker
    from src.services.agent_runner.source_client import SourceClient
    client = SourceClient(completion.config, token=api._credential)
    worker = KfDeliveryWorker(completion.config, client,
        completion.scope.database.connect, adapter_factory=completion.original_adapter)
    before = completion.platform.count('/cgi-bin/kf/send_msg')
    try:
        result = await worker.observe(locator)
        assert result['success'] is True
        assert result['outcome'] == 'closed_accepted_known'
        delivery_id = result['delivery_id']
        assert completion.platform.count('/cgi-bin/kf/send_msg') == before + 1
        repeat = await worker.observe(locator)
        assert repeat == result
        assert completion.platform.count('/cgi-bin/kf/send_msg') == before + 1
        rows = await asyncio.to_thread(completion.scope.rows,
            'SELECT input_ref,runner_id,phase,closed_outcome FROM wecom_kf_deliveries WHERE delivery_id=%s',
            (delivery_id,))
        assert rows == [{'input_ref': locator.stable_key, 'runner_id': identifier,
            'phase': 'closed', 'closed_outcome': 'closed_accepted_known'}]
        wires = await asyncio.to_thread(completion.scope.rows,
            'SELECT phase,response_origin,errcode,purpose FROM wecom_kf_wire_operations '
            'WHERE delivery_id=%s ORDER BY ordinal', (delivery_id,))
        assert wires == [{'phase': 'ack', 'response_origin': 'platform',
            'errcode': 0, 'purpose': 'body'}]
        assert not worker.tasks
        return delivery_id
    finally:
        await worker.close()
        await client.close()


async def read_original_presentation(completion, api, locator, expected_runner_id,
                                     expected_output):
    from src.services.agent_runner.source_client import SourceClient
    client = SourceClient(completion.config, token=api._credential)
    try:
        observation = await client.read(locator, presentation=True)
    finally:
        await client.close()
    assert observation['locator'] == locator.value()
    assert observation['input_ref'] == locator.stable_key
    assert observation['current_runner_id'] == expected_runner_id
    assert observation['runner']['runner_id'] == expected_runner_id
    assert observation['runner']['status'] == 'completed'
    assert observation['runner']['result']['output'] == expected_output
    assert observation['runner']['session']['session_id'] == completion.scope.legacy_sid
    return observation


def finish_original_delivery_and_assert_no_fee_or_history_replay(completion, api,
        locator, delivery_id, identifier, *, expected_outcome):
    from src.services.agent_runner.source_client import SourceClient
    s = completion.scope
    fees = s.rows('SELECT receipt_id,phase,applied,record_id FROM agent_runner_usage_receipts '
        'WHERE runner_id=%s ORDER BY receipt_id', (identifier,))
    records = s.rows('SELECT record_id,total_token_count,credit_cost FROM chat_records '
        'WHERE record_id IN (SELECT record_id FROM agent_runner_usage_receipts WHERE runner_id=%s) '
        'ORDER BY record_id', (identifier,))
    history = s.rows('SELECT message_id,role,content,metadata FROM channel_messages '
        'WHERE tenant_id=%s AND session_id=%s ORDER BY id', (s.tenant_id, s.legacy_sid))
    balance = s.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s', (s.tenant_id,))
    async def close_twice():
        client = SourceClient(completion.config, token=api._credential)
        try:
            first = await client.finish_delivery(locator, delivery_id)
            duplicate = await client.finish_delivery(locator, delivery_id)
            return first, duplicate
        finally:
            await client.close()
    first, duplicate = asyncio.run(close_twice())
    for result in (first, duplicate):
        assert result['success'] is True and result['delivery_id'] == delivery_id
        assert result['outcome'] == expected_outcome
    assert s.rows('SELECT 1 FROM agent_runner_session_claims WHERE owner_runner_id=%s', (identifier,)) == []
    assert s.rows('SELECT receipt_id,phase,applied,record_id FROM agent_runner_usage_receipts '
        'WHERE runner_id=%s ORDER BY receipt_id', (identifier,)) == fees
    assert s.rows('SELECT record_id,total_token_count,credit_cost FROM chat_records '
        'WHERE record_id IN (SELECT record_id FROM agent_runner_usage_receipts WHERE runner_id=%s) '
        'ORDER BY record_id', (identifier,)) == records
    assert s.rows('SELECT message_id,role,content,metadata FROM channel_messages '
        'WHERE tenant_id=%s AND session_id=%s ORDER BY id', (s.tenant_id, s.legacy_sid)) == history
    assert s.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s', (s.tenant_id,)) == balance
    delivery = s.rows('SELECT phase,closed_outcome FROM wecom_kf_deliveries WHERE delivery_id=%s', (delivery_id,))
    assert delivery == [{'phase': 'closed', 'closed_outcome': expected_outcome}]
    wires = s.rows('SELECT phase,response_origin,errcode,purpose FROM wecom_kf_wire_operations '
        'WHERE delivery_id=%s ORDER BY ordinal', (delivery_id,))
    assert wires and all(row['phase'] in {'ack', 'reject', 'suppressed'} for row in wires)
    return first
