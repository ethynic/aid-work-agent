"""
微信客服账号引流（kf-account-referral）Phase 1 单元测试

覆盖：
- api_client 新增的 5 个企微 API 方法（account_add/del/update/list + add_contact_way）
- wecom_kf_account.resolve_scene 场景反查
- channel_routes._is_kf_account_blocked 到期/积分拦截 + 固定话术回复落库
- CustomerReferralDB 的 SQL 构造（record/referral_stats/count_referred_messages/sum_kf_account_credit）
- 二维码 data URL 生成、账号不存在判定

方案见：docs/channel/wecom_kf/kf-account-referral-plan.md
"""

import base64
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.channels


# ---------- helpers ----------


class _FakeMsg:
    """模拟 unified_msg，仅暴露拦截逻辑用到的字段"""

    def __init__(self, user_id="ext_001", message_id="msg_123"):
        self.user_id = user_id
        self.message_id = message_id
        self.text = "你好"


def _mock_db_connection(cursor_rows):
    """构造 mock 的 get_db_connection 上下文，cursor.fetchone 依次返回 cursor_rows"""
    cursor = MagicMock()
    cursor.fetchone.side_effect = list(cursor_rows)
    cursor.fetchall.return_value = []
    cursor.rowcount = 1
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn, cursor


def _api_client():
    from src.channels.wecom_kf.api_client import WeComKfApiClient

    client = WeComKfApiClient(corp_id="corp", secret="secret")
    client._request = AsyncMock()
    return client


# ============ 1. api_client 企微账号 API ============


class TestKfAccountApiMethods:
    @pytest.mark.asyncio
    async def test_account_add(self):
        client = _api_client()
        client._request.return_value = {"errcode": 0, "open_kfid": "wk_abc123"}

        result = await client.account_add(name="引流客服", media_id="MEDIA_1")

        client._request.assert_called_once_with(
            "POST", "/cgi-bin/kf/account/add",
            json_body={"name": "引流客服", "media_id": "MEDIA_1"},
        )
        assert result["open_kfid"] == "wk_abc123"

    @pytest.mark.asyncio
    async def test_account_del(self):
        client = _api_client()
        client._request.return_value = {"errcode": 0}

        result = await client.account_del("wk_abc123")

        client._request.assert_called_once_with(
            "POST", "/cgi-bin/kf/account/del", json_body={"open_kfid": "wk_abc123"}
        )
        assert result["errcode"] == 0

    @pytest.mark.asyncio
    async def test_account_update_only_provided_fields(self):
        """account_update 只传非空字段，未提供的字段不发送"""
        client = _api_client()
        client._request.return_value = {"errcode": 0}

        # 只改名称
        await client.account_update("wk_1", name="新名称")
        body = client._request.call_args[1]["json_body"]
        assert body == {"open_kfid": "wk_1", "name": "新名称"}
        assert "media_id" not in body

        # 只改头像
        await client.account_update("wk_1", media_id="MEDIA_2")
        body = client._request.call_args[1]["json_body"]
        assert body == {"open_kfid": "wk_1", "media_id": "MEDIA_2"}
        assert "name" not in body

    @pytest.mark.asyncio
    async def test_account_list(self):
        client = _api_client()
        client._request.return_value = {
            "errcode": 0,
            "account_list": [{"open_kfid": "wk_1", "name": "客服A"}],
        }

        result = await client.account_list(offset=10, limit=50)

        client._request.assert_called_once_with(
            "POST", "/cgi-bin/kf/account/list", json_body={"offset": 10, "limit": 50}
        )
        assert len(result["account_list"]) == 1

    @pytest.mark.asyncio
    async def test_add_contact_way(self):
        client = _api_client()
        client._request.return_value = {"errcode": 0, "url": "https://work.weixin.qq.com/kfid/wk_1"}

        result = await client.add_contact_way("wk_1", "kf_ab12cd34")

        client._request.assert_called_once_with(
            "POST", "/cgi-bin/kf/add_contact_way",
            json_body={"open_kfid": "wk_1", "scene": "kf_ab12cd34"},
        )
        assert "kfid/wk_1" in result["url"]


