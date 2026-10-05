"""Actual paid summary receipt failure after the initial preparation marker.

Only original configuration parameters bound the history threshold/tail. Real
summary HTTP and original receipt UPDATE fail through a real PG trigger; no
synthetic StorageFailure, Compression, Engine or Usage result is installed.
"""
from pathlib import Path
import secrets
import threading

import pytest

from .conftest import wait_for
from .kf_ingress_fixtures import kf_scope
from .kf_voice_fixtures import voice_scope, voice_price
from .kf_voice_peer import AsrReply
from .kf_admission_service import verify_original_resources
from .provider import Reply
from .test_usage_storage import prices
from .test_worker import Workers, runner, decoded
from .test_kf_voice_results import start_voice_worker, post_count, safe_voice_error

pytestmark = pytest.mark.integration


def test_actual_original_summary_post_receipt_sql_failure_after_marker_clear_rejects_empty_engine_resume_without_paid_replay(
        voice_scope, service_processes, provider_peer, prices, voice_price):
    from psycopg2 import sql
    from src.memory.mid_term import count_text_tokens
    v, s = voice_scope, voice_scope.scope
    release = threading.Event()
    summary_reply = Reply(content='Known original summary response', release=release)
    summary_marker = provider_peer.register(summary_reply)
    model_marker = provider_peer.register(Reply(content='Must never reach original business model'))
    transcript = model_marker + ' exact original incoming voice'
    v.peer.replies.append(AsrReply(payload={'status': 20000000, 'result': transcript}))
    locator, _ = v.receive_voice()
    accepted = v.text.accept(locator)
    identifier, input_ref = accepted['current_runner_id'], accepted['input_ref']
    fleet = Workers(service_processes, v.api, provider_peer, prices)
    trigger = 'voice_summary_usage_' + secrets.token_hex(12)
    installed = False
    # Paired, owned, fictional original history. The economy gate is satisfied
    # by actual text token counting, not by a patched clock/token function.
    long_history = summary_marker + ' ' + ('compression_evidence ' * 30000)
    assert count_text_tokens(long_history) >= 40000
    with s.database.connect() as connection, connection.cursor() as cursor:
        for i, (role, content) in enumerate([
            ('user', long_history), ('assistant', 'Known fictional old answer'),
            ('user', 'Another old question'), ('assistant', 'Another old answer'),
            ('user', 'Recent historical question'), ('assistant', 'Recent historical answer')]):
            cursor.execute('''INSERT INTO channel_messages(message_id,session_id,tenant_id,role,content)
                VALUES(%s,%s,%s,%s,%s)''',
                ('voice_summary_history_' + secrets.token_hex(12), s.legacy_sid, s.tenant_id, role, content))

    def drop_trigger():
        nonlocal installed
        if installed:
            with s.database.connect() as connection, connection.cursor() as cursor:
                cursor.execute(sql.SQL('DROP TRIGGER {} ON agent_runner_usage_receipts').format(sql.Identifier(trigger)))
                cursor.execute(sql.SQL('DROP FUNCTION {}()').format(sql.Identifier(trigger)))
            installed = False

    try:
        verify_original_resources(fleet, v.api)
        with s.database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE FUNCTION {}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'FICTIONAL_SUMMARY_RECEIPT_SQL_FAILURE'; END $$").format(sql.Identifier(trigger)))
            cursor.execute(sql.SQL("CREATE TRIGGER {} BEFORE UPDATE ON agent_runner_usage_receipts FOR EACH ROW WHEN (OLD.runner_id={} AND OLD.owner='llm' AND OLD.purpose='compression' AND NEW.phase='observed') EXECUTE FUNCTION {}()").format(
                sql.Identifier(trigger), sql.Literal(identifier), sql.Identifier(trigger)))
        installed = True
        worker_id = 'voice_summary_worker_' + secrets.token_hex(10)
        process = fleet.processes.start([str(Path(__file__).with_name('kf_voice_compression_process.py')),
            'worker', '--worker-id', worker_id, '--max-tasks', '1'],
            environment={**fleet.environment, **v.api.environment, **v.asr_environment(),
                'QWEN_MODEL_CODE': fleet.models[0], 'KF_VOICE_SUMMARY_FIXTURE_MODEL': fleet.models[0]},
            private_working_directory=True)
        fleet.children.append((process, worker_id))
        assert summary_reply.arrived.wait(25)
        before = runner(s.database, identifier)
        assert before['status'] == 'running' and before['attempt'] == 1
        assert not decoded(before['checkpoint']).get('unstarted')
        assert not decoded(before['checkpoint']).get('execution')
        prep = s.rows('SELECT phase,authorized_attempt,fee_owner_runner_id FROM wecom_kf_input_preparations WHERE input_ref=%s', (input_ref,))
        assert prep == [{'phase': 'known', 'authorized_attempt': 1, 'fee_owner_runner_id': identifier}]
        assert post_count(v) == 1 and len(provider_peer.requests(summary_marker)) == 1
        release.set()
        interrupted = wait_for(lambda: r if (r := runner(s.database, identifier))['status'] == 'interrupted' else None, timeout=30)
        fleet.assert_clean_exit(process)
        checkpoint = decoded(interrupted['checkpoint'])
        assert not checkpoint.get('unstarted') and not checkpoint.get('execution')
        receipts = s.rows('SELECT owner,purpose,phase,usage,applied,authorized_attempt FROM agent_runner_usage_receipts WHERE runner_id=%s', (identifier,))
        assert len(receipts) == 2
        asr = next(r for r in receipts if r['owner'] == 'asr')
        summary = next(r for r in receipts if r['purpose'] == 'compression')
        assert asr['phase'] == 'observed' and asr['usage'] == {'calls': 1} and not asr['applied']
        assert summary['phase'] == 'started' and summary['usage'] is None and not summary['applied']
        assert asr['authorized_attempt'] == summary['authorized_attempt'] == 1
        assert s.rows('SELECT owner_runner_id,gate FROM agent_runner_session_claims WHERE session_id=%s', (s.legacy_sid,)) == [{'owner_runner_id': identifier, 'gate': 'execution'}]
        assert s.rows('SELECT 1 FROM chat_records WHERE record_id=%s', (interrupted['record_id'],)) == []
        drop_trigger()
        response = v.api.call('POST', '/v1/runners/' + identifier + '/controls',
            headers=v.api.headers(scope=s, input_ref=input_ref),
            json={'action': 'resume', 'client_request_id': 'summary_failure_resume_' + secrets.token_hex(10)})
        assert response.status_code == 202
        second = start_voice_worker(fleet, v, once=True)
        fleet.assert_clean_exit(second)
        rejected = s.rows('SELECT status,error_code,consumed_attempt FROM agent_runner_controls WHERE runner_id=%s', (identifier,))
        assert rejected == [{'status': 'rejected', 'error_code': 'CHECKPOINT_TREE_INVALID', 'consumed_attempt': None}]
        after = runner(s.database, identifier)
        assert after['attempt'] == 1 and after['status'] == 'interrupted' and after['settlement_status'] == 'pending'
        assert decoded(after['checkpoint']) == checkpoint
        assert s.rows('SELECT owner,purpose,phase,usage,applied,authorized_attempt FROM agent_runner_usage_receipts WHERE runner_id=%s', (identifier,)) == receipts
        assert post_count(v) == len(provider_peer.requests(summary_marker)) == 1
        assert provider_peer.requests(model_marker) == []
        assert not provider_peer.errors and not v.peer.errors
    except BaseException as error:
        safe_voice_error(v, identifier, service_processes, 'post_marker_compression_receipt_fault', error)
        raise
    finally:
        release.set()
        drop_trigger()
        fleet.close()
