"""wechat_mp WP13 图文 Markdown 化入库单元测试（真实 PG，对齐既有替身模式）。

覆盖（计划 WP13 节）：

- build_markdown 组装：图文交错保序、alt=VL 描述、无描述行 ![图片N](url)、
  纯图文章、无图文章、换行描述压缩 + 200 字截断、组装永不抛异常
- 门禁放开：文字充足有图触发 VL（基础场景已改写进 test_wp10
  test_text_rich_article_parses_images_gate_open）；WP13-r2 移除单篇 30 张产品
  上限——37 图全量解析（原超限 7 张无描述行语义取消），单篇仅保留 200 张
  防失控硬护栏（skipped 只会来自该护栏）
- 无多模态模型：有文本有图 → 入库成功不再 deferred、metadata 记
  image_parse_skipped_reason='no_model'、图片行无描述、不下载不解析；
  纯图无文本无模型 → 仍 deferred
- 总结：输入 = content_md 全文（含 # 标题与 Markdown 图片行），含图片关键
  信息的总结入库
- metadata schema：content_md / ingested_at（可解析 ISO）/ image_parsed_count
  / image_failed_count（VL 失败 + 下载失败）/ image_skipped_count（超上限）
- 计费：按每张实际 token 走标准算价——无价目模型走 deepseek-v4-flash 兜底、
  model = 实际模型、usage_breakdown.billing_mode='token'、失败张不计费
- p5：存量 p3 行复核重建（content_md 生成、图片补解析）；hash 不变量：
  二次 claim VL/总结 0 调用零计费

抓取走 StubFetcher、总结走 FakeSummarizer、VL 走 FakeVision、下载走
FakeDownloader/PartialDownloader（照 test_service/test_wp10 范式）。
每用例独立随机租户，测后清理 DB 行与 storage 目录。
"""

import json
import os
import shutil
from datetime import datetime

import pytest

from src.services.billing import calculate_credit_cost
from src.wechat_mp.content import (
    MAX_IMAGE_ALT_CHARS,
    ContentNode,
    build_markdown,
)
from src.wechat_mp.image_downloader import (
    MAX_IMAGES_PER_ARTICLE,
    DownloadOutcome,
    ImageDownload,
    ImageFailure,
)
from src.wechat_mp.vision import (
    VISION_PARSE_SOURCE_TYPE,
    ImageParseFailure,
    ImageParseSuccess,
    VisionParseOutcome,
)

from .test_service import (  # noqa: F401  复用开发测试的替身与 DB 辅助
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
    make_image_only_html,
    ok_result,
)
from .test_wp10_image_vision import FakeDownloader, FakeVision

# ------------------------------- 测试替身 -------------------------------


class PartialDownloader:
    """按序号失败指定图片的下载替身（其余照 FakeDownloader 成功路径）。"""

    def __init__(self, fail_n=()):
        self.fail_n = set(fail_n)
        self.calls = []

    def download(self, tenant_id, article_row_id, srcs):
        self.calls.append([n for n, _ in srcs])
        images = [
            ImageDownload(
                n=n,
                local_path=(
                    f"storage/tenants/{tenant_id}/knowledge/wechat_mp/"
                    f"{article_row_id}/img_{n}.jpg"
                ),
                ext="jpg", bytes_written=100,
            )
            for n, _ in srcs if n not in self.fail_n
        ]
        failures = [
            ImageFailure(n=n, reason="timeout")
            for n, _ in srcs if n in self.fail_n
        ]
        return DownloadOutcome(images=images, failures=failures)


class LimitDownloader(FakeDownloader):
    """模拟单篇 200 张防失控硬护栏的下载替身（超限部分计入 skipped_over_limit，
    WP13-r2：护栏非产品限制，正常文章不再触达）。"""

    def download(self, tenant_id, article_row_id, srcs):
        skipped = max(len(srcs) - MAX_IMAGES_PER_ARTICLE, 0)
        outcome = super().download(tenant_id, article_row_id,
                                   srcs[:MAX_IMAGES_PER_ARTICLE])
        outcome.skipped_over_limit = skipped
        return outcome


