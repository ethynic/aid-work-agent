"""租户数字员工实例服务层单测（全 mock DB，不依赖外部服务）"""

import pytest
from unittest.mock import patch

from src.saas.services import tenant_agent_instance_service as svc
from src.saas.services.tenant_agent_instance_service import (
    TenantAgentInstanceError,
    create_copy,
    update_overrides,
    delete_copy,
    validate_instance_id,
    _tenant_slug,
)
from src.db.tenant_agent_instance_db import OVERRIDABLE_FIELDS


def _row(agent_id, base_agent_id, **kw):
    row = {
        "id": 1, "tenant_id": "t1", "agent_id": agent_id,
        "base_agent_id": base_agent_id, "base_source": "builtin",
        "display_name": None, "description": None, "extra_prompt": None,
        "greeting_summary": None, "greeting_prompts": None,
        "status": "active", "created_by": None,
        "created_at": None, "updated_at": None,
    }
    row.update(kw)
    return row


# ============== id 生成与校验 ==============

class TestInstanceId:
    def test_slug_replaces_illegal_chars(self):
        assert _tenant_slug("t_1_华东") == "t-1"
        assert _tenant_slug("A__b") == "a-b"

    def test_slug_empty_fallback(self):
        assert _tenant_slug("___") == "tenant"

    def test_validate_rejects_underscore(self):
        assert validate_instance_id("sales_1") is not None

    def test_validate_rejects_uppercase_and_symbols(self):
        assert validate_instance_id("Sales-1") is not None
        assert validate_instance_id("sales.1") is not None

    def test_validate_accepts_lowercase_hyphen(self):
        assert validate_instance_id("sales-t1-ab12") is None


class TestGenerateInstanceId:
    def test_format_and_uniqueness(self):
        with patch.object(svc, "_is_id_taken", return_value=False):
            ids = {svc.generate_instance_id("sales", "t1") for _ in range(10)}
        assert all(i.startswith("sales-t1-") and len(i) == len("sales-t1-") + 4 for i in ids)
        assert len(ids) == 10

    def test_raises_after_retries(self):
        with patch.object(svc, "_is_id_taken", return_value=True):
            with pytest.raises(TenantAgentInstanceError):
                svc.generate_instance_id("sales", "t1")


# ============== create_copy ==============

class TestCreateCopy:
    def test_from_base_requires_subscription(self):
        with patch.object(svc, "_resolve_base_config", return_value={"base_source": "builtin"}), \
             patch.object(svc, "get_tenant_subscribed_agent_ids", return_value=["other"]):
            with pytest.raises(TenantAgentInstanceError, match="未订阅"):
                create_copy("t1", "sales", "南京销售助手")

    def test_from_base_unknown_agent(self):
        with patch.object(svc, "_resolve_base_config", return_value=None):
            with pytest.raises(TenantAgentInstanceError, match="不存在"):
                create_copy("t1", "nope", "x")

    def test_from_base_success(self):
        created = {}
        def fake_create(**kw):
            created.update(kw)
            return _row(kw["agent_id"], kw["base_agent_id"], display_name=kw["display_name"])
        with patch.object(svc, "_resolve_base_config", return_value={"base_source": "builtin"}), \
             patch.object(svc, "get_tenant_subscribed_agent_ids", return_value=["sales"]), \
             patch.object(svc, "TenantAgentInstanceDB") as db, \
             patch.object(svc, "generate_instance_id", return_value="sales-t1-ab12"), \
             patch.object(svc, "_get_max_instances", return_value=20):
            db.count_active.return_value = 0
            db.create.side_effect = fake_create
            row = create_copy("t1", "sales", "南京销售助手", created_by="u1")
        assert row["agent_id"] == "sales-t1-ab12"
        assert created["base_agent_id"] == "sales"
        assert created["display_name"] == "南京销售助手"

    def test_from_instance_clones_overrides_and_root_base(self):
        """实例来源：根父定义继承 + 覆盖项克隆（方案 A）"""
        source = _row("sales-t1-ab12", "sales",
                      description="源描述", extra_prompt="源提示词",
                      greeting_summary="源简介",
                      greeting_prompts=[{"label": "a", "message": "b"}])
        created = {}
        def fake_create(**kw):
            created.update(kw)
            return _row(kw["agent_id"], kw["base_agent_id"])
        with patch.object(svc, "_resolve_base_config", return_value={"base_source": "builtin"}), \
             patch.object(svc, "get_tenant_subscribed_agent_ids", return_value=["sales"]), \
             patch.object(svc, "TenantAgentInstanceDB") as db, \
             patch.object(svc, "generate_instance_id", return_value="sales-t1-cd34"), \
             patch.object(svc, "_get_max_instances", return_value=20):
            db.get.return_value = source
            db.count_active.return_value = 0
            db.create.side_effect = fake_create
            create_copy("t1", "sales-t1-ab12", "北京销售助手")
        # 根父定义保持一层继承；覆盖项克隆
        assert created["base_agent_id"] == "sales"
        assert created["description"] == "源描述"
        assert created["extra_prompt"] == "源提示词"
        assert created["greeting_summary"] == "源简介"
        assert created["greeting_prompts"] == [{"label": "a", "message": "b"}]

    def test_instance_source_still_checks_root_subscription(self):
        source = _row("sales-t1-ab12", "sales")
        with patch.object(svc, "_resolve_base_config", return_value={"base_source": "builtin"}), \
             patch.object(svc, "get_tenant_subscribed_agent_ids", return_value=["other"]), \
             patch.object(svc, "TenantAgentInstanceDB") as db:
            db.get.return_value = source
            with pytest.raises(TenantAgentInstanceError, match="未订阅"):
                create_copy("t1", "sales-t1-ab12", "x")

    def test_copy_limit(self):
        with patch.object(svc, "_resolve_base_config", return_value={"base_source": "builtin"}), \
             patch.object(svc, "get_tenant_subscribed_agent_ids", return_value=["sales"]), \
             patch.object(svc, "TenantAgentInstanceDB") as db, \
             patch.object(svc, "_get_max_instances", return_value=2):
            db.count_active.return_value = 2
            with pytest.raises(TenantAgentInstanceError, match="上限"):
                create_copy("t1", "sales", "x")


