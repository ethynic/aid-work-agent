import sys
from pathlib import Path

import openpyxl
import pytest
from unittest.mock import AsyncMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def test_excel_process_schema_has_structured_input_fields():
    from src.tools.excel.excel_process_tool import ExcelProcessTool

    schema = ExcelProcessTool().to_tool_definition()["input_schema"]
    properties = schema.get("properties", {})

    assert "instruction" in properties
    assert "content" in properties
    assert "content_type" in properties
    assert "output_name" in properties
    assert "context" in properties
    assert "file_paths" in properties


@pytest.mark.asyncio
async def test_excel_router_exports_markdown_table_without_llm():
    from src.tools.excel.excel_router import ExcelRouter

    router = ExcelRouter()
    context = """请导出Excel文件

| 类别 | 项目 | 金额 |
|------|------|------|
| 门票 | 成人票 | 120 |
"""

    result = await router.route(context)

    assert result["task"] == "export"
    assert result["params"]["data_type"] == "markdown"


@pytest.mark.asyncio
async def test_excel_process_export_structured_markdown_content():
    from src.tools.excel.excel_process_tool import ExcelProcessTool

    tool = ExcelProcessTool()
    mock_router = AsyncMock()
    mock_router.route.side_effect = AssertionError("确定性路由不应调用内部 LLM")
    tool._router = mock_router

    table = "| 项目 | 金额 |\n|------|------|\n| 门票 | 120 |"
    mock_result = {
        "success": True,
        "file_path": "/tmp/quote.xlsx",
        "file_name": "quote.xlsx",
        "file_size": 1024,
        "row_count": 1,
    }

    with patch("src.tools.excel.excel_writer.create_excel", return_value=mock_result) as mock_create:
        result = await tool.execute(
            instruction="导出Excel文件",
            content=table,
            content_type="markdown",
            output_name="报价单.xlsx",
        )

    assert result["success"] is True
    kwargs = mock_create.call_args.kwargs
    assert kwargs["data"] == table
    assert kwargs["data_type"] == "markdown"
    assert kwargs["file_name"] == "报价单.xlsx"


@pytest.mark.asyncio
async def test_excel_process_export_legacy_context_strips_instruction_prefix():
    from src.tools.excel.excel_process_tool import ExcelProcessTool

    tool = ExcelProcessTool()
    mock_router = AsyncMock()
    mock_router.route.side_effect = AssertionError("确定性路由不应调用内部 LLM")
    tool._router = mock_router

    context = "请导出Excel文件，下面是Markdown表格：\n\n| 项目 | 金额 |\n|------|------|\n| 住宿 | 300 |"
    mock_result = {
        "success": True,
        "file_path": "/tmp/export.xlsx",
        "file_name": "export.xlsx",
        "file_size": 1024,
        "row_count": 1,
    }

    with patch("src.tools.excel.excel_writer.create_excel", return_value=mock_result) as mock_create:
        result = await tool.execute(context=context)

    assert result["success"] is True
    data = mock_create.call_args.kwargs["data"]
    assert data.startswith("| 项目 | 金额 |")
    assert "请导出" not in data


@pytest.mark.asyncio
async def test_excel_process_export_structured_csv_content():
    from src.tools.excel.excel_process_tool import ExcelProcessTool

    tool = ExcelProcessTool()
    mock_router = AsyncMock()
    mock_router.route.side_effect = AssertionError("确定性路由不应调用内部 LLM")
    tool._router = mock_router

    csv_text = "项目,金额\n门票,120\n住宿,300"
    mock_result = {
        "success": True,
        "file_path": "/tmp/csv.xlsx",
        "file_name": "csv.xlsx",
        "file_size": 1024,
        "row_count": 2,
    }

    with patch("src.tools.excel.excel_writer.create_excel", return_value=mock_result) as mock_create:
        result = await tool.execute(
            instruction="导出Excel文件",
            content=csv_text,
            content_type="csv",
        )

    assert result["success"] is True
    kwargs = mock_create.call_args.kwargs
    assert kwargs["data"] == csv_text
    assert kwargs["data_type"] == "csv"


