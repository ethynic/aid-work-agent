"""wechat_mp WP10 P2 图片 VL 解析单元测试（真实 PG，require_db 门禁）。

覆盖（计划 WP10 节 + 设计 §6.1/§6.2/§11/§13）：

- 下载（image_downloader.py，httpx MockTransport 不触网）：
  域名白名单（qpic.cn 精确后缀命中 / evil-qpic.cn 拒 / IP 字面量拒 / 非 HTTPS 拒 /
  DNS 非公网拒）、重定向逐跳校验（跳内网拒、跳其他 qpic 子域放行）、
  10MB 字节上限（Content-Length 与流式累计两路）、解码像素上限与长边缩小、
  每文章 30 张上限、幂等复用（同 src 零下载 + src 变更不复用旧图）
- VL 解析（vision.py）：目标选择（多模态清单 × provider 配置交集、无模型信号）、
  指令构造（单轮 user message、无系统提示/会话历史/工具 schema）、
  Semaphore(3) 并发上限、单张失败重试 1 次、「图片无法识别」计 failed、
  超限图片压缩（data URL 重编码 JPEG）
- 管道（service.py）：纯图文章 + FakeVL → 入库成功且正文含 [图片N: 描述]、检索
  可见；FakeVL 全失败 → deferred；部分成功 → 失败保留 [图片N] 占位；文字充足有图
  不触发 VL；deferred 存量文章在 p2 重建可达；转存路径落 metadata；VL 无余额
  走 no_credit
- 按张计费：1 积分/张金额精确、usage_breakdown 对账字段、unknown 不重扣
  （含「embedding 成功 + 图片 unknown」合并保守语义）

抓取走 StubFetcher（照 test_service 范式）；下载走 FakeDownloader；VL 走
FakeVision/FakeGateway。每用例独立随机租户，测后清理 DB 行与 storage 目录。
"""

import asyncio
import importlib.util
import json
import os
import shutil
import threading
from io import BytesIO
from pathlib import Path

import httpx
import pytest

from src.config.settings import settings
from src.wechat_mp import service as svc_mod
from src.wechat_mp.content import extract_article
from src.wechat_mp.fetcher import STATUS_OK, FetchResult
from src.wechat_mp.identity import normalize_url
from src.wechat_mp.image_downloader import (
    MAX_IMAGES_PER_ARTICLE,
    DownloadOutcome,
    ImageDownload,
    ImageFailure,
    MPImageDownloader,
    validate_image_url,
)
from src.wechat_mp.service import (
    PIPELINE_VERSION,
    WeChatMPSyncService,
    _IMAGE_PLACEHOLDER_RE,
)
from src.wechat_mp.vision import (
    MAX_IMAGE_DATA_BYTES,
    PARSE_INSTRUCTION,
    UNRECOGNIZED_TEXT,
    VISION_PARSE_SOURCE_TYPE,
    ImageParseFailure,
    ImageParseSuccess,
    VisionParseOutcome,
    VisionParser,
    VisionTarget,
    build_image_data_url,
    calculate_image_parse_credit_cost,
    resolve_vision_targets,
)

from .conftest import cleanup_tenant
from .test_service import FakeSummarizer

# ------------------------------- 测试替身 -------------------------------


class FakeRedis:
    """进程内 Redis 替身（照 test_service 范式）。"""

    def __init__(self, available: bool = True):
        self._available = available
        self._store = {}
        self._lock = threading.Lock()

    def is_available(self) -> bool:
        return self._available

    @staticmethod
    def make_key(prefix: str, identifier: str = "") -> str:
        return f"{prefix}:{identifier}" if identifier else prefix

    def acquire_lock(self, key: str, value: str, ex: int = 60) -> bool:
        with self._lock:
            if key in self._store:
                return False
            self._store[key] = value
            return True

    def release_lock(self, key: str, value: str) -> bool:
        with self._lock:
            if self._store.get(key) == value:
                del self._store[key]
                return True
            return False

    def renew_lock(self, key: str, value: str, ex: int) -> bool:
        with self._lock:
            return self._store.get(key) == value

    def get(self, key: str):
        raw = self._store.get(key)
        return None if raw is None else json.loads(raw)


