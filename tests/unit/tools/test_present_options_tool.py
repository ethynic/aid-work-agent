# -*- coding: utf-8 -*-
"""PresentOptionsTool（选项卡片工具）单元测试"""
import pytest

from src.tools.agent.present_options_tool import PresentOptionsTool

pytestmark = pytest.mark.tools


class TestPresentOptionsTool:
    def test_tool_definition(self):
        tool = PresentOptionsTool()
        assert tool.name == "present_options"
        assert tool.catalog is False
        defn = tool.to_tool_definition()
        params = defn.get("input_schema", {})
        assert defn.get("name") == "present_options"
        assert "options" in params.get("properties", {})

    @pytest.mark.asyncio
    async def test_execute_success_returns_data_options(self):
        tool = PresentOptionsTool()
        result = await tool.execute(
            question="空间清单是否按提案继续？",
            options=[
                {"key": "1", "label": "按提案继续"},
                {"key": "2", "label": "我要调整", "description": "请打字说明重命名/合并/增删"},
            ],
        )
        assert result["success"] is True
        assert result["question"] == "空间清单是否按提案继续？"
        opts = result["data"]["options"]
        assert len(opts) == 2
        assert opts[0] == {"key": "1", "label": "按提案继续"}
        assert opts[1]["description"] == "请打字说明重命名/合并/增删"

    @pytest.mark.asyncio
    async def test_execute_too_few_options_fails(self):
        tool = PresentOptionsTool()
        result = await tool.execute(question="q", options=[{"key": "1", "label": "唯一项"}])
        assert result["success"] is False
        assert "2~6" in result["error"]

    @pytest.mark.asyncio
    async def test_execute_too_many_options_fails(self):
        tool = PresentOptionsTool()
        result = await tool.execute(
            question="q",
            options=[{"key": str(i), "label": f"选项{i}"} for i in range(1, 8)],
        )
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_execute_skips_items_with_empty_key_or_label(self):
        tool = PresentOptionsTool()
        result = await tool.execute(
            question="q",
            options=[
                {"key": "", "label": "无 key"},
                {"key": "1", "label": ""},
                {"key": "2", "label": "有效项 A"},
                {"key": "3", "label": "有效项 B"},
            ],
        )
        # 空 key/label 项被清洗掉后剩 2 个有效项，仍成功
        assert result["success"] is True
        assert [o["key"] for o in result["data"]["options"]] == ["2", "3"]