# ============ 2. resolve_scene 场景反查 ============


class TestResolveScene:
    def _patch_list(self, kf_accounts):
        return patch(
            "src.saas.api.wecom_kf_account.ChannelConfigDB.list_by_tenant",
            return_value=[{"config_id": "cfg_1", "config": {"kf_account": kf_accounts}}],
        )

    def test_match_returns_config_and_kf(self):
        from src.saas.api.wecom_kf_account import resolve_scene

        kf = {"open_kfid": "wk_1", "scene": "kf_abc", "tenant_user_id": "u_1"}
        with self._patch_list([kf]):
            cfg_id, binding = resolve_scene("t1", "kf_abc")
        assert cfg_id == "cfg_1"
        assert binding["tenant_user_id"] == "u_1"

    def test_no_match_returns_none(self):
        from src.saas.api.wecom_kf_account import resolve_scene

        kf = {"open_kfid": "wk_1", "scene": "kf_abc", "tenant_user_id": "u_1"}
        with self._patch_list([kf]):
            cfg_id, binding = resolve_scene("t1", "kf_zzz")
        assert (cfg_id, binding) == (None, None)

    def test_empty_scene_returns_none(self):
        from src.saas.api.wecom_kf_account import resolve_scene

        with self._patch_list([]):
            cfg_id, binding = resolve_scene("t1", "")
        assert (cfg_id, binding) == (None, None)


# ============ 3. 消息入口拦截 ============