class FakeEmbeddingClient:
    """embedding 替身：恒定单位向量 + 确定性 token 计数（照 test_service 范式）。"""

    model = "text-embedding-v3"

    def __init__(self):
        self.last_usage_tokens = 0
        self.embed_batch_calls = 0

    def reset_usage(self) -> None:
        self.last_usage_tokens = 0

    @staticmethod
    def _vec():
        return [1.0] + [0.0] * 1023

    async def embed_batch(self, texts, batch_size: int = 10):
        self.embed_batch_calls += 1
        self.last_usage_tokens += sum(max(1, len(t) // 4) for t in texts)
        return [self._vec() for _ in texts]

    async def embed(self, text: str):
        self.last_usage_tokens += max(1, len(text) // 4)
        return self._vec()


class StubFetcher:
    """抓取替身：fetch_url → FetchResult，不触网。"""

    def __init__(self):
        self.pages = {}
        self.calls = []

    def set_page(self, raw_url: str, result) -> None:
        self.pages[normalize_url(raw_url).fetch_url] = result

    def fetch(self, url: str) -> FetchResult:
        self.calls.append(url)
        return self.pages[url]


class FakeDownloader:
    """下载替身：返回可控行为的 DownloadOutcome，记录调用。"""

    def __init__(self, fail_all: bool = False):
        self.fail_all = fail_all
        self.calls = []

    def download(self, tenant_id, article_row_id, srcs):
        self.calls.append({"tenant_id": tenant_id, "article_id": article_row_id,
                           "srcs": list(srcs)})
        if self.fail_all:
            return DownloadOutcome(
                failures=[ImageFailure(n=n, reason="timeout") for n, _ in srcs]
            )
        return DownloadOutcome(
            images=[
                ImageDownload(
                    n=n,
                    local_path=(
                        f"storage/tenants/{tenant_id}/knowledge/wechat_mp/"
                        f"{article_row_id}/img_{n}.jpg"
                    ),
                    ext="jpg", bytes_written=100,
                )
                for n, _ in srcs
            ]
        )


class FakeVision:
    """VL 解析替身：可用性/全失败/部分失败可控行为，记录调用。"""

    def __init__(self, descriptions=None, no_model=False, fail_n=()):
        self.descriptions = descriptions or {}
        self.no_model = no_model
        self.fail_n = set(fail_n)
        self.calls = []

    def available(self) -> bool:
        return not self.no_model

    async def describe_images(self, images, tenant_id):
        self.calls.append({"images": list(images), "tenant_id": tenant_id})
        outcome = VisionParseOutcome()
        if self.no_model:
            outcome.no_model = True
            return outcome
        for n, path in images:
            if n in self.fail_n:
                outcome.failures.append(ImageParseFailure(n=n, reason="error"))
            else:
                outcome.successes.append(
                    ImageParseSuccess(
                        n=n,
                        description=self.descriptions.get(n, f"第{n}张图的内容转述"),
                        model="vl-test-model",
                        provider="fake",
                        usage={"prompt_tokens": 10, "completion_tokens": 5,
                               "total_tokens": 15},
                    )
                )
        return outcome


# ------------------------------- DB 辅助（照 test_service 范式） -------------------------------


def _load_real_vector_db():
    file_path = (
        Path(__file__).resolve().parents[3]
        / "src" / "knowledge" / "vector_db" / "vector_db.py"
    )
    spec = importlib.util.spec_from_file_location(
        "_real_vector_db_for_wp10_test", str(file_path)
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _real_vector_db_patch(monkeypatch):
    """service 内 get_vector_db 替换为真实 pgvector 实现（根 conftest 的是 stub）。"""
    module = _load_real_vector_db()
    monkeypatch.setattr(svc_mod, "get_vector_db", module.get_vector_db)


@pytest.fixture(autouse=True)
def _cleanup_storage(tenant_id):
    """测后清理该租户的图片转存目录（conftest 只清 DB 行）。"""
    yield
    shutil.rmtree(
        os.path.join("storage", "tenants", tenant_id), ignore_errors=True
    )


def _create_tenant(tenant_id: str, balance: float = 100.0) -> None:
    from src.core.cache_utils import invalidate_tenant_cache
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO tenants (tenant_id, company_name, credit_balance)
            VALUES (%s, %s, %s)
            ON CONFLICT (tenant_id)
            DO UPDATE SET credit_balance = EXCLUDED.credit_balance
            """,
            (tenant_id, "WP10测试", balance),
        )
        conn.commit()
    invalidate_tenant_cache(tenant_id)


def _enqueue(tenant_id: str, urls, trigger: str = "callback", action: str = "new"):
    """受理入队（articles upsert + queued run + pending items）。"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        row_ids = []
        for url in urls:
            identity = normalize_url(url)
            cursor.execute(
                """
                INSERT INTO bs_wechat_mp_articles
                    (tenant_id, external_id, original_url, fetch_url,
                     source_channel, status, processing_status)
                VALUES (%s, %s, %s, %s, 'callback', 'active', 'pending')
                ON CONFLICT (tenant_id, external_id) DO NOTHING
                RETURNING id
                """,
                (tenant_id, identity.external_id, identity.original_url,
                 identity.fetch_url),
            )
            row = cursor.fetchone()
            if row:
                row_ids.append(row["id"])
            else:
                cursor.execute(
                    "SELECT id FROM bs_wechat_mp_articles "
                    "WHERE tenant_id = %s AND external_id = %s",
                    (tenant_id, identity.external_id),
                )
                row_ids.append(cursor.fetchone()["id"])
        cursor.execute(
            """
            INSERT INTO bs_wechat_mp_sync_runs (tenant_id, trigger_type, status, total_count)
            VALUES (%s, %s, 'queued', %s)
            RETURNING id
            """,
            (tenant_id, trigger, len(row_ids)),
        )
        run_id = cursor.fetchone()["id"]
        item_ids = []
        for row_id in row_ids:
            cursor.execute(
                """
                INSERT INTO bs_wechat_mp_sync_items
                    (tenant_id, run_id, article_row_id, action, status)
                VALUES (%s, %s, %s, %s, 'pending')
                RETURNING id
                """,
                (tenant_id, run_id, row_id, action),
            )
            item_ids.append(cursor.fetchone()["id"])
        conn.commit()
    return run_id, row_ids, item_ids


def _query_one(sql, params):
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        return cursor.fetchone()


def _query_all(sql, params):
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        return cursor.fetchall()


def _tenant_balance(tenant_id: str) -> float:
    return float(_query_one(
        "SELECT credit_balance FROM tenants WHERE tenant_id = %s", (tenant_id,)
    )["credit_balance"])


def _make_service(fetcher, vision=None, downloader=None, embedding=None,
                  summarizer=None):
    return WeChatMPSyncService(
        fetcher=fetcher,
        redis=FakeRedis(),
        embedding_client=embedding or FakeEmbeddingClient(),
        image_downloader=downloader or FakeDownloader(),
        vision_parser=vision or FakeVision(),
        # WP12：总结器默认替身（避免触真实 LLM 网关），VL 管道断言不受影响
        summarizer=summarizer or FakeSummarizer(),
    )


async def _retrieve_doc_ids(tenant_id: str, query: str):
    """真实混合检索（真实 pgvector + 真实 FTS SQL），返回 doc_id 集合。"""
    from src.db.database import get_db_connection
    from src.knowledge.retriever.hybrid_retriever import HybridRetriever

    module = _load_real_vector_db()
    with get_db_connection() as conn:
        vector_db = module.get_vector_db(dimension=1024, conn=conn)
        retriever = HybridRetriever(
            vector_db=vector_db, embedding_client=FakeEmbeddingClient(), conn=conn
        )
        results = await retriever.retrieve(query=query, top_k=10, tenant_id=tenant_id)
    return {r["doc_id"] for r in results}


# ------------------------------- 夹具构造 -------------------------------

SHORT_URL = "https://mp.weixin.qq.com/s/Wp10TokAlpha01"


def _png_bytes(w: int, h: int, color=(200, 30, 30)) -> bytes:
    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


def image_only_html(title: str, image_count: int) -> str:
    """纯图文章页（js_content 仅含 data-src 图片节点，正文文字为 0）。"""
    imgs = "".join(
        f'<img data-src="https://mmecoa.qpic.cn/wp10/img_{i}.jpg?wx_fmt=jpeg">'
        for i in range(1, image_count + 1)
    )
    return (
        "<html><head>"
        f"<script>var msg_title = '{title}'.html(false);</script>"
        "</head><body>"
        f'<h1 id="activity-name">{title}</h1>'
        '<span id="js_name">WP10测试号</span>'
        f'<div id="js_content">{imgs}</div>'
        "</body></html>"
    )


def image_and_text_html(title: str, body: str, image_count: int) -> str:
    imgs = "".join(
        f'<img data-src="https://mmbiz.qpic.cn/wp10/t{i}.png">' for i in range(1, image_count + 1)
    )
    return (
        "<html><head>"
        f"<script>var msg_title = '{title}'.html(false);</script>"
        "</head><body>"
        f'<h1 id="activity-name">{title}</h1>'
        f'<div id="js_content"><p>{body}</p>{imgs}</div>'
        "</body></html>"
    )


def ok_result(html: str) -> FetchResult:
    return FetchResult(status=STATUS_OK, html=html, http_status=200,
                       evidence={"has_js_content": True})


# =============================== 下载：URL 校验 ===============================


class TestImageUrlValidation:
    def test_qpic_suffix_accepted(self):
        assert validate_image_url(
            "https://mmbiz.qpic.cn/mmbiz_jpg/abc?wx_fmt=jpeg", resolver=lambda h: True
        ) is None
        assert validate_image_url(
            "https://mmecoa.qpic.cn/x.jpg", resolver=lambda h: True
        ) is None

    def test_lookalike_domain_rejected(self):
        # evil-qpic.cn 以连字符伪装，不命中 .qpic.cn 精确后缀
        assert validate_image_url(
            "https://evil-qpic.cn/a.jpg", resolver=lambda h: True
        ) == "host_not_allowed"

    def test_ip_literal_rejected(self):
        assert validate_image_url(
            "https://127.0.0.1/a.jpg", resolver=lambda h: True
        ) == "ip_literal_not_allowed"
        assert validate_image_url(
            "https://10.0.0.5/a.jpg", resolver=lambda h: True
        ) == "ip_literal_not_allowed"

    def test_non_https_rejected(self):
        assert validate_image_url(
            "http://mmbiz.qpic.cn/a.jpg", resolver=lambda h: True
        ) == "scheme_not_https"

    def test_userinfo_and_port_rejected(self):
        assert validate_image_url(
            "https://user:pass@mmbiz.qpic.cn/a.jpg", resolver=lambda h: True
        ) == "userinfo_not_allowed"
        assert validate_image_url(
            "https://mmbiz.qpic.cn:8443/a.jpg", resolver=lambda h: True
        ) == "port_not_allowed"

    def test_dns_not_public_rejected(self):
        # resolver 返回 False 模拟解析到内网（防 DNS 重绑定）
        assert validate_image_url(
            "https://mmbiz.qpic.cn/a.jpg", resolver=lambda h: False
        ) == "dns_not_public"


# =============================== 下载：下载器行为 ===============================


def _make_client(handler, **kwargs) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(handler), follow_redirects=False, **kwargs
    )


class TestDownloader:
    def test_download_success_and_headers(self, tenant_id):
        """正常下载转存 + Referer/UA 按请求注入（防盗链）。"""
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["referer"] = request.headers.get("referer")
            seen["ua_present"] = bool(request.headers.get("user-agent"))
            return httpx.Response(200, headers={"content-type": "image/jpeg"},
                                  content=_png_bytes(4, 4))

        dl = MPImageDownloader(client=_make_client(handler), resolver=lambda h: True)
        outcome = dl.download(tenant_id, 9001, [(1, "https://mmbiz.qpic.cn/a.jpg")])

        assert outcome.ok_count == 1 and not outcome.failures
        img = outcome.images[0]
        assert img.n == 1 and img.ext == "jpg" and not img.reused
        assert os.path.exists(img.local_path)
        assert seen["referer"] == "https://mp.weixin.qq.com/"
        assert seen["ua_present"] is True
        # 路径规范：storage/tenants/{tid}/knowledge/wechat_mp/{article_row_id}/
        assert f"/knowledge/wechat_mp/9001/img_1." in img.local_path

    def test_redirect_revalidated_internal_target_rejected(self, tenant_id):
        """重定向逐跳重新校验：qpic 302 跳内网 IP → IP 字面量拒收；跳非白名单
        域名 → host 拒收；第二跳均不发起实际请求。"""
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            if request.url.host == "mmbiz.qpic.cn":
                return httpx.Response(302, headers={"location": "https://192.168.1.5/x.jpg"})
            return httpx.Response(200, headers={"content-type": "image/png"},
                                  content=_png_bytes(1, 1))

        dl = MPImageDownloader(client=_make_client(handler), resolver=lambda h: True)
        outcome = dl.download(tenant_id, 9002, [(1, "https://mmbiz.qpic.cn/r.jpg")])
        assert outcome.ok_count == 0
        assert outcome.failures[0].reason == "ip_literal_not_allowed"
        assert len(calls) == 1  # 第二跳被校验拦截，未发起请求

    def test_redirect_to_non_whitelisted_host_rejected(self, tenant_id):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            return httpx.Response(302, headers={"location": "https://evil.example.com/x.jpg"})

        dl = MPImageDownloader(client=_make_client(handler), resolver=lambda h: True)
        outcome = dl.download(tenant_id, 9014, [(1, "https://mmbiz.qpic.cn/r.jpg")])
        assert outcome.failures[0].reason == "host_not_allowed"
        assert len(calls) == 1

    def test_redirect_to_other_qpic_subdomain_allowed(self, tenant_id):
        """重定向到另一个 qpic 子域：逐跳校验通过后正常下载。"""
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "mmbiz.qpic.cn":
                return httpx.Response(302, headers={"location": "https://mmecoa.qpic.cn/b.jpg"})
            return httpx.Response(200, headers={"content-type": "image/jpeg"},
                                  content=_png_bytes(2, 2))

        dl = MPImageDownloader(client=_make_client(handler), resolver=lambda h: True)
        outcome = dl.download(tenant_id, 9003, [(1, "https://mmbiz.qpic.cn/r.jpg")])
        assert outcome.ok_count == 1 and not outcome.failures

    def test_too_many_redirects(self, tenant_id):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(302, headers={"location": "https://mmbiz.qpic.cn/loop.jpg"})

        dl = MPImageDownloader(client=_make_client(handler), resolver=lambda h: True)
        outcome = dl.download(tenant_id, 9004, [(1, "https://mmbiz.qpic.cn/r.jpg")])
        assert outcome.failures[0].reason == "too_many_redirects"

    def test_content_length_limit(self, tenant_id):
        """Content-Length 超限：不读 body 直接中断。"""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, headers={"content-type": "image/jpeg",
                              "content-length": str(11 * 1024 * 1024)},
                content=b"",
            )

        dl = MPImageDownloader(client=_make_client(handler), resolver=lambda h: True)
        outcome = dl.download(tenant_id, 9005, [(1, "https://mmbiz.qpic.cn/big.jpg")])
        assert outcome.failures[0].reason == "size_exceeded"

    def test_streamed_bytes_limit(self, tenant_id):
        """无 Content-Length 时流式累计超限中断（用小 max_bytes 模拟 10MB 语义）。"""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, headers={"content-type": "image/jpeg"},
                                  content=b"x" * 200)

        dl = MPImageDownloader(client=_make_client(handler), max_bytes=100,
                               resolver=lambda h: True)
        outcome = dl.download(tenant_id, 9006, [(1, "https://mmbiz.qpic.cn/big.jpg")])
        assert outcome.failures[0].reason == "size_exceeded"

    def test_timeout_and_network_errors_isolated(self, tenant_id):
        def timeout_handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("boom")

        dl = MPImageDownloader(client=_make_client(timeout_handler), resolver=lambda h: True)
        outcome = dl.download(tenant_id, 9007, [(1, "https://mmbiz.qpic.cn/slow.jpg")])
        assert outcome.failures[0].reason == "timeout"

    def test_decode_pixel_cap(self, tenant_id):
        """解码像素面积超上限：拒绝（防解压炸弹），不等比缩小。"""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, headers={"content-type": "image/png"},
                                  content=_png_bytes(20, 20))  # 400 像素

        dl = MPImageDownloader(client=_make_client(handler), max_pixels=100,
                               resolver=lambda h: True)
        outcome = dl.download(tenant_id, 9008, [(1, "https://mmbiz.qpic.cn/bomb.png")])
        assert outcome.failures[0].reason == "decode_failed"

    def test_long_edge_resized(self, tenant_id):
        """长边超限等比缩小并重编码 jpg。"""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, headers={"content-type": "image/png"},
                                  content=_png_bytes(40, 20))

        dl = MPImageDownloader(client=_make_client(handler), max_long_edge=10,
                               resolver=lambda h: True)
        outcome = dl.download(tenant_id, 9009, [(1, "https://mmbiz.qpic.cn/wide.png")])
        assert outcome.ok_count == 1
        img = outcome.images[0]
        assert img.ext == "jpg"
        from PIL import Image

        with Image.open(img.local_path) as check:
            assert max(check.size) == 10

    def test_per_article_image_limit(self, tenant_id):
        """每文章 30 张上限：超出部分不发起请求，记 skipped_over_limit。"""
        requests = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(str(request.url))
            return httpx.Response(200, headers={"content-type": "image/png"},
                                  content=_png_bytes(1, 1))

        dl = MPImageDownloader(client=_make_client(handler), resolver=lambda h: True)
        srcs = [(i, f"https://mmbiz.qpic.cn/{i}.jpg") for i in range(1, 36)]
        outcome = dl.download(tenant_id, 9010, srcs)
        assert outcome.ok_count == MAX_IMAGES_PER_ARTICLE
        assert outcome.skipped_over_limit == 5
        assert len(requests) == MAX_IMAGES_PER_ARTICLE

    def test_idempotent_reuse_same_src(self, tenant_id):
        """幂等复用：同 src 重复处理零下载（httpx MockTransport 计数不变）。"""
        requests = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(str(request.url))
            return httpx.Response(200, headers={"content-type": "image/jpeg"},
                                  content=_png_bytes(2, 2))

        dl = MPImageDownloader(client=_make_client(handler), resolver=lambda h: True)
        srcs = [(1, "https://mmbiz.qpic.cn/a.jpg"), (2, "https://mmbiz.qpic.cn/b.jpg")]
        first = dl.download(tenant_id, 9011, srcs)
        assert first.ok_count == 2 and not any(i.reused for i in first.images)
        assert len(requests) == 2

        second = dl.download(tenant_id, 9011, srcs)
        assert second.ok_count == 2
        assert all(i.reused for i in second.images)
        assert len(requests) == 2  # 零新请求

    def test_no_reuse_when_src_changed(self, tenant_id):
        """src 变更（内容改版编号错位）不复用旧图，重新下载。"""
        requests = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(str(request.url))
            return httpx.Response(200, headers={"content-type": "image/jpeg"},
                                  content=_png_bytes(2, 2))

        dl = MPImageDownloader(client=_make_client(handler), resolver=lambda h: True)
        dl.download(tenant_id, 9012, [(1, "https://mmbiz.qpic.cn/old.jpg")])
        assert len(requests) == 1
        outcome = dl.download(tenant_id, 9012, [(1, "https://mmbiz.qpic.cn/new.jpg")])
        assert not outcome.images[0].reused
        assert len(requests) == 2

    def test_invalid_url_rejected_per_image(self, tenant_id):
        """域名非法/非 HTTPS 逐张拒收，不中断其余图片。"""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, headers={"content-type": "image/png"},
                                  content=_png_bytes(1, 1))

        dl = MPImageDownloader(client=_make_client(handler), resolver=lambda h: True)
        srcs = [
            (1, "https://evil-qpic.cn/x.jpg"),
            (2, "http://mmbiz.qpic.cn/y.jpg"),
            (3, "https://mmbiz.qpic.cn/ok.jpg"),
        ]
        outcome = dl.download(tenant_id, 9013, srcs)
        assert [i.n for i in outcome.images] == [3]
        reasons = {f.n: f.reason for f in outcome.failures}
        assert reasons[1] == "host_not_allowed"
        assert reasons[2] == "scheme_not_https"