# ============== update_overrides ==============

class TestUpdateOverrides:
    def test_whitelist_filters_unknown_fields(self):
        with patch.object(svc, "TenantAgentInstanceDB") as db:
            db.get.return_value = _row("sales", "sales")
            db.update_overrides.return_value = True
            update_overrides("t1", "sales", {
                "display_name": "新名", "tools": {"allowed": ["x"]}, "llm_provider": "hack",
            })
            called = db.update_overrides.call_args[0][2]
        assert set(called.keys()) <= set(OVERRIDABLE_FIELDS)
        assert called == {"display_name": "新名"}

    def test_auto_creates_direct_customization_row(self):
        """基础员工首次定制：自动补建直定制行"""
        with patch.object(svc, "TenantAgentInstanceDB") as db, \
             patch.object(svc, "_resolve_base_config", return_value={"base_source": "builtin"}):
            db.get.side_effect = [None, _row("sales", "sales", display_name="新名"), _row("sales", "sales", display_name="新名")]
            db.update_overrides.return_value = True
            with patch.object(svc, "create_direct_customization") as mk:
                update_overrides("t1", "sales", {"display_name": "新名"})
            mk.assert_called_once()

    def test_unknown_agent_rejected(self):
        with patch.object(svc, "TenantAgentInstanceDB") as db, \
             patch.object(svc, "_resolve_base_config", return_value=None):
            db.get.return_value = None
            with pytest.raises(TenantAgentInstanceError, match="不存在"):
                update_overrides("t1", "ghost", {"display_name": "x"})

    def test_archived_rejected(self):
        with patch.object(svc, "TenantAgentInstanceDB") as db:
            db.get.return_value = _row("sales-t1-ab12", "sales", status="archived")
            with pytest.raises(TenantAgentInstanceError, match="归档"):
                update_overrides("t1", "sales-t1-ab12", {"display_name": "x"})

    def test_empty_overrides_rejected(self):
        with patch.object(svc, "TenantAgentInstanceDB") as db:
            db.get.return_value = _row("sales", "sales")
            with pytest.raises(TenantAgentInstanceError, match="无可更新"):
                update_overrides("t1", "sales", {"hacker_field": "x"})


# ============== delete_copy ==============

class TestDeleteCopy:
    def test_direct_customization_row_rejected(self):
        with patch.object(svc, "TenantAgentInstanceDB") as db:
            db.get.return_value = _row("sales", "sales")
            with pytest.raises(TenantAgentInstanceError, match="不可删除"):
                delete_copy("t1", "sales")

    def test_copy_soft_deleted(self):
        with patch.object(svc, "TenantAgentInstanceDB") as db:
            db.get.return_value = _row("sales-t1-ab12", "sales")
            db.soft_delete.return_value = True
            assert delete_copy("t1", "sales-t1-ab12") is True

    def test_missing_row_rejected(self):
        with patch.object(svc, "TenantAgentInstanceDB") as db:
            db.get.return_value = None
            with pytest.raises(TenantAgentInstanceError, match="不存在"):
                delete_copy("t1", "ghost")
