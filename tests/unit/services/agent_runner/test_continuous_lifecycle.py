"""Continuous policy requires a bound answer; sleep never loses charges or history."""

import copy
from contextlib import contextmanager
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from src.core.agent_engine.contracts import AgentMode, ExecutionState, Identity, ToolCall, ToolFact
from src.core.execution_usage import execution_token_usage
from src.services.agent_runner.contracts import RunnerSubmit, SessionRef, RunnerError
from src.services.agent_runner.continuous import apply_confirmation, confirmation_arguments
from src.services.agent_runner.continuous_history import closed_prefix, compact_facts, archive_and_compact
from src.services.agent_runner.usage_pricing import accumulate_cost, grouped_cost


def state():
    return ExecutionState(Identity('t','u','s'),'r',AgentMode.MASTER,'system',[],initial_len=0)


def test_omitted_mode_preserves_original_immutable_intent():
    request = RunnerSubmit(client_request_id='key',text='check',session=SessionRef(kind='web',session_id='s'))
    assert 'execution_mode' not in request.intent()
    assert request.digest() == request.model_copy(update={'execution_mode':None}).digest()
    assert request.digest() != request.model_copy(update={'execution_mode':'continuous'}).digest()
    with pytest.raises(ValidationError):
        RunnerSubmit(client_request_id='key',text='check',session={'kind':'web','session_id':'s'},execution_mode='bounded')


def confirmation():
    root = state()
    call = ToolCall('ask','ask_user',{'purpose':'confirm_continuous'})
    fact = ToolFact(call,phase='waiting')
    wait = {'wait_id':'w','tool_call_id':'ask','form':{'form_id':'f'},
        'continuous_confirmation':{'version':1,'purpose':'confirm_continuous','execution_id':'r',
            'tool_call_id':'ask','form_id':'f','question_id':'q1','positive_key':'q1o1'}}
    payload = {'target_execution_id':'r','wait_id':'w','form':{'form_id':'f'},
        'answers':[{'question_id':'q1','selected_keys':['q1o1'],'text':None}]}
    row = {'runner_id':'r','source':'chat','session_kind':'web','input':{'text':'unchanged'},'input_digest':'original'}
    return root,fact,wait,{'control_id':'c','payload':payload},row


@pytest.mark.parametrize('variant',['positive','negative','skip','text','ordinary'])
def test_only_bound_positive_structured_answer_enables_continuous(monkeypatch,variant):
    from src.config.settings import settings
    monkeypatch.setattr(settings.agent_runner,'durable_sleep_enabled',True)
    root,fact,wait,control,row = confirmation()
    root.iteration = 9
    root.resources['business_iterations'] = 7
    checkpoint = {}
    original = copy.deepcopy(row)
    if variant=='negative': control['payload']['answers'][0]['selected_keys'] = ['q1o2']
    if variant=='skip': control['payload'].update(skip=True,answers=None)
    if variant=='text': control['payload'].update(answers=None,answer='yes')
    if variant=='ordinary': wait.pop('continuous_confirmation')
    apply_confirmation(row,control,root,{'r':root},checkpoint,wait,fact)
    assert bool(checkpoint.get('continuous')) == (variant=='positive')
    assert row == original
    if variant=='positive':
        assert root.resources['business_iterations']==7
        assert root.resources['continuous_segment_start']==7
        root.resources['continuous_segment_start'] = 2
        apply_confirmation(row,control,root,{'r':root},checkpoint,wait,fact)
        assert root.resources['continuous_segment_start']==2  # Retry cannot grant another segment.


@pytest.mark.parametrize('changed',['wait_id','form','target_execution_id','cancel'])
def test_stale_or_cancelled_confirmation_cannot_grant_policy(monkeypatch,changed):
    from src.config.settings import settings
    monkeypatch.setattr(settings.agent_runner,'durable_sleep_enabled',True)
    root,fact,wait,control,row = confirmation()
    if changed=='cancel': row['cancel_requested']=True
    elif changed=='form': control['payload']['form']={'form_id':'other'}
    else: control['payload'][changed]='other'
    with pytest.raises(RunnerError):
        apply_confirmation(row,control,root,{'r':root},{},wait,fact)


