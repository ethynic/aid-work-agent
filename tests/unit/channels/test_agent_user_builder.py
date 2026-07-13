"""
build_agent_user_for_channel 单元测试

覆盖 DB 缓存命中、渠道 API 兜底、写回 DB、异常降级等分支。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.channels.agent_user_builder import build_agent_user_for_channel


# ---------- helpers ----------


def _make_user_record(phone: str = None, nickname: str = "张三", username: str = "u123"):
    """构造 UserDB.get_by_id 返回的记录"""
    record = {
        "user_id": "u123",
        "username": username,
        "nickname": nickname,
        "phone": phone,
        "tenant_id": "t1",
    }
    return record


def _make_adapter(mobile: str = "", raise_exc: Exception = None):
    """构造 mock 渠道适配器"""
    adapter = MagicMock()
    if raise_exc:
        adapter.get_user_info = AsyncMock(side_effect=raise_exc)
    else:
        adapter.get_user_info = AsyncMock(return_value={"mobile": mobile} if mobile else {})
    return adapter


# ---------- 测试用例 ----------


@pytest.mark.asyncio
async def test_user_id_none_returns_none():
    """user_id 为 None 时返回 None（自动注册失败的兜底）"""
    adapter = _make_adapter(mobile="13800138000")

    result = await build_agent_user_for_channel(
        channel_type="feishu",
        channel_user_id="ou_xxx",
        tenant_id="t1",
        adapter=adapter,
        user_id=None,
    )

    assert result is None
    adapter.get_user_info.assert_not_called()


@pytest.mark.asyncio
async def test_db_has_phone_skips_adapter():
    """DB 已有 phone 时直接返回，不调渠道 API"""
    adapter = _make_adapter(mobile="13800138000")

    with patch(
        "src.channels.agent_user_builder.UserDB.get_by_id",
        return_value=_make_user_record(phone="13900139000"),
    ) as mock_get:
        result = await build_agent_user_for_channel(
            channel_type="feishu",
            channel_user_id="ou_xxx",
            tenant_id="t1",
            adapter=adapter,
            user_id="u123",
        )

    mock_get.assert_called_once_with("u123")
    adapter.get_user_info.assert_not_called()
    assert result.user_id == "u123"
    assert result.phone == "13900139000"
    assert result.channel_type == "feishu"
    assert result.channel_user_id == "ou_xxx"


@pytest.mark.asyncio
async def test_db_no_phone_adapter_returns_mobile_writes_back():
    """DB 无 phone + adapter 返回 mobile -> 写回 DB，User.phone 正确"""
    adapter = _make_adapter(mobile="13800138000")

    with patch(
        "src.channels.agent_user_builder.UserDB.get_by_id",
        return_value=_make_user_record(phone=None),
    ), patch(
        "src.channels.agent_user_builder.UserDB.update_info"
    ) as mock_update:
        result = await build_agent_user_for_channel(
            channel_type="wecom",
            channel_user_id="staffid_xxx",
            tenant_id="t1",
            adapter=adapter,
            user_id="u123",
        )

    adapter.get_user_info.assert_awaited_once_with("staffid_xxx")
    mock_update.assert_called_once_with("u123", phone="13800138000")
    assert result.phone == "13800138000"
    assert result.channel_type == "wecom"


@pytest.mark.asyncio
async def test_db_no_phone_adapter_returns_empty_dict_no_writeback():
    """DB 无 phone + adapter 返回空 dict -> 不写回，User.phone=None"""
    adapter = MagicMock()
    adapter.get_user_info = AsyncMock(return_value={})

    with patch(
        "src.channels.agent_user_builder.UserDB.get_by_id",
        return_value=_make_user_record(phone=None),
    ), patch(
        "src.channels.agent_user_builder.UserDB.update_info"
    ) as mock_update:
        result = await build_agent_user_for_channel(
            channel_type="dingtalk",
            channel_user_id="staffid_xxx",
            tenant_id="t1",
            adapter=adapter,
            user_id="u123",
        )

    mock_update.assert_not_called()
    assert result.phone is None


@pytest.mark.asyncio
async def test_db_no_phone_adapter_raises_no_writeback():
    """DB 无 phone + adapter 抛异常 -> User.phone=None，主流程不抛"""
    adapter = _make_adapter(raise_exc=RuntimeError("network error"))

    with patch(
        "src.channels.agent_user_builder.UserDB.get_by_id",
        return_value=_make_user_record(phone=None),
    ), patch(
        "src.channels.agent_user_builder.UserDB.update_info"
    ) as mock_update:
        result = await build_agent_user_for_channel(
            channel_type="feishu",
            channel_user_id="ou_xxx",
            tenant_id="t1",
            adapter=adapter,
            user_id="u123",
        )

    mock_update.assert_not_called()
    assert result.phone is None
    assert result.user_id == "u123"


@pytest.mark.asyncio
async def test_db_no_phone_writeback_fails_still_returns_mobile():
    """DB 无 phone + 写回 DB 失败 -> User.phone 仍为 mobile（本次仍注入），不抛"""
    adapter = _make_adapter(mobile="13800138000")

    with patch(
        "src.channels.agent_user_builder.UserDB.get_by_id",
        return_value=_make_user_record(phone=None),
    ), patch(
        "src.channels.agent_user_builder.UserDB.update_info",
        side_effect=RuntimeError("db error"),
    ):
        result = await build_agent_user_for_channel(
            channel_type="feishu",
            channel_user_id="ou_xxx",
            tenant_id="t1",
            adapter=adapter,
            user_id="u123",
        )

    assert result.phone == "13800138000"


@pytest.mark.asyncio
async def test_db_get_by_id_raises_returns_none():
    """UserDB.get_by_id 抛异常 -> 返回 None，不调 adapter"""
    adapter = _make_adapter(mobile="13800138000")

    with patch(
        "src.channels.agent_user_builder.UserDB.get_by_id",
        side_effect=RuntimeError("db connection lost"),
    ):
        result = await build_agent_user_for_channel(
            channel_type="feishu",
            channel_user_id="ou_xxx",
            tenant_id="t1",
            adapter=adapter,
            user_id="u123",
        )

    assert result is None
    adapter.get_user_info.assert_not_called()


@pytest.mark.asyncio
async def test_db_returns_none_returns_none():
    """UserDB.get_by_id 返回 None（用户不存在）-> 返回 None"""
    adapter = _make_adapter(mobile="13800138000")

    with patch(
        "src.channels.agent_user_builder.UserDB.get_by_id",
        return_value=None,
    ):
        result = await build_agent_user_for_channel(
            channel_type="feishu",
            channel_user_id="ou_xxx",
            tenant_id="t1",
            adapter=adapter,
            user_id="u123",
        )

    assert result is None
    adapter.get_user_info.assert_not_called()


@pytest.mark.asyncio
async def test_name_fallback_to_username_when_nickname_empty():
    """nickname 为空时 name 回退到 username"""
    adapter = _make_adapter(mobile="13800138000")

    with patch(
        "src.channels.agent_user_builder.UserDB.get_by_id",
        return_value=_make_user_record(
            phone="13900139000", nickname=None, username="alice"
        ),
    ):
        result = await build_agent_user_for_channel(
            channel_type="feishu",
            channel_user_id="ou_xxx",
            tenant_id="t1",
            adapter=adapter,
            user_id="u123",
        )

    assert result.name == "alice"


@pytest.mark.asyncio
async def test_feishu_mobile_field_extraction():
    """飞书 adapter 返回 mobile 字段被正确读取"""
    adapter = MagicMock()
    adapter.get_user_info = AsyncMock(
        return_value={
            "user_id": "ou_xxx",
            "name": "李四",
            "email": "li@x.com",
            "mobile": "13700137000",
            "avatar": "https://x.com/a.png",
        }
    )

    with patch(
        "src.channels.agent_user_builder.UserDB.get_by_id",
        return_value=_make_user_record(phone=None, nickname="李四"),
    ), patch(
        "src.channels.agent_user_builder.UserDB.update_info"
    ) as mock_update:
        result = await build_agent_user_for_channel(
            channel_type="feishu",
            channel_user_id="ou_xxx",
            tenant_id="t1",
            adapter=adapter,
            user_id="u123",
        )

    mock_update.assert_called_once_with("u123", phone="13700137000")
    assert result.phone == "13700137000"
    assert result.name == "李四"
