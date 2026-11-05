"""RuntimeExecution 企微业务分支固定顺序测试（fake 工具端口/模型端口）。

真实 LocalToolAdapter 生命周期接缝（ToolFact/派生 context/权威异常）见
test_business_flow_lifecycle.py；本文件覆盖：补录不调用模型；日报逐会话
读取→分析→保存；一个会话失败继续其他；空日；模型用量沿原 observer 记账；
未排空/缺窗口按真实缺口失败；取消沿原 Runner 协议。
"""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.core.agent_engine.contracts import AgentMode, CheckpointFailure, ExecutionState, Identity, Outcome
from src.core.request_context import AgentRequestContext
from src.services.agent_runner.runtime import wecom_business_flow as flow


def test_v2_scheduled_scope_requires_frozen_ids_and_same_source():
    """计划改选后本次执行仍按受理快照；跨设备行不能借 ID 混入。"""
    execution = SimpleNamespace(identity=SimpleNamespace(tenant_id='tenant-a', user_id='user-a'))
    descriptor = {'schema_version': 2, 'kind': 'daily', 'task_id': 'sched-one',
                  'account': 'acc1', 'device': 'device-a', 'conversation_ids': [7]}
    with patch('src.wecom_conversation.service.get_conversation', return_value={
            'id': 7, 'source_account_id': 'acc1', 'device_id': 'device-a'}), \
         patch('src.wecom_conversation.service.plan_conversations') as live_scope:
        assert [row['id'] for row in flow._resolve_conversations(execution, descriptor)] == [7]
        live_scope.assert_not_called()
    with patch('src.wecom_conversation.service.get_conversation', return_value={
            'id': 7, 'source_account_id': 'acc1', 'device_id': 'device-b'}):
        with pytest.raises(flow.WecomBusinessFlowError, match='来源'):
            flow._resolve_conversations(execution, descriptor)
    with patch('src.wecom_conversation.service.plan_conversations') as live_scope:
        with pytest.raises(flow.WecomBusinessFlowError, match='冻结会话清单'):
            flow._resolve_conversations(execution, {**descriptor, 'conversation_ids': []})
        live_scope.assert_not_called()


def test_manual_scope_survives_runner_request_context_freeze():
    """A manually accepted scope must reach execution after request data is frozen."""
    execution = SimpleNamespace(identity=SimpleNamespace(tenant_id='tenant-a', user_id='user-a'))
    accepted = {'schema_version': 2, 'kind': 'daily', 'trigger': 'manual',
                'account': 'acc1', 'device': 'device-a', 'conversation_ids': [7, 8]}
    context = AgentRequestContext(request_data={'wecom_conversation_business': accepted})
    descriptor = dict(context.request_data['wecom_conversation_business'])
    assert descriptor['conversation_ids'] == (7, 8)
    rows = {item: {'id': item, 'source_account_id': 'acc1', 'device_id': 'device-a'}
            for item in (7, 8)}
    with patch('src.wecom_conversation.service.get_conversation',
               side_effect=lambda tenant, user, item: rows[item]):
        assert [row['id'] for row in flow._resolve_conversations(execution, descriptor)] == [7, 8]
    with pytest.raises(flow.WecomBusinessFlowError, match='冻结会话清单'):
        flow._resolve_conversations(execution, {**descriptor, 'conversation_ids': ()})


def _page(messages, start=1000, end=1000 + 86400, snapshot=1790000000, has_more=False, cursor=None):
    return {'read_window': {'start_utc': start, 'end_utc': end, 'snapshot_at': snapshot},
            'messages': messages, 'has_more': has_more, 'cursor': cursor,
            'source_generation': 'gen1'}


def _day_message(msg_id, text='内容', send_time=1500, side='peer'):
    return {'msg_id': msg_id, 'side': side, 'text': text, 'send_time': send_time,
            'sender_name': '张三', 'sender_platform': 'wechat'}


def _background_message(msg_id, send_time=500):
    return {'msg_id': msg_id, 'side': 'peer', 'text': '背景', 'send_time': send_time,
            'sender_name': '李四', 'sender_platform': 'wecom'}


def _summary(overview, **overrides):
    value = {'overview': overview, 'pending_human': [], 'anomalies': []}
    value.update(overrides)
    return json.dumps(value, ensure_ascii=False)


def _digest(value):
    return json.dumps({'digest': value, 'items': []}, ensure_ascii=False)


class FakeAdapter:
    """工具端口替身：按会话返回预置页序列（每次调用消费一页）。"""

    def __init__(self, pages_by_session):
        self.pages_by_session = pages_by_session
        self.calls = []

    def _run_local_required_tool(self, tool_name, args, tenant_id, user_id,
                                 cancel_check=None, context=None):
        self.calls.append((tool_name, dict(args)))
        assert context is not None and context.tool_call_id  # 评审 P1-1：调用身份必达

        async def generate():
            pages = self.pages_by_session.get(args.get('session'))
            if not pages:
                result = {'success': False, 'code': 'TARGET_NOT_FOUND', 'message': '无会话'}
            else:
                result = {'success': True, 'data': pages.pop(0)}
            yield ('result', result)

        return generate()


