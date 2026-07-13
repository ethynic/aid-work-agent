"""企业微信个人账号 RPA 路由器与会话授权单元测试

覆盖点（对齐 docs/system/wecom-personal-rpa-protocol.md §B.2 与任务契约）：
1. ``WeComPersonalRpaRouter.route`` 格式：
   - 基础格式 ``wecom_personal_rpa:{account_id}:{conversation_id}``
   - ``stable_id`` 优先于 ``conversation_id``（保证跨重启路由稳定）
2. ``check_conversation_authorization`` 授权语义：
   - ``status=active`` → 放行（needs_review=False, paused=False）
   - ``status=pending`` → needs_review=True
   - ``status=paused`` / ``status=invalid`` → paused=True
   - binding 写入失败（None） → needs_review=True
   - 同 search_key 多条（重名歧义） → needs_review=True 且 reason="ambiguous"

全部 mock ``src.channels.wecom_personal_rpa.db``，不触碰真实数据库。
"""

import pytest
from unittest.mock import patch

from src.channels.wecom_personal_rpa.router import (
    AuthorizationResult,
    WeComPersonalRpaRouter,
    check_conversation_authorization,
    is_allowed_by_monitor_whitelist,
    router,
)


# ============================================================
# route() 格式与 stable_id 优先级
# ============================================================


class TestRoute:
    def test_route_isolates_tenant_and_account(self):
        r = WeComPersonalRpaRouter()
        base = r.route("tenant_a", "account_a", "local", "stable")
        assert base != r.route("tenant_b", "account_a", "local", "stable")
        assert base != r.route("tenant_a", "account_b", "local", "stable")

    def test_route_basic_format(self):
        """基础格式：wecom_personal_rpa:{account_id}:{conversation_id}。"""
        r = WeComPersonalRpaRouter()
        sid = r.route("tenant_001", "wecom_account_001", "binding_abc")
        assert sid == "wecom_personal_rpa:tenant_001:wecom_account_001:binding_abc"

    def test_route_stable_id_preferred_over_conversation_id(self):
        """stable_id 非空时优先，保证同一对方跨重启路由稳定。"""
        r = WeComPersonalRpaRouter()
        sid = r.route(
            account_id="wecom_account_001",
            tenant_id="tenant_001",
            conversation_id="binding_abc",
            stable_id="external_userid_zhangsan",
        )
        assert sid == "wecom_personal_rpa:tenant_001:wecom_account_001:external_userid_zhangsan"

    def test_route_stable_id_empty_falls_back_to_conversation_id(self):
        """stable_id=None 时回退到 conversation_id。"""
        r = WeComPersonalRpaRouter()
        sid = r.route(
            account_id="acc1",
            tenant_id="t1",
            conversation_id="conv_local_1",
            stable_id=None,
        )
        assert sid == "wecom_personal_rpa:t1:acc1:conv_local_1"

    def test_route_stable_id_empty_string_falls_back(self):
        """stable_id 为空字符串时也回退（falsy 判定，对齐 stable_id or conversation_id）。"""
        r = WeComPersonalRpaRouter()
        sid = r.route(
            account_id="acc1",
            tenant_id="t1",
            conversation_id="conv_local_1",
            stable_id="",
        )
        assert sid == "wecom_personal_rpa:t1:acc1:conv_local_1"

    def test_route_module_singleton_matches_class(self):
        """模块级单例 router 行为与新建实例一致。"""
        sid = router.route("t1", "acc1", "conv1")
        assert sid == "wecom_personal_rpa:t1:acc1:conv1"


# ============================================================
# check_conversation_authorization —— 授权语义
# ============================================================


_TENANT = "tenant_001"
_ACCOUNT = "wecom_account_001"
_CONV_ID = "binding_abc"
_CONV_TYPE = "external_user"
_SEARCH_KEY = "external_userid_zhangsan"
_DISPLAY_NAME = "张三"
_STABLE_ID = "external_userid_zhangsan"


