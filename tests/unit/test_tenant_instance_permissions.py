"""租户实例权限展开单测（全 mock DB）"""

from unittest.mock import patch

from src.saas.permissions.checker import (
    expand_with_tenant_instances,
    get_tenant_instance_base,
)


def _row(agent_id, base_agent_id, status="active"):
    return {"agent_id": agent_id, "base_agent_id": base_agent_id, "status": status}


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
        rows = [_row("sales", "sales")]  # 直定制行不产生新 ID
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
