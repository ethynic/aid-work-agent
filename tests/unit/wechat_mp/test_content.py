"""content.py 单测：js_content 正文提取（夹具驱动）。

真实夹具：WP0 两篇公开营销文章（2026-09-14 重抓，见 fixtures README）。
"""

import pytest

from src.wechat_mp.content import (
    ContentExtractionError,
    extract_article,
    nodes_to_text,
)


class TestRealFixtures:
    @pytest.mark.parametrize("name,expect_form", [
        ("article_masssend_shortlink.html", "short"),
        ("article_freepublish.html", "long"),
    ])
    def test_extract(self, wechat_mp_fixtures, name, expect_form):
        html = (wechat_mp_fixtures / name).read_text(encoding="utf-8")
        article = extract_article(html)

        assert article.title == "爱定义：AI双擎驱动，您的数智化转型，自带AI加速度！"
        assert article.account_name == "爱定义 I AI企业落地"
        assert article.publish_time is not None
        assert article.publish_time.year == 2026

        # 保序序列：文字与图片交错，首节点为图（WP0 实测该文头图为首）
        assert article.nodes[0].type == "image"
        assert article.image_count == 17  # js_content 内 17 张（页外 QR/头像不混入）
        types = {n.type for n in article.nodes}
        assert types == {"text", "image"}

        # 图片节点取 data-src 原文 URL 占位
        for n in article.nodes:
            if n.type == "image":
                assert n.src.startswith("https://mmecoa.qpic.cn/")
                assert "wx_fmt=" in n.src  # &amp; 已反转义为 &（格式有 png/jpeg 混合）

        # 文本内容抽查（WP0 实测纯文字 2180 字，含图片占位后总长更大）
        text = article.text
        assert "智能推进器" in text
        assert text.count("[图片") == 17
        assert "[图片1]" in text and "[图片17]" in text
        assert len(text) > 2000

        # 别名：页面 msg_link 给出规范形态
        assert article.alias is not None
        assert article.alias.form == expect_form

    def test_two_samples_same_content_independent_articles(self, wechat_mp_fixtures):
        """两篇同主题文章（发布/群发各一篇）：各自独立身份，内容结构一致。"""
        a1 = extract_article((wechat_mp_fixtures / "article_masssend_shortlink.html").read_text(encoding="utf-8"))
        a2 = extract_article((wechat_mp_fixtures / "article_freepublish.html").read_text(encoding="utf-8"))
        assert a1.alias.external_id != a2.alias.external_id
        assert a1.text == a2.text  # 同内容两渠道各发一篇


class TestSynthetic:
    def test_quoting_deleted_phrase_article(self, wechat_mp_fixtures):
        html = (wechat_mp_fixtures / "article_quoting_deleted_phrase.html").read_text(encoding="utf-8")
        article = extract_article(html)
        assert article.title == "如何辨别文章是否被删除"
        assert "该内容已被发布者删除" in article.text  # 正文引用保留
        assert article.image_count == 1

    def test_missing_js_content_raises(self, wechat_mp_fixtures):
        html = (wechat_mp_fixtures / "deleted_page.html").read_text(encoding="utf-8")
        with pytest.raises(ContentExtractionError):
            extract_article(html)

    def test_empty_html_raises(self):
        with pytest.raises(ContentExtractionError):
            extract_article("")

    def test_empty_container_raises(self):
        html = '<div id="js_content">   </div>'
        with pytest.raises(ContentExtractionError):
            extract_article(html)

    def test_script_style_stripped(self):
        html = (
            '<div id="js_content"><p>第一段</p>'
            "<script>var x='不应出现';</script>"
            "<style>.a{color:red}</style>"
            "<p>第二段</p></div>"
        )
        article = extract_article(html)
        assert article.text == "第一段\n第二段"

    def test_block_order_preserved(self):
        html = (
            '<div id="js_content">'
            "<p>甲</p><section><span>乙</span></section>"
            '<p><img data-src="https://mmecoa.qpic.cn/a/640?wx_fmt=png"/></p>'
            "<p>丙</p></div>"
        )
        article = extract_article(html)
        # 相邻文本段合并为一个 text 节点（块级边界换行拼接），图片独立成节点
        assert [n.type for n in article.nodes] == ["text", "image", "text"]
        assert article.text == "甲\n乙\n[图片1]\n丙"

    def test_trailing_text_at_container_top_level(self):
        """CR P2-4：js_content 顶层尾部文本不得丢失。"""
        html = '<div id="js_content"><p>甲</p>尾部</div>'
        article = extract_article(html)
        assert article.text == "甲\n尾部"

    def test_leading_text_at_container_top_level(self):
        html = '<div id="js_content">开头<p>甲</p></div>'
        article = extract_article(html)
        assert article.text == "开头\n甲"

    def test_bare_text_only_container_not_empty(self):
        """纯文本（无任何块级标签）也是有效正文，不得误报空。"""
        html = '<div id="js_content">只有一句话的短文</div>'
        article = extract_article(html)
        assert article.text == "只有一句话的短文"

    def test_img_without_src_skipped(self):
        html = '<div id="js_content"><p>甲</p><p><img class="rich_pages"/></p><p>乙</p></div>'
        article = extract_article(html)
        assert article.image_count == 0
        assert article.text == "甲\n乙"

    def test_img_src_fallback_when_no_data_src(self):
        html = (
            '<div id="js_content"><p><img src="https://mmecoa.qpic.cn/a/640"/></p></div>'
        )
        article = extract_article(html)
        assert article.image_count == 1
        assert article.nodes[0].src == "https://mmecoa.qpic.cn/a/640"

    def test_surrogate_sanitized(self):
        html = '<div id="js_content"><p>正常文本\ud83c孤立代理</p></div>'
        article = extract_article(html)
        assert "\ud83c" not in article.text
        article.text.encode("utf-8")  # 不抛 UnicodeEncodeError

    def test_title_fallback_to_msg_title_var(self):
        html = (
            "<script>var msg_title = '备用标题'.html(false);</script>"
            '<div id="js_content"><p>正文</p></div>'
        )
        article = extract_article(html)
        assert article.title == "备用标题"

    def test_publish_time_fallback_create_time(self):
        html = (
            "<script>var createTime = '2026-09-14 19:20';</script>"
            '<div id="js_content"><p>正文</p></div>'
        )
        article = extract_article(html)
        assert article.publish_time is not None
        assert article.publish_time.hour == 11  # UTC+8 19:20 → UTC 11:20


class TestNodesToText:
    def test_placeholder_numbering(self):
        from src.wechat_mp.content import ContentNode

        nodes = [
            ContentNode(type="text", text="开头"),
            ContentNode(type="image", src="https://a"),
            ContentNode(type="image", src="https://b"),
            ContentNode(type="text", text="结尾"),
        ]
        assert nodes_to_text(nodes) == "开头\n[图片1]\n[图片2]\n结尾"