class FakeControl:
    def __init__(self):
        self.saved = []
        self.command_value = None
        self.attempt = type('Attempt', (), {'runner_id': 'run-123'})()

    def command(self):
        return self.command_value

    async def save(self, state, boundary):
        self.saved.append(boundary)


class FakeObserver:
    def __init__(self):
        self.events = []
        self.event_data = []
        self.usage = []

    async def event(self, state, event):
        self.events.append(event.get('type'))
        self.event_data.append(event.get('data'))

    async def model_usage(self, state, fact):
        self.usage.append(fact)


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get_model_name(self):
        return 'glm-test'

    def get_provider_name(self):
        return 'zhipu'

    async def chat_with_tools(self, system_prompt, messages, tools):
        self.calls.append({'system': system_prompt, 'user': messages[-1]['content']})
        content = self.responses.pop(0) if self.responses else '{}'
        return {'content': content, 'finish_reason': 'stop',
                'usage': {'prompt_tokens': 10, 'completion_tokens': 5}}


class FakeExecution:
    def __init__(self, adapter, llm, control=None, observer=None):
        self.identity = Identity(tenant_id='t1', user_id='u1', session_id='wecom_business_flow')
        self.state = ExecutionState(
            identity=self.identity, execution_id='exec-1', role=AgentMode.MASTER,
            system_prompt='', messages=[])
        self.control = control or FakeControl()
        self.observer = observer or FakeObserver()
        self.resources = type('Resources', (), {'llm': llm})()

    async def _tool_context(self, request_context=None):
        from src.tools.context import ExecutionContextFactory
        return ExecutionContextFactory.for_agent_call(
            tenant_id='t1', user_id='u1', session_id='wecom_business_flow',
            agent_execution_id='exec-1')


def _conversation(conversation_id, locator, name='群一'):
    return {'id': conversation_id, 'session_locator': locator, 'name': name,
            'timezone': 'Asia/Shanghai'}


def _async_save(callback):
    async def _save(e, c, d, a, s, r):
        callback(e, c, d, a, s, r)
        return {'applied': True}
    return _save


async def _collect(generator):
    events = []
    async for event in generator:
        events.append(event.get('type') if isinstance(event, dict) else event)
    return events


@pytest.mark.asyncio
async def test_backfill_reads_pages_without_model():
    pages = [_page([_day_message(1)], has_more=True, cursor='wd1.a'),
             _page([_day_message(2)])]
    adapter = FakeAdapter({'ws1.backfill': pages})
    llm = FakeLLM([])
    execution = FakeExecution(adapter, llm)
    events = await _collect(flow.run_wecom_business(
        execution, {'kind': 'backfill', 'session': 'ws1.backfill', 'date': '2026-11-03'}, adapter))
    assert execution.state.outcome == Outcome.COMPLETED
    assert 'execution_completed' in events
    assert llm.calls == []  # 补录不调用总结模型
    assert adapter.calls[0][1]['date'] == '2026-11-03'
    assert adapter.calls[1][1]['cursor'] == 'wd1.a'
    assert 'date' not in adapter.calls[1][1]  # 续页参数继承


@pytest.mark.asyncio
async def test_backfill_missing_cursor_when_has_more_fails():
    pages = [_page([_day_message(1)], has_more=True, cursor=None)]
    adapter = FakeAdapter({'ws1.backfill': pages})
    execution = FakeExecution(adapter, FakeLLM([]))
    with pytest.raises(flow.WecomBusinessFlowError) as error:
        await _collect(flow.run_wecom_business(
            execution, {'kind': 'backfill', 'session': 'ws1.backfill'}, adapter))
    assert error.value.code == 'WECOM_DAY_READ_INCOMPLETE'  # 未排空不装作完成


@pytest.mark.asyncio
async def test_non_date_response_rejected():
    pages = [{'messages': [], 'has_more': False}]  # 无 read_window（普通增量形状）
    adapter = FakeAdapter({'ws1.plain': pages})
    execution = FakeExecution(adapter, FakeLLM([]))
    with pytest.raises(flow.WecomBusinessFlowError) as error:
        await _collect(flow.run_wecom_business(
            execution, {'kind': 'backfill', 'session': 'ws1.plain'}, adapter))
    assert error.value.code == 'WECOM_DAY_MODE_UNAVAILABLE'


