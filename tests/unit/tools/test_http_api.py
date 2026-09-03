import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.tools


class TestHttpApiToolDefinition:
    def test_tool_name(self):
        from src.tools.network.http_api import HttpApiTool

        tool = HttpApiTool()
        assert tool.name == "http_api"

    def test_tool_has_input_model(self):
        from src.tools.network.http_api import HttpApiTool

        tool = HttpApiTool()
        assert tool.InputModel is not None

    def test_tool_definition_structure(self):
        from src.tools.network.http_api import HttpApiTool

        tool = HttpApiTool()
        defn = tool.to_tool_definition()
        assert "name" in defn
        assert "description" in defn
        assert "input_schema" in defn
        assert defn["name"] == "http_api"
        schema = defn["input_schema"]
        assert "url" in schema.get("required", [])

    def test_display_name_with_credential_masking(self):
        from src.tools.network.http_api import HttpApiTool

        tool = HttpApiTool()
        args = {
            "method": "GET",
            "url": "https://api.example.com?key=${API_KEY}",
        }
        display = tool.get_display_name(args)
        assert "HTTP GET" in display
        assert "${API_KEY}" not in display
        assert "***" in display

    def test_display_name_no_args(self):
        from src.tools.network.http_api import HttpApiTool

        tool = HttpApiTool()
        assert tool.get_display_name() == "HTTP API 调用"


class TestSubstituteEnvVars:
    def test_substitute_existing_var(self):
        from src.tools.network.http_api import _substitute_env_vars

        with patch.dict(os.environ, {"MY_TEST_KEY": "test_value_123"}):
            result = _substitute_env_vars("Bearer ${MY_TEST_KEY}")
            assert result == "Bearer test_value_123"

    def test_substitute_missing_var_keeps_placeholder(self):
        from src.tools.network.http_api import _substitute_env_vars

        result = _substitute_env_vars("Bearer ${NONEXISTENT_VAR_XYZ}")
        assert result == "Bearer ${NONEXISTENT_VAR_XYZ}"

    def test_substitute_multiple_vars(self):
        from src.tools.network.http_api import _substitute_env_vars

        with patch.dict(os.environ, {"KEY_A": "a", "KEY_B": "b"}):
            result = _substitute_env_vars("${KEY_A}:${KEY_B}")
            assert result == "a:b"

    def test_substitute_no_vars(self):
        from src.tools.network.http_api import _substitute_env_vars

        result = _substitute_env_vars("https://api.example.com")
        assert result == "https://api.example.com"

    def test_substitute_in_url_with_query(self):
        from src.tools.network.http_api import _substitute_env_vars

        with patch.dict(os.environ, {"API_KEY": "sk-abc"}):
            result = _substitute_env_vars(
                "https://api.example.com/search?key=${API_KEY}&q=test"
            )
            assert result == "https://api.example.com/search?key=sk-abc&q=test"


@pytest.fixture
def isolated_spill_dir(tmp_path, monkeypatch):
    """把落盘目录隔离到 tmp_path，避免污染真实临时目录 / 串扰其他测试。"""
    monkeypatch.setattr(
        "src.tools._spill.tempfile.gettempdir", lambda: str(tmp_path)
    )
    return tmp_path / "aid_agent_spill"


