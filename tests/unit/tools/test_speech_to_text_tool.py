"""
语音转文字工具单元测试

覆盖场景：
- 工具定义（name/description/schema）
- 缺少凭证配置（access_key/appkey）
- 音频内容为空
- base64 编码内容解码失败
- 本地文件路径不存在
- 阿里云 ASR 调用成功 / 失败 / 异常
- _transcribe_voice_with_asr 入口函数（channel_routes 集成路径）

复现的 Bug：日志中 "[wecom_kf] 调用 ASR 工具异常: unmatched '{' in format spec"
"""

import base64
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.tools, pytest.mark.asr]


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def mock_asr_config():
    """Mock 完整的 ASR 配置"""
    from src.config.settings import ASRToolConfig
    return ASRToolConfig(
        aliyun_access_key_id="test-access-key-id",
        aliyun_access_key_secret="test-access-key-secret",
        aliyun_appkey="test-appkey",
        endpoint="nls-gateway-cn-shanghai.aliyuncs.com",
    )


@pytest.fixture
def valid_audio_b64():
    """合法的 base64 编码音频（实际是 SILK_V3 magic，测试工具能否处理）"""
    return base64.b64encode(b"\x02\x23!SILK_V3" + b"\x00" * 100).decode("utf-8")


@pytest.fixture
def tool():
    """创建工具实例"""
    from src.tools.asr.speech_to_text_tool import SpeechToTextTool
    return SpeechToTextTool()


# ============================================================
# 1. 工具定义测试
# ============================================================


class TestSpeechToTextToolDefinition:
    """工具元数据测试"""

    def test_tool_basic_attributes(self, tool):
        assert tool.name == "speech_to_text"
        assert tool.category == "asr"
        assert "语音" in tool.display_name or "语音" in tool.description

    def test_tool_definition_schema(self, tool):
        defn = tool.to_tool_definition()
        assert defn["name"] == "speech_to_text"
        assert "input_schema" in defn
        schema = defn["input_schema"]
        # InputModel 应当自动生成 JSON Schema
        assert "properties" in schema
        assert "audio_content" in schema["properties"]
        assert "audio_content" in schema.get("required", [])

    def test_display_name_default(self, tool):
        assert tool.get_display_name() == "语音转文字"

    def test_display_name_with_language(self, tool):
        assert tool.get_display_name({"language": "zh_cn"}) == "语音转文字（中文）"
        assert tool.get_display_name({"language": "en"}) == "语音转文字（英文）"


# ============================================================
# 2. 参数校验测试
# ============================================================


class TestSpeechToTextParameterValidation:
    """输入参数校验"""

    @pytest.mark.asyncio
    async def test_empty_audio_content(self, tool):
        result = await tool.execute(audio_content="")
        assert result["success"] is False
        assert "请提供音频文件" in result["error"]


# ============================================================
# 3. 凭证配置测试
# ============================================================


class TestSpeechToTextCredentialCheck:
    """阿里云 ASR 凭证校验"""

    @pytest.mark.asyncio
    async def test_missing_access_key_id(self, tool, mock_asr_config):
        mock_asr_config.aliyun_access_key_id = ""
        with patch("src.tools.asr.speech_to_text_tool.settings") as mock_settings:
            mock_settings.tools.asr = mock_asr_config
            result = await tool.execute(audio_content=base64.b64encode(b"x" * 100).decode())
        assert result["success"] is False
        assert "ALIYUN_ASR_ACCESS_KEY_ID" in result["error"]

    @pytest.mark.asyncio
    async def test_missing_access_key_secret(self, tool, mock_asr_config):
        mock_asr_config.aliyun_access_key_secret = ""
        with patch("src.tools.asr.speech_to_text_tool.settings") as mock_settings:
            mock_settings.tools.asr = mock_asr_config
            result = await tool.execute(audio_content=base64.b64encode(b"x" * 100).decode())
        assert result["success"] is False
        assert "ALIYUN_ASR_ACCESS_KEY_SECRET" in result["error"]

    @pytest.mark.asyncio
    async def test_missing_appkey(self, tool, mock_asr_config):
        mock_asr_config.aliyun_appkey = ""
        with patch("src.tools.asr.speech_to_text_tool.settings") as mock_settings:
            mock_settings.tools.asr = mock_asr_config
            result = await tool.execute(audio_content=base64.b64encode(b"x" * 100).decode())
        assert result["success"] is False
        assert "ALIYUN_ASR_APPKEY" in result["error"]


