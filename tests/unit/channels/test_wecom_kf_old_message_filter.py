"""
微信客服旧消息过滤 + 恢复点 (0,4) 拆分 单元测试

对应 wecom_kf_95013_old_message_filter.md §5.1 / §5.3：
- 旧消息过滤（P0 核心）：超过 30 分钟的旧消息从源头丢弃，不进入发送前校验，不触发
  trans_service_state(1)（即 95013 刷屏源头消除）
- 新消息不被误过滤：30 分钟内的新消息正常进入处理
- 恢复点拆分（P0 兜底）：remote=4 已结束会话不调 trans，仅同步本地状态并跳过 AI；
  remote=0 仍尝试切回智能助手
- 边界/异常：恰好 30 分钟不过滤；send_time 缺失/非数字不崩溃

测试通过真实调用 _process_tenant_wecom_kf_messages，mock 外部依赖（adapter / 会话 / 去重）。
新消息路径在发送前校验处用 remote=4 或 remote=0 失败拦截，避免 mock 完整 AI 链路。
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


def _text_msg(msgid: str, content: str, send_time) -> dict:
    return {
        "msgid": msgid,
        "origin": 3,
        "msgtype": "text",
        "external_userid": "u1",
        "send_time": send_time,
        "text": {"content": content},
    }


def _make_adapter(msg_list, remote_state: int = 4):
    """构造最小可用的 wecom_kf adapter mock。

    remote_state 用于发送前校验 get_service_state 的返回值：
    - 4：已结束会话，走恢复点拆分后的 remote=4 分支（不调 trans）
    - 0：未处理，走 remote=0 分支（调 trans）
    """
    adapter = SimpleNamespace()
    # get_kf_config 在源码中是同步调用（无 await），必须用同步 mock，否则返回 coroutine
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
    adapter.parse_message = AsyncMock(
        return_value=SimpleNamespace(user_id="u1", text="新消息内容", message_type="text")
    )
    adapter.get_user_info = AsyncMock(return_value={})
    return adapter


class _FakeAgentRouter:
    """不应走到 agent 路由（本测试在新消息在发送前校验处即被拦截）。"""

    def get_agent(self, *args, **kwargs):
        raise AssertionError("测试不应走到 agent 路由")


def _apply_patches():
    """返回一组 patch context manager，隔离外部依赖。"""
    return [
        # 用 mock 模块替换 agent_router，避免函数内 import 触发 master_agent 构建
        # （全量测试下会与其它测试资源叠加导致 SIGABRT）
        patch.dict(
            sys.modules,
            {"src.core.agent_router": SimpleNamespace(agent_router=_FakeAgentRouter())},
        ),
        patch.object(cr_module, "_kf_tlog", lambda *a, **k: None),
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
        patch.object(
            cr_module,
            "_get_tenant_dedup",
            return_value=SimpleNamespace(is_duplicate=AsyncMock(return_value=False)),
        ),
    ]


async def _run(msg_list, remote_state: int):
    """调用处理函数，返回 (adapter, update_session_mock)。

    update_session_mock 必须在 patch 上下文内捕获引用，ExitStack 退出后
    channel_session_manager 上的 patch 已还原，但 mock 引用仍保留调用记录。
    """
    adapter = _make_adapter(msg_list, remote_state=remote_state)
    with ExitStack() as stack:
        for _p in _apply_patches():
            stack.enter_context(_p)
        update_session_mock = cr_module.channel_session_manager.update_session
        await _process_tenant_wecom_kf_messages("t1", "c1", "kfid", adapter)
    return adapter, update_session_mock


class TestOldMessageFilter:
    @pytest.mark.asyncio
    async def test_all_old_messages_discarded_no_remote_checks(self):
        """全旧消息批次：全部从源头丢弃，不进入发送前校验，不调 trans（无 95013）。"""
        now = int(time.time())
        msgs = [_text_msg(f"old{i}", f"旧消息{i}", now - THREE_DAYS) for i in range(3)]
        adapter, update_session_mock = await _run(msgs, remote_state=4)

        # 旧消息未走到发送前校验（get_service_state 不被调用），也不会调 trans / 同步状态
        adapter.api_client.get_service_state.assert_not_called()
        adapter.api_client.trans_service_state.assert_not_called()
        update_session_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_old_discarded_new_processed_remote4_updates_local_state(self):
        """旧+新混合：旧消息源头丢弃；新消息走到发送前校验，remote=4 同步本地状态不调 trans。"""
        now = int(time.time())
        msgs = [
            _text_msg("old1", "旧消息", now - THREE_DAYS),
            _text_msg("new1", "新消息", now),
        ]
        adapter, update_session_mock = await _run(msgs, remote_state=4)

        # 旧消息未走到发送前校验：get_service_state 只被新消息调用 1 次
        adapter.api_client.get_service_state.assert_awaited_once()
        # remote=4 同步本地状态
        update_session_mock.assert_called_once()
        kwargs = update_session_mock.call_args.kwargs
        assert kwargs["metadata"]["service_state"] == 4
        # 已结束会话不调 trans，无 95013
        adapter.api_client.trans_service_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_remote0_still_attempts_trans(self):
        """remote=0（未处理）：新消息仍尝试切回智能助手，调 trans(service_state=1)。"""
        now = int(time.time())
        msgs = [_text_msg("new1", "新消息", now)]
        adapter, _update_session_mock = await _run(msgs, remote_state=0)

        adapter.api_client.trans_service_state.assert_awaited_once()
        call_kwargs = adapter.api_client.trans_service_state.call_args.kwargs
        assert call_kwargs["service_state"] == 1

    @pytest.mark.asyncio
    async def test_boundary_exactly_30min_not_filtered(self, monkeypatch):
        """边界：恰好 30 分钟（elapsed==1800）不被过滤，正常进入处理。

        固定 time.time() 避免 float 截断导致 elapsed 略大于 1800 被误过滤。
        """
        fixed_now = 1_800_000_000.0
        monkeypatch.setattr(cr_module.time, "time", lambda: fixed_now)
        msgs = [_text_msg("b1", "边界消息", int(fixed_now) - 1800)]
        adapter, update_session_mock = await _run(msgs, remote_state=4)

        # 未被过滤 -> 走到发送前校验 -> remote=4 分支同步本地状态
        adapter.api_client.get_service_state.assert_awaited_once()
        update_session_mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_time_missing_not_crash(self):
        """异常：send_time 缺失 -> 按不过滤处理，不崩溃。"""
        now = int(time.time())
        msg = _text_msg("m1", "无时间消息", now)
        msg.pop("send_time")
        adapter, update_session_mock = await _run([msg], remote_state=4)

        adapter.api_client.get_service_state.assert_awaited_once()
        update_session_mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_time_non_numeric_not_crash(self):
        """异常：send_time 非数字 -> int() 转换失败被捕获，按不过滤处理，不崩溃。"""
        now = int(time.time())
        msg = _text_msg("m2", "坏时间消息", now)
        msg["send_time"] = "not-a-number"
        adapter, update_session_mock = await _run([msg], remote_state=4)

        adapter.api_client.get_service_state.assert_awaited_once()
        update_session_mock.assert_called_once()