@pytest.mark.asyncio
async def test_excel_process_export_structured_json_content():
    from src.tools.excel.excel_process_tool import ExcelProcessTool

    tool = ExcelProcessTool()
    mock_router = AsyncMock()
    mock_router.route.side_effect = AssertionError("确定性路由不应调用内部 LLM")
    tool._router = mock_router

    json_text = '[{"项目": "门票", "金额": 120}, {"项目": "住宿", "金额": 300}]'
    mock_result = {
        "success": True,
        "file_path": "/tmp/json.xlsx",
        "file_name": "json.xlsx",
        "file_size": 1024,
        "row_count": 2,
    }

    with patch("src.tools.excel.excel_writer.create_excel", return_value=mock_result) as mock_create:
        result = await tool.execute(
            instruction="导出Excel文件",
            content=json_text,
            content_type="json",
        )

    assert result["success"] is True
    kwargs = mock_create.call_args.kwargs
    assert kwargs["data"] == [{"项目": "门票", "金额": 120}, {"项目": "住宿", "金额": 300}]
    assert kwargs["data_type"] == "dict_list"


@pytest.mark.asyncio
async def test_excel_process_export_needs_data_when_only_intent():
    from src.tools.excel.excel_process_tool import ExcelProcessTool

    tool = ExcelProcessTool()
    mock_router = AsyncMock()
    mock_router.route.side_effect = AssertionError("确定性路由不应调用内部 LLM")
    tool._router = mock_router

    result = await tool.execute(instruction="帮我导出Excel文件")

    assert result["success"] is False
    assert result["needs_data"] is True


@pytest.mark.asyncio
async def test_excel_process_csv_attachment_converts_without_needs_data(tmp_path):
    from src.tools.excel.excel_process_tool import ExcelProcessTool

    tool = ExcelProcessTool()
    mock_router = AsyncMock()
    mock_router.route.side_effect = AssertionError("确定性路由不应调用内部 LLM")
    tool._router = mock_router

    csv_path = tmp_path / "quote.csv"
    csv_path.write_text("项目,金额\n门票,120\n", encoding="utf-8")
    mock_result = {
        "success": True,
        "file_path": "/tmp/quote.xlsx",
        "file_name": "quote.xlsx",
        "file_size": 1024,
    }

    with patch("src.tools.excel.excel_writer.convert_format", return_value=mock_result) as mock_convert:
        result = await tool.execute(
            instruction="把CSV转成Excel",
            file_paths=[str(csv_path)],
            output_name="报价单.xlsx",
        )

    assert result["success"] is True
    kwargs = mock_convert.call_args.kwargs
    assert kwargs["target_format"] == "excel"
    assert kwargs["output_name"] == "报价单.xlsx"


@pytest.mark.asyncio
async def test_excel_router_converts_csv_attachment_without_llm(tmp_path):
    from src.tools.excel.excel_router import ExcelRouter

    csv_path = tmp_path / "quote.csv"
    csv_path.write_text("项目,金额\n门票,120\n", encoding="utf-8")

    result = await ExcelRouter().route("有EXCEL格式吗", [str(csv_path)])

    assert result["task"] == "convert"
    assert result["params"]["source_format"] == "csv"
    assert result["params"]["target_format"] == "excel"
    assert result["params"]["output_name"] == "quote.xlsx"


def test_convert_csv_to_excel_creates_readable_workbook(tmp_path, monkeypatch):
    from src.tools.excel.excel_lib import ExcelFileHandler
    from src.tools.excel.excel_writer import convert_format

    csv_path = tmp_path / "quote.csv"
    csv_path.write_text("项目,金额\n门票,120\n住宿,300\n", encoding="utf-8")
    monkeypatch.setattr(ExcelFileHandler, "get_session_dir", staticmethod(lambda: tmp_path))

    result = convert_format(str(csv_path), target_format="excel", output_name="quote.xlsx")

    assert result["success"] is True
    assert result["file_name"] == "quote.xlsx"

    wb = openpyxl.load_workbook(result["file_path"])
    ws = wb.active
    assert ws["A1"].value == "项目"
    assert ws["B1"].value == "金额"
    assert ws["A2"].value == "门票"
    assert ws["B2"].value == 120
    wb.close()


