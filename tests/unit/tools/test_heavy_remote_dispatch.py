"""工具层远程派发单测（重工具服务化 §3/§4.2）：excel_process / upload_data_file /
paddleocr_doc_parsing 在 remote/local 两模式下的派发与失败语义。"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

pytestmark = pytest.mark.tools


def _enable_remote(monkeypatch, tools=("excel_process", "upload_data_file", "paddleocr_doc_parsing")):
    monkeypatch.setenv("HEAVY_TOOLS_MODE", "remote")
    from src.config import settings
    monkeypatch.setattr(settings, "agent_runner",
                        SimpleNamespace(heavy_tools=list(tools), slot_concurrency=4))


def _disable_local(monkeypatch):
    monkeypatch.delenv("HEAVY_TOOLS_MODE", raising=False)
    from src.config import settings
    monkeypatch.setattr(settings, "agent_runner",
                        SimpleNamespace(heavy_tools=["excel_process"], slot_concurrency=4))


class _FakeRemote:
    """替身 client.execute：记录调用并按脚本回放。"""

    def __init__(self, results=None, error=None):
        self.calls = []
        self.results = results or {}
        self.error = error

    async def __call__(self, *, tool, handler=None, params=None, context=None,
                       timeout_seconds=600):
        self.calls.append({"tool": tool, "handler": handler, "params": params,
                           "timeout_seconds": timeout_seconds})
        if self.error is not None:
            raise self.error
        return self.results.get(handler) or {"success": True}


# ==================== excel_process ====================

class TestExcelRemoteDispatch:

    def _tool(self):
        from src.tools.excel.excel_process_tool import ExcelProcessTool
        return ExcelProcessTool()

    def test_local_mode_keeps_local_handlers(self, monkeypatch):
        _disable_local(monkeypatch)
        assert self._tool()._remote_dispatch_enabled() is False

    def test_remote_mode_requires_whitelist(self, monkeypatch):
        _enable_remote(monkeypatch, tools=[])
        assert self._tool()._remote_dispatch_enabled() is False
        _enable_remote(monkeypatch)
        assert self._tool()._remote_dispatch_enabled() is True

    @pytest.mark.asyncio
    async def test_fill_template_split_local_analysis_remote_render(self, monkeypatch):
        """§3 拆分：结构分析（LLM）本地、渲染段远程（handler=fill_template_render）"""
        _enable_remote(monkeypatch)
        fake = _FakeRemote(results={"fill_template_render": {
            "success": True, "file_path": "/app/storage/tenants/t/conversation/out.xlsx"}})
        from src.services.heavy_tool_service import client
        monkeypatch.setattr(client, "execute", fake)

        from src.tools.excel.excel_process_tool import ExcelProcessTool
        tool = ExcelProcessTool()
        monkeypatch.setattr(tool, "_resolve_file", lambda p: f"/app/storage/resolved/{Path(p).name}")
        monkeypatch.setattr(
            "src.tools.excel.excel_template_ai.analyze_structure_safe",
            lambda *args, **kwargs: {"success": True, "structure": {
                "columns": [], "detail_first_row": 2, "detail_last_row": 3}})

        ctx = SimpleNamespace(file_paths=["/app/storage/tpl.xlsx"], content=None,
                              content_type=None, instruction=None, output_name=None,
                              data=None, results=[])
        result = await tool._fill_template_remote(
            ctx, {"data": {"rows": [{"a": 1}]}, "output_name": "out.xlsx"})

        assert result["success"] is True
        assert len(fake.calls) == 1
        call = fake.calls[0]
        assert call["tool"] == "excel_process"
        assert call["handler"] == "fill_template_render"
        # 无 template_file 参数时取已解析的 ctx.file_paths[0]
        assert call["params"]["template_file"] == "/app/storage/tpl.xlsx"
        assert call["params"]["structure"]["detail_first_row"] == 2
        # 渲染段远程，但结构分析产物已就地传入
        assert call["params"]["data"] == {"rows": [{"a": 1}]}

    @pytest.mark.asyncio
    async def test_dispatch_remote_passes_resolved_paths_and_route_params(self, monkeypatch):
        _enable_remote(monkeypatch)
        fake = _FakeRemote(results={"read": {"success": True, "headers": ["a"]}})
        from src.services.heavy_tool_service import client
        monkeypatch.setattr(client, "execute", fake)

        from src.tools.excel.excel_process_tool import ExcelProcessTool, PipelineContext
        tool = ExcelProcessTool()
        # execute() 远程分支已把 ctx.file_paths 解析为共享卷绝对路径，
        # _dispatch_remote 透传已解析路径（file_id 解析不在本方法职责内）
        ctx = PipelineContext(file_paths=["/app/storage/abs.xlsx"], content=None, content_type=None)
        result = await tool._dispatch_remote("read", ctx, {"sheet_name": "S1"})

        assert result == {"success": True, "headers": ["a"]}
        call = fake.calls[0]
        assert call["handler"] == "read"
        assert call["params"]["file_paths"] == ["/app/storage/abs.xlsx"]
        assert call["params"]["route_params"] == {"sheet_name": "S1"}

    @pytest.mark.asyncio
    async def test_busy_passthrough_with_retry_guidance(self, monkeypatch):
        _enable_remote(monkeypatch)
        from src.services.heavy_tool_service.client import HeavyToolBusy
        fake = _FakeRemote(error=HeavyToolBusy("重工具服务繁忙，请稍后重试"))
        from src.services.heavy_tool_service import client
        monkeypatch.setattr(client, "execute", fake)

        from src.tools.excel.excel_process_tool import ExcelProcessTool, PipelineContext
        tool = ExcelProcessTool()
        ctx = PipelineContext(file_paths=["/app/storage/a.xlsx"], content=None, content_type=None)
        result = await tool._dispatch_remote("export", ctx, {})
        assert result["success"] is False
        assert "稍后重试" in result["error"]

    @pytest.mark.asyncio
    async def test_unavailable_neutral_failure(self, monkeypatch):
        """§8：重容器不可用 → 中性失败（「处理未完成」口径），细节进日志不进 message"""
        _enable_remote(monkeypatch)
        from src.services.heavy_tool_service.client import HeavyToolUnavailable
        fake = _FakeRemote(error=HeavyToolUnavailable("connect refused"))
        from src.services.heavy_tool_service import client
        monkeypatch.setattr(client, "execute", fake)

        from src.tools.excel.excel_process_tool import ExcelProcessTool, PipelineContext
        tool = ExcelProcessTool()
        ctx = PipelineContext(file_paths=["/app/storage/a.xlsx"], content=None, content_type=None)
        result = await tool._dispatch_remote("modify", ctx, {})
        assert result["success"] is False
        assert result["error"] == "处理未完成，请稍后重试"


# ==================== upload_data_file ====================

class TestUploadRemoteParse:

    def _tool(self):
        from src.tools.data_analysis.upload_data_tool import UploadDataFileTool
        return UploadDataFileTool()

    def test_local_mode(self, monkeypatch):
        _disable_local(monkeypatch)
        assert self._tool()._remote_dispatch_enabled() is False

    @pytest.mark.asyncio
    async def test_remote_parse_dispatches_absolute_path(self, monkeypatch, tmp_path):
        _enable_remote(monkeypatch)
        fake = _FakeRemote(results={"parse": {
            "success": True,
            "sheets": [{"sheet_name": "S1", "rows": 2, "columns": ["a"],
                        "sample_data": [[1, 2]], "data_types": {"a": "int"}}]}})
        from src.services.heavy_tool_service import client
        monkeypatch.setattr(client, "execute", fake)

        file_path = tmp_path / "data.xlsx"
        file_path.write_bytes(b"x")
        parsed = await self._tool()._parse_sheets(str(file_path))
        assert parsed["error"] is None
        assert parsed["sheets"][0]["sheet_name"] == "S1"
        call = fake.calls[0]
        assert call["tool"] == "upload_data_file"
        assert call["handler"] == "parse"
        assert call["params"]["file_path"] == str(file_path.resolve())

    @pytest.mark.asyncio
    async def test_remote_busy_neutral_error(self, monkeypatch, tmp_path):
        _enable_remote(monkeypatch)
        from src.services.heavy_tool_service.client import HeavyToolBusy
        fake = _FakeRemote(error=HeavyToolBusy("重工具服务繁忙，请稍后重试"))
        from src.services.heavy_tool_service import client
        monkeypatch.setattr(client, "execute", fake)

        file_path = tmp_path / "data.csv"
        file_path.write_text("a\n1\n")
        parsed = await self._tool()._parse_sheets(str(file_path))
        assert parsed["sheets"] == []
        assert "稍后重试" in parsed["error"]

    @pytest.mark.asyncio
    async def test_remote_unavailable_does_not_fall_back_local(self, monkeypatch, tmp_path):
        """§8：远程不可用不回退本地解析（大文件解析正是要隔离的重负载）"""
        _enable_remote(monkeypatch)
        from src.services.heavy_tool_service.client import HeavyToolUnavailable
        fake = _FakeRemote(error=HeavyToolUnavailable("down"))
        from src.services.heavy_tool_service import client
        monkeypatch.setattr(client, "execute", fake)

        file_path = tmp_path / "data.csv"
        file_path.write_text("a\n1\n")
        parsed = await self._tool()._parse_sheets(str(file_path))
        assert parsed["error"] == "处理未完成，请稍后重试"


# ==================== paddleocr_doc_parsing ====================

class TestPaddleOCRRemote:

    def _tool(self):
        from src.tools.ocr.ocr_tool import PaddleOCRDocParsingTool
        return PaddleOCRDocParsingTool()

    def test_local_mode(self, monkeypatch):
        _disable_local(monkeypatch)
        assert self._tool()._remote_dispatch_enabled() is False

    def test_remote_mode(self, monkeypatch):
        _enable_remote(monkeypatch)
        assert self._tool()._remote_dispatch_enabled() is True

    @pytest.mark.asyncio
    async def test_whole_tool_remote_with_resolved_path(self, monkeypatch, tmp_path):
        _enable_remote(monkeypatch)
        fake = _FakeRemote(results={None: {"success": True, "texts": ["page1"],
                                           "full_text": "page1", "truncated": False,
                                           "message": "成功解析1页"}})
        from src.services.heavy_tool_service import client
        monkeypatch.setattr(client, "execute", fake)

        pdf = tmp_path / "doc.pdf"
        pdf.write_bytes(b"%PDF")
        result = await self._tool()._execute_remote(str(pdf), "", None)
        assert result["success"] is True
        call = fake.calls[0]
        assert call["tool"] == "paddleocr_doc_parsing"
        assert call["timeout_seconds"] == 900  # paddleocr 超时覆盖（§4.1）
        assert call["params"]["file_path"] == str(pdf.resolve())

    @pytest.mark.asyncio
    async def test_unavailable_neutral_failure(self, monkeypatch):
        _enable_remote(monkeypatch)
        from src.services.heavy_tool_service.client import HeavyToolUnavailable
        fake = _FakeRemote(error=HeavyToolUnavailable("down"))
        from src.services.heavy_tool_service import client
        monkeypatch.setattr(client, "execute", fake)
        result = await self._tool()._execute_remote("/app/storage/tenants/t/a.pdf", "", None)
        assert result == {"success": False, "error": "处理未完成，请稍后重试"}
