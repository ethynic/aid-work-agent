"""私有云部署模式（PRIVATE_CLOUD=true）计费 API 集成测试

覆盖（2026-10-22 私有云改动补测，公有云回归由既有测试锁定）：
- /usage 私有云模式：按日聚合额外返回 token_qty 7 分项 + summary.total_token_qty，
  验证 jsonb breakdown 优先、老数据裸列回填（prompt_tokens - cached_input_tokens）、
  畸形 breakdown（分项非对象 / 分项值为非数字字符串）正则守卫回落不炸查询
- /usage 公有云回归：不返回 token_qty / total_token_qty 字段
- /usage/daily-detail 私有云模式：租户管理员也返回 breakdown_items（reveal_tokens 扩展），
  但剥离单价/系数/积分（unit_price / usage_factor / credit 置 None、is_per_million 置 False）
- /usage/daily-detail 私有云模式：平台管理员保留单价/系数/积分（reveal_pricing 仅平台管理员）

jsonb 表达式为 PostgreSQL 专有语法，须连真实 PG（沿用本目录既有连库模式，
temp tenant + 测试后清理）。
"""

import json
import uuid
from datetime import datetime
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def temp_tenant_for_private_cloud():
    """创建临时租户用于私有云测试，测试后清理（模式同 test_billing_daily_detail_api）"""
    from src.saas.db.tenant_db import TenantDB
    from src.db.database import get_db_connection

    tenant_code = f"T{uuid.uuid4().hex[:6].upper()}"
    tenant = TenantDB.create(
        company_name=f"测试租户-{tenant_code}",
        tenant_code=tenant_code,
        contact_name="测试联系人",
        contact_phone="13800000000",
    )
    if not tenant:
        pytest.skip("无法创建测试租户（DB 不可用）")
    tenant_id = tenant["tenant_id"]

    yield tenant_id

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM chat_records WHERE tenant_id = %s AND user_id LIKE 'pc_test_%%'",
                (tenant_id,),
            )
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


def _insert_chat_record_raw(
    tenant_id: str,
    usage_breakdown: dict | None,
    prompt_tokens: int = 0,
    cached_input_tokens: int = 0,
    completion_tokens: int = 0,
    credit_cost: int = 1,
):
    """直接 SQL 写入 chat_records（绕过余额扣减），usage_breakdown 可传畸形结构"""
    from src.db.database import get_db_connection

    record_id = f"rec_{uuid.uuid4().hex[:12]}"
    breakdown_json = json.dumps(usage_breakdown) if usage_breakdown is not None else None
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO chat_records
            (record_id, session_id, tenant_id, user_id, user_message, assistant_message,
             prompt_tokens, completion_tokens, cached_input_tokens,
             model, status, source_type, credit_cost, usage_breakdown)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                record_id, f"sess_{uuid.uuid4().hex[:8]}", tenant_id,
                f"pc_test_{uuid.uuid4().hex[:6]}", "测试消息", "测试回复",
                prompt_tokens, completion_tokens, cached_input_tokens,
                "test-model", "completed", "chat", credit_cost, breakdown_json,
            ),
        )
        conn.commit()
    return record_id


def _run_coro(coro):
    """在独立事件循环中执行协程。

    不用 asyncio.run()：它会 set_event_loop(None) 清空当前循环，
    污染同会话内后续用 get_event_loop().run_until_complete() 的既有测试。
    """
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _call_get_usage(tenant_id: str, role: str = "tenant_admin"):
    """mock require_admin 后直接调用 /usage handler"""
    from src.saas.api import billing_balance

    def fake_require_admin(request):
        return {"user_id": "test_user", "role": role, "tenant_id": tenant_id}

    class FakeRequest:
        pass

    with patch("src.saas.api.billing_balance.require_admin", fake_require_admin):
        import asyncio
        return _run_coro(billing_balance.get_usage(
            FakeRequest(), date_from=None, date_to=None,
            session_id=None, model=None, page=1, page_size=20,
        ))


def _call_daily_detail(tenant_id: str, role: str, date: str):
    """mock require_admin 后直接调用 /usage/daily-detail handler"""
    from src.saas.api import billing_balance

    def fake_require_admin(request):
        return {"user_id": "test_user", "role": role, "tenant_id": tenant_id}

    class FakeRequest:
        pass

    with patch("src.saas.api.billing_balance.require_admin", fake_require_admin):
        import asyncio
        return _run_coro(billing_balance.get_daily_usage_detail(
            FakeRequest(), date=date, page=1, page_size=20,
        ))


