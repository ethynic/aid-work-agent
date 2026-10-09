"""完整 MD 不丢正文，发送预览只投影并遵守字符预算。"""

import pytest

from src.channels.wecom_kf.reply_format import (
    FILE_NOTICE,
    build_prefix_preview,
    inspect_reply_markdown,
    map_reply_image_sources,
    normalize_reply_markdown,
    reply_plain_text,
)


pytestmark = [pytest.mark.unit, pytest.mark.channels]


def test_standard_markdown_is_kept_verbatim_with_rendered_table():
    original = "# 查询结果\r\n\r\n| 姓名 | 金额 |\r\n| --- | --- |\r\n| 张三 | 12.50 |\r\n\r\n- 下一步：核对。\r\n\r\n```python\r\nprint('原代码')\r\n```\r\n"
    assert normalize_reply_markdown(original) == original
    assert inspect_reply_markdown(original) == (True, False)


def test_table_without_blank_line_is_repaired_without_losing_content():
    original = "您查询的记录：\n| 项目 | 金额 |\n| --- | --- |\n| 退款 | 18.23 |"
    normalized = normalize_reply_markdown(original)
    assert normalized == original.replace("记录：\n", "记录：\n\n")
    assert inspect_reply_markdown(normalized) == (True, False)
    assert "项目：退款；金额：18.23" in reply_plain_text(normalized)
    assert inspect_reply_markdown(original) == (True, False)


def test_extra_table_columns_fail_instead_of_silently_dropping_business_data():
    with pytest.raises(ValueError, match="超过表头"):
        normalize_reply_markdown("| 名称 | 金额 |\n| --- | --- |\n| 订单 | 12 | 不能丢掉的第三列 |")


def test_mismatched_header_separator_cannot_be_claimed_as_standard_table():
    with pytest.raises(ValueError, match="列数不一致"):
        normalize_reply_markdown("| 名称 | 金额 |\n| --- | --- | --- |\n| 订单 | 12 |")


def test_escaped_and_code_cell_pipes_remain_in_full_markdown():
    original = "| 命令 | 含义 |\n| --- | --- |\n| `a|b` | a\\|b |"
    normalized = normalize_reply_markdown(original)
    assert "`a\\|b`" in normalized
    assert "命令：a|b；含义：a|b" in reply_plain_text(normalized)


@pytest.mark.parametrize("fence", ["```", "~~~", "   ```"])
def test_code_samples_do_not_trigger_table_or_image_delivery(fence):
    sample = f"{fence}\n| 表头 |\n| --- |\n| 示例 |\n![示例](file_id:test)\n<img src='https://example.com/a.png'>\n{fence}\n\n`![示例](https://example.com/a.png)`"
    assert normalize_reply_markdown(sample) == sample
    assert inspect_reply_markdown(sample) == (False, False)
    assert "| 表头 |" in reply_plain_text(sample)


@pytest.mark.parametrize("source", [
    "> ```\n> ![示例](file_id:test)\n> ```",
    "- ```\n  ![示例](file_id:test)\n  ```",
    "<pre><code>\n| 例子 |\n| --- |\n![例子](x)\n</code></pre>",
    "`| 例子 |\n| --- |\n![例子](x)`",
])
def test_nested_and_html_code_samples_remain_content_instead_of_media(source):
    assert normalize_reply_markdown(source) == source
    assert inspect_reply_markdown(source) == (False, False)


@pytest.mark.parametrize("image", [
    "![截图](file_id:abc)",
    "![截图](https://example.com/image.png)",
    "![截图][screen]\n\n[screen]: https://example.com/image.png",
    "<img src='file_id:abc' alt='截图'>",
])
def test_actual_body_image_is_detected_with_existing_reference_syntax(image):
    assert inspect_reply_markdown(image) == (False, True)


def test_html_table_is_projected_as_fields_in_source_order():
    source = "您好。\n\n<table><tr><th>产品</th><th>价格</th></tr><tr><td>茶</td><td>12</td></tr></table>\n\n请确认。"
    assert inspect_reply_markdown(source) == (True, False)
    plain = reply_plain_text(source)
    assert plain.index("您好") < plain.index("产品：茶；价格：12") < plain.index("请确认")
    assert "<table" not in plain


def test_plain_projection_preserves_opening_lists_links_and_code_literals():
    source = "**您好**，请按以下步骤处理：\n\n1. 打开[帮助](https://example.com/help)。\n2. 核对订单。\n\n```text\na | b\n```"
    plain = reply_plain_text(source)
    assert plain.startswith("您好，请按以下步骤处理：")
    assert "1. 打开帮助（https://example.com/help）。\n2. 核对订单。" in plain
    assert "a | b" in plain
    assert "```" not in plain


def test_short_projection_does_not_pretend_to_omit_content():
    preview = build_prefix_preview("您好，办理已完成。")
    assert preview == "您好，办理已完成。" + FILE_NOTICE
    assert "…" not in preview


def test_total_character_budget_includes_notice_and_ellipsis_not_bytes():
    source = "您" * 1000
    preview = build_prefix_preview(source)
    assert len(preview) == 500
    assert preview == source[:500 - len(FILE_NOTICE) - 1] + "…" + FILE_NOTICE
    assert len(preview.encode("utf-8")) > 500


def test_prefix_prefers_last_complete_sentence_and_never_rewrites_style():
    source = "您好，先核对订单。" + "您可以联系客户经理。" * 30 + "长句" * 300
    preview = build_prefix_preview(source)
    assert preview.startswith("您好，先核对订单。")
    assert preview.endswith("。…" + FILE_NOTICE)
    assert len(preview) <= 500


