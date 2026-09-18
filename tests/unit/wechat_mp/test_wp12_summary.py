"""wechat_mp WP12 入库内容质量优化单元测试（真实 PG，对齐既有替身模式）。

覆盖（计划 WP12 节）：

- VL 描述清洗（vision.clean_description）：常见前缀剥离（中/半角冒号、多轮剥）、
  无前缀原样、多行仅剥首行、空值透传；PARSE_INSTRUCTION 含禁前缀要求；
  VisionParser 成功路径产出已清洗
- 总结指令构造（summarize.build_summary_messages）：含 500 字/无标签/图文去重/
  不发挥要求，merged（原文文本 + [图片N: 描述]）完整进入 message
- ArticleSummarizer：成功 / 失败重试 1 次 / 两次失败回退 None / 空响应计失败 /
  wait_for 超时 / 超长截断 500 / 产出标签前缀剥离
- 管道：成功入库 chunk 文本=总结本身（不拼链接）、原文链接存 documents.file_path、raw_text=merged、
  metadata.content_mode='summary'；总结失败回退 raw_fallback+summary_fallback；
  超长总结入库截断 500 字；VL 前缀描述端到端清洗
- 计费：record_background_llm_usage 以 source='wechat_mp_summary'+model 被调用
  （monkeypatch 断言）；无 SessionRecord 线程的真实独立落账分支（chat_records 行）
- 不变量：content_hash 按原始节点（VL 描述/总结均不进指纹）；同文章二次 claim
  VL/总结 0 次调用、零计费；p2 存量行复核后重建为当前 pipeline（p5）总结版

抓取走 StubFetcher、总结走 FakeSummarizer/ArticleSummarizer+替身网关、VL 走
FakeVision（照 test_service/test_wp10 范式）。每用例独立随机租户，测后清理。
"""

import asyncio
import json
import os
import shutil

import pytest

from src.db.database import get_db_connection
from src.wechat_mp.content import extract_article
from src.wechat_mp.identity import normalize_url
from src.wechat_mp.image_downloader import DownloadOutcome, ImageDownload
from src.wechat_mp.service import WeChatMPSyncService
from src.wechat_mp.summarize import (
    SUMMARY_INSTRUCTION,
    SUMMARY_MAX_CHARS,
    SUMMARY_SOURCE_TYPE,
    ArticleSummarizer,
    build_summary_messages,
    truncate_summary,
)
from src.wechat_mp.vision import (
    PARSE_INSTRUCTION,
    UNRECOGNIZED_TEXT,
    VisionParser,
    VisionTarget,
    clean_description,
)

from .test_service import (  # noqa: F401  复用开发测试的替身与 DB 辅助
    BODY_V1,
    SHORT_URL,
    FakeSummarizer,
    StubFetcher,
    _create_tenant,
    _enqueue,
    _make_service,
    _query_all,
    _query_one,
    _real_vector_db_patch,
    _tenant_balance,
    make_article_html,
    make_image_only_html,
    ok_result,
)
from .test_wp10_image_vision import (
    FakeDownloader,
    FakeVision,
    RecordingGateway,
    _ok_response,
    _png_bytes,
    _raise,
)


def _doc_row(article_row_id: int):
    doc_id = _query_one(
        "SELECT doc_id FROM bs_wechat_mp_articles WHERE id = %s", (article_row_id,)
    )["doc_id"]
    return _query_one(
        "SELECT raw_text, summary, metadata, file_path FROM documents WHERE id = %s", (doc_id,)
    ), doc_id


def _chunk_join(doc_id: int) -> str:
    rows = _query_all(
        "SELECT text FROM chunks WHERE doc_id = %s ORDER BY chunk_index", (doc_id,))
    return "\n".join(r["text"] for r in rows)


class _TinyPngDownloader:
    """下载替身（真实 VisionParser 用）：按真实路径规范写极小 PNG 供 data URL 编码。"""

    def download(self, tenant_id, article_row_id, srcs):
        from io import BytesIO

        from PIL import Image

        base = os.path.join("storage", "tenants", tenant_id, "knowledge",
                            "wechat_mp", str(article_row_id))
        os.makedirs(base, exist_ok=True)
        images = []
        for n, _src in srcs:
            path = os.path.join(base, f"img_{n}.png")
            buf = BytesIO()
            Image.new("RGB", (2, 2)).save(buf, format="PNG")
            with open(path, "wb") as f:
                f.write(buf.getvalue())
            images.append(ImageDownload(
                n=n, local_path=path, ext="png", bytes_written=buf.tell()))
        return DownloadOutcome(images=images)