class TestKfAccountBlocked:
    def _make_adapter(self):
        adapter = MagicMock()
        adapter.send_text = AsyncMock(return_value=True)
        return adapter

    def _patch_ctx(self):
        """mock 拦截依赖：channel_session_manager.add_message + sum_kf_account_credit"""
        sm = MagicMock()
        sm.add_message = MagicMock(return_value="msg_xxx")
        # sum_kf_account_credit 是同步方法，用 MagicMock 而非 AsyncMock
        mock_sum = MagicMock(return_value=0.0)
        patches = [
            patch("src.saas.api.channel_routes.channel_session_manager", sm),
            patch(
                "src.saas.api.channel_routes.CustomerReferralDB.sum_kf_account_credit",
                new=mock_sum,
            ),
        ]
        return patches, sm, mock_sum

    @pytest.mark.asyncio
    async def test_expired_blocks_and_sends_expired_msg(self):
        """expire_at 到期（过期日次日）→ 拦截，发送 MSG_EXPIRED 并落库 user+assistant"""
        from src.channels.wecom_kf.prompts import MSG_EXPIRED
        from src.saas.api.channel_routes import _is_kf_account_blocked

        adapter = self._make_adapter()
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        kf_config = {"expire_at": yesterday, "credit_limit": 0}

        patches, sm, mock_sum = self._patch_ctx()
        with patches[0], patches[1]:
            blocked = await _is_kf_account_blocked(
                adapter, kf_config, "t1", "wk_1", _FakeMsg(), "sess_1", "你好"
            )

        assert blocked is True
        adapter.send_text.assert_awaited_once_with(MSG_EXPIRED, "ext_001")
        # 用户消息 + assistant 话术都应落库（add_message 使用关键字参数）
        calls = sm.add_message.call_args_list
        assert len(calls) == 2
        assert [c.kwargs["role"] for c in calls] == ["user", "assistant"]
        assert calls[1].kwargs["content"] == MSG_EXPIRED

    @pytest.mark.asyncio
    async def test_expire_at_today_still_valid(self):
        """到期日当天仍有效（+23h59m59s 容差）"""
        from src.saas.api.channel_routes import _is_kf_account_blocked

        adapter = self._make_adapter()
        today = datetime.now().strftime("%Y-%m-%d")
        kf_config = {"expire_at": today, "credit_limit": 0}

        patches, sm, mock_sum = self._patch_ctx()
        with patches[0], patches[1]:
            blocked = await _is_kf_account_blocked(
                adapter, kf_config, "t1", "wk_1", _FakeMsg(), "sess_1", "你好"
            )

        assert blocked is False
        adapter.send_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_credit_exhausted_blocks(self):
        """credit_limit>0 且累计积分>=上限 → 拦截，发送 MSG_CREDIT_EXHAUSTED"""
        from src.channels.wecom_kf.prompts import MSG_CREDIT_EXHAUSTED
        from src.saas.api.channel_routes import _is_kf_account_blocked

        adapter = self._make_adapter()
        kf_config = {"credit_limit": 100}

        patches, sm, mock_sum = self._patch_ctx()
        mock_sum.return_value = 100.0
        with patches[0], patches[1]:
            blocked = await _is_kf_account_blocked(
                adapter, kf_config, "t1", "wk_1", _FakeMsg(), "sess_1", "你好"
            )

        assert blocked is True
        mock_sum.assert_called_once_with("t1", "wk_1")
        adapter.send_text.assert_awaited_once_with(MSG_CREDIT_EXHAUSTED, "ext_001")

    @pytest.mark.asyncio
    async def test_credit_limit_zero_skips_query(self):
        """credit_limit=0（跟随租户积分）→ 不查询账号积分，不拦截"""
        from src.saas.api.channel_routes import _is_kf_account_blocked

        adapter = self._make_adapter()
        kf_config = {"credit_limit": 0}

        patches, sm, mock_sum = self._patch_ctx()
        with patches[0], patches[1]:
            blocked = await _is_kf_account_blocked(
                adapter, kf_config, "t1", "wk_1", _FakeMsg(), "sess_1", "你好"
            )

        assert blocked is False
        mock_sum.assert_not_called()

    @pytest.mark.asyncio
    async def test_credit_not_exhausted_passes(self):
        """credit_limit>0 但累计积分未达上限 → 不拦截"""
        from src.saas.api.channel_routes import _is_kf_account_blocked

        adapter = self._make_adapter()
        kf_config = {"credit_limit": 100}

        patches, sm, mock_sum = self._patch_ctx()
        mock_sum.return_value = 50.0
        with patches[0], patches[1]:
            blocked = await _is_kf_account_blocked(
                adapter, kf_config, "t1", "wk_1", _FakeMsg(), "sess_1", "你好"
            )

        assert blocked is False


# ============ 4. CustomerReferralDB SQL 构造 ============