@pytest.mark.asyncio
async def test_daily_saves_report_and_records_model_usage():
    summary = _summary('当日概览', handled=[{'topic': '报价', 'detail': '已回复',
                                          'evidence': [1]}])
    adapter = FakeAdapter({'ws1.daily1': [_page([_day_message(1), _day_message(2)])]})
    llm = FakeLLM([summary])
    execution = FakeExecution(adapter, llm)
    saved = []
    with patch.object(flow, '_resolve_conversations',
                      lambda e, d: [_conversation(7, 'ws1.daily1')]), \
         patch.object(flow, '_save_report',
                      _async_save(lambda e, c, d, a, s, r: saved.append((c['id'], d, a['day_count'])))), \
         patch.object(flow, '_descriptor_date', lambda descriptor: '2026-11-02'):
        events = await _collect(flow.run_wecom_business(
            execution, {'kind': 'daily', 'task_id': 'sched_x', 'requirements': '关注退换货'}, adapter))
    assert execution.state.outcome == Outcome.COMPLETED
    assert saved == [(7, '2026-11-02', 2)]
    assert len(llm.calls) == 1
    assert '关注退换货' in llm.calls[0]['user']  # 计划特殊关注要求进入输入（P2-15）
    assert 'msg_id=1' in llm.calls[0]['user']    # 模型输入保留原消息 ID（P2-14）
    assert execution.observer.usage and execution.observer.usage[0]['usage']['prompt_tokens'] == 10
    assert 'before_model' in execution.control.saved
    result = execution.state.resources['wecom_daily_result']
    assert result['business_date'] == '2026-11-02'
    assert result['completed'] == [{'conversation_id': 7, 'name': '群一'}]
    assert result['task_id'] == 'sched_x'  # 本次范围进入事实（评审三 P1-4）
    assert result['scope_conversation_ids'] == [7]


@pytest.mark.asyncio
async def test_daily_input_includes_background_text_and_platform():
    summary = _summary('ok')
    adapter = FakeAdapter({'ws1.daily1': [_page([_background_message(1), _day_message(2)])]})
    llm = FakeLLM([summary])
    execution = FakeExecution(adapter, llm)
    with patch.object(flow, '_resolve_conversations',
                      lambda e, d: [_conversation(7, 'ws1.daily1')]), \
         patch.object(flow, '_save_report', _async_save(lambda *a: None)), \
         patch.object(flow, '_descriptor_date', lambda descriptor: '2026-11-02'):
        await _collect(flow.run_wecom_business(execution, {'kind': 'daily'}, adapter))
    user_text = llm.calls[0]['user']
    assert '[背景]' in user_text and '背景' in user_text  # 背景正文进入输入（P2-14）
    assert '/wecom' in user_text  # 平台身份进入输入
    assert '当日消息' in user_text


@pytest.mark.asyncio
async def test_daily_empty_day_saves_explicit_empty_report():
    adapter = FakeAdapter({'ws1.empty': [_page([], has_more=False)]})
    llm = FakeLLM([])
    execution = FakeExecution(adapter, llm)
    saved = []

    async def fake_save(e, c, d, a, s, r):
        saved.append((c['id'], a['summary']))
        return {'applied': True}

    with patch.object(flow, '_resolve_conversations',
                      lambda e, d: [_conversation(8, 'ws1.empty')]), \
         patch.object(flow, '_save_report', fake_save), \
         patch.object(flow, '_descriptor_date', lambda descriptor: '2026-11-02'):
        await _collect(flow.run_wecom_business(execution, {'kind': 'daily'}, adapter))
    assert saved and saved[0][1]['overview'] == '当日无消息'
    assert llm.calls == []


@pytest.mark.asyncio
async def test_empty_day_with_unreadable_timestamps_keeps_read_gap_visible():
    """无法确认日期的记录可能属于当天，不能断言当天完全没有消息。"""
    result = await flow._analyze_conversation(
        None, {}, {'name': '项目群'}, '2026-11-02', 'Asia/Shanghai', [_page([])],
        notes='1 条记录无有效时间未纳入')
    assert result['summary']['overview'] == '当日无可确认的消息'
    assert '无有效时间' in result['summary']['notes']
    assert result['summary']['pending_human'] == result['summary']['anomalies'] == []


@pytest.mark.asyncio
async def test_daily_continues_after_single_conversation_failure():
    summary = _summary('ok')
    adapter = FakeAdapter({
        'ws1.bad': [],  # 工具返回失败 → 该会话失败
        'ws1.good': [_page([_day_message(1)])],
    })
    llm = FakeLLM([summary])
    execution = FakeExecution(adapter, llm)
    saved = []
    with patch.object(flow, '_resolve_conversations',
                      lambda e, d: [_conversation(1, 'ws1.bad', '失败群'), _conversation(2, 'ws1.good', '正常群')]), \
         patch.object(flow, '_save_report',
                      _async_save(lambda e, c, d, a, s, r: saved.append(c['id']))), \
         patch.object(flow, '_descriptor_date', lambda descriptor: '2026-11-02'):
        events = await _collect(flow.run_wecom_business(execution, {'kind': 'daily'}, adapter))
    assert saved == [2]
    assert execution.state.outcome == Outcome.COMPLETED
    result = execution.state.resources['wecom_daily_result']
    assert result['failed'][0]['name'] == '失败群'
    assert result['failed'][0]['conversation_id'] == 1  # 稳定 ID（评审三 P1-4）
    assert result['completed'] == [{'conversation_id': 2, 'name': '正常群'}]


