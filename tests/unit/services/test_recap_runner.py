#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""recap runner 单元测试（docs/subagent/recap-mechanism-design.md）"""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.services.recap.runner import (
    RecapPayload,
    _run_tasks,
    parse_recap_tasks,
    trigger_recap,
)


def _make_payload(round_message_id=101, tenant_id="tenant_x"):
    return RecapPayload(
        tenant_id=tenant_id,
        session_id=f"{tenant_id}_wecom_kf_wk123_wmABC456_pre-sales",
        subagent_name="售前咨询专员",
        round_message_id=round_message_id,
        user_content="客户消息",
        assistant_reply="AI 回复",
    )


def _fake_create_task_close(coro):
    """mock create_task 的 side_effect：显式关闭未调度的 coroutine，避免 GC 泄漏警告"""
    coro.close()
    return MagicMock()


# ============== parse_recap_tasks：配置解析 ==============


class TestParseRecapTasks:
    def test_no_recap_block_returns_empty(self):
        assert parse_recap_tasks(None) == []
        assert parse_recap_tasks({}) == []
        assert parse_recap_tasks("bad") == []

    def test_empty_or_bad_tasks_returns_empty(self):
        assert parse_recap_tasks({"tasks": []}) == []
        assert parse_recap_tasks({"tasks": "x"}) == []
        assert parse_recap_tasks({"tasks": None}) == []

    def test_normal_parse(self):
        tasks = parse_recap_tasks({
            "tasks": [
                {"name": "external_push", "when": "every_round", "enabled": True},
            ]
        })
        assert len(tasks) == 1
        assert tasks[0].name == "external_push"
        assert tasks[0].when == "every_round"
        assert tasks[0].enabled is True

    def test_missing_name_skipped(self):
        tasks = parse_recap_tasks({"tasks": [{"when": "every_round"}, {"name": "a"}]})
        assert [t.name for t in tasks] == ["a"]

    def test_enabled_false_filtered(self):
        tasks = parse_recap_tasks({"tasks": [{"name": "a", "enabled": False}, {"name": "b"}]})
        assert [t.name for t in tasks] == ["b"]

    def test_unsupported_when_filtered(self):
        tasks = parse_recap_tasks({"tasks": [{"name": "a", "when": "session_end"}, {"name": "b"}]})
        assert [t.name for t in tasks] == ["b"]

    def test_non_dict_items_skipped(self):
        tasks = parse_recap_tasks({"tasks": ["junk", {"name": "a"}]})
        assert [t.name for t in tasks] == ["a"]


# ============== trigger_recap：触发入口 ==============


class TestTriggerRecap:
    def test_no_recap_config_no_task(self):
        agent = SimpleNamespace(subagent_config=None)
        with patch("src.services.recap.runner.asyncio.create_task") as mock_create:
            trigger_recap(
                agent=agent, session_id="s", tenant_id="t",
                user_content="u", assistant_reply="a", round_message_id=1,
            )
            mock_create.assert_not_called()

    def test_recap_tasks_dispatched(self):
        agent = SimpleNamespace(subagent_config=SimpleNamespace(
            recap={"tasks": [{"name": "external_push"}]},
            name="售前咨询专员", dir_name="pre-sales",
        ))
        with patch("src.services.recap.runner.asyncio.create_task") as mock_create, \
             patch("src.services.recap.runner._background_tasks"):
            mock_create.side_effect = _fake_create_task_close
            trigger_recap(
                agent=agent, session_id="tenant_x_wecom_kf_kf_u_sa",
                tenant_id="tenant_x",
                user_content="u", assistant_reply="a", round_message_id=5,
            )
            mock_create.assert_called_once()

    def test_round_message_id_missing_skips(self):
        agent = SimpleNamespace(subagent_config=SimpleNamespace(
            recap={"tasks": [{"name": "external_push"}]}, name="n", dir_name="d",
        ))
        with patch("src.services.recap.runner.asyncio.create_task") as mock_create:
            trigger_recap(
                agent=agent, session_id="s", tenant_id="t",
                user_content="u", assistant_reply="a", round_message_id=None,
            )
            mock_create.assert_not_called()

    def test_exception_swallowed(self):
        agent = SimpleNamespace(subagent_config=SimpleNamespace(
            recap={"tasks": [{"name": "external_push"}]}, name="n", dir_name="d",
        ))
        with patch("src.services.recap.runner.parse_recap_tasks", side_effect=RuntimeError("boom")):
            # 不上抛即通过
            trigger_recap(
                agent=agent, session_id="s", tenant_id="t",
                user_content="u", assistant_reply="a", round_message_id=1,
            )


# ============== _run_tasks：分发 / 幂等 / 隔离 ==============