def test_confirmation_options_have_fixed_meaning():
    question = confirmation_arguments()['questions'][0]
    assert question['type']=='single' and question['allow_custom_text'] is False
    assert [item['label'] for item in question['options']]==['持续跟进，直到我停止','只做本次']


def test_cut_cannot_drop_a_declaration_without_its_result():
    messages = [{'role':'assistant','tool_calls':[{'id':'a'},{'id':'b'}]},
        {'role':'tool','tool_call_id':'a'},{'role':'tool','tool_call_id':'b'},
        {'role':'assistant','tool_calls':[{'id':'waiting'}]}]
    assert closed_prefix(messages,2)==0
    assert closed_prefix(messages,4)==3


def test_compaction_keeps_unapplied_and_pending_facts_and_exact_token_totals():
    root = state()
    root.model_calls = [{'call_id':str(i),'execution_id':'r','phase':'completed','applied':True,
        'usage':{'prompt_tokens':10,'completion_tokens':2}} for i in range(400)]
    root.model_calls.append({'call_id':'unapplied','execution_id':'r','phase':'completed','applied':False,
        'usage':{'prompt_tokens':13,'completion_tokens':3},'response':{'content':'actual saved response'}})
    call = ToolCall('pending','agent_sleep',{'seconds':1})
    root.pending=[call]
    root.tools['pending']=ToolFact(call,phase='waiting')
    before = execution_token_usage(root.checkpoint())
    compact_facts(root)
    assert len(root.model_calls)==33
    assert root.model_calls[-1]['response']['content']=='actual saved response'
    assert root.tools['pending'].phase=='waiting'
    assert execution_token_usage(root.checkpoint())==before
    compact_facts(root)
    assert execution_token_usage(root.checkpoint())==before


def test_archived_root_messages_use_monotone_ids_after_each_cut(monkeypatch):
    from src.services.agent_runner import continuous_history as module
    @contextmanager
    def connect():
        yield SimpleNamespace(cursor=lambda:None,commit=lambda:None)
    monkeypatch.setattr(module,'lock_runner',lambda *args:{'status':'running'})
    captured=[]
    monkeypatch.setattr(module,'write_history',lambda cursor,row,result:captured.extend(result['messages']))
    control = SimpleNamespace(attempt=SimpleNamespace(runner_id='r'),
        repository=SimpleNamespace(connection_factory=connect),envelope={},snapshot={})
    root = state()
    root.messages=[{'role':'assistant','content':str(i)} for i in range(100)]
    root.resources.update(continuous_summary='already summarized',continuous_summary_through=52)
    archive_and_compact(control,root)
    assert root.resources['history_offset']==52 and len(root.messages)==48
    root.messages.extend({'role':'assistant','content':str(i)} for i in range(100,200))
    root.resources['continuous_summary_through']=152
    archive_and_compact(control,root)
    assert root.resources['history_offset']==152 and len(root.messages)==48
    assert [message['_history_id'] for message in captured]==['r:history:'+str(i) for i in range(152)]


def receipt(index, *, boundary='main', tiered=False):
    price={'input_price_per_m':3,'output_price_per_m':8,'cached_input_price_per_m':0.3}
    if tiered:
        price['tiered_pricing']=[{'max_input':1000,'input_per_m':1,'output_per_m':3,'cached_input_per_m':0.1},
            {'max_input':10000,'input_per_m':4,'output_per_m':8,'cached_input_per_m':0.4}]
    return {'phase':'observed','owner':'llm','provider':'test','model':'test','billing_boundary':boundary,
        'price_snapshot':{'version':1,'price':price,'usage_factor':100},
        'usage':{'prompt_tokens':500 if index%2 else 2000,'completion_tokens':31,
            'cached_tokens':20,'cache_creation_tokens':10}}


@pytest.mark.parametrize('tiered',[False,True])
def test_incremental_rounding_matches_original_grouped_cost_without_per_poll_rounding(tiered):
    receipts=[receipt(i,tiered=tiered) for i in range(101)]
    receipts.extend(receipt(i,boundary='skill-call:'+str(i),tiered=tiered) for i in range(13))
    previous=None
    for offset in range(0,len(receipts),7):
        cost,totals,breakdown = accumulate_cost(receipts[offset:offset+7],previous)
        previous=breakdown['runner_accumulator']
        expected,expected_totals,_ = grouped_cost(receipts[:offset+7])
        assert cost==expected
        assert totals==expected_totals
    assert len(previous['groups'])==1
    assert len(previous['independent'])==1  # Skill IDs live only in the immutable receipt ledger.
    again,_,_=accumulate_cost([],previous)
    assert again==cost


