from copy import deepcopy
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.agent_engine.contracts import AgentMode, CheckpointFailure, ExecutionState, Identity, ToolCall, ToolFact
from src.services.agent_runner.browser_recovery import ApplicationToolRecovery
from src.services.agent_runner.contracts import RunnerError
from src.services.agent_runner.recovery import RecoveryCoordinator
from src.services.agent_runner.runtime_manifest import configuration_fingerprint
from src.services.agent_runner.runtime.tools import ToolDispatcher


@pytest.mark.asyncio
@pytest.mark.parametrize('location', ['same-checkpoint','archived-tool-fact','archived-child'])
async def test_resolved_email_effect_blocks_equivalent_smtp_send_and_allows_independent_mail(location):
    from src.core.agent_engine.contracts import Outcome
    from src.services.agent_runner.continuous_history import KEEP_FACTS, compact_facts
    from src.services.agent_runner.runtime.child import ChildControl
    from src.services.agent_runner.server_recovery_reply import apply_reply
    from src.tools.email.email_tool import EmailProcessTool
    from src.tools.executor import ToolExecutor
    from src.tools.registry import ToolRegistry

    original = ToolCall('original-email','email_process',{'action':'send',
        'to':'first@example.invalid, second@example.invalid','cc':'third@example.invalid',
        'subject':'Fictional delivery','body':'Fictional body'})
    _,row,_ = recovery_fixture(original)
    state = ExecutionState.restore(row['checkpoint']['execution'])
    fact = state.tools[original.id]
    fact.phase = 'waiting'
    wait = {'wait_id':'wait','recovery_reply':{'purpose':'external_effect','choices':['skipped']}}
    apply_reply({'attempt':1},{'control_id':'control','payload':{'recovery_reply':{'decision':'skipped'}}},
                state,wait,fact)
    control = SimpleNamespace(durable_owner=True,attempt=SimpleNamespace(runner_id=state.execution_id,number=2),
        authorize_dispatch=AsyncMock())
    root_control = control
    root = None
    if location == 'archived-child':
        root = ExecutionState(state.identity,state.execution_id,AgentMode.MASTER,'fixture',[])
        state.execution_id = 'owned-child'
        control = ChildControl(root,root_control,'delegation')
    registry = ToolRegistry()
    tool = EmailProcessTool()
    smtp = AsyncMock(return_value={'success':True})
    tool._execute_send = smtp
    tool._resolve_user_email = lambda: object()
    registry.register(tool)
    dispatcher = ToolDispatcher(catalog=SimpleNamespace(_get_tools=registry.get_tool_definitions),
        registry=registry,executor=ToolExecutor(registry),control_tools=SimpleNamespace(
            controls={},context=SimpleNamespace(derive=lambda **_: None)),
        local=None,skills=SimpleNamespace(skill_registry=SimpleNamespace(get=lambda _: None)),
        plan_manager=SimpleNamespace(get_plan=lambda _: None,get_next_pending_task=lambda _: None),
        subagent_config=None,control=control)
    result = await dispatcher.recover(state,original)
    fact.phase, fact.result, fact.result_recorded = 'completed',result.content,True
    state.pending = []
    if root is not None:
        delegation = ToolCall('delegation','delegate_to_subagent',{})
        state.outcome = Outcome.COMPLETED
        root.tools[delegation.id] = ToolFact(delegation,phase='completed',result_recorded=True)
        root.children[delegation.id] = {'checkpoint':state.checkpoint()}
        state = root
        dispatcher.control = root_control
    if location != 'same-checkpoint':
        for number in range(KEEP_FACTS):
            call = ToolCall(f'later-{number}','knowledge_file_search',{})
            state.tools[call.id] = ToolFact(call,phase='completed',result_recorded=True)
        compact_facts(state)
        state = ExecutionState.restore(state.checkpoint())
        assert original.id not in state.tools
        if root is not None:
            assert not state.children
        assert original.arguments['body'] not in str(state.resources)

    same = ToolCall('model-repeat','email_process',{**original.arguments,'action':' SEND ',
        'to':['first@example.invalid',' second@example.invalid '],'cc':['third@example.invalid']})
    repeated = [item async for item in dispatcher.dispatch(state,same)][-1]
    smtp.assert_not_awaited()
    assert repeated.content['recovery']['decision'] == 'skipped'
    new_mail = ToolCall('different-mail','email_process',{**same.arguments,'body':'Another fictional message'})
    assert [item async for item in dispatcher.dispatch(state,new_mail)][-1].success
    smtp.assert_awaited_once()


