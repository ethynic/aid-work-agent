"""LLM 输出文本净化器单元测试

背景：线上案例 tr_45b66335492c4540，模型把整段回复（含表格）包进引用块，
wecom_kf 渠道表格检测失败，微信侧收到原始 `>`/`|` 文本而非长图。
"""
import pytest

pytestmark = pytest.mark.unit

from src.core.llm_output_sanitizer import (
    sanitize_llm_markdown,
    strip_blockquote_markers,
)


# 线上实际案例形态：首尾正常、中间整块被 `>` 包裹、表格行带 `>` 前缀
_PROD_LIKE_OUTPUT = """好的，那我细化一下每天的安排：

> ## 平塘天眼+荔波小七孔5天4晚行程
>
> **出发日期**：2026年9月20日
>
> **团队构成**：3位成人
>
> | 天数 | 时段 | 行程安排 | 备注 |
> |------|------|----------|------|
> | D1 | 下午 | 贵阳接站，前往平塘 | 约2.5-3小时车程 |
> | D2 | 上午 | 中国天眼科普基地 | 讲解按1-20人小团 |
> | D5 | 傍晚 | 贵阳送站，行程结束 | — |

这个详细版你看看行不行？没问题的话我出成 Word 给你。"""

_PROD_LIKE_EXPECTED = """好的，那我细化一下每天的安排：

## 平塘天眼+荔波小七孔5天4晚行程

**出发日期**：2026年9月20日

**团队构成**：3位成人

| 天数 | 时段 | 行程安排 | 备注 |
|------|------|----------|------|
| D1 | 下午 | 贵阳接站，前往平塘 | 约2.5-3小时车程 |
| D2 | 上午 | 中国天眼科普基地 | 讲解按1-20人小团 |
| D5 | 傍晚 | 贵阳送站，行程结束 | — |

这个详细版你看看行不行？没问题的话我出成 Word 给你。"""


class TestSanitizeLlmMarkdown:
    def test_prod_case_whole_block_wrap(self):
        """线上案例：中间整块引用包裹（含表格）→ 全部剥离"""
        assert sanitize_llm_markdown(_PROD_LIKE_OUTPUT) == _PROD_LIKE_EXPECTED

    def test_every_line_quoted(self):
        """所有非空行都带 `>` → 全部剥离"""
        md = "> ## 标题\n>\n> 正文内容\n> - 列表项"
        assert sanitize_llm_markdown(md) == "## 标题\n\n正文内容\n- 列表项"

    def test_nested_blockquote(self):
        """整段嵌套引用 `> >` 一并剥离"""
        md = "> > ## 标题\n>\n> > 正文内容"
        assert sanitize_llm_markdown(md) == "## 标题\n\n正文内容"

    def test_table_only_quoted_in_long_reply(self):
        """长回复中仅表格被引用包裹：未达整段阈值，但表格行仍被救援"""
        body_lines = [f"第{i}行普通内容" for i in range(1, 21)]
        md = "\n".join(body_lines) + "\n\n" + "\n".join(
            [
                "> | A | B |",
                "> |---|---|",
                "> | 1 | 2 |",
            ]
        )
        sanitized = sanitize_llm_markdown(md)
        assert "> | A | B |" not in sanitized
        assert "| A | B |" in sanitized
        assert "第1行普通内容" in sanitized

    def test_intentional_short_quote_preserved(self):
        """有意义的局部引用（少量行）保持原样"""
        md = """这是我的看法：

> 引用的一句原话

以上就是全部内容。"""
        assert sanitize_llm_markdown(md) == md

    def test_plain_markdown_untouched(self):
        """正常 markdown（含表格/标题/代码）原样返回"""
        md = """## 标题

| A | B |
|---|---|
| 1 | 2 |

```python
print("hello > world")
```

普通段落，包含 > 字符但不是行首引用。"""
        assert sanitize_llm_markdown(md) == md

    def test_empty_and_none_like(self):
        assert sanitize_llm_markdown("") == ""
        assert sanitize_llm_markdown("纯文本") == "纯文本"
        assert sanitize_llm_markdown("\n\n") == "\n\n"

    def test_no_gt_char_fast_path(self):
        """不含 `>` 的文本直接返回同一内容（快速路径）"""
        assert sanitize_llm_markdown("你好\n世界") == "你好\n世界"

    def test_idempotent(self):
        once = sanitize_llm_markdown(_PROD_LIKE_OUTPUT)
        assert sanitize_llm_markdown(once) == once

    def test_quoted_separator_and_no_space_marker(self):
        """分隔行 `> |---|` 与无空格 `>|A|B|` 均被救援"""
        md = "说明文字\n\n>|A|B|\n>|---|\n>|1|2|\n\n结尾"
        sanitized = sanitize_llm_markdown(md)
        assert ">|" not in sanitized
        assert "|A|B|" in sanitized


class TestStripBlockquoteMarkers:
    def test_strip_all_lines(self):
        assert strip_blockquote_markers("> a\n> b\n>") == "a\nb\n"

    def test_keep_unquoted_lines(self):
        assert strip_blockquote_markers("> a\nb") == "a\nb"

    def test_empty(self):
        assert strip_blockquote_markers("") == ""
