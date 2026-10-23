"""ChannelRunnerAgent.subagent_config 的租户上下文透传回归。

背景（2026-10-21 agent2）：副本实例对话正常但 recap/external_push 不触发——
subagent_config 解析 profile 时未传 tenant_id，副本实例不进全局 registry，
resolve 抛 PROFILE_NOT_FOUND 被 trigger_recap 静默吞掉。本测试固化
tenant_id 透传契约，防止回归。
"""

from unittest.mock import patch

from src.channels.runner_agent import ChannelRunnerAgent


class TestSubagentConfigTenantForwarding:
    def test_forwards_tenant_id_to_profile_resolve(self):
        agent = ChannelRunnerAgent(
            source='wecom_kf', session_id='sess', channel_user_id='u',
            channel_chat_id='c', profile_id='sales-assistant-t1-1upa',
            tenant_id='tenant_t1',
        )
        with patch('src.services.agent_runner.profiles.MainProfileCatalog') as mock_catalog:
            instance = mock_catalog.return_value
            sentinel = object()
            instance.resolve.return_value = (sentinel, 'fingerprint')
            assert agent.subagent_config is sentinel
            instance.resolve.assert_called_once_with(
                'sales-assistant-t1-1upa', tenant_id='tenant_t1',
            )

    def test_without_tenant_id_still_resolves(self):
        agent = ChannelRunnerAgent(
            source='wecom', session_id='sess', channel_user_id='u',
            channel_chat_id=None, profile_id='main',
        )
        with patch('src.services.agent_runner.profiles.MainProfileCatalog') as mock_catalog:
            instance = mock_catalog.return_value
            instance.resolve.return_value = (object(), 'fingerprint')
            agent.subagent_config
            instance.resolve.assert_called_once_with('main', tenant_id=None)
