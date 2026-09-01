# -*- coding: utf-8 -*-
"""企微个人 RPA verbose/final 唯一投递 ID 测试（Phase 3，回归 #3）

覆盖设计 §9.4：
- make_send_response final pre_send 收到 ``{event_id}:final``；
- make_send_verbose pre_send 收到 ``{eventId}:verbose:1``；
- 两条投递 request_id / outbox dedup_key 不同，重试各自幂等；
- outbox 与直推路径均保持 verbose → final 顺序；
- 不依赖 UnifiedResponse.message_id。
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.channels.base import StatusDeliveryResult
from src.channels.session import ChannelSessionManager
from src.channels.wecom_personal_rpa.adapter import WeComPersonalRpaAdapter
from src.core.agent_events import make_verbose_event


pytestmark = pytest.mark.channels

VALID_TEXT = "正在处理中，请耐心等待。"


def _make_manager():
    return ChannelSessionManager.__new__(ChannelSessionManager)


def _make_adapter():
    """RPA adapter：reply 上下文就绪、会话搜索名合法（不触发拒发）。"""
    adapter = WeComPersonalRpaAdapter(client_id="client_1", tenant_id="t1")
    adapter.deliver_actions = None  # 占位；实际方法内通过模块级 deliver_actions 调用
    return adapter


class TestRpaDeliveryIds:
    @pytest.mark.asyncio
    async def test_final_and_verbose_pre_send_receive_distinct_delivery_ids(self, monkeypatch):
        """final/verbose 分别以 {event_id}:final 与 {eventId}:verbose:1 写入
        set_reply_context 的 request_id（dedup_key 随之分离）。"""
        import src.channels.wecom_personal_rpa.action_client as action_client

        monkeypatch.setenv("RPA_SECRET_KEY", "test-rpa-audit-key-32-bytes-minimum")

        captured = []

        def fake_enqueue_action(**kwargs):
            captured.append(kwargs)
            return {"id": len(captured)}

        monkeypatch.setattr(
            action_client.db, "get_account_for_tenant",
            lambda tenant_id, account_id: {
                "client_id": None, "wecom_user_id": "self_wecom_user",
            },
        )
        monkeypatch.setattr(action_client.db, "enqueue_action", fake_enqueue_action)

        manager = _make_manager()
        adapter = _make_adapter()
        inbound_event_id = "evt_rpa_e2e"
        reply_context_kwargs = dict(
            account_id="acc_1",
            conversation_id="dm:peer_1",
            session_id="wecom_personal_rpa:acc_1:route_1",
            tenant_id="t1",
            sender_display_name="张三",
            sender_stable_id="stable_peer_1",
            conversation_search_name="张三",
            inbound_text="帮我生成报价",
        )

        # final：make_send_response 传 f"{event_id}:final"
        final_pre_ids = []
        final_pre = manager.make_send_response(
            adapter=adapter,
            message_id=inbound_event_id,
            reply_to="ext_user_1",
            log_tag="[RPA]",
            pre_send=lambda delivery_id=None: (
                final_pre_ids.append(delivery_id),
                adapter.set_reply_context(request_id=delivery_id, **reply_context_kwargs),
            ),
        )
        final_ok = await final_pre("最终报价已生成", [])

        # verbose：make_send_verbose 传 dispatcher 派生的 {eventId}:verbose:1
        from src.channels.verbose_dispatcher import verbose_delivery_id

        verbose_pre_ids = []
        verbose_event = make_verbose_event(
            event_id="verbose_rpa_1", data=VALID_TEXT, source="policy"
        )
        verbose_pre = manager.make_send_verbose(
            adapter=adapter,
            event_id=inbound_event_id,
            reply_to="ext_user_1",
            log_tag="[RPA]",
            pre_send=lambda delivery_id=None: (
                verbose_pre_ids.append(delivery_id),
                adapter.set_reply_context(request_id=delivery_id, **reply_context_kwargs),
            ),
        )
        verbose_result = await verbose_pre(
            verbose_event, verbose_delivery_id(verbose_event)
        )

        # —— pre_send 收到的 delivery_id 契约（设计 §9.4 冻结公式） ——
        assert final_pre_ids == [f"{inbound_event_id}:final"]
        assert verbose_pre_ids == ["verbose_rpa_1:verbose:1"]
        assert verbose_result.status == "sent"
        assert final_ok is True

        # —— outbox dedup_key 分离（以 request_id 派生，非 UnifiedResponse.message_id） ——
        assert len(captured) == 2
        # 本用例先 final 后 verbose（顺序契约见下一用例：真实路径 verbose 先投）
        assert [c["request_id"] for c in captured] == [
            f"{inbound_event_id}:final", "verbose_rpa_1:verbose:1",
        ]
        dedup_keys = [c["dedup_key"] for c in captured]
        assert dedup_keys[0] == f"wecom_personal_rpa:t1:{inbound_event_id}:final"
        assert dedup_keys[1] == "wecom_personal_rpa:t1:verbose_rpa_1:verbose:1"
        assert len(set(dedup_keys)) == 2
        assert {c["request_id"] for c in captured} == {
            f"{inbound_event_id}:final", "verbose_rpa_1:verbose:1",
        }

    @pytest.mark.asyncio
    async def test_retry_each_delivery_idempotent(self, monkeypatch):
        """同一 request_id 重试（客户端重拉/重推）只入队一次：各自幂等。"""
        import src.channels.wecom_personal_rpa.action_client as action_client

        monkeypatch.setenv("RPA_SECRET_KEY", "test-rpa-audit-key-32-bytes-minimum")

        captured = []

        def fake_enqueue_action(**kwargs):
            # 模拟 outbox dedup_key 唯一约束：重复入队返回 None（被去重吞掉）
            if any(c["dedup_key"] == kwargs["dedup_key"] for c in captured):
                return None
            captured.append(kwargs)
            return {"id": len(captured)}

        monkeypatch.setattr(
            action_client.db, "get_account_for_tenant",
            lambda tenant_id, account_id: {
                "client_id": None, "wecom_user_id": "self_wecom_user",
            },
        )
        monkeypatch.setattr(action_client.db, "enqueue_action", fake_enqueue_action)

        first = await action_client.deliver_actions(
            tenant_id="t1", account_id="acc_1",
            conversation_id="dm:peer_1", request_id="evt_x:verbose:1",
            session_id="wecom_personal_rpa:acc_1:route_1",
            actions=[{"type": "send_text", "text": VALID_TEXT}],
            reply_context={"sender_stable_id": "stable_peer_1"},
        )
        first_retry = await action_client.deliver_actions(
            tenant_id="t1", account_id="acc_1",
            conversation_id="dm:peer_1", request_id="evt_x:verbose:1",
            session_id="wecom_personal_rpa:acc_1:route_1",
            actions=[{"type": "send_text", "text": VALID_TEXT}],
            reply_context={"sender_stable_id": "stable_peer_1"},
        )
        final = await action_client.deliver_actions(
            tenant_id="t1", account_id="acc_1",
            conversation_id="dm:peer_1", request_id="evt_x:final",
            session_id="wecom_personal_rpa:acc_1:route_1",
            actions=[{"type": "send_text", "text": "最终回复"}],
            reply_context={"sender_stable_id": "stable_peer_1"},
        )

        # verbose 首投成功、重试被幂等去重（不入队）、final 独立入队
        assert len(captured) == 2
        assert captured[0]["request_id"] == "evt_x:verbose:1"
        assert captured[1]["request_id"] == "evt_x:final"
        # 投递语义成功（outbox 已有权威记录）
        assert first is True and final is True

    @pytest.mark.asyncio
    async def test_adapter_send_status_uses_injected_request_id(self, monkeypatch):
        """adapter.send_status_message 直接投递 pre_send 注入的 request_id。"""
        import src.channels.wecom_personal_rpa.adapter as rpa_adapter_module

        delivered = []

        async def fake_deliver_actions(**kwargs):
            delivered.append(kwargs)
            return True

        monkeypatch.setattr(
            rpa_adapter_module, "deliver_actions", fake_deliver_actions
        )
        adapter = WeComPersonalRpaAdapter(client_id="client_1", tenant_id="t1")
        adapter.set_reply_context(
            account_id="acc_1",
            conversation_id="dm:peer_1",
            session_id="wecom_personal_rpa:acc_1:route_1",
            request_id="verbose_rpa_9:verbose:1",
            sender_stable_id="stable_peer_1",
            conversation_search_name="张三",
        )
        from src.models.message import UnifiedResponse

        result = await adapter.send_status_message(
            UnifiedResponse(
                message_id="resp_anything",  # message_id 不参与幂等
                reply_to="ext_user_1",
                content={"text": VALID_TEXT},
            )
        )
        assert result.status == "sent"
        assert delivered[0]["request_id"] == "verbose_rpa_9:verbose:1"