@pytest.mark.asyncio
async def test_daily_all_failed_raises():
    adapter = FakeAdapter({'ws1.bad': []})
    execution = FakeExecution(adapter, FakeLLM([]))
    with patch.object(flow, '_resolve_conversations',
                      lambda e, d: [_conversation(1, 'ws1.bad')]), \
         patch.object(flow, '_descriptor_date', lambda descriptor: '2026-11-02'):
        with pytest.raises(flow.WecomBusinessFlowError) as error:
            await _collect(flow.run_wecom_business(execution, {'kind': 'daily'}, adapter))
    assert error.value.code == 'WECOM_DAILY_ALL_FAILED'


@pytest.mark.asyncio
async def test_all_superseded_finishes_without_claiming_report_saved():
    """A stale snapshot is analyzed but its UPSERT did not apply; preserve that fact."""
    adapter = FakeAdapter({'ws1.old': [_page([_day_message(1)])]})
    execution = FakeExecution(adapter, FakeLLM([_summary('旧快照')]))

    async def stale_save(*args):
        return {'report': {'source_runner_id': 'newer-runner'}, 'applied': False}

    with patch.object(flow, '_resolve_conversations',
                      lambda e, d: [_conversation(1, 'ws1.old')]), \
         patch.object(flow, '_save_report', stale_save), \
         patch.object(flow, '_descriptor_date', lambda descriptor: '2026-11-02'):
        events = await _collect(flow.run_wecom_business(
            execution, {'kind': 'daily'}, adapter))
    assert execution.state.outcome == Outcome.COMPLETED
    assert 'execution_completed' in events
    progress = execution.state.resources['wecom_daily_progress']['items'][0]
    assert progress['status'] == progress['phase'] == 'superseded'
    assert 'daily_conversation_superseded' in execution.control.saved
    result = execution.state.resources['wecom_daily_result']
    assert result['completed'] == result['failed'] == []
    assert result['superseded'] == [{'conversation_id': 1, 'name': '群一'}]
    assert 'daily_result' in execution.control.saved
    assert any('本次未覆盖' in str(value) for value in execution.observer.event_data)
    assert not any('日报已保存' in str(value) for value in execution.observer.event_data)


@pytest.mark.asyncio
async def test_v2_account_mismatch_stops_whole_scope_before_next_session():
    """日期回执账号变化是整轮身份失败，不被逐会话失败继续逻辑吞掉。"""
    page = {**_page([_day_message(1)]), 'self_user_id': 'other-account',
            'session': 'ws1.first'}
    adapter = FakeAdapter({'ws1.first': [page], 'ws1.second': [_page([])]})
    execution = FakeExecution(adapter, FakeLLM([]))
    descriptor = {'schema_version': 2, 'kind': 'daily', 'trigger': 'manual',
                  'business_date': '2026-11-02', 'account': 'acc1',
                  'device': 'device-a', 'conversation_ids': [1, 2]}
    with patch.object(flow, '_resolve_conversations', lambda e, d: [
            _conversation(1, 'ws1.first'), _conversation(2, 'ws1.second')]):
        with pytest.raises(flow.WecomSourceError) as error:
            await _collect(flow.run_wecom_business(execution, descriptor, adapter))
    assert error.value.code == 'WECOM_SOURCE_MISMATCH'
    assert len(adapter.calls) == 1
    items = execution.state.resources['wecom_daily_progress']['items']
    assert [item['status'] for item in items] == ['running', 'not_started']


@pytest.mark.asyncio
@pytest.mark.parametrize('code', ['CURSOR_UNSUPPORTED', 'NOT_LOGGED_IN', 'WECOM_NOT_FOUND'])
async def test_v2_source_lost_on_next_page_stops_before_second_target(code):
    """A changed account or generation cannot be a per-target recoverable gap."""
    class SourceChangedAdapter(FakeAdapter):
        def _run_local_required_tool(self, tool_name, args, tenant_id, user_id,
                                     cancel_check=None, context=None):
            self.calls.append((tool_name, dict(args)))

            async def generate():
                if args.get('cursor'):
                    yield ('result', {'success': False, 'code': code})
                else:
                    page = {**_page([_day_message(1)], has_more=True, cursor='wd1.next'),
                            'self_user_id': 'acc1', 'session': 'ws1.first'}
                    yield ('result', {'success': True, 'data': page})
            return generate()

    adapter = SourceChangedAdapter({})
    execution = FakeExecution(adapter, FakeLLM([]))
    descriptor = {'schema_version': 2, 'kind': 'daily', 'trigger': 'manual',
                  'business_date': '2026-11-02', 'account': 'acc1',
                  'device': 'device-a', 'conversation_ids': [1, 2]}
    with patch.object(flow, '_resolve_conversations', lambda e, d: [
            _conversation(1, 'ws1.first'), _conversation(2, 'ws1.second')]):
        with pytest.raises(flow.WecomSourceError) as error:
            await _collect(flow.run_wecom_business(execution, descriptor, adapter))
    assert error.value.code == code
    assert [call[1]['session'] for call in adapter.calls] == ['ws1.first', 'ws1.first']


@pytest.mark.asyncio
@pytest.mark.parametrize('command,outcome', [('cancel', Outcome.CANCELLED),
                                            ('pause', Outcome.PAUSED)])
