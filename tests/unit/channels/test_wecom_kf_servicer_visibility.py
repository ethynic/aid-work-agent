"""
微信客服：员工-客户对话可见性（v3）单元测试

对应 wecom_kf_servicer_conversation_visibility_v3.md §7 测试计划：
- 员工消息入库（text）：origin=5 -> role=user + `[人工客服] ` 前缀 + metadata.source=servicer
- 员工消息旧消息过滤（v3 新增）：send_time 超 30 分钟 -> 不入库、不收集
- 员工消息不触发 AI：mock agent，确认未调用 process_and_persist
- 人工期客户消息入库：远程=3 -> source=customer_human，未触发 AI
- 已结束会话积压入库：远程=4 -> source=customer_ended，未触发 AI
- 穿插顺序：同页 C1 -> S1 -> C2 -> S2，入库顺序必须为 C1、S1、C2、S2
- 队尾员工消息：晚于本页最后客户消息 -> 循环后 flush
- 语音占位符过滤：人工期语音消息（text="[语音消息]"）不入库
- 消息去重：同 msgid 员工消息重复拉取只入库一次（servicer: 前缀 key）

测试通过真实调用 _process_tenant_wecom_kf_messages，mock 外部依赖（adapter / 会话 / 去重 / add_message）。
"""

import sys
import time
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.saas.api.channel_routes as cr_module
from src.saas.api.channel_routes import _process_tenant_wecom_kf_messages

# 3 天前 = 超过 30 分钟，模拟 cursor 到期后微信 3 天保留窗口重放的旧消息
THREE_DAYS = 3 * 86400


def _text_msg(msgid, content, send_time, origin=3, user="u1", servicer="sv1"):
    msg = {
        "msgid": msgid,
        "origin": origin,
        "msgtype": "text",
        "external_userid": user,
        "send_time": send_time,
        "text": {"content": content},
    }
    if origin == 5:
        msg["servicer_userid"] = servicer
    return msg


def _voice_msg(msgid, send_time, origin=3, user="u1"):
    return {
        "msgid": msgid,
        "origin": origin,
        "msgtype": "voice",
        "external_userid": user,
        "send_time": send_time,
        "voice": {"media_id": "m1"},
    }


class _FakeDedup:
    """模拟 is_duplicate：首次返回 False 并记录，重复返回 True（PostgreSQL 去重语义）。"""

    def __init__(self):
        self.seen = set()

    async def is_duplicate(self, message_id: str) -> bool:
        if message_id in self.seen:
            return True
        self.seen.add(message_id)
        return False


class _FakeAgentRouter:
    """不应走到 agent 路由（本测试的客户消息均在 continue 路径被拦截）。"""

    def get_agent(self, *args, **kwargs):
        raise AssertionError("测试不应走到 agent 路由")


async def _parse_kf_message(msg):
    """mock adapter.parse_message：按消息原文返回 unified_msg（语音返回占位符）。"""
    text = msg.get("text", {}).get("content", "") or "[语音消息]"
    return SimpleNamespace(
        user_id=msg.get("external_userid", "u1"),
        text=text,
        message_type="text",
    )


def _make_adapter(msg_list, remote_state: int = 3):
    """构造最小可用的 wecom_kf adapter mock。

    remote_state 控制 get_service_state 返回（客户消息走 continue 路径的远程状态）：
    - 3：人工接待（customer_human）
    - 4：已结束会话（customer_ended）
    - 2：待接入池（customer_human）
    """
    adapter = SimpleNamespace()
    adapter.get_kf_config = MagicMock(return_value={"subagent_type": "test"})
    adapter.current_open_kfid = "kfid"
    adapter.cursor_manager = SimpleNamespace(
        get_cursor=lambda open_kfid: "",
        set_cursor=MagicMock(),
    )
    adapter.api_client = SimpleNamespace(
        sync_msg=AsyncMock(
            return_value={
                "errcode": 0,
                "has_more": 0,
                "next_cursor": "",
                "msg_list": msg_list,
            }
        ),
        get_service_state=AsyncMock(return_value={"service_state": remote_state}),
        trans_service_state=AsyncMock(return_value={"errcode": 0}),
    )
    adapter.parse_message = AsyncMock(side_effect=_parse_kf_message)
    adapter.get_user_info = AsyncMock(return_value={})
    adapter.should_exit_human = MagicMock(return_value=False)
    return adapter


