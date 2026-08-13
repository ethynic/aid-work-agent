"""知识库 embedding 计费集成测试

覆盖 `KnowledgeBaseService._record_knowledge_embedding_billing` 的：
- 正常：embedding_tokens + summary_usage -> 合并计费，usage_breakdown 含两分项
- 边界：summary_usage=None -> usage_breakdown 只含 embedding 分项
- 异常：embedding_tokens=0 + summary_usage=None -> 早退，无 chat_records 落库
"""

import uuid

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def temp_tenant():
    """创建临时租户，测试后清理"""
    from src.saas.db.tenant_db import TenantDB
    from src.db.database import get_db_connection
    from src.core.cache_utils import invalidate_tenant_cache

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

    yield tenant_id

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM chat_records WHERE tenant_id = %s AND source_type = 'knowledge_embedding'",
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


def _fetch_latest_kb_record(tenant_id: str):
    """取最新一条 knowledge_embedding 记录"""
    from src.db.database import get_db_connection
    import json

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM chat_records "
            "WHERE tenant_id = %s AND source_type = 'knowledge_embedding' "
            "ORDER BY created_at DESC LIMIT 1",
            (tenant_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        rec = dict(row)
        if rec.get("usage_breakdown"):
            try:
                rec["usage_breakdown"] = json.loads(rec["usage_breakdown"])
            except (Exception,):
                pass
        return rec


class TestKnowledgeEmbeddingBilling:
    """知识库文档处理计费"""

    def test_normal_with_embedding_and_summary(self, temp_tenant):
        """正常：embedding_tokens + summary_usage -> 合并计费"""
        from src.knowledge.service import KnowledgeBaseService
        from src.services.billing import (
            calculate_credit_cost,
            calculate_embedding_credit_cost,
        )

        tenant_id = temp_tenant
        service = KnowledgeBaseService()

        embedding_tokens = 500
        summary_usage = {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
            "cached_tokens": 0,
        }

        service._record_knowledge_embedding_billing(
            tenant_id=tenant_id,
            user_id="kb_user",
            doc_id=999,
            file_filename="x.pdf",
            embedding_tokens=embedding_tokens,
            summary_usage=summary_usage,
        )

        rec = _fetch_latest_kb_record(tenant_id)
        assert rec is not None, "应落库一条 knowledge_embedding 记录"
        assert rec["tenant_id"] == tenant_id
        assert rec["user_id"] == "kb_user"
        assert rec["source_type"] == "knowledge_embedding"
        assert rec["embedding_tokens"] == 500
        assert rec["prompt_tokens"] == 100
        assert rec["completion_tokens"] == 50

        # 校验 credit_cost = embedding_credit + llm_credit
        expected_embedding_credit = calculate_embedding_credit_cost(
            embedding_tokens=embedding_tokens,
        )
        # 知识库摘要使用主 gateway 默认模型（settings.llm.model_code）
        from src.config.settings import settings
        llm_model = getattr(settings.llm, "model_code", None) or "qwen-plus"
        expected_llm_credit = calculate_credit_cost(
            prompt_tokens=100,
            completion_tokens=50,
            model=llm_model,
            cached_input_tokens=0,
        )
        expected_total = round(expected_embedding_credit + expected_llm_credit, 2)
        # DB 返回的 credit_cost 是 Decimal，转 float 比较
        actual_cost = float(rec["credit_cost"])
        assert abs(actual_cost - expected_total) < 0.01, (
            f"credit_cost 应为 {expected_total}，实际 {actual_cost}"
        )

        # usage_breakdown 应含 embedding + summary_llm 两分项
        ub = rec["usage_breakdown"]
        assert "embedding" in ub
        assert "summary_llm" in ub
        assert ub["embedding"]["tokens"] == 500
        assert ub["summary_llm"]["prompt_tokens"] == 100
        assert ub["summary_llm"]["completion_tokens"] == 50

    def test_boundary_summary_none(self, temp_tenant):
        """边界：summary_usage=None -> usage_breakdown 只含 embedding 分项"""
        from src.knowledge.service import KnowledgeBaseService

        tenant_id = temp_tenant
        service = KnowledgeBaseService()

        service._record_knowledge_embedding_billing(
            tenant_id=tenant_id,
            user_id="kb_user",
            doc_id=998,
            file_filename="y.pdf",
            embedding_tokens=300,
            summary_usage=None,
        )

        rec = _fetch_latest_kb_record(tenant_id)
        assert rec is not None
        assert rec["embedding_tokens"] == 300
        # summary_usage=None 时 prompt/completion/total 都是 0
        assert rec["prompt_tokens"] == 0
        assert rec["completion_tokens"] == 0
        # usage_breakdown 只含 embedding
        ub = rec["usage_breakdown"]
        assert "embedding" in ub
        assert "summary_llm" not in ub
        assert ub["embedding"]["tokens"] == 300

    def test_exception_no_usage_no_records(self, temp_tenant):
        """异常：embedding_tokens=0 + summary_usage=None -> 早退无 chat_records"""
        from src.knowledge.service import KnowledgeBaseService
        from src.db.database import get_db_connection

        tenant_id = temp_tenant
        service = KnowledgeBaseService()

        # 先统计现有记录数
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) AS cnt FROM chat_records "
                "WHERE tenant_id = %s AND source_type = 'knowledge_embedding'",
                (tenant_id,),
            )
            before = int(cursor.fetchone()["cnt"])

        service._record_knowledge_embedding_billing(
            tenant_id=tenant_id,
            user_id="kb_user",
            doc_id=997,
            file_filename="z.pdf",
            embedding_tokens=0,
            summary_usage=None,
        )

        # 应早退，无新记录
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) AS cnt FROM chat_records "
                "WHERE tenant_id = %s AND source_type = 'knowledge_embedding'",
                (tenant_id,),
            )
            after = int(cursor.fetchone()["cnt"])

        assert after == before, "embedding_tokens=0 + summary_usage=None 时不应落库"