@pytest.mark.asyncio
async def test_http_resolution_normalizes_the_sent_request_and_omits_credentials_in_question():
    from src.services.agent_runner.server_recovery_reply import (
        previously_resolved_effect, remember_resolved_effect, request_external_check,
    )

    original = ToolCall('request','http_api',{'method':'post',
        'url':'https://hidden-user:hidden-pass@api.example.invalid/hidden-path?token=hidden-query',
        'headers':'{"Authorization": "Bearer hidden-header"}','body':'{"item": "hidden-body", "count": 1}'})
    _,row,_ = recovery_fixture(original)
    state = ExecutionState.restore(row['checkpoint']['execution'])
    attempt = SimpleNamespace(runner_id=state.execution_id,number=2)
    wait = (await request_external_check(state,original,attempt,'HTTP API')).wait
    assert 'HTTP POST' in wait['question'] and 'api.example.invalid' in wait['question']
    assert 'hidden-' not in str(wait)
    # A fresh attempt keeps the reference to the original operation, while
    # another request on the same host gets its own reference.
    reference = re.search(r'编号 ([0-9a-f]{10})',wait['question'])
    assert reference
    later_wait = (await request_external_check(state,original,SimpleNamespace(
        runner_id=state.execution_id,number=3),'HTTP API')).wait
    assert reference[0] in later_wait['question']
    another = ToolCall('untrusted-call-id','http_api',original.arguments)
    another_wait = (await request_external_check(state,another,attempt,'HTTP API')).wait
    assert reference[0] not in another_wait['question']
    assert 'untrusted-call-id' not in another_wait['question']
    remember_resolved_effect(state,original,'skipped')
    later = ToolCall('another-call-id','http_api',{**original.arguments,'method':'POST',
        'body':{'count':1,'item':'hidden-body'},'headers':{'Authorization':'Bearer hidden-header'},'timeout':10})
    assert previously_resolved_effect(state,later).content['recovery']['decision'] == 'skipped'
    assert previously_resolved_effect(state,ToolCall('new','http_api',{
        **later.arguments,'body':{'count':2,'item':'hidden-body'}})) is None
    assert 'hidden-' not in str(state.resources['server_resolved_effects'])


