"""weixin_marketing 工作台单元测试（P3-A1 纯函数：test 配额 scope / 能力视图 / 状态映射）"""

from src.weixin_marketing import quota_map
from src.weixin_marketing.config import WeixinMarketingConfig, WeixinQuotaConfig
from src.weixin_marketing.workbench import (
    SEARCH_RESULT_LIMIT,
    _candidates_incomplete_reason,
    _map_search_status,
    _normalize_title,
    _weixin_capability_view,
)


class TestBuildTestQuotaScopes:
    def test_scopes_are_test_prefixed_and_isolated(self):
        config = WeixinMarketingConfig(quotas=WeixinQuotaConfig(
            window_seconds=3600, tenant_limit=11, task_limit=5,
            target_limit=3, account_limit=7,
        ))
        scopes = quota_map.build_test_quota_scopes(
            tenant_id="t1", scenario_key="weixin.fixed_content.v1",
            task_ref="a1", group_binding_id="g1", account_binding_id="b1",
            config=config,
        )
        production = quota_map.build_quota_scopes(
            tenant_id="t1", scenario_key="weixin.fixed_content.v1",
            task_ref="a1", group_binding_id="g1", account_binding_id="b1",
            config=config,
        )
        assert [s.scope_id for s in scopes] == [
            f"wxm:test:{s.scope_id.removeprefix('wxm:')}" for s in production
        ]
        assert all(s.scope_id.startswith("wxm:test:") for s in scopes)
        # 层级与限额同构（tenant/task/target/account 四层）
        assert [s.scope_type for s in scopes] == ["tenant", "task", "target", "account"]
        assert [s.limit_count for s in scopes] == [11, 5, 3, 7]
        assert [s.window_seconds for s in scopes] == [3600] * 4
        # 与生产桶不相交
        assert not ({s.scope_id for s in scopes} & {s.scope_id for s in production})

    def test_no_account_binding_degrades_to_three_layers(self):
        scopes = quota_map.build_test_quota_scopes(
            tenant_id="t1", scenario_key="weixin.fixed_content.v1",
            task_ref="a1", group_binding_id="g1", account_binding_id=None,
        )
        assert [s.scope_type for s in scopes] == ["tenant", "task", "target"]


class TestWeixinCapabilityView:
    def test_providers_array(self):
        view = _weixin_capability_view({"providers": ["boss-recruiting", "weixin"]})
        assert view == {"available": True, "provider_key": "weixin", "protocol_version": None}

    def test_provider_manifests(self):
        view = _weixin_capability_view(
            {"provider_manifests": {"weixin": {
                "provider_id": "ai.aidwork.weixin", "protocol_version": 1,
            }}}
        )
        assert view["available"] is True
        assert view["protocol_version"] == 1

    def test_unavailable_shapes(self):
        assert _weixin_capability_view(None)["available"] is False
        assert _weixin_capability_view({"providers": ["boss-recruiting"]})["available"] is False
        assert _weixin_capability_view({"providers": ["weixin"]})["available"] is True


class TestSearchStateAndTitle:
    def test_state_mapping(self):
        assert _map_search_status("queued") == "pending"
        assert _map_search_status("claimed") == "running"
        assert _map_search_status("running") == "running"
        assert _map_search_status("succeeded") == "succeeded"
        for state in ("failed", "cancelled", "unknown", "expired"):
            assert _map_search_status(state) == "failed"

    def test_title_normalization_is_exact_not_fuzzy(self):
        assert _normalize_title(" 群A ") == "群A"
        # P3 不做子串/后缀模糊：人数后缀、包含关系一律不命中
        assert _normalize_title("群A(32)") != _normalize_title("群A")
        assert _normalize_title("群A分部") != _normalize_title("群A")
        assert _normalize_title(None) == ""


class TestCandidatesCompletenessGate:
    """P3 复审 P1-2：唯一性判定前的候选集合完整性门槛（纯函数判据）"""

    def _items(self, n: int):
        return [{"title": f"群{i}", "target_ref": f"wxg:{i}"} for i in range(n)]

    def test_no_flag_below_limit_is_complete(self):
        assert _candidates_incomplete_reason({"items": []}, self._items(3)) is None

    def test_no_flag_at_limit_is_incomplete(self):
        """无标志时条数 ≥ 请求 limit 视为可能截断（消息明示上限 N）"""
        reason = _candidates_incomplete_reason({"items": []}, self._items(SEARCH_RESULT_LIMIT))
        assert reason is not None
        assert "候选集合可能不完整" in reason
        assert f"达到上限 {SEARCH_RESULT_LIMIT}" in reason
        assert "缩小群名搜索词" in reason

    def test_explicit_truncated_flag_blocks_even_with_few_items(self):
        reason = _candidates_incomplete_reason(
            {"items": [], "truncated": True}, self._items(1)
        )
        assert reason is not None and "截断" in reason

    def test_explicit_complete_flag_overrides_count_heuristic(self):
        """显式 truncated=False：即使条数 == limit 也按标志判完整"""
        assert _candidates_incomplete_reason(
            {"items": [], "truncated": False}, self._items(SEARCH_RESULT_LIMIT)
        ) is None

    def test_complete_flag_false_blocks(self):
        reason = _candidates_incomplete_reason(
            {"items": [], "complete": False}, self._items(2)
        )
        assert reason is not None

    def test_complete_flag_true_passes(self):
        assert _candidates_incomplete_reason(
            {"items": [], "complete": True}, self._items(SEARCH_RESULT_LIMIT)
        ) is None

    def test_conflicting_flags_take_conservative_side(self):
        reason = _candidates_incomplete_reason(
            {"items": [], "truncated": True, "complete": True}, self._items(1)
        )
        assert reason is not None