# =============================== VL：目标选择与调用 ===============================


class TestVisionTargets:
    @staticmethod
    def _keys_patch(monkeypatch, keys):
        """在 LLMProviderConfig 类上替换 get_effective_keys（实例是 pydantic 模型，
        不允许挂非字段属性）。"""
        from src.config.settings import LLMProviderConfig

        monkeypatch.setattr(LLMProviderConfig, "get_effective_keys",
                            lambda self: keys, raising=True)

    def test_targets_from_multimodal_list_intersection(self, monkeypatch):
        """固定首选 GLM-5.3-Flash@zhipu 排首位；其余 = 多模态清单 ∩ provider 默认模型。"""
        monkeypatch.setattr(settings.llm, "provider", "qwen", raising=False)
        monkeypatch.setattr(settings.llm.qwen, "model", "qwen3-vl-plus", raising=False)
        monkeypatch.setattr(settings.llm.failover, "providers", ["zhipu", "deepseek"],
                            raising=False)
        monkeypatch.setattr(settings.llm.zhipu, "model", "GLM-5.3-Flash", raising=False)
        # deepseek 默认模型为文本模型，不在多模态清单 → 不进入目标
        monkeypatch.setattr(settings.llm.deepseek, "model", "deepseek-v4-flash",
                            raising=False)
        self._keys_patch(monkeypatch, ["k"])

        models = [{"model_name": "qwen3-vl-plus"}, {"model_name": "GLM-5.3-Flash"}]
        targets = resolve_vision_targets(multimodal_models=models)
        assert targets == [
            # 固定首选：GLM-5.3-Flash@zhipu 不随主 provider 变化
            VisionTarget(provider="zhipu", model="GLM-5.3-Flash"),
            VisionTarget(provider="qwen", model="qwen3-vl-plus"),
        ]

    def test_pinned_default_survives_text_primary_provider(self, monkeypatch):
        """主 provider 默认模型为文本模型时，固定首选仍激活（多数部署的真实形态）。"""
        monkeypatch.setattr(settings.llm, "provider", "deepseek", raising=False)
        monkeypatch.setattr(settings.llm.deepseek, "model", "deepseek-v4-pro",
                            raising=False)
        monkeypatch.setattr(settings.llm.failover, "providers", [], raising=False)
        self._keys_patch(monkeypatch, ["k"])
        models = [{"model_name": "GLM-5.3-Flash"}]
        targets = resolve_vision_targets(multimodal_models=models)
        assert targets == [VisionTarget(provider="zhipu", model="GLM-5.3-Flash")]

    def test_pinned_default_skipped_without_zhipu_keys(self, monkeypatch):
        """zhipu 未配置 key：固定首选跳过，不产生不可调用目标。"""
        monkeypatch.setattr(settings.llm, "provider", "deepseek", raising=False)
        monkeypatch.setattr(settings.llm.deepseek, "model", "deepseek-v4-pro",
                            raising=False)
        monkeypatch.setattr(settings.llm.failover, "providers", [], raising=False)

        from src.config.settings import LLMProviderConfig

        real = LLMProviderConfig.get_effective_keys

        def _keys(self):
            return ["k"] if self is settings.llm.deepseek else []

        monkeypatch.setattr(LLMProviderConfig, "get_effective_keys", _keys,
                            raising=True)
        try:
            targets = resolve_vision_targets(
                multimodal_models=[{"model_name": "GLM-5.3-Flash"}]
            )
        finally:
            monkeypatch.undo()
        assert targets == []
        assert real is not None

    def test_no_multimodal_models_empty(self):
        assert resolve_vision_targets(multimodal_models=[]) == []

    def test_provider_without_keys_excluded(self, monkeypatch):
        monkeypatch.setattr(settings.llm, "provider", "qwen", raising=False)
        monkeypatch.setattr(settings.llm.qwen, "model", "qwen3-vl-plus", raising=False)
        self._keys_patch(monkeypatch, [])
        targets = resolve_vision_targets(
            multimodal_models=[{"model_name": "qwen3-vl-plus"}]
        )
        assert targets == []