class TestCustomerReferralDB:
    def test_record_uses_on_conflict_do_nothing(self):
        from src.db.models import CustomerReferralDB

        conn, cursor = _mock_db_connection([None])
        with patch("src.db.models.get_db_connection") as m:
            m.return_value.__enter__.return_value = conn
            ok = CustomerReferralDB.record(
                tenant_id="t1", referrer_user_id="u_1",
                customer_user_id="c_1", open_kfid="wk_1", scene="kf_abc",
            )

        sql = cursor.execute.call_args[0][0]
        assert "ON CONFLICT (customer_user_id) DO NOTHING" in sql
        assert ok is True  # rowcount=1

    def test_referral_stats_sql_and_ratio(self):
        """referral_stats：日期条件拼接 + 员工占比计算"""
        from src.db.models import CustomerReferralDB

        # 第一次 fetchone=总数 32；第二次 fetchall=员工分组（12 -> 37.5%）
        cursor_rows = [{"cnt": 32}]
        cursor = MagicMock()
        cursor.fetchone.side_effect = cursor_rows
        cursor.fetchall.return_value = [
            {"referrer_user_id": "u_1", "referrer_nickname": "张伟", "referrer_username": "zw", "referral_count": 12},
            {"referrer_user_id": "u_2", "referrer_nickname": None, "referrer_username": None, "referral_count": 8},
        ]
        conn = MagicMock()
        conn.cursor.return_value = cursor
        with patch("src.db.models.get_db_connection") as m:
            m.return_value.__enter__.return_value = conn
            result = CustomerReferralDB.referral_stats("t1", start_date="2026-08-01", end_date="2026-08-31")

        assert result["total_referrals"] == 32
        assert result["referrers"][0]["ratio"] == 37.5
        assert result["referrers"][0]["referrer_name"] == "张伟"
        # 员工被删除时显示「已删除员工」并保留计数
        assert result["referrers"][1]["referrer_name"] == "已删除员工"
        assert result["referrers"][1]["referral_count"] == 8

        # 日期条件以占位符参数追加（start >= / end 含当日，SQL 内 +1 天）
        sql = cursor.execute.call_args_list[1][0][0]
        assert "cr.created_at >= %s" in sql
        assert "cr.created_at < (%s::date + INTERVAL '1 day')" in sql

    def test_count_referred_messages_sql(self):
        """count_referred_messages：三表 join + is_recalled=FALSE + 日期过滤"""
        from src.db.models import CustomerReferralDB

        conn, cursor = _mock_db_connection([{"cnt": 158}])
        with patch("src.db.models.get_db_connection") as m:
            m.return_value.__enter__.return_value = conn
            count = CustomerReferralDB.count_referred_messages(
                "t1", start_date="2026-08-01", end_date="2026-08-31"
            )

        sql = cursor.execute.call_args[0][0]
        assert "JOIN channel_sessions cs ON cs.session_id = cm.session_id" in sql
        assert "JOIN customer_referrals cr ON cr.customer_user_id = cs.user_id" in sql
        assert "cm.is_recalled = FALSE" in sql
        assert "cm.created_at >= %s" in sql
        assert "cm.created_at < (%s::date + INTERVAL '1 day')" in sql
        assert count == 158

    def test_sum_kf_account_credit_sql(self):
        """sum_kf_account_credit：按 open_kfid 归集 chat_records.credit_cost"""
        from src.db.models import CustomerReferralDB

        conn, cursor = _mock_db_connection([{"total_cost": 64.5}])
        with patch("src.db.models.get_db_connection") as m:
            m.return_value.__enter__.return_value = conn
            used = CustomerReferralDB.sum_kf_account_credit("t1", "wk_1")

        sql, params = cursor.execute.call_args[0]
        assert "COALESCE(SUM(cr.credit_cost), 0)" in sql
        assert "cs.channel_type = 'wecom_kf'" in sql
        assert "cs.channel_chat_id = %s" in sql
        assert params[1] == "wk_1"
        assert used == 64.5

    def test_sum_kf_account_credit_empty_returns_zero(self):
        from src.db.models import CustomerReferralDB

        conn, cursor = _mock_db_connection([{"total_cost": 0}])
        with patch("src.db.models.get_db_connection") as m:
            m.return_value.__enter__.return_value = conn
            assert CustomerReferralDB.sum_kf_account_credit("t1", "wk_1") == 0


# ============ 5. 二维码 / 账号不存在判定 ============


class TestKfAccountUtils:
    def test_build_qr_data_url(self):
        """_build_qr_data_url 返回合法 base64 PNG data URL"""
        from src.saas.api.wecom_kf_account import _build_qr_data_url

        data_url = _build_qr_data_url("https://work.weixin.qq.com/kfid/wk_1")
        assert data_url.startswith("data:image/png;base64,")
        raw = base64.b64decode(data_url.split(",", 1)[1])
        assert raw[:8] == b"\x89PNG\r\n\x1a\n"  # PNG magic bytes

    def test_is_account_not_exists(self):
        """企微"账号不存在"类 errmsg 判定（幂等删除）"""
        from src.saas.api.wecom_kf_account import _is_account_not_exists

        assert _is_account_not_exists({"errcode": 45009, "errmsg": "账号不存在"})
        assert _is_account_not_exists({"errcode": 48002, "errmsg": "account not exist"})
        assert not _is_account_not_exists({"errcode": 0, "errmsg": "ok"})
        assert not _is_account_not_exists({"errcode": -1, "errmsg": "system error"})