class TestParseResponse:
    def test_parse_json_success(self):
        from src.tools.network.http_api import _parse_response

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": 123, "name": "test"}

        result = _parse_response(mock_response)
        assert result["success"] is True
        assert result["status_code"] == 200
        assert result["data"] == {"id": 123, "name": "test"}
        assert "error" not in result
        # 小响应不落盘：无 file_path / truncated / full_size
        assert "file_path" not in result
        assert "truncated" not in result
        assert "full_size" not in result

    def test_parse_json_error(self):
        from src.tools.network.http_api import _parse_response

        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.json.return_value = {"message": "Unauthorized"}

        result = _parse_response(mock_response)
        assert result["success"] is False
        assert result["status_code"] == 401
        assert "error" in result
        assert "401" in result["error"]
        # 错误响应不落盘
        assert "file_path" not in result

    def test_parse_non_json_response(self):
        from src.tools.network.http_api import _parse_response

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.side_effect = ValueError("not json")
        mock_response.text = "<html>OK</html>"

        result = _parse_response(mock_response)
        assert result["success"] is True
        assert result["data"] == "<html>OK</html>"
        # 小文本响应不落盘
        assert "file_path" not in result
        assert "truncated" not in result

    def test_parse_long_text_spilled(self, isolated_spill_dir):
        """大文本响应（>5000）落盘：返回预览 + file_path + full_size + truncated。"""
        from src.tools.network.http_api import _parse_response

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.side_effect = ValueError("not json")
        text = "x" * 10000
        mock_response.text = text

        result = _parse_response(mock_response)
        assert result["success"] is True
        # 落盘标记齐备
        assert result["truncated"] is True
        assert "file_path" in result
        assert result["full_size"] == len(text)
        # 预览是截断字符串（前 5000 + "..."）
        assert isinstance(result["data"], str)
        assert result["data"].endswith("...")
        assert len(result["data"]) < len(text)
        # 落盘文件以 .txt 结尾，内容完整
        file_path = Path(result["file_path"])
        assert file_path.exists()
        assert file_path.suffix == ".txt"
        assert file_path.read_text(encoding="utf-8") == text

    def test_parse_short_text_not_truncated(self):
        """短文本响应不截断、不带 truncated 标记。"""
        from src.tools.network.http_api import _parse_response

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.side_effect = ValueError("not json")
        mock_response.text = "short body"

        result = _parse_response(mock_response)
        assert result["success"] is True
        assert result["data"] == "short body"
        assert "truncated" not in result
        # 小文本响应不落盘
        assert "file_path" not in result

    def test_parse_json_success_preserves_object(self):
        """小 JSON 响应保留原始对象结构语义，不转字符串、不落盘。"""
        from src.tools.network.http_api import _parse_response

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": 123, "name": "test"}

        result = _parse_response(mock_response)
        assert result["success"] is True
        assert result["data"] == {"id": 123, "name": "test"}
        # 未截断时 data 仍是原始对象，且无 truncated / file_path 标记
        assert "truncated" not in result
        assert "file_path" not in result

    def test_parse_large_json_spilled(self, isolated_spill_dir):
        """大 JSON 响应（>5000）落盘：预览 + file_path + full_size + truncated，
        且落盘文件内容与完整 JSON 一致。"""
        import json as _json

        from src.tools.network.http_api import _parse_response

        big_data = {"items": [{"v": i} for i in range(2000)]}
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = big_data

        result = _parse_response(mock_response)
        assert result["success"] is True
        # 落盘标记齐备
        assert result["truncated"] is True
        assert "file_path" in result
        # full_size 是完整序列化的字符数
        serialized = _json.dumps(big_data, ensure_ascii=False)
        assert result["full_size"] == len(serialized)
        # 预览是截断字符串（前 5000 + "..."），原始大对象未整体回塞
        assert isinstance(result["data"], str)
        assert result["data"].endswith("...")
        assert serialized not in result["data"]
        # 落盘文件以 .json 结尾，内容与完整序列化一致
        file_path = Path(result["file_path"])
        assert file_path.exists()
        assert file_path.suffix == ".json"
        assert file_path.read_text(encoding="utf-8") == serialized

    def test_parse_large_json_spill_file_greppable(self, isolated_spill_dir):
        """落盘文件可被 grep/read 消费：用关键词反查命中（闭环验证）。"""
        import json as _json

        from src.tools.network.http_api import _parse_response

        # 构造一个含可定位关键词的大 JSON
        big_data = {"records": [{"marker": "FINDME_" + str(i)} for i in range(1500)]}
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = big_data

        result = _parse_response(mock_response)
        file_path = Path(result["file_path"])
        content = file_path.read_text(encoding="utf-8")
        # 关键词命中（说明完整内容已落盘）
        assert "FINDME_1499" in content
        assert "FINDME_0" in content

    def test_parse_error_response_large_body_not_spilled(self, isolated_spill_dir):
        """错误响应（非 2xx）即使 body 很大也不落盘，data 截断、error 截断到 500。"""
        from src.tools.network.http_api import _parse_response

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.json.side_effect = ValueError("not json")
        mock_response.text = "E" * 10000

        result = _parse_response(mock_response)
        assert result["success"] is False
        assert result["status_code"] == 500
        # 错误响应不落盘
        assert "file_path" not in result
        # data 已截断（不灌入大体）+ 带 truncated 标记
        assert result["truncated"] is True
        assert isinstance(result["data"], str)
        assert len(result["data"]) <= 5000 + 3
        # error 截断到 500
        assert "error" in result
        assert "500" in result["error"]
        assert len(result["error"]) <= 500 + len("HTTP 500: ")

    def test_parse_large_error_json_truncated(self, isolated_spill_dir):
        """大 JSON 错误响应（非 2xx，>5000）data 降级为截断字符串，不落盘。"""
        import json as _json

        from src.tools.network.http_api import _parse_response

        big_data = {"items": ["detail_" + str(i) for i in range(2000)]}
        mock_response = MagicMock()
        mock_response.status_code = 502
        mock_response.json.return_value = big_data

        result = _parse_response(mock_response)
        assert result["success"] is False
        # 错误响应绝不落盘
        assert "file_path" not in result
        # data 降级为截断字符串（原对象不可整体回塞）
        serialized = _json.dumps(big_data, ensure_ascii=False)
        assert serialized not in result["data"]
        assert isinstance(result["data"], str)
        assert result["truncated"] is True

    def test_spill_meta_url_strips_query_secret(self, isolated_spill_dir):
        """落盘 meta 的 url 必须剥离 query/fragment，杜绝 token/凭证泄漏到临时文件。

        response.url 是 httpx 合并 query_params 后的完整 URL（${API_KEY} 已被替换
        为真实值）。meta.json 只允许写 scheme://host/path。
        """
        import httpx as _httpx

        from src.tools.network.http_api import _parse_response

        # 构造带敏感 query 的大 JSON 响应（用真实 httpx.URL，而非 MagicMock）
        big_data = {"records": [{"i": i} for i in range(1500)]}
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = big_data
        # 真实 httpx URL：含 token query 与 fragment
        mock_response.url = _httpx.URL(
            "https://api.example.com/orders?api_key=sk-secret-real-123&q=x#frag"
        )

        result = _parse_response(mock_response)
        assert "file_path" in result

        # 读 meta.json，断言不含任何敏感片段
        meta_path = Path(result["file_path"]).with_name(
            Path(result["file_path"]).name + ".meta.json"
        )
        assert meta_path.exists()
        meta_text = meta_path.read_text(encoding="utf-8")
        assert "sk-secret-real-123" not in meta_text
        assert "api_key" not in meta_text
        assert "frag" not in meta_text
        # 仅保留安全部分
        assert "api.example.com/orders" in meta_text

    def test_parse_server_error(self):
        from src.tools.network.http_api import _parse_response

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.json.return_value = {"error": "Internal Server Error"}

        result = _parse_response(mock_response)
        assert result["success"] is False
        assert result["status_code"] == 500

    def test_parse_business_error_code_nonzero_reclassifies_failure(self):
        """HTTP 200 + Code!=0 -> 重分类为 success=False 并记 error（进入 log_error 表）。

        外部 ERP 接口用 HTTP 200 承载业务失败（如 {"Code": -1, "Error": "非空内容"}），
        http_api 层识别为业务错误并标记失败，管理员在 /portal/error-logs 直接可见，
        无需翻 LLM 上下文。
        """
        from src.tools.network.http_api import _parse_response

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"Code": -1, "Error": "非空内容"}

        result = _parse_response(mock_response)
        assert result["success"] is False
        assert "业务错误" in result["error"]
        assert "Code=-1" in result["error"]
        assert "非空内容" in result["error"]
        # data 保留原始响应体，供 LLM 对照修正参数后重试
        assert result["data"] == {"Code": -1, "Error": "非空内容"}
        # 业务错误等同失败语义：不落盘
        assert "file_path" not in result

    def test_parse_code_zero_keeps_success(self):
        """HTTP 200 + Code=0 -> 业务成功，保持 success=True。"""
        from src.tools.network.http_api import _parse_response

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"Code": 0, "Data": {"id": 1}}

        result = _parse_response(mock_response)
        assert result["success"] is True
        assert "error" not in result
        assert result["data"] == {"Code": 0, "Data": {"id": 1}}

    def test_parse_code_allowed_values_keep_success(self):
        """Code 允许值 200 / \"0\" / \"200\" 均视为成功，避免误伤。"""
        from src.tools.network.http_api import _parse_response

        for code in (200, "0", "200"):
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"Code": code, "Data": "ok"}
            result = _parse_response(mock_response)
            assert result["success"] is True, f"Code={code!r} 应为成功"
            assert "error" not in result

    def test_parse_no_code_field_keeps_success(self):
        """响应体无 Code 字段 -> 不判定业务错误（保守门控，避免误伤其他系统）。"""
        from src.tools.network.http_api import _parse_response

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "success", "data": [1, 2, 3]}

        result = _parse_response(mock_response)
        assert result["success"] is True
        assert "error" not in result
        assert result["data"] == {"status": "success", "data": [1, 2, 3]}

    def test_parse_business_error_non_dict_keeps_success(self):
        """响应体非 dict（列表/字符串）不判定业务错误。"""
        from src.tools.network.http_api import _parse_response

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = ["a", "b"]

        result = _parse_response(mock_response)
        assert result["success"] is True
        assert result["data"] == ["a", "b"]
        assert "error" not in result

    def test_parse_business_error_large_body_truncated_not_spilled(
        self, isolated_spill_dir
    ):
        """大业务错误响应（Code!=0 且 >5000）data 截断、不落盘（等同错误响应语义）。"""
        from src.tools.network.http_api import _parse_response

        big_data = {"Code": -1, "Error": "E" * 8000}
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = big_data

        result = _parse_response(mock_response)
        assert result["success"] is False
        assert "file_path" not in result
        assert result["truncated"] is True
        assert isinstance(result["data"], str)
        assert len(result["data"]) <= 5000 + 3

    def test_parse_non_2xx_logging_does_not_crash_mock_without_request(self):
        """非 2xx 路径补 error 日志时，MagicMock（无 .request/.url）不抛异常。"""
        from src.tools.network.http_api import _parse_response

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.json.return_value = {"message": "boom"}

        result = _parse_response(mock_response)
        assert result["success"] is False
        assert result["status_code"] == 500
        assert "500" in result["error"]