class UsageVision(FakeVision):
    """按张返回差异化 token 用量的 VL 替身（重图 vs 轻图计费对比）。"""

    usage_by_n = {
        1: {"prompt_tokens": 2000, "completion_tokens": 500, "total_tokens": 2500},
        2: {"prompt_tokens": 200, "completion_tokens": 50, "total_tokens": 250},
    }

    async def describe_images(self, images, tenant_id):
        outcome = VisionParseOutcome()
        for n, path in images:
            outcome.successes.append(ImageParseSuccess(
                n=n, description=f"第{n}张图的内容转述",
                model="vl-test-model", provider="fake",
                usage=dict(self.usage_by_n.get(n, self.usage_by_n[2])),
            ))
        return outcome


# ------------------------------- 夹具构造 -------------------------------


def interleaved_html(title: str, blocks) -> str:
    """图文交错文章页：blocks = [('text', 文本) | ('img', src), ...] 保序。"""
    parts = []
    for kind, val in blocks:
        if kind == "text":
            parts.append(f"<p>{val}</p>")
        else:
            parts.append(f'<img data-src="{val}">')
    return (
        "<html><head>"
        f"<script>var msg_title = '{title}'.html(false);</script>"
        "</head><body>"
        f'<h1 id="activity-name">{title}</h1>'
        f'<div id="js_content">{"".join(parts)}</div>'
        "</body></html>"
    )


@pytest.fixture(autouse=True)
def _cleanup_storage(tenant_id):
    """测后清理该租户的图片转存目录（conftest 只清 DB 行）。"""
    yield
    shutil.rmtree(os.path.join("storage", "tenants", tenant_id), ignore_errors=True)


def _doc_row(article_row_id: int):
    doc_id = _query_one(
        "SELECT doc_id FROM bs_wechat_mp_articles WHERE id = %s", (article_row_id,)
    )["doc_id"]
    return _query_one(
        "SELECT raw_text, summary, metadata, file_path FROM documents WHERE id = %s",
        (doc_id,)
    ), doc_id


# =============================== build_markdown 纯函数 ===============================


class TestBuildMarkdown:
    def test_interleaved_order_alts_and_placeholders(self):
        """图文交错保序：文本段落原样、alt=VL 描述、无描述行用「图片N」。"""
        md = build_markdown(
            "春季活动",
            [
                ContentNode(type="text", text="第一段文字"),
                ContentNode(type="image", src="https://mmbiz.qpic.cn/a.jpg"),
                ContentNode(type="text", text="第二段文字"),
                ContentNode(type="image", src="https://mmbiz.qpic.cn/b.jpg"),
            ],
            image_descriptions={1: "门店招牌照片"},
        )
        assert md == (
            "# 春季活动\n\n"
            "第一段文字\n\n"
            "![门店招牌照片](https://mmbiz.qpic.cn/a.jpg)\n\n"
            "第二段文字\n\n"
            "![图片2](https://mmbiz.qpic.cn/b.jpg)"
        )

    def test_image_only_article(self):
        """纯图文章：全部图片行（成功描述 + 失败占位）独立成行。"""
        md = build_markdown(
            "活动长图",
            [ContentNode(type="image", src=f"https://mmbiz.qpic.cn/{i}.jpg")
             for i in (1, 2, 3)],
            image_descriptions={2: "第二张描述"},
        )
        lines = md.split("\n\n")
        assert lines == [
            "# 活动长图",
            "![图片1](https://mmbiz.qpic.cn/1.jpg)",
            "![第二张描述](https://mmbiz.qpic.cn/2.jpg)",
            "![图片3](https://mmbiz.qpic.cn/3.jpg)",
        ]

    def test_text_only_article(self):
        """无图文章同样生成：标题 + 文本段落，无图片行。"""
        md = build_markdown(
            "纯文本文章",
            [ContentNode(type="text", text="段落一"), ContentNode(type="text", text="段落二")],
        )
        assert md == "# 纯文本文章\n\n段落一\n\n段落二"

    def test_alt_multiline_compressed_and_truncated(self):
        """描述内含换行压成单行；超长截断到 MAX_IMAGE_ALT_CHARS。"""
        desc = "第一行\n第二行  多空格" + "长" * 300
        md = build_markdown(
            "标题",
            [ContentNode(type="image", src="https://mmbiz.qpic.cn/a.jpg")],
            image_descriptions={1: desc},
        )
        alt = md.split("![", 1)[1].split("](", 1)[0]
        assert "\n" not in alt and "  " not in alt
        assert len(alt) == MAX_IMAGE_ALT_CHARS == 200

    def test_never_raises_on_bad_inputs(self):
        """描述为 None/非字符串、图片节点缺 src：组装不抛异常（永不阻断入库）。"""
        md = build_markdown(
            "标题",
            [
                ContentNode(type="image", src=None),  # 不占编号不产出
                ContentNode(type="image", src="https://mmbiz.qpic.cn/a.jpg"),
                ContentNode(type="text", text=None),
                ContentNode(type="image", src="https://mmbiz.qpic.cn/b.jpg"),
            ],
            image_descriptions={1: None, 2: 123},
        )
        assert "![图片1](https://mmbiz.qpic.cn/a.jpg)" in md
        assert "![123](https://mmbiz.qpic.cn/b.jpg)" in md

    def test_empty_title_still_renders(self):
        """无标题文章：无标题行，正文段落照常。"""
        md = build_markdown(None, [ContentNode(type="text", text="内容")])
        assert md == "内容"


