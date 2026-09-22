"""
recap 冷却跳过延迟补推单元测试

覆盖：
1. defer_recap_task：round_message_id 加 -defer 后缀 / payload 键与 zset 成员写入 /
   latest-wins 覆盖 / Redis 异常吞掉返回 False
2. poll_due_deferred_tasks：到期成员派发（task_config 收窄为单任务）/ 未到期不派发 /
   payload 缺失跳过 / 派发后 zset 成员与 payload 键清理
3. external_push_human 冷却跳过分支：按冷却剩余 TTL 登记补推
"""

import time
from unittest.mock import MagicMock, patch

import pytest

from src.services.recap.runner import (
    RECAP_DEFER_BUFFER_SECONDS,
    RecapPayload,
    defer_recap_task,
    poll_due_deferred_tasks,
)


def _make_payload(**overrides) -> RecapPayload:
    base = dict(
        tenant_id="tenant_abc",
        session_id="tenant_abc_wecom_kf_kf1_user1_pre-sales",
        subagent_name="pre-sales",
        round_message_id="msg_123",
        user_content="",
        assistant_reply="",
        record_service=None,
        user_id=None,
        trace_id=None,
        enqueued_at=time.time(),
    )
    base.update(overrides)
    return RecapPayload(**base)


def _make_redis():
    redis = MagicMock()
    redis.make_key.side_effect = lambda prefix, identifier="": (
        f"{prefix}:{identifier}" if identifier else prefix
    )
    redis.is_available.return_value = True
    redis.zscore.return_value = 123.0
    redis.get.return_value = None
    redis.getdel.return_value = None
    redis.ttl.return_value = -2
    return redis


# ============== defer_recap_task ==============

class TestDeferRecapTask:
    def test_defer_writes_payload_and_zset(self):
        """payload 键写入带 -defer 后缀的 payload，zset 成员 score 为到期时间"""
        redis = _make_redis()
        redis.get.return_value = {"round_message_id": "msg_123-defer"}

        with patch("src.services.recap.runner.redis_client", redis):
            ok = defer_recap_task(_make_payload(), "external_push_human", 60)

        assert ok is True
        payload_key, payload_body = redis.set.call_args[0]
        assert "recap_deferred_payload:tenant_abc:" in payload_key
        assert payload_body["round_message_id"] == "msg_123-defer"
        # payload TTL 覆盖补推延迟 + 缓冲 + 冗余
        assert redis.set.call_args[1]["ex"] >= 60 + RECAP_DEFER_BUFFER_SECONDS

        queue_key, mapping = redis.zadd.call_args[0]
        assert queue_key == "recap_deferred_queue"
        (member, due), = mapping.items()
        assert member == "tenant_abc:tenant_abc_wecom_kf_kf1_user1_pre-sales:external_push_human"
        assert due == pytest.approx(time.time() + 60, abs=5)

    def test_defer_latest_wins(self):
        """同会话同任务重复登记时 member/payload 键复用（latest-wins）"""
        redis = _make_redis()
        redis.get.return_value = {"round_message_id": "x-defer"}

        with patch("src.services.recap.runner.redis_client", redis):
            defer_recap_task(_make_payload(round_message_id="m1"), "external_push_human", 60)
            defer_recap_task(_make_payload(round_message_id="m2"), "external_push_human", 120)

        assert redis.set.call_count == 2
        assert redis.zadd.call_count == 2
        assert redis.set.call_args_list[0][0][0] == redis.set.call_args_list[1][0][0]
        assert redis.zadd.call_args_list[0][0][1].keys() == redis.zadd.call_args_list[1][0][1].keys()

    def test_defer_redis_error_returns_false(self):
        """Redis 异常吞掉不上抛（退化为跳过不补推）"""
        redis = _make_redis()
        redis.set.side_effect = RuntimeError("redis down")

        with patch("src.services.recap.runner.redis_client", redis):
            assert defer_recap_task(_make_payload(), "external_push_human", 60) is False

    def test_defer_redis_unavailable_returns_false(self):
        """跨进程队列禁内存降级：Redis 不可用时直接返回 False，不写进程内存"""
        redis = _make_redis()
        redis.is_available.return_value = False

        with patch("src.services.recap.runner.redis_client", redis):
            assert defer_recap_task(_make_payload(), "external_push_human", 60) is False

        redis.set.assert_not_called()
        redis.zadd.assert_not_called()

    def test_defer_zadd_not_enqueued_cleans_payload(self):
        """zadd 失败（zscore 回读 None）时清理 payload 键并返回 False，不谎报入队成功"""
        redis = _make_redis()
        redis.get.return_value = {"round_message_id": "msg_123-defer"}
        redis.zscore.return_value = None

        with patch("src.services.recap.runner.redis_client", redis):
            assert defer_recap_task(_make_payload(), "external_push_human", 60) is False

        redis.delete.assert_called_once()


# ============== poll_due_deferred_tasks ==============

