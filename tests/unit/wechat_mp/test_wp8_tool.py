"""WP8 智能体薄工具 + 售前闭环测试（真实 PG，复用 test_service 替身，照 wp5 probe 模式）。

覆盖（计划 WP8 节 + 设计 §3/§14）：
- 工具 → 入库 → 检索命中闭环：wechat_mp_sync 提交真实夹具 URL → claim_and_run →
  真实混合检索可见
- 状态查询：run_id 详情（终态+计数+items 汇总）、他租户/不存在统一「未找到」（不泄露
  存在性）、run_id 字符串类型规范化、最近 5 条列表
- 边界：>50 条（业务错误文案）、全非法 URL、空 urls、上下文缺失
- 身份可信：InputModel 无任何身份字段；LLM 多传的身份参数被忽略，租户归属只来自
  可信执行上下文
- 自动发现：import 后 _CATALOG 含两个工具名（discover_tool_classes 可发现）

抓取走 StubFetcher（WP3 真实夹具喂替身，不触网）；embedding 走 FakeEmbeddingClient
（恒定单位向量，检索相似度恒 1.0，可见性断言不依赖语义）。
"""

from pathlib import Path
import json

import pytest

from src.tools.context import ExecutionContextFactory, tool_execution_scope
from src.tools.wechat_mp_sync_tool import (
    WechatMPSyncInput,
    WechatMPSyncStatusInput,
    WechatMPSyncStatusTool,
    WechatMPSyncTool,
)

from .conftest import cleanup_tenant
from .test_service import (  # noqa: F401  复用开发测试的替身与 DB 辅助
    StubFetcher,
    _create_tenant,
    _make_service,
    _query_one,
    _real_vector_db_patch,
    _retrieve_doc_ids,
    ok_result,
)

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "wechat_mp"
FIXTURE_URL = (FIXTURES_DIR / "article_freepublish.url.txt").read_text(
    encoding="utf-8"
).strip()
FIXTURE_HTML = FIXTURES_DIR / "article_freepublish.html"
# WP0 实测样本标题关键词（检索断言用）
FIXTURE_QUERY = "爱定义 数智化转型"

INVALID_URL = "https://example.com/not-mp-article"


def _agent_context(tenant_id: str, user_id: str = "u-wp8", subagent_id: str = "pre-sales"):
    """构造可信工具执行上下文（对齐生产 ToolExecutor 安装的上下文形态）。"""
    return ExecutionContextFactory.for_agent_call(
        tenant_id=tenant_id, user_id=user_id, subagent_id=subagent_id,
    )


def _short_url(n: int) -> str:
    return f"https://mp.weixin.qq.com/s/Wp8Batch{n:04d}Ab"


# ------------------------------- 工具定义与自动发现 -------------------------------


class TestToolDefinition:
    def test_input_models_have_no_identity_fields(self):
        """身份可信（静态）：InputModel 不含 tenant_id/user_id 等任何身份字段，
        LLM 无法通过参数改变租户归属。"""
        assert set(WechatMPSyncInput.model_fields.keys()) == {"urls"}
        assert set(WechatMPSyncStatusInput.model_fields.keys()) == {"run_id"}

    def test_sync_tool_definition_and_wording(self):
        """口径硬规则必须写进 description（只承诺排队、不宣称发现最新、刷新语义、
        完成后走 knowledge_base_search）。"""
        tool = WechatMPSyncTool()
        assert tool.name == "wechat_mp_sync"
        assert tool.display_name == "同步公众号文章"
        assert "已提交获取/刷新任务" in tool.description
        assert "排队" in tool.description
        assert "不能自动发现公众号最新文章" in tool.description
        assert "刷新" in tool.description and "不重复计费" in tool.description
        assert "knowledge_base_search" in tool.description
        schema = tool.to_tool_definition()["input_schema"]
        assert schema.get("required") == ["urls"]
        assert "URL" in schema["properties"]["urls"]["description"]

    def test_status_tool_definition_and_wording(self):
        tool = WechatMPSyncStatusTool()
        assert tool.name == "wechat_mp_sync_status"
        assert tool.display_name == "查询公众号同步状态"
        assert "run_id" in tool.description
        assert "失败原因" in tool.description
        assert "knowledge_base_search" in tool.description
        schema = tool.to_tool_definition()["input_schema"]
        assert "run_id" not in schema.get("required", [])

    def test_tools_discovered_in_catalog(self):
        """import 即登记 _CATALOG，discover_tool_classes() 能发现两个工具（无需手工注册）。"""
        import src.tools.wechat_mp_sync_tool  # noqa: F401
        from src.tools.base import _CATALOG
        from src.tools.registry import discover_tool_classes

        discover_tool_classes()
        names = {cls.name for cls in _CATALOG.values()}
        assert {"wechat_mp_sync", "wechat_mp_sync_status"} <= names


