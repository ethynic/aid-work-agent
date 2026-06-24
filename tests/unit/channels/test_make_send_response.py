"""
ChannelSessionManager.make_send_response 单元测试

背景：本次重构把 6 个渠道调用点（5 个 channel_routes + 1 个 wecom_personal_rpa_routes）
里重复定义的 _send_response 内联函数收敛到 ChannelSessionManager.make_send_response 工厂方法。

工厂职责：
- 构造 UnifiedResponse（含 DownloadableFileInfo 转换、extra_content 合并）
- 调用 adapter.send_message 并把返回值收敛为 bool
- 可选 pre_send 钩子（RPA 用 set_reply_context 注入）
- 异常吞掉并返回 False（不让 process_and_persist 上层崩溃）

本测试验证上述契约为何重要——不是单纯复现代码逻辑。
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, call

from src.channels.session import ChannelSessionManager


pytestmark = pytest.mark.channels


def _make_manager():
    """构造一个不带 DB 依赖的 ChannelSessionManager（make_send_response 不需要 DB）。"""
    manager = ChannelSessionManager.__new__(ChannelSessionManager)
    return manager


def _make_adapter(send_return=True, send_exc=None):
    """构造一个 mock adapter，其 send_message 是 AsyncMock。"""
    adapter = MagicMock()
    if send_exc is not None:
        adapter.send_message = AsyncMock(side_effect=send_exc)
    else:
        adapter.send_message = AsyncMock(return_value=send_return)
    return adapter


# ============================================================
# 测试用例
# ============================================================


@pytest.mark.asyncio
async def test_basic_path_returns_true_and_constructs_unified_response():
    """
    基础路径：callable 调用时正确构造 UnifiedResponse 并传给 adapter.send_message，
    返回 True。验证 message_id 加前缀 'resp_'、reply_to 透传、content.text 为正文。
    """
    manager = _make_manager()
    adapter = _make_adapter(send_return=True)

    send_response = manager.make_send_response(
        adapter=adapter,
        message_id="msg_001",
        reply_to="user_alice",
        log_tag="[TestChannel]",
    )

    result = await send_response("hello world", [])

    assert result is True
    adapter.send_message.assert_awaited_once()
    # 检查传给 send_message 的 UnifiedResponse 字段
    response = adapter.send_message.await_args.args[0]
    assert response.message_id == "resp_msg_001"
    assert response.reply_to == "user_alice"
    assert response.content == {"text": "hello world"}
    assert response.downloadable_files == []


@pytest.mark.asyncio
async def test_downloadable_files_are_converted_to_dataclass():
    """
    downloadable_files 转换：传入 list of dict，验证 DownloadableFileInfo(**f) 被正确调用。
    """
    manager = _make_manager()
    adapter = _make_adapter(send_return=True)

    send_response = manager.make_send_response(
        adapter=adapter,
        message_id="msg_002",
        reply_to="user_bob",
    )

    files = [
        {
            "file_id": "f_001",
            "file_name": "report.pdf",
            "file_size": 12345,
            "download_url": "https://example.com/f_001",
            "mime_type": "application/pdf",
        },
        {
            "file_id": "f_002",
            "file_name": "data.xlsx",
        },
    ]
    result = await send_response("text", files)

    assert result is True
    response = adapter.send_message.await_args.args[0]
    assert len(response.downloadable_files) == 2
    assert response.downloadable_files[0].file_id == "f_001"
    assert response.downloadable_files[0].file_name == "report.pdf"
    assert response.downloadable_files[0].file_size == 12345
    assert response.downloadable_files[1].file_id == "f_002"
    # 默认值生效
    assert response.downloadable_files[1].file_size == 0
    assert response.downloadable_files[1].mime_type == ""


@pytest.mark.asyncio
async def test_extra_content_merged_with_text():
    """
    DingTalk 场景：extra_content 应与 text 合并到同一 content dict 中，
    且 text 保留原值，conversation_type 透传。
    """
    manager = _make_manager()
    adapter = _make_adapter(send_return=True)

    send_response = manager.make_send_response(
        adapter=adapter,
        message_id="msg_003",
        reply_to="conversation_123",
        extra_content={"conversation_type": "2"},
        log_tag="[Tenant DingTalk]",
    )

    result = await send_response("群聊消息", [])

    assert result is True
    response = adapter.send_message.await_args.args[0]
    assert response.content == {"text": "群聊消息", "conversation_type": "2"}


@pytest.mark.asyncio
async def test_pre_send_hook_called_once_before_send():
    """
    RPA 场景：pre_send 钩子（set_reply_context 注入）必须在 send_message 之前被调用一次。
    用 call_order 验证次序。
    """
    manager = _make_manager()
    adapter = _make_adapter(send_return=True)

    call_order = []

    def pre_send():
        call_order.append("pre_send")

    adapter.send_message = AsyncMock(
        side_effect=lambda resp: call_order.append("send_message") or True
    )

    send_response = manager.make_send_response(
        adapter=adapter,
        message_id="event_001",
        reply_to="user_rpa",
        log_tag="[RPA]",
        pre_send=pre_send,
    )

    result = await send_response("reply", [])

    assert result is True
    assert call_order == ["pre_send", "send_message"]


@pytest.mark.asyncio
async def test_adapter_send_exception_returns_false_no_propagation():
    """
    异常路径：adapter.send_message 抛异常，callable 应返回 False 且不向上传播。
    这条契约保证 send_response 失败时 process_and_persist 的 finally 仍能正常 mark_idle，
    并触发 _ensure_last_not_orphan_user 兜底。
    """
    manager = _make_manager()
    adapter = _make_adapter(send_exc=RuntimeError("network down"))

    send_response = manager.make_send_response(
        adapter=adapter,
        message_id="msg_004",
        reply_to="user_e",
    )

    result = await send_response("text", [])

    assert result is False
    adapter.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_result_false_propagates_false():
    """
    send_result=False 路径：mock send_message 返回 False，callable 也返回 False。
    """
    manager = _make_manager()
    adapter = _make_adapter(send_return=False)

    send_response = manager.make_send_response(
        adapter=adapter,
        message_id="msg_005",
        reply_to="user_f",
    )

    result = await send_response("text", [])

    assert result is False


@pytest.mark.asyncio
async def test_send_result_none_falls_back_to_false():
    """
    send_result=None 路径：mock send_message 返回 None，callable 用 bool(None)=False 兜底。
    防御性测试，避免某渠道 adapter 返回 None 时被错误判定为「成功」。
    """
    manager = _make_manager()
    adapter = _make_adapter(send_return=None)

    send_response = manager.make_send_response(
        adapter=adapter,
        message_id="msg_006",
        reply_to="user_g",
    )

    result = await send_response("text", [])

    assert result is False


@pytest.mark.asyncio
async def test_empty_response_text_does_not_raise():
    """
    response_text 为空字符串时不应报错。
    实际场景：process_and_persist 批量写入事务失败时，会以空串调用 send_response 作为占位。
    """
    manager = _make_manager()
    adapter = _make_adapter(send_return=True)

    send_response = manager.make_send_response(
        adapter=adapter,
        message_id="msg_007",
        reply_to="user_h",
    )

    result = await send_response("", [])

    assert result is True
    response = adapter.send_message.await_args.args[0]
    assert response.content == {"text": ""}


async def test_pre_send_exception_returns_false_no_propagation():
    """
    pre_send 钩子抛异常时，callable 必须返回 False 且不向上传播。

    为何重要：RPA 的 set_reply_context 内部可能触发 DB 异常（如租户配置读取失败）。
    如果异常向上传播到 process_and_persist，会让整条消息处理崩溃；如果异常被吞掉
    但 send_message 仍然继续执行，会因缺少 reply_context 在 adapter 内部失败。
    正确契约是：pre_send 失败 → 整个 send_response 返回 False，由调用方决定后续。
    """
    manager = _make_manager()
    adapter = _make_adapter(send_return=True)

    def failing_pre_send():
        raise RuntimeError("set_reply_context failed: tenant config not found")

    send_response = manager.make_send_response(
        adapter=adapter,
        message_id="msg_008",
        reply_to="user_i",
        pre_send=failing_pre_send,
    )

    result = await send_response("hello", [])

    assert result is False
    # 关键不变量：pre_send 失败时 send_message 绝不能被调用
    adapter.send_message.assert_not_awaited()