class TestKfAvatarPersistence:
    """客服头像本地持久化（avatar_file_id）：创建/编辑注册到 ImageRegistry，视图回显"""

    def test_to_account_view_returns_avatar(self):
        """_to_account_view 返回 avatar_file_id / avatar_download_url（编辑弹框回显依据）"""
        from src.saas.api.wecom_kf_account import _to_account_view

        kf = {"open_kfid": "wk_1", "name": "高老师", "avatar_file_id": "file_abc"}
        view = _to_account_view(kf, "t1", "cfg_a")
        assert view["avatar_file_id"] == "file_abc"
        assert view["avatar_download_url"] == "/api/files/file_abc/download"

    def test_to_account_view_avatar_empty_when_missing(self):
        """本地无 avatar_file_id（历史账号/走 logo 兜底）时，头像字段为空"""
        from src.saas.api.wecom_kf_account import _to_account_view

        kf = {"open_kfid": "wk_1", "name": "高老师"}
        view = _to_account_view(kf, "t1", "cfg_a")
        assert view["avatar_file_id"] is None
        assert view["avatar_download_url"] == ""

    @pytest.mark.asyncio
    async def test_create_with_avatar_registers_local(self):
        """创建账号带自定义头像时，注册本地 avatar_file_id 并写入配置"""
        from src.saas.api.wecom_kf_account import KfAccountCreate, create_kf_account

        cfg = {"config_id": "cfg_a", "config": {"kf_account": []}}
        adapter = MagicMock()
        adapter.api_client = MagicMock()
        adapter.api_client.account_add = AsyncMock(return_value={"errcode": 0, "open_kfid": "wk_ava"})
        adapter.api_client.add_contact_way = AsyncMock(
            return_value={"errcode": 0, "url": "https://work.weixin.qq.com/kfid/wk_ava"}
        )
        mock_update = MagicMock(return_value=True)
        mock_register = AsyncMock(return_value="file_ava")
        patches = [
            patch(
                "src.saas.api.wecom_kf_account.require_admin",
                return_value={"tenant_id": "t1", "user_id": "u1"},
            ),
            patch(
                "src.saas.api.wecom_kf_account.ChannelConfigDB.list_by_tenant",
                return_value=[cfg],
            ),
            patch(
                "src.saas.api.wecom_kf_account.ChannelFactory.create_from_tenant_config",
                AsyncMock(return_value=(adapter, "cfg_a", None)),
            ),
            patch(
                "src.saas.api.wecom_kf_account._decode_avatar",
                AsyncMock(return_value=b"\x89PNG\r\n\x1a\nfake-avatar"),
            ),
            patch(
                "src.saas.api.wecom_kf_account._resolve_avatar_media_id",
                AsyncMock(return_value="MEDIA_1"),
            ),
            patch("src.saas.api.wecom_kf_account._register_avatar", mock_register),
            patch(
                "src.saas.api.wecom_kf_account._build_qr_data_url",
                return_value="data:image/png;base64,xxx",
            ),
            patch("src.saas.api.wecom_kf_account.ChannelConfigDB.update", mock_update),
            patch(
                "src.saas.api.wecom_kf_account.ChannelFactory.invalidate_adapter",
                AsyncMock(),
            ),
        ]
        body = KfAccountCreate(
            config_id="cfg_a", name="高老师", tenant_user_id="u1",
            avatar_base64="cG5n", allow_agent_transfer=True,
        )
        with (
            patches[0], patches[1], patches[2], patches[3], patches[4],
            patches[5], patches[6], patches[7], patches[8],
        ):
            await create_kf_account(MagicMock(), body)

        mock_register.assert_awaited_once()
        new_kf = mock_update.call_args.args[1]["kf_account"][-1]
        assert new_kf["avatar_file_id"] == "file_ava"

    @pytest.mark.asyncio
    async def test_update_avatar_registers_and_cleans_old(self):
        """编辑头像时注册新 file_id、清理旧 file_id，且企微 account_update 被调用"""
        from src.saas.api.wecom_kf_account import KfAccountUpdate, update_kf_account

        kf = {"open_kfid": "wk_1", "name": "高老师", "avatar_file_id": "file_old"}
        config_dict = {"kf_account": [kf]}
        adapter = MagicMock()
        adapter.api_client = MagicMock()
        adapter.api_client.account_update = AsyncMock(return_value={"errcode": 0})
        mock_update = MagicMock()
        mock_register = AsyncMock(return_value="file_new")
        mock_clean = AsyncMock()
        patches = [
            patch(
                "src.saas.api.wecom_kf_account.require_admin",
                return_value={"tenant_id": "t1", "user_id": "u1"},
            ),
            patch(
                "src.saas.api.wecom_kf_account._find_kf_entry",
                return_value=("cfg_a", config_dict, kf),
            ),
            patch(
                "src.saas.api.wecom_kf_account.ChannelFactory.create_from_tenant_config",
                AsyncMock(return_value=(adapter, "cfg_a", None)),
            ),
            patch(
                "src.saas.api.wecom_kf_account._decode_avatar",
                AsyncMock(return_value=b"\x89PNG\r\n\x1a\nfake-avatar"),
            ),
            patch(
                "src.saas.api.wecom_kf_account._resolve_avatar_media_id",
                AsyncMock(return_value="MEDIA_NEW"),
            ),
            patch("src.saas.api.wecom_kf_account._register_avatar", mock_register),
            patch("src.saas.api.wecom_kf_account._cleanup_avatar", mock_clean),
            patch("src.saas.api.wecom_kf_account.ChannelConfigDB.update", mock_update),
            patch(
                "src.saas.api.wecom_kf_account.ChannelFactory.invalidate_adapter",
                AsyncMock(),
            ),
            patch(
                "src.saas.api.wecom_kf_account._to_account_view",
                return_value={},
            ),
        ]
        body = KfAccountUpdate(name="高老师", avatar_base64="cG5n")
        with (
            patches[0], patches[1], patches[2], patches[3], patches[4],
            patches[5], patches[6], patches[7], patches[8], patches[9],
        ):
            await update_kf_account(MagicMock(), "wk_1", body)

        adapter.api_client.account_update.assert_awaited_once()
        mock_register.assert_awaited_once()
        mock_clean.assert_awaited_once_with("file_old")
        assert kf["avatar_file_id"] == "file_new"