TOKEN_QTY_KEYS = {
    "non_cached_input", "cached_input", "cache_creation_input",
    "output", "video", "asr", "embedding",
}


class TestUsageTokenQty:
    """/usage 私有云 7 分项 token 数量聚合"""

    def test_private_cloud_token_qty_aggregation(
        self, temp_tenant_for_private_cloud
    ):
        """私有云：token_qty 聚合覆盖 breakdown 优先 / 老数据回填 / 畸形守卫三种路径

        四行数据（同一日）：
        - 行1 breakdown chat 三分项(10/20/30) + 裸列冲突值(999/5/999) -> breakdown 优先
        - 行2 无 breakdown 老数据裸列(100/30/50) -> 回填 non_cached=70
        - 行3 畸形 {"chat": "not-an-object"} 裸列(200/0/60) -> 守卫 NULL 回落裸列
        - 行4 畸形 {"chat": {"non_cached_input_tokens": "abc"}} 裸列(300/50/70)
              -> 正则守卫 NULL，non_cached 回落 250、cached/output 回落裸列
        期望 total_token_qty：non_cached=530, cached=100, cache_creation=0,
        output=210, video/asr/embedding=0
        """
        tenant_id = temp_tenant_for_private_cloud
        _insert_chat_record_raw(
            tenant_id,
            usage_breakdown={"chat": {
                "non_cached_input_tokens": 10, "cached_input_tokens": 20,
                "completion_tokens": 30,
            }},
            prompt_tokens=999, cached_input_tokens=5, completion_tokens=999,
        )
        _insert_chat_record_raw(
            tenant_id, usage_breakdown=None,
            prompt_tokens=100, cached_input_tokens=30, completion_tokens=50,
        )
        _insert_chat_record_raw(
            tenant_id, usage_breakdown={"chat": "not-an-object"},
            prompt_tokens=200, cached_input_tokens=0, completion_tokens=60,
        )
        _insert_chat_record_raw(
            tenant_id,
            usage_breakdown={"chat": {"non_cached_input_tokens": "abc"}},
            prompt_tokens=300, cached_input_tokens=50, completion_tokens=70,
        )

        with patch.object(
            billing_settings().deployment, "private_cloud", True
        ):
            response = _call_get_usage(tenant_id)

        assert response["success"] is True, response.get("message")
        assert len(response["items"]) >= 1
        for it in response["items"]:
            assert "token_qty" in it, "私有云模式 items 应返回 token_qty"
            assert set(it["token_qty"].keys()) == TOKEN_QTY_KEYS
        summary = response["summary"]
        assert "total_token_qty" in summary, "私有云模式 summary 应返回 total_token_qty"
        tq = summary["total_token_qty"]
        assert set(tq.keys()) == TOKEN_QTY_KEYS
        assert tq["non_cached_input"] == 530, \
            f"breakdown 优先 + 回填 + 守卫回落应为 530，实际 {tq['non_cached_input']}"
        assert tq["cached_input"] == 100
        assert tq["cache_creation_input"] == 0
        assert tq["output"] == 210
        assert tq["video"] == 0
        assert tq["asr"] == 0
        assert tq["embedding"] == 0
        # token 数为整数展示（_clean_num 去 .0 尾巴）
        assert isinstance(tq["non_cached_input"], int)

    def test_public_cloud_usage_has_no_token_qty(self, temp_tenant_for_private_cloud):
        """公有云回归：不返回 token_qty / total_token_qty 字段"""
        tenant_id = temp_tenant_for_private_cloud
        _insert_chat_record_raw(
            tenant_id, usage_breakdown=None,
            prompt_tokens=100, cached_input_tokens=30, completion_tokens=50,
        )

        with patch.object(
            billing_settings().deployment, "private_cloud", False
        ):
            response = _call_get_usage(tenant_id)

        assert response["success"] is True
        for it in response["items"]:
            assert "token_qty" not in it, "公有云模式不应返回 token_qty"
        assert "total_token_qty" not in response["summary"]


