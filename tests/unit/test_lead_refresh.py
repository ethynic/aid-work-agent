"""
lead_refresh 适配器单元测试（留资线索动态刷新，#64）

覆盖：
1. 未留资会话 no-op（不触碰 LLM / 冷却键）
2. 线索已删除 no-op
3. 冷却中跳过（不调 LLM）
4. 正常回写（意向度/需求分条/游标，计费调用）
5. JSON 解析失败 / intent_level 非法 -> 保留旧值 + 删冷却键 + 不回写
6. LeadCaptureDB.update_analysis 非法意向度拒绝（不触 DB）
7. enqueue_human_period_tasks 入队 payload / 异常吞掉
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.recap.runner import RecapPayload, enqueue_human_period_tasks
from src.services.recap.tasks.lead_refresh import LeadRefreshAdapter


def _make_payload(**overrides) -> RecapPayload:
    base = dict(
        tenant_id="tenant_abc",
        session_id="tenant_abc_wecom_kf_kf1_user1_pre-sales",
        subagent_name="pre-sales",
        round_message_id="msg_123",
        user_content="",
        assistant_reply="",
        record_service=None,
        user_id="user_1",
        trace_id=None,
        enqueued_at=time.time(),
    )
    base.update(overrides)
    return RecapPayload(**base)


def _make_lead(**overrides) -> dict:
    lead = dict(
        lead_id="lead_lc_abc123",
        tenant_id="tenant_abc",
        contact_name="张三",
        demand_summary="想了解产品价格",
        stage="contacting",
        intent_level=None,
        intent_reason=None,
        demand_points=None,
    )
    lead.update(overrides)
    return lead


def _make_messages() -> list:
    return [
        {"message_id": "m1", "role": "user", "content": "你们的产品多少钱", "metadata": {}},
        {"message_id": "m2", "role": "assistant", "content": "您好，价格根据配置不同……", "metadata": None},
        {"message_id": "m3", "role": "user", "content": "[人工客服] 张三您好", "metadata": {"source": "servicer"}},
        {"message_id": "m4", "role": "user", "content": "那我再考虑下", "metadata": {"source": "customer_human"}},
        {"message_id": "m5", "role": "system", "content": "[已转人工] ……", "metadata": {}},
    ]


def _analysis_json(level="high", reason="主动询价", points=None):
    import json

    return json.dumps({
        "intent_level": level,
        "intent_reason": reason,
        "demand_points": points if points is not None else ["产品价格咨询", "售后保障"],
    }, ensure_ascii=False)


class TestLeadRefreshAdapter:
    @pytest.mark.asyncio
    async def test_noop_when_no_lead_capture(self):
        """未留资会话：读会话后静默返回，不触碰冷却键与 LLM"""
        payload = _make_payload()
        with patch("src.channels.session.channel_session_manager") as mock_mgr, \
                patch("src.services.recap.tasks.lead_refresh.redis_client") as mock_redis:
            mock_mgr.get_session_by_id.return_value = {"metadata": {}}
            await LeadRefreshAdapter.execute(payload)
            mock_redis.acquire_lock.assert_not_called()

    @pytest.mark.asyncio
    async def test_noop_when_lead_deleted(self):
        """线索已被删除（隐藏命令）：get_by_id 未命中即返回"""
        payload = _make_payload()
        with patch("src.channels.session.channel_session_manager") as mock_mgr, \
                patch("src.saas.db.lead_capture_db.LeadCaptureDB") as mock_db, \
                patch("src.services.recap.tasks.lead_refresh.redis_client") as mock_redis:
            mock_mgr.get_session_by_id.return_value = {
                "metadata": {"lead_capture": {"lead_id": "lead_lc_abc123"}}
            }
            mock_db.get_by_id.return_value = None
            await LeadRefreshAdapter.execute(payload)
            mock_redis.acquire_lock.assert_not_called()

    @pytest.mark.asyncio
    async def test_cooldown_skip(self):
        """冷却中（占坑失败）：跳过分析，不调 LLM"""
        payload = _make_payload()
        with patch("src.channels.session.channel_session_manager") as mock_mgr, \
                patch("src.saas.db.lead_capture_db.LeadCaptureDB") as mock_db, \
                patch("src.services.recap.tasks.lead_refresh.redis_client") as mock_redis, \
                patch("src.llm.gateway.llm_gateway") as mock_gw:
            mock_mgr.get_session_by_id.return_value = {
                "metadata": {"lead_capture": {"lead_id": "lead_lc_abc123"}}
            }
            mock_db.get_by_id.return_value = _make_lead()
            mock_redis.acquire_lock.return_value = False
            await LeadRefreshAdapter.execute(payload)
            mock_gw.chat_lite.assert_not_called()
            mock_redis.delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_normal_analysis_writeback(self):
        """正常链路：占坑成功 -> 分析 -> 回写（含计费与游标）"""
        payload = _make_payload()
        response = {"content": _analysis_json(), "usage": {"prompt_tokens": 100, "completion_tokens": 50}}
        with patch("src.channels.session.channel_session_manager") as mock_mgr, \
                patch("src.saas.db.lead_capture_db.LeadCaptureDB") as mock_db, \
                patch("src.services.recap.tasks.lead_refresh.redis_client") as mock_redis, \
                patch("src.llm.gateway.llm_gateway") as mock_gw, \
                patch("src.services.session_record.record_background_llm_usage") as mock_bill:
            mock_mgr.get_session_by_id.return_value = {
                "metadata": {"lead_capture": {"lead_id": "lead_lc_abc123"}}
            }
            mock_mgr.get_messages.return_value = _make_messages()
            mock_db.get_by_id.return_value = _make_lead()
            mock_redis.acquire_lock.return_value = True
            mock_gw.chat_lite = AsyncMock(return_value=response)

            await LeadRefreshAdapter.execute(payload)

            mock_gw.chat_lite.assert_awaited_once()
            mock_bill.assert_called_once()
            assert mock_bill.call_args.kwargs["source"] == "lead_refresh_pre-sales"
            mock_db.update_analysis.assert_called_once_with(
                "lead_lc_abc123",
                "tenant_abc",
                "high",
                "主动询价",
                ["产品价格咨询", "售后保障"],
                "m4",
            )
            # 成功路径不删冷却键（靠 TTL 过期）
            mock_redis.delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalid_json_keeps_old_value(self):
        """LLM 输出非法 JSON：保留旧值、删冷却键、不回写"""
        payload = _make_payload()
        response = {"content": "这不是 JSON", "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
        with patch("src.channels.session.channel_session_manager") as mock_mgr, \
                patch("src.saas.db.lead_capture_db.LeadCaptureDB") as mock_db, \
                patch("src.services.recap.tasks.lead_refresh.redis_client") as mock_redis, \
                patch("src.llm.gateway.llm_gateway") as mock_gw, \
                patch("src.services.session_record.record_background_llm_usage"):
            mock_mgr.get_session_by_id.return_value = {
                "metadata": {"lead_capture": {"lead_id": "lead_lc_abc123"}}
            }
            mock_mgr.get_messages.return_value = _make_messages()
            mock_db.get_by_id.return_value = _make_lead()
            mock_redis.acquire_lock.return_value = True
            mock_gw.chat_lite = AsyncMock(return_value=response)

            await LeadRefreshAdapter.execute(payload)

            mock_db.update_analysis.assert_not_called()
            mock_redis.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_invalid_intent_level_rejected(self):
        """intent_level 越界：视为解析失败，保留旧值"""
        payload = _make_payload()
        response = {"content": _analysis_json(level="very_high"), "usage": None}
        with patch("src.channels.session.channel_session_manager") as mock_mgr, \
                patch("src.saas.db.lead_capture_db.LeadCaptureDB") as mock_db, \
                patch("src.services.recap.tasks.lead_refresh.redis_client") as mock_redis, \
                patch("src.llm.gateway.llm_gateway") as mock_gw, \
                patch("src.services.session_record.record_background_llm_usage"):
            mock_mgr.get_session_by_id.return_value = {
                "metadata": {"lead_capture": {"lead_id": "lead_lc_abc123"}}
            }
            mock_mgr.get_messages.return_value = _make_messages()
            mock_db.get_by_id.return_value = _make_lead()
            mock_redis.acquire_lock.return_value = True
            mock_gw.chat_lite = AsyncMock(return_value=response)

            await LeadRefreshAdapter.execute(payload)

            mock_db.update_analysis.assert_not_called()
            mock_redis.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_llm_failure_releases_cooldown(self):
        """LLM 调用异常：删冷却键允许下一条消息重试，异常上抛由 runner 吞掉"""
        payload = _make_payload()
        with patch("src.channels.session.channel_session_manager") as mock_mgr, \
                patch("src.saas.db.lead_capture_db.LeadCaptureDB") as mock_db, \
                patch("src.services.recap.tasks.lead_refresh.redis_client") as mock_redis, \
                patch("src.llm.gateway.llm_gateway") as mock_gw, \
                patch("src.services.session_record.record_background_llm_usage"):
            mock_mgr.get_session_by_id.return_value = {
                "metadata": {"lead_capture": {"lead_id": "lead_lc_abc123"}}
            }
            mock_mgr.get_messages.return_value = _make_messages()
            mock_db.get_by_id.return_value = _make_lead()
            mock_redis.acquire_lock.return_value = True
            mock_gw.chat_lite = AsyncMock(side_effect=RuntimeError("llm down"))

            with pytest.raises(RuntimeError):
                await LeadRefreshAdapter.execute(payload)

            mock_redis.delete.assert_called_once()


class TestLeadCaptureDBUpdateAnalysis:
    def test_invalid_intent_level_rejected_before_db(self):
        """非法意向度在触 DB 前拒绝（枚举以 LeadIntentLevel 为准）"""
        from src.saas.db import lead_capture_db

        with patch.object(lead_capture_db, "get_db_connection") as mock_conn:
            result = lead_capture_db.LeadCaptureDB.update_analysis(
                "lead_lc_x", "tenant_abc", "very_high", "r", ["a"], "m1"
            )
            assert result is False
            mock_conn.assert_not_called()

    def test_update_transfer_info_sql(self):
        """update_transfer_info 正常 SQL 执行路径（rowcount 命中）"""
        from src.saas.db import lead_capture_db

        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1
        mock_conn_ctx = MagicMock()
        mock_conn_ctx.__enter__.return_value = mock_conn_ctx
        mock_conn_ctx.cursor.return_value = mock_cursor
        with patch.object(lead_capture_db, "get_db_connection", return_value=mock_conn_ctx):
            result = lead_capture_db.LeadCaptureDB.update_transfer_info(
                "lead_lc_x", "tenant_abc", "servicer_1", "李四"
            )
        assert result is True
        sql = mock_cursor.execute.call_args[0][0]
        assert "transferred_to" in sql and "servicer_name" in sql and "last_human_transfer_at" in sql


class TestEnqueueLeadRefresh:
    def test_enqueue_payload(self):
        """入队 payload：task_config 含 lead_refresh + external_push_human，subagent 从 session_id 解析"""
        with patch("src.services.recap.runner.redis_client") as mock_redis:
            mock_redis.rpush.return_value = True
            mock_redis.make_key.side_effect = lambda prefix, identifier="": (
                f"{prefix}:{identifier}" if identifier else prefix
            )
            enqueue_human_period_tasks("tenant_abc", "tenant_abc_wecom_kf_kf1_user1_pre-sales", "msgid_9")
            body = mock_redis.rpush.call_args[0][1]
        assert "recap_task_queue" in mock_redis.rpush.call_args[0][0]
        assert body["task_config"] == [
            {"name": "lead_refresh", "when": "every_round", "enabled": True},
            {"name": "external_push_human", "when": "every_round", "enabled": True},
        ]
        assert body["subagent_name"] == "pre-sales"
        assert body["round_message_id"] == "msgid_9"
        assert body["user_content"] == ""
        assert body["enqueued_at"] > 0

    def test_enqueue_exception_swallowed(self):
        """Redis 异常：吞掉不阻断消息链路"""
        with patch("src.services.recap.runner.redis_client") as mock_redis:
            mock_redis.rpush.side_effect = RuntimeError("redis down")
            enqueue_human_period_tasks("tenant_abc", "tenant_abc_wecom_kf_kf1_user1_pre-sales", "msgid_9")

    def test_enqueue_redis_unavailable_gives_up(self):
        """Redis 不可用（rpush False）：放弃不降级进程内执行"""
        with patch("src.services.recap.runner.redis_client") as mock_redis, \
                patch("src.services.recap.runner._run_tasks") as mock_run:
            mock_redis.rpush.return_value = False
            enqueue_human_period_tasks("tenant_abc", "tenant_abc_wecom_kf_kf1_user1_pre-sales", "msgid_9")
            mock_run.assert_not_called()


class TestDemandPointsMissingField:
    @pytest.mark.asyncio
    async def test_missing_demand_points_keeps_old(self):
        """合法 JSON 但缺 demand_points 字段：沿用旧分条，不覆盖为 NULL"""
        import json

        payload = _make_payload()
        content = json.dumps({"intent_level": "medium", "intent_reason": "持续追问"}, ensure_ascii=False)
        response = {"content": content, "usage": None}
        with patch("src.channels.session.channel_session_manager") as mock_mgr, \
                patch("src.saas.db.lead_capture_db.LeadCaptureDB") as mock_db, \
                patch("src.services.recap.tasks.lead_refresh.redis_client") as mock_redis, \
                patch("src.llm.gateway.llm_gateway") as mock_gw, \
                patch("src.services.session_record.record_background_llm_usage"):
            mock_mgr.get_session_by_id.return_value = {
                "metadata": {"lead_capture": {"lead_id": "lead_lc_abc123"}}
            }
            mock_mgr.get_messages.return_value = _make_messages()
            mock_db.get_by_id.return_value = _make_lead(demand_points=["价格咨询"])
            mock_redis.acquire_lock.return_value = True
            mock_gw.chat_lite = AsyncMock(return_value=response)

            await LeadRefreshAdapter.execute(payload)

            args = mock_db.update_analysis.call_args[0]
            assert args[4] == ["价格咨询"]
            mock_redis.delete.assert_not_called()


class TestEntryBWiring:
    """入口 B：人工期客户消息落库后触发 enqueue_human_period_tasks"""

    @pytest.mark.asyncio
    async def test_persist_customer_message_enqueues_lead_refresh(self):
        from src.saas.api import channel_routes

        unified_msg = MagicMock()
        unified_msg.text = "那我再考虑下"
        msg = {"msgid": "wecom_msg_1"}
        with patch.object(channel_routes, "channel_session_manager") as mock_mgr, \
                patch("src.services.recap.runner.enqueue_human_period_tasks") as mock_enqueue:
            await channel_routes._persist_kf_context_customer_message(
                unified_msg, msg, "tenant_abc_wecom_kf_kf1_u1_pre-sales",
                "kf1", "tenant_abc", "customer_human",
            )
            mock_mgr.add_message.assert_called_once()
            mock_enqueue.assert_called_once_with(
                "tenant_abc", "tenant_abc_wecom_kf_kf1_u1_pre-sales", "wecom_msg_1"
            )

    @pytest.mark.asyncio
    async def test_enqueue_skipped_without_msgid(self):
        """msgid 缺失：落库照常，入队被守卫放弃（防幂等键退化占坑）"""
        from src.saas.api import channel_routes
        from src.services.recap import runner as recap_runner

        unified_msg = MagicMock()
        unified_msg.text = "你好"
        with patch.object(channel_routes, "channel_session_manager") as mock_mgr, \
                patch("src.services.recap.runner.redis_client") as mock_redis:
            mock_redis.rpush.return_value = True
            await channel_routes._persist_kf_context_customer_message(
                unified_msg, {"msgid": ""}, "tenant_abc_wecom_kf_kf1_u1_pre-sales",
                "kf1", "tenant_abc", "customer_human",
            )
            mock_mgr.add_message.assert_called_once()
            mock_redis.rpush.assert_not_called()
            assert recap_runner.enqueue_human_period_tasks("t", "s", "") is None


class TestTransferWriteback:
    """转人工回写线索归属：lead_capture 缺失时零副作用"""

    @staticmethod
    def _make_ctx(adapter):
        return {
            "adapter": adapter,
            "open_kfid": "kf1",
            "external_userid": "u1",
            "kf_config": {"servicer_userid_list": ["servicer_1"]},
            "session_id": "tenant_abc_wecom_kf_kf1_u1_pre-sales",
            "tenant_id": "tenant_abc",
        }

    @pytest.mark.asyncio
    async def test_writeback_with_lead(self):
        with patch("src.channels.wecom_kf.context.get_kf_context"), \
                patch("src.channels.session.channel_session_manager") as mock_mgr, \
                patch("src.saas.db.lead_capture_db.LeadCaptureDB") as mock_db, \
                patch("src.core.redis_client.redis_client") as mock_redis:
            adapter = MagicMock()
            adapter.transfer_to_human = AsyncMock(return_value=True)
            ctx = {
                "adapter": adapter,
                "open_kfid": "kf1",
                "external_userid": "u1",
                "kf_config": {"servicer_userid_list": ["servicer_1"]},
                "session_id": "tenant_abc_wecom_kf_kf1_u1_pre-sales",
                "tenant_id": "tenant_abc",
            }
            mock_get = MagicMock(return_value=ctx)
            import src.channels.wecom_kf.context as kf_context
            with patch.object(kf_context, "get_kf_context", mock_get):
                mock_mgr.get_session_by_id.return_value = {
                    "metadata": {"lead_capture": {"lead_id": "lead_lc_x"}}
                }
                mock_redis.get.return_value = "李四"
                from src.tools.transfer_to_human import TransferToHumanTool

                result = await TransferToHumanTool().execute(reason="user_request")
            assert result["success"] is True
            mock_db.update_transfer_info.assert_called_once_with(
                "lead_lc_x", "tenant_abc", "servicer_1", "李四"
            )

    @pytest.mark.asyncio
    async def test_no_side_effect_without_lead_capture(self):
        """会话未留资：不触碰线索表（零副作用）"""
        with patch("src.channels.wecom_kf.context.get_kf_context"), \
                patch("src.channels.session.channel_session_manager") as mock_mgr, \
                patch("src.saas.db.lead_capture_db.LeadCaptureDB") as mock_db, \
                patch("src.core.redis_client.redis_client"):
            adapter = MagicMock()
            adapter.transfer_to_human = AsyncMock(return_value=True)
            ctx = {
                "adapter": adapter,
                "open_kfid": "kf1",
                "external_userid": "u1",
                "kf_config": {"servicer_userid_list": ["servicer_1"]},
                "session_id": "tenant_abc_wecom_kf_kf1_u1_pre-sales",
                "tenant_id": "tenant_abc",
            }
            import src.channels.wecom_kf.context as kf_context
            with patch.object(kf_context, "get_kf_context", MagicMock(return_value=ctx)):
                mock_mgr.get_session_by_id.return_value = {"metadata": {}}
                from src.tools.transfer_to_human import TransferToHumanTool

                result = await TransferToHumanTool().execute(reason="user_request")
            assert result["success"] is True
            mock_db.update_transfer_info.assert_not_called()
