"""C 模式标准用量上报集成测试（POST /api/client/v1/usage/report，P4 客户端计费统一接入）

真实 PG（agent2），临时租户用后即清。覆盖：
- 单条上报（模型 before 校验器自动包装为 reports 数组）：落台账 + 扣余额 + detail 事实存档
- client_ref_id 幂等：重复上报返回首次结果，不重复扣费
- 价目匹配优先级：client_name:command > command > default_credit_price
- quantity 乘数 + ceil 到分
- 总开关关闭：404 USAGE_REPORT_DISABLED
- 批量部分失败隔离：单条落账异常不影响其余条目
"""

import json
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def report_tenant():
    """临时租户（余额 100）+ 假 binding，测试后清理台账与租户"""
    from src.saas.db.tenant_db import TenantDB
    from src.db.database import get_db_connection
    from src.core.cache_utils import invalidate_tenant_cache

    tenant_code = f"T{uuid.uuid4().hex[:6].upper()}"
    tenant = TenantDB.create(
        company_name=f"上报测试租户-{tenant_code}",
        tenant_code=tenant_code,
        contact_name="测试联系人",
        contact_phone="13800000000",
    )
    if not tenant:
        pytest.skip("无法创建测试租户（DB 不可用）")
    tenant_id = tenant["tenant_id"]

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE tenants SET credit_balance = 100 WHERE tenant_id = %s", (tenant_id,)
        )
        conn.commit()
    invalidate_tenant_cache(tenant_id)

    binding = SimpleNamespace(
        tenant_id=tenant_id,
        binding_id="cb_reporttest",
        client_name="future-client",
        tenant={"credit_balance": 100.0},
    )
    yield tenant_id, binding

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM client_usage_logs WHERE tenant_id = %s", (tenant_id,))
            conn.commit()
    except Exception:
        pass
    TenantDB.delete(tenant_id)
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM tenants WHERE tenant_id = %s", (tenant_id,))
            conn.commit()
    except Exception:
        pass


def _patch_settings(prices: dict, default: float = 0.5, enabled: bool = True):
    """统一 mock settings.client_usage_report（enabled/价目表/default）"""
    cfg = SimpleNamespace(enabled=enabled, default_credit_price=default,
                          command_credit_prices=prices)
    container = SimpleNamespace(client_usage_report=cfg)
    return patch("src.api.client_routes.settings", container)


def _call(payload: dict, binding):
    """构造请求模型后直调路由函数（FastAPI 之外的等价校验路径）"""
    import asyncio
    from src.api import client_routes

    req = client_routes.UsageReportRequest(**payload)
    return asyncio.get_event_loop().run_until_complete(
        client_routes.report_usage(req, binding=binding)
    )


def _ledger_rows(tenant_id: str):
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """SELECT binding_id, client_name, session_id, client_ref_id, stage, status,
                      model, provider, credit_cost, detail
               FROM client_usage_logs WHERE tenant_id = %s ORDER BY id""",
            (tenant_id,),
        )
        return cursor.fetchall()


def _balance(tenant_id: str) -> float:
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT credit_balance FROM tenants WHERE tenant_id = %s", (tenant_id,))
        return float(cursor.fetchone()["credit_balance"])


