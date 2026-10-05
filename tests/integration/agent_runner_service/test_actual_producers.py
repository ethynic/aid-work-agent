"""Actual summary + synchronous SDK embedding IO owned by one durable receipt scope."""
from decimal import Decimal
import os

import pytest

from .provider import Reply, EmbeddingReply
from .test_worker import workers, api_pair, prices, accept, runner

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("reported", [True, False])
def test_real_summary_background_helper_and_embedding_sdk_have_original_receipts_and_atomic_finalization(workers, actors, service_database, reported):
    summary_reply = Reply(content="actual-summary-known-content") if reported else Reply(content="actual-summary-known-content", reported_usage=None)
    embedding_reply = EmbeddingReply() if reported else EmbeddingReply(reported_usage=None)
    summary_marker = workers.provider.register(summary_reply)
    embedding_marker = workers.provider.register(embedding_reply)
    accepted = accept(workers.api, actors["a"], summary_marker)
    original_price = service_database.rows("SELECT embedding_price_per_m FROM token_cost_prices WHERE model_name='text-embedding-v3'")
    service_database.rows("INSERT INTO token_cost_prices(model_name,embedding_price_per_m) VALUES ('text-embedding-v3',1) ON CONFLICT(model_name) DO UPDATE SET embedding_price_per_m=1")
    script = """
import asyncio,os,sys
from src.config.settings import settings,SummaryLLMConfig
from src.db.database import init_postgres_pool,close_postgres_pool
from src.services.agent_runner.execution_repository import ExecutionRepository
from src.services.agent_runner.usage_repository import UsageRepository
from src.services.agent_runner.ownership import Attempt
from src.services.agent_runner.usage_context import UsageScope,runner_usage_scope
from src.services.agent_runner.finalizer import RunnerFinalizer
from src.memory.mid_term import ContextCompressionService
from src.knowledge.embedding.embedding_client import TextEmbeddingV3Client
init_postgres_pool()
try:
    repository=ExecutionRepository()
    owned=repository.acquire('fixture-actual-producers',30)
    assert owned['runner_id']==sys.argv[1]
    attempt=Attempt(owned['runner_id'],owned['worker_id'],owned['attempt'])
    scope=UsageScope(attempt,UsageRepository(),owned['runner_id'])
    config=settings.memory.mid_term.model_copy(update={
        'summary_llm':SummaryLLMConfig(provider='qwen',model=sys.argv[4]),'summary_llm_retry':0})
    summary=ContextCompressionService(settings_cfg=config)
    embedding=TextEmbeddingV3Client(os.environ['EMBEDDING_API_KEY'])
    async def produce():
        with runner_usage_scope(scope):
            content,usage,truncated=await summary._call_summary_llm(None,[{'role':'user','content':sys.argv[2]}],
                tenant_id=owned['tenant_id'],user_id=owned['user_id'])
            assert content=='actual-summary-known-content' and not truncated
            vectors=await embedding.embed_batch([sys.argv[3]])
            assert len(vectors)==1 and len(vectors[0])==1024
    asyncio.run(produce())
    result={'status':'completed','output':'producer-known-output','images':[],'messages':[]}
    checkpoint={'execution':{'iteration':0,'tools':{},'children':{},'model_calls':[]}}
    repository.stage_finalization(attempt,owned['revision'],checkpoint,result,owned['public_snapshot'])
    RunnerFinalizer().finalize(attempt)
finally:
    close_postgres_pool()
"""
    process = workers.processes.start(["-c",script,accepted["runner_id"],summary_marker,embedding_marker,workers.models[0]],
                                      environment=workers.environment,private_working_directory=True)
    try:
        workers.assert_clean_exit(process)
        finished = runner(service_database,accepted["runner_id"])
        assert finished["status"] == "completed"
        receipts = service_database.rows("SELECT owner,purpose,provider,model,phase,usage,applied FROM agent_runner_usage_receipts WHERE runner_id=%s ORDER BY owner",(accepted["runner_id"],))
        assert len(receipts) == 2
        assert [(row["owner"],row["purpose"],row["provider"],row["model"]) for row in receipts] == [
            ("embedding","embedding","dashscope","text-embedding-v3"), ("llm","compression","qwen",workers.models[0])]
        if reported:
            assert all(row["phase"]=="observed" and row["applied"] for row in receipts)
            assert receipts[0]["usage"]["tokens"] == 9
            assert finished["settlement_status"] == "settled"
            cost = Decimal("0.02")
        else:
            assert all(row["phase"]=="unknown" and row["usage"] is None and not row["applied"] for row in receipts)
            assert finished["settlement_status"] == "pending"
            cost = Decimal("0")
        assert service_database.rows("SELECT credit_cost FROM chat_records WHERE user_id=%s",(actors["a"].user_id,)) == [{"credit_cost":cost}]
        assert service_database.rows("SELECT credit_balance FROM tenants WHERE tenant_id=%s",(actors["a"].tenant_id,))[0]["credit_balance"] == Decimal("1000")-cost
        assert len(workers.provider.requests(summary_marker)) == len(workers.provider.requests(embedding_marker)) == 1
        assert not workers.provider.errors
    finally:
        workers.processes.stop(process)
        if original_price:
            service_database.rows("UPDATE token_cost_prices SET embedding_price_per_m=%s WHERE model_name='text-embedding-v3'",(original_price[0]["embedding_price_per_m"],))
        else:
            service_database.rows("DELETE FROM token_cost_prices WHERE model_name='text-embedding-v3'")