def _binding(status: str, search_key: str = _SEARCH_KEY):
    """构造一条 binding dict，模拟 db 返回行。"""
    return {
        "id": "rpa_bind_xxx",
        "tenant_id": _TENANT,
        "account_id": _ACCOUNT,
        "conversation_type": _CONV_TYPE,
        "display_name": _DISPLAY_NAME,
        "search_key": search_key,
        "stable_id": _STABLE_ID,
        "status": status,
    }


@patch("src.channels.wecom_personal_rpa.router.db")
@pytest.mark.asyncio
async def test_authorization_active_binding_passes(mock_db):
    """status=active → 放行：needs_review=False, paused=False, reason=None。"""
    mock_db.get_or_create_binding.return_value = _binding("active")
    mock_db.find_binding_by_search_key.return_value = _binding("active")
    mock_db.list_bindings.return_value = [_binding("active")]

    result = await check_conversation_authorization(
        tenant_id=_TENANT,
        account_id=_ACCOUNT,
        conversation_id=_CONV_ID,
        conversation_type=_CONV_TYPE,
        search_key=_SEARCH_KEY,
        display_name=_DISPLAY_NAME,
        stable_id=_STABLE_ID,
    )

    assert isinstance(result, AuthorizationResult)
    assert result.session_id == "wecom_personal_rpa:tenant_001:wecom_account_001:external_userid_zhangsan"
    assert result.needs_review is False
    assert result.paused is False
    assert result.reason is None


@patch("src.channels.wecom_personal_rpa.router.db")
@pytest.mark.asyncio
async def test_authorization_pending_triggers_needs_review(mock_db):
    """status=pending（首次见到未确认）→ needs_review=True。"""
    mock_db.get_or_create_binding.return_value = _binding("pending")
    mock_db.find_binding_by_search_key.return_value = _binding("pending")
    mock_db.list_bindings.return_value = [_binding("pending")]

    result = await check_conversation_authorization(
        tenant_id=_TENANT,
        account_id=_ACCOUNT,
        conversation_id=_CONV_ID,
        conversation_type=_CONV_TYPE,
        search_key=_SEARCH_KEY,
        display_name=_DISPLAY_NAME,
        stable_id=None,
    )

    assert result.needs_review is True
    assert result.paused is False
    assert result.reason == "pending"
    # stable_id=None 时回退到 conversation_id 作为 route_key
    assert result.session_id == "wecom_personal_rpa:tenant_001:wecom_account_001:external_userid_zhangsan"


@patch("src.channels.wecom_personal_rpa.router.db")
@pytest.mark.asyncio
async def test_authorization_paused_status_triggers_paused(mock_db):
    """status=paused → paused=True（账号/会话被暂停，等待人工恢复）。"""
    mock_db.get_or_create_binding.return_value = _binding("paused")
    mock_db.find_binding_by_search_key.return_value = _binding("paused")
    mock_db.list_bindings.return_value = [_binding("paused")]

    result = await check_conversation_authorization(
        tenant_id=_TENANT,
        account_id=_ACCOUNT,
        conversation_id=_CONV_ID,
        conversation_type=_CONV_TYPE,
        search_key=_SEARCH_KEY,
        display_name=_DISPLAY_NAME,
    )

    assert result.paused is True
    assert result.needs_review is False
    assert result.reason == "paused"


@patch("src.channels.wecom_personal_rpa.router.db")
@pytest.mark.asyncio
async def test_authorization_invalid_status_triggers_paused(mock_db):
    """status=invalid → paused=True（绑定失效，按暂停语义处理）。"""
    mock_db.get_or_create_binding.return_value = _binding("invalid")
    mock_db.find_binding_by_search_key.return_value = _binding("invalid")
    mock_db.list_bindings.return_value = [_binding("invalid")]

    result = await check_conversation_authorization(
        tenant_id=_TENANT,
        account_id=_ACCOUNT,
        conversation_id=_CONV_ID,
        conversation_type=_CONV_TYPE,
        search_key=_SEARCH_KEY,
        display_name=_DISPLAY_NAME,
    )

    assert result.paused is True
    assert result.needs_review is False
    assert result.reason == "invalid"