class TestPollDueDeferredTasks:
    @pytest.mark.asyncio
    async def test_due_member_dispatched(self):
        """到期成员派发执行，task_config 收窄为单任务，payload/zset 清理"""
        redis = _make_redis()
        member = "tenant_abc:tenant_abc_wecom_kf_kf1_user1_pre-sales:external_push_human"
        redis.zrangebyscore.return_value = [member]
        payload = _make_payload(round_message_id="msg_123-defer")
        payload.task_config = [
            {"name": "lead_refresh", "when": "every_round", "enabled": True},
            {"name": "external_push_human", "when": "every_round", "enabled": True},
        ]
        redis.getdel.return_value = payload.to_dict()

        with patch("src.services.recap.runner.redis_client", redis), \
                patch("src.services.recap.runner._run_tasks") as mock_run, \
                patch("src.services.recap.runner.asyncio.create_task") as mock_create:
            dispatched = poll_due_deferred_tasks()

        assert dispatched == 1
        assert mock_create.called is True
        tasks_arg, payload_arg = mock_run.call_args[0]
        assert [t.name for t in tasks_arg] == ["external_push_human"]
        assert payload_arg.round_message_id == "msg_123-defer"
        redis.zremrangebyscore.assert_called_once()
        redis.getdel.assert_called_once()

    def test_no_due_members(self):
        redis = _make_redis()
        redis.zrangebyscore.return_value = []

        with patch("src.services.recap.runner.redis_client", redis):
            assert poll_due_deferred_tasks() == 0
        assert redis.zremrangebyscore.called is False

    @pytest.mark.asyncio
    async def test_missing_payload_skipped(self):
        """payload 已过期（getdel 返回 None）时跳过派发，zset 成员仍清理"""
        redis = _make_redis()
        redis.zrangebyscore.return_value = ["tenant_abc:sess:external_push_human"]
        redis.getdel.return_value = None

        with patch("src.services.recap.runner.redis_client", redis), \
                patch("src.services.recap.runner._run_tasks"), \
                patch("src.services.recap.runner.asyncio.create_task") as mock_create:
            assert poll_due_deferred_tasks() == 0
        assert mock_create.called is False
        redis.getdel.assert_called_once()

    def test_redis_error_returns_zero(self):
        redis = _make_redis()
        redis.zrangebyscore.side_effect = RuntimeError("redis down")

        with patch("src.services.recap.runner.redis_client", redis):
            assert poll_due_deferred_tasks() == 0


# ============== external_push_human 冷却跳过补推 ==============

class TestHumanCooldownSkipDefers:
    @pytest.mark.asyncio
    async def test_cooldown_skip_registers_defer(self):
        """冷却占坑失败时按剩余 TTL 登记延迟补推"""
        from src.services.recap.tasks import external_push_human as eph

        payload = _make_payload()
        payload.task_config = [{"name": "external_push_human", "when": "every_round", "enabled": True}]

        with patch.object(eph, "_collect_context") as mock_ctx, \
                patch.object(eph, "_load_tenant_doc") as mock_doc, \
                patch.object(eph, "parse_api_meta") as mock_meta, \
                patch.object(eph, "_strip_excluded_sections", side_effect=lambda d, m, t: d), \
                patch.object(eph, "_get_agent_token", return_value="tok"), \
                patch("src.channels.session.channel_session_manager") as mock_csm, \
                patch.object(eph.redis_client, "acquire_lock", return_value=False), \
                patch.object(eph.redis_client, "ttl", return_value=120), \
                patch.object(eph, "defer_recap_task") as mock_defer:
            mock_csm.get_messages.return_value = [
                {"metadata": {"source": "customer_human"}, "content": "hi"},
            ]
            mock_ctx.return_value = {
                "subagent": "pre-sales", "open_kfid": "kf1", "assignee_phone": "13800000000",
            }
            mock_doc.return_value = "doc"
            mock_meta.return_value = {"login_url": "https://x"}

            await eph.ExternalPushHumanAdapter.execute(payload)

        mock_defer.assert_called_once()
        args = mock_defer.call_args[0]
        assert args[0] is payload
        assert args[1] == "external_push_human"
        assert args[2] == pytest.approx(120 + eph._COOLDOWN_DEFER_BUFFER_SECONDS)

    @pytest.mark.asyncio
    async def test_cooldown_skip_ttl_invalid_uses_full_cooldown(self):
        """TTL 不可用（-2/-1）时按完整冷却时长登记"""
        from src.services.recap.tasks import external_push_human as eph

        payload = _make_payload()
        payload.task_config = [{"name": "external_push_human", "when": "every_round", "enabled": True}]

        with patch.object(eph, "_collect_context") as mock_ctx, \
                patch.object(eph, "_load_tenant_doc") as mock_doc, \
                patch.object(eph, "parse_api_meta") as mock_meta, \
                patch.object(eph, "_strip_excluded_sections", side_effect=lambda d, m, t: d), \
                patch.object(eph, "_get_agent_token", return_value="tok"), \
                patch("src.channels.session.channel_session_manager") as mock_csm, \
                patch.object(eph.redis_client, "acquire_lock", return_value=False), \
                patch.object(eph.redis_client, "ttl", return_value=-2), \
                patch.object(eph, "defer_recap_task") as mock_defer:
            mock_csm.get_messages.return_value = [
                {"metadata": {"source": "customer_human"}, "content": "hi"},
            ]
            mock_ctx.return_value = {
                "subagent": "pre-sales", "open_kfid": "kf1", "assignee_phone": "13800000000",
            }
            mock_doc.return_value = "doc"
            mock_meta.return_value = {"login_url": "https://x"}

            await eph.ExternalPushHumanAdapter.execute(payload)

        assert mock_defer.call_args[0][2] == pytest.approx(eph._HUMAN_COOLDOWN_SECONDS)