class TestSafeUrlHelpers:
    def test_safe_url_str_strips_query_userinfo_and_fragment(self):
        from src.tools.network.http_api import _safe_url_str

        result = _safe_url_str(
            "https://user:pass@api.example.com/orders?api_key=sk-secret&q=x#frag"
        )
        assert result == "https://api.example.com/orders"
        assert "sk-secret" not in result
        assert "user:pass" not in result
        assert "frag" not in result

    def test_safe_url_str_empty(self):
        from src.tools.network.http_api import _safe_url_str

        assert _safe_url_str("") == ""

    def test_safe_url_str_no_scheme_strips_query(self):
        from src.tools.network.http_api import _safe_url_str

        result = _safe_url_str("api.example.com/orders?key=sk-secret")
        assert result == "api.example.com/orders"

    def test_safe_method_returns_empty_for_mock_without_request(self):
        from src.tools.network.http_api import _safe_method

        mock_response = MagicMock()  # 无 .request 属性
        assert _safe_method(mock_response) == ""


class TestBusinessErrorHelpers:
    def test_extract_detects_nonzero_code(self):
        from src.tools.network.http_api import _extract_business_error

        assert _extract_business_error({"Code": -1, "Error": "非空内容"}) == (
            "Code=-1: 非空内容"
        )

    def test_extract_none_when_code_success(self):
        from src.tools.network.http_api import _extract_business_error

        for code in (0, 200, "0", "200"):
            assert _extract_business_error({"Code": code, "Error": "x"}) is None

    def test_extract_none_when_no_code_field(self):
        from src.tools.network.http_api import _extract_business_error

        assert _extract_business_error({"status": "ok"}) is None
        assert _extract_business_error(["a", "b"]) is None
        assert _extract_business_error("plain text") is None
        assert _extract_business_error(None) is None

    def test_extract_without_error_field(self):
        from src.tools.network.http_api import _extract_business_error

        assert _extract_business_error({"Code": -99}) == "Code=-99: "

    def test_correlation_suffix_empty_without_tool_context(self):
        """无工具执行上下文（测试环境/独立调用）时返回空串，不干扰日志。"""
        from src.tools.network.http_api import _correlation_suffix

        assert _correlation_suffix() == ""

    def test_correlation_suffix_includes_context(self):
        """工具执行上下文可用时，后缀携带 tenant/session/subagent 便于定位。"""
        from src.tools.network.http_api import _correlation_suffix
        from src.tools.context import ToolExecutionContext, tool_execution_scope

        ctx = ToolExecutionContext(
            tenant_id="tenant_001",
            session_id="sess_1",
            subagent_id="pre-sales",
        )
        with tool_execution_scope(ctx):
            suffix = _correlation_suffix()
        assert "tenant=tenant_001" in suffix
        assert "session=sess_1" in suffix
        assert "subagent=pre-sales" in suffix


