"""微信客服消息格式转换单元测试"""
import pytest

pytestmark = pytest.mark.channels


class TestSegmentMarkdown:
    def test_empty_input(self):
        from src.channels.wecom_kf.message import segment_markdown
        assert segment_markdown("") == []
        assert segment_markdown(None) == []

    def test_plain_text_only(self):
        from src.channels.wecom_kf.message import segment_markdown
        blocks = segment_markdown("你好，这是一段纯文本。")
        assert len(blocks) == 1
        assert blocks[0].type == "text"
        assert blocks[0].content == "你好，这是一段纯文本。"

    def test_text_with_headings_and_lists(self):
        from src.channels.wecom_kf.message import segment_markdown
        md = """## 标题

- 项目1
- 项目2

这是正文内容。"""
        blocks = segment_markdown(md)
        assert len(blocks) == 1
        assert blocks[0].type == "text"

    def test_single_table(self):
        from src.channels.wecom_kf.message import segment_markdown
        md = """| 姓名 | 年龄 | 城市 |
|------|------|------|
| 张三 | 28 | 北京 |
| 李四 | 32 | 上海 |"""
        blocks = segment_markdown(md)
        assert len(blocks) == 1
        assert blocks[0].type == "table"
        assert "张三" in blocks[0].content
        assert blocks[0].meta["rows"] == 2  # 2 data rows
        assert blocks[0].meta["cols"] == 3

    def test_text_before_table(self):
        from src.channels.wecom_kf.message import segment_markdown
        md = """以下是查询结果：

| 姓名 | 年龄 |
|------|------|
| 张三 | 28 |"""
        blocks = segment_markdown(md)
        assert len(blocks) == 2
        assert blocks[0].type == "text"
        assert "查询结果" in blocks[0].content
        assert blocks[1].type == "table"

    def test_text_after_table(self):
        from src.channels.wecom_kf.message import segment_markdown
        md = """| 姓名 | 年龄 |
|------|------|
| 张三 | 28 |

以上是全部结果。"""
        blocks = segment_markdown(md)
        assert len(blocks) == 2
        assert blocks[0].type == "table"
        assert blocks[1].type == "text"
        assert "全部结果" in blocks[1].content

    def test_text_surrounding_table(self):
        from src.channels.wecom_kf.message import segment_markdown
        md = """以下是查询结果：

| 姓名 | 年龄 |
|------|------|
| 张三 | 28 |

共找到 1 条记录。"""
        blocks = segment_markdown(md)
        assert len(blocks) == 3
        assert blocks[0].type == "text"
        assert blocks[1].type == "table"
        assert blocks[2].type == "text"

    def test_multiple_tables(self):
        from src.channels.wecom_kf.message import segment_markdown
        md = """| A | B |
|---|---|
| 1 | 2 |

中间文字

| X | Y |
|---|---|
| 3 | 4 |"""
        blocks = segment_markdown(md)
        assert len(blocks) == 3
        assert blocks[0].type == "table"
        assert blocks[1].type == "text"
        assert "中间文字" in blocks[1].content
        assert blocks[2].type == "table"

    def test_table_without_separator(self):
        from src.channels.wecom_kf.message import segment_markdown
        md = """| 姓名 | 年龄 | 城市 |
| 张三 | 28 | 北京 |
| 李四 | 32 | 上海 |"""
        blocks = segment_markdown(md)
        assert len(blocks) == 1
        assert blocks[0].type == "table"
        # 应该自动补上分隔行
        assert "---" in blocks[0].content

    def test_standalone_link(self):
        from src.channels.wecom_kf.message import segment_markdown
        blocks = segment_markdown("[点击查看](https://example.com/doc)")
        assert len(blocks) == 1
        assert blocks[0].type == "link"
        assert blocks[0].meta["url"] == "https://example.com/doc"
        assert blocks[0].meta["title"] == "点击查看"

    def test_standalone_pure_url(self):
        from src.channels.wecom_kf.message import segment_markdown
        blocks = segment_markdown("https://example.com/page")
        assert len(blocks) == 1
        assert blocks[0].type == "link"
        assert blocks[0].meta["url"] == "https://example.com/page"

    def test_link_with_text_not_standalone(self):
        from src.channels.wecom_kf.message import segment_markdown
        md = """请 [点击查看](https://example.com/doc) 详情。"""
        blocks = segment_markdown(md)
        # 链接嵌入在文本中，不应拆成 link 块
        assert all(b.type == "text" for b in blocks)

    def test_code_block_not_table(self):
        from src.channels.wecom_kf.message import segment_markdown
        md = """```python
print("hello")
```"""
        blocks = segment_markdown(md)
        assert len(blocks) == 1
        assert blocks[0].type == "text"


class TestTableToPlainText:
    def test_basic_table(self):
        from src.channels.wecom_kf.message import table_to_plain_text
        md = """| Name | Age | City |
|------|-----|------|
| Alice | 25 | Beijing |
| Bob | 30 | Shanghai |"""
        result = table_to_plain_text(md)
        assert "[表格]" in result
        assert "Name | Age | City" in result
        assert "Alice | 25 | Beijing" in result
        assert "Bob | 30 | Shanghai" in result
        assert "---" not in result  # 分隔行应该被移除

    def test_table_without_separator(self):
        from src.channels.wecom_kf.message import table_to_plain_text
        md = """| 姓名 | 年龄 |
| 张三 | 28 |
| 李四 | 32 |"""
        result = table_to_plain_text(md)
        assert "[表格]" in result
        assert "姓名 | 年龄" in result


class TestMarkdownToPlainText:
    def test_bold_removal(self):
        from src.channels.wecom_kf.message import markdown_to_plain_text
        assert markdown_to_plain_text("**加粗**") == "加粗"

    def test_link_conversion(self):
        from src.channels.wecom_kf.message import markdown_to_plain_text
        result = markdown_to_plain_text("[百度](https://baidu.com)")
        assert "百度" in result
        assert "baidu.com" in result

    def test_heading_removal(self):
        from src.channels.wecom_kf.message import markdown_to_plain_text
        assert markdown_to_plain_text("## 标题") == "标题"

    def test_table_separator_removal(self):
        from src.channels.wecom_kf.message import markdown_to_plain_text
        md = """| A | B |
|---|---|
| 1 | 2 |"""
        result = markdown_to_plain_text(md)
        # 分隔行被移除，但保留表头和内容
        assert "A" in result
        assert "B" in result
        assert "1" in result
        assert "2" in result

    def test_list_preservation(self):
        from src.channels.wecom_kf.message import markdown_to_plain_text
        result = markdown_to_plain_text("- 项目1\n- 项目2")
        assert "- 项目1" in result
        assert "- 项目2" in result

    def test_html_tag_removal(self):
        from src.channels.wecom_kf.message import markdown_to_plain_text
        assert markdown_to_plain_text("<b>文本</b>") == "文本"
