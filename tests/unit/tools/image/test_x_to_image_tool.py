"""XToImageTool 单元测试。

验证 src/tools/image/x_to_image_tool.py 的 XToImageTool：
- 工具定义 / schema（name / display_name / category / description / input_schema）
- 参数校验（validate_parameters / get_missing_parameters）
- execute() 成功 / 失败 / 非法 content_type / 合法 content_type 透传 / 字段透传
- 工具注册（master_agent.tool_registry.get_tool("x_to_image")）

所有 execute() 用例均 mock 掉 src.services.x_to_image.x_to_image_service.convert，
绝不真正启动 browser_pool / Chromium（CI-safe）。
"""
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.tools


# =============================================================================
# 工具定义 / schema（不执行）
# =============================================================================


class TestXToImageToolDefinition:
    """工具定义、显示名、分类、描述、schema 测试。"""

    def test_name_is_x_to_image(self):
        from src.tools.image.x_to_image_tool import XToImageTool
        assert XToImageTool().name == "x_to_image"

    def test_display_name_is_chinese(self):
        from src.tools.image.x_to_image_tool import XToImageTool
        assert XToImageTool().display_name == "内容转图片"

    def test_category_is_image(self):
        from src.tools.image.x_to_image_tool import XToImageTool
        assert XToImageTool().category == "image"

    def test_description_nonempty_mentions_image_or_markdown(self):
        from src.tools.image.x_to_image_tool import XToImageTool
        desc = XToImageTool().description
        assert isinstance(desc, str) and len(desc) > 0
        # 描述应提及 Markdown/HTML 或 图片 之一
        assert any(
            kw in desc for kw in ("Markdown", "HTML", "图片", "长图")
        ), f"description 未提及图片相关关键词: {desc!r}"

    def test_to_tool_definition_structure(self):
        from src.tools.image.x_to_image_tool import XToImageTool
        defn = XToImageTool().to_tool_definition()
        assert set(["name", "description", "input_schema"]).issubset(defn)
        assert defn["name"] == "x_to_image"
        schema = defn["input_schema"]
        assert "required" in schema
        assert "content" in schema["required"]
        props = schema.get("properties", {})
        for key in ("content_type", "is_file_path", "width", "output_name"):
            assert key in props, f"input_schema.properties 缺少 {key}"

    def test_validate_parameters_valid(self):
        from src.tools.image.x_to_image_tool import XToImageTool
        assert XToImageTool().validate_parameters(content="x", content_type="markdown") is True

    def test_get_missing_parameters_without_content(self):
        from src.tools.image.x_to_image_tool import XToImageTool
        missing = XToImageTool().get_missing_parameters()
        assert "content" in missing

    def test_get_missing_parameters_with_content_is_empty(self):
        from src.tools.image.x_to_image_tool import XToImageTool
        assert XToImageTool().get_missing_parameters(content="x") == []


# =============================================================================
# execute() —— mock service.convert，不启动 Chromium
# =============================================================================
# 工具的 execute() 内部做函数级延迟导入：
#     from src.services.x_to_image import x_to_image_service, XToImageInput, InputType
# 因此 convert 在模块级单例对象上。patch 该单例的 convert 方法即可拦截，
# 无需 Chromium。

# patch 目标：模块级单例对象的方法
_CONVERT_PATCH = "src.services.x_to_image.x_to_image_service.convert"


def _ok_result(**overrides):
    """构造一个成功的 XToImageResult（真实 dataclass，保证字段完整）。"""
    from src.services.x_to_image.models import XToImageResult
    base = dict(
        success=True,
        image_path="/tmp/x/y.png",
        width=800,
        height=1200,
        file_size=5000,
        truncated=False,
        renderer="markdown",
    )
    base.update(overrides)
    return XToImageResult(**base)