class TestHttpApiToolExecute:
    @pytest.mark.asyncio
    async def test_execute_success_get(self):
        from src.tools.network.http_api import HttpApiTool

        tool = HttpApiTool()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"result": "ok"}

        with patch("src.tools.network.http_api.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await tool.execute(
                method="GET",
                url="https://api.example.com/data",
            )
            assert result["success"] is True
            assert result["data"] == {"result": "ok"}

    @pytest.mark.asyncio
    async def test_execute_with_env_var_substitution(self):
        from src.tools.network.http_api import HttpApiTool

        tool = HttpApiTool()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": "ok"}

        with patch.dict(os.environ, {"TEST_API_TOKEN": "sk-secret123"}):
            with patch("src.tools.network.http_api.httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.request = AsyncMock(return_value=mock_response)
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                mock_client_cls.return_value = mock_client

                result = await tool.execute(
                    method="GET",
                    url="https://api.example.com/data",
                    headers={"Authorization": "Bearer ${TEST_API_TOKEN}"},
                )
                assert result["success"] is True
                # Verify the substituted header was passed
                call_kwargs = mock_client.request.call_args
                assert call_kwargs[1]["headers"]["Authorization"] == "Bearer sk-secret123"

    @pytest.mark.asyncio
    async def test_execute_post_with_body(self):
        from src.tools.network.http_api import HttpApiTool

        tool = HttpApiTool()
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"id": 1}

        with patch("src.tools.network.http_api.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await tool.execute(
                method="POST",
                url="https://api.example.com/data",
                body={"name": "test", "value": 42},
            )
            assert result["success"] is True
            assert result["status_code"] == 201
            call_kwargs = mock_client.request.call_args
            assert call_kwargs[1]["json"] == {"name": "test", "value": 42}

    @pytest.mark.asyncio
    async def test_execute_timeout_error(self):
        from src.tools.network.http_api import HttpApiTool
        import httpx

        tool = HttpApiTool()

        with patch("src.tools.network.http_api.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await tool.execute(
                method="GET",
                url="https://api.example.com/slow",
                timeout=5,
            )
            assert result["success"] is False
            assert "超时" in result["error"]

    @pytest.mark.asyncio
    async def test_execute_connect_error(self):
        from src.tools.network.http_api import HttpApiTool
        import httpx

        tool = HttpApiTool()

        with patch("src.tools.network.http_api.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(
                side_effect=httpx.ConnectError("connection refused")
            )
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await tool.execute(
                method="GET",
                url="https://unreachable.example.com",
            )
            assert result["success"] is False
            assert "连接失败" in result["error"]

    @pytest.mark.asyncio
    async def test_execute_empty_url(self):
        from src.tools.network.http_api import HttpApiTool

        tool = HttpApiTool()
        result = await tool.execute(url="")
        assert result["success"] is False
        assert "URL" in result["error"]

    @pytest.mark.asyncio
    async def test_execute_with_query_params(self):
        from src.tools.network.http_api import HttpApiTool

        tool = HttpApiTool()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"items": []}

        with patch("src.tools.network.http_api.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await tool.execute(
                method="GET",
                url="https://api.example.com/search",
                query_params={"page": "1", "size": "10"},
            )
            assert result["success"] is True
            call_kwargs = mock_client.request.call_args
            assert call_kwargs[1]["params"] == {"page": "1", "size": "10"}

    @pytest.mark.asyncio
    async def test_execute_with_form_data(self):
        from src.tools.network.http_api import HttpApiTool

        tool = HttpApiTool()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"ok": True}

        with patch("src.tools.network.http_api.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await tool.execute(
                method="POST",
                url="https://api.example.com/submit",
                form_data={"field1": "value1", "field2": "value2"},
            )
            assert result["success"] is True
            call_kwargs = mock_client.request.call_args
            assert call_kwargs[1]["data"] == {"field1": "value1", "field2": "value2"}
            # body should not be present when form_data is used
            assert "json" not in call_kwargs[1]


