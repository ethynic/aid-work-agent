"""Registered MCP declarations must stay tied to the internal actor's selected Runtime."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import sys
import pytest
from src.config.settings import settings
from src.core.agent_engine import AgentEngine, Outcome
from src.core.agent_engine.contracts import AgentMode, CheckpointFailure, ExecutionState, Identity, ToolCall, ToolFact
from src.core.skill_registry import SkillRegistry
from src.local_tools import repository
from src.local_tools.catalog import WEIXIN_FRIEND_ADD
from src.local_tools.device_policy import device_ready_error
from src.local_tools.lifecycle import current_local_operation, local_operation_scope
from src.local_tools.plugin_manifests import MANIFEST_DIGESTS, PLUGIN_CHANGED_CODE, VERIFIED_MANIFESTS, load_device_tools, sync_plugin_manifests
from src.local_tools.plugin_tool import PLUGIN_BINDINGS, PLUGIN_PHASE, bound_plugin_tool
from src.local_tools.proxy_tool import BossSendToTool
from src.models.subagent import SubagentConfig
from src.services.agent_runner.local_recovery import LocalRecoveryProof
from src.services.agent_runner.runtime.executor import CompatibilityControl, CompatibilityObserver, ModelAdapter, RuntimeExecution
from src.services.agent_runner.runtime.profile import RuntimeResources
from src.services.agent_runner.runtime.tool_catalog import ToolCatalog
from src.services.agent_runner.runtime.tools import ToolDispatcher
from src.tools.context import ExecutionContextFactory
from src.tools.executor import ToolExecutor
from src.tools.registry import ToolRegistry
from tests.fixtures.runtime_plugin_registry import PROVIDER, TOOL, definition, signed_registry


pytestmark = pytest.mark.unit


def make_device(*, selected=False):
    return {'id': str(uuid4()), 'status': 'active', 'selected': selected,
            'last_seen_at': datetime.now(timezone.utc),
            'capabilities_json': {'capabilities': ['tool_declarations_v1'],
                                  'declared_tools': {PROVIDER: [TOOL]}}}


@pytest.fixture
def registrations(tmp_path, monkeypatch):
    path = str(tmp_path / 'trust-roots.json')
    monkeypatch.setattr(settings.agent_runner, 'plugin_trust_roots', path)
    sign = signed_registry(tmp_path)

    def register(device, *tools, declared=None):
        bundle = sign(*tools, declared=declared)
        capabilities = {**device['capabilities_json'], MANIFEST_DIGESTS: {PROVIDER: bundle['envelope']['manifest_digest']}}
        device['capabilities_json'], missing = sync_plugin_manifests(path, device['id'], capabilities, None, {PROVIDER: bundle})
        assert not missing
        return bundle

    return register


def make_runner(*, allow_local_runtime_tools=True, **kwargs):
    registry = ToolRegistry()
    controls = SimpleNamespace(get=lambda name: None, definitions=lambda **kwargs: [],
                               get_display_name=lambda name, args: None)
    catalog = ToolCatalog(AgentMode.STANDALONE, registry, controls, SimpleNamespace(available=lambda: []),
                          allow_local_runtime_tools=allow_local_runtime_tools, **kwargs)
    executor = ToolExecutor(registry)
    dispatcher = ToolDispatcher(catalog=catalog, registry=registry, executor=executor,
        control_tools=None, local=None, skills=None, plan_manager=None, subagent_config=None, control=None)
    return catalog, dispatcher


def test_cached_projection_requires_original_signature_without_worker_canonicalizer(registrations, monkeypatch):
    device = make_device(selected=True)
    expected = definition('target')
    bundle = registrations(device, expected)
    path = settings.agent_runner.plugin_trust_roots
    capabilities = device['capabilities_json']
    declarations = capabilities['declared_tools']
    monkeypatch.setitem(sys.modules, 'rfc8785', None)
    assert load_device_tools(path, capabilities, declarations) == [(PROVIDER, expected)]

    # An old device row could already contain arbitrary values in this key.
    # Even valid envelope bytes and a copied signature cannot authorize modified tools.
    material = capabilities[VERIFIED_MANIFESTS][PROVIDER]
    material['manifest'] = material['manifest'].replace('target', 'modified')
    assert load_device_tools(path, capabilities, declarations) == []
    received, missing = sync_plugin_manifests(path, device['id'], capabilities, capabilities, {PROVIDER: bundle})
    assert missing == [PROVIDER] and VERIFIED_MANIFESTS not in received
    assert received['declared_tools'] == declarations


@pytest.mark.asyncio
async def test_friend_apply_declaration_does_not_make_it_a_model_tool(registrations, monkeypatch):
    device = make_device(selected=True)
    device['capabilities_json']['declared_tools'][PROVIDER].append(WEIXIN_FRIEND_ADD)
    friend = {**definition('identifier', name=WEIXIN_FRIEND_ADD), 'annotations': {'readOnlyHint': False}}
    registrations(device, definition('target'), friend)
    monkeypatch.setattr(repository, 'list_devices', lambda tenant, user: [device])
    catalog, runner = make_runner()
    await catalog.prime_local_tools(Identity('tenant', 'actor', 'conversation'), include_runtime_plugins=True)
    assert [tool['name'] for tool in runner.definitions()] == [TOOL]
    assert not runner.registry.has_tool(WEIXIN_FRIEND_ADD)


@pytest.mark.asyncio
async def test_selected_runtime_controls_model_definitions_and_identity(registrations, monkeypatch):
    device_a, device_b = make_device(selected=True), make_device()
    tool_a, tool_b = definition('first_field'), definition('second_field', '另一台设备的描述')
    registrations(device_a, tool_a, definition('arg', name='internal'), declared={TOOL})
    registrations(device_b, tool_b)
    device_a['capabilities_json']['declared_tools'][PROVIDER].append('internal')
    monkeypatch.setattr(repository, 'list_devices', lambda tenant, user: [device_a, device_b]
                        if (tenant, user) == ('tenant', 'actor') else [])
    catalog, runner = make_runner()
    chat = Identity('tenant', 'actor', 'conversation')
    await catalog.prime_local_tools(chat, include_runtime_plugins=True)
    assert runner.definitions() == [{
        'name': TOOL, 'description': tool_a['description'], 'input_schema': tool_a['inputSchema'],
    }]

    stale_binding = runner.registry.get_tool(TOOL)
    device_a['selected'], device_b['selected'] = False, True
    await catalog.prime_local_tools(chat, include_runtime_plugins=True)
    assert runner.definitions() == [{
        'name': TOOL, 'description': tool_b['description'], 'input_schema': tool_b['inputSchema'],
    }]
    stale_registry = ToolRegistry()
    stale_registry.register(stale_binding)
    context = ExecutionContextFactory.for_agent_call(tenant_id='tenant', user_id='actor',
        session_id='conversation', channel='chat')
    owner = SimpleNamespace(bind_invocation=AsyncMock())
    with local_operation_scope(owner):
        result = await ToolExecutor(stale_registry).execute(TOOL, {'first_field': 'value'}, context=context)
    assert result['code'] == 'DEVICE_UNAVAILABLE'
    assert '发生变化' in result['message']
    owner.bind_invocation.assert_not_awaited()

    for identity in (Identity('tenant', 'actor', 'external', 'wecom', 'channel'),
                     Identity('tenant', 'actor', 'public', 'chat', 'channel'),
                     Identity('tenant', 'other', 'other-conversation')):
        await catalog.prime_local_tools(identity, include_runtime_plugins=True)
        assert runner.definitions() == []

    device_b['capabilities_json']['declared_tools'][PROVIDER] = []
    await catalog.prime_local_tools(chat, include_runtime_plugins=True)
    assert runner.definitions() == []


@pytest.mark.asyncio
async def test_unsigned_or_unregistered_declaration_never_extends_profile(registrations, monkeypatch):
    device = make_device(selected=True)
    monkeypatch.setattr(repository, 'list_devices', lambda tenant, user: [device])
    chat = Identity('tenant', 'actor', 'conversation')
    inherited, runner = make_runner(configured_tools=['boss_send_to'])
    runner.registry.register(BossSendToTool())
    await inherited.prime_local_tools(chat, include_runtime_plugins=True)
    assert [item['name'] for item in runner.definitions()] == ['boss_send_to']

    bundle = registrations(device, definition('target'))
    strict, strict_runner = make_runner(allow_local_runtime_tools=False)
    await strict.prime_local_tools(chat, include_runtime_plugins=True)
    assert strict_runner.definitions() == []

    excluded, excluded_runner = make_runner(excluded_tools=[TOOL])
    await excluded.prime_local_tools(chat, include_runtime_plugins=True)
    assert excluded_runner.definitions() == []

    bundle['manifest']['tools'][0]['description'] = 'Modified after signing'
    device['capabilities_json'], missing = sync_plugin_manifests(settings.agent_runner.plugin_trust_roots,
        device['id'], device['capabilities_json'], None, {PROVIDER: bundle})
    assert missing == [PROVIDER]
    await inherited.prime_local_tools(chat, include_runtime_plugins=True)
    assert [item['name'] for item in runner.definitions()] == ['boss_send_to']


@pytest.mark.asyncio
@pytest.mark.parametrize('durable', [False, True])
async def test_unconfigured_master_keeps_existing_tools_without_plugin(registrations, monkeypatch, tmp_path, durable):
    device = make_device(selected=True)
    device['capabilities_json']['providers'] = ['boss-recruiting', PROVIDER]
    registrations(device, definition('target'))
    monkeypatch.setattr(repository, 'list_devices', lambda tenant, user: [device])
    monkeypatch.setattr('src.services.agent_runner.runtime.profile.cached_skill_registry',
                        lambda allowed: SkillRegistry())
    resources = RuntimeResources(tenant_id='tenant', user_id='actor', session_id='conversation',
                                 resource_directory=tmp_path)
    control = CompatibilityControl()
    control.durable_owner = durable
    runtime = RuntimeExecution(resources, Identity('tenant', 'actor', 'conversation'), control=control)
    await runtime._prime_local_tools()
    names = {item['name'] for item in runtime.catalog._visible_definitions()}
    assert 'boss_jobs_list' in names
    assert TOOL not in names and not resources.tool_registry.has_tool(TOOL)


@pytest.mark.asyncio
@pytest.mark.parametrize('preference', [{}, {'allow_local_runtime_tools': False},
                                       {'allow_local_runtime_tools': 'true'},
                                       {'allow_local_runtime_tools': 1},
                                       {'allow_local_runtime_tools': True}])
async def test_only_opted_in_internal_profile_adds_plugins_to_strict_tool_list(
        preference, registrations, monkeypatch, tmp_path):
    device = make_device(selected=True)
    device['capabilities_json']['providers'] = ['boss-recruiting', PROVIDER]
    blocked = 'sample_mcp_excluded'
    device['capabilities_json']['declared_tools'][PROVIDER].append(blocked)
    registrations(device, definition('target'), definition('target', name=blocked))
    monkeypatch.setattr(repository, 'list_devices', lambda tenant, user: [device])
    monkeypatch.setattr('src.services.agent_runner.runtime.profile.cached_skill_registry',
                        lambda allowed: SkillRegistry())
    config = SubagentConfig(name='在线电商客服', dir_name='after-sales', tools={
        'inherit': False, 'allowed': ['boss_send_to'], 'additional': ['read'], 'excluded': [blocked],
        **preference,
    })
    identity = Identity('tenant', 'actor', 'conversation')

    def make_resources():
        return RuntimeResources(mode=AgentMode.STANDALONE, subagent_config=config, tenant_id='tenant',
                                user_id='actor', session_id='conversation', resource_directory=tmp_path)

    control = CompatibilityControl()
    control.durable_owner = True
    runtime = RuntimeExecution(make_resources(), identity, control=control)
    await runtime._prime_local_tools()
    names = {item['name'] for item in runtime.catalog._visible_definitions()}
    expected = {'boss_send_to', TOOL} if preference.get('allow_local_runtime_tools') is True else {'boss_send_to'}
    assert names == expected

    if preference.get('allow_local_runtime_tools') is True:
        legacy = RuntimeExecution(make_resources(), identity)
        await legacy._prime_local_tools()
        assert TOOL not in {item['name'] for item in legacy.catalog._visible_definitions()}


@pytest.mark.asyncio
async def test_engine_resumes_uncommitted_plugin_call_after_tool_disappears(registrations, monkeypatch):
    device = make_device(selected=True)
    registrations(device, definition('target'))
    monkeypatch.setattr(repository, 'list_devices', lambda tenant, user: [device])
    catalog, runner = make_runner()
    chat = Identity('tenant', 'actor', 'conversation')
    await catalog.prime_local_tools(chat, include_runtime_plugins=True)
    original = ExecutionState(chat, 'runner-id', AgentMode.STANDALONE, '', [])
    call = ToolCall('pending-plugin', TOOL, {'target': 'element-1'})
    original.pending = [call]
    original.tools[call.id] = ToolFact(call)

    class StopAfterDispatchIntent(CompatibilityControl):
        durable_owner = True

        async def save(self, state, boundary):
            if boundary == 'before_tool':
                self.checkpoint = state.checkpoint()
                raise CheckpointFailure('fixture interrupted before enqueue')

    first = StopAfterDispatchIntent()
    runner.control = first
    observer = CompatibilityObserver()
    with pytest.raises(CheckpointFailure, match='fixture interrupted before enqueue'):
        async for _ in AgentEngine().run(original, None, runner, first, observer):
            pass
    restored = ExecutionState.restore(first.checkpoint)
    fact = restored.tools[call.id]
    assert fact.phase == 'dispatching' and fact.invocation_id is None
    assert not restored.resources.get('local_invocations')

    device['selected'] = False
    await catalog.prime_local_tools(chat, include_runtime_plugins=True)
    assert not runner.registry.has_tool(TOOL)
    assert LocalRecoveryProof().can_restore({'runner_id': 'runner-id', 'tenant_id': 'tenant'}, restored, fact)
    model = SimpleNamespace(complete=AsyncMock(return_value={'content': 'Tool failure handled.', 'tool_calls': []}))
    async for _ in AgentEngine().run(restored, model, runner, CompatibilityControl(), observer):
        pass
    assert restored.outcome == Outcome.COMPLETED and restored.waiting is None
    assert fact.result['code'] == 'LOCAL_NOT_DISPATCHED' and fact.result['effect'] == 'none'
    model.complete.assert_awaited_once()


@pytest.mark.asyncio
async def test_saved_model_response_cannot_use_replacement_plugin(registrations, monkeypatch):
    device = make_device(selected=True)
    registrations(device, definition('original'))
    monkeypatch.setattr(repository, 'list_devices', lambda tenant, user: [device])
    catalog, runner = make_runner()
    chat = Identity('tenant', 'actor', 'conversation')
    await catalog.prime_local_tools(chat, include_runtime_plugins=True)
    call = ToolCall('original-plugin', TOOL, {'original': 'original argument'})
    gateway = SimpleNamespace(chat_with_tools=AsyncMock(return_value={'tool_calls': [call.message_call()]}),
                              get_model_name=lambda: 'model', get_provider_name=lambda: 'provider')
    state = ExecutionState(chat, 'runner-id', AgentMode.STANDALONE, '', [])
    model = ModelAdapter(gateway, plugin_bindings=catalog.runtime_plugin_bindings())
    reply = await model.complete(state, 'model-id', runner.definitions())
    assert reply['tool_calls'] == [call.message_call()]
    assert state.resources[PLUGIN_BINDINGS][TOOL][0] == device['id']

    state.pending = [call]
    state.tools[call.id] = ToolFact(call)
    restored = ExecutionState.restore(state.checkpoint())
    registrations(device, definition('replacement'))
    await catalog.prime_local_tools(chat, include_runtime_plugins=True)
    assert runner.definitions()[0]['input_schema']['required'] == ['replacement']
    runner.executor.execute = AsyncMock()
    [terminal] = [event async for event in runner.dispatch(restored, restored.pending[0])]
    assert terminal.content['code'] == PLUGIN_CHANGED_CODE and terminal.content['effect'] == 'none'
    assert terminal.wait is None
    restored.resources.pop(PLUGIN_BINDINGS)
    [unbound] = [event async for event in runner.dispatch(restored, restored.pending[0])]
    assert unbound.content['code'] == 'LOCAL_NOT_DISPATCHED' and unbound.wait is None
    runner.executor.execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('expires', [False, True])
async def test_native_call_is_bound_and_ordinary_runtime_receipt_is_returned(registrations, monkeypatch, expires):
    device = make_device(selected=True)
    registrations(device, definition('target'))
    devices = Mock(return_value=[device])
    monkeypatch.setattr(repository, 'list_devices', devices)
    catalog, runner = make_runner()
    await catalog.prime_local_tools(Identity('tenant', 'actor', 'conversation'), include_runtime_plugins=True)
    assert runner.registry.has_tool(TOOL)

    invocation = {'id': 'invocation-id', 'tenant_id': 'tenant', 'effect': 'none',
                  'state': 'failed', 'error_code': 'BROWSER_CLOSED',
                  'result_json': {'message': '页面已关闭', 'data': {'completed_steps': 1}}}
    monkeypatch.setattr(repository, 'get_invocation', lambda inv_id, tenant: invocation)
    monkeypatch.setattr(repository, 'list_events', lambda inv_id, seq: [])
    args = {'target': 'element-1'}
    sent = []

    async def bind(**request):
        sent.append(request)
        assert device_ready_error(device, TOOL, datetime.now(timezone.utc),
                                  declared_provider=request['declared_provider'],
                                  plugin_manifest_digest=request['plugin_manifest_digest']) is None
        assert device_ready_error(device, TOOL, datetime.now(timezone.utc), declared_provider=PROVIDER,
                                  plugin_manifest_digest='sha256:old') == 'plugin_changed'
        assert device_ready_error({**device, 'capabilities_json': {'capabilities': ['tool_declarations_v1'],
                                  'declared_tools': {'unrelated': [TOOL]}}}, TOOL, datetime.now(timezone.utc),
                                  declared_provider=request['declared_provider']) == 'unsupported'
        return invocation

    if expires:
        invocation.update(state='unknown', effect='unknown', result_json=None, error_code=None)
    owner = SimpleNamespace(saved_phase=AsyncMock(return_value=None), bind_invocation=bind)
    context = ExecutionContextFactory.for_agent_call(tenant_id='tenant', user_id='actor',
        session_id='conversation', channel='chat')
    devices.reset_mock()
    unbound = await runner.executor.execute(TOOL, args, context=context)
    assert unbound['code'] == 'LOCAL_NOT_DISPATCHED' and unbound['effect'] == 'none'
    devices.assert_not_called()
    with local_operation_scope(owner):
        invalid = await runner.executor.execute(TOOL, {'target': ['sensitive-marker']}, context=context)
        assert not invalid['success'] and 'sensitive-marker' not in str(invalid)
        assert sent == []
        result = await runner.executor.execute(TOOL, {**args, '_session_id': 'trusted-session'}, context=context)
    assert sent == [{'branch': PLUGIN_PHASE, 'ordinal': 0, 'tool_name': TOOL, 'device': device,
                     'arguments': args, 'provider_key': PROVIDER, 'deadline_at': None,
                     'declared_provider': PROVIDER,
                     'plugin_manifest_digest': device['capabilities_json'][MANIFEST_DIGESTS][PROVIDER]}]
    assert result['code'] == ('EXECUTION_UNKNOWN' if expires else 'BROWSER_CLOSED')
    if not expires:
        assert result['message'] == '页面已关闭'
        assert result['data'] == {'completed_steps': 1}
    else:
        assert result['message'] == '本机执行结果无法确认'
    assert 'recovery_action' not in result

    device['selected'] = False
    call = ToolCall('call-id', TOOL, args)
    checkpoint = {'tool_call_id': call.id, 'branch': PLUGIN_PHASE, 'ordinal': 0,
                  'request': {'device_id': device['id'], 'provider_key': PROVIDER}, 'invocation': invocation}
    owner.saved_phase.return_value = checkpoint
    state = SimpleNamespace(resources={'local_invocations': {'ref': checkpoint}})
    original = bound_plugin_tool(state, call)
    assert original.device_id == device['id']
    with local_operation_scope(owner):
        assert await original.recover(current_local_operation(), None) == result
    assert len(sent) == 1
