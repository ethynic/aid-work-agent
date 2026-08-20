"""
video-agent 多模态支持单元测试

覆盖：
1. DelegateToSubagentInput schema 含 image_paths 字段
2. Agent._build_multimodal_user_content 构造 OpenAI 多模态 content
3. delegate_tool.execute 透传 image_paths 给 executor.delegate
"""

import base64
import pytest
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from src.tools.context import ToolExecutionContext, tool_execution_scope

pytestmark = [pytest.mark.tools, pytest.mark.unit]


# 最小 PNG 文件头（1x1 透明 PNG）
_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
    b"\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


class _AllowAuthorizer:
    def authorize(self, context, name, config):
        return True


class TestDelegateToSubagentInput:
    """DelegateToSubagentInput schema 含 image_paths 字段，LLM 可显式感知。"""

    def test_schema_contains_image_paths(self):
        from src.tools.agent.delegate_tool import DelegateToSubagentInput
        fields = DelegateToSubagentInput.model_fields
        assert "image_paths" in fields
        assert "subagent_name" in fields
        assert "task_description" in fields

    def test_image_paths_optional_default_none(self):
        """image_paths 默认 None（不传时老调用路径兼容）"""
        from src.tools.agent.delegate_tool import DelegateToSubagentInput
        inp = DelegateToSubagentInput(subagent_name="video-agent", task_description="test")
        assert inp.image_paths is None

    def test_image_paths_accepts_list(self):
        from src.tools.agent.delegate_tool import DelegateToSubagentInput
        inp = DelegateToSubagentInput(
            subagent_name="video-agent",
            task_description="test",
            image_paths=["/tmp/a.png", "/tmp/b.jpg"],
        )
        assert inp.image_paths == ["/tmp/a.png", "/tmp/b.jpg"]

    def test_tool_definition_exposes_image_paths_in_schema(self):
        """to_tool_definition() 输出的 input_schema 必须含 image_paths 字段，LLM 才能看到"""
        from src.tools.agent.delegate_tool import DelegateToSubagentTool
        tool = DelegateToSubagentTool(
            subagent_registry=MagicMock(),
            subagent_executor=MagicMock(),
        )
        defn = tool.to_tool_definition()
        schema = defn["input_schema"]
        assert "image_paths" in schema.get("properties", {})


class TestBuildMultimodalUserContent:
    """Agent._build_multimodal_user_content 构造 OpenAI 多模态 content。"""

    def _make_agent(self):
        """构造一个最小 Agent 实例，跳过 __init__ 重组件"""
        from src.core.agent import Agent
        agent = Agent.__new__(Agent)
        return agent

    def test_none_image_paths_returns_none(self):
        """image_paths=None 返回 None，调用方按纯文本处理"""
        agent = self._make_agent()
        result = agent._build_multimodal_user_content("hello", None)
        assert result is None

    def test_empty_image_paths_returns_none(self):
        """image_paths=[] 返回 None"""
        agent = self._make_agent()
        result = agent._build_multimodal_user_content("hello", [])
        assert result is None

    def test_single_png_image_builds_multimodal_content(self, tmp_path):
        """单张 PNG 构造 text + image_url 两段 content"""
        img_path = tmp_path / "product.png"
        img_path.write_bytes(_PNG_BYTES)

        agent = self._make_agent()
        result = agent._build_multimodal_user_content("描述这张图", [str(img_path)])

        assert result is not None
        assert len(result) == 2
        assert result[0] == {"type": "text", "text": "描述这张图"}
        assert result[1]["type"] == "image_url"
        url = result[1]["image_url"]["url"]
        assert url.startswith("data:image/png;base64,")

        # 验证 base64 内容可解码回原 bytes
        b64_data = url.split(",", 1)[1]
        assert base64.b64decode(b64_data) == _PNG_BYTES

    def test_jpeg_extension_uses_jpeg_mime(self, tmp_path):
        """jpg 扩展名映射到 image/jpeg MIME"""
        img_path = tmp_path / "photo.jpg"
        img_path.write_bytes(_PNG_BYTES)  # 内容不重要，只看扩展名

        agent = self._make_agent()
        result = agent._build_multimodal_user_content("test", [str(img_path)])

        assert result is not None
        url = result[1]["image_url"]["url"]
        assert url.startswith("data:image/jpeg;base64,")

    def test_unsupported_extension_skipped(self, tmp_path):
        """不支持的扩展名（如 .bmp）跳过，返回 None（无有效图）"""
        img_path = tmp_path / "image.bmp"
        img_path.write_bytes(_PNG_BYTES)

        agent = self._make_agent()
        result = agent._build_multimodal_user_content("test", [str(img_path)])
        assert result is None

    def test_max_three_images(self, tmp_path):
        """超过 3 张图片时只取前 3 张"""
        paths = []
        for i in range(5):
            p = tmp_path / f"img{i}.png"
            p.write_bytes(_PNG_BYTES)
            paths.append(str(p))

        agent = self._make_agent()
        result = agent._build_multimodal_user_content("test", paths)

        # 1 text + 3 images = 4 parts
        assert len(result) == 4
        assert result[0]["type"] == "text"
        assert sum(1 for p in result if p["type"] == "image_url") == 3

    def test_oversized_image_skipped(self, tmp_path):
        """单张超过 5MB 的图片跳过"""
        img_path = tmp_path / "big.png"
        # 写 6MB 假数据（开头是 PNG 头，但内容是填充）
        big_bytes = _PNG_BYTES + b"\x00" * (6 * 1024 * 1024)
        img_path.write_bytes(big_bytes)

        agent = self._make_agent()
        result = agent._build_multimodal_user_content("test", [str(img_path)])
        assert result is None  # 唯一一张图被跳过，返回 None

    def test_nonexistent_file_skipped(self, tmp_path):
        """文件不存在时跳过，不抛异常"""
        agent = self._make_agent()
        result = agent._build_multimodal_user_content("test", ["/nonexistent/path.png"])
        assert result is None

    def test_mixed_valid_and_invalid_only_attaches_valid(self, tmp_path):
        """混合路径中只附加有效图"""
        valid = tmp_path / "ok.png"
        valid.write_bytes(_PNG_BYTES)

        agent = self._make_agent()
        result = agent._build_multimodal_user_content(
            "test",
            ["/nonexistent.png", str(valid), "invalid.bmp"],
        )
        # 只有 valid 被附加
        assert len(result) == 2
        assert result[1]["image_url"]["url"].startswith("data:image/png;base64,")