@pytest.mark.asyncio
@pytest.mark.parametrize('shape', ['query-params','replace-query','multi-headers','restore-header-order'])
async def test_http_guard_uses_effective_request_as_sent_by_httpx(monkeypatch,shape):
    import json
    import httpx
    from src.services.agent_runner.server_recovery_reply import remember_resolved_effect
    from src.tools.executor import ToolExecutor
    from src.tools.network.http_api import HttpApiTool
    from src.tools.registry import ToolRegistry

    requests = []

    async def receive(request):
        requests.append((request.url,request.headers.get_list('x-targets')))
        return httpx.Response(200,json={'received':True})

    client = httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs: client(
        **kwargs,transport=httpx.MockTransport(receive)))
    host = 'https://api.example.invalid'
    target = host+'/submit'
    if shape in {'multi-headers','restore-header-order'}:
        original_args = {'method':'POST','url':target,'headers':{'x-targets':'a','X-Targets':'b'}}
        equivalent_args = {**original_args,'headers':{'X-TARGETS':'a','X-Targets':'b'}}
        distinct_args = {**original_args,'headers':{'x-targets':'b'}}
        delivered = (httpx.URL(target),['a','b'])
        independent = (httpx.URL(target),['b'])
    else:
        original_args = {'method':'POST','url':target+'?mode=test'}
        equivalent_args = {'method':'POST','url':target+('?obsolete=ignored' if shape=='replace-query' else ''),
                           'query_params':{'mode':'test'}}
        distinct_args = {**equivalent_args,'query_params':{'mode':'live'}}
        delivered = (httpx.URL(target+'?mode=test'),[])
        independent = (httpx.URL(target+'?mode=live'),[])
    original = ToolCall('original','http_api',original_args)
    equivalent = ToolCall('duplicate','http_api',equivalent_args)
    distinct = ToolCall('different','http_api',distinct_args)
    _,row,_ = recovery_fixture(original)
    state = ExecutionState.restore(row['checkpoint']['execution'])
    registry = ToolRegistry()
    registry.register(HttpApiTool())
    dispatcher = ToolDispatcher(catalog=SimpleNamespace(_get_tools=registry.get_tool_definitions),
        registry=registry,executor=ToolExecutor(registry),control_tools=SimpleNamespace(
            controls={},context=SimpleNamespace(derive=lambda **_: None)),
        local=None,skills=SimpleNamespace(skill_registry=SimpleNamespace(get=lambda _: None)),
        plan_manager=SimpleNamespace(get_plan=lambda _: None,get_next_pending_task=lambda _: None),
        subagent_config=None,control=SimpleNamespace(durable_owner=True,
            attempt=SimpleNamespace(runner_id=state.execution_id,number=2),authorize_dispatch=AsyncMock()))

    # Establish request identity with the production tool and actual HTTPX.
    tool = registry.get_tool(original.name)
    assert (await tool.execute(**original.arguments))['success']
    assert (await tool.execute(**equivalent.arguments))['success']
    assert requests == [delivered]*2
    requests.clear()

    if shape == 'restore-header-order':
        state.messages.append({'role':'assistant','tool_calls':[original.message_call()]})
        state = ExecutionState.restore(json.loads(json.dumps(state.checkpoint(),sort_keys=True)))
        original = state.tools[original.id].call
        assert list(original.arguments['headers']) == ['X-Targets','x-targets']
        assert [item async for item in dispatcher.dispatch(state,original)][-1].success
        assert requests == [delivered]
        requests.clear()
    if shape == 'restore-header-order':
        state.resources['server_recovery_replies'] = {original.id:{'decision':'skipped'}}
        assert (await dispatcher.recover(state,original)).content['recovery']['decision'] == 'skipped'
    else:
        remember_resolved_effect(state,original,'skipped')
    blocked = [item async for item in dispatcher.dispatch(state,equivalent)][-1]
    assert not requests and blocked.content['recovery']['decision'] == 'skipped'
    assert [item async for item in dispatcher.dispatch(state,distinct)][-1].success
    assert requests == [independent]
    if shape == 'restore-header-order':
        # Same case-sensitive keys, different outbound value order. JSONB
        # sorts both objects identically but this interrupted call is distinct.
        reversed_headers = ToolCall('independent-order','http_api',{
            **original_args,'headers':{'X-Targets':'b','x-targets':'a'}})
        state.tools[reversed_headers.id] = ToolFact(reversed_headers,phase='dispatching')
        state.messages.append({'role':'assistant','tool_calls':[reversed_headers.message_call()]})
        state = ExecutionState.restore(json.loads(json.dumps(state.checkpoint(),sort_keys=True)))
        restored = state.tools[reversed_headers.id].call
        assert (await dispatcher.recover(state,restored)).wait['kind'] == 'clarification'
        assert requests == [independent]