# ============================================================
# 4. 输入内容校验测试
# ============================================================


class TestSpeechToTextInputProcessing:
    """base64 / 文件路径处理"""

    @pytest.mark.asyncio
    async def test_invalid_base64_content(self, tool, mock_asr_config):
        """非法 base64 内容"""
        with patch("src.tools.asr.speech_to_text_tool.settings") as mock_settings:
            mock_settings.tools.asr = mock_asr_config
            result = await tool.execute(audio_content="!!!not-valid-base64@@@")
        assert result["success"] is False
        assert "base64" in result["error"]

    @pytest.mark.asyncio
    async def test_local_file_not_exists(self, tool, mock_asr_config):
        """本地文件路径不存在"""
        with patch("src.tools.asr.speech_to_text_tool.settings") as mock_settings:
            mock_settings.tools.asr = mock_asr_config
            result = await tool.execute(audio_content="/nonexistent/path/audio.mp3")
        assert result["success"] is False
        assert "不存在" in result["error"]

    @pytest.mark.asyncio
    async def test_audio_too_large(self, tool, mock_asr_config):
        """音频文件超过 10MB"""
        with patch("src.tools.asr.speech_to_text_tool.settings") as mock_settings:
            mock_settings.tools.asr = mock_asr_config
            large_audio = base64.b64encode(b"x" * (11 * 1024 * 1024)).decode()
            result = await tool.execute(audio_content=large_audio)
        assert result["success"] is False
        assert "10MB" in result["error"]

    @pytest.mark.asyncio
    async def test_local_file_read_success(self, tool, mock_asr_config, tmp_path):
        """本地文件能正常读取并传递到 ASR 调用"""
        audio_file = tmp_path / "test.mp3"
        audio_file.write_bytes(b"\x02\x23!SILK_V3" + b"\x00" * 100)

        with patch("src.tools.asr.speech_to_text_tool.settings") as mock_settings:
            mock_settings.tools.asr = mock_asr_config
            with patch.object(tool, "_call_aliyun_asr", new=AsyncMock(return_value={
                "success": True, "text": "你好世界", "message": "成功"
            })) as mock_call:
                result = await tool.execute(audio_content=str(audio_file))
                assert result["success"] is True
                # 验证 _call_aliyun_asr 被调用
                assert mock_call.called
                call_kwargs = mock_call.call_args.kwargs
                assert call_kwargs["audio_format"] == "mp3"
                assert call_kwargs["audio_bytes"].startswith(b"\x02\x23!SILK_V3")


# ============================================================
# 5. 阿里云 ASR 网络调用测试
# ============================================================