class TestDelegateToolExecutePassesImagePaths:
    """delegate_tool.execute() 把 image_paths 透传给 executor.delegate。"""

    @pytest.mark.asyncio
    async def test_execute_passes_image_paths_to_delegate(self):
        from src.tools.agent.delegate_tool import DelegateToSubagentTool

        registry = MagicMock()
        executor = MagicMock()
        executor.delegate = AsyncMock(return_value=MagicMock(success=True, execution_id="e1", subagent_name="video-agent"))
        executor.wait_for_result = AsyncMock(return_value=MagicMock(
            is_clarifying=lambda: False,
            status="completed",
            result={"ok": True},
            summary="done",
            error=None,
            token_usage={},
        ))

        registry.get = MagicMock(return_value=MagicMock(name="video-agent"))

        tool = DelegateToSubagentTool(
            subagent_registry=registry,
            subagent_executor=executor,
            authorizer=_AllowAuthorizer(),
        )

        with tool_execution_scope(ToolExecutionContext(
            tenant_id="tenant", user_id="user", session_id="sid"
        )):
            await tool.execute(
                subagent_name="video-agent",
                task_description="生成产品视频",
                image_paths=["/tmp/a.png", "/tmp/b.jpg"],
                session_id="untrusted-session",
            )

        # 验证 delegate 被调用时含 image_paths
        delegate_call = executor.delegate.call_args
        assert delegate_call.kwargs.get("image_paths") == ["/tmp/a.png", "/tmp/b.jpg"]
        assert delegate_call.kwargs.get("session_id") == "sid"
        assert delegate_call.kwargs.get("user_id") == "user"

    @pytest.mark.asyncio
    async def test_execute_without_image_paths_passes_none(self):
        """老调用路径（不传 image_paths）应传 None，保持兼容"""
        from src.tools.agent.delegate_tool import DelegateToSubagentTool

        registry = MagicMock()
        executor = MagicMock()
        executor.delegate = AsyncMock(return_value=MagicMock(success=True, execution_id="e1", subagent_name="travel-consultant"))
        executor.wait_for_result = AsyncMock(return_value=MagicMock(
            is_clarifying=lambda: False,
            status="completed",
            result={"ok": True},
            summary="done",
            error=None,
            token_usage={},
        ))

        registry.get = MagicMock(return_value=MagicMock(name="travel-consultant"))

        tool = DelegateToSubagentTool(
            subagent_registry=registry,
            subagent_executor=executor,
            authorizer=_AllowAuthorizer(),
        )

        with tool_execution_scope(ToolExecutionContext(session_id="sid")):
            await tool.execute(
                subagent_name="travel-consultant",
                task_description="规划行程",
                session_id="sid",
            )

        delegate_call = executor.delegate.call_args
        assert delegate_call.kwargs.get("image_paths") is None

    @pytest.mark.asyncio
    async def test_execute_filters_empty_image_paths(self):
        """image_paths 含空字符串时被过滤为 None"""
        from src.tools.agent.delegate_tool import DelegateToSubagentTool

        registry = MagicMock()
        executor = MagicMock()
        executor.delegate = AsyncMock(return_value=MagicMock(success=True, execution_id="e1", subagent_name="video-agent"))
        executor.wait_for_result = AsyncMock(return_value=MagicMock(
            is_clarifying=lambda: False,
            status="completed",
            result={"ok": True},
            summary="done",
            error=None,
            token_usage={},
        ))

        registry.get = MagicMock(return_value=MagicMock(name="video-agent"))

        tool = DelegateToSubagentTool(
            subagent_registry=registry,
            subagent_executor=executor,
            authorizer=_AllowAuthorizer(),
        )

        with tool_execution_scope(ToolExecutionContext(session_id="sid")):
            await tool.execute(
                subagent_name="video-agent",
                task_description="test",
                image_paths=["", None],
                session_id="sid",
            )

        # 全空被过滤为 None
        delegate_call = executor.delegate.call_args
        assert delegate_call.kwargs.get("image_paths") is None
