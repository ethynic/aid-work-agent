"""_persist_trace 把 runner result.error_code 投影进 trace.error_message。

背景（2026-10-27 生产排查 tr_runner_10cce9cd）：ITERATION_LIMIT 走 engine
正常限流路径，collector 收不到 error 事件，obs_traces.error_message 恒为空，
排查必须翻主库 agent_runners.result 才能找到终态原因。失败终态时 worker
持久化 trace 前把 error_code 补进 trace.error_message。
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.services.agent_runner.worker import RunnerWorker

pytestmark = pytest.mark.unit


def _worker(runner_row):
    worker = object.__new__(RunnerWorker)
    worker.repository = SimpleNamespace(get=lambda runner_id: {"attempt": 1, **runner_row})
    return worker


def _collector():
    return SimpleNamespace(
        trace=SimpleNamespace(trace_id='tr_x', status='running', output='',
                              error_message=None, metadata={}, total_tokens=0,
                              agent_iterations=0, tags=[]),
        on_complete=MagicMock(),
    )


def _control():
    return SimpleNamespace(snapshot={}, attempt=SimpleNamespace(number=1))


def _failed_runner(error_code='ITERATION_LIMIT'):
    return {'status': 'failed', 'result': {'status': 'failed', 'output': '',
                                           'error_code': error_code},
            'checkpoint': {}, 'record_id': 'record-1'}


def test_finalize_retry_failed_projects_error_code():
    """finalization_retry 失败终态：error_code 传给 update_completion 并写入 trace"""
    worker = _worker(_failed_runner())
    collector = _collector()
    with patch('src.core.trace_persist.update_completion') as mock_update:
        worker._persist_trace(collector, 'runner-1', _control(), finalization_retry=True)

    mock_update.assert_called_once()
    assert mock_update.call_args.kwargs['error_message'] == 'ITERATION_LIMIT'
    assert collector.trace.error_message == 'ITERATION_LIMIT'
    assert collector.trace.status == 'failed'


def test_finalize_retry_completed_passes_no_error_code():
    """finalization_retry 完成终态：不投影 error_code"""
    runner = {'status': 'completed', 'result': {'status': 'completed', 'output': 'done'},
              'checkpoint': {}, 'record_id': 'record-1'}
    worker = _worker(runner)
    collector = _collector()
    with patch('src.core.trace_persist.update_completion') as mock_update:
        worker._persist_trace(collector, 'runner-1', _control(), finalization_retry=True)

    mock_update.assert_called_once()
    assert mock_update.call_args.kwargs['error_message'] is None
    assert collector.trace.error_message is None


def test_normal_persist_failed_projects_error_code(monkeypatch):
    """常规持久化失败终态：trace.error_message 承接 error_code，随 UPSERT 落库"""
    worker = _worker(_failed_runner())
    collector = _collector()
    with patch('src.db.models.ChatRecordDB.get_by_id', return_value=None):
        worker._persist_trace(collector, 'runner-1', _control())

    assert collector.trace.error_message == 'ITERATION_LIMIT'
    assert collector.trace.status == 'failed'
    assert collector.trace.metadata.get('runner_summary') is True
    collector.on_complete.assert_called_once()


def test_normal_persist_completed_keeps_no_error_message(monkeypatch):
    """常规持久化完成终态：不写 error_message，已有多余值不误伤"""
    runner = {'status': 'completed', 'result': {'status': 'completed', 'output': 'done'},
              'checkpoint': {}, 'record_id': 'record-1'}
    worker = _worker(runner)
    collector = _collector()
    with patch('src.db.models.ChatRecordDB.get_by_id', return_value=None):
        worker._persist_trace(collector, 'runner-1', _control())

    assert collector.trace.error_message is None


def test_failed_without_error_code_keeps_existing_message(monkeypatch):
    """失败但无 error_code（如异常路径已记录错误）：不覆盖已有 error_message"""
    runner = {'status': 'failed', 'result': {'status': 'failed', 'output': ''},
              'checkpoint': {}, 'record_id': 'record-1'}
    worker = _worker(runner)
    collector = _collector()
    collector.trace.error_message = 'LLM_UNAVAILABLE'
    with patch('src.db.models.ChatRecordDB.get_by_id', return_value=None):
        worker._persist_trace(collector, 'runner-1', _control())

    assert collector.trace.error_message == 'LLM_UNAVAILABLE'


def test_finalize_retry_existing_message_not_overwritten_by_error_code():
    """retry 失败终态已有更具体错误时：不用 result.error_code 覆盖，update_completion 收 None"""
    worker = _worker(_failed_runner(error_code='EXECUTION_FAILED'))
    collector = _collector()
    collector.trace.error_message = 'EXECUTION_INTERRUPTED'
    with patch('src.core.trace_persist.update_completion') as mock_update:
        worker._persist_trace(collector, 'runner-1', _control(), finalization_retry=True)

    assert collector.trace.error_message == 'EXECUTION_INTERRUPTED'
    assert mock_update.call_args.kwargs['error_message'] is None