class TestSpeechToTextAliyunAPI:
    """阿里云 NLS API 调用（mock aiohttp）"""

    @pytest.mark.asyncio
    async def test_get_token_url_construction(self, tool, mock_asr_config):
        """验证 GetToken URL 构造正确（捕获 unmatched '{' in format spec 错误）"""
        import src.tools.asr.speech_to_text_tool as asr_module
        # 清空 token 缓存
        asr_module._TOKEN_CACHE["token"] = ""
        asr_module._TOKEN_CACHE["expire_at"] = 0.0

        # 模拟 aiohttp 响应
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.text = AsyncMock(return_value='{"Token":{"Id":"test-token","ExpireTime":9999999999}}')

        mock_session = MagicMock()
        mock_session.get.return_value.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_session.get.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch("src.tools.asr.speech_to_text_tool.aiohttp.ClientSession") as mock_cs:
            mock_cs.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_cs.return_value.__aexit__ = AsyncMock(return_value=False)

            # 关键：捕获日志中提到的 ValueError
            try:
                token = await tool._get_or_refresh_token(
                    access_key_id=mock_asr_config.aliyun_access_key_id,
                    access_key_secret=mock_asr_config.aliyun_access_key_secret,
                )
                assert token == "test-token"
            except ValueError as e:
                if "unmatched '{' in format" in str(e):
                    pytest.fail(f"复现 Bug: 'unmatched {{' in format spec' 错误: {e}")
                raise

    @pytest.mark.asyncio
    async def test_get_token_uses_createtoken_api(self, tool, mock_asr_config):
        """回归测试：使用阿里云 OpenAPI CreateToken（Version=2019-02-28），不再使用旧的 GetToken（2018-05-18）

        复现的 Bug：日志中 'GetToken HTTP 404: Specified api is not found'
        根因：旧代码使用已废弃的 /pop/2018-05-18/GetToken 路径和 Action=GetToken，应改用
        OpenAPI 风格的 CreateToken（Version=2019-02-28，路径为 /）。
        """
        import src.tools.asr.speech_to_text_tool as asr_module
        asr_module._TOKEN_CACHE["token"] = ""
        asr_module._TOKEN_CACHE["expire_at"] = 0.0

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.text = AsyncMock(return_value='{"Token":{"Id":"test-token","ExpireTime":9999999999}}')

        mock_session = MagicMock()
        mock_session.get.return_value.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_session.get.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch("src.tools.asr.speech_to_text_tool.aiohttp.ClientSession") as mock_cs:
            mock_cs.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_cs.return_value.__aexit__ = AsyncMock(return_value=False)

            await tool._get_or_refresh_token(
                access_key_id=mock_asr_config.aliyun_access_key_id,
                access_key_secret=mock_asr_config.aliyun_access_key_secret,
            )

            # 断言：URL 应该是 OpenAPI 风格的 / 路径（不是 /pop/2018-05-18/GetToken）
            assert mock_session.get.called, "未调用 session.get"
            called_url = mock_session.get.call_args.args[0]
            assert "nls-meta.cn-shanghai.aliyuncs.com" in called_url, f"域名错误: {called_url}"
            assert "/pop/2018-05-18/GetToken" not in called_url, (
                f"不应使用已废弃的 /pop/2018-05-18/GetToken 路径: {called_url}"
            )
            # CreateToken 路径应为根路径 /
            assert called_url.startswith("https://nls-meta.cn-shanghai.aliyuncs.com/?"), (
                f"URL 应以根路径开头: {called_url}"
            )
            # 关键参数校验
            assert "Action=CreateToken" in called_url, f"应使用 Action=CreateToken: {called_url}"
            assert "Version=2019-02-28" in called_url, f"应使用 Version=2019-02-28: {called_url}"
            # CreateToken 不应再带 AppKey 参数
            assert "AppKey=" not in called_url, f"CreateToken 不应带 AppKey 参数: {called_url}"

    @pytest.mark.asyncio
    async def test_get_token_http_error(self, tool, mock_asr_config):
        """GetToken HTTP 错误处理"""
        import src.tools.asr.speech_to_text_tool as asr_module
        asr_module._TOKEN_CACHE["token"] = ""
        asr_module._TOKEN_CACHE["expire_at"] = 0.0

        mock_resp = MagicMock()
        mock_resp.status = 401
        mock_resp.text = AsyncMock(return_value="Invalid credentials")

        mock_session = MagicMock()
        mock_session.get.return_value.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_session.get.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch("src.tools.asr.speech_to_text_tool.aiohttp.ClientSession") as mock_cs:
            mock_cs.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_cs.return_value.__aexit__ = AsyncMock(return_value=False)

            with pytest.raises(RuntimeError, match="GetToken HTTP 401"):
                await tool._get_or_refresh_token(
                    access_key_id=mock_asr_config.aliyun_access_key_id,
                    access_key_secret=mock_asr_config.aliyun_access_key_secret,
                )

    @pytest.mark.asyncio
    async def test_get_token_response_missing_id(self, tool, mock_asr_config):
        """GetToken 响应缺少 Token.Id"""
        import src.tools.asr.speech_to_text_tool as asr_module
        asr_module._TOKEN_CACHE["token"] = ""
        asr_module._TOKEN_CACHE["expire_at"] = 0.0

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.text = AsyncMock(return_value='{"Token":{}}')

        mock_session = MagicMock()
        mock_session.get.return_value.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_session.get.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch("src.tools.asr.speech_to_text_tool.aiohttp.ClientSession") as mock_cs:
            mock_cs.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_cs.return_value.__aexit__ = AsyncMock(return_value=False)

            with pytest.raises(RuntimeError, match="GetToken 响应异常"):
                await tool._get_or_refresh_token(
                    access_key_id=mock_asr_config.aliyun_access_key_id,
                    access_key_secret=mock_asr_config.aliyun_access_key_secret,
                )

    @pytest.mark.asyncio
    async def test_token_cache_hit(self, tool, mock_asr_config):
        """Token 缓存命中时不应再次请求"""
        import src.tools.asr.speech_to_text_tool as asr_module
        asr_module._TOKEN_CACHE["token"] = "cached-token"
        asr_module._TOKEN_CACHE["expire_at"] = time.time() + 7200  # 2 小时后过期

        token = await tool._get_or_refresh_token(
            access_key_id=mock_asr_config.aliyun_access_key_id,
            access_key_secret=mock_asr_config.aliyun_access_key_secret,
        )
        assert token == "cached-token"

    @pytest.mark.asyncio
    async def test_asr_recognition_success(self, tool, mock_asr_config):
        """语音识别成功"""
        import src.tools.asr.speech_to_text_tool as asr_module
        asr_module._TOKEN_CACHE["token"] = "test-token"
        asr_module._TOKEN_CACHE["expire_at"] = 9999999999

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.text = AsyncMock(return_value='{"status":20000000,"result":"你好世界","task_id":"abc123"}')

        mock_session = MagicMock()
        mock_session.post.return_value.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_session.post.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch("src.tools.asr.speech_to_text_tool.aiohttp.ClientSession") as mock_cs:
            mock_cs.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_cs.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await tool._call_aliyun_asr(
                audio_bytes=b"\x00" * 100,
                audio_format="mp3",
                sample_rate=16000,
                language="zh_cn",
                access_key_id=mock_asr_config.aliyun_access_key_id,
                access_key_secret=mock_asr_config.aliyun_access_key_secret,
                appkey=mock_asr_config.aliyun_appkey,
                endpoint=mock_asr_config.endpoint,
            )
            assert result["success"] is True
            assert result["text"] == "你好世界"

    @pytest.mark.asyncio
    async def test_asr_recognition_business_error(self, tool, mock_asr_config):
        """阿里云业务错误码（非 20000000）"""
        import src.tools.asr.speech_to_text_tool as asr_module
        asr_module._TOKEN_CACHE["token"] = "test-token"
        asr_module._TOKEN_CACHE["expire_at"] = 9999999999

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.text = AsyncMock(return_value='{"status":40000001,"message":"音频格式错误","task_id":"xyz"}')

        mock_session = MagicMock()
        mock_session.post.return_value.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_session.post.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch("src.tools.asr.speech_to_text_tool.aiohttp.ClientSession") as mock_cs:
            mock_cs.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_cs.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await tool._call_aliyun_asr(
                audio_bytes=b"\x00" * 100,
                audio_format="wav",
                sample_rate=16000,
                language="zh_cn",
                access_key_id=mock_asr_config.aliyun_access_key_id,
                access_key_secret=mock_asr_config.aliyun_access_key_secret,
                appkey=mock_asr_config.aliyun_appkey,
                endpoint=mock_asr_config.endpoint,
            )
            assert result["success"] is False
            assert "40000001" in result["error"]

    @pytest.mark.asyncio
    async def test_asr_http_error(self, tool, mock_asr_config):
        """阿里云 HTTP 错误"""
        import src.tools.asr.speech_to_text_tool as asr_module
        asr_module._TOKEN_CACHE["token"] = "test-token"
        asr_module._TOKEN_CACHE["expire_at"] = 9999999999

        mock_resp = MagicMock()
        mock_resp.status = 500
        mock_resp.text = AsyncMock(return_value="Internal Server Error")

        mock_session = MagicMock()
        mock_session.post.return_value.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_session.post.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch("src.tools.asr.speech_to_text_tool.aiohttp.ClientSession") as mock_cs:
            mock_cs.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_cs.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await tool._call_aliyun_asr(
                audio_bytes=b"\x00" * 100,
                audio_format="mp3",
                sample_rate=16000,
                language="zh_cn",
                access_key_id=mock_asr_config.aliyun_access_key_id,
                access_key_secret=mock_asr_config.aliyun_access_key_secret,
                appkey=mock_asr_config.aliyun_appkey,
                endpoint=mock_asr_config.endpoint,
            )
            assert result["success"] is False
            assert "HTTP 500" in result["error"]

    @pytest.mark.asyncio
    async def test_asr_network_exception(self, tool, mock_asr_config):
        """aiohttp 网络异常（ClientError）"""
        import aiohttp
        import src.tools.asr.speech_to_text_tool as asr_module
        asr_module._TOKEN_CACHE["token"] = "test-token"
        asr_module._TOKEN_CACHE["expire_at"] = 9999999999

        with patch("src.tools.asr.speech_to_text_tool.aiohttp.ClientSession") as mock_cs:
            mock_cs.return_value.__aenter__ = AsyncMock(
                side_effect=aiohttp.ClientError("Connection refused")
            )
            mock_cs.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await tool._call_aliyun_asr(
                audio_bytes=b"\x00" * 100,
                audio_format="mp3",
                sample_rate=16000,
                language="zh_cn",
                access_key_id=mock_asr_config.aliyun_access_key_id,
                access_key_secret=mock_asr_config.aliyun_access_key_secret,
                appkey=mock_asr_config.aliyun_appkey,
                endpoint=mock_asr_config.endpoint,
            )
            assert result["success"] is False
            assert "网络错误" in result["error"]


