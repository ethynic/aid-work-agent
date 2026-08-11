"""
src.tools.ocr.ocr_tool 单元测试

覆盖（Phase 2 试点改造点）：
- 成功返回删除冗余 `result` 字段（原始 API 响应），保留 full_text + texts
- full_text 截断到 5000 字符 + truncated 标记
- 全部错误路径删除 `debug` 字段，统一为 {"success": False, "error": <脱敏>}
- PaddleOCRDocParsingTool.execute 同样不带 debug
"""

import pytest
from unittest.mock import MagicMock, patch

pytestmark = [pytest.mark.tools, pytest.mark.unit]


# 成功的 API 响应骨架（result.layoutParsingResults[].markdown.text）
def _fake_api_result(page_texts):
    """构造 _make_paddleocr_request 返回的原始 API 响应结构。"""
    return {
        "errorCode": 0,
        "result": {
            "layoutParsingResults": [
                {"markdown": {"text": t}} for t in page_texts
            ]
        },
    }


class TestPaddleOCRSuccessReturn:
    """成功返回结构：删 result、保留 full_text/texts、截断 + truncated。"""

    @patch("src.tools.ocr.ocr_tool._make_paddleocr_request")
    @patch("src.tools.ocr.ocr_tool._get_paddleocr_config",
           return_value=("https://x.paddleocr.com/layout-parsing", "tok"))
    @patch("src.tools.ocr.ocr_tool._load_file_as_base64", return_value="b64")
    def test_success_drops_raw_result_field(self, _b64, _cfg, mock_req):
        """成功返回不再回塞原始 API 响应 `result`（防 token 黑洞）。"""
        from src.tools.ocr.ocr_tool import paddleocr_doc_parsing
        mock_req.return_value = _fake_api_result(["page one", "page two"])

        out = paddleocr_doc_parsing(file_path="/fake/a.pdf", file_type=0)

        assert out["success"] is True
        # 关键：原始 API 响应字段已删除
        assert "result" not in out
        # 保留 texts（pdf_process 依赖）和 full_text
        assert out["texts"] == ["page one", "page two"]
        assert out["full_text"] == "page one\n\npage two"

    @patch("src.tools.ocr.ocr_tool._make_paddleocr_request")
    @patch("src.tools.ocr.ocr_tool._get_paddleocr_config",
           return_value=("https://x.paddleocr.com/layout-parsing", "tok"))
    @patch("src.tools.ocr.ocr_tool._load_file_as_base64", return_value="b64")
    def test_success_full_text_truncated(self, _b64, _cfg, mock_req):
        """full_text 超过 5000 字符时截断并带 truncated 标记。"""
        from src.tools.ocr.ocr_tool import paddleocr_doc_parsing
        long_text = "字" * 8000
        mock_req.return_value = _fake_api_result([long_text])

        out = paddleocr_doc_parsing(file_path="/fake/big.pdf", file_type=0)

        assert out["success"] is True
        assert out["truncated"] is True
        assert len(out["full_text"]) <= 5000 + 3  # limit + 默认后缀
        assert out["full_text"].endswith("...")

    @patch("src.tools.ocr.ocr_tool._make_paddleocr_request")
    @patch("src.tools.ocr.ocr_tool._get_paddleocr_config",
           return_value=("https://x.paddleocr.com/layout-parsing", "tok"))
    @patch("src.tools.ocr.ocr_tool._load_file_as_base64", return_value="b64")
    def test_success_short_full_text_not_truncated(self, _b64, _cfg, mock_req):
        """短 full_text 不截断、truncated=False。"""
        from src.tools.ocr.ocr_tool import paddleocr_doc_parsing
        mock_req.return_value = _fake_api_result(["短文本"])

        out = paddleocr_doc_parsing(file_path="/fake/small.pdf", file_type=0)

        assert out["success"] is True
        assert out["truncated"] is False
        assert out["full_text"] == "短文本"