def test_excel_save_temp_does_not_overwrite_existing_file(tmp_path, monkeypatch):
    from src.tools.excel.excel_lib import ExcelFileHandler
    from src.tools.excel.excel_writer import create_excel

    monkeypatch.setattr(ExcelFileHandler, "get_session_dir", staticmethod(lambda: tmp_path))

    first = create_excel(
        data="| 项目 | 金额 |\n|------|------|\n| 门票 | 120 |",
        data_type="markdown",
        file_name="quote.xlsx",
    )
    second = create_excel(
        data="| 项目 | 金额 |\n|------|------|\n| 住宿 | 300 |",
        data_type="markdown",
        file_name="quote.xlsx",
    )

    assert first["success"] is True
    assert second["success"] is True
    assert first["file_path"] != second["file_path"]
    assert Path(first["file_path"]).exists()
    assert Path(second["file_path"]).exists()


def test_create_excel_writes_currency_text_as_number(tmp_path, monkeypatch):
    from src.tools.excel.excel_lib import ExcelFileHandler
    from src.tools.excel.excel_writer import create_excel

    monkeypatch.setattr(ExcelFileHandler, "get_session_dir", staticmethod(lambda: tmp_path))

    result = create_excel(
        data="| 项目 | 金额 |\n|------|------|\n| 合计 | ¥1,446.07 |",
        data_type="markdown",
        file_name="amount.xlsx",
    )

    assert result["success"] is True

    wb = openpyxl.load_workbook(result["file_path"])
    ws = wb.active
    assert ws["B2"].value == 1446.07
    wb.close()


# ============================================================
# 规则路由：修改类指令不得被 to_md 劫持（2026-09-15 研学行程事故回归）
# ============================================================


def test_rule_route_modify_intent_falls_through_to_llm():
    """修改类指令（含"内容"/"文本"字样）不命中 to_md 规则，交给 LLM 路由生成 modify"""
    from src.tools.excel.excel_router import ExcelRouter

    router = ExcelRouter()
    modify_contexts = [
        "修改该表格：1）把第一行标题单元格A1的内容从A改为B；2）删除重复行",
        "对表格做文本查找替换：把全文中的X替换为Y",
        "只做一件事：把 A1 单元格的文字改为 X",
        "把表格里的价格更新一下",
    ]
    for ctx in modify_contexts:
        res = router._rule_based_route(ctx, ["a.xlsx"])
        assert res.get("task") == "", f"修改指令被规则路由劫持: {ctx} -> {res}"


def test_rule_route_to_md_still_works_for_read_intent():
    """非修改指令的 to_md 短路保持不变"""
    from src.tools.excel.excel_router import ExcelRouter

    router = ExcelRouter()
    res = router._rule_based_route("读取这个附件的内容", ["a.xlsx"])
    assert res["task"] == "to_md"


# ============================================================
# fill_template 平铺 data 事故回归（2026-09-23 报价单，tr_26e2c23499dc4441）
# ============================================================

