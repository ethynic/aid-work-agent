"""Accepted Runner facts and stable report pages remain visible before final checkpoint."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from src.wecom_conversation import service


def _conn(cursor):
    connection = MagicMock()
    connection.__enter__.return_value.cursor.return_value = cursor
    return connection


def test_report_page_filters_scope_before_limit_and_has_stable_cursor():
    cursor = MagicMock()
    cursor.fetchall.return_value = [
        {'conversation_id': 501, 'summary': None, 'coverage': None},
        {'conversation_id': 502, 'summary': {'overview': 'ok'}, 'coverage': None},
        {'conversation_id': 503, 'summary': None, 'coverage': None}]
    with patch.object(service, 'get_db_connection', return_value=_conn(cursor)):
        page = service.daily_summaries_for_date('tenant-a', 'user-a', '2026-11-04',
                                                conversation_ids=[501, 502, 503],
                                                after_id=500, limit=2)
    assert [row['conversation_id'] for row in page['reports']] == [501, 502]
    assert page['has_more'] and page['next_cursor'] == 502
    sql, params = cursor.execute.call_args.args
    assert sql.index('c.id > %s') < sql.index('LIMIT %s')
    assert sql.index('c.id=ANY(%s::bigint[])') < sql.index('LIMIT %s')
    assert 'ORDER BY c.id LIMIT %s' in sql
    assert params == ('2026-11-04', 'tenant-a', 'user-a', 500, [501, 502, 503], 3)


def _execution_row():
    accepted = datetime(2026, 11, 4, 8, tzinfo=timezone.utc)
    return {'runner_id': 'runner-a', 'status': 'failed', 'accepted_at': accepted,
            'finished_at': accepted, 'error_code': 'WECOM_SOURCE_MISMATCH',
            'descriptor': {'schema_version': 2, 'kind': 'daily', 'trigger': 'manual',
                           'business_date': '2026-11-04', 'conversation_ids': [1, 2, 3, 4, 5]},
            'progress': {'items': [
                {'conversation_id': 1, 'status': 'completed', 'phase': 'completed'},
                {'conversation_id': 2, 'status': 'running', 'phase': 'saving'},
                {'conversation_id': 3, 'status': 'running', 'phase': 'saving'},
                {'conversation_id': 4, 'status': 'running', 'phase': 'reading'}]}}


def test_execution_detail_reconciles_only_current_report_from_same_runner():
    """报告落库但完成 checkpoint 失败时，归属可证才补成功，否则保留疑点。"""
    cursor = MagicMock()
    cursor.fetchone.return_value = _execution_row()
    cursor.fetchall.return_value = [
        {'id': 1, 'name': '一', 'source_runner_id': 'runner-b'},
        {'id': 2, 'name': '二', 'source_runner_id': 'runner-a'},
        {'id': 3, 'name': '三', 'source_runner_id': 'runner-new'},
        {'id': 4, 'name': '四', 'source_runner_id': None}]
    with patch.object(service, 'get_db_connection', return_value=_conn(cursor)):
        execution = service.get_daily_execution('tenant-a', 'user-a', 'runner-a')
    assert [row['status'] for row in execution['targets']] == [
        'completed', 'completed_reconciled', 'result_unverified',
        'interrupted', 'not_executed']
    assert execution['error_code'] == 'WECOM_SOURCE_MISMATCH'
    assert execution['targets'][2]['error_code'] is None
    assert 'runner-a|3' in execution['targets'][2]['diagnostic']
    sql, params = cursor.execute.call_args_list[0].args
    assert "input->'request_data'->'wecom_conversation_business'" in sql
    assert 'tenant_id=%s AND user_id=%s' in sql
    assert params == ('runner-a', 'tenant-a', 'user-a')


def test_explicit_superseded_checkpoint_is_not_reconciled_as_completed():
    """Same Runner may own an older report; an unapplied UPSERT remains superseded."""
    cursor = MagicMock()
    cursor.fetchone.return_value = {**_execution_row(), 'status': 'completed',
                                    'progress': {'items': [
                                        {'conversation_id': 1, 'status': 'superseded',
                                         'phase': 'superseded'}]}}
    cursor.fetchall.return_value = [{'id': 1, 'name': '一',
                                     'source_runner_id': 'runner-a'}]
    with patch.object(service, 'get_db_connection', return_value=_conn(cursor)):
        execution = service.get_daily_execution('tenant-a', 'user-a', 'runner-a')
    assert execution['targets'][0]['status'] == 'superseded'
    assert '本次未覆盖' in execution['targets'][0]['reason_label']


def test_upsert_no_returning_row_reports_unapplied_even_if_current_source_matches():
    """Freshness comparison, not source_runner_id equality, decides this write."""
    cursor = MagicMock()
    cursor.fetchone.return_value = None
    with patch.object(service, 'get_conversation', return_value={'timezone': 'Asia/Shanghai'}), \
         patch.object(service, 'get_db_connection', return_value=_conn(cursor)), \
         patch.object(service, 'get_daily_summary',
                      return_value={'source_runner_id': 'runner-a'}) as current:
        result = service.save_daily_report(
            'tenant-a', 'user-a', 1, '2026-11-04', {'summary': {'overview': 'stale'}},
            snapshot_at=datetime(2026, 11, 4, 1, tzinfo=timezone.utc),
            generated_at=datetime(2026, 11, 4, 2, tzinfo=timezone.utc),
            source_runner_id='runner-a')
    assert result == {'report': {'source_runner_id': 'runner-a'}, 'applied': False}
    current.assert_called_once()
    sql = cursor.execute.call_args.args[0]
    assert 'EXCLUDED.snapshot_at > bs_wecom_daily_summaries.snapshot_at' in sql
    assert 'EXCLUDED.generated_at >= bs_wecom_daily_summaries.generated_at' in sql


def test_upsert_returning_row_reports_this_write_applied():
    """A returned UPSERT row is the transaction's direct evidence of application."""
    cursor = MagicMock()
    cursor.fetchone.return_value = {'business_date': '2026-11-04',
                                    'source_runner_id': 'runner-a',
                                    'summary': {'overview': 'new'}, 'coverage': None}
    with patch.object(service, 'get_conversation', return_value={'timezone': 'Asia/Shanghai'}), \
         patch.object(service, 'get_db_connection', return_value=_conn(cursor)), \
         patch.object(service, 'get_daily_summary') as current:
        result = service.save_daily_report(
            'tenant-a', 'user-a', 1, '2026-11-04', {'summary': {'overview': 'new'}},
            snapshot_at=datetime(2026, 11, 4, 1, tzinfo=timezone.utc),
            generated_at=datetime(2026, 11, 4, 2, tzinfo=timezone.utc),
            source_runner_id='runner-a')
    assert result['applied'] is True
    assert result['report']['summary']['overview'] == 'new'
    current.assert_not_called()


