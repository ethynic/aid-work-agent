"""
客服账号保存时接待人员校验与同步 单元测试

覆盖：
- api_client 新增的 get_user / servicer_add / servicer_del / servicer_list 方法（路径与参数构造）
- _raise_servicer_op_error 逐 userid 结果解析
- _sync_kf_servicers 全场景：新增 / 删除 / 无变化 / 清空 / 分批 / userid 不存在 / 企微接口报错
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from fastapi import HTTPException

from src.saas.api.wecom_kf_account import (
    _raise_servicer_op_error,
    _sync_kf_servicers,
)

pytestmark = pytest.mark.channels

_OK_TOP = {"errcode": 0, "errmsg": "ok"}
_OK_RESULT = {"errcode": 0, "result_list": [{"userid": "A", "errcode": 0, "errmsg": "success"}]}


def _api_client():
    from src.channels.wecom_kf.api_client import WeComKfApiClient

    client = WeComKfApiClient(corp_id="corp", secret="secret")
    # 必须设 return_value 为 dict：不设时 await AsyncMock 返回 AsyncMock，
    # result.get(...) 会产生未 await 的协程（RuntimeWarning）且 errcode!=0 恒为真
    client._request = AsyncMock(return_value=_OK_TOP)
    return client


def _adapter():
    """构造带 mock api_client 的 adapter。"""
    api = MagicMock()
    api.get_user = AsyncMock(return_value=_OK_TOP)
    api.servicer_list = AsyncMock(
        return_value={**_OK_TOP, "servicer_list": [{"userid": "B"}]}
    )
    api.servicer_add = AsyncMock(return_value=_OK_RESULT)
    api.servicer_del = AsyncMock(return_value=_OK_RESULT)
    adapter = MagicMock()
    adapter.api_client = api
    return adapter


# ============ 1. api_client 新方法 ============


class TestServicerApiClientMethods:
    @pytest.mark.asyncio
    async def test_get_user_passes_userid_param(self):
        client = _api_client()
        await client.get_user("qinshan")
        client._request.assert_awaited_once()
        args, kwargs = client._request.call_args
        assert args[0] == "GET"
        assert args[1] == "/cgi-bin/user/get"
        assert kwargs["extra_params"] == {"userid": "qinshan"}

    @pytest.mark.asyncio
    async def test_servicer_add_passes_body(self):
        client = _api_client()
        await client.servicer_add("kfid_1", ["a", "b"])
        client._request.assert_awaited_once()
        args, kwargs = client._request.call_args
        assert args[0] == "POST"
        assert args[1] == "/cgi-bin/kf/servicer/add"
        assert kwargs["json_body"] == {"open_kfid": "kfid_1", "userid_list": ["a", "b"]}

    @pytest.mark.asyncio
    async def test_servicer_del_passes_body(self):
        client = _api_client()
        await client.servicer_del("kfid_1", ["a"])
        client._request.assert_awaited_once()
        args, kwargs = client._request.call_args
        assert args[0] == "POST"
        assert args[1] == "/cgi-bin/kf/servicer/del"
        assert kwargs["json_body"] == {"open_kfid": "kfid_1", "userid_list": ["a"]}

    @pytest.mark.asyncio
    async def test_servicer_list_passes_open_kfid_param(self):
        client = _api_client()
        await client.servicer_list("kfid_1")
        client._request.assert_awaited_once()
        args, kwargs = client._request.call_args
        assert args[0] == "GET"
        assert args[1] == "/cgi-bin/kf/servicer/list"
        assert kwargs["extra_params"] == {"open_kfid": "kfid_1"}


# ============ 2. _raise_servicer_op_error ============


class TestRaiseServicerOpError:
    def test_all_success_no_error(self):
        result = {
            "errcode": 0,
            "result_list": [
                {"userid": "A", "errcode": 0, "errmsg": "success"},
                {"userid": "B", "errcode": 0, "errmsg": "ignored"},
            ],
        }
        _raise_servicer_op_error(result, "添加接待人员")  # 不抛异常

    def test_top_level_error_raises(self):
        with pytest.raises(HTTPException) as exc:
            _raise_servicer_op_error({"errcode": 40058, "errmsg": "bad request"}, "添加接待人员")
        assert exc.value.status_code == 400
        assert "40058" in str(exc.value.detail) or "bad request" in str(exc.value.detail)

    def test_result_list_failure_raises(self):
        result = {
            "errcode": 0,
            "result_list": [
                {"userid": "A", "errcode": 0, "errmsg": "success"},
                {"userid": "qinshan", "errcode": 60111, "errmsg": "userid not found"},
            ],
        }
        with pytest.raises(HTTPException) as exc:
            _raise_servicer_op_error(result, "添加接待人员")
        assert exc.value.status_code == 400
        assert "qinshan" in str(exc.value.detail)
        assert "userid not found" in str(exc.value.detail)


# ============ 3. _sync_kf_servicers ============


class TestSyncKfServicers:
    @pytest.mark.asyncio
    async def test_add_new_and_del_removed(self):
        """目标 [A, B]，企微当前 [B, C] -> add A, del C"""
        adapter = _adapter()
        adapter.api_client.servicer_list.return_value = {
            **_OK_TOP,
            "servicer_list": [{"userid": "B"}, {"userid": "C"}],
        }

        await _sync_kf_servicers(adapter, "kfid_1", ["A", "B"])

        # 校验两个目标 userid 均查通讯录
        assert adapter.api_client.get_user.await_count == 2
        get_users = [c.args[0] for c in adapter.api_client.get_user.await_args_list]
        assert set(get_users) == {"A", "B"}

        adapter.api_client.servicer_add.assert_awaited_once_with("kfid_1", ["A"])
        adapter.api_client.servicer_del.assert_awaited_once_with("kfid_1", ["C"])

    @pytest.mark.asyncio
    async def test_no_change_skips_operations(self):
        adapter = _adapter()
        adapter.api_client.servicer_list.return_value = {
            **_OK_TOP,
            "servicer_list": [{"userid": "A"}, {"userid": "B"}],
        }

        await _sync_kf_servicers(adapter, "kfid_1", ["A", "B"])

        adapter.api_client.servicer_add.assert_not_awaited()
        adapter.api_client.servicer_del.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_userid_raises_and_no_mutation(self):
        adapter = _adapter()
        adapter.api_client.get_user.side_effect = [
            {**_OK_TOP, "userid": "A"},
            {"errcode": 60111, "errmsg": "invalid string value `qinshan`. userid not found"},
        ]

        with pytest.raises(HTTPException) as exc:
            await _sync_kf_servicers(adapter, "kfid_1", ["A", "qinshan"])

        assert exc.value.status_code == 400
        assert "qinshan" in str(exc.value.detail)
        adapter.api_client.servicer_add.assert_not_awaited()
        adapter.api_client.servicer_del.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_case_mismatch_uses_canonical_userid(self):
        """用户输入大小写与企微 canonical 不一致时，用 user/get 返回的 canonical userid
        计算差集，避免同一用户被同时判为"新增+删除"而反复 add/del。"""
        adapter = _adapter()
        # user/get 返回 canonical 小写 admin；用户输入大写 Admin
        adapter.api_client.get_user.return_value = {**_OK_TOP, "userid": "admin"}
        adapter.api_client.servicer_list.return_value = {
            **_OK_TOP,
            "servicer_list": [{"userid": "admin"}],
        }

        await _sync_kf_servicers(adapter, "kfid_1", ["Admin"])

        adapter.api_client.servicer_add.assert_not_awaited()
        adapter.api_client.servicer_del.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_clear_all_deletes_all(self):
        adapter = _adapter()
        adapter.api_client.servicer_list.return_value = {
            **_OK_TOP,
            "servicer_list": [{"userid": "A"}, {"userid": "B"}],
        }

        await _sync_kf_servicers(adapter, "kfid_1", [])

        adapter.api_client.get_user.assert_not_awaited()
        adapter.api_client.servicer_add.assert_not_awaited()
        adapter.api_client.servicer_del.assert_awaited_once_with("kfid_1", ["A", "B"])

    @pytest.mark.asyncio
    async def test_batch_add_over_100(self):
        adapter = _adapter()
        adapter.api_client.servicer_list.return_value = {**_OK_TOP, "servicer_list": []}
        target = [f"user_{i}" for i in range(150)]

        await _sync_kf_servicers(adapter, "kfid_1", target)

        assert adapter.api_client.servicer_add.await_count == 2
        first, second = adapter.api_client.servicer_add.await_args_list
        assert len(first.args[1]) == 100
        assert len(second.args[1]) == 50

    @pytest.mark.asyncio
    async def test_list_failure_raises(self):
        adapter = _adapter()
        adapter.api_client.servicer_list.return_value = {
            "errcode": 40058,
            "errmsg": "invalid request",
        }

        with pytest.raises(HTTPException) as exc:
            await _sync_kf_servicers(adapter, "kfid_1", ["A"])

        assert exc.value.status_code == 400
        assert "获取企微接待人员列表失败" in str(exc.value.detail)

    @pytest.mark.asyncio
    async def test_add_top_level_failure_raises(self):
        adapter = _adapter()
        adapter.api_client.servicer_list.return_value = {**_OK_TOP, "servicer_list": []}
        adapter.api_client.servicer_add.return_value = {
            "errcode": 40058,
            "errmsg": "missing field",
        }

        with pytest.raises(HTTPException) as exc:
            await _sync_kf_servicers(adapter, "kfid_1", ["A"])

        assert exc.value.status_code == 400
        assert "添加接待人员失败" in str(exc.value.detail)

    @pytest.mark.asyncio
    async def test_add_result_failure_raises(self):
        adapter = _adapter()
        adapter.api_client.servicer_list.return_value = {**_OK_TOP, "servicer_list": []}
        adapter.api_client.servicer_add.return_value = {
            "errcode": 0,
            "result_list": [{"userid": "A", "errcode": 95014, "errmsg": "user is not a servicer"}],
        }

        with pytest.raises(HTTPException) as exc:
            await _sync_kf_servicers(adapter, "kfid_1", ["A"])

        assert exc.value.status_code == 400
        assert "95014" in str(exc.value.detail) or "user is not a servicer" in str(exc.value.detail)

    @pytest.mark.asyncio
    async def test_del_failure_raises(self):
        adapter = _adapter()
        adapter.api_client.servicer_list.return_value = {
            **_OK_TOP,
            "servicer_list": [{"userid": "A"}],
        }
        adapter.api_client.servicer_del.return_value = {
            "errcode": 0,
            "result_list": [{"userid": "A", "errcode": -1, "errmsg": "unknown"}],
        }

        with pytest.raises(HTTPException) as exc:
            await _sync_kf_servicers(adapter, "kfid_1", [])

        assert exc.value.status_code == 400
        assert "删除接待人员失败" in str(exc.value.detail)