def _apply_patches(dedup=None):
    """返回一组 patch context manager，隔离外部依赖。"""
    dedup = dedup or _FakeDedup()
    return [
        # 用 mock 模块替换 agent_router，避免函数内 import 触发 master_agent 构建
        patch.dict(
            sys.modules,
            {"src.core.agent_router": SimpleNamespace(agent_router=_FakeAgentRouter())},
        ),
        patch.object(cr_module, "_kf_tlog", lambda *a, **k: None),
        # 实验期埋点（origin != 3 时调用），测试环境静默
        patch.object(cr_module, "_tlog", lambda *a, **k: None),
        patch("src.channels.wecom_kf.context.set_kf_context", lambda *a, **k: None),
        patch(
            "src.saas.services.auto_register.ensure_user_registered",
            AsyncMock(return_value="u1"),
        ),
        patch.object(
            cr_module.channel_session_manager,
            "get_or_create_session",
            return_value={"session_id": "s1", "metadata": {}},
        ),
        patch.object(cr_module.channel_session_manager, "update_session", MagicMock()),
        patch.object(cr_module, "_get_tenant_dedup", return_value=dedup),
    ]


async def _run(msg_list, remote_state=3, merge_identity=False, dedup=None):
    """调用处理函数，返回 (adapter, add_message_mock, update_session_mock, process_and_persist_mock)。

    merge_identity=True 时用 identity 替换合并逻辑，保证每条客户消息单独处理，
    以便精确验证 C1 -> S1 -> C2 -> S2 的穿插入库顺序。
    """
    adapter = _make_adapter(msg_list, remote_state=remote_state)
    with ExitStack() as stack:
        for _p in _apply_patches(dedup):
            stack.enter_context(_p)
        if merge_identity:
            stack.enter_context(
                patch.object(
                    cr_module, "_merge_consecutive_user_messages", side_effect=lambda x: x
                )
            )
        add_message_mock = MagicMock()
        stack.enter_context(
            patch.object(cr_module.channel_session_manager, "add_message", add_message_mock)
        )
        process_and_persist_mock = MagicMock()
        stack.enter_context(
            patch.object(
                cr_module.channel_session_manager,
                "process_and_persist",
                process_and_persist_mock,
            )
        )
        update_session_mock = cr_module.channel_session_manager.update_session
        # 捕获 get_or_create_session mock，供调用方断言会话归属参数
        adapter.get_or_create_session = cr_module.channel_session_manager.get_or_create_session
        await _process_tenant_wecom_kf_messages("t1", "c1", "kfid", adapter)
    return adapter, add_message_mock, update_session_mock, process_and_persist_mock