class TestResolveFilePath:
    def test_resolve_absolute_path(self, tmp_path):
        from src.tools.network.http_api import _resolve_file_path

        test_file = tmp_path / "test.txt"
        test_file.write_text("hello")
        result = _resolve_file_path(str(test_file))
        assert result is not None
        assert result.name == "test.txt"

    def test_resolve_nonexistent_path(self):
        from src.tools.network.http_api import _resolve_file_path

        result = _resolve_file_path("/nonexistent/path/file.txt")
        assert result is None

    def test_resolve_relative_path_in_cwd(self, tmp_path, monkeypatch):
        from src.tools.network.http_api import _resolve_file_path

        test_file = tmp_path / "relative.txt"
        test_file.write_text("data")
        monkeypatch.chdir(tmp_path)
        result = _resolve_file_path("relative.txt")
        assert result is not None
        assert result.name == "relative.txt"


class TestPrepareFiles:
    def test_prepare_single_file(self, tmp_path):
        from src.tools.network.http_api import HttpApiTool

        test_file = tmp_path / "upload.txt"
        test_file.write_text("file content")

        httpx_files, opened = HttpApiTool._prepare_files(
            {"file": str(test_file)}
        )

        try:
            assert httpx_files is not None
            assert "file" in httpx_files
            assert httpx_files["file"][0] == "upload.txt"
        finally:
            for f in opened:
                f.close()

    def test_prepare_multiple_files(self, tmp_path):
        from src.tools.network.http_api import HttpApiTool

        f1 = tmp_path / "a.txt"
        f1.write_text("aaa")
        f2 = tmp_path / "b.pdf"
        f2.write_text("bbb")

        httpx_files, opened = HttpApiTool._prepare_files(
            {"doc": str(f1), "attachment": str(f2)}
        )

        try:
            assert httpx_files is not None
            assert "doc" in httpx_files
            assert "attachment" in httpx_files
            assert httpx_files["doc"][0] == "a.txt"
            assert httpx_files["attachment"][0] == "b.pdf"
        finally:
            for f in opened:
                f.close()

    def test_prepare_nonexistent_file(self):
        from src.tools.network.http_api import HttpApiTool

        httpx_files, opened = HttpApiTool._prepare_files(
            {"file": "/nonexistent/file.txt"}
        )
        assert httpx_files is None
        assert opened == []

    def test_prepare_file_too_large(self, tmp_path):
        from src.tools.network import http_api

        # 创建一个小文件但 mock MAX_FILE_SIZE 为 0
        test_file = tmp_path / "big.txt"
        test_file.write_text("x")

        original_max = http_api.MAX_FILE_SIZE
        try:
            http_api.MAX_FILE_SIZE = 0
            httpx_files, opened = http_api.HttpApiTool._prepare_files(
                {"file": str(test_file)}
            )
            assert httpx_files is None
        finally:
            http_api.MAX_FILE_SIZE = original_max


