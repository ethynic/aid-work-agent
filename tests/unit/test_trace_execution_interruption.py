"""An interrupted tool must not be persisted as a successful completed span."""
import json
from unittest.mock import patch

import pytest

from src.core.agent_engine.contracts import CheckpointFailure
from src.core.trace_collector import TraceCollector


def test_execution_error_closes_only_inflight_spans_and_preserves_authoritative_status():
    collector = TraceCollector(session_id='s', tenant_id='t', user_id='u', input_msg='next')
    collector.on_event({'type': 'tool_start', 'toolName': 'boss_jobs_list'})
    collector.on_event({'type': 'tool_result', 'toolName': 'boss_jobs_list', 'result': {'success': True}})
    collector.on_event({'type': 'tool_start', 'toolName': 'boss_resume_next'})
    collector.trace.status = 'interrupted'
    collector.on_execution_error(CheckpointFailure('COVERED_USAGE_OWNER_REQUIRED'))
    assert collector.trace.status == 'interrupted'
    assert collector.trace.error_message == 'COVERED_USAGE_OWNER_REQUIRED'
    complete, interrupted = collector.trace.spans
    assert complete.success is True
    assert interrupted.success is False
    assert interrupted.end_time is not None and interrupted.duration_ms >= 0
    assert json.loads(interrupted.result)['error_code'] == 'COVERED_USAGE_OWNER_REQUIRED'
    assert not collector._active_spans


def test_execution_error_never_copies_arbitrary_exception_text():
    collector = TraceCollector(session_id='s', tenant_id='t', user_id='u', input_msg='next')
    collector.on_event({'type': 'tool_start', 'toolName': 'boss_resume_next'})
    collector.on_execution_error(RuntimeError('postgresql://user:private-password@example/db'))
    assert collector.trace.error_message == 'EXECUTION_INTERRUPTED'
    assert collector.trace.metadata['execution_error_kind'] == 'RuntimeError'
    assert 'private-password' not in collector.trace.spans[-1].result


@pytest.mark.parametrize('status', ['interrupted', 'waiting', 'paused', 'cancelled'])
def test_incomplete_tool_span_keeps_nonterminal_or_cancelled_status_without_false_failure(status):
    collector = TraceCollector(session_id='s', tenant_id='t', user_id='u', input_msg='next')
    collector.on_event({'type': 'tool_start', 'toolName': 'boss_resume_next'})
    collector.trace.status = status
    with patch('src.core.trace_persist.schedule_persist'):
        collector.on_complete()
    span = collector.trace.spans[0]
    assert span.status == status
    assert span.success is False
    assert collector.trace.error_message is None
    assert 'error' not in collector.trace.tags