class TestPaddleOCRErrorPathsNoDebug:
    """全部错误路径：统一 {"success": False, "error": <脱敏>}，无 debug 字段。"""

    def test_missing_input(self):
        """缺 file_path/file_url：无 debug。"""
        from src.tools.ocr.ocr_tool import paddleocr_doc_parsing
        out = paddleocr_doc_parsing()
        assert out == {"success": False, "error": "请提供文件路径或URL"}
        assert "debug" not in out

    def test_invalid_file_type(self):
        """非法 file_type：无 debug。"""
        from src.tools.ocr.ocr_tool import paddleocr_doc_parsing
        out = paddleocr_doc_parsing(file_path="/fake/a.pdf", file_type=9)
        assert out["success"] is False
        assert "0(PDF)" in out["error"] or "0(PDF)" in out["error"]
        assert "debug" not in out

    @patch("src.tools.ocr.ocr_tool._get_paddleocr_config",
           side_effect=ValueError("token=SECRET leaked"))
    def test_config_error_sanitized(self, _cfg):
        """配置错误：error 脱敏（不泄漏内部 ValueError 原文），无 debug。"""
        from src.tools.ocr.ocr_tool import paddleocr_doc_parsing
        out = paddleocr_doc_parsing(file_path="/fake/a.pdf", file_type=0)
        assert out["success"] is False
        # 内部异常原文（含 SECRET）不得出现在返回值
        assert "SECRET" not in out["error"]
        assert "token=SECRET" not in out["error"]
        assert "debug" not in out

    @patch("src.tools.ocr.ocr_tool._get_paddleocr_config",
           return_value=("https://x.paddleocr.com/layout-parsing", "tok"))
    @patch("src.tools.ocr.ocr_tool._load_file_as_base64",
           side_effect=FileNotFoundError("internal path /secrets/x"))
    def test_file_processing_error_sanitized(self, _b64, _cfg):
        """文件处理错误：error 固定脱敏，无 debug，不泄漏路径细节。"""
        from src.tools.ocr.ocr_tool import paddleocr_doc_parsing
        out = paddleocr_doc_parsing(file_path="/fake/a.pdf", file_type=0)
        assert out["success"] is False
        assert out["error"] == "文件处理失败"
        assert "/secrets/x" not in out["error"]
        assert "debug" not in out

    @patch("src.tools.ocr.ocr_tool._make_paddleocr_request",
           side_effect=RuntimeError("API key=key123 leaked in trace"))
    @patch("src.tools.ocr.ocr_tool._get_paddleocr_config",
           return_value=("https://x.paddleocr.com/layout-parsing", "tok"))
    @patch("src.tools.ocr.ocr_tool._load_file_as_base64", return_value="b64")
    def test_api_call_error_sanitized(self, _b64, _cfg, _req):
        """API 调用错误：error 固定脱敏，无 debug，不泄漏异常原文。"""
        from src.tools.ocr.ocr_tool import paddleocr_doc_parsing
        out = paddleocr_doc_parsing(file_path="/fake/a.pdf", file_type=0)
        assert out["success"] is False
        assert out["error"] == "PaddleOCR API调用失败"
        assert "key123" not in out["error"]
        assert "debug" not in out

    @patch("src.tools.ocr.ocr_tool._make_paddleocr_request",
           return_value={"errorCode": 0, "result": {"layoutParsingResults": "not_a_list"}})
    @patch("src.tools.ocr.ocr_tool._get_paddleocr_config",
           return_value=("https://x.paddleocr.com/layout-parsing", "tok"))
    @patch("src.tools.ocr.ocr_tool._load_file_as_base64", return_value="b64")
    def test_parse_error_sanitized(self, _b64, _cfg, _req):
        """结果解析错误：error 固定脱敏，无 debug。"""
        from src.tools.ocr.ocr_tool import paddleocr_doc_parsing
        out = paddleocr_doc_parsing(file_path="/fake/a.pdf", file_type=0)
        assert out["success"] is False
        assert out["error"] == "结果解析失败"
        assert "debug" not in out


class TestPaddleOCRToolExecute:
    """PaddleOCRDocParsingTool.execute 路径同样不带 debug。"""

    @pytest.mark.asyncio
    async def test_execute_missing_input_no_debug(self):
        from src.tools.ocr.ocr_tool import PaddleOCRDocParsingTool
        tool = PaddleOCRDocParsingTool()
        out = await tool.execute()
        assert out["success"] is False
        assert "debug" not in out

    def test_tool_definition(self):
        from src.tools.ocr.ocr_tool import PaddleOCRDocParsingTool
        tool = PaddleOCRDocParsingTool()
        defn = tool.to_tool_definition()
        assert defn["name"] == "paddleocr_doc_parsing"
        # description ≤80 字符（规范 §1.1）
        assert len(tool.description) <= 80