class TestHttpApiFileUpload:
    @pytest.mark.asyncio
    async def test_file_upload(self, tmp_path):
        from src.tools.network.http_api import HttpApiTool

        test_file = tmp_path / "data.csv"
        test_file.write_text("col1,col2\n1,2")

        tool = HttpApiTool()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"uploaded": True}

        with patch("src.tools.network.http_api.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await tool.execute(
                method="POST",
                url="https://api.example.com/upload",
                files={"file": str(test_file)},
            )
            assert result["success"] is True
            call_kwargs = mock_client.request.call_args
            assert "files" in call_kwargs[1]
            assert call_kwargs[1]["files"]["file"][0] == "data.csv"

    @pytest.mark.asyncio
    async def test_file_upload_with_form_data(self, tmp_path):
        from src.tools.network.http_api import HttpApiTool

        test_file = tmp_path / "doc.pdf"
        test_file.write_text("pdf content")

        tool = HttpApiTool()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"ok": True}

        with patch("src.tools.network.http_api.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await tool.execute(
                method="POST",
                url="https://api.example.com/upload",
                files={"file": str(test_file)},
                form_data={"category": "report", "description": "月度报告"},
            )
            assert result["success"] is True
            call_kwargs = mock_client.request.call_args
            assert "files" in call_kwargs[1]
            assert call_kwargs[1]["data"] == {
                "category": "report",
                "description": "月度报告",
            }

    @pytest.mark.asyncio
    async def test_files_and_body_mutually_exclusive(self):
        from src.tools.network.http_api import HttpApiTool

        tool = HttpApiTool()
        result = await tool.execute(
            method="POST",
            url="https://api.example.com/upload",
            files={"file": "/some/path.txt"},
            body={"name": "test"},
        )
        assert result["success"] is False
        assert "不能同时使用" in result["error"]

    @pytest.mark.asyncio
    async def test_file_upload_nonexistent_file(self):
        from src.tools.network.http_api import HttpApiTool

        tool = HttpApiTool()
        result = await tool.execute(
            method="POST",
            url="https://api.example.com/upload",
            files={"file": "/nonexistent/file.txt"},
        )
        assert result["success"] is False
        assert "文件准备失败" in result["error"]