# ============================================================
# 6. loguru 格式串回归测试（修复后必须通过）
# ============================================================


class TestLoguruFormatRegression:
    """回归测试：loguru 日志消息含 { 或 } 时不应再抛 ValueError"""

    @pytest.mark.asyncio
    async def test_http_error_log_with_braces_in_body(self, tool, mock_asr_config):
        """阿里云返回含 { } 的 body 时，HTTP 错误日志不应让 loguru 二次解析失败"""
        import src.tools.asr.speech_to_text_tool as asr_module
        asr_module._TOKEN_CACHE["token"] = "test-token"
        asr_module._TOKEN_CACHE["expire_at"] = 9999999999

        # body 中含 {invalid} 这种"看起来像 format spec" 的内容
        mock_resp = MagicMock()
        mock_resp.status = 500
        mock_resp.text = AsyncMock(
            return_value='{"error":"invalid request {abc}","code":"bad {req}"}'
        )

        mock_session = MagicMock()
        mock_session.post.return_value.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_session.post.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch("src.tools.asr.speech_to_text_tool.aiohttp.ClientSession") as mock_cs:
            mock_cs.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_cs.return_value.__aexit__ = AsyncMock(return_value=False)

            # 关键：不应抛 ValueError("unmatched '{' in format spec")
            try:
                result = await tool._call_aliyun_asr(
                    audio_bytes=b"\x00" * 100,
                    audio_format="mp3",
                    sample_rate=16000,
                    language="zh_cn",
                    access_key_id=mock_asr_config.aliyun_access_key_id,
                    access_key_secret=mock_asr_config.aliyun_access_key_secret,
                    appkey=mock_asr_config.aliyun_appkey,
                    endpoint=mock_asr_config.endpoint,
                )
            except ValueError as e:
                if "unmatched '{' in format" in str(e) or "KeyError" in str(e):
                    pytest.fail(f"loguru 二次解析失败: {e}")
                raise
            assert result["success"] is False
            assert "HTTP 500" in result["error"]

    @pytest.mark.asyncio
    async def test_business_error_log_with_braces_in_message(self, tool, mock_asr_config):
        """阿里云业务错误 message 含 { } 时不应崩溃"""
        import src.tools.asr.speech_to_text_tool as asr_module
        asr_module._TOKEN_CACHE["token"] = "test-token"
        asr_module._TOKEN_CACHE["expire_at"] = 9999999999

        # message 中含 { "key": "value" } 这种 JSON 片段
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.text = AsyncMock(
            return_value='{"status":40000001,"message":"error: {\\"a\\":1}","task_id":"x"}'
        )

        mock_session = MagicMock()
        mock_session.post.return_value.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_session.post.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch("src.tools.asr.speech_to_text_tool.aiohttp.ClientSession") as mock_cs:
            mock_cs.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_cs.return_value.__aexit__ = AsyncMock(return_value=False)

            try:
                result = await tool._call_aliyun_asr(
                    audio_bytes=b"\x00" * 100,
                    audio_format="mp3",
                    sample_rate=16000,
                    language="zh_cn",
                    access_key_id=mock_asr_config.aliyun_access_key_id,
                    access_key_secret=mock_asr_config.aliyun_access_key_secret,
                    appkey=mock_asr_config.aliyun_appkey,
                    endpoint=mock_asr_config.endpoint,
                )
            except ValueError as e:
                if "unmatched '{' in format" in str(e) or "KeyError" in str(e):
                    pytest.fail(f"loguru 二次解析失败: {e}")
                raise
            assert result["success"] is False
            assert "40000001" in result["error"]

    @pytest.mark.asyncio
    async def test_network_error_log_with_braces_in_exception(self, tool, mock_asr_config):
        """底层 aiohttp 抛出的异常消息含 { } 时不应让 loguru 崩溃"""
        import aiohttp
        import src.tools.asr.speech_to_text_tool as asr_module
        asr_module._TOKEN_CACHE["token"] = "test-token"
        asr_module._TOKEN_CACHE["expire_at"] = 9999999999

        # ClientError 的消息含 {}
        with patch("src.tools.asr.speech_to_text_tool.aiohttp.ClientSession") as mock_cs:
            mock_cs.return_value.__aenter__ = AsyncMock(
                side_effect=aiohttp.ClientError("Connection failed {host=localhost}")
            )
            mock_cs.return_value.__aexit__ = AsyncMock(return_value=False)

            try:
                result = await tool._call_aliyun_asr(
                    audio_bytes=b"\x00" * 100,
                    audio_format="mp3",
                    sample_rate=16000,
                    language="zh_cn",
                    access_key_id=mock_asr_config.aliyun_access_key_id,
                    access_key_secret=mock_asr_config.aliyun_access_key_secret,
                    appkey=mock_asr_config.aliyun_appkey,
                    endpoint=mock_asr_config.endpoint,
                )
            except ValueError as e:
                if "unmatched '{' in format" in str(e) or "KeyError" in str(e):
                    pytest.fail(f"loguru 二次解析失败: {e}")
                raise
            assert result["success"] is False
            assert "网络错误" in result["error"]

    @pytest.mark.asyncio
    async def test_unexpected_exception_log_with_braces(self, tool, mock_asr_config):
        """未知异常消息含 { } 时（模拟上次的 ValueError 场景）不应崩溃"""
        import src.tools.asr.speech_to_text_tool as asr_module
        asr_module._TOKEN_CACHE["token"] = "test-token"
        asr_module._TOKEN_CACHE["expire_at"] = 9999999999

        with patch("src.tools.asr.speech_to_text_tool.aiohttp.ClientSession") as mock_cs:
            mock_cs.return_value.__aenter__ = AsyncMock(
                side_effect=ValueError("unmatched '{' in format spec")
            )
            mock_cs.return_value.__aexit__ = AsyncMock(return_value=False)

            try:
                result = await tool._call_aliyun_asr(
                    audio_bytes=b"\x00" * 100,
                    audio_format="mp3",
                    sample_rate=16000,
                    language="zh_cn",
                    access_key_id=mock_asr_config.aliyun_access_key_id,
                    access_key_secret=mock_asr_config.aliyun_access_key_secret,
                    appkey=mock_asr_config.aliyun_appkey,
                    endpoint=mock_asr_config.endpoint,
                )
            except ValueError as e:
                if "unmatched '{' in format" in str(e):
                    pytest.fail(f"loguru 二次解析失败: {e}")
                raise
            assert result["success"] is False