@patch("src.channels.wecom_personal_rpa.router.db")
@pytest.mark.asyncio
async def test_authorization_binding_unavailable_triggers_needs_review(mock_db):
    """get_or_create_binding 返回 None（写入失败）→ 保守触发 needs_review。"""
    mock_db.get_or_create_binding.return_value = None
    mock_db.find_binding_by_search_key.return_value = None
    mock_db.list_bindings.return_value = []

    result = await check_conversation_authorization(
        tenant_id=_TENANT,
        account_id=_ACCOUNT,
        conversation_id=_CONV_ID,
        conversation_type=_CONV_TYPE,
        search_key=_SEARCH_KEY,
        display_name=_DISPLAY_NAME,
    )

    assert result.needs_review is True
    assert result.paused is False
    assert result.reason == "binding_unavailable"


@patch("src.channels.wecom_personal_rpa.router.db")
@pytest.mark.asyncio
async def test_authorization_ambiguous_search_key_triggers_needs_review(mock_db):
    """同 search_key 在租户内多条（重名歧义）→ needs_review=True 且 reason="ambiguous"。

    场景：两个不同 account 下绑定了同一 external_userid（跨账号重名），或同一 search_key
    被人工录入多次。客户端暂停该会话自动发送，等待人工区分。
    """
    same_key_binding_a = _binding("active", search_key=_SEARCH_KEY)
    same_key_binding_a["account_id"] = "wecom_account_002"
    same_key_binding_b = _binding("active", search_key=_SEARCH_KEY)

    mock_db.get_or_create_binding.return_value = same_key_binding_b
    mock_db.find_binding_by_search_key.return_value = same_key_binding_b
    mock_db.list_bindings.return_value = [same_key_binding_a, same_key_binding_b]

    result = await check_conversation_authorization(
        tenant_id=_TENANT,
        account_id=_ACCOUNT,
        conversation_id=_CONV_ID,
        conversation_type=_CONV_TYPE,
        search_key=_SEARCH_KEY,
        display_name=_DISPLAY_NAME,
    )

    assert result.needs_review is True
    assert result.paused is False
    assert result.reason == "ambiguous"


@patch("src.channels.wecom_personal_rpa.router.db")
@pytest.mark.asyncio
async def test_authorization_falls_back_to_get_or_create_binding_when_find_misses(mock_db):
    """find_binding_by_search_key 返回 None（极端竞态：刚 upsert 即被删）时，
    回退到 get_or_create_binding 的返回值，避免误判。
    """
    pending = _binding("pending")
    mock_db.get_or_create_binding.return_value = pending
    mock_db.find_binding_by_search_key.return_value = None
    mock_db.list_bindings.return_value = [pending]

    result = await check_conversation_authorization(
        tenant_id=_TENANT,
        account_id=_ACCOUNT,
        conversation_id=_CONV_ID,
        conversation_type=_CONV_TYPE,
        search_key=_SEARCH_KEY,
        display_name=_DISPLAY_NAME,
    )

    assert result.needs_review is True
    assert result.reason == "pending"


@patch("src.channels.wecom_personal_rpa.router.db")
@pytest.mark.asyncio
async def test_authorization_reuses_historical_binding_stable_id(mock_db):
    """客户端本次缺 stable_id 时，历史 binding 仍保持同一 session。"""
    active = _binding("active")
    mock_db.get_or_create_binding.return_value = active
    mock_db.find_binding_by_search_key.return_value = active
    mock_db.list_bindings.return_value = [active]

    first = await check_conversation_authorization(
        tenant_id=_TENANT, account_id=_ACCOUNT, conversation_id="local_old",
        conversation_type=_CONV_TYPE, search_key=_SEARCH_KEY,
        display_name=_DISPLAY_NAME, stable_id=None,
    )
    second = await check_conversation_authorization(
        tenant_id=_TENANT, account_id=_ACCOUNT, conversation_id="local_new",
        conversation_type=_CONV_TYPE, search_key=_SEARCH_KEY,
        display_name=_DISPLAY_NAME, stable_id=None,
    )
    assert first.session_id == second.session_id
    assert first.session_id.endswith(":" + _STABLE_ID)


