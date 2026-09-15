"""fetcher.py 单测：页面多信号定性 + SSRF 拦截 + 限速与大小上限。

网络全部走注入的假 client（httpx.Client 兼容），不打真实外网。
"""

import threading
import time

import pytest

from src.wechat_mp import fetcher
from src.wechat_mp.fetcher import (
    FetchResult,
    MPArticleFetcher,
    classify_page,
)

ARTICLE_HTML = '<html><body><div id="js_content"><p>正文</p></div></body></html>'


class FakeStreamResponse:
    def __init__(self, status_code=200, body=b"", headers=None):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self._body

    def iter_bytes(self, chunk_size=65536):
        yield self._body


class FakeClient:
    """按 URL 前缀路由到预置响应；记录请求历史。"""

    def __init__(self, routes):
        self.routes = routes  # list[(url_contains, FakeStreamResponse)]
        self.requests = []

    def stream(self, method, url):
        self.requests.append(url)
        for prefix, resp in self.routes:
            if prefix in url:
                return resp
        return FakeStreamResponse(404, b"not found")


def make_fetcher(routes, **kwargs):
    kwargs.setdefault("min_interval", 0)
    # 注入恒真 resolver：FakeClient 用例统一隔离真实 DNS（CR P2-3）；
    # DNS 重绑定用例显式覆盖 resolver=False
    kwargs.setdefault("resolver", lambda host: True)
    return MPArticleFetcher(client=FakeClient(routes), **kwargs)


class TestClassifyPage:
    def test_ok(self):
        r = classify_page(ARTICLE_HTML, 200)
        assert r.status == fetcher.STATUS_OK
        assert r.evidence["has_js_content"] is True

    def test_deleted_multi_signal(self, wechat_mp_fixtures):
        html = (wechat_mp_fixtures / "deleted_page.html").read_text(encoding="utf-8")
        r = classify_page(html, 200)
        assert r.status == fetcher.STATUS_DELETED
        assert r.evidence["error_page_dom"] is True
        assert r.evidence["has_js_content"] is False
        assert r.evidence["deleted_phrase"] is True

    def test_deleted_old_style_dom(self):
        """旧样式 page_msg 错误页 + 删除文案 + 无 js_content → deleted。"""
        html = (
            '<div class="page_msg minipage simple_default"><div class="msg_icon_wrp">'
            '<span class="icon_msg warn"></span></div>'
            '<div class="msg_content"><h4>该内容已被发布者删除</h4></div></div>'
        )
        assert classify_page(html, 200).status == fetcher.STATUS_DELETED

    def test_phrase_alone_not_deleted(self):
        """只有删除文案、无错误页 DOM → 不判删除（防正文引用误删）。"""
        html = "<html><body><p>该内容已被发布者删除</p></body></html>"
        r = classify_page(html, 200)
        assert r.status == fetcher.STATUS_FETCH_FAILED

    def test_live_article_quoting_phrase_not_deleted(self, wechat_mp_fixtures):
        html = (wechat_mp_fixtures / "article_quoting_deleted_phrase.html").read_text(encoding="utf-8")
        r = classify_page(html, 200)
        assert r.status == fetcher.STATUS_OK
        assert r.evidence["deleted_phrase"] is True  # 文案在正文里，但 js_content 存在

    def test_verify_page_real_fixture(self, wechat_mp_fixtures):
        html = (wechat_mp_fixtures / "verify_page_real.html").read_text(encoding="utf-8")
        r = classify_page(html, 200)
        assert r.status == fetcher.STATUS_RISK_BLOCKED
        assert r.evidence["verify_page"] is True

    def test_verify_text_variant(self):
        """CR P1-2：通用风控文案单独出现（无结构信号）不再判 risk_blocked，落 fetch_failed。"""
        html = "<html><body><p>当前环境异常，完成验证后即可继续访问</p></body></html>"
        r = classify_page(html, 200)
        assert r.status == fetcher.STATUS_FETCH_FAILED
        assert r.evidence["verify_page"] is True        # 原始信号仍记录供审计
        assert r.evidence["verify_structural"] is False

    def test_verify_structural_and_text_combined(self):
        html = ("<html><body><script>var PAGE_MID='mmbizwap:secitptpage/verify.html';</script>"
                "<p>当前环境异常，完成验证后即可继续访问</p></body></html>")
        r = classify_page(html, 200)
        assert r.status == fetcher.STATUS_RISK_BLOCKED

    def test_verify_structural_without_text(self):
        """结构信号本身（secitptpage/poc_token）不会出现在正常文章，单独即可判验证页。"""
        html = ("<html><body><script>var PAGE_MID='mmbizwap:secitptpage/verify.html';"
                "var d={poc_token:'abc'};</script></body></html>")
        assert classify_page(html, 200).status == fetcher.STATUS_RISK_BLOCKED

    def test_live_article_discussing_risk_control_ok(self):
        """CR P1-2 负样本：js_content 存在 + 正文含"环境异常" → ok，不永久重试。"""
        html = ('<div id="js_content"><p>近期不少账号遇到"当前环境异常，'
                '完成验证后即可继续访问"的提示，本文讲解如何应对风控。</p></div>')
        r = classify_page(html, 200)
        assert r.status == fetcher.STATUS_OK

    def test_unrecognized_not_deleted(self, wechat_mp_fixtures):
        html = (wechat_mp_fixtures / "unrecognized_page.html").read_text(encoding="utf-8")
        r = classify_page(html, 200)
        assert r.status == fetcher.STATUS_FETCH_FAILED
        assert r.error == "unrecognized_page_structure"

    def test_real_articles_ok(self, wechat_mp_fixtures):
        for name in ("article_masssend_shortlink.html", "article_freepublish.html"):
            html = (wechat_mp_fixtures / name).read_text(encoding="utf-8")
            assert classify_page(html, 200).status == fetcher.STATUS_OK

    @pytest.mark.parametrize("status", [403, 404, 500, 502])
    def test_non_200_fetch_failed(self, status):
        r = classify_page("", status)
        assert r.status == fetcher.STATUS_FETCH_FAILED
        assert r.error == f"http_{status}"