def test_queue_cancellation_without_business_checkpoint_keeps_accepted_scope():
    """受理 input 已有目标；业务未开始也能展示 Runner ID 与全部未执行。"""
    cursor = MagicMock()
    cursor.fetchone.return_value = {**_execution_row(), 'status': 'cancelled',
                                    'progress': None, 'error_code': None}
    cursor.fetchall.return_value = []
    with patch.object(service, 'get_db_connection', return_value=_conn(cursor)):
        execution = service.get_daily_execution('tenant-a', 'user-a', 'runner-a')
    assert execution['runner_id'] == 'runner-a'
    assert execution['target_count'] == 5
    assert all(item['status'] == 'not_executed' for item in execution['targets'])


def test_execution_list_filters_accepted_input_and_keyset_before_limit():
    first = _execution_row()
    second = {**first, 'runner_id': 'runner-older'}
    cursor = MagicMock()
    cursor.fetchall.return_value = [first, second]
    before = (datetime(2026, 11, 4, 9, tzinfo=timezone.utc), 'runner-z')
    with patch.object(service, 'get_db_connection', return_value=_conn(cursor)):
        page = service.list_daily_executions('tenant-a', 'user-a', '2026-11-04',
                                             before=before, limit=1)
    assert len(page['executions']) == 1
    assert page['has_more']
    assert page['next_cursor'] == (first['accepted_at'].isoformat(), 'runner-a')
    sql, params = cursor.execute.call_args.args
    assert "input->'request_data'->'wecom_conversation_business'->>'business_date'=%s" in sql
    assert '(accepted_at,runner_id)<(%s,%s)' in sql
    assert 'ORDER BY accepted_at DESC,runner_id DESC LIMIT %s' in sql
    assert params == ('tenant-a', 'user-a', '2026-11-04', *before, 2)