# ------------------------------- 闭环与状态查询 -------------------------------


class TestClosedLoop:
    async def test_tool_submit_ingest_then_retrievable(self, require_db, tenant_id):
        """闭环：工具提交 → 排队受理 → worker 入库 → 检索命中。"""
        _create_tenant(tenant_id)
        fetcher = StubFetcher()
        fetcher.set_page(FIXTURE_URL, ok_result(FIXTURE_HTML.read_text(encoding="utf-8")))

        tool = WechatMPSyncTool()
        with tool_execution_scope(_agent_context(tenant_id)):
            result = await tool.execute(urls=[FIXTURE_URL])

        assert result["success"] is True
        assert isinstance(result["run_id"], int)
        assert result["accepted"] == 1
        assert result["rejected"] == [] and result["duplicates"] == []
        # 口径硬规则写入返回 message
        assert "已提交获取/刷新任务" in result["message"]
        assert "排队" in result["message"]
        assert "不能自动发现公众号最新文章" in result["message"]
        assert "knowledge_base_search" in result["message"]

        # 受理三件套：queued run + pending items（trigger=manual，user_id 来自上下文）
        run = _query_one(
            "SELECT * FROM bs_wechat_mp_sync_runs WHERE id = %s", (result["run_id"],)
        )
        assert run["tenant_id"] == tenant_id
        assert run["status"] == "queued"
        assert run["trigger_type"] == "manual"
        assert run["user_id"] == "u-wp8"

        # worker 领取执行（真实 PG + 真实 pgvector + 夹具页正文提取；embedding 走替身）
        svc = _make_service(fetcher)
        outcome = await svc.claim_and_run(tenant_id)
        assert outcome["run_ids"] == [result["run_id"]]

        run = _query_one(
            "SELECT * FROM bs_wechat_mp_sync_runs WHERE id = %s", (result["run_id"],)
        )
        assert run["status"] == "success"
        assert run["new_count"] == 1 and run["failed_count"] == 0

        article = _query_one(
            "SELECT doc_id, processing_status FROM bs_wechat_mp_articles "
            "WHERE tenant_id = %s",
            (tenant_id,),
        )
        assert article["processing_status"] == "success"
        doc_id = article["doc_id"]
        assert doc_id

        # 检索命中（与 test_service 相同的真实混合检索路径）
        doc_ids, _ = await _retrieve_doc_ids(tenant_id, FIXTURE_QUERY)
        assert doc_id in doc_ids

    async def test_status_query_by_run_id_after_completion(self, require_db, tenant_id):
        """状态查询：终态 + 计数 + credits_charged + items 汇总字段齐全。"""
        _create_tenant(tenant_id)
        fetcher = StubFetcher()
        fetcher.set_page(FIXTURE_URL, ok_result(FIXTURE_HTML.read_text(encoding="utf-8")))
        with tool_execution_scope(_agent_context(tenant_id)):
            submit = await WechatMPSyncTool().execute(urls=[FIXTURE_URL])
        assert submit["success"] is True
        await _make_service(fetcher).claim_and_run(tenant_id)

        with tool_execution_scope(_agent_context(tenant_id)):
            status = await WechatMPSyncStatusTool().execute(run_id=submit["run_id"])

        assert status["success"] is True
        run = status["run"]
        assert run["run_id"] == submit["run_id"]
        assert run["status"] == "success"
        assert run["new_count"] == 1
        assert run["total_count"] == 1
        assert "credits_charged" in run
        # datetime 必须 ISO 字符串化（工具结果经 json.dumps 序列化，datetime 会炸）
        for _ts_field in ("created_at", "started_at", "completed_at"):
            assert run[_ts_field] is None or isinstance(run[_ts_field], str), (
                _ts_field, run[_ts_field],
            )
        json.dumps(status, ensure_ascii=False)
        assert status["items"] and len(status["items"]) == 1
        item = status["items"][0]
        assert set(item.keys()) == {
            "article_row_id", "action", "status", "error_code", "error_message",
        }
        assert item["status"] == "success" and item["action"] == "new"
        assert "knowledge_base_search" in status["message"]

    async def test_status_run_id_string_coerced(self, require_db, tenant_id):
        """LLM 常把 run_id 传成字符串，薄工具层做类型规范化。"""
        _create_tenant(tenant_id)
        with tool_execution_scope(_agent_context(tenant_id)):
            submit = await WechatMPSyncTool().execute(urls=[FIXTURE_URL])
            status = await WechatMPSyncStatusTool().execute(run_id=str(submit["run_id"]))
        assert status["success"] is True
        assert status["run"]["run_id"] == submit["run_id"]

    async def test_status_other_tenant_not_found_no_leak(self, require_db, tenant_id):
        """他租户 run_id 查不到：与不存在的 run 同文案，不泄露存在性。"""
        _create_tenant(tenant_id)
        with tool_execution_scope(_agent_context(tenant_id)):
            submit = await WechatMPSyncTool().execute(urls=[FIXTURE_URL])

        other_tenant = f"wmp_test_{tenant_id.split('_', 2)[-1]}_other"
        try:
            with tool_execution_scope(_agent_context(other_tenant)):
                cross = await WechatMPSyncStatusTool().execute(run_id=submit["run_id"])
            assert cross["success"] is False
            assert cross["error"] == "未找到该任务"
        finally:
            cleanup_tenant(other_tenant)

        with tool_execution_scope(_agent_context(tenant_id)):
            missing = await WechatMPSyncStatusTool().execute(run_id=999999999)
        assert missing["success"] is False
        assert missing["error"] == "未找到该任务"

    async def test_recent_runs_list_limit_five(self, require_db, tenant_id):
        """不传 run_id：返回最近任务概要（本租户隔离）。"""
        _create_tenant(tenant_id)
        fetcher = StubFetcher()
        # 第一次提交后立即消化，第二次提交才不会被 pending 去重拦截
        fetcher.set_page(
            FIXTURE_URL,
            [ok_result(FIXTURE_HTML.read_text(encoding="utf-8")),
             ok_result(FIXTURE_HTML.read_text(encoding="utf-8"))],
        )
        with tool_execution_scope(_agent_context(tenant_id)):
            first = await WechatMPSyncTool().execute(urls=[FIXTURE_URL])
            assert first["success"] is True
        await _make_service(fetcher).claim_and_run(tenant_id)

        with tool_execution_scope(_agent_context(tenant_id)):
            # 同一 URL 再次提交 = 请求刷新：首个 run 已消化 → 新建第二条 run
            second = await WechatMPSyncTool().execute(urls=[FIXTURE_URL, INVALID_URL])
            assert second["success"] is True
            assert second["accepted"] == 1
            assert len(second["rejected"]) == 1
            assert second["rejected"][0]["url"] == INVALID_URL

            listing = await WechatMPSyncStatusTool().execute()

        assert listing["success"] is True
        assert listing["total"] == 2
        assert len(listing["runs"]) == 2
        statuses = {r["status"] for r in listing["runs"]}
        assert statuses == {"success", "queued"}
        assert {r["run_id"] for r in listing["runs"]} == {first["run_id"], second["run_id"]}
        assert "knowledge_base_search" in listing["message"]