class TestServicerMessage:
    @pytest.mark.asyncio
    async def test_servicer_text_message_persisted(self):
        """员工消息（origin=5, text）入库：role=user + [人工客服] 前缀 + metadata.source=servicer。"""
        now = int(time.time())
        msgs = [_text_msg("sv1", "您好，我是人工客服小张", now, origin=5)]
        adapter, add_message_mock, _u, pp_mock = await _run(msgs, remote_state=4)

        add_message_mock.assert_called_once()
        kwargs = add_message_mock.call_args.kwargs
        assert kwargs["role"] == "user"
        assert kwargs["content"] == "[人工客服] 您好，我是人工客服小张"
        assert kwargs["metadata"]["source"] == "servicer"
        assert kwargs["metadata"]["servicer_userid"] == "sv1"
        assert kwargs["metadata"]["msgid"] == "sv1"
        assert kwargs["metadata"]["open_kfid"] == "kfid"
        # 会话归属参数：channel_userid 取 external_userid，channel_type=wecom_kf，tenant/subagent/open_kfid 透传
        gs_kwargs = adapter.get_or_create_session.call_args.kwargs
        assert gs_kwargs["channel_type"] == "wecom_kf"
        assert gs_kwargs["channel_user_id"] == "u1"
        assert gs_kwargs["tenant_id"] == "t1"
        assert gs_kwargs["subagent_id"] == "test"
        assert gs_kwargs["channel_chat_id"] == "kfid"
        # 员工消息只持久化，不触发 AI
        pp_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_servicer_message_context_rebuild_compatible(self):
        """上下文重建兼容：入库消息 role=user、content 带前缀 -> 与任何 current_user_input 均不相等，
        上下文重建链路（_load_channel_history 末条 user 比较）不会误弹；to_llm_messages 透传 user 角色。"""
        now = int(time.time())
        msgs = [_text_msg("sv1", "人工客服的话", now, origin=5)]
        _a, add_message_mock, _u, _p = await _run(msgs, remote_state=4)

        kwargs = add_message_mock.call_args.kwargs
        assert kwargs["role"] == "user"
        # 带前缀的 content 不可能等于任何用户原始输入（"人工客服的话"）
        assert kwargs["content"] != "人工客服的话"
        assert kwargs["content"].startswith("[人工客服] ")

    @pytest.mark.asyncio
    async def test_old_servicer_message_filtered(self):
        """v3 新增：员工消息 send_time 超 30 分钟 -> 不入库、不收集。"""
        now = int(time.time())
        msgs = [_text_msg("sv_old", "三天前的员工消息", now - THREE_DAYS, origin=5)]
        _a, add_message_mock, _u, _p = await _run(msgs, remote_state=4)

        add_message_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_servicer_voice_not_persisted(self):
        """员工语音消息（非 text）不入库（与 should_process_kf_message 口径一致）。"""
        now = int(time.time())
        msgs = [_voice_msg("sv_voice", now, origin=5)]
        _a, add_message_mock, _u, _p = await _run(msgs, remote_state=4)

        add_message_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_servicer_dedup_by_servicer_prefix(self):
        """同 msgid 员工消息重复拉取只入库一次（servicer: 前缀 key 去重）。"""
        now = int(time.time())
        msg = _text_msg("sv1", "员工消息", now, origin=5)
        dedup = _FakeDedup()

        _a, add_message_mock1, _u, _p = await _run([msg], remote_state=4, dedup=dedup)
        _b, add_message_mock2, _u2, _p2 = await _run([msg], remote_state=4, dedup=dedup)

        add_message_mock1.assert_called_once()
        add_message_mock2.assert_not_called()


class TestContextCustomerMessage:
    @pytest.mark.asyncio
    async def test_human_period_customer_persisted(self):
        """人工期客户消息（远程=3）入库：source=customer_human，未触发 AI。"""
        now = int(time.time())
        msgs = [_text_msg("c1", "请问价格是多少", now, origin=3)]
        adapter, add_message_mock, _u, pp_mock = await _run(msgs, remote_state=3)

        add_message_mock.assert_called_once()
        kwargs = add_message_mock.call_args.kwargs
        assert kwargs["role"] == "user"
        assert kwargs["content"] == "请问价格是多少"
        assert kwargs["metadata"]["source"] == "customer_human"
        assert kwargs["metadata"]["msgid"] == "c1"
        # 会话归属参数：人工期客户消息同样落到客户自己的会话
        gs_kwargs = adapter.get_or_create_session.call_args.kwargs
        assert gs_kwargs["channel_user_id"] == "u1"
        assert gs_kwargs["channel_type"] == "wecom_kf"
        pp_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_ended_session_customer_persisted(self):
        """已结束会话积压客户消息（远程=4）入库：source=customer_ended，未触发 AI。"""
        now = int(time.time())
        msgs = [_text_msg("c1", "会话结束后的消息", now, origin=3)]
        _a, add_message_mock, update_session_mock, pp_mock = await _run(msgs, remote_state=4)

        add_message_mock.assert_called_once()
        kwargs = add_message_mock.call_args.kwargs
        assert kwargs["metadata"]["source"] == "customer_ended"
        # 已结束会话同步本地状态为 4
        update_session_mock.assert_called_once()
        assert update_session_mock.call_args.kwargs["metadata"]["service_state"] == 4
        pp_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_human_period_voice_placeholder_filtered(self):
        """人工期语音消息（text="[语音消息]"）不入库（人工期不做 ASR，占位符过滤）。"""
        now = int(time.time())
        msgs = [_voice_msg("v1", now, origin=3)]
        _a, add_message_mock, _u, _p = await _run(msgs, remote_state=3)

        add_message_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_waiting_pool_customer_persisted(self):
        """远程=2（待接入池）不允许发送：落库 customer_human，未触发 AI。"""
        now = int(time.time())
        msgs = [_text_msg("c1", "待接入池消息", now, origin=3)]
        _a, add_message_mock, _u, pp_mock = await _run(msgs, remote_state=2)

        add_message_mock.assert_called_once()
        assert add_message_mock.call_args.kwargs["metadata"]["source"] == "customer_human"
        pp_mock.assert_not_called()