class RecordingGateway:
    """可编程网关替身：responder(messages, kwargs, history) → result dict 或抛异常。"""

    def __init__(self, responder):
        self.responder = responder
        self.calls = []          # (messages, kwargs)
        self.concurrent = 0
        self.max_concurrent = 0
        self._lock = threading.Lock()

    async def chat(self, messages=None, **kwargs):
        with self._lock:
            self.concurrent += 1
            self.max_concurrent = max(self.max_concurrent, self.concurrent)
        self.calls.append((messages, kwargs))
        try:
            await asyncio.sleep(0.02)
            return self.responder(len(self.calls))
        finally:
            with self._lock:
                self.concurrent -= 1


def _ok_response(content="图中文字：春季促销"):
    return {"content": content, "finish_reason": "stop",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}


class TestVisionParser:
    def test_no_model_signal(self):
        """无可用多模态模型：available()=False + describe_images 返回 no_model 信号。"""
        parser = VisionParser(targets=[])
        assert parser.available() is False

        outcome = asyncio.run(parser.describe_images([(1, "/tmp/x.jpg")], "t"))
        assert outcome.no_model is True
        assert outcome.descriptions == {} and outcome.ok_count == 0

    def test_message_shape_instruction_only_no_context(self, tmp_path):
        """单轮 user message = 固定指令 + 1 张 image_url data URL；无系统提示/历史/工具。"""
        img = tmp_path / "img_1.png"
        img.write_bytes(_png_bytes(2, 2))
        gw = RecordingGateway(lambda i: _ok_response())
        parser = VisionParser(
            targets=[VisionTarget("qwen", "qwen3-vl-plus")],
            gateway_factory=lambda p, m: gw,
        )
        outcome = asyncio.run(parser.describe_images([(1, str(img))], "t"))
        assert outcome.ok_count == 1

        assert len(gw.calls) == 1
        messages, kwargs = gw.calls[0]
        assert [m["role"] for m in messages] == ["user"]  # 单轮、无 system
        assert len(messages[0]["content"]) == 2
        text_part, image_part = messages[0]["content"]
        assert text_part["type"] == "text"
        assert text_part["text"] == PARSE_INSTRUCTION  # 固定指令原文
        assert image_part["type"] == "image_url"
        assert image_part["image_url"]["url"].startswith("data:image/png;base64,")
        assert "tools" not in kwargs and "tool_choice" not in kwargs  # 无工具 schema

    def test_semaphore_concurrency_cap(self, tmp_path):
        """Semaphore(3)：并发调用上限不超过 3。"""
        paths = []
        for i in range(1, 7):
            p = tmp_path / f"img_{i}.png"
            p.write_bytes(_png_bytes(2, 2))
            paths.append((i, str(p)))
        gw = RecordingGateway(lambda i: _ok_response())
        parser = VisionParser(
            targets=[VisionTarget("qwen", "qwen3-vl-plus")],
            gateway_factory=lambda p, m: gw,
        )
        outcome = asyncio.run(parser.describe_images(paths, "t"))
        assert outcome.ok_count == 6
        assert gw.max_concurrent <= 3

    def test_per_image_retry_once(self, tmp_path):
        """单张失败重试 1 次：首次异常、第二次成功 → 成功且恰好 2 次调用。"""
        img = tmp_path / "img_1.png"
        img.write_bytes(_png_bytes(2, 2))
        gw = RecordingGateway(
            lambda i: (_raise(RuntimeError("boom")) if i == 1 else _ok_response())
        )
        parser = VisionParser(
            targets=[VisionTarget("qwen", "qwen3-vl-plus")],
            gateway_factory=lambda p, m: gw,
        )
        outcome = asyncio.run(parser.describe_images([(1, str(img))], "t"))
        assert outcome.ok_count == 1
        assert len(gw.calls) == 2  # 初次 + 重试 1 次

    def test_failure_after_retry_marked_failed(self, tmp_path):
        """重试仍失败（无次选目标）→ 计 failed，共 2 次调用。"""
        img = tmp_path / "img_1.png"
        img.write_bytes(_png_bytes(2, 2))
        gw = RecordingGateway(lambda i: _raise(RuntimeError("down")))
        parser = VisionParser(
            targets=[VisionTarget("qwen", "qwen3-vl-plus")],
            gateway_factory=lambda p, m: gw,
        )
        outcome = asyncio.run(parser.describe_images([(1, str(img))], "t"))
        assert outcome.ok_count == 0
        assert outcome.failures[0].n == 1 and outcome.failures[0].reason == "error"
        assert len(gw.calls) == 2

    def test_failover_to_second_multimodal_target(self, tmp_path):
        """次选目标降级：首选持续失败 → 按列表降级到第二个多模态目标（共 3 次）。"""
        img = tmp_path / "img_1.png"
        img.write_bytes(_png_bytes(2, 2))
        gateways = {}

        def factory(provider, model):
            gw = RecordingGateway(
                lambda i: (_raise(RuntimeError("down")) if provider == "qwen"
                           else _ok_response("备用目标解析结果"))
            )
            gateways[(provider, model)] = gw
            return gw

        parser = VisionParser(
            targets=[VisionTarget("qwen", "qwen3-vl-plus"),
                     VisionTarget("zhipu", "GLM-5.3-Flash")],
            gateway_factory=factory,
        )
        outcome = asyncio.run(parser.describe_images([(1, str(img))], "t"))
        assert outcome.ok_count == 1
        assert outcome.successes[0].model == "GLM-5.3-Flash"  # 降级后仍为多模态模型
        assert len(gateways[("qwen", "qwen3-vl-plus")].calls) == 2
        assert len(gateways[("zhipu", "GLM-5.3-Flash")].calls) == 1

    def test_unrecognized_counts_failed(self, tmp_path):
        """「图片无法识别」按约定计 failed（不计费语义由调用方保证）。"""
        img = tmp_path / "img_1.png"
        img.write_bytes(_png_bytes(2, 2))
        gw = RecordingGateway(lambda i: _ok_response(UNRECOGNIZED_TEXT))
        parser = VisionParser(
            targets=[VisionTarget("qwen", "qwen3-vl-plus")],
            gateway_factory=lambda p, m: gw,
        )
        outcome = asyncio.run(parser.describe_images([(1, str(img))], "t"))
        assert outcome.ok_count == 0
        assert outcome.failures[0].reason == "unrecognized"

    def test_empty_response_retried_then_failed(self, tmp_path):
        """空响应按失败重试 1 次，仍空 → failed(empty_response)。"""
        img = tmp_path / "img_1.png"
        img.write_bytes(_png_bytes(2, 2))
        gw = RecordingGateway(lambda i: _ok_response(""))
        parser = VisionParser(
            targets=[VisionTarget("qwen", "qwen3-vl-plus")],
            gateway_factory=lambda p, m: gw,
        )
        outcome = asyncio.run(parser.describe_images([(1, str(img))], "t"))
        assert outcome.failures[0].reason == "empty_response"
        assert len(gw.calls) == 2

    def test_missing_image_file_failed(self):
        """图片文件读取失败 → failed(image_read_failed)，不触发 LLM。"""
        gw = RecordingGateway(lambda i: _ok_response())
        parser = VisionParser(
            targets=[VisionTarget("qwen", "qwen3-vl-plus")],
            gateway_factory=lambda p, m: gw,
        )
        outcome = asyncio.run(parser.describe_images([(1, "Z:/not/exist/img.png")], "t"))
        assert outcome.failures[0].reason == "image_read_failed"
        assert gw.calls == []


