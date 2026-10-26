"""Research evidence survives projection and remains inside its trusted session."""

import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.services.web_search import evidence
from src.tools.context import ToolExecutionContext, tool_execution_scope
from src.tools.executor import ToolExecutor
from src.tools.file.read_tool import ReadTool
from src.tools.registry import ToolRegistry
from src.tools.search import search_tool

pytestmark = [pytest.mark.tools, pytest.mark.search]


@pytest.fixture
def research_context(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_RUNNER_STORAGE_ROOT", str(tmp_path / "storage"))
    return ToolExecutionContext(tenant_id="tenant_research", user_id="alice", session_id="research-1")


def publish(context, items):
    with tool_execution_scope(context):
        return evidence.publish_research_result({"success": True, "provider": "test", "count": len(items), "results": items})


def item(index=0, body="Company manufactures windows."):
    return {"url": f"https://example.test/source/{index}", "title": f"Source {index}",
            "content": body, "content_kind": "full_text", "provider": "test"}


def test_projection_retains_all_fifty_source_identities_not_only_head_and_tail(research_context):
    original = [item(i, "Long factual evidence " * 2000) for i in range(50)]
    result = publish(research_context, original)
    assert len(result["results"]) == 50
    assert [s["url"] for s in result["results"]] == [s["url"] for s in original]
    assert len({s["source_id"] for s in result["results"]}) == 50
    assert result["_no_truncate"] is True
    for source in result["results"]:
        assert source["content_truncated"] and len(source["content"]) <= 100
        assert source["body_sha256"] and Path(source["body_file_path"]).is_file()
    assert len(json.dumps(result)) < sum(len(s["content"]) for s in original) // 10


@pytest.mark.asyncio
async def test_search_executor_projects_then_existing_read_pages_recover_middle_evidence(research_context, monkeypatch):
    body = "\n".join(f"Evidence line {i:04}: verified factory statement." for i in range(120))
    provider = SimpleNamespace(search=AsyncMock(return_value={"success": True, "count": 1, "results": [item(body=body)]}))
    monkeypatch.setattr(search_tool, "create_provider", lambda *args: provider)
    registry = ToolRegistry()
    registry.register(search_tool.WebSearchTool())
    registry.register(ReadTool())
    executor = ToolExecutor(registry)
    result = await executor.execute("web_search", {"keyword": "factory"}, context=research_context)
    assert result["success"] and result["_no_truncate"]
    source = result["results"][0]
    assert "Evidence line 0070" not in source["content"]
    page = await executor.execute("read", {"file_path": source["body_file_path"], "offset": 71, "limit": 2}, context=research_context)
    assert "Evidence line 0070" in page["content"] and "Evidence line 0071" in page["content"]
    assert "Evidence line 0069" not in page["content"]


@pytest.mark.parametrize("identity", [{"tenant_id": "tenant_other"}, {"user_id": "bob"}, {"session_id": "research-2"}])
@pytest.mark.asyncio
async def test_other_tenant_user_or_session_cannot_read_saved_evidence(research_context, identity):
    source = publish(research_context, [item(body="PRIVATE_SYNTHETIC_EVIDENCE")])["results"][0]
    other = research_context.derive(**identity)
    with pytest.raises(ValueError, match="source_scope_invalid"):
        evidence.source_body(source, other)
    with tool_execution_scope(other):
        result = await ReadTool().execute(file_path=source["body_file_path"])
    assert isinstance(result, str) and "research_scope_invalid" in result
    assert "PRIVATE_SYNTHETIC_EVIDENCE" not in result


def test_body_hash_detects_changes_after_original_search(research_context):
    source = publish(research_context, [item()])["results"][0]
    Path(source["body_file_path"]).write_text("Changed source", encoding="utf-8")
    with pytest.raises(ValueError, match="source_body_changed"):
        evidence.source_body(source, research_context)


def test_long_single_line_can_be_read_without_losing_characters(research_context):
    body = "BEGIN" + "0123456789" * 1000 + "END"
    source = publish(research_context, [item(body=body)])["results"][0]
    restored = evidence.source_body(source, research_context)
    assert restored.replace("\n", "") == body
    assert max(map(len, restored.splitlines())) <= 300


def test_only_successful_original_search_and_fetch_facts_are_trusted(research_context):
    result = publish(research_context, [item()])
    def fact(name, success=True):
        return SimpleNamespace(call=SimpleNamespace(name=name), success=success, result=result)
    state = SimpleNamespace(tools={"original": fact("web_search"), "copy": fact("write"), "failed": fact("web_fetch", False)})
    sources = evidence.sources_from_state(state)
    assert list(sources) == [result["results"][0]["source_id"]]


def test_unscoped_legacy_call_does_not_claim_to_create_session_evidence():
    payload = {"success": True, "results": [item()]}
    with tool_execution_scope(None):
        assert evidence.publish_research_result(payload) is payload
        with pytest.raises(ValueError, match="research_session_required"):
            evidence.scope_directory()




@pytest.mark.asyncio
async def test_current_sessions_research_read_supports_an_external_storage_mount(research_context, tmp_path, monkeypatch):
    source = publish(research_context, [item(body="MOUNTED_EVIDENCE")])["results"][0]
    from src.tools.file import read_tool
    monkeypatch.setattr(read_tool.tempfile, "gettempdir", lambda: str(tmp_path / "unrelated-temp-root"))
    with tool_execution_scope(research_context):
        result = await ReadTool().execute(file_path=source["body_file_path"])
    assert isinstance(result, dict) and "MOUNTED_EVIDENCE" in result["content"]




def decoded_page(page):
    lines = [line.split("\t", 1) for line in page["content"].splitlines()]
    assert page["read_lines"] == len(lines)
    return [(int(number.strip()), text) for number, text in lines]


@pytest.mark.parametrize("requested_limit", [None, 100000])
@pytest.mark.asyncio
async def test_long_research_body_default_and_large_limit_can_page_without_omissions(research_context, requested_limit):
    original = [f"Factory evidence {i:04}: " + "verified " * 25 for i in range(400)]
    source = publish(research_context, [item(body="\n".join(original))])["results"][0]
    tool = ReadTool()
    seen = []
    offset = 1
    with tool_execution_scope(research_context):
        for _ in range(len(original)):
            kwargs = {"file_path": source["body_file_path"], "offset": offset}
            if requested_limit is not None:
                kwargs["limit"] = requested_limit
            page = await tool.execute(**kwargs)
            assert isinstance(page, dict) and page["_no_truncate"] is True
            assert len(page["content"]) <= 6000
            assert page["total_lines"] == len(original)
            entries = decoded_page(page)
            assert entries and entries[0][0] == offset
            seen.extend(entries)
            if "next_hint" not in page:
                break
            next_offset = int(re.search(r"offset=(\d+)", page["next_hint"]).group(1))
            assert next_offset == offset + page["read_lines"]
            offset = next_offset
        else:
            pytest.fail("research pagination failed to make progress")
    assert seen == list(enumerate(original, 1))


@pytest.mark.parametrize("with_end", [False, True])
@pytest.mark.asyncio
async def test_research_section_paging_uses_actual_start_line_and_preserves_boundaries(research_context, with_end):
    prefix = [f"Introduction {i}" for i in range(35)]
    section = ["BEGIN_FACTS"] + [f"Manufacturing fact {i:03}: " + "confirmed " * 22 for i in range(110)] + ["END_FACTS"]
    suffix = ["OUTSIDE_SECTION", "Trailing unrelated text"]
    original = prefix + section + suffix
    source = publish(research_context, [item(body="\n".join(original))])["results"][0]
    tool = ReadTool()
    expected_end = len(prefix) + len(section) if with_end else len(original)
    with tool_execution_scope(research_context):
        page = await tool.execute(file_path=source["body_file_path"], section_start="BEGIN_FACTS",
                                  section_end="END_FACTS" if with_end else None)
        assert page["section_start_line"] == len(prefix) + 1
        assert page["_no_truncate"] and len(page["content"]) <= 6000
        seen = decoded_page(page)
        while seen[-1][0] < expected_end:
            next_offset = int(re.search(r"offset=(\d+)", page["next_hint"]).group(1))
            assert next_offset == seen[-1][0] + 1
            page = await tool.execute(file_path=source["body_file_path"], offset=next_offset,
                                      limit=expected_end - next_offset + 1)
            assert page["_no_truncate"] and len(page["content"]) <= 6000
            entries = decoded_page(page)
            assert entries and entries[0][0] == next_offset
            seen.extend(entries)
    assert seen == list(enumerate(original[len(prefix):expected_end], len(prefix) + 1))


@pytest.mark.asyncio
async def test_normal_read_keeps_existing_behavior_for_large_nonresearch_files(tmp_path):
    original = [f"Ordinary content {i:03}: " + "plain " * 25 for i in range(100)]
    path = tmp_path / "ordinary.txt"
    path.write_text("\n".join(original), encoding="utf-8")
    result = await ReadTool().execute(file_path=str(path))
    assert len(result["content"]) > 6000
    assert result["read_lines"] == result["total_lines"] == len(original)
    assert "_no_truncate" not in result and "next_hint" not in result
    assert decoded_page(result) == list(enumerate(original, 1))


@pytest.mark.asyncio
async def test_abnormally_large_research_line_fails_instead_of_returning_a_nonadvancing_page(research_context):
    source = publish(research_context, [item(body="Original body")])["results"][0]
    Path(source["body_file_path"]).write_bytes(("x" * 7000).encode("utf-8"))
    with tool_execution_scope(research_context):
        result = await ReadTool().execute(file_path=source["body_file_path"])
    assert isinstance(result, str) and "research_body_line_too_long" in result


@pytest.mark.parametrize("answer,include_answer", [
    ("Provider summary", True),
    ("Summary line: " + "abcdef" * 1800 + " END_SUMMARY", True),
    ("Provider summary", False),
])
@pytest.mark.asyncio
async def test_tavily_session_keeps_requested_summary_without_promoting_it_to_source(
    research_context, monkeypatch, answer, include_answer,
):
    import hashlib
    import sys
    from unittest.mock import MagicMock
    from src.config.settings import SearchToolConfig

    client = MagicMock()
    client.search.return_value = {"results": [item()], "answer": answer, "response_time": 0.42}
    monkeypatch.setitem(sys.modules, "tavily", SimpleNamespace(TavilyClient=MagicMock(return_value=client)))
    monkeypatch.setattr(search_tool, "settings", SimpleNamespace(tools=SimpleNamespace(search=SearchToolConfig(
        provider="tavily", tavily_api_key="synthetic-search-key", include_answer=include_answer,
    ))))
    registry = ToolRegistry()
    registry.register(search_tool.WebSearchTool())
    registry.register(ReadTool())
    executor = ToolExecutor(registry)
    result = await executor.execute("web_search", {"keyword": "factory"}, context=research_context)
    assert result["success"] and result["response_time"] == 0.42
    assert len(result["results"]) == 1
    assert result["results"][0]["content"] != answer
    assert client.search.call_args.kwargs["include_answer"] is include_answer
    if not include_answer:
        assert "answer" not in result and "answer_file_path" not in result
        return
    assert result["answer"] == answer[:700]
    assert result["answer_kind"] == "provider_generated_summary"
    assert result["answer_truncated"] is (len(answer) > 700)
    assert result["answer_chars"] == len(answer)
    if len(answer) <= 700:
        assert "answer_file_path" not in result
        return
    raw = Path(result["answer_file_path"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == result["answer_sha256"]
    recovered = []
    offset = 1
    for _ in range(100):
        page = await executor.execute("read", {"file_path": result["answer_file_path"], "offset": offset}, context=research_context)
        entries = decoded_page(page)
        assert entries and entries[0][0] == offset
        recovered.extend(text for _, text in entries)
        if "next_hint" not in page:
            break
        offset += page["read_lines"]
    else:
        pytest.fail("summary pagination failed to terminate")
    assert "".join(recovered) == answer
    other = research_context.derive(session_id="other-summary-session")
    blocked = await executor.execute("read", {"file_path": result["answer_file_path"]}, context=other)
    assert "END_SUMMARY" not in str(blocked) and "research_scope_invalid" in str(blocked)


def test_projection_preserves_existing_provider_engine_metadata(research_context):
    with tool_execution_scope(research_context):
        result = evidence.publish_research_result({
            "success": True, "results": [item()], "search_engine": "search_pro", "response_time": 0.42,
        })
    assert result["search_engine"] == "search_pro"
    assert result["response_time"] == 0.42