class TestXToImageToolExecute:
    """execute() 行为测试。"""

    async def test_execute_success_returns_path_and_meta_no_download_url(self):
        from src.tools.image.x_to_image_tool import XToImageTool
        tool = XToImageTool()

        with patch(_CONVERT_PATCH, new_callable=AsyncMock) as mock_convert:
            mock_convert.return_value = _ok_result()
            result = await tool.execute(content="# hi", content_type="markdown")

        assert result["success"] is True
        assert result["image_path"] == "/tmp/x/y.png"
        assert result["image_name"] == "y.png"
        assert result["file_size"] == 5000
        assert result["image_width"] == 800
        assert result["image_height"] == 1200
        assert result["truncated"] is False
        assert result["renderer"] == "markdown"
        # 关键 v1.1 契约：不注册下载、不返回 download_url
        assert "download_url" not in result

    async def test_execute_failure_propagates_error(self):
        from src.tools.image.x_to_image_tool import XToImageTool
        tool = XToImageTool()

        with patch(_CONVERT_PATCH, new_callable=AsyncMock) as mock_convert:
            from src.services.x_to_image.models import XToImageResult
            mock_convert.return_value = XToImageResult(
                success=False, error="渲染失败"
            )
            result = await tool.execute(content="x", content_type="markdown")

        assert result["success"] is False
        assert result["error"] == "渲染失败"
        assert "image_path" not in result

    async def test_execute_invalid_content_type_fails_before_convert(self):
        """非法 content_type 应在调用 convert 之前即失败，且不调用 convert。"""
        from src.tools.image.x_to_image_tool import XToImageTool
        tool = XToImageTool()

        with patch(_CONVERT_PATCH, new_callable=AsyncMock) as mock_convert:
            result = await tool.execute(content="x", content_type="bogus")

        assert result["success"] is False
        assert "error" in result
        # 错误提示「不支持」或列出合法类型
        assert (
            "不支持" in result["error"]
            or "text/markdown/html" in result["error"]
        ), f"error 应提及不支持/合法类型, 实际: {result['error']!r}"
        # convert 不应被调用
        mock_convert.assert_not_called()

    @pytest.mark.parametrize(
        "ctype,expected_enum",
        [
            ("text", "TEXT"),
            ("html", "HTML"),
            ("markdown", "MARKDOWN"),
        ],
    )
    async def test_execute_valid_content_type_passes_correct_inputtype(
        self, ctype, expected_enum
    ):
        """合法 content_type 各变体：传给 convert 的 XToImageInput.content_type 正确。"""
        from src.services.x_to_image.models import InputType
        from src.tools.image.x_to_image_tool import XToImageTool
        tool = XToImageTool()

        with patch(_CONVERT_PATCH, new_callable=AsyncMock) as mock_convert:
            mock_convert.return_value = _ok_result()
            result = await tool.execute(content="x", content_type=ctype)

        assert result["success"] is True
        mock_convert.assert_called_once()
        inp = mock_convert.call_args.args[0]
        assert inp.content_type == InputType[expected_enum]

    async def test_execute_passes_is_file_path_width_output_name(self):
        """is_file_path / width / output_name 应透传到 XToImageInput。"""
        from src.tools.image.x_to_image_tool import XToImageTool
        tool = XToImageTool()

        with patch(_CONVERT_PATCH, new_callable=AsyncMock) as mock_convert:
            mock_convert.return_value = _ok_result()
            await tool.execute(
                content="/tmp/report.html",
                content_type="html",
                is_file_path=True,
                width=500,
                output_name="report",
            )

        inp = mock_convert.call_args.args[0]
        assert inp.is_file_path is True
        assert inp.width == 500
        assert inp.output_name == "report"

    @pytest.mark.parametrize(
        "bad_width,expected",
        [
            (999999, 4000),  # 超大值 → 上限
            (10, 200),       # 过小值 → 下限
        ],
    )
    async def test_execute_clamps_width_to_safe_range(self, bad_width, expected):
        """width 超出合理区间应被钳制，防止 LLM 误传超大值导致 Playwright 大视窗资源滥用。"""
        from src.tools.image.x_to_image_tool import XToImageTool
        tool = XToImageTool()

        with patch(_CONVERT_PATCH, new_callable=AsyncMock) as mock_convert:
            mock_convert.return_value = _ok_result()
            await tool.execute(content="x", content_type="markdown", width=bad_width)

        inp = mock_convert.call_args.args[0]
        assert inp.width == expected

    async def test_execute_convert_exception_returns_failure(self):
        """convert 抛异常时工具应捕获并返回 success=False，不向上传播。"""
        from src.tools.image.x_to_image_tool import XToImageTool
        tool = XToImageTool()

        with patch(_CONVERT_PATCH, new_callable=AsyncMock) as mock_convert:
            mock_convert.side_effect = RuntimeError("boom")
            result = await tool.execute(content="x", content_type="markdown")

        assert result["success"] is False
        assert "error" in result


# =============================================================================
# 注册测试 —— 仅 import + registry 查找，不启动 Chromium
# =============================================================================


class TestXToImageToolRegistration:
    """验证 master_agent 启动时工具已注册。"""

    def test_tool_registered_in_master_agent(self):
        from src.core.agent import master_agent
        from src.tools.image.x_to_image_tool import XToImageTool

        tool = master_agent.tool_registry.get_tool("x_to_image")
        assert tool is not None, "master_agent 未注册 x_to_image 工具"
        assert isinstance(tool, XToImageTool)