class TestInterleavedOrder:
    @pytest.mark.asyncio
    async def test_interleaved_order_c1_s1_c2_s2(self):
        """穿插顺序：同页 C1 -> S1 -> C2 -> S2，入库顺序必须为 C1、S1、C2、S2。"""
        now = int(time.time())
        msgs = [
            _text_msg("c1", "问题1", now - 60, origin=3),
            _text_msg("sv1", "回答1", now - 40, origin=5),
            _text_msg("c2", "问题2", now - 20, origin=3),
            _text_msg("sv2", "回答2", now, origin=5),
        ]
        _a, add_message_mock, _u, _p = await _run(msgs, remote_state=3, merge_identity=True)

        calls = add_message_mock.call_args_list
        assert len(calls) == 4
        sources = [c.kwargs["metadata"]["source"] for c in calls]
        contents = [c.kwargs["content"] for c in calls]
        # 穿插顺序：C1(customer_human) -> S1([人工客服]) -> C2(customer_human) -> S2([人工客服])
        assert sources == ["customer_human", "servicer", "customer_human", "servicer"]
        assert contents[1].startswith("[人工客服] ")
        assert contents[3].startswith("[人工客服] ")
        assert contents[0] == "问题1"
        assert contents[2] == "问题2"

    @pytest.mark.asyncio
    async def test_trailing_servicer_flushed_after_loop(self):
        """队尾员工消息：晚于本页最后客户消息 -> 第二段循环结束后 flush 入库。"""
        now = int(time.time())
        msgs = [
            _text_msg("c1", "问题1", now - 20, origin=3),
            _text_msg("sv1", "最后的员工回答", now, origin=5),
        ]
        _a, add_message_mock, _u, _p = await _run(msgs, remote_state=3, merge_identity=True)

        calls = add_message_mock.call_args_list
        assert len(calls) == 2
        assert calls[0].kwargs["metadata"]["source"] == "customer_human"
        assert calls[1].kwargs["content"].startswith("[人工客服] ")
        # 员工回答在客户问题之后
        assert calls[0].kwargs["content"] == "问题1"

    @pytest.mark.asyncio
    async def test_merged_customer_still_before_later_servicer(self):
        """合并场景：C1、C2 被合并为一条（真实 merge 逻辑），S1 在合并消息之前入库，顺序不破坏。"""
        now = int(time.time())
        msgs = [
            _text_msg("c1", "问题1", now - 60, origin=3),
            _text_msg("sv1", "回答1", now - 40, origin=5),
            _text_msg("c2", "问题2", now - 20, origin=3),
            _text_msg("sv2", "回答2", now, origin=5),
        ]
        # 不 patch merge：C1、C2 是同一用户连续 text，会被合并为一条
        _a, add_message_mock, _u, _p = await _run(msgs, remote_state=3)

        calls = add_message_mock.call_args_list
        # 合并后：S1（早于合并消息 send_time）-> 合并消息(C1+C2) -> S2
        assert len(calls) == 3
        sources = [c.kwargs["metadata"]["source"] for c in calls]
        assert sources == ["servicer", "customer_human", "servicer"]
        # 员工回答不能晚于它应处的客户消息（S1 在合并客户消息之前入库）
        assert calls[0].kwargs["content"].startswith("[人工客服] ")
        # 合并消息内容包含两个客户问题（真实 merge 逻辑）
        merged_content = calls[1].kwargs["content"]
        assert "问题1" in merged_content and "问题2" in merged_content
