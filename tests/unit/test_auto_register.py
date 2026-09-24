"""IM 用户自动注册（auto_register）单元测试

覆盖 user_info_fetcher 按需抓取渠道资料（姓名/头像补全）的行为。
"""

import pytest
from unittest.mock import AsyncMock, patch

from src.saas.services.auto_register import ensure_user_registered


@pytest.mark.asyncio
async def test_existing_user_without_avatar_triggers_fetch():
    """本地用户缺头像时调用抓取器，并用抓取结果更新姓名/头像"""
    with patch("src.saas.services.auto_register._find_user_by_channel_id", return_value="u1"), \
         patch("src.saas.services.auto_register.UserDB") as mock_db, \
         patch("src.saas.services.auto_register._update_user_info_from_channel") as mock_update:
        mock_db.get_by_id.return_value = {
            "user_id": "u1", "avatar_url": None, "tenant_id": "t1", "source": "wecom",
        }
        fetcher = AsyncMock(return_value={"name": "张三", "avatar": "http://a/x.png"})

        uid = await ensure_user_registered(
            "wecom", "wxid123", "t1", source="wecom", user_info_fetcher=fetcher
        )

        assert uid == "u1"
        fetcher.assert_awaited_once()
        mock_update.assert_called_once_with("u1", {"name": "张三", "avatar": "http://a/x.png"})


@pytest.mark.asyncio
async def test_existing_user_with_avatar_skips_fetch():
    """本地用户已有头像时不调用抓取器（避免每条消息都请求渠道 API）"""
    with patch("src.saas.services.auto_register._find_user_by_channel_id", return_value="u1"), \
         patch("src.saas.services.auto_register.UserDB") as mock_db, \
         patch("src.saas.services.auto_register._update_user_info_from_channel") as mock_update:
        mock_db.get_by_id.return_value = {
            "user_id": "u1", "avatar_url": "http://a/x.png", "tenant_id": "t1", "source": "wecom",
        }
        fetcher = AsyncMock(return_value={"name": "张三", "avatar": "http://a/y.png"})

        uid = await ensure_user_registered(
            "wecom", "wxid123", "t1", source="wecom", user_info_fetcher=fetcher
        )

        assert uid == "u1"
        fetcher.assert_not_awaited()
        mock_update.assert_not_called()


@pytest.mark.asyncio
async def test_fetcher_failure_does_not_block_registration():
    """抓取器抛异常时不阻断注册主流程，也不更新资料"""
    with patch("src.saas.services.auto_register._find_user_by_channel_id", return_value="u1"), \
         patch("src.saas.services.auto_register.UserDB") as mock_db, \
         patch("src.saas.services.auto_register._update_user_info_from_channel") as mock_update:
        mock_db.get_by_id.return_value = {
            "user_id": "u1", "avatar_url": None, "tenant_id": "t1", "source": "wecom",
        }
        fetcher = AsyncMock(side_effect=RuntimeError("api down"))

        uid = await ensure_user_registered(
            "wecom", "wxid123", "t1", source="wecom", user_info_fetcher=fetcher
        )

        assert uid == "u1"
        mock_update.assert_not_called()


@pytest.mark.asyncio
async def test_new_user_fetches_info_and_saves_avatar():
    """新建用户时调用抓取器：昵称带入 create，头像写入 update_info"""
    with patch("src.saas.services.auto_register._find_user_by_channel_id", return_value=None), \
         patch("src.saas.services.auto_register.UserDB") as mock_db, \
         patch("src.saas.services.auto_register._save_channel_user_mapping"):
        mock_db.create.return_value = {"user_id": "u_new"}
        fetcher = AsyncMock(return_value={"name": "张三", "avatar": "http://a/x.png"})

        uid = await ensure_user_registered(
            "wecom", "wxid123", "t1", source="wecom", user_info_fetcher=fetcher
        )

        assert uid == "u_new"
        fetcher.assert_awaited_once()
        assert mock_db.create.call_args.kwargs["nickname"] == "张三"
        mock_db.update_info.assert_called_once_with("u_new", avatar_url="http://a/x.png")


@pytest.mark.asyncio
async def test_explicit_user_info_skips_fetch():
    """调用方已传 user_info 时（wecom_kf 语义）不再调用抓取器"""
    with patch("src.saas.services.auto_register._find_user_by_channel_id", return_value="u1"), \
         patch("src.saas.services.auto_register.UserDB") as mock_db, \
         patch("src.saas.services.auto_register._update_user_info_from_channel") as mock_update:
        mock_db.get_by_id.return_value = {
            "user_id": "u1", "avatar_url": None, "tenant_id": "t1", "source": "wecom_kf",
        }
        fetcher = AsyncMock(return_value={"name": "另一个人", "avatar": "http://a/z.png"})

        uid = await ensure_user_registered(
            "wecom_kf", "wmXXX", "t1",
            user_info={"name": "客户昵称", "avatar": "http://a/kf.png"},
            source="wecom_kf",
            user_info_fetcher=fetcher,
        )

        assert uid == "u1"
        fetcher.assert_not_awaited()
        mock_update.assert_called_once_with("u1", {"name": "客户昵称", "avatar": "http://a/kf.png"})
