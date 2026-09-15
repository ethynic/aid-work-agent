"""WP3 独立验证探针（独立测试智能体补测，覆盖开发用例未覆盖的边界）。"""
import socket
from pathlib import Path

import pytest

from src.wechat_mp import fetcher
from src.wechat_mp.fetcher import MPArticleFetcher, classify_page, _is_public_ip, _resolve_all_public, _validate_hop_url
from src.wechat_mp.identity import URLIdentityError, normalize_url
from src.wechat_mp.content import extract_article

FIX = Path(__file__).parent.parent.parent / "fixtures" / "wechat_mp"
LONG = ("https://mp.weixin.qq.com/s?__biz=MzkzMDY5NTc4MA==&mid=2247483728"
        "&idx=1&sn=379f0f38594f4ea4da417a95d427add5")


class TestIdentityIndependent:
    def test_percent_encoded_identity_param_collides(self):
        a = normalize_url(LONG)
        b = normalize_url(LONG.replace("MzkzMDY5NTc4MA==", "MzkzMDY5NTc4MA%3D%3D"))
        assert a.external_id == b.external_id
        assert a.fetch_url == b.fetch_url

    def test_param_order_permutation_collides(self):
        shuffled = ("https://mp.weixin.qq.com/s?sn=379f0f38594f4ea4da417a95d427add5"
                    "&idx=1&__biz=MzkzMDY5NTc4MA==&mid=2247483728")
        assert normalize_url(shuffled).external_id == normalize_url(LONG).external_id

    def test_rare_tracking_params_dropped(self):
        extra = ("&wx_header=0&spm=1001&fontgear=1&vid=wxv_123&isappinstalled=0"
                 "&nettype=WIFI&abtest_cookie=AA==&poisearch=1&share_token=zzz"
                 "&utm_source=foo&trackid=123&__biz_ignore=&biz=evil")
        u = normalize_url(LONG + extra + "#wechat_redirect")
        for banned in ("wx_header", "spm", "fontgear", "vid", "isappinstalled",
                       "nettype", "abtest_cookie", "poisearch", "share_token",
                       "utm_source", "trackid"):
            assert banned not in u.fetch_url, banned
        assert "&biz=" not in u.fetch_url  # __biz= 合法保留，独立 biz= 必须剔除
        assert u.external_id == normalize_url(LONG).external_id

    def test_duplicate_identity_param_last_wins(self):
        """重复定位参数 dict 取后者——记录实际行为（idx=1&idx=2 → idx=2）。"""
        u = normalize_url(LONG + "&idx=2")
        assert "|2|" in u.external_id

    @pytest.mark.parametrize("tok,ok", [("abc", False), ("abcd", True),
                                        ("a" * 128, True), ("a" * 129, False)])
    def test_short_token_length_bounds(self, tok, ok):
        url = f"https://mp.weixin.qq.com/s/{tok}"
        if ok:
            assert normalize_url(url).external_id == f"mp:s:{tok}"
        else:
            with pytest.raises(URLIdentityError):
                normalize_url(url)

    def test_explicit_port_443_and_fragment_ok(self):
        u = normalize_url("https://mp.weixin.qq.com:443/s/abcdefGH#rd")
        assert u.external_id == "mp:s:abcdefGH"

    def test_uppercase_path_rejected(self):
        with pytest.raises(URLIdentityError):
            normalize_url("https://mp.weixin.qq.com/S/h8NxFr8whGfZ-KjO_AQZUA")

    def test_idx_leading_zero_not_normalized(self):
        """idx=01 与 idx=1 身份不同——记录未做数值归一的行为。"""
        assert normalize_url(LONG.replace("idx=1", "idx=01")).external_id != \
               normalize_url(LONG).external_id


class TestClassifyIndependent:
    def test_all_deleted_signals_but_js_content_present_is_ok(self):
        html = ('<div class="weui-msg__icon-area"></div>'
                '<p>该内容已被发布者删除</p>'
                '<div id="js_content"><p>正文仍在</p></div>')
        assert classify_page(html, 200).status == fetcher.STATUS_OK

    def test_error_dom_alone_not_deleted(self):
        html = '<div class="weui-msg__icon-area"><i class="weui-icon-warn"></i></div><p>系统繁忙</p>'
        r = classify_page(html, 200)
        assert r.status == fetcher.STATUS_FETCH_FAILED
        assert r.evidence["error_page_dom"] is True

    def test_verify_plus_deleted_signals_precedence(self):
        """验证页标记与三删除信号同时出现时的实际判级（deleted 分支在前）。"""
        html = ('<div class="weui-msg__icon-area"></div>'
                '<p>该内容已被发布者删除</p>'
                '<p>完成验证后即可继续访问</p>')
        r = classify_page(html, 200)
        # 记录实际行为：当前实现判 deleted（验证分支在后）
        assert r.status == fetcher.STATUS_DELETED
        assert r.evidence["verify_page"] is True

    def test_real_verify_page_evidence_flags(self):
        html = (FIX / "verify_page_real.html").read_text(encoding="utf-8")
        r = classify_page(html, 200)
        assert r.status == fetcher.STATUS_RISK_BLOCKED
        assert r.evidence["error_page_dom"] is False
        assert r.evidence["deleted_phrase"] is False
        assert r.evidence["has_js_content"] is False

    def test_quoting_fixture_ok(self):
        html = (FIX / "article_quoting_deleted_phrase.html").read_text(encoding="utf-8")
        assert classify_page(html, 200).status == fetcher.STATUS_OK

    def test_deleted_phrase_variants(self):
        for phrase in ("该内容已被删除", "此内容因违规无法查看"):
            html = f'<div class="weui-msg__icon-area"></div><p>{phrase}</p>'
            assert classify_page(html, 200).status == fetcher.STATUS_DELETED


