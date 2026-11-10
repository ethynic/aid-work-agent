"""
UploadDataFileTool 行为测试（压测缺陷 #3 回归）

缺陷背景：SchemaExtractor.extract_schema 是 async 函数，曾被
asyncio.to_thread 调用——只拿到 coroutine 对象，随后 .get 必抛
AttributeError，每个 sheet 都失败，工具整体失效。

测试策略：
- CSV 走真实 SheetParser 解析（纯本地 pandas，无 LLM）
- extract_schema 走真实实现，仅 stub 其 LLM gateway（chat_no_thinking）
- save_schema_to_knowledge stub 为成功（真实实现写库+向量）
- 断言注册成功路径：tables 非空且 errors 为空——修复前该断言必然失败
"""

import json
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [pytest.mark.tools, pytest.mark.unit]


_SCHEMA_JSON = {
    "table_name": "orders_2024",
    "description": "订单明细表",
    "columns": [
        {
            "name": "order_id",
            "semantic_name": "订单编号",
            "data_type": "string",
            "description": "唯一标识",
            "enum_values": [],
            "sample_values": ["SYN000001"],
        },
        {
            "name": "sales_amount",
            "semantic_name": "销售金额",
            "data_type": "number",
            "description": "订单金额",
            "enum_values": [],
            "sample_values": ["100"],
        },
    ],
}


def _stub_gateway(schema: dict) -> MagicMock:
    """构造返回固定 schema JSON 的 LLM gateway stub（chat_no_thinking 为 async）"""
    gateway = MagicMock()
    gateway.get_model_name.return_value = "stub-model"

    async def chat_no_thinking(*args, **kwargs):
        return {"content": json.dumps(schema, ensure_ascii=False), "usage": None}

    gateway.chat_no_thinking = chat_no_thinking
    return gateway


@pytest.fixture
def csv_file(tmp_path):
    """真实 CSV 附件（走 SheetParser 本地解析）"""
    p = tmp_path / "orders_2024.csv"
    p.write_text(
        "order_id,sales_amount\nSYN000001,100\nSYN000002,200\n",
        encoding="utf-8",
    )
    return p


@pytest.fixture
def stubbed_llm():
    """stub SchemaExtractor 的 LLM gateway（不触真实 LLM）"""
    gateway = _stub_gateway(_SCHEMA_JSON)
    with patch(
        "src.services.data_analysis.schema_extractor.SchemaExtractor._get_gateway",
        return_value=gateway,
    ):
        yield gateway


@pytest.fixture
def stubbed_save():
    """stub save_schema_to_knowledge 为成功（真实实现写库+向量）"""
    async def fake_save(**kwargs):
        return {"success": True, "doc_id": 101, "message": "ok", "existing": False}

    with patch(
        "src.services.data_analysis.schema_saver.save_schema_to_knowledge",
        side_effect=fake_save,
    ) as mock_save:
        yield mock_save


class TestUploadDataFileTool:
    @pytest.mark.asyncio
    async def test_register_single_sheet_success(self, csv_file, stubbed_llm, stubbed_save):
        """注册成功：tables 非空、errors 为空（缺陷 #3 回归：修复前每个 sheet 都失败）"""
        from src.tools.data_analysis.upload_data_tool import UploadDataFileTool

        tool = UploadDataFileTool()
        result = await tool.execute(
            file_path=str(csv_file),
            analysis_intent="按渠道统计销售额",
        )

        assert result["success"] is True, f"注册失败: {result.get('error')} / {result.get('details')}"
        assert result["errors"] is None
        assert len(result["tables"]) == 1
        table = result["tables"][0]
        assert table["table_name"] == "orders_2024"
        assert table["doc_id"] == 101
        assert table["columns"] == ["order_id", "sales_amount"]
        assert table["rows"] == 2
        stubbed_save.assert_called_once()

    @pytest.mark.asyncio
    async def test_tenant_id_passed_to_saver(
        self, csv_file, stubbed_llm, stubbed_save, monkeypatch
    ):
        """当前租户上下文透传给 save_schema_to_knowledge"""
        from src.tools.data_analysis.upload_data_tool import UploadDataFileTool

        monkeypatch.setattr(
            "src.saas.context.get_current_tenant_id", lambda: "tenant_upload_test"
        )

        tool = UploadDataFileTool()
        result = await tool.execute(
            file_path=str(csv_file),
            analysis_intent="按渠道统计销售额",
        )

        assert result["success"] is True
        assert stubbed_save.call_args.kwargs["tenant_id"] == "tenant_upload_test"

    @pytest.mark.asyncio
    async def test_multi_sheet_all_registered(self, tmp_path, stubbed_save):
        """多 sheet 文件：每个 sheet 都注册成功（缺陷 #3 修复前全部失败）"""
        from openpyxl import Workbook

        from src.tools.data_analysis.upload_data_tool import UploadDataFileTool

        # 每次调用返回递增 table_name，验证循环内逐 sheet 独立注册
        counter = {"n": 0}

        async def chat_no_thinking(*args, **kwargs):
            counter["n"] += 1
            schema = dict(_SCHEMA_JSON)
            schema["table_name"] = f"table_{counter['n']}"
            return {"content": json.dumps(schema, ensure_ascii=False), "usage": None}

        gateway = MagicMock()
        gateway.get_model_name.return_value = "stub-model"
        gateway.chat_no_thinking = chat_no_thinking

        xlsx = tmp_path / "multi.xlsx"
        wb = Workbook()
        ws1 = wb.active
        ws1.title = "orders"
        ws1.append(["order_id", "sales_amount"])
        ws1.append(["SYN000001", 100])
        ws2 = wb.create_sheet("refunds")
        ws2.append(["order_id", "sales_amount"])
        ws2.append(["SYN000002", 50])
        wb.save(xlsx)

        with patch(
            "src.services.data_analysis.schema_extractor.SchemaExtractor._get_gateway",
            return_value=gateway,
        ):
            tool = UploadDataFileTool()
            result = await tool.execute(file_path=str(xlsx), analysis_intent="分析")

        assert result["success"] is True
        assert result["errors"] is None
        assert [t["table_name"] for t in result["tables"]] == ["table_1", "table_2"]
        assert [t["sheet_name"] for t in result["tables"]] == ["orders", "refunds"]

    @pytest.mark.asyncio
    async def test_file_not_found(self):
        from src.tools.data_analysis.upload_data_tool import UploadDataFileTool

        tool = UploadDataFileTool()
        result = await tool.execute(
            file_path="/nonexistent/file.csv", analysis_intent="分析"
        )
        assert result["success"] is False
        assert "文件不存在" in result["error"]

    @pytest.mark.asyncio
    async def test_unsupported_extension(self, tmp_path):
        from src.tools.data_analysis.upload_data_tool import UploadDataFileTool

        p = tmp_path / "doc.txt"
        p.write_text("hello", encoding="utf-8")
        tool = UploadDataFileTool()
        result = await tool.execute(file_path=str(p), analysis_intent="分析")
        assert result["success"] is False
        assert "不支持的文件格式" in result["error"]