def _raise(exc):
    raise exc


class TestImageDataUrl:
    def test_small_png_passthrough(self, tmp_path):
        p = tmp_path / "a.png"
        p.write_bytes(_png_bytes(3, 3))
        url = build_image_data_url(str(p))
        assert url.startswith("data:image/png;base64,")

    def test_oversize_image_recompressed_to_jpeg(self, tmp_path):
        """超 5MB 的不可压缩图片 → 重编码 JPEG 且体积进限制内。"""
        import os as _os

        raw = _os.urandom(1500 * 1500 * 3)  # 随机噪声，PNG 近似不可压缩
        from PIL import Image

        img = Image.frombytes("RGB", (1500, 1500), raw)
        p = tmp_path / "big.png"
        img.save(p, format="PNG")
        assert p.stat().st_size > MAX_IMAGE_DATA_BYTES

        url = build_image_data_url(str(p))
        assert url is not None
        assert url.startswith("data:image/jpeg;base64,")
        # 解码回图片验证有效
        header, b64 = url.split(",", 1)
        import base64

        with Image.open(BytesIO(base64.b64decode(b64))) as decoded:
            assert decoded.size[0] > 0

    def test_missing_file_returns_none(self):
        assert build_image_data_url("Z:/no/such/file.png") is None


class TestCreditCalculation:
    def test_per_call_formula_aligned_with_asr(self):
        # 0.01 元/张 × usage_factor 100 = 1 积分/张
        assert calculate_image_parse_credit_cost(0.01, 100) == 1.0
        assert calculate_image_parse_credit_cost(0.005, 100) == 0.5  # ceil 到分
        assert calculate_image_parse_credit_cost(0.0, 100) == 0.0