class TestRunTasks:
    def _payload(self):
        return _make_payload()

    def test_unknown_task_name_skipped(self):
        payload = self._payload()
        from src.services.recap.runner import RecapTaskConfig

        with patch("src.services.recap.tasks.RECAP_TASK_ADAPTERS", {}), \
             patch("src.services.recap.runner._system_switch_enabled", return_value=True), \
             patch("src.services.recap.runner.redis_client") as mock_redis:
            mock_redis.make_key.return_value = "k"
            mock_redis.acquire_lock.return_value = True
            asyncio.run(_run_tasks([RecapTaskConfig(name="nope")], payload))

    def test_idempotent_dup_skipped(self):
        payload = self._payload()
        from src.services.recap.runner import RecapTaskConfig

        adapter = MagicMock()
        adapter.execute = MagicMock(side_effect=AssertionError("不应被调用"))

        with patch("src.services.recap.tasks.RECAP_TASK_ADAPTERS", {"external_push": adapter}), \
             patch("src.services.recap.runner._system_switch_enabled", return_value=True), \
             patch("src.services.recap.runner.redis_client") as mock_redis:
            mock_redis.make_key.return_value = "k"
            mock_redis.acquire_lock.return_value = False  # 幂等命中
            asyncio.run(_run_tasks([RecapTaskConfig(name="external_push")], payload))
            adapter.execute.assert_not_called()
            # 键构成校验
            args = mock_redis.make_key.call_args[0]
            assert args[0] == "recap_task"
            assert "tenant_x" in args[1] and "external_push" in args[1] and "101" in args[1]

    def test_contextvar_record_cleared_inside_task(self):
        """任务内清空 SessionRecord ContextVar：后台 LLM 计费走独立落库而非累加已落库的主对话 record；
        调用方请求协程的 ContextVar 不受影响"""
        from src.services.session_record import SessionRecordManager

        payload = self._payload()
        from src.services.recap.runner import RecapTaskConfig

        seen = {}

        async def _probe(_payload):
            seen["inside"] = SessionRecordManager.get_current_record()

        adapter = MagicMock()
        adapter.execute = _probe

        fake_record = object()
        token = SessionRecordManager.set_current_record(fake_record)
        try:
            with patch("src.services.recap.tasks.RECAP_TASK_ADAPTERS", {"external_push": adapter}), \
                 patch("src.services.recap.runner._system_switch_enabled", return_value=True), \
                 patch("src.services.recap.runner.redis_client") as mock_redis:
                mock_redis.make_key.return_value = "k"
                mock_redis.acquire_lock.return_value = True
                asyncio.run(_run_tasks([RecapTaskConfig(name="external_push")], payload))
            outer_after = SessionRecordManager.get_current_record()
        finally:
            SessionRecordManager.reset_current_record(token)

        assert seen["inside"] is None
        assert outer_after is fake_record

    def test_adapter_exception_isolated(self):
        payload = self._payload()
        from src.services.recap.runner import RecapTaskConfig

        ok_calls = []

        async def _ok(_payload):
            ok_calls.append(True)

        bad_adapter = MagicMock()

        async def _boom(_payload):
            raise RuntimeError("adapter failed")

        bad_adapter.execute = _boom
        ok_adapter = MagicMock()
        ok_adapter.execute = _ok

        with patch("src.services.recap.tasks.RECAP_TASK_ADAPTERS",
                   {"bad": bad_adapter, "ok": ok_adapter}), \
             patch("src.services.recap.runner._system_switch_enabled", return_value=True), \
             patch("src.services.recap.runner.redis_client") as mock_redis:
            mock_redis.make_key.return_value = "k"
            mock_redis.acquire_lock.return_value = True
            # 第一个任务失败不影响第二个（不上抛即通过）
            asyncio.run(_run_tasks(
                [RecapTaskConfig(name="bad"), RecapTaskConfig(name="ok")], payload
            ))
            assert len(ok_calls) == 1

    def test_system_switch_off_skips(self):
        payload = self._payload()
        from src.services.recap.runner import RecapTaskConfig

        adapter = MagicMock()
        adapter.execute = MagicMock(side_effect=AssertionError("不应被调用"))

        with patch("src.services.recap.tasks.RECAP_TASK_ADAPTERS", {"external_push": adapter}), \
             patch("src.services.recap.runner._system_switch_enabled", return_value=False), \
             patch("src.services.recap.runner.redis_client") as mock_redis:
            mock_redis.make_key.return_value = "k"
            mock_redis.acquire_lock.return_value = True
            asyncio.run(_run_tasks([RecapTaskConfig(name="external_push")], payload))
            adapter.execute.assert_not_called()
            mock_redis.acquire_lock.assert_not_called()  # 开关关时不占幂等键


# ============== 系统级开关 ==============


class TestSystemSwitch:
    def test_external_push_switch(self):
        from src.services.recap.runner import _system_switch_enabled

        mock_settings = SimpleNamespace(external_push=SimpleNamespace(
            pre_sales=SimpleNamespace(enabled=False)
        ))
        with patch("src.config.settings.settings", mock_settings):
            assert _system_switch_enabled("external_push") is False

        mock_settings_on = SimpleNamespace(external_push=SimpleNamespace(
            pre_sales=SimpleNamespace(enabled=True)
        ))
        with patch("src.config.settings.settings", mock_settings_on):
            assert _system_switch_enabled("external_push") is True

    def test_unknown_task_defaults_on(self):
        from src.services.recap.runner import _system_switch_enabled

        assert _system_switch_enabled("other_task") is True
