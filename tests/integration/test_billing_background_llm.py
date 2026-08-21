"""background_llm 计费兜底路径集成测试（P1 修复）

覆盖 `record_background_llm_usage` + `_persist_background_llm_record` 在
background_runner 调度场景下的计费归属：

- 正常：传入 tenant_id/user_id/source -> chat_records 落库且归属正确
- 边界：不传 kwargs -> 兼容老路径（tenant_id IS NULL）
- 异常：usage=None / usage={} -> 早退，无新 chat_records
- 端到端：mock _call_summary_llm_direct -> compress_session(force=True)
  -> 落库 chat_records.tenant_id = SessionMeta.tenant_id
"""

import uuid
from unittest.mock import patch, AsyncMock

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def temp_tenant():
    """创建临时租户，测试后清理（不充值，仅用于 chat_records 归属）

    同时把 settings.memory.mid_term.summary_llm.model 临时改为 qwen3.7-flash
    （有单价配置），让 _persist_background_llm_record 内 calculate_credit_cost
    能算出非 0 积分；teardown 恢复原值。
    """
    from src.saas.db.tenant_db import TenantDB
    from src.db.database import get_db_connection
    from src.core.cache_utils import invalidate_tenant_cache
    from src.config.settings import settings

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
    invalidate_tenant_cache(tenant_id)

    # 临时改用有单价的模型（deepseek-chat 未在 token_cost_prices 配置单价）
    original_model = settings.memory.mid_term.summary_llm.model
    settings.memory.mid_term.summary_llm.model = "qwen3.7-flash"

    yield tenant_id

    # 恢复
    settings.memory.mid_term.summary_llm.model = original_model

    # 清理：删除本测试产生的 chat_records，再软删 + 物理删除租户
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM chat_records WHERE tenant_id = %s",
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


def _count_chat_records(tenant_id: str, source_type: str = "background_llm") -> int:
    """统计指定租户 + source_type 的 chat_records 数量"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) AS cnt FROM chat_records "
            "WHERE tenant_id = %s AND source_type = %s",
            (tenant_id, source_type),
        )
        row = cursor.fetchone()
        return int(row["cnt"] if row else 0)


def _fetch_latest_bg_record(tenant_id: str):
    """取最新一条 background_llm 记录"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM chat_records "
            "WHERE tenant_id = %s AND source_type = 'background_llm' "
            "ORDER BY created_at DESC LIMIT 1",
            (tenant_id,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


class TestRecordBackgroundLLMUsage:
    """record_background_llm_usage 函数级测试"""

    def test_normal_with_tenant_user(self, temp_tenant):
        """正常：传入 tenant_id/user_id/source -> chat_records 落库正确"""
        from src.services.session_record import record_background_llm_usage

        tenant_id = temp_tenant
        before = _count_chat_records(tenant_id)

        record_background_llm_usage(
            {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            tenant_id=tenant_id,
            user_id="bg_user",
            source="mid_term_summary",
            user_message="上下文压缩扫描摘要",
        )

        after = _count_chat_records(tenant_id)
        assert after == before + 1, f"应新增 1 条记录，实际新增 {after - before}"

        rec = _fetch_latest_bg_record(tenant_id)
        assert rec is not None
        assert rec["user_id"] == "bg_user"
        assert rec["source_type"] == "background_llm"
        assert rec["prompt_tokens"] == 100
        assert rec["completion_tokens"] == 50
        # credit_cost 应大于 0（按 deepseek-chat 单价计算）
        assert rec["credit_cost"] > 0
        # session_id 应含 source + user_id（与 memory_summarizer 一致）
        assert "mid_term_summary" in rec["session_id"]
        assert "bg_user" in rec["session_id"]
        # provider 标记为 mid_term_background_scan（区别于 memory_summarizer 的 qwen）
        assert rec["provider"] == "mid_term_background_scan"

    def test_boundary_no_tenant_kwargs(self, temp_tenant):
        """边界：不传 tenant_id（仅传 user_id）-> 兼容老路径，tenant_id IS NULL 落库

        注：chat_records.user_id 是 TEXT（无 NOT NULL 约束），允许 NULL；
        本用例验证 tenant_id 兜底为 NULL 的兼容路径（P1 修复前的硬编码行为），
        同时传入 user_id 以便落库后能定位到本测试产生的记录做清理。
        """
        from src.services.session_record import record_background_llm_usage
        from src.db.database import get_db_connection

        record_background_llm_usage(
            {"prompt_tokens": 80, "completion_tokens": 40, "total_tokens": 120},
            user_id="bg_boundary_user",
        )

        # 查最新一条 background_llm 且 tenant_id IS NULL 的记录
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM chat_records "
                "WHERE source_type = 'background_llm' AND tenant_id IS NULL "
                "AND user_id = 'bg_boundary_user' "
                "ORDER BY created_at DESC LIMIT 1"
            )
            row = cursor.fetchone()

        assert row is not None, "应落库一条 tenant_id IS NULL 的记录"
        rec = dict(row)
        assert rec["prompt_tokens"] == 80
        assert rec["completion_tokens"] == 40
        assert rec["tenant_id"] is None
        # session_id 格式：background_llm_{source}_{user_id|unknown}
        assert "background_llm_" in rec["session_id"]
        assert "bg_boundary_user" in rec["session_id"]

        # 清理这条 NULL 记录避免污染其他测试
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "DELETE FROM chat_records WHERE record_id = %s",
                    (rec["record_id"],),
                )
                conn.commit()
        except Exception:
            pass

    def test_exception_none_usage(self, temp_tenant):
        """异常：usage=None -> 早退，无新 chat_records 落库"""
        from src.services.session_record import record_background_llm_usage

        before = _count_chat_records(temp_tenant)
        record_background_llm_usage(None, tenant_id=temp_tenant)
        after = _count_chat_records(temp_tenant)
        assert after == before, "usage=None 时不应落库"

    def test_exception_empty_usage(self, temp_tenant):
        """异常：usage={} -> 早退（`if not usage` 判空），无新 chat_records"""
        from src.services.session_record import record_background_llm_usage

        before = _count_chat_records(temp_tenant)
        record_background_llm_usage({}, tenant_id=temp_tenant)
        after = _count_chat_records(temp_tenant)
        assert after == before, "usage={} 时不应落库"


