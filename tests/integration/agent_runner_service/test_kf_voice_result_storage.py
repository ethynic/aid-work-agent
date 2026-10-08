"""Real Speech/SQL failure, followed by explicit original observer-port retry.

The provider really returns the known result after original POST. A PG trigger
rolls back the result transaction. Re-submitting that exact known result below
is a declared typed-observer PG contract DI; it is not proof that a naturally
lost HTTP result is recoverable. Original resumed Worker uses the known cache
without another ASR POST and original receipt keeps its first Attempt owner.
"""
from decimal import Decimal
import json
import re
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
from .test_worker import Workers, runner, terminal
from .test_kf_voice_results import start_voice_worker, post_count, safe_voice_error

pytestmark = pytest.mark.integration


def test_actual_original_asr_result_sql_rollback_then_typed_observer_exact_retry_keeps_owner_and_restart_does_not_repost(
        voice_scope, service_processes, provider_peer, prices, voice_price):
    from psycopg2 import sql
    from src.channels.wecom_kf.voice_repository import VoiceRepository
    from src.services.agent_runner.source_receipts import SourceUnavailable
    v, s = voice_scope, voice_scope.scope
    marker = provider_peer.register(Reply(content='Known cache restored after explicit typed result retry'))
    transcript = marker + ' actual provider known result before SQL fault'
    release = threading.Event()
    reply = AsrReply(payload={'status': 20000000, 'result': transcript}, release=release)
    v.peer.replies.append(reply)
    locator, _ = v.receive_voice()
    accepted = v.text.accept(locator)
    identifier, input_ref = accepted['current_runner_id'], accepted['input_ref']
    trigger = 'voice_result_' + secrets.token_hex(12)
    installed = False
    fleet = Workers(service_processes, v.api, provider_peer, prices)

    def drop_trigger():
        nonlocal installed
        if installed:
            with s.database.connect() as connection, connection.cursor() as cursor:
                cursor.execute(sql.SQL('DROP TRIGGER {} ON wecom_kf_input_preparations').format(sql.Identifier(trigger)))
                cursor.execute(sql.SQL('DROP FUNCTION {}()').format(sql.Identifier(trigger)))
            installed = False

    try:
        verify_original_resources(fleet, v.api)
        first = start_voice_worker(fleet, v)
        assert reply.arrived.wait(15)
        started = s.rows('SELECT * FROM wecom_kf_input_preparations WHERE input_ref=%s', (input_ref,))[0]
        assert started['phase'] == 'started' and started['authorized_attempt'] == 1
        with s.database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE FUNCTION {}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'FICTIONAL_VOICE_RESULT_SQL_FAILURE'; END $$").format(sql.Identifier(trigger)))
            cursor.execute(sql.SQL("CREATE TRIGGER {} BEFORE UPDATE ON wecom_kf_input_preparations FOR EACH ROW WHEN (OLD.input_ref={} AND NEW.phase='known') EXECUTE FUNCTION {}()").format(
                sql.Identifier(trigger), sql.Literal(input_ref), sql.Identifier(trigger)))
        installed = True
        release.set()
        interrupted = wait_for(lambda: row if (row := runner(s.database, identifier))['status'] == 'interrupted' else None, timeout=25)
        fleet.assert_clean_exit(first)
        failed = s.rows('SELECT * FROM wecom_kf_input_preparations WHERE input_ref=%s', (input_ref,))[0]
        assert failed['phase'] == 'unknown' and failed['transcript'] is None and failed['success'] is False
        assert failed['observed_at'] is None
        assert failed['receipt_id'] == started['receipt_id']
        unknown = s.rows('SELECT * FROM agent_runner_usage_receipts WHERE receipt_id=%s', (started['receipt_id'],))[0]
        assert unknown['phase'] == 'unknown' and not unknown['applied']
        assert unknown['authorized_attempt'] == 1
        assert post_count(v) == 1 and not provider_peer.requests(marker)
        assert s.rows('SELECT 1 FROM chat_records WHERE session_id=%s', (s.legacy_sid,)) == []
        drop_trigger()

        # Explicit original typed-observer result retry contract, not a fake
        # Speech result or a claim of automatic recovery of a lost response.
        repository = VoiceRepository(None, s.database.connect)
        repository.result(input_ref, success=True, text=transcript, status=20000000)
        known = s.rows('SELECT * FROM wecom_kf_input_preparations WHERE input_ref=%s', (input_ref,))[0]
        receipt = s.rows('SELECT * FROM agent_runner_usage_receipts WHERE receipt_id=%s', (started['receipt_id'],))[0]
        assert known['phase'] == 'known' and known['transcript'] == transcript
        assert known['authorized_attempt'] == 1 and known['fee_owner_runner_id'] == identifier
        assert known['artifact'] == started['artifact'] and known['receipt_id'] == started['receipt_id']
        assert receipt['phase'] == 'observed' and receipt['usage'] == {'calls': 1}
        repository.result(input_ref, success=True, text=transcript, status=20000000)
        assert s.rows('SELECT * FROM wecom_kf_input_preparations WHERE input_ref=%s', (input_ref,)) == [known]
        assert s.rows('SELECT * FROM agent_runner_usage_receipts WHERE receipt_id=%s', (started['receipt_id'],)) == [receipt]
        with pytest.raises(SourceUnavailable, match='SOURCE_PREPARATION_RESULT_CONFLICT'):
            repository.result(input_ref, success=True, text='Conflicting foreign text', status=20000000)
        assert s.rows('SELECT * FROM wecom_kf_input_preparations WHERE input_ref=%s', (input_ref,)) == [known]
        resume = v.api.call('POST', '/v1/runners/' + identifier + '/controls',
            headers=v.api.headers(scope=s, input_ref=input_ref),
            json={'action': 'resume', 'client_request_id': 'voice_result_resume_' + secrets.token_hex(10)})
        assert resume.status_code == 202
        second = start_voice_worker(fleet, v)
        finished = terminal(s.database, identifier)
        fleet.assert_clean_exit(second)
        assert finished['status'] == 'completed' and finished['settlement_status'] == 'settled'
        assert finished['attempt'] == 2 and finished['record_id'] == interrupted['record_id']
        assert post_count(v) == len(provider_peer.requests(marker)) == 1
        receipts = s.rows('SELECT owner,authorized_attempt,phase,applied,record_id FROM agent_runner_usage_receipts WHERE runner_id=%s', (identifier,))
        assert len(receipts) == 2 and all(r['phase'] == 'observed' and r['applied'] and r['record_id'] == finished['record_id'] for r in receipts)
        assert next(r for r in receipts if r['owner'] == 'asr')['authorized_attempt'] == 1
        assert next(r for r in receipts if r['owner'] == 'llm')['authorized_attempt'] == 2
        assert s.rows('SELECT user_message,asr_calls,total_token_count,credit_cost FROM chat_records WHERE record_id=%s', (finished['record_id'],)) == [
            {'user_message': '[ASR识别结果] ' + transcript, 'asr_calls': 1,
                'total_token_count': 18, 'credit_cost': Decimal('0.11')}]
        assert s.rows('SELECT phase FROM agent_runner_inputs WHERE input_ref=%s', (input_ref,)) == [{'phase': 'applied'}]
        assert not v.peer.errors and not provider_peer.errors
    except BaseException as error:
        # Observe only bounded protocol enums/booleans before fixture cleanup.
        # The failed window's missing diagnostic is never reconstructed later.
        try:
            current = runner(s.database, identifier)
            checkpoint = current['checkpoint']
            if isinstance(checkpoint, str):
                checkpoint = json.loads(checkpoint)
            controls = s.rows('SELECT status,error_code FROM agent_runner_controls WHERE runner_id=%s', (identifier,))
            codes = [code if isinstance(code, str) and re.fullmatch(r'[A-Z][A-Z0-9_]{0,79}', code)
                else 'NO_CODE' if code is None else 'OTHER_CODE' for code in (item['error_code'] for item in controls)]
            print('SAFE_VOICE_RESULT_RETRY=' + json.dumps({
                'control_error_codes': codes,
                'has_execution': isinstance(checkpoint.get('execution'), dict),
                'unstarted': checkpoint.get('unstarted') is True,
                'preparation_phases': [item['phase'] for item in s.rows(
                    'SELECT phase FROM wecom_kf_input_preparations WHERE input_ref=%s', (input_ref,))],
                'receipt_phases': [item['phase'] for item in s.rows(
                    'SELECT phase FROM agent_runner_usage_receipts WHERE runner_id=%s', (identifier,))],
                'model_request_count': len(provider_peer.requests(marker)),
                'asr_post_count': post_count(v)}, sort_keys=True))
        except Exception as diagnostic_error:
            print('SAFE_VOICE_RESULT_RETRY=' + json.dumps({'diagnostic_exception_class': type(diagnostic_error).__name__}))
        safe_voice_error(v, identifier, service_processes, 'result_sql_rollback_retry', error)
        raise
    finally:
        release.set()
        drop_trigger()
        fleet.close()
