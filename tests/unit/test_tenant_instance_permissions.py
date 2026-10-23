"""租户实例权限展开单测（全 mock DB）"""

from unittest.mock import MagicMock, patch

from src.saas.permissions import checker
from src.saas.permissions.checker import (
    _merge_directly_granted_instances,
    expand_with_tenant_instances,
    get_allowed_agent_ids_for_user,
    get_tenant_instance_base,
)


def _row(agent_id, base_agent_id, status="active"):
    return {"agent_id": agent_id, "base_agent_id": base_agent_id, "status": status}


def _quota_patch(has_access=True):
    return patch("src.saas.permissions.checker.get_agent_quota", return_value=(has_access, 0))


class TestExpandWithTenantInstances:
    def test_no_tenant_returns_original(self):
        assert expand_with_tenant_instances(None, ["sales"]) == ["sales"]
        assert expand_with_tenant_instances("t1", []) == []

    def test_appends_active_copies_with_subscribed_base(self):
        rows = [_row("sales-t1-ab12", "sales"), _row("hr-t1-cd34", "hr")]
        with patch("src.db.tenant_agent_instance_db.TenantAgentInstanceDB.list_by_tenant", return_value=rows):
            result = expand_with_tenant_instances("t1", ["sales"])
        # 仅 base 在允许集合内的副本附加；hr 未订阅不附加
        assert result == ["sales", "sales-t1-ab12"]

    def test_skips_unsubscribed_base_and_archived(self):
        rows = [
            _row("sales-t1-ab12", "sales"),
            _row("ghost-t1-ee55", "ghost"),          # base 不在允许集合
            _row("hr-t1-cd34", "hr", status="archived"),  # 已归档
        ]
        with patch("src.db.tenant_agent_instance_db.TenantAgentInstanceDB.list_by_tenant", return_value=rows):
            result = expand_with_tenant_instances("t1", ["sales", "hr"])
        assert result == ["sales", "hr", "sales-t1-ab12"]

    def test_no_duplicate_ids(self):
        rows = [_row("sales", "sales")]  # 直用实例行不产生新 ID
        with patch("src.db.tenant_agent_instance_db.TenantAgentInstanceDB.list_by_tenant", return_value=rows):
            assert expand_with_tenant_instances("t1", ["sales"]) == ["sales"]

    def test_db_error_returns_original(self):
        with patch("src.db.tenant_agent_instance_db.TenantAgentInstanceDB.list_by_tenant",
                   side_effect=RuntimeError("db down")):
            assert expand_with_tenant_instances("t1", ["sales"]) == ["sales"]


class TestGetTenantInstanceBase:
    def test_copy_maps_to_base(self):
        with patch("src.db.tenant_agent_instance_db.TenantAgentInstanceDB.get",
                   return_value=_row("sales-t1-ab12", "sales")):
            assert get_tenant_instance_base("t1", "sales-t1-ab12") == "sales"

    def test_direct_customization_row_returns_none(self):
        with patch("src.db.tenant_agent_instance_db.TenantAgentInstanceDB.get",
                   return_value=_row("sales", "sales")):
            assert get_tenant_instance_base("t1", "sales") is None

    def test_missing_row_returns_none(self):
        with patch("src.db.tenant_agent_instance_db.TenantAgentInstanceDB.get", return_value=None):
            assert get_tenant_instance_base("t1", "ghost") is None

    def test_empty_args_return_none(self):
        assert get_tenant_instance_base(None, "sales") is None
        assert get_tenant_instance_base("t1", "") is None


# ============== _merge_directly_granted_instances ==============

class TestMergeDirectlyGrantedInstances:
    def test_directly_granted_copy_appended(self):
        rows = [_row("sales-t1-ab12", "sales")]
        with patch("src.db.tenant_agent_instance_db.TenantAgentInstanceDB.list_by_tenant", return_value=rows), \
             _quota_patch(True):
            result = _merge_directly_granted_instances("t1", [], ["sales-t1-ab12"])
        # 仅勾副本（未勾基础员工）→ 副本补回，基础员工不连带
        assert result == ["sales-t1-ab12"]

    def test_copy_with_unsubscribed_base_not_appended(self):
        rows = [_row("hr-t1-cd34", "hr")]
        with patch("src.db.tenant_agent_instance_db.TenantAgentInstanceDB.list_by_tenant", return_value=rows), \
             _quota_patch(False):
            result = _merge_directly_granted_instances("t1", [], ["hr-t1-cd34"])
        # base 无有效订阅 → 副本不补回（租户级闸门仍生效）
        assert result == []

    def test_archived_copy_not_appended(self):
        rows = [_row("sales-t1-ab12", "sales", status="archived")]
        with patch("src.db.tenant_agent_instance_db.TenantAgentInstanceDB.list_by_tenant", return_value=rows), \
             _quota_patch(True):
            result = _merge_directly_granted_instances("t1", [], ["sales-t1-ab12"])
        assert result == []

    def test_base_grant_copies_not_duplicated(self):
        rows = [_row("sales-t1-ab12", "sales")]
        with patch("src.db.tenant_agent_instance_db.TenantAgentInstanceDB.list_by_tenant", return_value=rows), \
             _quota_patch(True):
            result = _merge_directly_granted_instances(
                "t1", ["sales"], ["sales", "sales-t1-ab12"])
        # 授权基础员工 → expand 已连带全部副本，直接授权路径不重复追加
        assert result == ["sales", "sales-t1-ab12"]

    def test_direct_row_and_plain_id_ignored(self):
        rows = [_row("sales", "sales")]
        with patch("src.db.tenant_agent_instance_db.TenantAgentInstanceDB.list_by_tenant", return_value=rows), \
             _quota_patch(True):
            result = _merge_directly_granted_instances("t1", [], ["sales", "ghost"])
        # 直用实例行与普通 ID 不产生附加项
        assert result == []

    def test_db_error_returns_expanded(self):
        with patch("src.db.tenant_agent_instance_db.TenantAgentInstanceDB.list_by_tenant",
                   side_effect=RuntimeError("db down")):
            result = _merge_directly_granted_instances("t1", ["sales"], ["sales-t1-ab12"])
        assert result == ["sales"]

    def test_no_tenant_or_granted_returns_expanded(self):
        with patch("src.db.tenant_agent_instance_db.TenantAgentInstanceDB.list_by_tenant",
                   return_value=[]) as mock_list, \
             _quota_patch(True):
            assert _merge_directly_granted_instances(None, ["sales"], ["sales-t1-ab12"]) == ["sales"]
            assert _merge_directly_granted_instances("t1", ["sales"], []) == ["sales"]
        mock_list.assert_called_once()


