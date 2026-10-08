"""Context-only off/progress risks; never alter the sealed normal helper."""
import asyncio
import json
from datetime import datetime, timezone

import pytest

from .kf_context_fixtures import context_receipts, kf_scope, human_text, recall_event
from .kf_admission_peer import StateReply

pytestmark = pytest.mark.integration


def test_default_off_keeps_fixed_retry_boundary_and_bad_receipt_does_not_block_next_candidate(
        context_receipts):
    from src.channels.wecom_kf.context_repository import ContextRepository
    from src.channels.wecom_kf.context_worker import ContextWorker
    from src.channels.wecom_kf.lifecycle_repository import StateObservation, route_scope
    from src.channels.wecom_kf.ingress_auth import KfIngressError
    c, s = context_receipts, context_receipts.scope
    fixed, damaged, healthy = c.receive(
        human_text(s, 'fixed_off_' + s.marker, 'Fictional fixed human retry'),
        recall_event(s, 'damaged_context_' + s.marker, 'missing_target_' + s.marker),
        human_text(s, 'after_damaged_' + s.marker, 'Fictional healthy candidate progress'))
    repository = ContextRepository(s.database.connect)
    c.platform.states[:] = [StateReply({'errcode': 0, 'service_state': 3}),
        StateReply({'errcode': 0, 'service_state': 3})]

    async def first_real_classification():
        current, inbox, route = repository.read(fixed)
        client = c.original_client(current.proof.corp_id, current.secret)
        client.enable_ingress_mode(c.config.wecom_kf.page_bytes)
        try:
            response = await client.get_service_state(current.proof.open_kfid, inbox['actor_id'])
            observed = datetime.now(timezone.utc)
        finally:
            await client.close()
        assert response == {'errcode': 0, 'service_state': 3}
        return repository.classify(fixed, StateObservation(response['service_state'], observed,
            current.proof.config_version, inbox['payload_digest'], route_scope(route)))

    classification = asyncio.run(first_real_classification())
    assert classification['classification'] == 'human'
    original_damaged = c.receipt(damaged)
    original_healthy = c.receipt(healthy)
    disabled = c.config.wecom_kf.model_copy(deep=True)
    disabled.enabled = False

    async def off():
        worker = ContextWorker(disabled, repository=repository, client_factory=c.original_client)
        try:
            stored = await worker.run_once()  # immutable fixed human retry stays permissible
            assert stored['disposition'] == 'persisted'
            assert await worker.run_once() is None  # new unclassified receipt cannot dispatch
            assert await worker.run_once() is None
            return stored
        finally:
            await worker.close()
            assert not worker.tasks

    stored = asyncio.run(off())
    assert len(c.platform.calls) == 1 and not c.platform.errors
    assert c.receipt(damaged) == original_damaged and c.receipt(healthy) == original_healthy
    assert len(c.stored_history()) == 2
    assert s.rows('SELECT message_id FROM wecom_kf_receipt_classifications WHERE account_id=%s',
        (c.account['account_id'],)) == [{'message_id': fixed.message_id}]
    # Explicit own-inbox corruption DI. No fixture supplies successful auth or
    # a manufactured classification; the original reader must reject the digest.
    altered = {**original_damaged['payload'], 'event': {
        **original_damaged['payload']['event'], 'recall_msgid': 'different_target_' + s.marker}}
    try:
        s.rows('UPDATE wecom_kf_inbox SET payload=%s::jsonb WHERE account_id=%s AND namespace=%s AND message_id=%s',
            (json.dumps(altered), damaged.account_id, damaged.namespace, damaged.message_id))

        # Original candidate filtering excludes already consumed fixed history.
        assert repository.commit(fixed)['history_id'] == stored['history_id']

        async def progress():
            worker = ContextWorker(c.config.wecom_kf, repository=repository,
                client_factory=c.original_client)
            try:
                with pytest.raises(KfIngressError) as failure:
                    await worker.run_once()
                assert failure.value.code == 'KF_CONTEXT_BINDING_CHANGED'
                result = await worker.run_once()
                assert result['disposition'] == 'persisted'
                assert not worker.stopping and not worker.tasks
                return result
            finally:
                await worker.close()
        healthy_result = asyncio.run(progress())
        assert len(c.stored_history()) == 3 and healthy_result['history_id'] != stored['history_id']
        assert len(c.platform.calls) == 2 and not c.platform.errors
        assert s.rows('SELECT 1 FROM wecom_kf_receipt_classifications WHERE account_id=%s AND message_id=%s',
            (damaged.account_id, damaged.message_id)) == []
        assert s.rows('SELECT 1 FROM agent_runners WHERE session_id=%s', (s.legacy_sid,)) == []
        assert s.rows('SELECT 1 FROM chat_records WHERE session_id=%s', (s.legacy_sid,)) == []
    finally:
        s.rows('UPDATE wecom_kf_inbox SET payload=%s::jsonb,payload_digest=%s WHERE account_id=%s AND namespace=%s AND message_id=%s',
            (json.dumps(original_damaged['payload']), original_damaged['payload_digest'],
             damaged.account_id, damaged.namespace, damaged.message_id))
        assert c.receipt(damaged) == original_damaged