@pytest.mark.asyncio
async def test_insufficient_balance_pauses_new_work_but_allows_safe_sleep(monkeypatch):
    from src.services.agent_runner import continuous_lifecycle as module
    from src.services.agent_runner.ownership import StopRequested
    row={'public_snapshot':{'usage':{'task_credits':0.23}}}
    monkeypatch.setattr(module.RunnerFinalizer,'settle_active',lambda *args:row)
    monkeypatch.setattr(module,'assert_credit',Mock(side_effect=RunnerError('CREDIT_BLOCKED',402)))
    root=state()
    control=SimpleNamespace(envelope={'continuous':{'version':1,'enabled':True}},state=root,
        repository=SimpleNamespace(connection_factory=None),attempt=None,snapshot={})
    with pytest.raises(StopRequested):
        await module.before_dispatch(control,root)
    assert control.credit_paused is True and root.resources['credit_blocked'] is True
    root.pending=[ToolCall('sleep','agent_sleep',{'seconds':1})]
    await module.before_dispatch(control,root)
    assert control.snapshot['usage']['task_credits']==0.23


@pytest.mark.asyncio
async def test_compaction_keeps_supplemental_rules_and_separate_target_cursors_in_next_model_prompt():
    from src.services.agent_runner.continuous_context import prepare_compaction,context_prompt
    root=state()
    root.messages=[{'role':'assistant','content':'old '+str(i)} for i in range(96)]
    root.messages[0]={'role':'user','content':'只回复重要消息',
        'metadata':{'control_id':'rule','submitted_text':'只回复重要消息'}}
    targets=['group-'+str(index) for index in range(5)]
    for target in targets:
        root.messages.extend([{'role':'assistant','tool_calls':[{'id':target,
            'function':{'name':'read','arguments':'{"target":"'+target+'","cursor":"old"}'}}]},
            {'role':'tool','tool_call_id':target,'content':'{"next_cursor":"cursor-'+target+'"}'}])
    async def summarize(*args,**kwargs): return '持续任务摘要',{'prompt_tokens':5},False
    await prepare_compaction(root,summarize)
    prompt=context_prompt(root)
    assert '只回复重要消息' in prompt
    assert all('cursor-'+target in prompt for target in targets)
    assert len(root.resources['continuous_cursors'])==5
    assert root.resources['continuous_summary_through']==58


@pytest.mark.asyncio
async def test_failed_or_truncated_summary_does_not_authorize_dropping_original_context():
    from src.services.agent_runner.continuous_context import prepare_compaction
    from src.core.agent_engine.contracts import CheckpointFailure
    root=state()
    root.messages=[{'role':'assistant','content':'data '+str(i)} for i in range(100)]
    original=copy.deepcopy(root.messages)
    async def truncated(*args,**kwargs): return 'partial',{},True
    with pytest.raises(CheckpointFailure,match='COMPACTION_FAILED'):
        await prepare_compaction(root,truncated)
    assert root.messages==original
    assert 'continuous_summary_through' not in root.resources


@pytest.mark.asyncio
async def test_park_at_compaction_threshold_never_needs_another_paid_model(monkeypatch):
    from src.services.agent_runner import continuous_lifecycle as module,continuous_context
    from unittest.mock import AsyncMock
    summarizer=AsyncMock(side_effect=AssertionError('park cannot dispatch a paid summary'))
    monkeypatch.setattr(continuous_context,'prepare_compaction',summarizer)
    monkeypatch.setattr(module.RunnerFinalizer,'settle_active',lambda *args:{'public_snapshot':{'usage':{'task_credits':1.25}}})
    root=state()
    root.messages=[{'role':'assistant','content':'still needed '+str(i)} for i in range(100)]
    original=copy.deepcopy(root.messages)
    control=SimpleNamespace(envelope={'continuous':{'version':1,'enabled':True}},state=root,
        repository=SimpleNamespace(connection_factory=None),attempt=None,snapshot={})
    await module.before_park(control,root)
    assert root.messages==original
    assert root.resources['history_version']==1
    summarizer.assert_not_called()


