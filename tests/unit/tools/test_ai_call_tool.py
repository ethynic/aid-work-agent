"""
AI 外呼工具单元测试
"""
import pytest
from contextlib import contextmanager
from unittest.mock import patch, MagicMock

pytestmark = [pytest.mark.tools, pytest.mark.unit]


@contextmanager
def _capture_logs():
    """捕获 loguru 输出（验证 mock 标记走日志承接，spec §1.4/§1.5）"""
    from loguru import logger

    sink = []
    handler_id = logger.add(sink.append, format="{message}")
    try:
        yield sink
    finally:
        logger.remove(handler_id)


class TestAICallToolDefinition:
    def test_tool_properties(self):
        from src.tools.phone.ai_call_tool import AICallTool, AICallInput
        tool = AICallTool()
        assert tool.name == "ai_call"
        assert tool.display_name == "AI外呼"
        assert tool.category == "phone"
        assert tool.InputModel is AICallInput

    def test_tool_definition_schema(self):
        from src.tools.phone.ai_call_tool import AICallTool
        tool = AICallTool()
        defn = tool.to_tool_definition()
        assert "name" in defn
        assert defn["name"] == "ai_call"
        assert "input_schema" in defn
        schema = defn["input_schema"]
        assert "phone" in schema.get("required", [])
        assert "call_purpose" in schema.get("required", [])

    def test_input_model_validation(self):
        from src.tools.phone.ai_call_tool import AICallInput
        # valid
        inp = AICallInput(phone="13800138000", call_purpose="first_contact")
        assert inp.phone == "13800138000"
        assert inp.max_duration == 180

        # missing required
        with pytest.raises(Exception):
            AICallInput(call_purpose="first_contact")


class TestAICallToolExecute:
    @pytest.mark.asyncio
    async def test_execute_success_mock(self):
        from src.tools.phone.ai_call_tool import AICallTool
        tool = AICallTool()
        result = await tool.execute(
            phone="13800138000",
            call_purpose="first_contact",
            script_hint="了解客户需求",
        )
        assert result["success"] is True
        assert result["status"] == "initiated"
        assert result["call_id"].startswith("call_")
        # spec §1.5：成功返回禁止 echo 输入参数；mock 标记走日志不进返回值
        assert "phone" not in result
        assert "mock" not in result

    @pytest.mark.asyncio
    async def test_mock_marker_logged_not_returned(self):
        """mock 标记改走日志（spec §1.4/§1.5）：返回值只带新信息，
        Mock 模拟状态在 loguru 日志中承接，不进 tool_result"""
        from src.tools.phone.ai_call_tool import AICallTool

        tool = AICallTool()
        with _capture_logs() as logs:
            result = await tool.execute(
                phone="13800138000",
                lead_id="lead_abc123",
                call_purpose="followup",
            )

        assert result["success"] is True
        # 返回值键集 == 新信息（call_id/status）+ 状态说明 message，无输入回显、无 mock 布尔
        assert set(result.keys()) == {"success", "call_id", "status", "message"}
        joined = "\n".join(logs)
        # mock 标记由日志承接（_execute_call 的 Mock 模拟日志）
        assert "Mock 模拟" in joined
        assert result["call_id"] in joined

    @pytest.mark.asyncio
    async def test_execute_missing_phone(self):
        from src.tools.phone.ai_call_tool import AICallTool
        tool = AICallTool()
        result = await tool.execute(call_purpose="first_contact")
        assert result["success"] is False
        assert "号码" in result["error"]

    @pytest.mark.asyncio
    async def test_execute_invalid_purpose(self):
        from src.tools.phone.ai_call_tool import AICallTool
        tool = AICallTool()
        result = await tool.execute(
            phone="13800138000",
            call_purpose="invalid_purpose",
        )
        assert result["success"] is False
        assert "无效" in result["error"]

    @pytest.mark.asyncio
    async def test_execute_with_lead_id(self):
        from src.tools.phone.ai_call_tool import AICallTool
        tool = AICallTool()
        result = await tool.execute(
            phone="13800138000",
            lead_id="lead_abc123",
            call_purpose="followup",
        )
        assert result["success"] is True
        # spec §1.5：lead_id 是输入参数，不再 echo 进返回值
        assert "lead_id" not in result

    @pytest.mark.asyncio
    async def test_execute_all_valid_purposes(self):
        from src.tools.phone.ai_call_tool import AICallTool
        tool = AICallTool()
        for purpose in ["first_contact", "followup", "appointment_reminder", "satisfaction_survey"]:
            result = await tool.execute(phone="13800138000", call_purpose=purpose)
            assert result["success"] is True, f"Failed for purpose: {purpose}"

    @pytest.mark.asyncio
    async def test_execute_with_max_duration(self):
        from src.tools.phone.ai_call_tool import AICallTool
        tool = AICallTool()
        result = await tool.execute(
            phone="13800138000",
            call_purpose="first_contact",
            max_duration=300,
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_mock_execute_call_returns_dict(self):
        """Test that the mock _execute_call returns proper dict structure"""
        from src.tools.phone.ai_call_tool import AICallTool
        tool = AICallTool()
        result = tool._execute_call(
            phone="02112345678",
            call_id="call_test123",
            call_purpose="first_contact",
            script_hint="test hint",
            max_duration=120,
            lead_id="lead_test",
        )
        assert isinstance(result, dict)
        assert result["success"] is True
        assert result["call_id"] == "call_test123"
        # spec §1.5：不 echo 输入参数；mock 标记走日志不进返回值
        assert "phone" not in result
        assert "mock" not in result