# ============ 6. create_kf_account 目标渠道定位（同租户多条 wecom_kf 配置） ============


class TestCreateKfAccountConfigId:
    def _mock_adapter(self, open_kfid="wk_new_1"):
        adapter = MagicMock()
        adapter.api_client = MagicMock()
        adapter.api_client.account_add = AsyncMock(
            return_value={"errcode": 0, "open_kfid": open_kfid}
        )
        adapter.api_client.add_contact_way = AsyncMock(
            return_value={"errcode": 0, "url": f"https://work.weixin.qq.com/kfid/{open_kfid}"}
        )
        return adapter

    def _patches(self, configs, adapter, config_id):
        mock_create = AsyncMock(return_value=(adapter, config_id, None))
        mock_update = MagicMock(return_value=True)
        patches = [
            patch(
                "src.saas.api.wecom_kf_account.require_admin",
                return_value={"tenant_id": "t1", "user_id": "u1"},
            ),
            patch(
                "src.saas.api.wecom_kf_account.ChannelConfigDB.list_by_tenant",
                return_value=configs,
            ),
            patch(
                "src.saas.api.wecom_kf_account.ChannelFactory.create_from_tenant_config",
                mock_create,
            ),
            patch(
                "src.saas.api.wecom_kf_account._resolve_avatar_media_id",
                AsyncMock(return_value="MEDIA_1"),
            ),
            patch(
                "src.saas.api.wecom_kf_account._build_qr_data_url",
                return_value="data:image/png;base64,xxx",
            ),
            patch("src.saas.api.wecom_kf_account.ChannelConfigDB.update", mock_update),
            patch(
                "src.saas.api.wecom_kf_account.ChannelFactory.invalidate_adapter",
                AsyncMock(),
            ),
        ]
        return patches, mock_create, mock_update

    @pytest.mark.asyncio
    async def test_create_writes_to_specified_config(self):
        """传 config_id 时，账号写入指定渠道配置（同租户多条 wecom_kf 配置归属正确）"""
        from src.saas.api.wecom_kf_account import KfAccountCreate, create_kf_account

        cfg_a = {"config_id": "cfg_a", "config": {"kf_account": []}}
        cfg_b = {"config_id": "cfg_b", "config": {"kf_account": []}}
        adapter = self._mock_adapter()
        patches, mock_create, mock_update = self._patches([cfg_a, cfg_b], adapter, "cfg_b")
        body = KfAccountCreate(
            config_id="cfg_b", name="高老师", tenant_user_id="u1",
            subagent_type="pre-sales", allow_agent_transfer=True,
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            result = await create_kf_account(MagicMock(), body)

        # adapter 用指定 config_id 构建（企微调用走该渠道凭证）
        assert mock_create.call_args.kwargs["config_id"] == "cfg_b"
        # 写入 cfg_b 配置
        update_args = mock_update.call_args.args
        assert update_args[0] == "cfg_b"
        new_kf = update_args[1]["kf_account"][-1]
        assert new_kf["name"] == "高老师"
        assert new_kf["open_kfid"] == "wk_new_1"
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_create_specified_config_not_found_404(self):
        """指定的 config_id 不存在 → 404，不调用企微"""
        from fastapi import HTTPException
        from src.saas.api.wecom_kf_account import KfAccountCreate, create_kf_account

        cfg_a = {"config_id": "cfg_a", "config": {"kf_account": []}}
        adapter = self._mock_adapter()
        patches, mock_create, mock_update = self._patches([cfg_a], adapter, "cfg_b")
        body = KfAccountCreate(
            config_id="cfg_b", name="高老师", tenant_user_id="u1",
            allow_agent_transfer=True,
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            with pytest.raises(HTTPException) as exc:
                await create_kf_account(MagicMock(), body)
        assert exc.value.status_code == 404
        # 企微创建未被调用
        assert not adapter.api_client.account_add.await_args_list

    @pytest.mark.asyncio
    async def test_create_falls_back_to_verified_config(self):
        """不传 config_id 时回退到租户第一个已验证的 wecom_kf 配置（兼容历史单渠道场景）"""
        from src.saas.api.wecom_kf_account import KfAccountCreate, create_kf_account

        cfg_a = {"config_id": "cfg_a", "verified": 0, "config": {"kf_account": []}}
        cfg_b = {"config_id": "cfg_b", "verified": 1, "config": {"kf_account": []}}
        adapter = self._mock_adapter()
        patches, mock_create, mock_update = self._patches([cfg_a, cfg_b], adapter, "cfg_b")
        body = KfAccountCreate(
            name="高老师", tenant_user_id="u1", allow_agent_transfer=True,
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            result = await create_kf_account(MagicMock(), body)

        update_args = mock_update.call_args.args
        assert update_args[0] == "cfg_b"
        assert result["success"] is True