@pytest.fixture(autouse=True)
def _cleanup_storage(tenant_id):
    """测后清理该租户的图片转存目录（conftest 只清 DB 行）。"""
    yield
    shutil.rmtree(os.path.join("storage", "tenants", tenant_id), ignore_errors=True)


# =============================== VL 描述清洗 ===============================


class TestCleanDescription:
    def test_recognition_result_prefix_fullwidth_colon(self):
        assert clean_description("图片识别结果如下：春季促销活动") == "春季促销活动"

    def test_recognition_result_prefix_halfwidth_colon(self):
        assert clean_description("图片识别结果如下:春季促销") == "春季促销"

    def test_parse_and_recognition_variants(self):
        assert clean_description("图片解析结果如下：门店地址") == "门店地址"
        assert clean_description("识别结果：营业时间 9:00-21:00") == "营业时间 9:00-21:00"

    def test_label_prefixes_require_colon(self):
        assert clean_description("标题：春季促销") == "春季促销"
        assert clean_description("描述：全场八折") == "全场八折"
        assert clean_description("内容：活动细则如下") == "活动细则如下"
        # 无冒号不剥（保守：可能就是图片文字本身以该词开头）
        assert clean_description("标题 春季促销") == "标题 春季促销"

    def test_showcase_prefixes(self):
        assert clean_description("这张图片展示了春季促销海报") == "春季促销海报"
        assert clean_description("这张图显示：门店地址与营业时间") == "门店地址与营业时间"
        assert clean_description("图中展示了活动细则") == "活动细则"

    def test_no_prefix_unchanged(self):
        text = "春季促销全场八折，欢迎到店咨询。"
        assert clean_description(text) == text

    def test_multiline_strips_first_line_only(self):
        text = "图片识别结果如下：\n春季促销\n标题：活动海报"
        assert clean_description(text) == "春季促销\n标题：活动海报"

    def test_multi_round_nested_prefix(self):
        assert clean_description("图片识别结果如下：标题：春季促销") == "春季促销"

    def test_empty_passthrough(self):
        assert clean_description("") == ""
        assert clean_description("   ") == ""


class TestParseInstructionAndParser:
    def test_instruction_forbids_prefix_and_labels(self):
        assert "不要任何前缀、标签、标题行" in PARSE_INSTRUCTION
        assert "图片识别结果如下" in PARSE_INSTRUCTION  # 明确点名的反例
        assert "这张图片展示了" in PARSE_INSTRUCTION
        assert UNRECOGNIZED_TEXT in PARSE_INSTRUCTION

    def test_parser_success_output_cleaned(self, tmp_path):
        """成功路径接 clean_description：带前缀的模型产出入库前被剥掉。"""
        img = tmp_path / "img_1.png"
        img.write_bytes(_png_bytes(2, 2))
        gw = RecordingGateway(lambda i: _ok_response("图片识别结果如下：春季促销"))
        parser = VisionParser(
            targets=[VisionTarget("qwen", "qwen3-vl-plus")],
            gateway_factory=lambda p, m: gw,
        )
        outcome = asyncio.run(parser.describe_images([(1, str(img))], "t"))
        assert outcome.ok_count == 1
        assert outcome.successes[0].description == "春季促销"


# =============================== 总结指令与总结器 ===============================


class TestSummaryMessages:
    def test_instruction_requirements(self):
        assert "不超过500字" in SUMMARY_INSTRUCTION
        assert "只保留一份" in SUMMARY_INSTRUCTION  # 图文信息不重复
        assert "不要任何前缀、标签或标题行" in SUMMARY_INSTRUCTION
        assert "不评价不发挥、不编造未提及的信息" in SUMMARY_INSTRUCTION

    def test_merged_text_and_title_in_message(self):
        """merged 全文（原文文本节点原样 + [图片N: 干净描述]）完整进入 user message。"""
        merged = "正文第一段\n[图片1: 图一描述]"
        messages = build_summary_messages(merged, "春季活动")
        assert len(messages) == 1 and messages[0]["role"] == "user"
        content = messages[0]["content"]
        assert SUMMARY_INSTRUCTION in content
        assert "春季活动" in content
        assert "正文第一段" in content
        assert "[图片1: 图一描述]" in content