class TestCoerceJsonObject:
    def test_parse_valid_json_object_string(self):
        from src.tools.network.http_api import _coerce_json_object

        assert _coerce_json_object('{"a": 1}') == {"a": 1}

    def test_parse_valid_json_list_string(self):
        from src.tools.network.http_api import _coerce_json_object

        assert _coerce_json_object('[1, 2, 3]') == [1, 2, 3]

    def test_invalid_json_string_kept_unchanged(self):
        from src.tools.network.http_api import _coerce_json_object

        # 非 JSON 字符串（如普通文本 body）原样返回，不破坏既有行为
        assert _coerce_json_object("hello world") == "hello world"

    def test_non_string_kept_unchanged(self):
        from src.tools.network.http_api import _coerce_json_object

        assert _coerce_json_object({"a": 1}) == {"a": 1}
        assert _coerce_json_object(None) is None
        assert _coerce_json_object(123) == 123

    def test_empty_string_kept_unchanged(self):
        from src.tools.network.http_api import _coerce_json_object

        assert _coerce_json_object("") == ""
        assert _coerce_json_object("   ") == "   "


class TestHttpApiJsonStringTolerance:
    """#1 修复：LLM 把 headers/body 等整体序列化成 JSON 字符串传参时兼容处理"""

    def test_validate_parameters_accepts_string_headers(self):
        from src.tools.network.http_api import HttpApiTool

        tool = HttpApiTool()
        # 修复前：headers 传 JSON 字符串会 dict_type 校验失败
        assert tool.validate_parameters(
            method="GET",
            url="https://api.example.com/data",
            headers='{"Authorization": "Bearer xxx"}',
        ) is True

    def test_validate_parameters_accepts_string_body(self):
        from src.tools.network.http_api import HttpApiTool

        tool = HttpApiTool()
        assert tool.validate_parameters(
            method="POST",
            url="https://api.example.com/data",
            body='{"customer": "123"}',
        ) is True

    def test_validate_parameters_accepts_string_query_params(self):
        from src.tools.network.http_api import HttpApiTool

        tool = HttpApiTool()
        assert tool.validate_parameters(
            method="GET",
            url="https://api.example.com/data",
            query_params='{"page": "1"}',
        ) is True

    def test_validate_parameters_still_rejects_invalid_json_string_headers(self):
        """非 JSON 字符串保持原样，仍应校验失败（headers 必须是字典）。"""
        from src.tools.network.http_api import HttpApiTool

        tool = HttpApiTool()
        assert tool.validate_parameters(
            method="GET",
            url="https://api.example.com/data",
            headers="Authorization Bearer xxx",  # 不是合法 JSON
        ) is False

    @pytest.mark.asyncio
    async def test_execute_coerces_string_headers_and_substitutes(self):
        from src.tools.network.http_api import HttpApiTool

        tool = HttpApiTool()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": "ok"}

        with patch.dict(os.environ, {"TEST_API_TOKEN": "sk-secret123"}):
            with patch("src.tools.network.http_api.httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.request = AsyncMock(return_value=mock_response)
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                mock_client_cls.return_value = mock_client

                result = await tool.execute(
                    method="GET",
                    url="https://api.example.com/data",
                    headers='{"Authorization": "Bearer ${TEST_API_TOKEN}"}',
                )
                assert result["success"] is True
                call_kwargs = mock_client.request.call_args
                assert call_kwargs[1]["headers"] == {
                    "Authorization": "Bearer sk-secret123"
                }

    @pytest.mark.asyncio
    async def test_execute_coerces_string_body_to_json_object(self):
        """body 传 JSON 字符串时，httpx 的 json= 应收到解析后的字典而非字符串字面量。"""
        from src.tools.network.http_api import HttpApiTool

        tool = HttpApiTool()
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"id": 1}

        with patch("src.tools.network.http_api.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await tool.execute(
                method="POST",
                url="https://api.example.com/data",
                body='{"customer": "123"}',
            )
            assert result["success"] is True
            call_kwargs = mock_client.request.call_args
            assert call_kwargs[1]["json"] == {"customer": "123"}

    @pytest.mark.asyncio
    async def test_execute_coerces_string_query_params(self):
        from src.tools.network.http_api import HttpApiTool

        tool = HttpApiTool()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": "ok"}

        with patch("src.tools.network.http_api.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await tool.execute(
                method="GET",
                url="https://api.example.com/search",
                query_params='{"page": "1", "size": "10"}',
            )
            assert result["success"] is True
            call_kwargs = mock_client.request.call_args
            assert call_kwargs[1]["params"] == {"page": "1", "size": "10"}

    def test_get_validation_errors_includes_type_error(self):
        """#2 修复：类型错误（dict_type）纳入校验错误，不再返回误导性空列表。"""
        from src.tools.network.http_api import HttpApiTool

        tool = HttpApiTool()
        errors = tool.get_validation_errors(
            method="GET",
            url="https://api.example.com/data",
            headers="Authorization Bearer xxx",  # 非法 headers（非 JSON 字符串/字典）
        )
        assert errors, "应返回可读校验错误，而非空列表"
        joined = "; ".join(errors)
        assert "headers" in joined
        assert "实际输入类型" in joined
        assert "str" in joined

    def test_get_validation_errors_empty_when_valid(self):
        from src.tools.network.http_api import HttpApiTool

        tool = HttpApiTool()
        assert tool.get_validation_errors(
            method="GET",
            url="https://api.example.com/data",
            headers={"Authorization": "Bearer xxx"},
        ) == []

    def test_get_validation_errors_missing_url(self):
        from src.tools.network.http_api import HttpApiTool

        tool = HttpApiTool()
        errors = tool.get_validation_errors(method="GET")
        assert errors
        joined = "; ".join(errors)
        assert "url" in joined
        assert "未提供" in joined


