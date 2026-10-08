"""Prepared actual typed/default configuration and disabled original worker."""
import asyncio

import pytest

from .kf_ingress_fixtures import kf_scope, seed_intent

pytestmark = pytest.mark.integration


def test_original_defaultfalse_strict_migration_switch_leaves_pending_intent_and_old_user_history_alone(kf_scope, monkeypatch):
    from pydantic import ValidationError
    from src.channels.wecom_kf.ingress_repository import KfIngressRepository
    from src.channels.wecom_kf.ingress_worker import KfIngressWorker
    from src.config.settings import AgentRunnerWeComKfConfig, create_settings
    scope = kf_scope
    repository = KfIngressRepository(scope.database.connect)
    seed_intent(scope, repository)
    config = AgentRunnerWeComKfConfig()
    assert config.enabled is False
    monkeypatch.delenv('AGENT_RUNNER_WECOM_KF_ENABLED', raising=False)
    assert create_settings().agent_runner.wecom_kf.enabled is False
    with pytest.raises(ValidationError):
        AgentRunnerWeComKfConfig(enabled='false')
    with pytest.raises(ValidationError):
        AgentRunnerWeComKfConfig(lease_seconds=5, heartbeat_seconds=3)
    with pytest.raises(ValidationError):
        AgentRunnerWeComKfConfig(page_limit=1001)
    monkeypatch.setenv('AGENT_RUNNER_WECOM_KF_ENABLED', 'not-a-boolean')
    with pytest.raises(ValueError, match='KF_INGRESS_ENABLED_REQUIRES_TRUE_OR_FALSE'):
        create_settings()
    monkeypatch.setenv('AGENT_RUNNER_WECOM_KF_ENABLED', 'false')
    assert create_settings().agent_runner.wecom_kf.enabled is False
    before_credit = scope.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s', (scope.tenant_id,))[0]['credit_balance']

    async def no_dispatch():
        worker = KfIngressWorker(config, repository, scope.original_client)
        try:
            assert await worker.run_once() is None
            assert not worker._active
        finally:
            await worker.close()

    asyncio.run(no_dispatch())
    row = scope.rows('SELECT * FROM wecom_kf_account_sync WHERE config_id=%s', (scope.config_id,))[0]
    assert row['requested_generation'] == 1 and row['completed_generation'] == row['claim_epoch'] == 0
    assert row['cursor'] == '' and row['worker_id'] is None and row['lease_until'] is None
    assert scope.rows('SELECT 1 FROM wecom_kf_inbox WHERE config_id=%s', (scope.config_id,)) == []
    assert scope.rows('SELECT 1 FROM channel_session_routes WHERE config_id=%s', (scope.config_id,)) == []
    assert scope.rows('SELECT 1 FROM agent_runners WHERE tenant_id=%s', (scope.tenant_id,)) == []
    assert scope.rows('SELECT content FROM channel_messages WHERE session_id=%s', (scope.legacy_sid,)) == [{'content': 'Original fictional history'}]
    assert scope.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s', (scope.tenant_id,))[0]['credit_balance'] == before_credit
    assert not scope.peer.calls