class TestUsageReport:
    def test_single_report_bills_and_records(self, report_tenant):
        """单条对象自动包装：落台账（stage/model/detail/session）+ 扣余额"""
        tenant_id, binding = report_tenant
        with _patch_settings({"weixin_add_friend": 2.0}):
            resp = _call(
                {"client_ref_id": f"ref-{uuid.uuid4().hex[:12]}", "command": "weixin_add_friend",
                 "arguments_summary": {"target": "张三"}, "session_id": "local-run-1",
                 "occurred_at": "2026-09-01T12:00:00+08:00"},
                binding,
            )

        assert resp["success"] is True
        assert resp["accepted"] == 1
        r = resp["results"][0]
        assert r["success"] is True and r["duplicate"] is False
        assert r["credit_cost"] == 2.0
        assert r["balance_after"] == 98.0
        assert _balance(tenant_id) == 98.0

        rows = _ledger_rows(tenant_id)
        assert len(rows) == 1
        row = rows[0]
        assert row["stage"] == "future-client_action"
        assert row["model"] == "weixin_add_friend"
        assert row["provider"] == "client-report"
        assert row["binding_id"] == "cb_reporttest"
        assert row["session_id"] == "local-run-1"
        detail = json.loads(row["detail"])
        assert detail["command"] == "weixin_add_friend"
        assert detail["arguments"] == {"target": "张三"}
        assert detail["quantity"] == 1
        assert detail["occurred_at"] == "2026-09-01T12:00:00+08:00"
        assert detail["client_ref_id"] == r["client_ref_id"]

    def test_duplicate_ref_is_idempotent(self, report_tenant):
        """同 client_ref_id 重复上报：返回首次结果、不重复落账扣费"""
        tenant_id, binding = report_tenant
        ref = f"ref-{uuid.uuid4().hex[:12]}"
        with _patch_settings({"weixin_add_friend": 2.0}):
            first = _call({"client_ref_id": ref, "command": "weixin_add_friend"}, binding)
            second = _call({"client_ref_id": ref, "command": "weixin_add_friend"}, binding)

        assert first["results"][0]["duplicate"] is False
        assert second["results"][0]["duplicate"] is True
        assert second["results"][0]["credit_cost"] == 2.0  # 返回首次金额
        assert len(_ledger_rows(tenant_id)) == 1
        assert _balance(tenant_id) == 98.0  # 只扣一次

    def test_price_priority_client_prefix_over_command_over_default(self, report_tenant):
        """价目优先级：client_name:command > command > default_credit_price"""
        tenant_id, binding = report_tenant

        def _ref():
            return f"ref-{uuid.uuid4().hex[:12]}"

        with _patch_settings(
            {"future-client:cmd_a": 2.0, "cmd_a": 9.9, "cmd_b": 1.0}, default=0.5
        ):
            resp = _call(
                {"reports": [
                    {"client_ref_id": _ref(), "command": "cmd_a"},   # 命中 client 前缀 2.0
                    {"client_ref_id": _ref(), "command": "cmd_b"},   # 命中通用 1.0
                    {"client_ref_id": _ref(), "command": "cmd_c"},   # 兜底 default 0.5
                ]},
                binding,
            )
        vals = sorted(r["credit_cost"] for r in resp["results"])
        assert vals == [0.5, 1.0, 2.0]
        assert _balance(tenant_id) == 100.0 - sum(vals)

    def test_quantity_multiplier_ceil_to_cents(self, report_tenant):
        """quantity 乘数 + ceil 到分：0.33 × 2 = 0.66"""
        tenant_id, binding = report_tenant
        with _patch_settings({"cmd_q": 0.33}):
            resp = _call(
                {"client_ref_id": f"ref-{uuid.uuid4().hex[:12]}",
                 "command": "cmd_q", "quantity": 2},
                binding,
            )
        assert resp["results"][0]["credit_cost"] == 0.66
        assert _balance(tenant_id) == 100.0 - 0.66

    def test_disabled_returns_404(self, report_tenant):
        """总开关关闭：404 USAGE_REPORT_DISABLED（无实例时端点不可探）"""
        from fastapi import HTTPException
        from src.api import client_routes

        _tenant_id, binding = report_tenant
        with _patch_settings({}, enabled=False), pytest.raises(HTTPException) as ei:
            _call({"client_ref_id": f"ref-{uuid.uuid4().hex[:12]}", "command": "cmd_x"}, binding)
        assert ei.value.status_code == 404

    def test_batch_partial_failure_isolated(self, report_tenant):
        """批量部分失败：单条落账异常不影响其余条目，accepted 只计成功数"""
        tenant_id, binding = report_tenant

        def _flaky(**k):
            if k["command"] == "cmd_bad":
                raise RuntimeError("db down")
            return {"credit_cost": 0.0, "balance_after": 100.0, "duplicate": False}

        with _patch_settings({}), \
             patch("src.api.client_routes.ClientUsageLogDB") as usage_db:
            usage_db.record_client_report = _flaky
            resp = _call(
                {"reports": [
                    {"client_ref_id": f"ref-{uuid.uuid4().hex[:12]}", "command": "cmd_ok"},
                    {"client_ref_id": f"ref-{uuid.uuid4().hex[:12]}", "command": "cmd_bad"},
                ]},
                binding,
            )
        assert resp["accepted"] == 1
        assert resp["results"][0]["success"] is True
        assert resp["results"][1]["success"] is False
        assert resp["results"][1]["error"] == "RECORD_FAILED"


