"""企业微信个人账号 RPA 连接注册表单元测试

覆盖 ClientConnectionRegistry 的核心契约（见 protocol.md §B.6）：
- register 后 is_online 返回 True
- send 在线客户端成功（mock ws.send_text 为 AsyncMock）
- send 离线客户端返回 False 不抛异常
- unregister 后 is_online 返回 False
"""

from unittest.mock import AsyncMock

import pytest

from src.channels.wecom_personal_rpa.connection import (
    ClientConnectionRegistry,
    client_connection_registry,
)


def _make_ws(send_text_side_effect=None):
    """构造一个 duck-type WebSocket：仅需要 async send_text 方法。

    send_text_side_effect 为 None 表示正常完成；传入 Exception 模拟发送失败。
    """
    ws = AsyncMock()
    if send_text_side_effect is not None:
        ws.send_text.side_effect = send_text_side_effect
    return ws


class TestClientConnectionRegistry:
    """连接注册表核心行为测试。"""

    @pytest.mark.asyncio
    async def test_register_then_is_online_true(self):
        """注册后 is_online 返回 True。"""
        registry = ClientConnectionRegistry()
        ws = _make_ws()

        await registry.register("client_001", ws)

        assert registry.is_online("client_001") is True

    @pytest.mark.asyncio
    async def test_unknown_client_is_online_false(self):
        """未注册的 client_id is_online 返回 False。"""
        registry = ClientConnectionRegistry()

        assert registry.is_online("client_unknown") is False

    @pytest.mark.asyncio
    async def test_send_online_client_succeeds(self):
        """在线客户端 send 返回 True，且 ws.send_text 被以 JSON 字符串调用。

        校验意图：在线投递路径必须把 dict 序列化为 JSON（ensure_ascii=False，
        保留中文）再调用 ws.send_text，这是 action_client 在线推送的底层契约。
        """
        registry = ClientConnectionRegistry()
        ws = _make_ws()
        await registry.register("client_001", ws)

        payload = {"request_id": "req_x", "text": "你好，已收到。"}
        ok = await registry.send("client_001", payload)

        assert ok is True
        ws.send_text.assert_awaited_once()
        sent_text = ws.send_text.await_args.args[0]
        # ensure_ascii=False：中文应原样出现，而非 \\uXXXX 转义
        assert "你好，已收到。" in sent_text
        assert "req_x" in sent_text
        # 必须是合法 JSON 且字段对齐
        import json

        decoded = json.loads(sent_text)
        assert decoded["request_id"] == "req_x"

    @pytest.mark.asyncio
    async def test_send_offline_client_returns_false_no_raise(self):
        """离线客户端 send 返回 False 且不抛异常。

        校验意图：离线是常态（客户端未连接或连到了别的 worker），调用方
        据此 False 走 outbox 落库降级路径；若这里抛异常会污染 agent 主循环。
        """
        registry = ClientConnectionRegistry()

        ok = await registry.send("client_offline", {"x": 1})

        assert ok is False

    @pytest.mark.asyncio
    async def test_send_failure_unregisters_and_returns_false(self):
        """发送过程抛异常时，连接被移除并返回 False。

        校验意图：WebSocket 已关闭 / 对端 reset 等场景下，残留的失效连接
        必须被清理，否则后续 send 会反复命中同一死连接。
        """
        registry = ClientConnectionRegistry()
        ws = _make_ws(send_text_side_effect=ConnectionError("socket closed"))
        await registry.register("client_001", ws)

        ok = await registry.send("client_001", {"x": 1})

        assert ok is False
        assert registry.is_online("client_001") is False

    @pytest.mark.asyncio
    async def test_unregister_makes_client_offline(self):
        """unregister 后客户端变为离线。"""
        registry = ClientConnectionRegistry()
        ws = _make_ws()
        await registry.register("client_001", ws)
        assert registry.is_online("client_001") is True

        await registry.unregister("client_001")

        assert registry.is_online("client_001") is False
        # 离线后 send 也应返回 False（验证清理彻底，不只是状态翻转）
        assert await registry.send("client_001", {"x": 1}) is False

    @pytest.mark.asyncio
    async def test_unregister_unknown_client_idempotent(self):
        """unregister 不存在的 client_id 幂等，不抛异常。"""
        registry = ClientConnectionRegistry()

        await registry.unregister("never_existed")  # 不应抛异常

    @pytest.mark.asyncio
    async def test_register_overwrites_old_connection(self):
        """同一 client_id 重复注册覆盖旧连接。

        校验意图：客户端重连或多实例误用同一 client_id 时，旧连接必须被替换，
        否则 send 会发到已关闭的旧 socket 上。
        """
        registry = ClientConnectionRegistry()
        ws_old = _make_ws()
        ws_new = _make_ws()
        await registry.register("client_001", ws_old)

        await registry.register("client_001", ws_new)

        # send 只应命中新连接
        await registry.send("client_001", {"x": 1})
        ws_old.send_text.assert_not_awaited()
        ws_new.send_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_list_online_filters_subset(self):
        """list_online 从候选列表中筛出在线子集，保持入参顺序。"""
        registry = ClientConnectionRegistry()
        await registry.register("c2", _make_ws())
        await registry.register("c4", _make_ws())

        result = registry.list_online(["c1", "c2", "c3", "c4"])

        assert result == ["c2", "c4"]

    def test_list_online_empty_input(self):
        """空入参返回空列表。"""
        registry = ClientConnectionRegistry()
        assert registry.list_online([]) == []

    def test_module_level_singleton_exists(self):
        """模块级单例按协议 §B.6 必须可用，且类型正确。

        校验意图：下游 action_client / WebSocket handler 直接 import 此单例，
        若被误删或改名会导致跨模块引用失败。
        """
        assert isinstance(client_connection_registry, ClientConnectionRegistry)
