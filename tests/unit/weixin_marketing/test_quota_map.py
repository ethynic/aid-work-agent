"""微信配额 → quota scopes 映射测试（R41：tenant/task/target/account 层级）"""

from datetime import datetime, timezone

import pytest

from src.desktop_automation.constants import QUOTA_SCOPE_RANK
from src.weixin_marketing import quota_map

pytestmark = pytest.mark.unit


class TestQuotaMap:
    def test_full_scope_stack_order(self, wx_config):
        scopes = quota_map.build_quota_scopes(
            tenant_id="t-1", scenario_key="weixin.fixed_content.v1",
            task_ref="task-9", group_binding_id="gb-1", account_binding_id="ab-1",
            config=wx_config,
        )
        assert [s.scope_type for s in scopes] == ["tenant", "task", "target", "account"]
        # scope_id 场景前缀隔离（避免与其他场景同 scope_type 撞桶）
        assert scopes[0].scope_id == "wxm:t-1"
        assert scopes[1].scope_id == "wxm:weixin.fixed_content.v1:task-9"
        assert scopes[2].scope_id == "wxm:gb:gb-1"
        assert scopes[3].scope_id == "wxm:ab:ab-1"
        # 限额与窗口来自配置
        assert scopes[0].limit_count == wx_config.quotas.tenant_limit
        assert scopes[3].limit_count == wx_config.quotas.account_limit
        assert all(s.window_seconds == wx_config.quotas.window_seconds for s in scopes)
        # 排序后符合 R9 固定顺序（tenant < task < target < account）
        ranks = [QUOTA_SCOPE_RANK[s.scope_type] for s in scopes]
        assert ranks == sorted(ranks)

    def test_without_account_binding_degrades(self, wx_config):
        scopes = quota_map.build_quota_scopes(
            tenant_id="t-1", scenario_key="weixin.fixed_content.v1",
            task_ref="task-9", group_binding_id="gb-1", account_binding_id=None,
            config=wx_config,
        )
        assert [s.scope_type for s in scopes] == ["tenant", "task", "target"]

    def test_reserve_through_base_quota(self, wx_config, tenant_id):
        """映射结果可直接进入底座 quota 预留/结算（原子性由底座保证，此处验证接线）"""
        from src.db.database import get_db_connection
        from src.desktop_automation import quota as da_quota

        scopes = quota_map.build_quota_scopes(
            tenant_id=tenant_id, scenario_key="weixin.fixed_content.v1",
            task_ref="task-1", group_binding_id="gb-1", account_binding_id="ab-1",
            config=wx_config,
        )
        converted = [
            da_quota.QuotaScope(
                scope_type=s.scope_type, scope_id=s.scope_id,
                limit_count=s.limit_count, window_seconds=s.window_seconds,
            )
            for s in scopes
        ]
        now = datetime.now(timezone.utc)
        with get_db_connection() as conn:
            cur = conn.cursor()
            reservations = da_quota.reserve_quota(cur, tenant_id, converted, now)
            da_quota.settle_quota(cur, tenant_id, [r.as_dict() for r in reservations])
            conn.commit()
        assert len(reservations) == 4
        # 桶已建且 used=1（wxm 前缀 scope_id 落账）
        bucket = da_quota.get_bucket(
            tenant_id, "task", "wxm:weixin.fixed_content.v1:task-1",
            da_quota.bucket_start_for(wx_config.quotas.window_seconds, now),
        )
        assert bucket is not None and bucket["used_count"] == 1
