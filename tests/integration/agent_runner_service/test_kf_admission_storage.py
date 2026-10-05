"""Narrow original PG checkpoint ports, explicitly not an Engine producer.

Native receipt/API acceptance remains real. The initial execution/tree is a
declared contract fixture. SQL failure is a real trigger in the isolated DB.
"""
import copy
import uuid

import pytest

from .kf_admission_fixtures import text_scope, kf_scope
from .test_kf_admission_authority import rejection
from .test_worker import decoded, runner

pytestmark = pytest.mark.integration


def original_owned_checkpoint(t):
    from src.core.agent_engine.contracts import ExecutionState, Identity, AgentMode
    from src.services.agent_runner.execution_repository import ExecutionRepository
    from src.services.agent_runner.input_repository import InputRepository, input_message
    from src.services.agent_runner.ownership import Attempt
    locator = t.text('pg_initial_')
    accepted = t.accept(locator)
    execution = ExecutionRepository(t.scope.database.connect)
    execution.input_repository = InputRepository(t.scope.database.connect)
    owned = execution.acquire('native_input_pg_owner_' + t.scope.marker, 120)
    assert owned['runner_id'] == accepted['current_runner_id']
    authority = Attempt(owned['runner_id'], owned['worker_id'], owned['attempt'])
    state = ExecutionState(Identity(t.scope.tenant_id, None, t.scope.legacy_sid, 'wecom_kf', 'channel'),
        owned['runner_id'], AgentMode.MASTER, 'Fictional storage-only context',
        [input_message(t.facts()[0])], initial_len=1)
    checkpoint = {**decoded(owned['checkpoint']), 'execution': state.checkpoint()}
    saved = execution.save_checkpoint(authority, owned['revision'], checkpoint)
    return execution, authority, accepted, saved


def test_real_checkpoint_cas_and_sql_commit_failure_leave_input_refs_unattached_then_once_original_save(text_scope):
    from psycopg2 import sql
    from src.services.agent_runner.ownership import LeaseLost
    t, s = text_scope, text_scope.scope
    execution, authority, initial, saved = original_owned_checkpoint(t)
    locators = [t.text('pg_order_a_'), t.text('pg_order_b_')]
    accepted = [t.accept(locator) for locator in locators]
    assert all(value['current_runner_id'] == saved['runner_id'] for value in accepted)
    before, facts = runner(s.database, saved['runner_id']), t.facts()
    assert [fact['phase'] for fact in facts] == ['appended', 'accepted', 'accepted']
    with pytest.raises(LeaseLost, match='CHECKPOINT_REVISION_CHANGED'):
        execution.save_checkpoint(authority, saved['revision'] - 1, saved['checkpoint'])
    assert runner(s.database, saved['runner_id']) == before and t.facts() == facts
    name = 'text_commit_' + uuid.uuid4().hex
    with s.database.connect() as connection, connection.cursor() as cursor:
        cursor.execute(sql.SQL("CREATE FUNCTION {}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'FICTIONAL_SOURCE_CP_COMMIT_FAILURE'; END $$").format(sql.Identifier(name)))
        cursor.execute(sql.SQL('CREATE TRIGGER {} BEFORE UPDATE ON agent_runners FOR EACH ROW WHEN (OLD.runner_id={}) EXECUTE FUNCTION {}()').format(sql.Identifier(name), sql.Literal(saved['runner_id']), sql.Identifier(name)))
    try:
        with pytest.raises(Exception) as failure:
            execution.save_checkpoint(authority, saved['revision'], saved['checkpoint'])
        assert getattr(failure.value, 'pgcode', None) == 'P0001'
        assert runner(s.database, saved['runner_id']) == before
        assert t.facts() == facts  # Input attachment UPDATE precedes failing root UPDATE in the same TX.
    finally:
        with s.database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql.SQL('DROP TRIGGER {} ON agent_runners').format(sql.Identifier(name)))
            cursor.execute(sql.SQL('DROP FUNCTION {}()').format(sql.Identifier(name)))
    queued = execution.save_checkpoint(authority, saved['revision'], saved['checkpoint'])
    refs = [message['metadata']['input_ref'] for message in queued['checkpoint']['execution']['followup_messages']]
    assert refs == [a['input_ref'] for a in accepted]
    assert [fact['phase'] for fact in t.facts()] == ['appended', 'attached', 'attached']
    appended = execution.save_checkpoint(authority, queued['revision'], queued['checkpoint'], input_boundary='before_model')
    state = appended['checkpoint']['execution']
    assert [m['metadata']['input_ref'] for m in state['messages']] == [initial['input_ref'], *refs]
    assert state['followup_messages'] == []
    replay = execution.save_checkpoint(authority, appended['revision'], appended['checkpoint'], input_boundary='before_model')
    assert replay['checkpoint']['execution']['messages'] == state['messages']
    assert len(t.facts()) == 3 and [f['phase'] for f in t.facts()] == ['appended'] * 3
    # Known staged intent is a PG port fixture, not a completed model/record.
    # A later source receipt may queue a new root but cannot mutate that intent.
    result = {'status': 'completed', 'output': 'Storage-only immutable staged output', 'images': [], 'messages': [], 'error_code': None}
    staged = execution.stage_finalization(authority, replay['revision'], replay['checkpoint'], result, {})
    assert staged['status'] == 'finalizing' and staged['checkpoint']['pending_finalization'] == result
    late_locator = t.text('pg_after_cutoff_')
    late = t.accept(late_locator)
    assert late['accepted_runner_id'] == late['current_runner_id'] != staged['runner_id']
    assert runner(s.database, staged['runner_id'])['checkpoint'] == staged['checkpoint']
    queued_root = runner(s.database, late['current_runner_id'])
    assert queued_root['status'] == 'queued' and queued_root['attempt'] == 0
    assert execution.acquire('staged_input_contender_' + s.marker, 30) is None
    repeated_stage = execution.stage_finalization(authority, staged['revision'], staged['checkpoint'], result, {})
    assert repeated_stage['checkpoint'] == staged['checkpoint']
    assert s.rows('SELECT 1 FROM chat_records WHERE session_id=%s', (s.legacy_sid,)) == []
    assert s.rows('SELECT 1 FROM agent_runner_usage_receipts WHERE runner_id=%s', (saved['runner_id'],)) == []


