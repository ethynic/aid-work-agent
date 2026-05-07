import os
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

    def test_parse_non_json_response(self):
        from src.tools.network.http_api import _parse_response

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.side_effect = ValueError("not json")
        mock_response.text = "<html>OK</html>"

        result = _parse_response(mock_response)
        assert result["success"] is True
        assert result["data"] == "<html>OK</html>"

    def test_parse_long_text_truncated(self):
        from src.tools.network.http_api import _parse_response

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.side_effect = ValueError("not json")
        mock_response.text = "x" * 10000

        result = _parse_response(mock_response)
        assert result["success"] is True
        assert len(result["data"]) < 10000
        assert "截断" in result["data"]

    def test_parse_server_error(self):
        from src.tools.network.http_api import _parse_response

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.json.return_value = {"error": "Internal Server Error"}

        result = _parse_response(mock_response)
        assert result["success"] is False
        assert result["status_code"] == 500


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