# =============================== 管道：门禁放开与超限 ===============================


class TestGateOpenPipeline:
    async def test_interleaved_content_md_order_and_summary_input(self, tenant_id):
        """图文交错文章端到端：content_md 按节点顺序忠实组装，总结输入 = content_md
        全文（含 # 标题与 Markdown 图片行）。"""
        _create_tenant(tenant_id)
        src_a = "https://mmbiz.qpic.cn/wp13/inter_a.jpg"
        src_b = "https://mmbiz.qpic.cn/wp13/inter_b.jpg"
        fetcher = StubFetcher()
        fetcher.set_page(SHORT_URL, ok_result(interleaved_html("图文交错", [
            ("text", "第一段文字"),
            ("img", src_a),
            ("text", "第二段文字"),
            ("img", src_b),
        ])))
        vision = FakeVision(descriptions={1: "第一张的描述", 2: "第二张的描述"})
        summarizer = FakeSummarizer(summary="图文要点总结")
        svc = _make_service(fetcher, vision=vision, downloader=FakeDownloader(),
                            summarizer=summarizer)

        _, row_ids, item_ids, _ = _enqueue(tenant_id, [SHORT_URL])
        assert (await svc.claim_and_run(tenant_id))["executed"] is True
        assert _query_one(
            "SELECT status FROM bs_wechat_mp_sync_items WHERE id = %s",
            (item_ids[0],))["status"] == "success"

        expected_md = (
            "# 图文交错\n\n"
            "第一段文字\n\n"
            "![第一张的描述](https://mmbiz.qpic.cn/wp13/inter_a.jpg)\n\n"
            "第二段文字\n\n"
            "![第二张的描述](https://mmbiz.qpic.cn/wp13/inter_b.jpg)"
        )
        # 总结替身收到的 merged 即 content_md 全文（WP13：图片行进总结输入）
        assert summarizer.calls[0]["merged_text"] == expected_md
        assert summarizer.calls[0]["title"] == "图文交错"

        doc, _ = _doc_row(row_ids[0])
        metadata = json.loads(doc["metadata"])
        assert metadata["content_md"] == expected_md
        # raw_text 维持 merged 纯文本（审计兼容）
        assert doc["raw_text"] == (
            "第一段文字\n[图片1: 第一张的描述]\n第二段文字\n[图片2: 第二张的描述]"
        )

    async def test_37_images_all_parsed_without_product_limit(self, tenant_id):
        """WP13-r2 移除单篇 30 张产品上限：37 图全量下载解析、全部带描述，
        image_skipped_count=0（skipped 只会来自 200 防失控护栏）；
        计数同时回写文章列。"""
        _create_tenant(tenant_id)
        fetcher = StubFetcher()
        fetcher.set_page(SHORT_URL, ok_result(make_image_only_html("长图集文章", 37)))
        svc = _make_service(fetcher, vision=FakeVision(), downloader=LimitDownloader())

        _, row_ids, _, _ = _enqueue(tenant_id, [SHORT_URL])
        assert (await svc.claim_and_run(tenant_id))["executed"] is True

        doc, _ = _doc_row(row_ids[0])
        metadata = json.loads(doc["metadata"])
        assert metadata["image_parsed_count"] == 37
        assert metadata["image_failed_count"] == 0
        assert metadata["image_skipped_count"] == 0
        # WP13-r2：解析计数成功路径回写文章列
        assert _query_one(
            "SELECT image_parsed_count FROM bs_wechat_mp_articles WHERE id = %s",
            (row_ids[0],))["image_parsed_count"] == 37

        blocks = metadata["content_md"].split("\n\n")
        assert len(blocks) == 1 + 37  # 标题 + 37 个图片行
        assert blocks[1] == "![第1张图的内容转述](https://mmecoa.qpic.cn/wp5test/img_1.jpg)"
        # 原 30 张上限两侧的图片行现在均带描述（超限无描述语义已移除）
        assert blocks[30] == (
            "![第30张图的内容转述](https://mmecoa.qpic.cn/wp5test/img_30.jpg)"
        )
        assert blocks[31] == (
            "![第31张图的内容转述](https://mmecoa.qpic.cn/wp5test/img_31.jpg)"
        )
        assert blocks[37] == (
            "![第37张图的内容转述](https://mmecoa.qpic.cn/wp5test/img_37.jpg)"
        )