class TestTruncateSummary:
    def test_truncate_at_500_no_ellipsis(self):
        assert truncate_summary("字" * 800) == "字" * 500

    def test_within_limit_unchanged(self):
        assert truncate_summary("短文本") == "短文本"

    def test_label_prefix_stripped(self):
        assert truncate_summary("总结：要点一要点二") == "要点一要点二"

    def test_empty_safe(self):
        assert truncate_summary("") == ""


class TestArticleSummarizer:
    def test_success_returns_summary_model_usage(self):
        gw = RecordingGateway(lambda i: {
            "content": "总结正文要点",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        })
        result = asyncio.run(ArticleSummarizer(gateway=gw).summarize("原文内容", "标题"))
        assert result is not None
        text, model, usage = result
        assert text == "总结正文要点"
        assert usage["total_tokens"] == 15
        assert isinstance(model, str) and model  # 计费模型名已解析

    def test_retry_once_then_success(self):
        gw = RecordingGateway(
            lambda i: (_raise(RuntimeError("boom")) if i == 1
                       else {"content": "重试成功", "usage": {}})
        )
        result = asyncio.run(ArticleSummarizer(gateway=gw).summarize("原文", "标题"))
        assert result is not None and result[0] == "重试成功"
        assert len(gw.calls) == 2  # 初次 + 重试 1 次

    def test_both_failures_return_none(self):
        gw = RecordingGateway(lambda i: _raise(RuntimeError("down")))
        assert asyncio.run(ArticleSummarizer(gateway=gw).summarize("原文", "标题")) is None
        assert len(gw.calls) == 2

    def test_empty_content_counts_as_failure(self):
        gw = RecordingGateway(lambda i: {"content": "  ", "usage": {}})
        assert asyncio.run(ArticleSummarizer(gateway=gw).summarize("原文", "标题")) is None
        assert len(gw.calls) == 2

    def test_timeout_wraps_gateway_call(self):
        """gateway 无 timeout 参数：asyncio.wait_for 包裹生效（超时计失败进重试）。"""

        class SlowGateway:
            async def chat_no_thinking(self, messages=None, **kwargs):
                await asyncio.sleep(0.5)
                return {"content": "迟到的总结", "usage": {}}

        summarizer = ArticleSummarizer(gateway=SlowGateway(), timeout_seconds=0.05)
        assert asyncio.run(summarizer.summarize("原文", "标题")) is None

    def test_overlong_output_truncated_to_500(self):
        gw = RecordingGateway(lambda i: {"content": "长" * 800, "usage": {}})
        text, _, _ = asyncio.run(ArticleSummarizer(gateway=gw).summarize("原文", "标题"))
        assert len(text) == SUMMARY_MAX_CHARS == 500

    def test_output_label_prefix_stripped(self):
        gw = RecordingGateway(lambda i: {"content": "总结：核心要点是打折", "usage": {}})
        text, _, _ = asyncio.run(ArticleSummarizer(gateway=gw).summarize("原文", "标题"))
        assert text == "核心要点是打折"


# =============================== 管道集成 ===============================