# =============================== 管道集成 ===============================


class TestPipelineVL:
    async def test_image_only_article_ingested_with_descriptions(self, tenant_id):
        """纯图文章 + VL 成功：入库成功、正文含 [图片N: 描述]、检索可见、转存路径落 metadata。"""
        _create_tenant(tenant_id)
        fetcher = StubFetcher()
        fetcher.set_page(SHORT_URL, ok_result(image_only_html("活动长图", 2)))
        vision = FakeVision(descriptions={1: "春季促销活动长图", 2: "门店地址与营业时间"})
        downloader = FakeDownloader()
        svc = _make_service(fetcher, vision=vision, downloader=downloader)

        run_id, row_ids, item_ids = _enqueue(tenant_id, [SHORT_URL])
        result = await svc.claim_and_run(tenant_id)
        assert result["executed"] is True

        item = _query_one(
            "SELECT status, action, billing_status FROM bs_wechat_mp_sync_items "
            "WHERE id = %s", (item_ids[0],))
        assert item["status"] == "success" and item["action"] == "new"

        article = _query_one(
            "SELECT processing_status, pipeline_version, doc_id, image_count "
            "FROM bs_wechat_mp_articles WHERE id = %s", (row_ids[0],))
        assert article["processing_status"] == "success"
        assert article["pipeline_version"] == PIPELINE_VERSION == "p3"
        assert article["image_count"] == 2
        doc_id = article["doc_id"]
        assert doc_id

        doc = _query_one("SELECT raw_text, metadata, summary FROM documents WHERE id = %s",
                         (doc_id,))
        # [图片N: 描述] 插回原位置（描述计入 raw_text 审计正文）
        assert "[图片1: 春季促销活动长图]" in doc["raw_text"]
        assert "[图片2: 门店地址与营业时间]" in doc["raw_text"]
        # WP12：summary = 总结（替身 = merged 截断口径），含图片描述
        assert "[图片1:" in doc["summary"]
        metadata = json.loads(doc["metadata"])
        assert len(metadata["image_local_paths"]) == 2
        assert metadata["image_parse_failed_count"] == 0
        assert metadata["image_skipped_count"] == 0

        # 下载与解析收到彼此对齐的序号/路径
        assert downloader.calls and len(downloader.calls[0]["srcs"]) == 2
        assert len(vision.calls[0]["images"]) == 2
        assert vision.calls[0]["images"][0][1] == metadata["image_local_paths"][0]

        # 检索可见（图片描述进入正文参与分块）
        doc_ids = await _retrieve_doc_ids(tenant_id, "春季促销活动长图")
        assert doc_id in doc_ids

    async def test_mixed_short_text_and_images_numbering_aligned(self, tenant_id):
        """混排（短文字在前 + 多图）：下载/VL 编号必须按图片序数，与 [图片N] 占位对齐。

        回归锁定：srcs 曾用全节点索引编号（第 N 个 image 节点的 n = 其在 nodes
        列表中的位置+1），文字先于图片时与 _merge_image_descriptions 的图片序数
        错位 → 描述张冠李戴/丢失。纯图文章两者巧合一致，故需本用例锁定混排布局。
        """
        _create_tenant(tenant_id)
        src_a = "https://mmecoa.qpic.cn/wp10/mixed_first.jpg"
        src_b = "https://mmecoa.qpic.cn/wp10/mixed_second.jpg"
        html = (
            "<html><head>"
            "<script>var msg_title = '混排短文'.html(false);</script>"
            "</head><body>"
            '<h1 id="activity-name">混排短文</h1>'
            '<div id="js_content">'
            "<p>报名看图</p>"
            f'<img data-src="{src_a}">'
            "<p> </p>"
            f'<img data-src="{src_b}">'
            "</div></body></html>"
        )
        fetcher = StubFetcher()
        fetcher.set_page(SHORT_URL, ok_result(html))
        vision = FakeVision(descriptions={1: "第一张的描述", 2: "第二张的描述"})
        downloader = FakeDownloader()
        svc = _make_service(fetcher, vision=vision, downloader=downloader)

        _, row_ids, item_ids = _enqueue(tenant_id, [SHORT_URL])
        result = await svc.claim_and_run(tenant_id)
        assert result["executed"] is True

        item = _query_one(
            "SELECT status FROM bs_wechat_mp_sync_items WHERE id = %s",
            (item_ids[0],))
        assert item["status"] == "success"

        # 下载/VL 收到图片序数编号（1 起），与占位编号一致
        srcs = downloader.calls[0]["srcs"]
        assert [n for n, _ in srcs] == [1, 2]
        assert [s for _, s in srcs] == [src_a, src_b]
        assert [n for n, _ in vision.calls[0]["images"]] == [1, 2]

        doc_id = _query_one(
            "SELECT doc_id FROM bs_wechat_mp_articles WHERE id = %s",
            (row_ids[0],))["doc_id"]
        doc = _query_one("SELECT raw_text FROM documents WHERE id = %s", (doc_id,))
        # 描述按图片序数各归各位，不串号不丢失
        assert "[图片1: 第一张的描述]" in doc["raw_text"]
        assert "[图片2: 第二张的描述]" in doc["raw_text"]
        assert "报名看图" in doc["raw_text"]

    async def test_all_images_failed_deferred(self, tenant_id):
        """VL 全部解析失败 → deferred（错误信息更新为待解析语义），零计费零文档。"""
        _create_tenant(tenant_id)
        fetcher = StubFetcher()
        fetcher.set_page(SHORT_URL, ok_result(image_only_html("纯图活动", 2)))
        vision = FakeVision(fail_n={1, 2})
        svc = _make_service(fetcher, vision=vision)

        run_id, row_ids, item_ids = _enqueue(tenant_id, [SHORT_URL])
        await svc.claim_and_run(tenant_id)

        item = _query_one(
            "SELECT status, error_code, error_message, billing_status, credits_charged "
            "FROM bs_wechat_mp_sync_items WHERE id = %s", (item_ids[0],))
        assert item["status"] == "deferred"
        assert item["error_code"] == "deferred_image_pending"
        assert "解析" in item["error_message"] and "重试" in item["error_message"]
        assert item["billing_status"] == "not_required"
        assert float(item["credits_charged"]) == 0.0

        article = _query_one(
            "SELECT processing_status, doc_id, next_retry_at FROM bs_wechat_mp_articles "
            "WHERE id = %s", (row_ids[0],))
        assert article["processing_status"] == "deferred"
        assert article["doc_id"] is None
        assert article["next_retry_at"] is None  # 非技术失败，不进退避
        assert _query_one(
            "SELECT COUNT(*) AS c FROM documents WHERE tenant_id = %s",
            (tenant_id,))["c"] == 0
        assert _query_one(
            "SELECT COUNT(*) AS c FROM chat_records WHERE tenant_id = %s",
            (tenant_id,))["c"] == 0

    async def test_no_multimodal_model_deferred_before_download(self, tenant_id):
        """无可用多模态模型 → deferred，且不触发图片下载（不发纯文本模型）。"""
        _create_tenant(tenant_id)
        fetcher = StubFetcher()
        fetcher.set_page(SHORT_URL, ok_result(image_only_html("无模型场景", 1)))
        vision = FakeVision(no_model=True)
        downloader = FakeDownloader()
        svc = _make_service(fetcher, vision=vision, downloader=downloader)

        run_id, row_ids, item_ids = _enqueue(tenant_id, [SHORT_URL])
        await svc.claim_and_run(tenant_id)

        item = _query_one(
            "SELECT status, error_code, error_message FROM bs_wechat_mp_sync_items "
            "WHERE id = %s", (item_ids[0],))
        assert item["status"] == "deferred"
        assert "无可用多模态模型" in item["error_message"]
        assert downloader.calls == []  # 模型缺失时零下载
        assert vision.calls == []

    async def test_all_downloads_failed_deferred_without_vl_calls(self, tenant_id):
        """图片全部下载失败 → 直接 deferred，不发起 VL 调用。"""
        _create_tenant(tenant_id)
        fetcher = StubFetcher()
        fetcher.set_page(SHORT_URL, ok_result(image_only_html("下载全挂", 2)))
        vision = FakeVision()
        downloader = FakeDownloader(fail_all=True)
        svc = _make_service(fetcher, vision=vision, downloader=downloader)

        _, _, item_ids = _enqueue(tenant_id, [SHORT_URL])
        await svc.claim_and_run(tenant_id)

        item = _query_one(
            "SELECT status, error_message FROM bs_wechat_mp_sync_items WHERE id = %s",
            (item_ids[0],))
        assert item["status"] == "deferred"
        assert "下载或解析全部失败" in item["error_message"]
        assert vision.calls == []

    async def test_partial_success_keeps_placeholder(self, tenant_id):
        """部分成功：成功描述插回，失败保留 [图片N] 占位照常入库；只对成功张计费。"""
        _create_tenant(tenant_id)
        fetcher = StubFetcher()
        fetcher.set_page(SHORT_URL, ok_result(image_only_html("部分成功", 3)))
        vision = FakeVision(descriptions={1: "首图活动主题"}, fail_n={2, 3})
        svc = _make_service(fetcher, vision=vision)

        _, row_ids, item_ids = _enqueue(tenant_id, [SHORT_URL])
        await svc.claim_and_run(tenant_id)

        item = _query_one(
            "SELECT status FROM bs_wechat_mp_sync_items WHERE id = %s", (item_ids[0],))
        assert item["status"] == "success"
        doc_id = _query_one(
            "SELECT doc_id FROM bs_wechat_mp_articles WHERE id = %s", (row_ids[0],)
        )["doc_id"]
        doc = _query_one("SELECT raw_text, metadata FROM documents WHERE id = %s", (doc_id,))
        assert "[图片1: 首图活动主题]" in doc["raw_text"]
        # 失败图片保留整段占位（占位符正则可完整剔除）
        plain = _IMAGE_PLACEHOLDER_RE.sub("", doc["raw_text"]).strip()
        assert "[图片2]" in doc["raw_text"] and "[图片3]" in doc["raw_text"]
        assert len(plain) > 0  # 描述文本计入正文
        metadata = json.loads(doc["metadata"])
        assert metadata["image_parse_failed_count"] == 2

        # 仅成功张计费：3 张中成功 1 张 → 1 条图片记录
        rows = _query_all(
            "SELECT source_type, credit_cost FROM chat_records WHERE tenant_id = %s "
            "AND source_type = %s", (tenant_id, VISION_PARSE_SOURCE_TYPE))
        assert len(rows) == 1
        assert float(rows[0]["credit_cost"]) == 1.0

    async def test_text_rich_article_skips_vl(self, tenant_id):
        """文字充足且有图：不触发 VL（P2 决策：仅文字不足场景解析），维持占位。"""
        _create_tenant(tenant_id)
        fetcher = StubFetcher()
        fetcher.set_page(
            SHORT_URL,
            ok_result(image_and_text_html("图文并茂", "春季促销全场瓷砖八折，欢迎到店咨询选购。", 2)),
        )

        class _MustNotCall(FakeVision):
            def available(self):
                return True

            async def describe_images(self, images, tenant_id):
                raise AssertionError("文字充足的文章不应触发 VL 解析")

        vision = _MustNotCall()
        downloader = FakeDownloader()
        svc = _make_service(fetcher, vision=vision, downloader=downloader)

        _, row_ids, item_ids = _enqueue(tenant_id, [SHORT_URL])
        await svc.claim_and_run(tenant_id)

        item = _query_one(
            "SELECT status FROM bs_wechat_mp_sync_items WHERE id = %s", (item_ids[0],))
        assert item["status"] == "success"
        doc_id = _query_one(
            "SELECT doc_id FROM bs_wechat_mp_articles WHERE id = %s", (row_ids[0],)
        )["doc_id"]
        doc = _query_one("SELECT raw_text, metadata FROM documents WHERE id = %s", (doc_id,))
        assert "[图片1]" in doc["raw_text"] and "[图片2]" in doc["raw_text"]
        assert "image_local_paths" not in json.loads(doc["metadata"])
        assert downloader.calls == [] and vision.calls == []
        assert _query_one(
            "SELECT COUNT(*) AS c FROM chat_records WHERE tenant_id = %s "
            "AND source_type = %s", (tenant_id, VISION_PARSE_SOURCE_TYPE))["c"] == 0

    async def test_deferred_article_rebuilds_under_p3(self, tenant_id):
        """p1 deferred 存量文章在当前 pipeline 下重建可达：hash 相同但
        pipeline_version 不同 → 走重建分支执行 VL 并成功入库（deferred 行
        processing_status != 'success'，无快路径短路）。"""
        _create_tenant(tenant_id)
        html = image_only_html("存量deferred", 2)
        fetcher = StubFetcher()
        fetcher.set_page(SHORT_URL, ok_result(html))

        # 手工构造 p1 时代的 deferred 行：content_hash 与当前页面一致、pipeline_version='p1'
        extracted = extract_article(html)
        content_hash = WeChatMPSyncService._content_hash(
            extracted.title or "", extracted.nodes)
        identity = normalize_url(SHORT_URL)
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO bs_wechat_mp_articles
                    (tenant_id, external_id, original_url, fetch_url, source_channel,
                     status, processing_status, content_hash, pipeline_version, image_count)
                VALUES (%s, %s, %s, %s, 'callback', 'active', 'deferred', %s, 'p1', 2)
                RETURNING id
                """,
                (tenant_id, identity.external_id, identity.original_url,
                 identity.fetch_url, content_hash),
            )
            row_id = cursor.fetchone()["id"]
            cursor.execute(
                """
                INSERT INTO bs_wechat_mp_sync_runs (tenant_id, trigger_type, status, total_count)
                VALUES (%s, 'manual', 'queued', 1) RETURNING id
                """,
                (tenant_id,),
            )
            run_id = cursor.fetchone()["id"]
            cursor.execute(
                """
                INSERT INTO bs_wechat_mp_sync_items
                    (tenant_id, run_id, article_row_id, status)
                VALUES (%s, %s, %s, 'pending') RETURNING id
                """,
                (tenant_id, run_id, row_id),
            )
            conn.commit()

        vision = FakeVision(descriptions={1: "存量图一", 2: "存量图二"})
        svc = _make_service(fetcher, vision=vision)
        await svc.claim_and_run(tenant_id)

        article = _query_one(
            "SELECT processing_status, pipeline_version, doc_id FROM bs_wechat_mp_articles "
            "WHERE id = %s", (row_id,))
        assert article["processing_status"] == "success"
        assert article["pipeline_version"] == PIPELINE_VERSION
        doc = _query_one(
            "SELECT raw_text FROM documents WHERE id = %s", (article["doc_id"],))
        assert "[图片1: 存量图一]" in doc["raw_text"]

    async def test_no_credit_skips_vl_with_no_credit_semantics(self, tenant_id):
        """余额不足：整篇跳过解析（复用 no_credit 语义），不下载不解析不计费。"""
        _create_tenant(tenant_id, balance=0.0)
        html = image_only_html("无余额纯图", 2)
        extracted = extract_article(html)
        identity = normalize_url(SHORT_URL)
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO bs_wechat_mp_articles
                    (tenant_id, external_id, original_url, fetch_url, source_channel,
                     status, processing_status)
                VALUES (%s, %s, %s, %s, 'callback', 'active', 'pending')
                RETURNING id
                """,
                (tenant_id, identity.external_id, identity.original_url,
                 identity.fetch_url),
            )
            row_id = cursor.fetchone()["id"]
            conn.commit()

        vision = FakeVision()
        downloader = FakeDownloader()
        svc = _make_service(fetcher=StubFetcher(), vision=vision, downloader=downloader)
        item = {"id": 0, "article_row_id": row_id, "action": None}
        article = {"id": row_id}

        result = await svc._parse_images_or_defer(tenant_id, item, article, extracted)
        assert result is None
        assert downloader.calls == [] and vision.calls == []
        # item 级 no_credit：直接调 _mark_item_no_credit 的效果（此处验证文章侧退避）
        article_row = _query_one(
            "SELECT processing_status, next_retry_at FROM bs_wechat_mp_articles "
            "WHERE id = %s", (row_id,))
        assert article_row["processing_status"] == "pending"
        assert article_row["next_retry_at"] is not None


