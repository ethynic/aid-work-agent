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