@pytest.mark.asyncio
async def test_email_verification_question_identifies_recipient_and_subject_without_body():
    from src.services.agent_runner.server_recovery_reply import request_external_check

    call = ToolCall('mail','email_process',{'action':' SEND ','to':'Alice <alice@example.invalid>',
        'subject':'Quotation for tomorrow','body':'fictional private body'})
    _,row,_ = recovery_fixture(call)
    state = ExecutionState.restore(row['checkpoint']['execution'])
    attempt = SimpleNamespace(runner_id=state.execution_id,number=2)
    wait = (await request_external_check(state,call,attempt,'邮件处理')).wait
    assert 'alice@example.invalid' in wait['question'] and 'Quotation for tomorrow' in wait['question']
    assert call.arguments['body'] not in str(wait)


def recovery_fixture(call, workspace=None):
    identity = Identity('fixture-tenant', 'fixture-user', 'fixture-session')
    state = ExecutionState(identity, 'fixture-root', AgentMode.MASTER, 'fixture', [], profile_id='main')
    state.resources['configuration_fingerprint'] = configuration_fingerprint('fixture-main')
    if workspace:
        state.resources['workspace'] = str(workspace)
    state.tools['saved'] = ToolFact(ToolCall('saved', 'knowledge_file_search', {'file_name': 'old'}),
        phase='completed', result={'count': 1}, result_recorded=True)
    state.tools[call.id] = ToolFact(call, phase='dispatching')
    state.pending = [call]
    row = {'profile_id': 'main', 'profile_fingerprint': 'fixture-main', 'runner_id': state.execution_id,
           'status': 'interrupted', 'checkpoint': {'execution': state.checkpoint()}}
    authorizer = SimpleNamespace(authorize_persisted=lambda row: SimpleNamespace(identity=identity),
                                 assert_credit=lambda principal: None)
    profiles = SimpleNamespace(resolve=lambda profile, tenant=None: (None, 'fixture-main'))
    recovery = RecoveryCoordinator(authorizer, profiles, tool_recovery=ApplicationToolRecovery(None, None, profiles))
    return recovery, row, {'control_id': 'fixture-resume', 'action': 'resume', 'payload': {}}


def quotation_command(source):
    return (f'OUT=/app/storage/tmp/quoteprice_$(date +%s) && mkdir -p $OUT && echo "OUT=$OUT"'
            f' && cd /app/src/skills/quotation-pricing-1.0.0 && python scripts/parse_quotation.py "{source}"'
            ' --json $OUT/items_extracted.json --project "项目名"'
            ' && echo "=====JSON HEAD=====" && head -c 3000 $OUT/items_extracted.json')


@pytest.mark.parametrize('quote', [False, True])
def test_interrupted_repeatable_tool_restores_original_pending_call(tmp_path, quote, monkeypatch):
    from src.config.settings import settings
    from src.saas.services.tenant_skill_cache import tenant_skill_cache
    from src.services.agent_runner.server_tool_recovery import QUOTATION_DIRECTORY
    monkeypatch.setattr(settings.skills.master_agent,'allowed',['quotation-pricing'])
    monkeypatch.setattr(tenant_skill_cache,'get_or_load',lambda tenant, root, allowed: {
        'quotation-pricing':SimpleNamespace(dir=QUOTATION_DIRECTORY,version='1.0.0')} if 'quotation-pricing' in allowed else {})
    source = tmp_path / '原始 清单.xlsx'
    source.touch()
    call = (ToolCall('pending', 'skill_execute', {'skill': 'quotation-pricing', 'command': quotation_command(source.as_posix())})
            if quote else ToolCall('pending', 'knowledge_file_search', {'file_name': '报价单'}))
    recovery, row, control = recovery_fixture(call, tmp_path)
    original = deepcopy(row['checkpoint'])
    restored = recovery.prepare(row, control).states[row['runner_id']]
    assert restored.pending == [call]
    assert restored.tools[call.id].phase == 'dispatching'
    assert restored.tools['saved'].result == {'count': 1}
    assert row['checkpoint'] == original