@pytest.mark.parametrize('target_count', [1, 2])
@pytest.mark.parametrize('receipt_success', [False, True])
async def test_cancelled_local_read_on_last_target_obeys_runner_command(
        command, outcome, target_count, receipt_success):
    """Neither a CANCELLED receipt nor a late successful empty page may save after stop."""
    control = FakeControl()

    class CancelledAdapter(FakeAdapter):
        def _run_local_required_tool(self, tool_name, args, tenant_id, user_id,
                                     cancel_check=None, context=None):
            self.calls.append((tool_name, dict(args)))

            async def generate():
                if args['session'] == 'ws1.last':
                    control.command_value = command
                    yield ('result', ({'success': True, 'data': _page([])}
                                      if receipt_success else
                                      {'success': False, 'code': 'CANCELLED'}))
                else:
                    yield ('result', {'success': True, 'data': _page([])})
            return generate()

    adapter = CancelledAdapter({})
    execution = FakeExecution(adapter, FakeLLM([]), control=control)
    saved = []
    conversations = ([_conversation(1, 'ws1.first')] if target_count == 2 else [])
    conversations.append(_conversation(target_count, 'ws1.last'))
    with patch.object(flow, '_resolve_conversations', lambda e, d: conversations), \
         patch.object(flow, '_save_report',
                      _async_save(lambda *args: saved.append(args[1]['id']))):
        events = await _collect(flow.run_wecom_business(execution, {'kind': 'daily'}, adapter))
    assert execution.state.outcome == outcome
    assert events[-1] == ('cancelled' if command == 'cancel' else 'paused')
    assert command in control.saved
    assert 'wecom_daily_result' not in execution.state.resources
    assert execution.state.resources['wecom_daily_progress']['items'][-1]['status'] == 'running'
    assert saved == ([1] if target_count == 2 else [])
    assert len(adapter.calls) == target_count


@pytest.mark.asyncio
async def test_report_saved_then_completion_checkpoint_failure_stops_scope():
    """报告已写但完成事实未落库时停止；查询稍后只能按来源 runner 对账。"""
    class FailingControl(FakeControl):
        persisted = None

        async def save(self, state, boundary):
            if boundary == 'daily_conversation_completed':
                raise OSError('checkpoint unavailable')
            self.persisted = state.checkpoint()
            await super().save(state, boundary)

    first = {**_page([]), 'self_user_id': 'acc1', 'session': 'ws1.first'}
    adapter = FakeAdapter({'ws1.first': [first], 'ws1.second': [_page([])]})
    control = FailingControl()
    execution = FakeExecution(adapter, FakeLLM([]), control=control)
    saved = []
    with patch.object(flow, '_resolve_conversations', lambda e, d: [
            _conversation(1, 'ws1.first'), _conversation(2, 'ws1.second')]), \
         patch.object(flow, '_save_report',
                      _async_save(lambda e, c, d, a, s, r: saved.append(c['id']))):
        with pytest.raises(CheckpointFailure):
            await _collect(flow.run_wecom_business(execution, {'kind': 'daily'}, adapter))
    assert saved == [1]
    assert len(adapter.calls) == 1
    first_fact = control.persisted['resources']['wecom_daily_progress']['items'][0]
    assert first_fact['status'] == 'running' and first_fact['phase'] == 'saving'


@pytest.mark.asyncio
async def test_model_length_finish_is_failure_and_preserves_old_report():
    class TruncatedLLM(FakeLLM):
        async def chat_with_tools(self, system_prompt, messages, tools):
            result = await super().chat_with_tools(system_prompt, messages, tools)
            result['finish_reason'] = 'length'
            return result

    adapter = FakeAdapter({'ws1.one': [_page([_day_message(1)])]})
    execution = FakeExecution(adapter, TruncatedLLM([_summary('partial')]))
    saved = []
    with patch.object(flow, '_resolve_conversations',
                      lambda e, d: [_conversation(1, 'ws1.one')]), \
         patch.object(flow, '_save_report',
                      _async_save(lambda e, c, d, a, s, r: saved.append(c['id']))):
        with pytest.raises(flow.WecomBusinessFlowError):
            await _collect(flow.run_wecom_business(execution, {'kind': 'daily'}, adapter))
    assert saved == []
    assert execution.state.resources['wecom_daily_progress']['items'][0]['error_code'] == 'WECOM_SUMMARY_TRUNCATED'


@pytest.mark.asyncio
async def test_model_missing_required_sections_never_overwrites_old_report():
    adapter = FakeAdapter({'ws1.one': [_page([_day_message(1)])]})
    execution = FakeExecution(adapter, FakeLLM(['{"overview":"partial"}']))
    saved = []
    with patch.object(flow, '_resolve_conversations',
                      lambda e, d: [_conversation(1, 'ws1.one')]), \
         patch.object(flow, '_save_report',
                      _async_save(lambda e, c, d, a, s, r: saved.append(c['id']))):
        with pytest.raises(flow.WecomBusinessFlowError):
            await _collect(flow.run_wecom_business(execution, {'kind': 'daily'}, adapter))
    assert saved == []
    assert execution.state.resources['wecom_daily_progress']['items'][0]['status'] == 'failed'