class TestPipelineSummary:
    async def test_success_doc_is_summary_with_source_link(self, tenant_id):
        """总结成功：chunk 文本=总结本身（无「文档标题：」前缀、不拼链接）、
        原文链接存 documents.file_path（文档位置字段）、raw_text=merged、
        summary=总结、metadata.content_mode='summary'。"""
        _create_tenant(tenant_id)
        fetcher = StubFetcher()
        fetcher.set_page(SHORT_URL, ok_result(make_article_html("春季活动", BODY_V1)))
        summarizer = FakeSummarizer(summary="核心要点：全场八折")
        svc = _make_service(fetcher, summarizer=summarizer)

        _, row_ids, item_ids, _ = _enqueue(tenant_id, [SHORT_URL])
        assert (await svc.claim_and_run(tenant_id))["executed"] is True
        assert _query_one(
            "SELECT status FROM bs_wechat_mp_sync_items WHERE id = %s",
            (item_ids[0],))["status"] == "success"

        # 总结指令收到 content_md 全文与标题（WP13：含 # 标题行）
        assert len(summarizer.calls) == 1
        assert BODY_V1 in summarizer.calls[0]["merged_text"]
        assert summarizer.calls[0]["merged_text"].startswith("# 春季活动")
        assert summarizer.calls[0]["title"] == "春季活动"

        article = _query_one(
            "SELECT doc_id, pipeline_version FROM bs_wechat_mp_articles WHERE id = %s",
            (row_ids[0],))
        assert article["pipeline_version"] == "p5"
        doc, doc_id = _doc_row(row_ids[0])

        assert doc["raw_text"] == BODY_V1  # raw_text 存 merged 全文（审计）
        assert doc["summary"] == "核心要点：全场八折"  # summary 列存总结
        assert doc["file_path"] == SHORT_URL  # 文档位置 = 原文链接
        metadata = json.loads(doc["metadata"])
        assert metadata["content_mode"] == "summary"
        assert "summary_fallback" not in metadata
        assert metadata["original_url"] == SHORT_URL  # 原文链接保留
        # WP13：metadata.content_md = 标题 + 文本段落；ingested_at 可解析 ISO
        assert metadata["content_md"] == f"# 春季活动\n\n{BODY_V1}"
        from datetime import datetime

        datetime.fromisoformat(metadata["ingested_at"])

        all_text = _chunk_join(doc_id)
        assert "核心要点：全场八折" in all_text
        assert "原文链接：" not in all_text  # 链接不再拼进正文（挤占/独占 chunk）
        assert SHORT_URL not in all_text
        assert "文档标题：" not in all_text  # 无标签噪音
        assert BODY_V1 not in all_text  # 向量打在总结上，不打包原文全文

    async def test_fallback_raw_content_on_summary_failure(self, tenant_id):
        """总结两次失败：回退 merged 原文入库（+原文链接），metadata 标记
        raw_fallback/summary_fallback，item 仍 success，无总结计费。"""
        _create_tenant(tenant_id)
        fetcher = StubFetcher()
        fetcher.set_page(SHORT_URL, ok_result(make_article_html("春季活动", BODY_V1)))

        class FailingGateway:
            def __init__(self):
                self.calls = 0

            async def chat_no_thinking(self, messages=None, **kwargs):
                self.calls += 1
                raise RuntimeError("summary down")

        summarizer = ArticleSummarizer(gateway=FailingGateway())
        svc = _make_service(fetcher, summarizer=summarizer)
        _, row_ids, item_ids, _ = _enqueue(tenant_id, [SHORT_URL])
        await svc.claim_and_run(tenant_id)

        assert summarizer._gateway.calls == 2  # 初次 + 重试 1 次
        assert _query_one(
            "SELECT status FROM bs_wechat_mp_sync_items WHERE id = %s",
            (item_ids[0],))["status"] == "success"  # 回退不失败

        doc, doc_id = _doc_row(row_ids[0])
        metadata = json.loads(doc["metadata"])
        assert metadata["content_mode"] == "raw_fallback"
        assert metadata["summary_fallback"] is True

        all_text = _chunk_join(doc_id)
        assert BODY_V1 in all_text  # 正文 = content_md（含原文文本，不丢数据）
        assert doc["file_path"] == SHORT_URL  # 文档位置 = 原文链接（回退路径同样写入）
        # WP13：回退正文 = content_md 本身（# 标题 + 文本段落），截断口径同前
        assert doc["summary"] == f"# 春季活动\n\n{BODY_V1}"[:SUMMARY_MAX_CHARS]
        # 总结失败无 usage → 零总结计费
        assert _query_one(
            "SELECT COUNT(*) AS c FROM chat_records WHERE tenant_id = %s "
            "AND user_message = '公众号文章总结'", (tenant_id,))["c"] == 0

    async def test_overlong_summary_truncated_in_doc(self, tenant_id):
        """模型输出 800 字 → 经真实 ArticleSummarizer 截断 → 入库 500 字
        （SUMMARY_MAX_CHARS）。"""
        _create_tenant(tenant_id)
        fetcher = StubFetcher()
        fetcher.set_page(SHORT_URL, ok_result(make_article_html("超长总结", BODY_V1)))
        gw = RecordingGateway(lambda i: {"content": "点" * 800, "usage": {}})
        summarizer = ArticleSummarizer(gateway=gw)
        svc = _make_service(fetcher, summarizer=summarizer)
        _, row_ids, _, _ = _enqueue(tenant_id, [SHORT_URL])
        await svc.claim_and_run(tenant_id)

        doc, doc_id = _doc_row(row_ids[0])
        assert doc["summary"] == "点" * 500
        all_text = _chunk_join(doc_id)
        assert ("点" * 501) not in all_text
        assert doc["file_path"] == SHORT_URL

    async def test_vision_prefixed_description_cleaned_before_ingest(self, tenant_id):
        """VL 成功描述带「图片识别结果如下：」前缀 → 入库内容（raw_text / 总结输入）
        无该前缀。走真实 VisionParser（替身网关返回带前缀产出，清洗在成功路径内）。"""
        _create_tenant(tenant_id)
        fetcher = StubFetcher()
        fetcher.set_page(SHORT_URL, ok_result(make_image_only_html("前缀清洗", 2)))
        # 替身网关：两张图都返回带标签前缀的模型产出（清洗前的真实形态）
        gw = RecordingGateway(
            lambda i: _ok_response("图片识别结果如下：春季促销全场八折")
        )
        vision = VisionParser(
            targets=[VisionTarget("qwen", "qwen3-vl-plus")],
            gateway_factory=lambda p, m: gw,
        )
        svc = _make_service(fetcher, vision=vision, downloader=_TinyPngDownloader())
        _, row_ids, item_ids, _ = _enqueue(tenant_id, [SHORT_URL])
        await svc.claim_and_run(tenant_id)

        assert _query_one(
            "SELECT status FROM bs_wechat_mp_sync_items WHERE id = %s",
            (item_ids[0],))["status"] == "success"
        doc, _ = _doc_row(row_ids[0])
        assert "[图片1: 春季促销全场八折]" in doc["raw_text"]
        assert "[图片2: 春季促销全场八折]" in doc["raw_text"]
        assert "图片识别结果如下" not in doc["raw_text"]
        # 总结输入（WP13 起 = content_md）同样干净，且含 Markdown 图片行
        merged = svc._summarizer.calls[0]["merged_text"]
        assert "图片识别结果如下" not in merged
        assert "春季促销全场八折" in merged
        assert "![" in merged and merged.startswith("# 前缀清洗")


