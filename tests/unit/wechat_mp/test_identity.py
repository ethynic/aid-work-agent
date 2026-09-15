"""identity.py 单测：URL 规范化与身份算法（设计 §5.4）。

覆盖：撞键/不撞键/跟踪参数剔除/拒收/别名解析。纯逻辑，不依赖真实 DB。
"""

import pytest

from src.wechat_mp.identity import (
    URLIdentityError,
    extract_alias_url,
    normalize_url,
)

SHORT_URL = "http://mp.weixin.qq.com/s/h8NxFr8whGfZ-KjO_AQZUA"
LONG_URL = (
    "https://mp.weixin.qq.com/s?__biz=MzkzMDY5NTc4MA==&mid=2247483728&idx=1"
    "&sn=379f0f38594f4ea4da417a95d427add5"
)


class TestShortForm:
    def test_basic(self):
        ident = normalize_url(SHORT_URL)
        assert ident.form == "short"
        assert ident.external_id == "mp:s:h8NxFr8whGfZ-KjO_AQZUA"
        assert ident.fetch_url == "https://mp.weixin.qq.com/s/h8NxFr8whGfZ-KjO_AQZUA"
        assert ident.original_url == SHORT_URL

    def test_tracking_query_and_fragment_dropped(self):
        u1 = normalize_url(SHORT_URL)
        u2 = normalize_url(SHORT_URL + "?scene=21&clicktime=1700000000#wechat_redirect")
        u3 = normalize_url("https://mp.weixin.qq.com/s/h8NxFr8whGfZ-KjO_AQZUA?from=groupmessage#rd")
        assert u1.external_id == u2.external_id == u3.external_id
        assert u2.fetch_url == u1.fetch_url  # 短链 query 不进 fetch_url

    def test_missing_scheme_tolerated(self):
        ident = normalize_url("mp.weixin.qq.com/s/h8NxFr8whGfZ-KjO_AQZUA")
        assert ident.external_id == "mp:s:h8NxFr8whGfZ-KjO_AQZUA"

    def test_uppercase_host(self):
        ident = normalize_url("HTTPS://MP.WeiXin.QQ.Com/s/h8NxFr8whGfZ-KjO_AQZUA")
        assert ident.external_id == "mp:s:h8NxFr8whGfZ-KjO_AQZUA"

    @pytest.mark.parametrize("url", [
        "https://mp.weixin.qq.com/s/",                # 空 token
        "https://mp.weixin.qq.com/s/a b",             # 非法字符
        "https://mp.weixin.qq.com/s/abc",             # 过短
        "https://mp.weixin.qq.com/s/tok/en",          # 多段路径
    ])
    def test_invalid_short_rejected(self, url):
        with pytest.raises(URLIdentityError):
            normalize_url(url)


class TestLongForm:
    def test_basic(self):
        ident = normalize_url(LONG_URL)
        assert ident.form == "long"
        assert ident.external_id == "mp:q:MzkzMDY5NTc4MA==|2247483728|1|379f0f38594f4ea4da417a95d427add5"
        for param in ("__biz=MzkzMDY5NTc4MA%3D%3D", "mid=2247483728", "idx=1", "sn=379f0f38594f4ea4da417a95d427add5"):
            assert param in ident.fetch_url

    def test_tracking_params_dropped_but_collide(self):
        tracking = (
            "&scene=21&sessionid=svr_abc&subscene=10000&clicktime=1700000000"
            "&enterid=1700000000&exportkey=xxx&uin=MTIz&key=abc&pass_ticket=xyz"
            "&devicetype=iOS&version=18001030&lang=zh_CN&ascene=1&from=groupmessage"
        )
        u1 = normalize_url(LONG_URL)
        u2 = normalize_url(LONG_URL + tracking + "#wechat_redirect")
        assert u1.external_id == u2.external_id
        for banned in ("scene=", "sessionid=", "subscene=", "clicktime=", "enterid=",
                       "exportkey=", "pass_ticket=", "devicetype=", "version=",
                       "lang=", "ascene=", "from=", "uin="):
            assert banned not in u2.fetch_url
        # 注意 "key=" 是 pass_ticket 场景参数：fetch_url 中不得以独立参数出现
        assert "&key=" not in u2.fetch_url and not u2.fetch_url.startswith("https://mp.weixin.qq.com/s?key=")

    def test_chksm_kept_in_fetch_url_not_identity(self):
        u1 = normalize_url(LONG_URL)
        u2 = normalize_url(LONG_URL + "&chksm=c2771e5bf500974d")
        assert u1.external_id == u2.external_id  # chksm 不进身份
        assert "chksm=c2771e5bf500974d" in u2.fetch_url
        assert "chksm" not in u1.fetch_url

    def test_html_escaped_ampersand(self):
        ident = normalize_url(LONG_URL.replace("&", "&amp;"))
        assert ident.external_id == "mp:q:MzkzMDY5NTc4MA==|2247483728|1|379f0f38594f4ea4da417a95d427add5"

    def test_different_sub_article_not_collide(self):
        other = normalize_url(LONG_URL.replace("idx=1", "idx=2"))
        assert other.external_id != normalize_url(LONG_URL).external_id

    def test_different_sn_not_collide(self):
        other = normalize_url(LONG_URL.replace("379f0f", "aaaaaaa"))
        assert other.external_id != normalize_url(LONG_URL).external_id

    def test_short_and_long_same_article_not_collide(self):
        """短长链是不同身份，同文收敛靠抓取后别名（WP5），受理阶段不得撞键。"""
        assert normalize_url(SHORT_URL).external_id != normalize_url(LONG_URL).external_id

    @pytest.mark.parametrize("missing", ["__biz", "mid", "idx", "sn"])
    def test_missing_param_rejected(self, missing):
        params = {"__biz": "MzkzMDY5NTc4MA==", "mid": "2247483728", "idx": "1", "sn": "379f"}
        params[missing] = ""
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        with pytest.raises(URLIdentityError, match=missing):
            normalize_url(f"https://mp.weixin.qq.com/s?{qs}")