@pytest.mark.asyncio
async def test_trusted_invocation_device_mismatch_is_fatal():
    from types import SimpleNamespace

    execution = SimpleNamespace(identity=Identity('t1', 'u1', 'sess1'),
                                control=SimpleNamespace(durable_owner=True))
    result = {'success': True, 'invocation_id': 'inv-one',
              'data': {'self_user_id': 'acc1', 'session': 'ws1.one'}}
    descriptor = {'schema_version': 2, 'account': 'acc1', 'device': 'device-a'}
    invocation = {'device_id': 'device-b', 'user_id': 'u1',
                  'tool_name': flow.READ_TOOL, 'session_id': 'sess1'}
    with patch('src.local_tools.repository.get_invocation', return_value=invocation) as query:
        with pytest.raises(flow.WecomSourceError) as error:
            await flow._verify_read_source(execution, descriptor, result,
                                           SimpleNamespace(invocation_id='inv-one'),
                                           {}, 'ws1.one')
    assert error.value.code == 'WECOM_SOURCE_MISMATCH'
    query.assert_called_once_with('inv-one', 't1')


@pytest.mark.asyncio
async def test_failed_read_with_wrong_returned_account_is_fatal():
    """An ordinary read error cannot hide a conflicting account receipt."""
    from types import SimpleNamespace

    execution = SimpleNamespace(control=SimpleNamespace(durable_owner=False))
    result = {'success': False, 'code': 'READ_FAILED',
              'data': {'self_user_id': 'account-b'}}
    with pytest.raises(flow.WecomSourceError) as error:
        await flow._verify_read_source(
            execution, {'schema_version': 2, 'account': 'account-a'}, result,
            SimpleNamespace(invocation_id=None), {}, 'ws1.one')
    assert error.value.code == 'WECOM_SOURCE_MISMATCH'


@pytest.mark.asyncio
async def test_daily_missing_snapshot_fails_not_fabricated():
    pages = [{'read_window': {'start_utc': 1000, 'end_utc': 90000},  # 无 snapshot_at
              'messages': [_day_message(1)], 'has_more': False}]
    adapter = FakeAdapter({'ws1.nosnap': pages})
    execution = FakeExecution(adapter, FakeLLM([]))
    saved = []
    with patch.object(flow, '_resolve_conversations',
                      lambda e, d: [_conversation(1, 'ws1.nosnap')]), \
         patch.object(flow, '_save_report',
                      _async_save(lambda e, c, d, a, s, r: saved.append(c['id']))), \
         patch.object(flow, '_descriptor_date', lambda descriptor: '2026-11-02'):
        with pytest.raises(flow.WecomBusinessFlowError) as error:
            await _collect(flow.run_wecom_business(execution, {'kind': 'daily'}, adapter))
    # 唯一会话按缺口失败 → 终态全部失败；不保存报告、不用当前时间补造快照（P1-8）
    assert error.value.code == 'WECOM_DAILY_ALL_FAILED'
    assert 'WECOM_DAY_SNAPSHOT_MISSING' in str(error.value)
    assert saved == []


@pytest.mark.asyncio
async def test_long_content_groups_pages_before_synthesis():
    long_text = '长' * 9000
    pages = [_page([_day_message(1, long_text)], has_more=True, cursor='wd1.next'),
             _page([_day_message(2, long_text)])]
    adapter = FakeAdapter({'ws1.long': pages})
    llm = FakeLLM([_digest('组1'), _digest('组2'), _summary('合成')])
    execution = FakeExecution(adapter, llm)
    saved = []
    with patch.object(flow, '_resolve_conversations',
                      lambda e, d: [_conversation(9, 'ws1.long')]), \
         patch.object(flow, '_save_report',
                      _async_save(lambda e, c, d, a, s, r: saved.append(a['summary']))), \
         patch.object(flow, '_descriptor_date', lambda descriptor: '2026-11-02'):
        await _collect(flow.run_wecom_business(execution, {'kind': 'daily'}, adapter))
    assert len(llm.calls) == 3  # 2 页分组 + 1 次合成
    assert saved[0]['overview'] == '合成'
    # 中间结论不被硬截断（P2-16）：digest 完整进入合成输入
    assert '组1' in llm.calls[2]['user'] and '组2' in llm.calls[2]['user']


@pytest.mark.asyncio
async def test_cancel_mid_flow_follows_runner_protocol():
    pages = [_page([_day_message(1)])]
    adapter = FakeAdapter({'ws1.cancel': pages})
    execution = FakeExecution(adapter, FakeLLM([]))
    execution.control.command_value = 'cancel'
    events = await _collect(flow.run_wecom_business(
        execution, {'kind': 'backfill', 'session': 'ws1.cancel'}, adapter))
    assert execution.state.outcome == Outcome.CANCELLED
    assert 'cancelled' in events