# ============================================================
# 7. _transcribe_voice_with_asr 入口测试（复现日志错误）
# ============================================================


class TestTranscribeVoiceWithASR:
    """测试 channel_routes._transcribe_voice_with_asr 入口

    日志中的 "unmatched '{' in format spec" 错误可能源于：
    - ASR 工具自身 f-string 错误
    - channel_routes 中 f-string 错误
    - sanitize_error_info 处理含 {} 的错误信息时出错

    这些测试需要 src.saas.api.channel_routes 完整 import 链，依赖 psycopg2；
    在缺少依赖的环境下自动 skip。
    """

    @pytest.fixture
    def transcribe_fn(self):
        """延迟导入 _transcribe_voice_with_asr，跳过无依赖环境"""
        pytest.importorskip("psycopg2")
        from src.saas.api.channel_routes import _transcribe_voice_with_asr
        return _transcribe_voice_with_asr

    @pytest.mark.asyncio
    async def test_transcribe_voice_success(self, transcribe_fn, valid_audio_b64):
        with patch(
            "src.tools.asr.speech_to_text_tool.SpeechToTextTool.execute",
            new=AsyncMock(return_value={"success": True, "text": "你好世界", "message": "成功"}),
        ):
            result = await transcribe_fn(valid_audio_b64, "mp3")
            assert result == "你好世界"

    @pytest.mark.asyncio
    async def test_transcribe_voice_failure_returns_placeholder(self, transcribe_fn, valid_audio_b64):
        with patch(
            "src.tools.asr.speech_to_text_tool.SpeechToTextTool.execute",
            new=AsyncMock(return_value={"success": False, "error": "识别失败"}),
        ):
            result = await transcribe_fn(valid_audio_b64, "mp3")
            assert result == "[语音消息]"

    @pytest.mark.asyncio
    async def test_transcribe_voice_exception_returns_placeholder(self, transcribe_fn, valid_audio_b64):
        """ASR 工具抛异常时，入口应捕获并返回 [语音消息]，不向外传播"""
        # 复现日志中的异常类型
        with patch(
            "src.tools.asr.speech_to_text_tool.SpeechToTextTool.execute",
            new=AsyncMock(side_effect=ValueError("unmatched '{' in format spec")),
        ):
            # 不应抛出，应被 try/except 捕获
            result = await transcribe_fn(valid_audio_b64, "mp3")
            assert result == "[语音消息]"

    @pytest.mark.asyncio
    async def test_transcribe_voice_empty_audio(self, transcribe_fn):
        with patch(
            "src.tools.asr.speech_to_text_tool.SpeechToTextTool.execute",
            new=AsyncMock(return_value={"success": False, "error": "请提供音频文件内容"}),
        ) as mock_exec:
            result = await transcribe_fn("", "mp3")
            assert result == "[语音消息]"
            # 当前实现不做空检查，直接传到底层工具
            assert mock_exec.called

    @pytest.mark.asyncio
    async def test_warning_log_with_braces_in_error(self, transcribe_fn, valid_audio_b64):
        """回归测试：ASR 工具返回的 error 含 { } 时，warning 日志不应让 loguru 崩溃"""
        with patch(
            "src.tools.asr.speech_to_text_tool.SpeechToTextTool.execute",
            new=AsyncMock(return_value={
                "success": False,
                "error": 'HTTP 500: {"code":"bad {req}"}',
            }),
        ):
            try:
                result = await transcribe_fn(valid_audio_b64, "mp3")
            except ValueError as e:
                if "unmatched '{' in format" in str(e) or "KeyError" in str(e):
                    pytest.fail(f"loguru 二次解析失败: {e}")
                raise
            assert result == "[语音消息]"

    @pytest.mark.asyncio
    async def test_error_log_with_braces_in_exception(self, transcribe_fn, valid_audio_b64):
        """回归测试：ASR 工具抛出的异常消息含 { } 时，error 日志不应让 loguru 崩溃

        复现原 bug：'调用 ASR 工具异常: unmatched '{' in format spec'
        """
        with patch(
            "src.tools.asr.speech_to_text_tool.SpeechToTextTool.execute",
            new=AsyncMock(side_effect=ValueError("unmatched '{' in format spec")),
        ):
            try:
                result = await transcribe_fn(valid_audio_b64, "mp3")
            except ValueError as e:
                if "unmatched '{' in format" in str(e):
                    pytest.fail(f"loguru 二次解析失败（原始 bug 复现）: {e}")
                raise
            assert result == "[语音消息]"