# ============== get_allowed_agent_ids_for_user 普通用户分支 ==============

class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, *args, **kwargs):
        pass

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def cursor(self):
        return _FakeCursor(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _patch_normal_user_env(tenant_types, granted_ids, instance_rows, quota=True):
    """统一 mock：订阅查询 / 用户授权 / 实例表 / 订阅闸门 / 缓存"""
    conns = [_FakeConn([{"subagent_type": t} for t in tenant_types]),
             _FakeConn([{"subagent_type": t} for t in tenant_types])]
    return [
        patch("src.saas.permissions.checker.get_db_connection", side_effect=lambda: conns.pop(0) if conns else _FakeConn([])),
        patch("src.saas.permissions.checker.get_cached", return_value=None),
        patch("src.saas.permissions.checker.set_cached"),
        patch.object(checker.UserAgentPermissionDB, "get_allowed_agents", return_value=list(granted_ids)),
        patch("src.db.tenant_agent_instance_db.TenantAgentInstanceDB.list_by_tenant", return_value=instance_rows),
        _quota_patch(quota),
    ]


class TestGetAllowedAgentIdsForUserNormalUser:
    def _run(self, tenant_types, granted_ids, instance_rows, quota=True):
        user = {"user_id": "u1", "role": "user", "tenant_id": "t1"}
        patches = _patch_normal_user_env(tenant_types, granted_ids, instance_rows, quota)
        for p in patches:
            p.start()
        try:
            return get_allowed_agent_ids_for_user(user)
        finally:
            for p in patches:
                p.stop()

    def test_copy_only_grant_visible(self):
        result = self._run(
            tenant_types=["data-analysis", "sales-assistant"],
            granted_ids=["data-analysis-t1-yreb", "data-analysis-t1-8tpg"],
            instance_rows=[
                _row("data-analysis-t1-yreb", "data-analysis"),
                _row("data-analysis-t1-8tpg", "data-analysis"),
                _row("data-analysis-t1-54fe", "data-analysis"),  # 未授权副本
            ],
        )
        # 仅授权 2 个副本、未勾基础员工 → 恰好看到这 2 个（sorted 比较不依赖授权行序）
        assert sorted(result) == ["data-analysis-t1-8tpg", "data-analysis-t1-yreb"]

    def test_base_grant_expands_all_copies(self):
        result = self._run(
            tenant_types=["data-analysis"],
            granted_ids=["data-analysis"],
            instance_rows=[
                _row("data-analysis-t1-yreb", "data-analysis"),
                _row("data-analysis-t1-54fe", "data-analysis"),
            ],
        )
        # 授权基础员工 → 连带全部副本（既有语义不变）
        assert result == ["data-analysis", "data-analysis-t1-yreb", "data-analysis-t1-54fe"]

    def test_unsubscribed_tenant_returns_empty(self):
        result = self._run(
            tenant_types=[],
            granted_ids=["data-analysis-t1-yreb"],
            instance_rows=[_row("data-analysis-t1-yreb", "data-analysis")],
        )
        assert result == []

    def test_cache_hit_merges_directly_granted(self):
        user = {"user_id": "u1", "role": "user", "tenant_id": "t1"}
        with patch("src.saas.permissions.checker.get_cached", return_value=[]), \
             patch("src.saas.permissions.checker.get_db_connection", return_value=_FakeConn([])), \
             patch.object(checker.UserAgentPermissionDB, "get_allowed_agents",
                          return_value=["data-analysis-t1-yreb"]), \
             patch("src.db.tenant_agent_instance_db.TenantAgentInstanceDB.list_by_tenant",
                   return_value=[_row("data-analysis-t1-yreb", "data-analysis")]), \
             _quota_patch(True):
            result = get_allowed_agent_ids_for_user(user)
        # 缓存命中路径同样补回直接授权副本
        assert result == ["data-analysis-t1-yreb"]