@pytest.mark.asyncio
async def test_invalid_descriptor_kind_rejected():
    adapter = FakeAdapter({})
    execution = FakeExecution(adapter, FakeLLM([]))
    with pytest.raises(flow.WecomBusinessFlowError):
        await _collect(flow.run_wecom_business(execution, {'kind': 'unknown'}, adapter))


@pytest.mark.asyncio
async def test_summary_parser_rejects_non_json_and_missing_structure():
    assert flow._parse_summary_json('这不是 JSON') is None
    assert flow._parse_summary_json('前置噪音 ' + _summary('ok') + ' 后置') is None
    assert flow._parse_summary_json('{"overview":"ok"}') is None
    assert flow._parse_summary_json(_digest('group result')) is None
    assert flow._parse_summary_json(_digest('group result'), chunk=True)['digest'] == 'group result'
    assert flow._parse_summary_json(_summary('ok'))['overview'] == 'ok'


def test_summary_contract_keeps_only_actionable_followups_and_observed_anomalies():
    """新报告不再要求分类计数；无证据或旧结构不能冒充成功日报。"""
    concise = _summary('孙晨咨询优惠和女鞋，确认两双并获报价。',
                       pending_human=[{'detail': '确认颜色及收件方式。', 'evidence': [1980, 1981]}],
                       anomalies=[{'detail': '欢迎语重复、截断。', 'evidence': [1970]}])
    parsed = flow._parse_summary_json(concise)
    assert parsed['pending_human'][0]['evidence'] == [1980, 1981]
    assert parsed['anomalies'][0]['detail'] == '欢迎语重复、截断。'
    assert flow._parse_summary_json(_summary('ok', anomalies=[{'detail': '缺证据'}])) is None
    legacy = json.dumps({'overview': '旧报告', 'categories': [], 'handled': [],
                         'pending_human': [], 'noteworthy': []}, ensure_ascii=False)
    assert flow._parse_summary_json(legacy) is None


def test_render_message_projection_keeps_required_facts():
    line = flow._render_message(_day_message(3, '你好'), 'Asia/Shanghai')
    assert 'msg_id=3' in line and '张三' in line and '/wechat' in line and '你好' in line
    attachment = dict(_day_message(4), kind='image',
                      attachment={'meta': {'name': '截图.png', 'size_bytes': 2048}})
    line2 = flow._render_message(attachment, 'Asia/Shanghai')
    assert '截图.png' in line2 and '仅元数据未读内容' in line2
    background = flow._render_message(_background_message(5), 'Asia/Shanghai', background=True)
    assert background.startswith('[背景]')


@pytest.mark.asyncio
async def test_long_path_synthesis_includes_background_feed():
    """评审二 P2-9：长短路径同一背景理解——长路径背景正文以提要进入合成输入。"""
    long_text = '长' * 9000
    pages = [_page([_background_message(1), _day_message(2, long_text)],
                   has_more=True, cursor='wd1.next'),
             _page([_day_message(3, long_text)])]
    adapter = FakeAdapter({'ws1.bg': pages})
    llm = FakeLLM([_digest('组1'), _digest('组2'), _summary('合成')])
    execution = FakeExecution(adapter, llm)
    with patch.object(flow, '_resolve_conversations',
                      lambda e, d: [_conversation(9, 'ws1.bg')]), \
         patch.object(flow, '_save_report', _async_save(lambda *a: None)), \
         patch.object(flow, '_descriptor_date', lambda descriptor: '2026-11-02'):
        await _collect(flow.run_wecom_business(execution, {'kind': 'daily'}, adapter))
    synthesis_input = llm.calls[-1]['user']
    assert '[背景]' in synthesis_input and '背景正文' not in ''  # 背景提要进入合成
    assert 'msg_id=1' in synthesis_input  # 背景消息原 ID 保留
    assert len(synthesis_input) <= flow.SYNTHESIS_INPUT_CHAR_LIMIT + len('长' * 9000) * 0  # 有界


@pytest.mark.asyncio
async def test_oversized_digest_compressed_before_synthesis():
    """评审二 P2-10：单条中间结论超预算 → 先压缩；合成输入不超预算。"""
    long_text = '长' * 9000
    pages = [_page([_day_message(1, long_text)], has_more=True, cursor='wd1.n'),
             _page([_day_message(2, long_text)])]
    adapter = FakeAdapter({'ws1.big': pages})
    oversized_digest = _digest('巨' * 13000)
    llm = FakeLLM([oversized_digest, oversized_digest,
                   _digest('已压缩'), _digest('已压缩'),
                   _summary('合成')])
    execution = FakeExecution(adapter, llm)
    saved = []
    with patch.object(flow, '_resolve_conversations',
                      lambda e, d: [_conversation(1, 'ws1.big')]), \
         patch.object(flow, '_save_report',
                      _async_save(lambda e, c, d, a, s, r: saved.append(a['summary']))), \
         patch.object(flow, '_descriptor_date', lambda descriptor: '2026-11-02'):
        await _collect(flow.run_wecom_business(execution, {'kind': 'daily'}, adapter))
    assert saved and saved[0]['overview'] == '合成'
    synthesis_input = llm.calls[-1]['user']
    assert len(synthesis_input) <= flow.SYNTHESIS_INPUT_CHAR_LIMIT  # 构造性受控
    assert '压缩' in llm.calls[2]['user']  # 走了压缩步