class TestPaddleOCRToolExecuteAsyncWrapped:
    """execute() 必须用 asyncio.to_thread 包裹同步函数，避免阻塞事件循环。

    回归场景：原本 `return paddleocr_doc_parsing(...)` 直接同步调用，
    导致 Gunicorn worker 因事件循环阻塞被 SIGABRT 强杀。
    """

    @pytest.mark.asyncio
    @patch("src.tools.ocr.ocr_tool._make_paddleocr_request")
    @patch("src.tools.ocr.ocr_tool._get_paddleocr_config",
           return_value=("https://x.paddleocr.com/layout-parsing", "tok"))
    @patch("src.tools.ocr.ocr_tool._load_file_as_base64", return_value="b64")
    async def test_execute_uses_to_thread(self, _b64, _cfg, mock_req):
        """execute() 必须通过 asyncio.to_thread 调用同步函数 paddleocr_doc_parsing。"""
        import asyncio
        from unittest.mock import AsyncMock
        from src.tools.ocr.ocr_tool import PaddleOCRDocParsingTool, paddleocr_doc_parsing

        mock_req.return_value = _fake_api_result(["page one"])
        tool = PaddleOCRDocParsingTool()

        # 用 AsyncMock 替换 asyncio.to_thread，验证被调用且第一个参数是同步函数对象
        with patch("asyncio.to_thread", new=AsyncMock(return_value={"success": True})) as mock_to_thread:
            await tool.execute(file_path="/fake/a.jpg", file_type=1)
            mock_to_thread.assert_awaited_once()
            # 第一个位置参数必须是同步函数 paddleocr_doc_parsing 本身（不加括号调用）
            assert mock_to_thread.call_args.args[0] is paddleocr_doc_parsing

    @pytest.mark.asyncio
    @patch("src.tools.ocr.ocr_tool._make_paddleocr_request")
    @patch("src.tools.ocr.ocr_tool._get_paddleocr_config",
           return_value=("https://x.paddleocr.com/layout-parsing", "tok"))
    @patch("src.tools.ocr.ocr_tool._load_file_as_base64", return_value="b64")
    async def test_execute_does_not_block_event_loop(self, _b64, _cfg, mock_req):
        """execute() 期间事件循环必须保持可调度：并发 quick_task 应在同步阻塞结束前完成。

        若 to_thread 生效：paddleocr_doc_parsing 在另一线程跑 0.3s，事件循环同时调度
        quick_task（asyncio.sleep(0.1)），quick_task 在 ~0.1s 完成。
        若 to_thread 未生效（假异步）：paddleocr_doc_parsing 阻塞事件循环 0.3s，期间
        quick_task 的 asyncio.sleep 无法调度，quick_task 被推迟到 ~0.4s 才完成。
        """
        import asyncio
        import time
        from src.tools.ocr.ocr_tool import PaddleOCRDocParsingTool

        # 让同步的 _make_paddleocr_request 阻塞 0.3s（模拟远端 API 耗时）
        def slow_request(*args, **kwargs):
            time.sleep(0.3)
            return _fake_api_result(["page"])

        mock_req.side_effect = slow_request
        tool = PaddleOCRDocParsingTool()

        quick_done_at = {}

        async def quick_task():
            await asyncio.sleep(0.1)
            quick_done_at["t"] = time.monotonic()
            return "done"

        start = time.monotonic()
        await asyncio.gather(
            tool.execute(file_path="/fake/a.jpg", file_type=1),
            quick_task(),
        )

        # quick_task 完成时刻相对起点应在 ~0.1s；如果事件循环被阻塞 0.3s，
        # quick_task 会等到 ~0.3s 后才开始调度 sleep(0.1)，完成时刻接近 0.4s。
        quick_elapsed = quick_done_at["t"] - start
        assert quick_elapsed < 0.25, (
            f"事件循环被阻塞，quick_task 在 {quick_elapsed:.3f}s 才完成（应 ~0.1s）"
        )




class TestPdfToMdConsumesOCRFields:
    """回归：pdf_to_md._ocr_fallback 依赖 full_text + texts，改造后仍可用。"""

    def test_ocr_fallback_consumes_new_structure(self):
        """_ocr_fallback 读取 full_text（markdown）和 len(texts)（页数）。"""
        from src.tools.pdf.pdf_to_md import _ocr_fallback
        mock_module = MagicMock()
        mock_module.paddleocr_doc_parsing.return_value = {
            "success": True,
            "full_text": "OCR extracted text",
            "texts": ["p1", "p2"],
            "truncated": False,
        }
        with patch.dict("sys.modules", {"src.tools.ocr.ocr_tool": mock_module}):
            result = _ocr_fallback("/fake/test.pdf")
        assert result["success"] is True
        assert result["markdown"] == "OCR extracted text"
        assert result["page_count"] == 2