@pytest.mark.parametrize('configured,expected',[(True,True),(False,False),('true',False)])
def test_capabilities_use_frozen_boolean_v1_names(monkeypatch,configured,expected):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.api import agent_runner_web
    monkeypatch.setattr(agent_runner_web,'authorize_capabilities',lambda authorization:None)
    config=SimpleNamespace(web_enabled=True,api_url='http://runner',durable_sleep_enabled=configured)
    app=FastAPI()
    app.include_router(agent_runner_web.create_router(config,SimpleNamespace(token='configured')))
    with TestClient(app) as client:
        result=client.get('/api/chat/runners/capabilities').json()
    assert result['durable_sleep_v1'] is expected
    assert result['continuous_execution_v1'] is expected
    assert 'durable_sleep_supported' not in result and 'continuous_supported' not in result


def test_reused_summary_prompt_sanitizes_credentials_and_preserves_cursor_text():
    from src.memory.mid_term import ContextCompressionService
    service=ContextCompressionService()
    prompt=service._build_summary_prompt('password=private-password',[
        {'role':'tool','content':'api_key=private-api-key next_cursor=opaque-cursor'},
        {'role':'assistant','tool_calls':[{'function':{'name':'read','arguments':'token=private-token'}}]}])
    assert 'private-password' not in prompt and 'private-api-key' not in prompt and 'private-token' not in prompt
    assert 'opaque-cursor' in prompt


@pytest.mark.asyncio
async def test_accelerated_history_exceeds_old_checkpoint_limit_but_compacted_state_stays_bounded(monkeypatch):
    from src.services.agent_runner import continuous_history as module
    from src.services.agent_runner.continuous_context import prepare_compaction,context_prompt
    from src.services.agent_runner.contracts import canonical_json
    from src.config.settings import settings
    @contextmanager
    def connect(): yield SimpleNamespace(cursor=lambda:None,commit=lambda:None)
    monkeypatch.setattr(module,'lock_runner',lambda *args:{'status':'running'})
    archived=[]
    monkeypatch.setattr(module,'write_history',lambda cursor,row,result:archived.extend(message['_history_id'] for message in result['messages']))
    root=state()
    root.messages=[{'role':'user','content':'keep original task'}]
    root.initial_len=1
    root.resources['continuous_instructions']={'user-rule':{'text':'only important messages','attachments':[]}}
    unpruned=copy.deepcopy(root)
    control=SimpleNamespace(attempt=SimpleNamespace(runner_id='r'),
        repository=SimpleNamespace(connection_factory=connect),envelope={},snapshot={})
    payload='business observation '*100
    async def summarize(*args,**kwargs): return 'ongoing task and latest decisions',{'prompt_tokens':3},False
    for index in range(2000):
        call=ToolCall(str(index),'read',{'target':'a'})
        messages=[{'role':'assistant','tool_calls':[call.message_call()]},
            {'role':'tool','tool_call_id':call.id,'content':payload}]
        fact={'call_id':str(index),'execution_id':'r','phase':'completed','applied':True,
            'usage':{'prompt_tokens':10,'completion_tokens':2},'response':{'content':payload}}
        for current in (root,unpruned):
            current.messages.extend(copy.deepcopy(messages))
            current.tools[call.id]=ToolFact(call,phase='completed',result=payload,result_recorded=True)
            current.model_calls.append(copy.deepcopy(fact))
            current.iteration+=1
        if index%50==49:
            await prepare_compaction(root,summarize)
            archive_and_compact(control,root)
    assert len(canonical_json(unpruned.checkpoint()).encode('utf-8'))>settings.agent_runner.limits.checkpoint_bytes
    assert len(canonical_json(root.checkpoint()).encode('utf-8'))<512*1024
    assert len(root.messages)<=97 and len(root.tools)<=32 and len(root.model_calls)<=32
    assert execution_token_usage(root.checkpoint())=={'input':20000,'output':4000,'cached':0}
    assert len(archived)==len(set(archived))
    assert 'only important messages' in context_prompt(root)