class TestFetchPipeline:
    URL = "https://mp.weixin.qq.com/s/h8NxFr8whGfZ-KjO_AQZUA"

    def test_ok_end_to_end(self):
        f = make_fetcher([("/s/", FakeStreamResponse(200, ARTICLE_HTML.encode("utf-8")))])
        r = f.fetch(self.URL)
        assert r.status == fetcher.STATUS_OK
        assert r.html == ARTICLE_HTML
        assert r.final_url == self.URL

    def test_reject_non_mp_url_no_request(self):
        client = FakeClient([])
        f = MPArticleFetcher(client=client, min_interval=0, resolver=lambda h: True)
        r = f.fetch("https://evil.example.com/s/abcDEF1234")
        assert r.status == fetcher.STATUS_FETCH_FAILED
        assert r.error.startswith("url_rejected:")
        assert client.requests == []  # 拒收在发请求之前

    def test_redirect_to_intranet_blocked(self):
        """SSRF：重定向到内网 IP/其它域，逐跳校验拦截，不发起第二跳请求。"""
        client = FakeClient([
            ("/s/", FakeStreamResponse(302, headers={"location": "http://192.168.1.1/internal/admin"})),
        ])
        f = MPArticleFetcher(client=client, min_interval=0, resolver=lambda h: True)
        r = f.fetch(self.URL)
        assert r.status == fetcher.STATUS_FETCH_FAILED
        assert r.error == "ssrf_blocked:host_not_allowed"
        assert r.evidence["reason"] == "ssrf_blocked"
        assert client.requests == ["https://mp.weixin.qq.com/s/h8NxFr8whGfZ-KjO_AQZUA"]

    def test_redirect_to_loopback_blocked(self):
        client = FakeClient([
            ("/s/", FakeStreamResponse(301, headers={"location": "http://127.0.0.1:8080/"})),
        ])
        r = MPArticleFetcher(client=client, min_interval=0, resolver=lambda h: True).fetch(self.URL)
        assert r.status == fetcher.STATUS_FETCH_FAILED
        assert r.error.startswith("ssrf_blocked:")

    def test_redirect_non_http_scheme_blocked(self):
        client = FakeClient([
            ("/s/", FakeStreamResponse(302, headers={"location": "file:///etc/passwd"})),
        ])
        r = MPArticleFetcher(client=client, min_interval=0, resolver=lambda h: True).fetch(self.URL)
        assert r.error == "ssrf_blocked:scheme_file"

    def test_dns_rebinding_blocked(self):
        """host 合法但 DNS 解析到内网（重绑定）→ 拦截（注入 resolver=False）。"""
        client = FakeClient([("/s/", FakeStreamResponse(200, ARTICLE_HTML.encode()))])
        r = MPArticleFetcher(client=client, min_interval=0,
                             resolver=lambda h: False).fetch(self.URL)
        assert r.error == "ssrf_blocked:dns_not_public"
        assert client.requests == []

    def test_redirect_invalid_port_blocked(self):
        """CR P2-5：重定向到非法端口（:abc）应归为 ssrf_blocked 而非 internal。"""
        client = FakeClient([
            ("/s/", FakeStreamResponse(302, headers={"location": "https://mp.weixin.qq.com:abc/s/x"})),
        ])
        r = MPArticleFetcher(client=client, min_interval=0,
                             resolver=lambda h: True).fetch(self.URL)
        assert r.error == "ssrf_blocked:port_invalid"

    def test_redirect_same_host_allowed(self):
        """mp 域内重定向放行（真实场景 http→https / 短链跳参数形态）。"""
        target = "https://mp.weixin.qq.com/s?__biz=Mz==&mid=1&idx=1&sn=abc"
        # routes 顺序敏感：/s?__biz 必须排在 /s/h8 之前精确匹配
        client = FakeClient([
            ("/s?__biz", FakeStreamResponse(200, ARTICLE_HTML.encode())),
            ("/s/h8", FakeStreamResponse(302, headers={"location": target})),
        ])
        r = MPArticleFetcher(client=client, min_interval=0, resolver=lambda h: True).fetch(self.URL)
        assert r.status == fetcher.STATUS_OK
        assert r.final_url == target
        assert r.evidence["redirect_chain"] == [302]

    def test_too_many_redirects(self):
        client = FakeClient([
            ("/s/", FakeStreamResponse(302, headers={"location": self.URL})),
        ])
        r = MPArticleFetcher(client=client, min_interval=0, resolver=lambda h: True).fetch(self.URL)
        assert r.error == "too_many_redirects"

    def test_page_too_large(self):
        big = b"x" * (1024 * 1024)  # 1MB，fetcher 上限设 100KB
        client = FakeClient([("/s/", FakeStreamResponse(200, big))])
        r = MPArticleFetcher(client=client, min_interval=0, max_bytes=100 * 1024, resolver=lambda h: True).fetch(self.URL)
        assert r.status == fetcher.STATUS_FETCH_FAILED
        assert r.error == "page_too_large"

    def test_content_length_precheck(self):
        big = b"x" * 10
        client = FakeClient([
            ("/s/", FakeStreamResponse(200, big, headers={"content-length": str(200 * 1024)})),
        ])
        r = MPArticleFetcher(client=client, min_interval=0, max_bytes=100 * 1024, resolver=lambda h: True).fetch(self.URL)
        assert r.error == "page_too_large"

    def test_http_500(self):
        client = FakeClient([("/s/", FakeStreamResponse(500, b"err"))])
        r = MPArticleFetcher(client=client, min_interval=0, resolver=lambda h: True).fetch(self.URL)
        assert r.status == fetcher.STATUS_FETCH_FAILED
        assert r.error == "http_500"

    def test_network_exception_converged(self):
        import httpx

        class BoomClient:
            def stream(self, method, url):
                raise httpx.ConnectError("boom")

        r = MPArticleFetcher(client=BoomClient(), min_interval=0, resolver=lambda h: True).fetch(self.URL)
        assert r.status == fetcher.STATUS_FETCH_FAILED
        assert r.error == "network:ConnectError"

    def test_timeout_converged(self):
        import httpx

        class SlowClient:
            def stream(self, method, url):
                raise httpx.ReadTimeout("slow")

        r = MPArticleFetcher(client=SlowClient(), min_interval=0, resolver=lambda h: True).fetch(self.URL)
        assert r.error == "timeout"


class TestThrottle:
    def test_min_interval_enforced(self):
        f = make_fetcher([("/s/", FakeStreamResponse(200, ARTICLE_HTML.encode()))],
                         min_interval=0.15)
        start = time.monotonic()
        f.fetch(TestFetchPipeline.URL)
        f.fetch(TestFetchPipeline.URL)
        elapsed = time.monotonic() - start
        assert elapsed >= 0.15

    def test_thread_safe(self):
        f = make_fetcher([("/s/", FakeStreamResponse(200, ARTICLE_HTML.encode()))],
                         min_interval=0.02)
        errors = []

        def worker():
            try:
                f.fetch(TestFetchPipeline.URL)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        start = time.monotonic()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        assert time.monotonic() - start >= 0.02 * 3