def test_recovery_preflight_rejects_tenant_override_before_claim(tmp_path, monkeypatch):
    from src.saas.services.tenant_skill_cache import tenant_skill_cache
    monkeypatch.setattr(tenant_skill_cache,'get_or_load',lambda *_: {
        'quotation-pricing':SimpleNamespace(dir=tmp_path / 'tenant-override',version='1.0.0')})
    source = tmp_path / 'test.xlsx'
    source.touch()
    call = ToolCall('pending','skill_execute',{'skill':'quotation-pricing',
        'command':quotation_command(source.as_posix())})
    recovery,row,control = recovery_fixture(call,tmp_path)
    with pytest.raises(RunnerError, match='RECOVERY_TOOL_UNAVAILABLE'):
        recovery.prepare(row,control)
    assert row['checkpoint']['execution']['tools'][call.id]['phase'] == 'dispatching'


@pytest.mark.parametrize('name', ['read','grep'])
def test_existing_text_tool_can_reenter_from_its_original_pending_call(name,tmp_path,monkeypatch):
    monkeypatch.delenv('RIPGREP_CONFIG_PATH',raising=False)
    source = str(tmp_path / 'a.txt')
    arguments = {'file_path':source} if name == 'read' else {'path':source,'pattern':'original'}
    call = ToolCall('pending',name,arguments)
    recovery,row,control = recovery_fixture(call)
    restored = recovery.prepare(row,control).states[row['runner_id']]
    assert restored.pending == [call] and restored.tools[call.id].phase == 'dispatching'


@pytest.mark.parametrize('name', ['office','grep-option','grep-config'])
def test_repeatable_text_tool_excludes_office_and_ripgrep_execution_options(name,tmp_path,monkeypatch):
    monkeypatch.delenv('RIPGREP_CONFIG_PATH',raising=False)
    if name == 'office':
        call = ToolCall('pending','read',{'file_path':str(tmp_path/'quote.xlsx')})
    else:
        if name == 'grep-config':
            monkeypatch.setenv('RIPGREP_CONFIG_PATH',str(tmp_path/'rg-config'))
        call = ToolCall('pending','grep',{'path':str(tmp_path),'pattern':'--pre=tool' if name=='grep-option' else 'original'})
    recovery,row,control = recovery_fixture(call)
    with pytest.raises(RunnerError,match='RECOVERY_TOOL_VERIFICATION_REQUIRED'):
        recovery.prepare(row,control)


@pytest.mark.parametrize('name', ['skill_execute', 'session_task_publish'])
def test_original_interrupted_call_can_reenter_worker_to_ask_bound_question(tmp_path, monkeypatch, name):
    from src.services.agent_runner import server_tool_recovery
    monkeypatch.setattr(server_tool_recovery,'quotation_available',lambda *_: True)
    arguments = ({'skill':'quotation-pricing','command':quotation_command((tmp_path / 'missing.xlsx').as_posix())}
                 if name == 'skill_execute' else {'task_id':'7cdd437b-deb7-4e3d-8117-d579a54df689',
                    'expected_version':1,'confirmation_id':'f2cd87ba-f9a4-4839-b84f-8aa9bf4f412b'})
    call = ToolCall('pending',name,arguments)
    recovery,row,control = recovery_fixture(call,tmp_path)
    prepared = recovery.prepare(row,control)
    assert prepared.states[row['runner_id']].tools[call.id].phase == 'dispatching'
    assert prepared.states[row['runner_id']].tools[call.id].result is None


@pytest.mark.parametrize('suffix', [' && touch /tmp/external-effect', ' # changed command', '$(touch /tmp/external-effect)'])
def test_unverified_extra_shell_operations_remain_blocked(tmp_path, suffix):
    source = tmp_path / '原始 清单.xlsx'
    source.touch()
    call = ToolCall('pending', 'skill_execute',
                    {'skill': 'quotation-pricing', 'command': quotation_command(source.as_posix()) + suffix})
    recovery, row, control = recovery_fixture(call, tmp_path)
    with pytest.raises(RunnerError, match='RECOVERY_TOOL_VERIFICATION_REQUIRED'):
        recovery.prepare(row, control)


