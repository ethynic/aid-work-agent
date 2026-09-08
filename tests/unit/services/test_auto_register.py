"""auto_register 渠道用户昵称/头像刷新逻辑测试"""

from unittest.mock import patch

import pytest

from src.saas.services import auto_register as ar


pytestmark = pytest.mark.unit


def _run_update(existing_user, user_info):
    with patch.object(ar.UserDB, "get_by_id", return_value=existing_user) as m_get, \
            patch.object(ar.UserDB, "update_info") as m_update:
        ar._update_user_info_from_channel("user_x", user_info)
        assert m_get.called
        return m_update


class TestUpdateUserInfoFromChannel:
    def test_nickname_override_when_channel_differs(self):
        """渠道昵称与现值不同时覆盖（本次 bug：首次异常值被永久固化）"""
        existing = {"nickname": "小腾老师", "avatar_url": "http://old/1", "wx_openid": "", "wx_unionid": ""}
        m_update = _run_update(existing, {"name": "曹老师", "avatar": "http://new/1"})
        m_update.assert_called_once()
        kwargs = m_update.call_args.kwargs
        assert kwargs["nickname"] == "曹老师"
        assert kwargs["avatar_url"] == "http://new/1"

    def test_no_update_when_values_unchanged(self):
        """渠道返回与现值相同则不写库"""
        existing = {"nickname": "曹老师", "avatar_url": "http://same/1", "wx_openid": "", "wx_unionid": ""}
        m_update = _run_update(existing, {"name": "曹老师", "avatar": "http://same/1"})
        m_update.assert_not_called()

    def test_fill_empty_nickname_and_avatar(self):
        """昵称/头像为空时写入"""
        existing = {"nickname": None, "avatar_url": None, "wx_openid": "", "wx_unionid": ""}
        m_update = _run_update(existing, {"name": "曹老师", "avatar": "http://new/1"})
        m_update.assert_called_once()
        kwargs = m_update.call_args.kwargs
        assert kwargs["nickname"] == "曹老师"
        assert kwargs["avatar_url"] == "http://new/1"

    def test_wx_openid_kept_when_present(self):
        """wx_openid 已有值不覆盖；wx_unionid 为空时补写"""
        existing = {"nickname": "曹老师", "avatar_url": "http://same/1", "wx_openid": "existing", "wx_unionid": ""}
        m_update = _run_update(existing, {
            "name": "曹老师", "avatar": "http://same/1",
            "wx_openid": "new_openid", "wx_unionid": "new_unionid",
        })
        m_update.assert_called_once()
        kwargs = m_update.call_args.kwargs
        assert "wx_openid" not in kwargs
        assert kwargs["wx_unionid"] == "new_unionid"

    def test_blank_name_and_avatar_ignored(self):
        """渠道返回空昵称/空头像时跳过"""
        existing = {"nickname": None, "avatar_url": None, "wx_openid": "", "wx_unionid": ""}
        m_update = _run_update(existing, {"name": "", "avatar": ""})
        m_update.assert_not_called()

    def test_avatar_override_only(self):
        """仅头像变化时只更新头像，不动昵称"""
        existing = {"nickname": "曹老师", "avatar_url": "http://old/1", "wx_openid": "", "wx_unionid": ""}
        m_update = _run_update(existing, {"name": "曹老师", "avatar": "http://new/1"})
        m_update.assert_called_once()
        kwargs = m_update.call_args.kwargs
        assert kwargs["avatar_url"] == "http://new/1"
        assert "nickname" not in kwargs

    def test_gender_written_when_changed(self):
        """渠道返回性别且与现值不同时写入（企微 0未知/1男/2女）"""
        existing = {"nickname": "夜未央", "avatar_url": "http://same/1", "gender": 0, "wx_openid": "", "wx_unionid": ""}
        m_update = _run_update(existing, {"name": "夜未央", "avatar": "http://same/1", "gender": 2})
        m_update.assert_called_once()
        assert m_update.call_args.kwargs["gender"] == 2

    def test_gender_zero_ignored(self):
        """渠道返回性别 0（未知）时不覆盖已有值"""
        existing = {"nickname": "夜未央", "avatar_url": "http://same/1", "gender": 1, "wx_openid": "", "wx_unionid": ""}
        m_update = _run_update(existing, {"name": "夜未央", "avatar": "http://same/1", "gender": 0})
        m_update.assert_not_called()

    def test_gender_unchanged_not_written(self):
        """性别与现值相同则不写库"""
        existing = {"nickname": "夜未央", "avatar_url": "http://same/1", "gender": 2, "wx_openid": "", "wx_unionid": ""}
        m_update = _run_update(existing, {"name": "夜未央", "avatar": "http://same/1", "gender": 2})
        m_update.assert_not_called()
