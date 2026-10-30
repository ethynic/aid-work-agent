"""Business visibility follows actor devices, without becoming a recovery gate."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.core.agent_engine.contracts import AgentMode
from src.local_tools import catalog
from src.services.agent_runner.runtime.tool_catalog import ToolCatalog
from src.tools.assembly import ToolAssemblyRequest, ToolAssemblyRole, assemble_agent_tools


IDENTITY = SimpleNamespace(tenant_id='tenant', user_id='user', session_id='session',
                          source='chat', session_kind='web')


def bundle(config=None):
    return assemble_agent_tools(ToolAssemblyRequest(
        role=ToolAssemblyRole.SUBAGENT if config else ToolAssemblyRole.MASTER,
        subagent_config=config, plan_manager=object(), skill_registry=object(),
        skill_executor=object(), subagent_registry=object()))


def tool_catalog(config=None):
    tools = bundle(config)
    controls = SimpleNamespace(definitions=lambda **kw: [], usage_guides=lambda: '')
    return ToolCatalog(AgentMode.MASTER, tools.registry, controls,
                       SimpleNamespace(available=lambda: []),
                       configured_tools=config.get_allowed_tools() if config else ())


def device(key, *, device_id='selected', selected=True, status='active'):
    # No heartbeat: known installation remains visible while offline.
    return {'id': device_id, 'selected': selected, 'status': status,
            'capabilities_json': {'providers': [key]}}


def names(tools):
    return {item['name'] for item in tools._get_tools()}


def config(allowed=(), excluded=(), inherit=False):
    return SimpleNamespace(tools={'inherit': inherit}, get_allowed_tools=lambda: list(allowed),
                           get_excluded_tools=lambda: list(excluded))


@pytest.mark.asyncio
async def test_two_installed_providers_project_business_schemas_and_guides_without_internal_steps(monkeypatch):
    tools = tool_catalog()
    lookup = Mock(return_value=[{**device('wecom'), 'capabilities_json': {
        'providers': ['wecom', 'boss-recruiting']}}])
    monkeypatch.setattr('src.local_tools.repository.list_devices', lookup)
    await tools.prime_local_tools(IDENTITY)
    lookup.assert_called_once_with('tenant', 'user')
    actual = names(tools)
    assert set(catalog.agent_tools('wecom')) <= actual
    assert {'boss_filter', 'boss_read_chat', 'boss_jobs_list'} <= actual
    assert not {'wecom_probe', 'wecom_group_poll', 'wecom_send_status',
                'boss_overlay_inspect', 'skill_script_run', 'runtime_recovery_observe'} & actual
    for tool in tools.tool_registry.snapshot().values():
        tool.get_usage_guide = lambda tool=tool: 'GUIDE:' + tool.name
    guides = tools._collect_tool_usage_guides()
    assert 'GUIDE:boss_filter' in guides and 'GUIDE:wecom_read_sessions' in guides
    assert 'GUIDE:wecom_probe' not in guides


@pytest.mark.asyncio
async def test_actor_refresh_removes_uninstalled_provider_but_keeps_executors_and_cloud_tools(monkeypatch):
    tools = tool_catalog()
    lookup = Mock(side_effect=[[device('wecom')], []])
    monkeypatch.setattr('src.local_tools.repository.list_devices', lookup)
    await tools.prime_local_tools(IDENTITY)
    assert 'wecom_read_sessions' in names(tools) and 'boss_filter' not in names(tools)
    await tools.prime_local_tools(SimpleNamespace(tenant_id='other', user_id='other-user'))
    assert 'wecom_read_sessions' not in names(tools)
    assert tools.tool_registry.get_tool('wecom_read_sessions') is not None
    assert {'boss_jobs_list', 'boss_interview_notify'} <= names(tools)
    assert lookup.call_args.args == ('other', 'other-user')


@pytest.mark.asyncio
async def test_unselected_device_is_not_an_implicit_execution_target(monkeypatch):
    tools = tool_catalog()
    monkeypatch.setattr('src.local_tools.repository.list_devices', Mock(return_value=[
        device('wecom', device_id='old', selected=False), device('boss-recruiting')]))
    await tools.prime_local_tools(IDENTITY)
    assert 'boss_filter' in names(tools) and 'wecom_read_sessions' not in names(tools)
    await tools.prime_local_tools(IDENTITY, {'wecom': 'old'})
    assert {'boss_filter', 'wecom_read_sessions'} <= names(tools)


@pytest.mark.asyncio
async def test_revoked_original_device_does_not_fall_back_to_new_selected_device(monkeypatch):
    tools = tool_catalog()
    monkeypatch.setattr('src.local_tools.repository.list_devices', Mock(return_value=[
        device('wecom', device_id='old', selected=False, status='revoked'), device('wecom')]))
    await tools.prime_local_tools(IDENTITY, {'wecom': 'old'})
    assert 'wecom_read_sessions' not in names(tools)
    assert tools.tool_registry.get_tool('wecom_read_sessions') is not None


@pytest.mark.asyncio
async def test_metadata_without_actor_does_not_query_devices(monkeypatch):
    tools = tool_catalog()
    lookup = Mock()
    monkeypatch.setattr('src.local_tools.repository.list_devices', lookup)
    await tools.prime_local_tools(SimpleNamespace(tenant_id=None, user_id=None))
    lookup.assert_not_called()
    assert 'wecom_read_sessions' not in names(tools)


@pytest.mark.asyncio
async def test_temporary_capability_query_error_does_not_kill_unrelated_agent_work(monkeypatch):
    tools = tool_catalog()
    monkeypatch.setattr('src.local_tools.repository.list_devices', Mock(side_effect=RuntimeError('temporary')))
    await tools.prime_local_tools(IDENTITY)
    assert 'wecom_read_sessions' in names(tools)
    assert '可用性未知' in tools._collect_tool_usage_guides()
    monkeypatch.setattr('src.local_tools.repository.list_devices', Mock(return_value=[]))
    await tools.prime_local_tools(IDENTITY)
    assert '可用性未知' not in tools._collect_tool_usage_guides()


@pytest.mark.asyncio
async def test_configured_agent_policy_is_not_widened_by_installation(monkeypatch):
    tools = tool_catalog(config(['wecom_unread_list'], excluded=['wecom_read_sessions']))
    monkeypatch.setattr('src.local_tools.repository.list_devices', Mock(return_value=[device('wecom')]))
    await tools.prime_local_tools(IDENTITY)
    actual = names(tools)
    assert actual == {'wecom_read_session'}
    # The old accepted operation still has an executable for reconciliation.
    assert tools.tool_registry.has_tool('wecom_unread_list')


def test_inherited_business_surface_respects_excluded_legacy_read_aliases():
    tools = bundle(config(excluded=['wecom_group_poll'], inherit=True)).registry
    assert tools.has_tool('boss_filter') and tools.has_tool('wecom_message_send')
    assert not tools.has_tool('wecom_read_session') and not tools.has_tool('wecom_read_sessions')


@pytest.mark.asyncio
async def test_new_catalog_provider_uses_same_assembly_and_visibility_path(monkeypatch):
    from src.local_tools.proxy_tool import LocalToolProxyTool
    class ExampleTool(LocalToolProxyTool):
        name = 'example_business_read'
        provider_key = 'example'
    monkeypatch.setitem(catalog.TRUSTED_PROVIDERS, 'example', {
        'provider_id': 'example.provider', 'tools': ['example_business_read']})
    monkeypatch.setattr('src.local_tools.proxy_tool.LOCAL_PROXY_TOOL_CLASSES', (ExampleTool,))
    tools = tool_catalog()
    monkeypatch.setattr('src.local_tools.repository.list_devices', Mock(return_value=[device('example')]))
    await tools.prime_local_tools(IDENTITY)
    assert 'example_business_read' in names(tools)


@pytest.mark.asyncio
async def test_child_refresh_reuses_existing_domain_pin_without_rebinding_other_providers():
    from src.services.agent_runner.runtime.executor import RuntimeExecution
    from src.services.agent_runner.wecom_device_binding import RESOURCE
    identity = vars(IDENTITY)
    root = SimpleNamespace(durable_owner=True, attempt=SimpleNamespace(runner_id='root'),
        envelope={'execution': {'execution_id': 'root', 'identity': identity, 'resources': {
            RESOURCE: {'version': 2, 'runner_id': 'root', 'identity': identity, 'device_id': 'original'}}}})
    execution = object.__new__(RuntimeExecution)
    execution.control = SimpleNamespace(parent_control=root)
    execution.identity = IDENTITY
    execution.catalog = SimpleNamespace(prime_local_tools=AsyncMock())
    await execution._prime_local_tools()
    execution.catalog.prime_local_tools.assert_awaited_once_with(IDENTITY, {'wecom': 'original'})


@pytest.mark.asyncio
async def test_server_only_upgrade_keeps_boss_visible_for_old_provider_id(monkeypatch):
    tools = tool_catalog()
    legacy = {**device('unused'), 'capabilities_json': {'provider_id': 'ai.aidwork.boss-recruiting'}}
    monkeypatch.setattr('src.local_tools.repository.list_devices', Mock(return_value=[legacy]))
    await tools.prime_local_tools(IDENTITY)
    assert set(catalog.agent_tools('boss-recruiting')) <= names(tools)
    assert 'wecom_read_sessions' not in names(tools)


@pytest.mark.asyncio
@pytest.mark.parametrize('devices', [[], [device('wecom')], [
    {**device('unused'), 'capabilities_json': {}}]])
async def test_explicit_legacy_profile_tools_survive_missing_capability_metadata(monkeypatch, devices):
    tools = tool_catalog(config(['boss_filter', 'boss_send_to', 'boss_read_chat', 'boss_jobs_list'],
                                excluded=['boss_filter']))
    monkeypatch.setattr('src.local_tools.repository.list_devices', Mock(return_value=devices))
    await tools.prime_local_tools(IDENTITY)
    assert names(tools) == {'boss_send_to', 'boss_read_chat', 'boss_jobs_list'}
    assert not tools.tool_registry.has_tool('boss_filter')


@pytest.mark.asyncio
async def test_production_execution_uses_real_recruiting_profile_for_legacy_visibility(monkeypatch, tmp_path):
    from pathlib import Path
    from src.core.agent_engine.contracts import Identity
    from src.core.skill_registry import SkillRegistry
    from src.services.agent_runner.runtime.executor import RuntimeExecution
    from src.services.agent_runner.runtime.profile import RuntimeResources
    from src.subagents.loader import SubagentLoader
    profile = SubagentLoader().parse_subagent_md(
        Path(__file__).resolve().parents[3] / 'subagents/recruiting-operator/SUBAGENT.md')
    # Keep the production profile/assembly path, without initializing unrelated
    # tenant skill tables during this device-visibility regression.
    empty_skills = SkillRegistry()
    monkeypatch.setattr('src.services.agent_runner.runtime.profile.cached_skill_registry',
                        lambda allowed: empty_skills)
    resources = RuntimeResources(is_master=False, subagent_config=profile,
        tenant_id='tenant', user_id='user', session_id='session', resource_directory=tmp_path)
    runtime = RuntimeExecution(resources, Identity('tenant', 'user', 'session'))
    monkeypatch.setattr('src.local_tools.repository.list_devices', Mock(return_value=[]))
    await runtime._prime_local_tools()
    assert set(catalog.agent_tools('boss-recruiting')) <= names(runtime.catalog)
    assert 'boss_overlay_inspect' not in names(runtime.catalog)