@pytest.mark.asyncio
async def test_registered_server_skill_with_unverified_command_asks_without_dispatch(tmp_path, monkeypatch):
    from src.services.agent_runner import server_tool_recovery
    from src.tools.skill.skill_execute_tool import SkillExecuteTool
    source = tmp_path / '原始报价.xlsx'
    call = ToolCall('pending','skill_execute',{'skill':'quotation-pricing',
        'command':quotation_command(source.as_posix()) + ' && curl https://example.invalid'})
    skill = SimpleNamespace(name='quotation-pricing', dir=server_tool_recovery.QUOTATION_DIRECTORY)
    monkeypatch.setattr(server_tool_recovery,'recovery_skill',lambda *_: skill)
    recovery,row,control = recovery_fixture(call)
    state = recovery.prepare(row,control).states[row['runner_id']]
    attempt = SimpleNamespace(runner_id=row['runner_id'],number=2)
    dispatcher = ToolDispatcher(catalog=None,registry=SimpleNamespace(get_tool=lambda name: None),
        executor=None,control_tools=SimpleNamespace(controls={'skill_execute':SkillExecuteTool}),
        local=None,skills=SimpleNamespace(skill_registry=SimpleNamespace(get=lambda name: skill)),
        plan_manager=None,subagent_config=None,control=SimpleNamespace(durable_owner=True,attempt=attempt))
    result = await dispatcher.recover(state,call)
    assert result.wait['kind'] == 'clarification'
    assert result.wait['tool_call_id'] == call.id
    assert [option['label'] for option in result.wait['form']['questions'][0]['options']] == [
        '已完成','无法确认，跳过此步骤']
    assert 'curl' not in result.wait['question']
    assert state.tools[call.id].result is None


@pytest.mark.parametrize('project', ['$(touch /tmp/external-effect)', '`touch /tmp/external-effect`',
    'project --user-id replacement', 'project {session_id}', 'project --input /tmp/input'])
def test_skill_recovery_does_not_admit_interpolation_or_normal_command_rewrites(tmp_path, project):
    source = tmp_path / '原始清单.xlsx'
    source.touch()
    call = ToolCall('pending', 'skill_execute', {'skill': 'quotation-pricing',
        'command': quotation_command(source.as_posix()).replace('项目名', project)})
    recovery, row, control = recovery_fixture(call, tmp_path)
    with pytest.raises(RunnerError, match='RECOVERY_TOOL_VERIFICATION_REQUIRED'):
        recovery.prepare(row, control)


@pytest.mark.asyncio
@pytest.mark.parametrize('call_type', ['custom_skill', 'missing_knowledge_tool'])
async def test_repeatable_call_drift_interrupts_instead_of_creating_unanswerable_wait(tmp_path, call_type):
    if call_type == 'custom_skill':
        call = ToolCall('pending','skill_execute',{'skill':'quotation-pricing',
            'command':quotation_command((tmp_path / 'test.xlsx').as_posix())})
        skill = SimpleNamespace(dir=tmp_path / 'tenant-override', version='1.0.0')
    else:
        call = ToolCall('pending','knowledge_file_search',{'file_name':'price list'})
        skill = None
    _,row,_ = recovery_fixture(call)
    state = ExecutionState.restore(row['checkpoint']['execution'])
    dispatcher = ToolDispatcher(catalog=SimpleNamespace(_get_tools=lambda: []),
        registry=SimpleNamespace(get_tool=lambda name: None),executor=None,control_tools=None,
        local=None,skills=SimpleNamespace(skill_registry=SimpleNamespace(get=lambda name: skill)),
        plan_manager=None,subagent_config=None,control=SimpleNamespace(durable_owner=True))

    with pytest.raises(CheckpointFailure, match='RECOVERY_TOOL_UNAVAILABLE') as failure:
        await dispatcher.recover(state,call)
    assert failure.value.public_verification
    assert state.tools[call.id].phase == 'dispatching'
    assert state.waiting is None
