# -*- coding: utf-8 -*-
"""渠道 send_status_message 原子预留限流测试（Phase 3，回归 #8/#11）

覆盖：
- wecom / dingtalk（进程内 deque）：额度只剩 1 时 verbose 抑制、final 成功；
  同步临界区内完成校验+扣减（无 TOCTOU）。
- feishu：Redis Lua 路径原子「校验预留+扣减」+ 内存降级路径同 wecom。
- dingtalk 群聊：send_status_message 的 conversation_type / reply target 与 final 一致。
- 抑制不占额度：抑制后 _check_rate_limit 的剩余额度不变。
"""

import asyncio
from collections import deque
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.channels.base import StatusDeliveryResult
from src.channels.dingtalk.adapter import DingTalkAdapter
from src.channels.feishu.adapter import FeishuAdapter
from src.channels.wecom.adapter import WeComAdapter
from src.models.message import UnifiedResponse


pytestmark = pytest.mark.channels


def _text_response(text, reply_to="user_rl", conversation_type=None):
    content = {"text": text}
    if conversation_type is not None:
        content["conversation_type"] = conversation_type
    return UnifiedResponse(
        message_id="resp_rl", reply_to=reply_to, content=content
    )


def _fill_window(adapter, user_id, count, window=60.0):
    """把进程内滑动窗口填到 count 条（模拟已消耗的额度）。"""
    import time

    now = time.time()
    if isinstance(adapter._rate_limiter, dict):
        window_deque = adapter._rate_limiter.setdefault(user_id, deque())
    else:  # defaultdict
        window_deque = adapter._rate_limiter[user_id]
    window_deque.clear()
    for i in range(count):
        window_deque.append(now - i * 0.001)


# ============================================================
# wecom
# ============================================================


class TestWeComStatusRateLimit:
    def _adapter(self):
        return WeComAdapter(
            corp_id="c", agent_id="a", secret="s",
            rate_limit_enabled=True, rate_limit_max=10,
        )

    @pytest.mark.asyncio
    async def test_last_quota_suppresses_verbose_final_still_ok(self):
        """额度只剩 1：verbose suppressed_rate_limit（不占额度），final 正常发送。"""
        adapter = self._adapter()
        _fill_window(adapter, "u_wecom", 9)  # 只剩 1 个额度

        result = await adapter.send_status_message(
            _text_response("提示", reply_to="u_wecom"), reserve_for_final=1
        )
        assert result.status == "suppressed_rate_limit"
        assert len(adapter._rate_limiter["u_wecom"]) == 9, "抑制不占额度"

        # final 走既有 _check_rate_limit：唯一额度给 final
        adapter._send_with_retry = AsyncMock(return_value=True)
        ok = await adapter.send_long_message("最终回复", "u_wecom")
        assert ok is True
        assert len(adapter._rate_limiter["u_wecom"]) == 10

    @pytest.mark.asyncio
    async def test_enough_quota_sends_and_consumes(self):
        adapter = self._adapter()
        adapter.send_text = AsyncMock(return_value=True)
        result = await adapter.send_status_message(
            _text_response("提示", reply_to="u_wecom"), reserve_for_final=1
        )
        assert result.status == "sent"
        assert adapter.send_text.await_count == 1
        assert len(adapter._rate_limiter["u_wecom"]) == 1, "发送成功扣减 1 个额度"

    @pytest.mark.asyncio
    async def test_send_text_failure_returns_failed(self):
        adapter = self._adapter()
        adapter.send_text = AsyncMock(return_value=False)
        result = await adapter.send_status_message(
            _text_response("提示"), reserve_for_final=1
        )
        assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_empty_text_suppressed_unsupported(self):
        adapter = self._adapter()
        result = await adapter.send_status_message(
            _text_response(""), reserve_for_final=1
        )
        assert result.status == "suppressed_unsupported"

    def test_base_class_default_suppresses(self):
        """不支持安全预留的 adapter 默认 suppressed_unsupported，绝不降级 send_message。"""
        from src.channels.base import ChannelAdapter

        class BareAdapter(ChannelAdapter):
            @property
            def channel_type(self):
                return "bare"

            async def parse_message(self, raw_message):
                return None

            async def send_message(self, message):
                return True

            async def get_user_info(self, user_id):
                return {}

        adapter = BareAdapter()
        adapter.send_message = AsyncMock(return_value=True)
        result = asyncio.get_event_loop().run_until_complete(
            adapter.send_status_message(_text_response("提示"))
        ) if False else None
        # 直接在事件循环内运行
        result = asyncio.run(adapter.send_status_message(_text_response("提示")))
        assert result.status == "suppressed_unsupported"
        adapter.send_message.assert_not_awaited()


# ============================================================
# dingtalk
# ============================================================