class TestSSRFIndependent:
    @pytest.mark.parametrize("ip,expect_public", [
        ("10.0.0.1", False), ("172.16.5.5", False), ("192.168.0.1", False),
        ("127.0.0.1", False), ("169.254.169.254", False), ("::1", False),
        ("fe80::1", False), ("0.0.0.0", False), ("224.0.0.1", False),
        ("8.8.8.8", True), ("140.207.54.73", True),
    ])
    def test_is_public_ip(self, ip, expect_public):
        assert _is_public_ip(ip) is expect_public

    def test_resolve_all_public_mixed_rejected(self, monkeypatch):
        """任一解析结果为内网即拒（重绑定防护的真实逻辑）。"""
        monkeypatch.setattr(socket, "getaddrinfo", lambda h, p: [
            (None, None, None, None, ("140.207.54.73", 0)),
            (None, None, None, None, ("10.0.0.9", 0)),
        ])
        assert _resolve_all_public("mp.weixin.qq.com") is False

    def test_resolve_all_public_pure_public_ok(self, monkeypatch):
        monkeypatch.setattr(socket, "getaddrinfo", lambda h, p: [
            (None, None, None, None, ("140.207.54.73", 0)),
        ])
        assert _resolve_all_public("mp.weixin.qq.com") is True

    def test_resolve_failure_rejected(self, monkeypatch):
        monkeypatch.setattr(socket, "getaddrinfo",
                            lambda h, p: (_ for _ in ()).throw(socket.gaierror()))
        assert _resolve_all_public("mp.weixin.qq.com") is False

    @pytest.mark.parametrize("url,expect", [
        ("file:///etc/passwd", "scheme_file"),
        ("gopher://127.0.0.1/_x", "scheme_gopher"),
        ("https://user:pw@mp.weixin.qq.com/s/abc", "userinfo_not_allowed"),
        ("https://mp.weixin.qq.com:22/s/abc", "port_not_allowed"),
        ("https://mp.weixin.qq.com.evil.com/s/abc", "host_not_allowed"),
    ])
    def test_validate_hop_url_direct(self, url, expect):
        assert _validate_hop_url(url) == expect

    def test_trailing_dot_host_accepted(self, monkeypatch):
        monkeypatch.setattr(socket, "getaddrinfo", lambda h, p: [
            (None, None, None, None, ("140.207.54.73", 0))])
        assert _validate_hop_url("https://mp.weixin.qq.com./s/abc") is None

    def test_scheme_relative_redirect_to_evil_blocked(self):
        """//evil.com 协议相对重定向同样被逐跳校验拦截。"""
        class C:
            def __init__(self): self.n = 0
            def stream(self, method, url):
                self.n += 1
                class R:
                    status_code = 302
                    headers = {"location": "//evil.example.com/x"}
                    def __enter__(self): return self
                    def __exit__(self, *a): return False
                return R()
        r = MPArticleFetcher(client=C(), min_interval=0).fetch(
            "https://mp.weixin.qq.com/s/abcdefGH")
        assert r.error == "ssrf_blocked:host_not_allowed"


class TestContentIndependent:
    def test_real_fixture_numbers(self):
        html = (FIX / "article_masssend_shortlink.html").read_text(encoding="utf-8")
        art = extract_article(html)
        assert art.image_count == 17
        assert art.title == "爱定义：AI双擎驱动，您的数智化转型，自带AI加速度！"
        assert art.account_name == "爱定义 I AI企业落地"
        assert art.alias is not None
        assert art.alias.external_id == "mp:s:h8NxFr8whGfZ-KjO_AQZUA"
        pure_text = "\n".join(n.text for n in art.nodes if n.type == "text")
        assert len(pure_text) >= 2100  # WP0 实测纯文字 2180 字
        assert html.count("video_iframe") > 0
        assert "video" not in pure_text  # 视频 iframe 无占位，静默丢失

    def test_freepublish_alias_is_long_with_chksm(self):
        html = (FIX / "article_freepublish.html").read_text(encoding="utf-8")
        art = extract_article(html)
        assert art.alias.form == "long"
        assert "chksm" in art.alias.fetch_url
        assert "chksm" not in art.alias.external_id