# =============================== 管道：无多模态模型 ===============================


class TestNoModelBehavior:
    async def test_mixed_article_no_model_ingests_without_deferred(self, tenant_id):
        """有文本有图 + 无模型：入库成功（不再 deferred），图片行保留地址无描述，
        metadata 记 image_parse_skipped_reason='no_model'，不下载不解析。"""
        _create_tenant(tenant_id)
        fetcher = StubFetcher()
        fetcher.set_page(SHORT_URL, ok_result(interleaved_html("无模型图文", [
            ("text", "正文文字足够长，可以独立成篇总结。"),
            ("img", "https://mmbiz.qpic.cn/wp13/nm_1.jpg"),
            ("img", "https://mmbiz.qpic.cn/wp13/nm_2.jpg"),
        ])))
        vision = FakeVision(no_model=True)
        downloader = FakeDownloader()
        svc = _make_service(fetcher, vision=vision, downloader=downloader)

        _, row_ids, item_ids, _ = _enqueue(tenant_id, [SHORT_URL])
        assert (await svc.claim_and_run(tenant_id))["executed"] is True

        item = _query_one(
            "SELECT status, error_code FROM bs_wechat_mp_sync_items WHERE id = %s",
            (item_ids[0],))
        assert item["status"] == "success"  # 不再 deferred
        assert downloader.calls == [] and vision.calls == []

        doc, _ = _doc_row(row_ids[0])
        metadata = json.loads(doc["metadata"])
        assert metadata["image_parse_skipped_reason"] == "no_model"
        assert metadata["image_parsed_count"] == 0
        assert metadata["image_failed_count"] == 0
        assert metadata["image_skipped_count"] == 0
        assert "image_local_paths" not in metadata
        assert "![图片1](https://mmbiz.qpic.cn/wp13/nm_1.jpg)" in metadata["content_md"]
        assert "![图片2](https://mmbiz.qpic.cn/wp13/nm_2.jpg)" in metadata["content_md"]
        # 总结输入（content_md）同样无描述
        assert "![图片1]" in svc._summarizer.calls[0]["merged_text"]

    async def test_image_only_no_model_still_deferred(self, tenant_id):
        """纯图无文本 + 无模型：无任何可总结内容 → 维持 deferred。"""
        _create_tenant(tenant_id)
        fetcher = StubFetcher()
        fetcher.set_page(SHORT_URL, ok_result(make_image_only_html("纯图无模型", 2)))
        svc = _make_service(fetcher, vision=FakeVision(no_model=True),
                            downloader=FakeDownloader())

        _, row_ids, item_ids, _ = _enqueue(tenant_id, [SHORT_URL])
        await svc.claim_and_run(tenant_id)

        item = _query_one(
            "SELECT status, error_code FROM bs_wechat_mp_sync_items WHERE id = %s",
            (item_ids[0],))
        assert item["status"] == "deferred"
        assert item["error_code"] == "deferred_image_pending"
        article = _query_one(
            "SELECT processing_status, doc_id FROM bs_wechat_mp_articles WHERE id = %s",
            (row_ids[0],))
        assert article["processing_status"] == "deferred"
        assert article["doc_id"] is None


# =============================== metadata schema ===============================