@pytest.mark.asyncio
async def test_uncompressible_digest_reports_gap_not_fake():
    """压缩后仍超预算 → 如实报内容缺口失败（保留旧日报），不切片伪装。"""
    long_text = '长' * 9000
    pages = [_page([_day_message(1, long_text)], has_more=True, cursor='wd1.n'),
             _page([_day_message(2, long_text)])]
    adapter = FakeAdapter({'ws1.huge': pages})
    oversized_digest = _digest('巨' * 13000)
    llm = FakeLLM([oversized_digest, oversized_digest,
                   oversized_digest, oversized_digest])  # 压缩步也不缩小
    execution = FakeExecution(adapter, llm)
    saved = []
    with patch.object(flow, '_resolve_conversations',
                      lambda e, d: [_conversation(1, 'ws1.huge')]), \
         patch.object(flow, '_save_report',
                      _async_save(lambda e, c, d, a, s, r: saved.append(c['id']))), \
         patch.object(flow, '_descriptor_date', lambda descriptor: '2026-11-02'):
        with pytest.raises(flow.WecomBusinessFlowError) as error:
            await _collect(flow.run_wecom_business(execution, {'kind': 'daily'}, adapter))
    # 单会话缺口进逐会话收集 → 唯一会话失败时终态为全部失败（与快照缺失同契约）
    assert error.value.code == 'WECOM_DAILY_ALL_FAILED'
    assert 'WECOM_SUMMARY_INPUT_OVERSIZE' in str(error.value)
    assert saved == []  # 不保存、不伪装完整


@pytest.mark.asyncio
async def test_all_failed_persists_daily_result_before_raising():
    """评审三 P1-4：全部失败也是执行事实——先落 checkpoint 再终态失败。"""
    adapter = FakeAdapter({'ws1.bad': []})
    execution = FakeExecution(adapter, FakeLLM([]))
    saved = []
    with patch.object(flow, '_resolve_conversations',
                      lambda e, d: [_conversation(1, 'ws1.bad', '失败群')]),          patch.object(flow, '_save_report',
                      _async_save(lambda e, c, d, a, s, r: saved.append(c['id']))),          patch.object(flow, '_descriptor_date', lambda descriptor: '2026-11-02'):
        with pytest.raises(flow.WecomBusinessFlowError) as error:
            await _collect(flow.run_wecom_business(
                execution, {'kind': 'daily', 'task_id': 'sched_x'}, adapter))
    assert error.value.code == 'WECOM_DAILY_ALL_FAILED'
    result = execution.state.resources['wecom_daily_result']
    assert result['completed'] == []
    assert result['failed'] == [{'conversation_id': 1, 'name': '失败群',
                                 'code': 'TARGET_NOT_FOUND'}]
    assert result['scope_conversation_ids'] == [1]
    assert result['task_id'] == 'sched_x'
    assert 'daily_result' in execution.control.saved  # checkpoint 已持久化
    assert saved == []


@pytest.mark.asyncio
async def test_stalled_merge_terminates_with_bounded_calls():
    """评审三 P1-1：归并输出与初始压缩同约束（per-item），模型不缩小时
    调用次数有界、以 OVERSIZE 明确结束，不无限消耗积分。"""
    # 5 组 ~5000 字符中间结论 → 总超 24000 触发归并；归并模型每次仍输出
    # ~10000 字符（超 per-item 6000），触发二次压缩并仍不缩 → OVERSIZE。
    digest_text = '巨' * 4900
    pages = []
    for index in range(5):
        page_messages = [_day_message(index + 1, digest_text)]
        pages.append(_page(page_messages, has_more=index < 4, cursor=f'wd1.{index}'))
    adapter = FakeAdapter({'ws1.stall': pages})
    stalled = _digest('巨' * 9900)
    llm = FakeLLM([stalled] * 40)  # 任何一步都返回同样不缩小的输出
    execution = FakeExecution(adapter, llm)
    with patch.object(flow, '_resolve_conversations',
                      lambda e, d: [_conversation(1, 'ws1.stall')]),          patch.object(flow, '_save_report',
                      _async_save(lambda e, c, d, a, s, r: saved.append(c['id'])) if False else
                      _async_save(lambda e, c, d, a, s, r: None)),          patch.object(flow, '_descriptor_date', lambda descriptor: '2026-11-02'):
        with pytest.raises(flow.WecomBusinessFlowError) as error:
            await _collect(flow.run_wecom_business(execution, {'kind': 'daily'}, adapter))
    # 单会话缺口经逐会话收集 → 唯一会话失败终态为全部失败（同快照缺失契约）
    assert error.value.code == 'WECOM_DAILY_ALL_FAILED'
    assert 'WECOM_SUMMARY_INPUT_OVERSIZE' in str(error.value)
    assert len(llm.calls) < 30  # 调用次数有界（非无限循环）