def test_deterministic_fill_hijack_requires_nonempty_rows():
    """确定性劫持仅在 data 携带非空 rows 时触发；平铺标量 dict 交给 LLM 路由"""
    from src.tools.excel.excel_process_tool import ExcelProcessTool

    tool = ExcelProcessTool()

    # 平铺标量 dict（事故现场形状）：不劫持，交 LLM 路由识别 modify/set_cell
    flat = {"日期": "2026年09月23日", "意向场景": "人力资源AI员工"}
    assert tool._resolve_task_deterministic("填日期和意向场景", ["file_x"]) is None
    assert tool._resolve_task_deterministic("填日期", ["file_x"], data=flat) is None

    # rows 空列表：同样不劫持
    assert tool._resolve_task_deterministic("填充", ["file_x"], data={"rows": []}) is None

    # 非 dict data：不劫持（走原有分支）
    assert tool._resolve_task_deterministic("填充", ["file_x"], data="文本数据") is None

    # 合法结构化 data：仍确定性走 fill_template
    ok = tool._resolve_task_deterministic("填充", ["file_x"], data={"rows": [{"品名": "A"}]})
    assert ok["task"] == "fill_template"
    assert ok["params"]["data"] == {"rows": [{"品名": "A"}]}


@pytest.mark.asyncio
async def test_fill_template_flat_data_returns_needs_data_with_modify_hint(tmp_path):
    """data 无有效 rows 时返回 needs_data 引导（含 modify/set_cell 改路提示 + 模板预览）"""
    from openpyxl import Workbook
    from src.tools.excel.excel_process_tool import ExcelProcessTool

    tpl = tmp_path / "tpl.xlsx"
    wb = Workbook()
    ws = wb.active
    ws["A1"] = "报价单"
    ws["B4"] = "日期："
    wb.save(tpl)

    res = await ExcelProcessTool._fill_template_needs_data(
        str(tpl),
        shape_hint=(
            "当前传入的 data 不含可填充的数据行（rows 为空或不是列表）。两种改法："
            "1) 若要把表格数据填入模板，构造 data={'rows': [{'列名': '值', ...}, ...]} 后重调；"
            "2) 若只需填写个别单元格并保留模板其余内容，改用 modify 操作（set_cell 指定单元格坐标），不要用 fill_template。"
        ),
    )
    assert res["success"] is False
    assert res["needs_data"] is True
    # 引导里必须包含两条自愈路径：构造 rows / 改走 modify
    assert "rows" in res["error"]
    assert "modify" in res["error"] or "set_cell" in res["error"]
    # 模板预览附在引导里，Agent 一次往返拿到"模板长什么样"
    assert "报价单" in res.get("template_preview", "")


@pytest.mark.asyncio
async def test_fill_template_routed_with_flat_data_heals_in_one_roundtrip(tmp_path):
    """LLM 路由返回 fill_template + 平铺 data：不再干报错，直接 needs_data 自愈引导"""
    from openpyxl import Workbook
    from src.tools.excel.excel_process_tool import ExcelProcessTool

    tpl = tmp_path / "quote.xlsx"
    wb = Workbook()
    wb.active["A1"] = "产品报价单"
    wb.save(tpl)

    tool = ExcelProcessTool()
    flat = {"日期": "2026年09月23日", "意向场景": "人力资源AI员工"}

    with patch.object(tool, "_resolve_file", return_value=str(tpl)), \
         patch.object(tool, "_get_router") as mock_router_getter:
        mock_router = AsyncMock()
        mock_router.route.return_value = {
            "task": "fill_template",
            "params": {"template_file": str(tpl)},
        }
        mock_router_getter.return_value = mock_router

        res = await tool.execute(
            instruction="仅填充日期和意向场景，其余保留模板原文",
            file_paths=[str(tpl)],
            data=flat,
        )

    assert res["success"] is False
    assert res.get("needs_data") is True
    assert "modify" in res["error"] or "set_cell" in res["error"]


# =============================================================================
# to_md 落盘闭环（Phase 6：全文不再回塞）
# 契约：docs/tools/tool-development-spec.md §1.3 —— 全文类 > 5000 字符
# 必须调 spill_large_content 落盘，返回 preview + file_path + truncated + full_size
# =============================================================================