class TestCompressSessionBilling:
    """端到端：compress_session -> _call_summary_llm -> record_background_llm_usage"""

    def test_compress_session_attributes_tenant(self, temp_tenant):
        """mock _call_summary_llm_direct 后，compress_session 落库计费归属到 SessionMeta.tenant_id"""
        from src.db.models import SessionDB, MessageDB
        from src.config.settings import MidTermMemoryConfig, SummaryLLMConfig
        from src.memory.mid_term import ContextCompressionService

        tenant_id = temp_tenant

        # 1) 创建 chat_session，绑定 tenant_id
        session = SessionDB.create(
            user_id="bg_user",
            title="P1 测试会话",
            tenant_id=tenant_id,
        )
        assert session is not None, "chat_sessions 创建失败"
        session_id = session["session_id"]

        # 2) 插入 2 条消息（user + assistant），让 COMPRESS 区非空
        #    使用 header_keep=0 / tail_keep=1 配置，messages[0]=user 进入 COMPRESS
        MessageDB.create(session_id, "user", "请帮我总结这段对话")
        MessageDB.create(session_id, "assistant", "好的，我马上帮您总结。")

        # 3) 构造 ContextCompressionService，覆盖 header_keep/tail_keep 避免 30+ 消息
        #    summary_llm.model 用 qwen3.7-flash（有单价配置，credit_cost > 0）
        cfg = MidTermMemoryConfig(
            enabled=True,
            header_keep=0,
            tail_keep=1,
            summary_max_tokens=100,
            summary_llm_retry=0,
            summary_llm=SummaryLLMConfig(provider="deepseek", model="qwen3.7-flash"),
        )
        service = ContextCompressionService(settings_cfg=cfg)

        # 4) mock _call_summary_llm_direct 返回固定 usage；
        #    同时 mock _get_provider_api_key 让 has_direct_key=True 走 direct 分支
        fake_usage = {"prompt_tokens": 80, "completion_tokens": 40, "total_tokens": 120}
        before = _count_chat_records(tenant_id)

        async def fake_direct(*args, **kwargs):
            return ("summary text", fake_usage)

        with patch(
            "src.memory.mid_term._call_summary_llm_direct", new=fake_direct
        ), patch(
            "src.memory.mid_term._get_provider_api_key",
            return_value="fake_key_for_test",
        ):
            import asyncio
            result = asyncio.get_event_loop().run_until_complete(
                service.compress_session(session_id, "chat", force=True)
            )

        # 5) 断言 chat_records 落库 tenant_id=T
        after = _count_chat_records(tenant_id)
        assert after == before + 1, (
            f"compress_session 应新增 1 条 background_llm 记录，"
            f"实际新增 {after - before}（result={result}）"
        )

        rec = _fetch_latest_bg_record(tenant_id)
        assert rec is not None
        assert rec["tenant_id"] == tenant_id
        assert rec["user_id"] == "bg_user"
        assert rec["source_type"] == "background_llm"
        assert rec["prompt_tokens"] == 80
        assert rec["completion_tokens"] == 40
        assert rec["credit_cost"] > 0

        # 清理：删除本测试产生的 chat_messages / chat_sessions
        try:
            from src.db.database import get_db_connection
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "DELETE FROM chat_messages WHERE session_id = %s",
                    (session_id,),
                )
                cursor.execute(
                    "DELETE FROM chat_sessions WHERE session_id = %s",
                    (session_id,),
                )
                conn.commit()
        except Exception:
            pass