class TestDingTalkStatusRateLimit:
    def _adapter(self):
        return DingTalkAdapter(
            app_key="k", app_secret="s" * 32, robot_code="r",
            rate_limit_enabled=True, rate_limit_max=10,
        )

    @pytest.mark.asyncio
    async def test_last_quota_suppresses_verbose_final_still_ok(self):
        adapter = self._adapter()
        _fill_window(adapter, "u_dt", 9)

        result = await adapter.send_status_message(
            _text_response("提示", reply_to="u_dt"), reserve_for_final=1
        )
        assert result.status == "suppressed_rate_limit"
        assert len(adapter._rate_limiter["u_dt"]) == 9

        adapter._send_with_retry = AsyncMock(return_value=True)
        ok = await adapter.send_long_message("最终回复", "u_dt", "1")
        assert ok is True
        assert len(adapter._rate_limiter["u_dt"]) == 10

    @pytest.mark.asyncio
    async def test_group_conversation_type_matches_final(self):
        """群聊：status 消息的 conversation_type / reply target 与 final 一致（回归 #11）。"""
        adapter = self._adapter()
        captured = []

        async def fake_send_text(text, user_id, conversation_type="1"):
            captured.append((text, user_id, conversation_type))
            return True

        adapter.send_text = AsyncMock(side_effect=fake_send_text)
        # final 群聊：reply_to=openConversationId、conversation_type="2"
        final_resp = UnifiedResponse(
            message_id="resp_g",
            reply_to="cid_group_1",
            content={"text": "群最终回复", "conversation_type": "2"},
        )
        await adapter.send_message(final_resp)

        # verbose 群聊：make_send_verbose 经 extra_content 透传同一 conversation_type
        verbose_resp = UnifiedResponse(
            message_id="verbose_g",
            reply_to="cid_group_1",
            content={"text": "群提示", "conversation_type": "2"},
        )
        result = await adapter.send_status_message(verbose_resp, reserve_for_final=1)
        assert result.status == "sent"
        assert captured[-1] == ("群提示", "cid_group_1", "2"), (
            "status 消息必须复用 final 的群聊目标与 conversation type"
        )


# ============================================================
# feishu
# ============================================================


class TestFeishuStatusRateLimit:
    def _adapter(self):
        return FeishuAdapter(
            app_id="i", app_secret="s",
            rate_limit_window=60, rate_limit_max=10,
        )

    @pytest.mark.asyncio
    async def test_redis_lua_atomic_reserve(self):
        """Redis 路径：Lua 脚本一次往返原子完成校验预留+扣减。"""
        adapter = self._adapter()
        fake_client = MagicMock()
        # 第一次：Lua 返回 1（允许）；第二次：返回 0（额度不足）
        fake_client.eval = MagicMock(side_effect=[1, 0])
        fake_redis = MagicMock()
        fake_redis._connected = True
        fake_redis._client = fake_client
        fake_redis.make_key = MagicMock(side_effect=lambda prefix, sid: f"{prefix}:{sid}")

        adapter.send_text = AsyncMock(return_value=True)
        with patch("src.channels.feishu.adapter.redis_client", fake_redis):
            ok = await adapter.send_status_message(
                _text_response("提示", reply_to="u_fs"), reserve_for_final=1
            )
            assert ok.status == "sent"
            assert fake_client.eval.call_count == 1
            # eval(script, numkeys, key, window_start, now, reserve, max, ttl, member)
            args = fake_client.eval.call_args.args
            assert args[1] == 1  # numkeys
            assert args[5] == 1  # reserve_for_final
            assert args[6] == 10  # max

            blocked = await adapter.send_status_message(
                _text_response("提示2", reply_to="u_fs"), reserve_for_final=1
            )
        assert blocked.status == "suppressed_rate_limit"
        # 抑制路径不发送
        assert adapter.send_text.await_count == 1

    @pytest.mark.asyncio
    async def test_memory_fallback_last_quota_suppresses_final_still_ok(self):
        adapter = self._adapter()
        fake_redis = MagicMock()
        fake_redis._connected = False
        with patch("src.channels.feishu.adapter.redis_client", fake_redis):
            _fill_window(adapter, "u_fs", 9)
            result = await adapter.send_status_message(
                _text_response("提示", reply_to="u_fs"), reserve_for_final=1
            )
            assert result.status == "suppressed_rate_limit"
            assert len(adapter._rate_limiter["u_fs"]) == 9

            adapter._send_with_retry = AsyncMock(return_value=True)
            ok = await adapter.send_long_message("最终回复", "u_fs")
            assert ok is True

    @pytest.mark.asyncio
    async def test_redis_error_degrades_to_memory(self):
        adapter = self._adapter()
        fake_client = MagicMock()
        fake_client.eval = MagicMock(side_effect=RuntimeError("redis down"))
        fake_redis = MagicMock()
        fake_redis._connected = True
        fake_redis._client = fake_client
        fake_redis.make_key = MagicMock(side_effect=lambda prefix, sid: f"{prefix}:{sid}")

        adapter.send_text = AsyncMock(return_value=True)
        with patch("src.channels.feishu.adapter.redis_client", fake_redis):
            result = await adapter.send_status_message(
                _text_response("提示", reply_to="u_fs2"), reserve_for_final=1
            )
        assert result.status == "sent"
        assert len(adapter._rate_limiter["u_fs2"]) == 1, "降级到内存 deque 并扣减"
