"""Full binding scoped recall through original pull, SDK and domain cursor."""
import asyncio
import json
from datetime import datetime, timezone

import pytest

from .kf_context_fixtures import context_receipts, kf_scope, human_text, recall_event
from .kf_context_scope import secondary_context_scope
from .kf_admission_peer import StateReply

pytestmark = pytest.mark.integration


def test_null_user_empty_profile_shared_sid_recall_before_and_after_target_stays_in_full_route(
        context_receipts):
    from src.channels.wecom_kf.context_repository import ContextRepository
    from src.channels.wecom_kf.lifecycle_repository import StateObservation, route_scope
    from src.channels.wecom_kf.ingress_auth import KfIngressError
    c, s = context_receipts, context_receipts.scope
    repository = ContextRepository(s.database.connect)
    target_id = 'shared_context_target_' + s.marker
    before_event = c.receive(recall_event(s, 'before_recall_' + s.marker, target_id))[0]
    early = repository.commit(before_event)
    assert early['disposition'] == 'pending_target'
    c.platform.states[:] = [StateReply({'errcode':0,'service_state':3}) for _ in range(3)]

    def classify_and_project(scope, locator, *, project=True):
        async def observe():
            current, inbox, route = repository.read(locator)
            client = scope.original_client(current.proof.corp_id, current.secret)
            client.enable_ingress_mode(scope.config.wecom_kf.page_bytes)
            try:
                value = await client.get_service_state(current.proof.open_kfid, inbox['actor_id'])
                observed_at = datetime.now(timezone.utc)
            finally:
                await client.close()
            assert value == {'errcode':0,'service_state':3}
            return StateObservation(3,observed_at,current.proof.config_version,
                inbox['payload_digest'],route_scope(route))
        classified=repository.classify(locator,asyncio.run(observe()))
        return repository.commit(locator) if project else classified

    with secondary_context_scope(c) as other:
        second_target = other.receive(human_text(other.scope,target_id,'Fictional other scoped target'))[0]
        second = classify_and_project(other,second_target)
        first_target = c.receive(human_text(s,target_id,'Fictional recalled target'))[0]
        first_class=classify_and_project(c,first_target,project=False)
        assert first_class['classification']=='human'
        assert all((row['metadata'] or {}).get('msgid')!=first_target.message_id
            or (row['metadata'] or {}).get('account_id')!=first_target.account_id for row in c.stored_history())
        # Target inbox/classification exist, but no stable history row exists.
        # This genuine pre-history recall is allowed, not a false wrong-scope row.
        assert repository.commit(before_event)['disposition']=='recalled'
        first=repository.commit(first_target)
        assert first['history_id'] != second['history_id']
        routes = s.rows('SELECT * FROM channel_session_routes WHERE config_id=ANY(%s)',
            ([s.config_id,other.scope.config_id],))
        assert len(routes)==2 and len({row['route_id'] for row in routes})==2
        assert all(row['session_id']==s.legacy_sid and row['user_id'] is None
            and row['raw_profile']=='' and row['profile_id']=='main' and row['legacy_shared'] for row in routes)
        stored = {row['message_id']:row for row in c.stored_history()}
        assert stored[first['history_id']]['is_recalled'] is True
        assert stored[second['history_id']]['is_recalled'] is False
        assert stored[first['history_id']]['metadata']['account_id']==first_target.account_id
        assert stored[second['history_id']]['metadata']['account_id']==second_target.account_id
        assert repository.commit(before_event)['disposition']=='recalled'
        later_target = c.receive(human_text(s,'after_target_'+s.marker,'Fictional target recalled later'))[0]
        later = classify_and_project(c,later_target)
        event = c.receive(recall_event(s,'after_recall_'+s.marker,later_target.message_id))[0]
        assert repository.commit(event)['disposition']=='recalled'
        stored = {row['message_id']:row for row in c.stored_history()}
        assert stored[later['history_id']]['is_recalled'] is True
        assert stored[second['history_id']]['is_recalled'] is False
        wrong_recall=c.receive(recall_event(s,'wrong_history_scope_'+s.marker,later_target.message_id))[0]
        original_metadata=stored[later['history_id']]['metadata']
        try:
            s.rows('UPDATE channel_messages SET metadata=%s::jsonb WHERE tenant_id=%s AND session_id=%s AND message_id=%s',
                (json.dumps({**original_metadata,'corp_id':'different_corp_scope'}),
                 s.tenant_id,s.legacy_sid,later['history_id']))
            mismatched=c.stored_history()
            with pytest.raises(KfIngressError) as wrong_scope:
                repository.commit(wrong_recall)
            assert wrong_scope.value.code=='KF_CONTEXT_RECALL_HISTORY_CONFLICT'
            assert c.stored_history()==mismatched
            assert s.rows('SELECT 1 FROM wecom_kf_context_consumptions WHERE account_id=%s AND message_id=%s',
                (wrong_recall.account_id,wrong_recall.message_id))==[]
        finally:
            s.rows('UPDATE channel_messages SET metadata=%s::jsonb WHERE tenant_id=%s AND session_id=%s AND message_id=%s',
                (json.dumps(original_metadata),s.tenant_id,s.legacy_sid,later['history_id']))
        # An actual competing config version invalidates the original route;
        # no SID-only history update may occur under that invalid binding.
        original = other.stored_config()
        snapshot = c.stored_history()
        try:
            s.rows("UPDATE channel_session_routes SET actor_id=%s WHERE route_id=%s",
                ('different_full_actor',c.receipt(event)['route_id']))
            with pytest.raises(KfIngressError) as rejected:
                repository.commit(event)
            assert rejected.value.code in {'KF_INGRESS_ROUTE_INVALID'}
            assert c.stored_history()==snapshot
        finally:
            s.rows('UPDATE channel_session_routes SET actor_id=%s WHERE route_id=%s',
                (s.actor_id,c.receipt(event)['route_id']))
        assert other.stored_config()==original
        assert s.rows('SELECT 1 FROM agent_runners WHERE session_id=%s',(s.legacy_sid,))==[]
        assert len(c.platform.calls)==3 and not c.platform.errors
        assert all('[人工客服]' not in row['content'] for row in c.runtime_history())