# =============================== 按张计费 ===============================


class TestImageBilling:
    async def test_per_image_billing_amount_and_breakdown(self, tenant_id):
        """按张计费：每成功 1 张一条 chat_records，credit_cost=1 积分/张，
        usage_breakdown 记录 billing_mode/单价/系数/VL token（对账用）。"""
        _create_tenant(tenant_id, balance=50.0)
        fetcher = StubFetcher()
        fetcher.set_page(SHORT_URL, ok_result(image_only_html("计费场景", 2)))
        svc = _make_service(fetcher, vision=FakeVision(descriptions={1: "图一", 2: "图二"}))

        _, row_ids, item_ids = _enqueue(tenant_id, [SHORT_URL])
        await svc.claim_and_run(tenant_id)

        records = _query_all(
            "SELECT * FROM chat_records WHERE tenant_id = %s AND source_type = %s "
            "ORDER BY id", (tenant_id, VISION_PARSE_SOURCE_TYPE))
        assert len(records) == 2
        for rec in records:
            assert float(rec["credit_cost"]) == 1.0  # 0.01 × 100 向上取整到分
            assert rec["model"] == "vl-test-model"
            breakdown = rec["usage_breakdown"]
            assert breakdown["billing_mode"] == "per_call"
            assert breakdown["price_per_call"] == 0.01
            assert breakdown["usage_factor"] == 100
            assert breakdown["vl_usage"]["total_tokens"] == 15  # 实际 token 记账对账

        # item 计费字段合并了 embedding + 图片两笔（reference 含全部记录 ID）
        item = _query_one(
            "SELECT billing_status, billing_reference, credits_charged "
            "FROM bs_wechat_mp_sync_items WHERE id = %s", (item_ids[0],))
        emb_record = _query_one(
            "SELECT record_id, credit_cost FROM chat_records WHERE tenant_id = %s "
            "AND source_type = 'wechat_mp_embedding'", (tenant_id,))
        image_record = _query_one(
            "SELECT record_id FROM chat_records WHERE tenant_id = %s "
            "AND source_type = %s ORDER BY id LIMIT 1",
            (tenant_id, VISION_PARSE_SOURCE_TYPE))
        expected_total = 2.0 + float(emb_record["credit_cost"])
        assert item["billing_status"] == "charged"
        assert emb_record["record_id"] in (item["billing_reference"] or "")
        assert image_record["record_id"] in (item["billing_reference"] or "")
        assert float(item["credits_charged"]) == pytest.approx(expected_total)
        assert _tenant_balance(tenant_id) == pytest.approx(round(50.0 - expected_total, 2))

    async def test_image_billing_unknown_never_recharged(self, tenant_id, monkeypatch):
        """图片计费不可确认（create 返回 None）→ 合并记 unknown、embedding 部分照常；
        下一轮 hash 未变走 check，禁止自动重扣。"""
        _create_tenant(tenant_id, balance=50.0)
        fetcher = StubFetcher()
        fetcher.set_page(SHORT_URL, ok_result(image_only_html("unknown场景", 1)))
        svc = _make_service(fetcher, vision=FakeVision(descriptions={1: "描述"}))

        from src.db.models import ChatRecordDB

        real_create = ChatRecordDB.create  # staticmethod 描述符取回即原函数

        def selective_create(**kw):
            if kw.get("source_type") == VISION_PARSE_SOURCE_TYPE:
                return None  # 图片计费结果不可确认
            return real_create(**kw)

        monkeypatch.setattr(ChatRecordDB, "create", staticmethod(selective_create))

        _, row_ids, item_ids = _enqueue(tenant_id, [SHORT_URL])
        await svc.claim_and_run(tenant_id)

        item = _query_one(
            "SELECT status, billing_status, credits_charged FROM bs_wechat_mp_sync_items "
            "WHERE id = %s", (item_ids[0],))
        assert item["status"] == "success"  # 计费失败不回滚内容
        assert item["billing_status"] == "unknown"  # 合并语义：任一 unknown → unknown
        emb_record = _query_one(
            "SELECT credit_cost FROM chat_records WHERE tenant_id = %s "
            "AND source_type = 'wechat_mp_embedding'", (tenant_id,))
        assert float(item["credits_charged"]) == float(emb_record["credit_cost"])
        balance_after = _tenant_balance(tenant_id)
        assert _query_one(
            "SELECT COUNT(*) AS c FROM chat_records WHERE tenant_id = %s "
            "AND source_type = %s", (tenant_id, VISION_PARSE_SOURCE_TYPE))["c"] == 0

        # 下一轮：hash 未变 → check 终态，不重扣
        monkeypatch.undo()
        _, _, item_ids2 = _enqueue(tenant_id, [SHORT_URL], trigger="recheck", action="check")
        await svc.claim_and_run(tenant_id)
        item2 = _query_one(
            "SELECT action, billing_status FROM bs_wechat_mp_sync_items WHERE id = %s",
            (item_ids2[0],))
        assert item2["action"] == "check" and item2["billing_status"] == "not_required"
        assert _query_one(
            "SELECT COUNT(*) AS c FROM chat_records WHERE tenant_id = %s "
            "AND source_type = %s", (tenant_id, VISION_PARSE_SOURCE_TYPE))["c"] == 0
        assert _tenant_balance(tenant_id) == balance_after

    async def test_failed_images_not_billed(self, tenant_id):
        """解析失败张不计费：全部失败走 deferred，无任何 chat_records/documents
        （「图片无法识别」单张计 failed 不计费语义已在 TestVisionParser 覆盖）。"""
        _create_tenant(tenant_id)
        fetcher = StubFetcher()
        fetcher.set_page(SHORT_URL, ok_result(image_only_html("失败不计费", 2)))
        vision = FakeVision(fail_n={1, 2})
        svc = _make_service(fetcher, vision=vision)
        _enqueue(tenant_id, [SHORT_URL])
        await svc.claim_and_run(tenant_id)
        assert _query_one(
            "SELECT COUNT(*) AS c FROM chat_records WHERE tenant_id = %s "
            "AND source_type = %s", (tenant_id, VISION_PARSE_SOURCE_TYPE))["c"] == 0
        assert _query_one(
            "SELECT COUNT(*) AS c FROM chat_records WHERE tenant_id = %s",
            (tenant_id,))["c"] == 0