class TestMetadataSchema:
    async def test_counts_iso_ingested_at_and_failed_breakdown(self, tenant_id):
        """计数口径：parsed=VL 成功 / failed=VL 失败 + 下载失败 / skipped=超上限；
        ingested_at 为可解析 ISO；部分失败时 content_md 失败张无描述。"""
        _create_tenant(tenant_id)
        fetcher = StubFetcher()
        fetcher.set_page(SHORT_URL, ok_result(make_image_only_html("计数文章", 3)))
        # 3 张：第 1 张成功、第 2 张 VL 失败、第 3 张下载失败
        svc = _make_service(fetcher, vision=FakeVision(fail_n={2}),
                            downloader=PartialDownloader(fail_n={3}))

        _, row_ids, _, _ = _enqueue(tenant_id, [SHORT_URL])
        assert (await svc.claim_and_run(tenant_id))["executed"] is True

        doc, _ = _doc_row(row_ids[0])
        metadata = json.loads(doc["metadata"])
        assert metadata["image_parsed_count"] == 1
        assert metadata["image_failed_count"] == 2  # 1 VL 失败 + 1 下载失败
        assert metadata["image_skipped_count"] == 0
        # ingested_at 可解析为 ISO 时间（datetime.now(timezone.utc) 口径）
        parsed_at = datetime.fromisoformat(metadata["ingested_at"])
        assert parsed_at.tzinfo is not None
        # content_md：成功张带描述，失败/下载失败张 alt 用「图片N」
        assert "![第1张图的内容转述](https://mmecoa.qpic.cn/wp5test/img_1.jpg)" \
            in metadata["content_md"]
        assert "![图片2](https://mmecoa.qpic.cn/wp5test/img_2.jpg)" in metadata["content_md"]
        assert "![图片3](https://mmecoa.qpic.cn/wp5test/img_3.jpg)" in metadata["content_md"]
        # raw_text 审计口径不变（描述插回 + 失败占位）
        assert "[图片1: 第1张图的内容转述]" in doc["raw_text"]
        assert "[图片2]" in doc["raw_text"] and "[图片3]" in doc["raw_text"]


# =============================== 计费：按实际 token ===============================


class TestTokenBilling:
    async def test_no_price_model_uses_fallback_per_token(self, tenant_id):
        """无价目模型走 deepseek-v4-flash 兜底：credit = calculate_credit_cost
        （标准算价路径）；model 记实际模型；billing_mode='token'。"""
        _create_tenant(tenant_id)
        fetcher = StubFetcher()
        fetcher.set_page(SHORT_URL, ok_result(make_image_only_html("兜底计费", 2)))
        svc = _make_service(fetcher, vision=UsageVision(), downloader=FakeDownloader())

        _, row_ids, _, _ = _enqueue(tenant_id, [SHORT_URL])
        assert (await svc.claim_and_run(tenant_id))["executed"] is True

        records = _query_all(
            "SELECT * FROM chat_records WHERE tenant_id = %s AND source_type = %s "
            "ORDER BY prompt_tokens DESC", (tenant_id, VISION_PARSE_SOURCE_TYPE))
        assert len(records) == 2
        credits = []
        for rec in records:
            assert rec["model"] == "vl-test-model"  # 实际使用的模型
            expected = calculate_credit_cost(
                rec["prompt_tokens"], rec["completion_tokens"], model="vl-test-model")
            # vl-test-model 无价目行 → 兜底模型价格，credit > 0（不落 0）
            assert expected > 0
            assert float(rec["credit_cost"]) == expected
            breakdown = rec["usage_breakdown"]
            assert breakdown["billing_mode"] == "token"
            assert breakdown["model"] == "vl-test-model"
            assert breakdown["vl_usage"]["prompt_tokens"] == rec["prompt_tokens"]
            credits.append(float(rec["credit_cost"]))
        # 重图（tokens 多）计费高于轻图
        assert credits[0] > credits[1]

    async def test_failed_images_not_billed(self, tenant_id):
        """失败张不计费：3 张中仅 1 张成功 → 仅 1 条图片计费记录。"""
        _create_tenant(tenant_id)
        fetcher = StubFetcher()
        fetcher.set_page(SHORT_URL, ok_result(make_image_only_html("失败不计费", 3)))
        svc = _make_service(fetcher, vision=FakeVision(fail_n={2, 3}),
                            downloader=PartialDownloader())

        _, _, _, _ = _enqueue(tenant_id, [SHORT_URL])
        await svc.claim_and_run(tenant_id)

        records = _query_all(
            "SELECT credit_cost FROM chat_records WHERE tenant_id = %s "
            "AND source_type = %s", (tenant_id, VISION_PARSE_SOURCE_TYPE))
        assert len(records) == 1
        assert float(records[0]["credit_cost"]) > 0


# =============================== p5 重建与不变量 ===============================