def test_real_pending_input_cannot_requeue_on_unknown_failure_and_parked_child_wait_is_not_a_reply(text_scope):
    from src.core.agent_engine.contracts import ExecutionState, AgentMode
    from src.services.agent_runner.source_receipts import SourceUnavailable
    t, s = text_scope, text_scope.scope
    execution, authority, initial, saved = original_owned_checkpoint(t)
    second_locator = t.text('pg_failure_')
    second = t.accept(second_locator)
    original_facts = t.facts()
    result = {'status': 'failed', 'output': '', 'images': [], 'error_code': 'FICTIONAL_RUNTIME_UNKNOWN'}
    with pytest.raises(SourceUnavailable, match='SOURCE_INPUT_CUTOFF_UNRESOLVED'):
        execution.stage_finalization(authority, saved['revision'], saved['checkpoint'], result, {})
    assert runner(s.database, saved['runner_id'])['checkpoint'] == saved['checkpoint']
    assert t.facts() == original_facts and len(t.facts()) == 2
    assert s.rows('SELECT runner_id FROM agent_runners WHERE session_id=%s', (s.legacy_sid,)) == [{'runner_id': saved['runner_id']}]
    child = ExecutionState.restore(saved['checkpoint']['execution'])
    child.execution_id = 'storage_child_' + s.marker
    child.role = AgentMode.SUBAGENT
    child.messages = [{'role': 'user', 'content': 'Unanswered original child question'}]
    child.waiting = {'kind': 'clarification', 'question': 'Original question remains unanswered'}
    child.outcome = 'waiting'
    checkpoint = copy.deepcopy(saved['checkpoint'])
    checkpoint['execution']['children'] = {'original_child': {'phase': 'waiting', 'checkpoint': child.checkpoint()}}
    checkpoint['execution']['waiting'] = {'kind': 'clarification', 'execution_id': child.execution_id, 'question': 'Original question remains unanswered'}
    checkpoint['execution']['outcome'] = 'waiting'
    parked = execution.park(authority, saved['revision'], 'waiting', checkpoint, {'progress': 'Waiting for original child'})
    before = runner(s.database, saved['runner_id'])
    claim = s.rows('SELECT * FROM agent_runner_session_claims WHERE owner_runner_id=%s', (saved['runner_id'],))
    third_locator = t.text('held_child_')
    rejection(t.post(third_locator), 409, 'SOURCE_WAITING_INPUT_NOT_SUPPORTED')
    assert runner(s.database, saved['runner_id']) == before
    assert before['checkpoint'] == parked['checkpoint']
    assert before['checkpoint']['execution']['children'] == checkpoint['execution']['children']
    assert before['resume_control_id'] is None and before['attempt'] == saved['attempt']
    assert t.facts() == original_facts
    assert s.rows('SELECT * FROM agent_runner_session_claims WHERE owner_runner_id=%s', (saved['runner_id'],)) == claim
    assert s.rows('SELECT 1 FROM agent_runner_controls WHERE runner_id=%s', (saved['runner_id'],)) == []
    assert s.rows('SELECT accepted_input_ref FROM wecom_kf_inbox WHERE account_id=%s AND message_id=%s',
        (third_locator.account_id, third_locator.message_id)) == [{'accepted_input_ref': None}]
    assert s.rows('SELECT 1 FROM chat_records WHERE session_id=%s', (s.legacy_sid,)) == []
