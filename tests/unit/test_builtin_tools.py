from pathlib import Path


def test_document_tools_are_not_registered():
    """文档摘要和翻译工具实现可以保留，但不得暴露给 Agent 调用。"""
    agent_source = (
        Path(__file__).parents[2] / "src" / "core" / "agent.py"
    ).read_text(encoding="utf-8")

    assert "register(DocSummarizeTool())" not in agent_source
    assert "register(DocTranslateTool())" not in agent_source