class TestUsageReportHardening:
    """CR 加固项防回归：Decimal 计价、user_id 防伪造、detail 上限、client_name 归一、envelope"""

    def test_decimal_pricing_no_float_overcharge(self, report_tenant):
        """0.1 × 3 必须是 0.30：math.ceil 浮点路径会错收 0.31（P2-1 防回归）"""
        tenant_id, binding = report_tenant
        with _patch_settings({"cmd_f": 0.1}):
            resp = _call(
                {"client_ref_id": f"ref-{uuid.uuid4().hex[:12]}",
                 "command": "cmd_f", "quantity": 3},
                binding,
            )
        assert resp["results"][0]["credit_cost"] == 0.30
        assert _balance(tenant_id) == 100.0 - 0.30

    def test_client_cannot_forge_user_id_in_detail(self, report_tenant):
        """客户端 detail 传 user_id 无效：保留键以服务端为准（C 模式恒 None，防伪造归属）"""
        tenant_id, binding = report_tenant
        with _patch_settings({"cmd_u": 1.0}):
            _call(
                {"client_ref_id": f"ref-{uuid.uuid4().hex[:12]}", "command": "cmd_u",
                 "detail": {"user_id": "forged_user", "memo": "正常补充字段"}},
                binding,
            )
        detail = json.loads(_ledger_rows(tenant_id)[0]["detail"])
        assert detail["user_id"] is None
        assert detail["memo"] == "正常补充字段"  # 非保留键正常存档

    def test_oversized_detail_dropped(self, report_tenant):
        """detail 整体 >1500 字符：整块丢弃替换为 _truncated，不影响落账计费"""
        tenant_id, binding = report_tenant
        with _patch_settings({"cmd_big": 1.0}):
            _call(
                {"client_ref_id": f"ref-{uuid.uuid4().hex[:12]}", "command": "cmd_big",
                 "detail": {"pad": "x" * 2000}},
                binding,
            )
        detail = json.loads(_ledger_rows(tenant_id)[0]["detail"])
        assert "_truncated" in detail
        assert detail["command"] == "cmd_big"

    def test_client_name_none_normalizes_to_unknown(self, report_tenant):
        """binding 无 client_name：价目匹配与台账 stage 归一到 unknown-client 同源"""
        tenant_id, binding = report_tenant
        binding.client_name = None
        with _patch_settings({"unknown-client:cmd_n": 3.0, "cmd_n": 0.1}):
            resp = _call(
                {"client_ref_id": f"ref-{uuid.uuid4().hex[:12]}", "command": "cmd_n"},
                binding,
            )
        # 归一后命中 unknown-client:cmd_n 差异价 3.0（而非通用价 0.1）
        assert resp["results"][0]["credit_cost"] == 3.0
        assert _ledger_rows(tenant_id)[0]["stage"] == "unknown-client_action"

    def test_envelope_reflects_partial_failure(self, report_tenant):
        """envelope：部分失败时 success=False 且 failed 计数正确"""
        tenant_id, binding = report_tenant

        def _flaky(**k):
            if k["command"] == "cmd_bad":
                raise RuntimeError("db down")
            return {"credit_cost": 0.0, "balance_after": 100.0, "duplicate": False}

        with _patch_settings({}), \
             patch("src.api.client_routes.ClientUsageLogDB") as usage_db:
            usage_db.record_client_report = _flaky
            resp = _call(
                {"reports": [
                    {"client_ref_id": f"ref-{uuid.uuid4().hex[:12]}", "command": "cmd_ok"},
                    {"client_ref_id": f"ref-{uuid.uuid4().hex[:12]}", "command": "cmd_bad"},
                ]},
                binding,
            )
        assert resp["success"] is False
        assert resp["accepted"] == 1
        assert resp["failed"] == 1
