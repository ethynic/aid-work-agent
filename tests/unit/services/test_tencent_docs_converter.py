"""doc v3 属性树 -> Markdown 转换器单测

fixture 基于真实 API 抓取（HRbb 文档：Paragraph/Run/Text 结构）与
快照 §2.5 节点类型构造的表格场景。
"""

import pytest

from src.services.tencent_docs.converter import document_tree_to_markdown


def make_text(t: str) -> dict:
    return {"type": "Text", "text": t, "children": []}


def make_run(*texts: str) -> dict:
    return {"type": "Run", "text": "", "children": [make_text(t) for t in texts]}


def make_para(*texts: str, nesting: int = 0, number_id: str = "") -> dict:
    return {
        "type": "Paragraph",
        "text": "",
        "children": [make_run(*texts)] if texts else [],
        "property": {
            "paragraphProperty": {
                "numberProperty": {"nestingLevel": nesting, "numberId": number_id},
            }
        },
    }


def make_cell(*texts: str) -> dict:
    # 每个文本一段：验证单元格内多段落压成一行的行为
    return {"type": "TableCell", "text": "", "children": [make_para(t) for t in texts]}


def make_row(*cells: dict) -> dict:
    return {"type": "TableRow", "text": "", "children": list(cells)}


def make_table(*rows: dict) -> dict:
    return {"type": "Table", "text": "", "children": list(rows)}


@pytest.mark.unit
class TestPlainText:
    def test_real_world_paragraph_tree(self):
        """真实抓取结构：Document -> MainStory -> Section -> Paragraph -> Run -> Text"""
        doc = {
            "type": "Document",
            "children": [{
                "type": "MainStory",
                "children": [{
                    "type": "Section",
                    "children": [
                        make_para("劳动合同"),
                        make_para("第一条 ", "甲方与乙方"),
                        make_para(),
                    ],
                }],
            }],
        }
        md = document_tree_to_markdown(doc)
        assert "劳动合同\n\n第一条 甲方与乙方" in md
        # 末尾单个空段落不产生多余空行
        assert not md.endswith("\n\n\n")

    def test_children_none_entries_skipped(self):
        """实测响应中出现过 None 子节点，不能抛异常"""
        doc = {
            "type": "Document",
            "children": [None, make_para("正常段落"), "junk", {"type": "Paragraph"}, None],
        }
        md = document_tree_to_markdown(doc)
        assert "正常段落" in md
        assert "junk" not in md

    def test_empty_and_invalid_input(self):
        assert document_tree_to_markdown({}) == ""
        assert document_tree_to_markdown(None) == ""
        assert document_tree_to_markdown("not a dict") == ""

    def test_blank_paragraphs_collapsed(self):
        doc = {"type": "Document", "children": [
            make_para("一"), make_para(), make_para(), make_para(), make_para(), make_para("二"),
        ]}
        md = document_tree_to_markdown(doc)
        assert "一\n\n二" in md


@pytest.mark.unit
class TestListParagraph:
    def test_numbered_property_becomes_bullet(self):
        doc = {"type": "Document", "children": [
            make_para("待办一", number_id="num-1"),
            make_para("子项", nesting=1, number_id="num-1"),
        ]}
        md = document_tree_to_markdown(doc)
        assert "- 待办一" in md
        assert "  - 子项" in md

    def test_plain_paragraph_not_bulleted(self):
        doc = {"type": "Document", "children": [make_para("普通段落", nesting=0)]}
        md = document_tree_to_markdown(doc)
        assert "- 普通段落" not in md
        assert "普通段落" in md


@pytest.mark.unit
class TestTable:
    def test_table_becomes_markdown_rows(self):
        doc = {"type": "Document", "children": [
            make_para("表前"),
            make_table(
                make_row(make_cell("时间"), make_cell("记录")),
                make_row(make_cell("10月20日"), make_cell("王xx")),
            ),
        ]}
        md = document_tree_to_markdown(doc)
        assert "| 时间 | 记录 |" in md
        assert "|---|---|" in md  # 分隔行列数与表头一致（2 列）
        assert "| 10月20日 | 王xx |" in md

    def test_cell_multiline_paragraph_joined(self):
        doc = {"type": "Document", "children": [make_table(
            make_row(make_cell("第一行", "第二行")),
        )]}
        md = document_tree_to_markdown(doc)
        assert "| 第一行 第二行 |" in md

    def test_empty_cell_placeholder(self):
        doc = {"type": "Document", "children": [make_table(
            make_row(make_cell(), make_cell("有值")),
        )]}
        md = document_tree_to_markdown(doc)
        assert "|  | 有值 |" in md


@pytest.mark.unit
class TestUnknownNodes:
    def test_image_placeholder(self):
        doc = {"type": "Document", "children": [
            make_para("见图"),
            {"type": "Image", "children": []},
        ]}
        md = document_tree_to_markdown(doc)
        assert "见图" in md
        assert "（图片）" in md

    def test_unknown_type_recurses_children(self):
        doc = {"type": "Document", "children": [
            {"type": "SmartCanvas", "text": "", "children": [make_para("画布内文字")]},
        ]}
        assert "画布内文字" in document_tree_to_markdown(doc)

    def test_text_leaf_direct(self):
        doc = {"type": "Document", "children": [make_text("裸文本")]}
        assert "裸文本" in document_tree_to_markdown(doc)
