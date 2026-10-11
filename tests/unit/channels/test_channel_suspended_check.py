"""渠道入口 users.status 停用拦截测试：suspended 硬阻断 / 正常放行 / 查询异常 fail-open。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.channels.session import ChannelSessionManager
from src.core import session_queue as session_queue_module


BLOCK_TEXT = "您的账号已被暂停使用，如有疑问请联系管理员。"


def make_manager() -> ChannelSessionManager:
    # 只测 process_and_persist 前置阻断段，不跑真实构造（依赖渠道配置）
    return ChannelSessionManager.__new__(ChannelSessionManager)


@pytest.fixture
def queue(monkeypatch):
    mock = AsyncMock(return_value=SimpleNamespace(
        status="merged", response_text="", downloadable_files=[],
        was_merged=True, merged_input="x"))
    monkeypatch.setattr(session_queue_module.session_queue,
                        "enqueue_and_process", mock)
    return mock


def _patch_userdb(monkeypatch, row):
    userdb = MagicMock()
    userdb.get_by_id.return_value = row
    import src.db.models as db_models
    monkeypatch.setattr(db_models, "UserDB", userdb)
    return userdb


async def _call(manager, send_response, record_service=None, agent_user=None):
    return await manager.process_and_persist(
        session_id="t1_wecom_kf_u1_default",
        tenant_id="t1",
        user_content="你好",
        agent=MagicMock(),
        send_response=send_response,
        agent_user=agent_user,
        record_service=record_service,
    )


class TestSuspendedCheck:
    async def test_suspended_user_blocked(self, monkeypatch, queue):
        _patch_userdb(monkeypatch, {"user_id": "u1", "status": "suspended"})
        send_response = AsyncMock(return_value=True)
        record_service = MagicMock()
        manager = make_manager()

        result = await _call(manager, send_response, record_service,
                             agent_user=SimpleNamespace(user_id="u1"))

        assert result["status"] == "user_suspended"
        send_response.assert_awaited_once()
        assert send_response.await_args.args[0] == BLOCK_TEXT
        assert record_service.skip_save is True
        # 不进执行链
        queue.assert_not_awaited()

    async def test_active_user_passes_through(self, monkeypatch, queue):
        _patch_userdb(monkeypatch, {"user_id": "u1", "status": "active"})
        send_response = AsyncMock(return_value=True)
        manager = make_manager()

        result = await _call(manager, send_response,
                             agent_user=SimpleNamespace(user_id="u1"))

        assert result["status"] == "merged"
        send_response.assert_not_awaited()
        queue.assert_awaited_once()

    async def test_db_error_fail_open(self, monkeypatch, queue):
        userdb = _patch_userdb(monkeypatch, None)
        userdb.get_by_id.side_effect = RuntimeError("db down")
        send_response = AsyncMock(return_value=True)
        manager = make_manager()

        result = await _call(manager, send_response,
                             agent_user=SimpleNamespace(user_id="u1"))

        # fail-open 放行继续处理，不发停用话术
        assert result["status"] == "merged"
        send_response.assert_not_awaited()
        queue.assert_awaited_once()

    async def test_user_not_found_fail_open(self, monkeypatch, queue):
        _patch_userdb(monkeypatch, None)
        send_response = AsyncMock(return_value=True)
        manager = make_manager()

        result = await _call(manager, send_response,
                             agent_user=SimpleNamespace(user_id="u_ghost"))

        assert result["status"] == "merged"
        send_response.assert_not_awaited()

    async def test_no_agent_user_skips_check(self, monkeypatch, queue):
        send_response = AsyncMock(return_value=True)
        manager = make_manager()

        result = await _call(manager, send_response, agent_user=None)

        assert result["status"] == "merged"
        send_response.assert_not_awaited()
