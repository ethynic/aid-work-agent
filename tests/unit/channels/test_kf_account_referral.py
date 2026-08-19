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
