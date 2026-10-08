"""Bounded calls to the original admission orchestration, localhost IO only.

No candidate, classification, ownership, wire decision or acceptance method is
replaced. The factories construct original SDK/adapter instances and change
only their external HTTP address. Final execution awaits the source freeze.
"""
import asyncio


def original_admission(completion, api):
    from src.channels.wecom_kf.admission_worker import KfAdmissionWorker
    from src.services.agent_runner.source_client import SourceClient
    client = SourceClient(completion.config, token=api._credential)
    return KfAdmissionWorker(completion.config, completion.scope.database.connect,
        client=client, client_factory=completion.original_client,
        adapter_factory=completion.original_adapter)


async def accept_through_original_admission(completion, worker, locators):
    expected = [locator.stable_key for locator in locators]
    for _ in range(16):
        accepted = await worker.run_once()
        if accepted is not None:
            assert accepted['success'] is True
            assert [member['input_ref'] for member in accepted['members']] == expected
            assert all(member['current_runner_id'] == accepted['current_runner_id']
                for member in accepted['members'])
            assert accepted['created'] is True
            return accepted
        await asyncio.sleep(completion.config.wecom_kf.poll_seconds)
    raise AssertionError('OWN_ORIGINAL_ADMISSION_DID_NOT_ACCEPT_BATCH')


async def deliver_through_original_admission(completion, worker, locator, identifier):
    for _ in range(16):
        # Any acceptance here would create a task the fixture did not submit.
        assert await worker.run_once() is None
        observation = await worker.client.read(locator, presentation=True)
        view = observation['runner']
        assert view['runner_id'] == identifier
        assert view['status'] in {'completed', 'failed', 'cancelled'}
        deliveries = await asyncio.to_thread(completion.scope.rows,
            "SELECT delivery_id,phase,closed_outcome,input_ref,locator,scope,payload_digest,presentation "
            "FROM wecom_kf_deliveries WHERE runner_id=%s AND view_revision=%s AND control_revision=%s "
            "AND presentation->>'execution_status'=%s", (identifier, view['view_revision'],
                view['control_revision'], view['status']))
        if deliveries:
            # Earlier question presentations are legitimate; only the exact
            # current authoritative terminal presentation must be unique.
            assert len(deliveries) == 1
            final = deliveries[0]
            assert final['presentation']['output'] == view['result']['output']
            if final['phase'] != 'closed':
                await asyncio.sleep(completion.config.wecom_kf.poll_seconds)
                continue
            assert final['closed_outcome'] == 'closed_accepted_known'
            original = final['locator']
            binding = await asyncio.to_thread(completion.scope.rows,
                "SELECT i.accepted_input_ref,i.payload_digest,i.tenant_id AS receipt_tenant_id,r.*,p.current_runner_id "
                "FROM wecom_kf_inbox i JOIN channel_session_routes r USING(route_id) "
                "JOIN agent_runner_inputs p ON p.input_ref=i.accepted_input_ref "
                "WHERE i.account_id=%s AND i.namespace=%s AND i.message_id=%s",
                (original['account_id'], original['namespace'], original['message_id']))
            assert len(binding) == 1
            bound = binding[0]
            assert original['source'] == bound['source'] == 'wecom_kf'
            assert bound['accepted_input_ref'] == final['input_ref']
            assert bound['current_runner_id'] == identifier
            assert bound['payload_digest'] == final['payload_digest']
            keys = ('tenant_id', 'source', 'config_id', 'corp_id', 'open_kfid',
                'actor_id', 'chat_kind', 'chat_id', 'profile_id', 'session_id', 'user_id', 'route_id')
            assert final['scope'] == {key: bound[key] for key in keys}
            assert bound['receipt_tenant_id'] == bound['tenant_id'] == completion.scope.tenant_id
            assert bound['actor_id'] == completion.scope.actor_id
            assert bound['session_id'] == completion.scope.legacy_sid == view['session']['session_id']
            assert bound['profile_id'] == view['profile_id']
            wires = await asyncio.to_thread(completion.scope.rows,
                'SELECT phase,response_origin,errcode,purpose FROM wecom_kf_wire_operations '
                "WHERE delivery_id=%s AND purpose='body' ORDER BY ordinal", (final['delivery_id'],))
            assert wires == [{'phase': 'ack', 'response_origin': 'platform',
                'errcode': 0, 'purpose': 'body'}]
            return final['delivery_id']
        await asyncio.sleep(completion.config.wecom_kf.poll_seconds)
    raise AssertionError('OWN_ORIGINAL_ADMISSION_DID_NOT_CLOSE_DELIVERY')