class TestReject:
    @pytest.mark.parametrize("url", [
        "", "   ",
        "https://example.com/s/abcDEF1234",                       # 非 mp 域
        "https://weixin110.qq.com/s/abcDEF1234",                  # 微信安全域也不收
        "https://mp.weixin.qq.com.evil.com/s/abcDEF1234",         # 后缀钓鱼域
        "http://192.168.1.1/s/abcDEF1234",                        # 内网 IP
        "https://mp.weixin.qq.com/mp/profile?action=home",        # 非 /s 路径
        "https://mp.weixin.qq.com/",                              # 根路径
        "ftp://mp.weixin.qq.com/s/h8NxFr8whGfZ-KjO_AQZUA",        # 非 http scheme
        "https://user:pw@mp.weixin.qq.com/s/h8NxFr8whGfZ-KjO_AQZUA",  # userinfo
        "https://mp.weixin.qq.com:8443/s/h8NxFr8whGfZ-KjO_AQZUA",     # 非标端口
        "not-a-url",                                              # 无 scheme 非 mp 开头
    ])
    def test_rejected(self, url):
        with pytest.raises(URLIdentityError):
            normalize_url(url)

    def test_error_message_mentions_domain(self):
        with pytest.raises(URLIdentityError, match="mp.weixin.qq.com"):
            normalize_url("https://example.com/s/abcDEF1234")


class TestAliasExtraction:
    def test_msg_link_short(self):
        html = '<script>var msg_link = "https://mp.weixin.qq.com/s/h8NxFr8whGfZ-KjO_AQZUA";</script>'
        alias = extract_alias_url(html)
        assert alias is not None
        assert alias.external_id == "mp:s:h8NxFr8whGfZ-KjO_AQZUA"

    def test_msg_link_long_escaped(self):
        html = (
            '<script>var msg_link = "https://mp.weixin.qq.com/s?__biz=MzkzMDY5NTc4MA=='
            "&amp;mid=2247483728&amp;idx=1&amp;sn=379f0f38594f4ea4da417a95d427add5"
            '&amp;chksm=c2771e5b";</script>'
        )
        alias = extract_alias_url(html)
        assert alias is not None
        assert alias.form == "long"
        assert alias.external_id == "mp:q:MzkzMDY5NTc4MA==|2247483728|1|379f0f38594f4ea4da417a95d427add5"

    def test_canonical_fallback(self):
        html = '<link rel="canonical" href="https://mp.weixin.qq.com/s/h8NxFr8whGfZ-KjO_AQZUA" />'
        alias = extract_alias_url(html)
        assert alias is not None
        assert alias.external_id == "mp:s:h8NxFr8whGfZ-KjO_AQZUA"

    def test_msg_link_wins_over_canonical(self):
        """CR P1-1：两者同时存在且都合法时 msg_link 胜出（msg_link 是权威来源）。"""
        html = (
            '<link rel="canonical" href="https://mp.weixin.qq.com/s/AAAAcanonical123" />'
            '<script>var msg_link = "https://mp.weixin.qq.com/s/h8NxFr8whGfZ-KjO_AQZUA";</script>'
        )
        alias = extract_alias_url(html)
        assert alias is not None
        assert alias.external_id == "mp:s:h8NxFr8whGfZ-KjO_AQZUA"

    def test_msg_link_invalid_falls_back_canonical(self):
        html = (
            '<link rel="canonical" href="https://mp.weixin.qq.com/s/AAAAcanonical123" />'
            '<script>var msg_link = "https://evil.example.com/x";</script>'
        )
        alias = extract_alias_url(html)
        assert alias is not None
        assert alias.external_id == "mp:s:AAAAcanonical123"

    def test_invalid_port_wrapped(self):
        """CR P2-5：非数字端口应抛 URLIdentityError，不得漏出原生 ValueError。"""
        with pytest.raises(URLIdentityError):
            normalize_url("https://mp.weixin.qq.com:abc/s/h8NxFr8whGfZ-KjO_AQZUA")

    def test_canonical_untrusted_falls_through_msg_link(self):
        html = (
            '<link rel="canonical" href="https://evil.example.com/x" />'
            '<script>var msg_link = "https://mp.weixin.qq.com/s/h8NxFr8whGfZ-KjO_AQZUA";</script>'
        )
        alias = extract_alias_url(html)
        assert alias is not None
        assert alias.external_id == "mp:s:h8NxFr8whGfZ-KjO_AQZUA"

    def test_no_source_returns_none(self):
        assert extract_alias_url("<html><body>no link</body></html>") is None
        assert extract_alias_url("") is None

    def test_real_fixtures(self, wechat_mp_fixtures):
        short_html = (wechat_mp_fixtures / "article_masssend_shortlink.html").read_text(encoding="utf-8")
        long_html = (wechat_mp_fixtures / "article_freepublish.html").read_text(encoding="utf-8")
        alias_short = extract_alias_url(short_html)
        alias_long = extract_alias_url(long_html)
        assert alias_short is not None and alias_short.form == "short"
        assert alias_long is not None and alias_long.form == "long"