# =============================== 计费 ===============================


class TestSummaryBilling:
    async def test_billing_called_with_source_and_model(self, tenant_id, monkeypatch):
        """总结成功后 record_background_llm_usage 以 source='wechat_mp_summary'+
        显式 model 被调用（monkeypatch 断言参数）。"""
        _create_tenant(tenant_id)
        fetcher = StubFetcher()
        fetcher.set_page(SHORT_URL, ok_result(make_article_html("春季活动", BODY_V1)))
        summarizer = FakeSummarizer(
            summary="总结",
            usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        )
        svc = _make_service(fetcher, summarizer=summarizer)

        captured = {}

        def fake_record(usage, **kwargs):
            captured["usage"] = usage
            captured.update(kwargs)

        monkeypatch.setattr(
            "src.services.session_record.record_background_llm_usage", fake_record
        )
        _, _, _, _ = _enqueue(tenant_id, [SHORT_URL])
        await svc.claim_and_run(tenant_id)

        assert captured["usage"] == {
            "prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
        assert captured["source"] == SUMMARY_SOURCE_TYPE == "wechat_mp_summary"
        assert captured["model"] == "summary-test-model"  # 显式模型名（计价依据）
        assert captured["tenant_id"] == tenant_id
        assert captured["user_message"] == "公众号文章总结"

    async def test_billing_persisted_without_session_record(self, tenant_id):
        """无 SessionRecord 的 worker 线程：真实走独立落账分支（chat_records 落行）。"""
        _create_tenant(tenant_id)
        fetcher = StubFetcher()
        fetcher.set_page(SHORT_URL, ok_result(make_article_html("春季活动", BODY_V1)))
        summarizer = FakeSummarizer(
            summary="总结正文",
            usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        )
        svc = _make_service(fetcher, summarizer=summarizer)
        _, _, _, _ = _enqueue(tenant_id, [SHORT_URL])
        await svc.claim_and_run(tenant_id)

        rec = _query_one(
            "SELECT * FROM chat_records WHERE tenant_id = %s AND user_message = %s",
            (tenant_id, "公众号文章总结"))
        assert rec is not None
        assert rec["source_type"] == "background_llm"  # 独立落账固定来源
        assert SUMMARY_SOURCE_TYPE in rec["session_id"]  # source 拼进 session_id 供追溯
        assert rec["prompt_tokens"] == 100 and rec["completion_tokens"] == 50
        assert rec["model"] == "summary-test-model"
        # 测试模型无单价配置 → 按 billing.py 兜底模型（deepseek-flash）计价
        # （commit 94f69cf7：修复总结/后台 LLM 计费落 0），不再落 0；此处只锁
        # 「不为 0」防回归，具体金额随价目表变动不断言
        assert float(rec["credit_cost"]) > 0


# =============================== 不变量 ===============================


class TestInvariants:
    async def test_hash_ignores_vl_and_summary_zero_cost_recheck(self, tenant_id):
        """不变量：content_hash 按原始节点（VL 描述/总结均不进指纹）；同文章二次
        claim 走 check：VL/总结 0 次调用、零新计费、余额不变。"""
        _create_tenant(tenant_id)
        html = make_image_only_html("指纹不变量", 2)
        fetcher = StubFetcher()
        fetcher.set_page(SHORT_URL, ok_result(html))
        vision = FakeVision(descriptions={1: "图一内容", 2: "图二内容"})
        summarizer = FakeSummarizer(summary="总结要点")
        svc = _make_service(fetcher, vision=vision, downloader=FakeDownloader(),
                            summarizer=summarizer)

        _, row_ids, _, _ = _enqueue(tenant_id, [SHORT_URL])
        await svc.claim_and_run(tenant_id)

        # 指纹 == 原始节点（文本 + 图片 src）计算值，VL 描述不参与
        extracted = extract_article(html)
        expected = WeChatMPSyncService._content_hash(extracted.title or "", extracted.nodes)
        article = _query_one(
            "SELECT content_hash FROM bs_wechat_mp_articles WHERE id = %s",
            (row_ids[0],))
        assert article["content_hash"] == expected

        records_before = _query_all(
            "SELECT record_id FROM chat_records WHERE tenant_id = %s", (tenant_id,))
        balance_before = _tenant_balance(tenant_id)

        _, _, item_ids, _ = _enqueue(tenant_id, [SHORT_URL], trigger="recheck",
                                     action="check")
        await svc.claim_and_run(tenant_id)

        item = _query_one(
            "SELECT action, status, billing_status FROM bs_wechat_mp_sync_items "
            "WHERE id = %s", (item_ids[0],))
        assert item["action"] == "check" and item["status"] == "success"
        assert item["billing_status"] == "not_required"
        # 快路径零成本：无新的 VL/总结调用、无新计费记录
        assert len(vision.calls) == 1
        assert len(summarizer.calls) == 1
        assert len(_query_all(
            "SELECT record_id FROM chat_records WHERE tenant_id = %s",
            (tenant_id,))) == len(records_before)
        assert _tenant_balance(tenant_id) == balance_before

    async def test_p2_row_rebuilt_as_summary_p5(self, tenant_id):
        """p2 存量行（hash 相同、pipeline 不匹配）复核后自动重建为当前 pipeline
        （p5）总结版。"""
        _create_tenant(tenant_id)
        html = make_article_html("存量p2", BODY_V1)
        fetcher = StubFetcher()
        fetcher.set_page(SHORT_URL, ok_result(html))
        extracted = extract_article(html)
        content_hash = WeChatMPSyncService._content_hash(
            extracted.title or "", extracted.nodes)
        identity = normalize_url(SHORT_URL)

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO bs_wechat_mp_articles
                    (tenant_id, external_id, original_url, fetch_url, source_channel,
                     status, processing_status, content_hash, pipeline_version)
                VALUES (%s, %s, %s, %s, 'callback', 'active', 'success', %s, 'p2')
                RETURNING id
                """,
                (tenant_id, identity.external_id, identity.original_url,
                 identity.fetch_url, content_hash),
            )
            row_id = cursor.fetchone()["id"]
            cursor.execute(
                "INSERT INTO bs_wechat_mp_sync_runs "
                "(tenant_id, trigger_type, status, total_count) "
                "VALUES (%s, 'recheck', 'queued', 1) RETURNING id",
                (tenant_id,),
            )
            run_id = cursor.fetchone()["id"]
            cursor.execute(
                "INSERT INTO bs_wechat_mp_sync_items "
                "(tenant_id, run_id, article_row_id, action, status) "
                "VALUES (%s, %s, %s, 'check', 'pending') RETURNING id",
                (tenant_id, run_id, row_id),
            )
            conn.commit()

        summarizer = FakeSummarizer(summary="p2 存量重建总结")
        svc = _make_service(fetcher, summarizer=summarizer)
        await svc.claim_and_run(tenant_id)

        article = _query_one(
            "SELECT processing_status, pipeline_version, doc_id FROM "
            "bs_wechat_mp_articles WHERE id = %s", (row_id,))
        assert article["processing_status"] == "success"
        assert article["pipeline_version"] == "p5"
        all_text = _chunk_join(article["doc_id"])
        assert "p2 存量重建总结" in all_text
        assert "原文链接：" not in all_text
        assert "文档标题：" not in all_text