class TestExcelToMdSpill:
    """excel to_md 输出：≤5000 字原样返回零变化，>5000 落盘闭环"""

    @staticmethod
    def _merge_to_md(markdown):
        from src.tools.excel.excel_process_tool import PipelineContext, ExcelProcessTool

        tool = ExcelProcessTool()
        ctx = PipelineContext()
        ctx.results = [{
            "operation": "to_md",
            "success": True,
            "markdown": markdown,
            "sheet_count": 1,
            "total_rows": 10,
            "truncated": False,
        }]
        return tool._merge_results(ctx)

    def test_small_markdown_returned_as_is(self):
        md = "| A | B |\n| --- | --- |\n| 1 | 2 |"

        merged = self._merge_to_md(md)

        assert merged["markdown"] == md
        for key in ("preview", "file_path", "truncated", "full_size"):
            assert key not in merged

    def test_boundary_exactly_5000_chars_not_spilled(self):
        from src.tools._spill import PREVIEW_LIMIT

        merged = self._merge_to_md("x" * PREVIEW_LIMIT)

        assert merged["markdown"] == "x" * PREVIEW_LIMIT
        assert "file_path" not in merged

    def test_oversized_markdown_spilled(self):
        from src.tools._spill import PREVIEW_LIMIT

        big = "## Sheet1\n| a | b |\n| --- | --- |\n" + ("| 数据 | 值 |\n" * 900)
        assert len(big) > PREVIEW_LIMIT

        merged = self._merge_to_md(big)

        assert merged["truncated"] is True
        assert merged["full_size"] == len(big)
        assert merged["preview"].startswith("## Sheet1")
        assert "markdown" not in merged
        # 落盘的是完整原文（可 read/grep 回读），suffix=.md
        spilled = Path(merged["file_path"])
        assert spilled.exists()
        assert spilled.read_text(encoding="utf-8") == big
        assert spilled.name.endswith(".md")


class TestExcelToMdRealFileSpill:
    """真实 xlsx 直驱 _handle_to_md（真实 markitdown 转换）的落盘闭环 E2E。

    markitdown 默认 100 行上限内制造 >5000 字（长单元格），
    验证真实转换引擎产出的全文在落盘闭环中可 grep / read 消费。
    """

    @pytest.mark.asyncio
    async def test_real_xlsx_to_md_spill_then_grep_read(self, tmp_path):
        from src.tools._spill import PREVIEW_LIMIT
        from src.tools.excel.excel_process_tool import PipelineContext, ExcelProcessTool
        from src.tools.file.grep_tool import GrepTool
        from src.tools.file.read_tool import ReadTool

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["项目", "数值", "备注"])
        long_cell = "长内容单元格文本" * 12  # ~84 字/行 × 100 行 > 5000 字
        for i in range(120):  # 超过 markitdown 100 行上限，前 100 行已 >5000 字
            ws.append([f"条目{i:03d}", i * 3, long_cell])
        xlsx_path = tmp_path / "big.xlsx"
        wb.save(str(xlsx_path))

        tool = ExcelProcessTool()
        ctx = PipelineContext(file_paths=[str(xlsx_path)])
        step = await tool._handle_to_md(ctx, {})
        assert step["success"] is True
        assert len(step["markdown"]) > PREVIEW_LIMIT  # 真实转换确实超限
        # excel 原有 row 级 truncated 语义不受落盘改造影响
        assert step["truncated"] is True

        step["operation"] = "to_md"
        ctx.results.append(step)
        merged = tool._merge_results(ctx)

        assert merged["truncated"] is True
        assert "markdown" not in merged
        spilled = Path(merged["file_path"])
        assert spilled.name.endswith(".md")
        assert spilled.read_text(encoding="utf-8") == step["markdown"]  # 落盘=转换全文

        g = await GrepTool().execute(
            pattern="条目099", path=merged["file_path"], output_mode="content",
        )
        assert g["success"] is True and g["count"] >= 1
        r = await ReadTool().execute(
            file_path=merged["file_path"], offset=g["matches"][0]["line"], limit=2,
        )
        assert isinstance(r, dict) and "条目099" in r["content"]