class TestTenantEnvVarSubstitution:
    """租户级环境变量经 ToolExecutionContext 传递（替代旧的 os.environ 注入）"""

    def test_tenant_env_var_preferred_over_process_env(self):
        from src.tools.context import ToolExecutionContext, tool_execution_scope
        from src.tools.network.http_api import _substitute_env_vars

        ctx = ToolExecutionContext(tenant_id="t1", env_vars={"AGENT_TOKEN": "tenant_token"})
        with tool_execution_scope(ctx):
            with patch.dict(os.environ, {"AGENT_TOKEN": "process_token"}):
                assert _substitute_env_vars("Bearer ${AGENT_TOKEN}") == "Bearer tenant_token"

    def test_fallback_to_process_env_without_context(self):
        from src.tools.network.http_api import _substitute_env_vars

        with patch.dict(os.environ, {"MY_KEY_ZZ": "v123"}):
            assert _substitute_env_vars("Bearer ${MY_KEY_ZZ}") == "Bearer v123"

    def test_missing_var_keeps_placeholder(self):
        from src.tools.context import ToolExecutionContext, tool_execution_scope
        from src.tools.network.http_api import _substitute_env_vars

        with tool_execution_scope(ToolExecutionContext(tenant_id="t1", env_vars={})):
            result = _substitute_env_vars("Bearer ${MISSING_VAR_AB}")
        assert result == "Bearer ${MISSING_VAR_AB}"


class TestUnresolvedPlaceholderFailFast:
    """凭证占位符未解析时应拒绝请求，而不是把 ${VAR} 字面量发给第三方"""

    @pytest.mark.asyncio
    async def test_unresolved_header_token_rejected(self):
        from src.tools.network.http_api import HttpApiTool

        tool = HttpApiTool()
        result = await tool.execute(
            method="POST",
            url="https://erp.example.com/api/login",
            headers={"Authorization": "Bearer ${AGENT_TOKEN}"},
        )
        assert result["success"] is False
        assert "AGENT_TOKEN" in result["error"]

    @pytest.mark.asyncio
    async def test_unresolved_url_var_rejected(self):
        from src.tools.network.http_api import HttpApiTool

        tool = HttpApiTool()
        result = await tool.execute(
            method="GET",
            url="https://api.example.com/data?key=${API_KEY_ZZ}",
        )
        assert result["success"] is False
        assert "API_KEY_ZZ" in result["error"]

    @pytest.mark.asyncio
    async def test_resolved_tenant_var_proceeds(self):
        from src.tools.context import ToolExecutionContext, tool_execution_scope
        from src.tools.network.http_api import HttpApiTool

        tool = HttpApiTool()
        ctx = ToolExecutionContext(tenant_id="t1", env_vars={"AGENT_TOKEN": "real_token"})
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"Code": 0}
        with tool_execution_scope(ctx):
            with patch("src.tools.network.http_api.httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.request = AsyncMock(return_value=mock_response)
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                mock_client_cls.return_value = mock_client

                result = await tool.execute(
                    method="POST",
                    url="https://erp.example.com/api/login",
                    headers={"Authorization": "Bearer ${AGENT_TOKEN}"},
                )

        assert result["success"] is True
        sent_headers = mock_client.request.call_args.kwargs["headers"]
        assert sent_headers["Authorization"] == "Bearer real_token"