class TestDailyDetailReveal:
    """/usage/daily-detail 私有云 reveal_tokens / reveal_pricing"""

    FULL_BREAKDOWN = {
        "chat": {
            "non_cached_input_tokens": 641,
            "cached_input_tokens": 217088,
            "completion_tokens": 123,
            "model": "deepseek-flash",
            "credit": 1.24,
            "unit_prices": {"input_per_m": 3, "cached_input_per_m": 0.1, "output_per_m": 9},
            "usage_factor": 50,
            "credits": {
                "non_cached_input": 0.09615,
                "cached_input": 1.08544,
                "output": 0.05535,
            },
        },
        "asr": {
            "calls": 1,
            "model": "aliyun-nls-asr",
            "credit": 1,
            "usage_factor": 100,
            "unit_price_per_call": 0.01,
        },
    }

    def _insert_full_record(self, tenant_id):
        return _insert_chat_record_raw(
            tenant_id, usage_breakdown=self.FULL_BREAKDOWN,
            prompt_tokens=222000, cached_input_tokens=217088, completion_tokens=123,
            credit_cost=3,
        )

    def test_private_cloud_tenant_admin_gets_stripped_breakdown(
        self, temp_tenant_for_private_cloud
    ):
        """私有云 + 租户管理员：返回 breakdown_items（公有云下不返回），
        但单价/系数/积分被剥离，qty 保留"""
        tenant_id = temp_tenant_for_private_cloud
        record_id = self._insert_full_record(tenant_id)
        today = datetime.now().strftime("%Y-%m-%d")

        with patch.object(
            billing_settings().deployment, "private_cloud", True
        ):
            response = _call_daily_detail(tenant_id, "tenant_admin", today)

        assert response["success"] is True, response.get("message")
        items = [it for it in response["items"] if it["record_id"] == record_id]
        assert len(items) == 1
        it = items[0]
        # reveal_tokens 扩展：token 三列在（公有云租户管理员不返回）
        assert it["prompt_tokens"] == 222000
        assert it["cached_input_tokens"] == 217088
        assert it["completion_tokens"] == 123
        # breakdown_items 返回且 qty 保留
        bd = it["breakdown_items"]
        assert len(bd) == 7
        assert bd[0]["key"] == "non_cached_input"
        assert bd[0]["qty"] == 641
        assert bd[1]["qty"] == 217088
        assert bd[2]["qty"] is None, "未提供的 cache_creation 分项 qty 应为 None（前端 '-' 兜底）"
        assert bd[3]["qty"] == 123
        asr_item = [b for b in bd if b["key"] == "asr"][0]
        assert asr_item["qty"] == 1
        # 剥离：单价/系数/积分全空，is_per_million 一律 False
        for bi in bd:
            assert bi["unit_price"] is None, f"{bi['key']} 单价应剥离"
            assert bi["usage_factor"] is None, f"{bi['key']} 系数应剥离"
            assert bi["credit"] is None, f"{bi['key']} 积分应剥离"
            assert bi["is_per_million"] is False, f"{bi['key']} is_per_million 应置 False"
        assert it["model"] == "deepseek-flash"

    def test_private_cloud_platform_admin_keeps_pricing(
        self, temp_tenant_for_private_cloud
    ):
        """私有云 + 平台管理员：单价/系数/积分保留（reveal_pricing 仅平台管理员）"""
        tenant_id = temp_tenant_for_private_cloud
        record_id = self._insert_full_record(tenant_id)
        today = datetime.now().strftime("%Y-%m-%d")

        with patch.object(
            billing_settings().deployment, "private_cloud", True
        ):
            response = _call_daily_detail(tenant_id, "platform_admin", today)

        assert response["success"] is True
        items = [it for it in response["items"] if it["record_id"] == record_id]
        assert len(items) == 1
        bd = items[0]["breakdown_items"]
        assert len(bd) == 7
        non_cached = bd[0]
        assert non_cached["qty"] == 641
        assert non_cached["unit_price"] == 3
        assert non_cached["usage_factor"] == 50
        assert non_cached["credit"] == 0.09615
        assert non_cached["is_per_million"] is True
        asr_item = [b for b in bd if b["key"] == "asr"][0]
        assert asr_item["unit_price"] == 0.01
        assert asr_item["is_per_million"] is False

    def test_public_cloud_tenant_admin_no_breakdown(
        self, temp_tenant_for_private_cloud
    ):
        """公有云回归：租户管理员不返回 token 三列与 breakdown_items"""
        tenant_id = temp_tenant_for_private_cloud
        record_id = self._insert_full_record(tenant_id)
        today = datetime.now().strftime("%Y-%m-%d")

        with patch.object(
            billing_settings().deployment, "private_cloud", False
        ):
            response = _call_daily_detail(tenant_id, "tenant_admin", today)

        assert response["success"] is True
        items = [it for it in response["items"] if it["record_id"] == record_id]
        assert len(items) == 1
        assert "prompt_tokens" not in items[0]
        assert "breakdown_items" not in items[0]


def billing_settings():
    """billing_balance 模块导入的 settings 单例（patch 目标）"""
    from src.config.settings import settings
    return settings