# ------------------------------- 边界与身份可信 -------------------------------


class TestBoundaries:
    async def test_gt50_urls_business_error_wording(self, require_db, tenant_id):
        """>50 条合法 URL：service 限流文案透传（工具不复制逻辑）。"""
        _create_tenant(tenant_id)
        urls = [_short_url(i) for i in range(51)]
        with tool_execution_scope(_agent_context(tenant_id)):
            result = await WechatMPSyncTool().execute(urls=urls)
        assert result["success"] is False
        assert "50" in result["error"]
        assert "分批" in result["error"]

    async def test_all_invalid_urls_no_run(self, require_db, tenant_id):
        """全非法 URL：不建 run，回传逐条驳回原因。"""
        _create_tenant(tenant_id)
        with tool_execution_scope(_agent_context(tenant_id)):
            result = await WechatMPSyncTool().execute(
                urls=[INVALID_URL, "   ", "ftp://mp.weixin.qq.com/s/x"]
            )
        assert result["success"] is False
        assert "没有可导入的合法 URL" in result["error"]
        assert len(result["rejected"]) == 3
        assert all(r["reason"] for r in result["rejected"])
        assert _query_one(
            "SELECT COUNT(*) AS c FROM bs_wechat_mp_sync_runs WHERE tenant_id = %s",
            (tenant_id,),
        )["c"] == 0

    async def test_empty_urls_friendly_failure(self, require_db, tenant_id):
        """空 urls：友好失败，不抛异常。"""
        _create_tenant(tenant_id)
        with tool_execution_scope(_agent_context(tenant_id)):
            result = await WechatMPSyncTool().execute(urls=[])
        assert result["success"] is False
        assert "urls 不能为空" in result["error"]

    async def test_missing_tenant_context_explicit_failure(self, require_db):
        """上下文缺失（tenant_id 为 None）：明确失败，不静默。"""
        sync_result = await WechatMPSyncTool().execute(urls=[FIXTURE_URL])
        assert sync_result["success"] is False
        assert "租户身份上下文" in sync_result["error"]

        status_result = await WechatMPSyncStatusTool().execute()
        assert status_result["success"] is False
        assert "租户身份上下文" in status_result["error"]

        status_by_id = await WechatMPSyncStatusTool().execute(run_id=1)
        assert status_by_id["success"] is False
        assert "租户身份上下文" in status_by_id["error"]

    async def test_llm_passed_identity_kwargs_are_ignored(self, require_db, tenant_id):
        """身份可信（运行时）：LLM 伪造 tenant_id/user_id 参数不影响租户归属，
        run 只落可信上下文的租户。"""
        _create_tenant(tenant_id)
        evil_tenant = "wmp_evil_tenant"
        with tool_execution_scope(_agent_context(tenant_id)):
            result = await WechatMPSyncTool().execute(
                urls=[FIXTURE_URL], tenant_id=evil_tenant, user_id="attacker"
            )
        assert result["success"] is True
        run = _query_one(
            "SELECT tenant_id, user_id FROM bs_wechat_mp_sync_runs WHERE id = %s",
            (result["run_id"],),
        )
        assert run["tenant_id"] == tenant_id
        assert run["user_id"] == "u-wp8"
        assert _query_one(
            "SELECT COUNT(*) AS c FROM bs_wechat_mp_sync_runs WHERE tenant_id = %s",
            (evil_tenant,),
        )["c"] == 0