class TestRebuildP5:
    async def test_p3_row_rebuilt_with_content_md_and_reparse(self, tenant_id):
        """存量 p3 成功行（hash 相同、pipeline 不匹配）复核后重建为当前版本
        （p5）：content_md 生成、图片补解析（VL 再次调用并计费）。"""
        _create_tenant(tenant_id)
        html = make_image_only_html("存量p3", 2)
        fetcher = StubFetcher()
        fetcher.set_page(SHORT_URL, ok_result(html))
        extracted_hash = _content_hash_of(html)
        _insert_p3_row(tenant_id, extracted_hash)

        vision = FakeVision()
        svc = _make_service(fetcher, vision=vision, downloader=FakeDownloader())
        _, row_ids, _, _ = _enqueue(tenant_id, [SHORT_URL], trigger="recheck",
                                    action="check")
        await svc.claim_and_run(tenant_id)

        article = _query_one(
            "SELECT processing_status, pipeline_version, doc_id FROM "
            "bs_wechat_mp_articles WHERE id = %s", (row_ids[0],))
        assert article["processing_status"] == "success"
        assert article["pipeline_version"] == "p5"
        assert len(vision.calls) == 1  # 图片补解析
        doc, _ = _doc_row(row_ids[0])
        metadata = json.loads(doc["metadata"])
        assert metadata["image_parsed_count"] == 2
        assert "![第1张图的内容转述](https://mmecoa.qpic.cn/wp5test/img_1.jpg)" \
            in metadata["content_md"]
        # 补解析产生按 token 计费记录
        assert _query_one(
            "SELECT COUNT(*) AS c FROM chat_records WHERE tenant_id = %s "
            "AND source_type = %s", (tenant_id, VISION_PARSE_SOURCE_TYPE))["c"] == 2

    async def test_hash_invariant_zero_cost_recheck(self, tenant_id):
        """hash 不变量：VL/总结/md 均不进指纹；二次 claim（hash 未变 + 当前
        pipeline 匹配）
        VL/总结 0 调用、零新计费、余额不变。"""
        _create_tenant(tenant_id)
        fetcher = StubFetcher()
        fetcher.set_page(SHORT_URL, ok_result(make_image_only_html("指纹不变量", 2)))
        vision = FakeVision()
        summarizer = FakeSummarizer(summary="总结要点")
        svc = _make_service(fetcher, vision=vision, downloader=FakeDownloader(),
                            summarizer=summarizer)

        _enqueue(tenant_id, [SHORT_URL])
        await svc.claim_and_run(tenant_id)
        assert len(vision.calls) == 1 and len(summarizer.calls) == 1
        records_before = _query_all(
            "SELECT record_id FROM chat_records WHERE tenant_id = %s", (tenant_id,))
        balance_before = _tenant_balance(tenant_id)

        _enqueue(tenant_id, [SHORT_URL], trigger="recheck", action="check")
        await svc.claim_and_run(tenant_id)

        assert len(vision.calls) == 1  # 零 VL 调用
        assert len(summarizer.calls) == 1  # 零总结调用
        assert len(_query_all(
            "SELECT record_id FROM chat_records WHERE tenant_id = %s",
            (tenant_id,))) == len(records_before)
        assert _tenant_balance(tenant_id) == balance_before


# ------------------------------- p3 存量行构造辅助 -------------------------------


def _content_hash_of(html: str) -> str:
    from src.wechat_mp.content import extract_article
    from src.wechat_mp.service import WeChatMPSyncService

    extracted = extract_article(html)
    return WeChatMPSyncService._content_hash(extracted.title or "", extracted.nodes)


def _insert_p3_row(tenant_id: str, content_hash: str) -> int:
    """手工构造 p3 时代成功行（无 doc_id，触发重建插入路径），返回行 ID。"""
    from src.db.database import get_db_connection
    from src.wechat_mp.identity import normalize_url

    identity = normalize_url(SHORT_URL)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO bs_wechat_mp_articles
                (tenant_id, external_id, original_url, fetch_url, source_channel,
                 status, processing_status, content_hash, pipeline_version, image_count)
            VALUES (%s, %s, %s, %s, 'callback', 'active', 'success', %s, 'p3', 2)
            RETURNING id
            """,
            (tenant_id, identity.external_id, identity.original_url,
             identity.fetch_url, content_hash),
        )
        row_id = cursor.fetchone()["id"]
        conn.commit()
    return row_id