# ============================================================
# is_allowed_by_monitor_whitelist —— 绑定级监控白名单服务端二次校验
# ============================================================


class TestMonitorWhitelist:
    """监控白名单服务端二次校验语义。

    客户端缓存白名单只是优化（减少 callback），真正过滤由服务端做。
    """

    def test_whitelist_empty_allows_all(self):
        """两个字段都为空 → 允许所有（首版默认行为）。"""
        binding = {"monitor_user_names": [], "monitor_user_ids": []}
        assert is_allowed_by_monitor_whitelist(binding, "任何人", "any_id") is True

    def test_whitelist_fields_none_allows_all(self):
        """字段为 None（兼容旧 binding）→ 允许所有。"""
        binding = {"monitor_user_names": None, "monitor_user_ids": None}
        assert is_allowed_by_monitor_whitelist(binding, "任何人", "any_id") is True

    def test_whitelist_binding_none_allows_all(self):
        """binding 查不到 → 放行（授权层会按 needs_review 拦截）。"""
        assert is_allowed_by_monitor_whitelist(None, "任何人", "any_id") is True

    def test_whitelist_user_names_filters_non_matching(self):
        """配置 monitor_user_names=["陆伟"]，sender="孙晨" → 拒绝。"""
        binding = {"monitor_user_names": ["陆伟"], "monitor_user_ids": []}
        assert is_allowed_by_monitor_whitelist(binding, "孙晨", "wm_sun") is False

    def test_whitelist_user_names_matches(self):
        """配置 monitor_user_names=["陆伟"]，sender="陆伟" → 允许。"""
        binding = {"monitor_user_names": ["陆伟"], "monitor_user_ids": []}
        assert is_allowed_by_monitor_whitelist(binding, "陆伟", None) is True

    def test_whitelist_user_ids_filters_non_matching(self):
        """配置 monitor_user_ids=["wm_xxx"]，sender_id="wm_yyy" → 拒绝。"""
        binding = {"monitor_user_names": [], "monitor_user_ids": ["wm_xxx"]}
        assert is_allowed_by_monitor_whitelist(binding, "陆伟", "wm_yyy") is False

    def test_whitelist_user_ids_matches(self):
        """配置 monitor_user_ids=["wm_xxx"]，sender_id="wm_xxx" → 允许。"""
        binding = {"monitor_user_names": [], "monitor_user_ids": ["wm_xxx"]}
        assert is_allowed_by_monitor_whitelist(binding, None, "wm_xxx") is True

    def test_whitelist_both_fields_either_matches_name(self):
        """names=["A"], ids=["B"]；sender_name="A" 通过（name 命中）。"""
        binding = {"monitor_user_names": ["A"], "monitor_user_ids": ["B"]}
        assert is_allowed_by_monitor_whitelist(binding, "A", "xxx") is True

    def test_whitelist_both_fields_either_matches_id(self):
        """names=["A"], ids=["B"]；sender_id="B" 通过（id 命中）。"""
        binding = {"monitor_user_names": ["A"], "monitor_user_ids": ["B"]}
        assert is_allowed_by_monitor_whitelist(binding, "路人", "B") is True

    def test_whitelist_both_fields_neither_matches(self):
        """names=["A"], ids=["B"]；name 和 id 都不命中 → 拒绝。"""
        binding = {"monitor_user_names": ["A"], "monitor_user_ids": ["B"]}
        assert is_allowed_by_monitor_whitelist(binding, "路人", "xxx") is False

    def test_whitelist_sender_fields_none_with_strict_whitelist(self):
        """配置了白名单但 sender 信息缺失 → 拒绝（保守）。"""
        binding = {"monitor_user_names": ["A"], "monitor_user_ids": ["B"]}
        assert is_allowed_by_monitor_whitelist(binding, None, None) is False