def test_prefix_uses_table_field_projection_and_preserves_full_source():
    source = "您好，记录如下：\n\n| 产品 | 金额 |\n| --- | --- |\n" + "| 茶 | 18.50 |\n" * 100
    full = normalize_reply_markdown(source)
    preview = build_prefix_preview(full)
    assert "产品：茶；金额：18.50" in preview
    assert "|" not in preview
    assert len(preview) <= 500
    assert full == source
    assert full.count("| 茶 | 18.50 |") == 100


def test_decimal_and_url_periods_do_not_create_false_sentence_boundaries():
    source = "售价 18.50，详情 https://example.com/item. " + "说明" * 300
    preview = build_prefix_preview(source, max_chars=100)
    budget = 100 - len(FILE_NOTICE) - 1
    assert preview == source[:budget] + "…" + FILE_NOTICE


def test_truncation_does_not_detach_combining_character():
    budget = 500 - len(FILE_NOTICE) - 1
    source = "字" * (budget - 1) + "e\u0301" + "字" * 600
    preview = build_prefix_preview(source)
    assert preview == "字" * (budget - 1) + "…" + FILE_NOTICE


def test_empty_or_failed_conversion_has_fixed_notice_without_model(monkeypatch):
    assert build_prefix_preview("") == FILE_NOTICE.strip()
    from src.channels.wecom_kf import reply_format

    def fail(_):
        raise ValueError("无法转换")

    monkeypatch.setattr(reply_format, "reply_plain_text", fail)
    assert build_prefix_preview("原文仍由完整 MD 保存") == FILE_NOTICE.strip()


def test_image_mapping_preserves_complete_body_and_authorized_target():
    source = '# 答复\n\n正文。\n\n![截图](file_id:abc "业务标题")\n\n结束。'
    result = map_reply_image_sources(source, lambda src: "https://example.com/download/abc" if src == "file_id:abc" else None)
    assert result == source.replace('(file_id:abc "业务标题")', '(<https://example.com/download/abc> "业务标题")')
    assert inspect_reply_markdown(result) == (False, True)


@pytest.mark.parametrize("target", ["/srv/private/photo.png", "file_id:unknown", "C:/private/photo.png"])
def test_unavailable_images_become_readable_without_leaking_target(target):
    source = f"正文。\n\n![订单截图]({target})"
    result = map_reply_image_sources(source, lambda src: None)
    assert result == "正文。\n\n订单截图（图片暂无法在文件中显示）"
    assert target not in result
    assert inspect_reply_markdown(result) == (False, False)


def test_reference_images_are_authorized_and_unsafe_definitions_removed():
    source = "![订单截图][photo]\n\n[photo]: file_id:abc \"订单\""
    mapped = map_reply_image_sources(source, lambda src: "https://example.com/download/abc")
    assert mapped == '![订单截图](<https://example.com/download/abc> "订单")\n\n[photo]: <https://example.com/download/abc> "订单"'
    assert inspect_reply_markdown(mapped) == (False, True)
    rejected = map_reply_image_sources(source, lambda src: None)
    assert rejected.startswith("订单截图（图片暂无法在文件中显示）")
    assert "file_id:" not in rejected


@pytest.mark.parametrize("syntax", ["![photo][]", "![photo]"])
def test_shortcut_and_collapsed_reference_images_use_authorized_target(syntax):
    source = syntax + "\n\n[photo]: file_id:abc"
    result = map_reply_image_sources(source, lambda src: "https://example.com/image.png")
    assert result.startswith("![photo](<https://example.com/image.png>)")
    assert "file_id:" not in result


def test_html_image_mapping_keeps_alt_and_removes_server_path():
    source = "<img src='/srv/private.png' alt='订单截图'>"
    assert map_reply_image_sources(source, lambda src: "https://example.com/image.png") == '<img src="https://example.com/image.png" alt="订单截图">'
    assert map_reply_image_sources(source, lambda src: None) == "订单截图（图片暂无法在文件中显示）"


def test_code_images_and_escaped_examples_are_untouched_by_mapping():
    source = "```md\n![代码](file_id:literal)\n```\n\n`![行内](file_id:literal)`\n\n<pre><code><img src='/srv/literal'></code></pre>\n\n\\![转义](file_id:literal)\n\n![正文](file_id:actual)"
    seen = []

    def resolver(src):
        seen.append(src)
        return "https://example.com/actual.png"

    result = map_reply_image_sources(source, resolver)
    assert seen == ["file_id:actual"]
    assert result == source.replace("![正文](file_id:actual)", "![正文](<https://example.com/actual.png>)")


def test_list_body_image_is_mapped_but_indented_code_is_not():
    source = "- 请查看截图：\n\n    ![正文](file_id:actual)\n\n结束。\n\n    ![代码](file_id:literal)"
    seen = []

    def resolver(src):
        seen.append(src)
        return "https://example.com/image.png"

    result = map_reply_image_sources(source, resolver)
    assert seen == ["file_id:actual"]
    assert result == source.replace("![正文](file_id:actual)", "![正文](<https://example.com/image.png>)")


@pytest.mark.parametrize("unsafe", ["javascript:alert(1)", "file:///srv/a.png", "https://user:password@example.com/a.png", "https://example.com/a b.png"])
def test_mapper_rejects_nonstandard_or_credentialed_resolver_output(unsafe):
    result = map_reply_image_sources("![截图](file_id:abc)", lambda src: unsafe)
    assert result == "截图（图片暂无法在文件中显示）"


def test_parentheses_in_remote_image_url_are_preserved_using_standard_destination():
    source = "![截图](https://example.com/image(1).png)"
    result = map_reply_image_sources(source, lambda src: src)
    assert result == "![截图](<https://example.com/image(1).png>)"
    assert inspect_reply_markdown(result) == (False, True)
