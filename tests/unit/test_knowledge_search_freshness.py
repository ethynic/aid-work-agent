"""A second search may reuse only knowledge proven present and unchanged."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.core.agent_engine.contracts import AgentMode, ExecutionState, Identity, ToolCall
from src.services.agent_runner.runtime.tools import ToolDispatcher
from src.tools.context import ToolExecutionContext, tool_execution_scope
from src.tools.knowledge.knowledge_base_tool import KnowledgeBaseTool
from src.tools.knowledge.search_context import chunk_fingerprint, context_inventory, fingerprint


def _db(payload):
    cursor = MagicMock()
    cursor.fetchall.return_value = [{
        "id": 7, "title": "报价", "tenant_id": None, "source_type": "pricing",
        "metadata": json.dumps({"raw_payload": payload}), "summary": "当前报价",
    }]
    conn = MagicMock()
    conn.cursor.return_value = cursor
    cm = MagicMock()
    cm.__enter__.return_value = conn
    return cm


def _row(text="原价", score=0.9):
    return {"doc_id": 7, "chunk_index": 0, "text": text, "score": score, "metadata": {"kind": "price"}}


def _messages(result):
    return [
        {"role": "assistant", "tool_calls": [{"id": "k1", "function": {"name": "knowledge_base_search"}}]},
        {"role": "tool", "tool_call_id": "k1", "content": json.dumps(result, ensure_ascii=False)},
    ]


async def test_repeat_search_runs_again_and_returns_changed_raw_payload():
    """The model must see a price change even when the same chunk text ranks again."""
    tool = KnowledgeBaseTool()
    tool._retriever = MagicMock(retrieve=AsyncMock(side_effect=[[_row()], [_row(score=0.3)]]))
    with patch("src.tools.knowledge.knowledge_base_tool.get_db_connection", side_effect=[_db({"price": 100}), _db({"price": 120})]):
        first = await tool.execute(query="价格")
        with tool_execution_scope(ToolExecutionContext(knowledge_search_inventory=context_inventory(_messages(first)))):
            second = await tool.execute(query="最新价格")

    assert tool._retriever.retrieve.await_count == 2
    assert second["matched_count"] == 1 and second["count"] == 0
    assert len(second["already_in_context_chunks"]) == 1
    assert second["documents"]["7"]["doc_metadata"]["raw_payload"]["price"] == 120
    assert second["already_in_context_documents"] == []


async def test_new_chunk_keeps_document_reference_to_complete_history():
    """A new hit remains usable when its unchanged document data is already visible."""
    tool = KnowledgeBaseTool()
    tool._retriever = MagicMock(retrieve=AsyncMock(side_effect=[[_row()], [_row("新价说明")]]))
    with patch("src.tools.knowledge.knowledge_base_tool.get_db_connection", return_value=_db({"price": 100})):
        first = await tool.execute(query="价格")
        with tool_execution_scope(ToolExecutionContext(knowledge_search_inventory=context_inventory(_messages(first)))):
            second = await tool.execute(query="说明")
    assert second["count"] == 1
    assert second["results"][0]["text"] == "新价说明"
    assert second["documents"] == {}
    assert second["already_in_context_documents"][0]["doc_id"] == "7"


async def test_old_or_missing_body_never_proves_freshness():
    """Legacy receipts and marker-only receipts cannot suppress current text."""
    tool = KnowledgeBaseTool()
    tool._retriever = MagicMock(retrieve=AsyncMock(return_value=[_row()]))
    legacy = {"success": True, "results": [_row()], "documents": {}}
    with patch("src.tools.knowledge.knowledge_base_tool.get_db_connection", return_value=_db({"price": 100})):
        fresh = await tool.execute(query="价格")
        marker_only = {**fresh, "results": [], "documents": {}}
        messages = _messages(legacy) + [
            {"role": "assistant", "tool_calls": [{"id": "k2", "function": {"name": "knowledge_base_search"}}]},
            {"role": "tool", "tool_call_id": "k2", "content": json.dumps(marker_only)},
        ]
        with tool_execution_scope(ToolExecutionContext(knowledge_search_inventory=context_inventory(messages))):
            current = await tool.execute(query="价格")
    assert current["matched_count"] == current["count"] == 1
    assert current["documents"]["7"]["doc_metadata"]["raw_payload"]["price"] == 100


async def test_authorization_read_failure_stops_search():
    """A failed permission read cannot silently become unrestricted retrieval."""
    tool = KnowledgeBaseTool()
    tool._retriever = MagicMock(retrieve=AsyncMock())
    with tool_execution_scope(ToolExecutionContext(tenant_id="tenant", subagent_id="sales")), \
            patch("src.db.database.get_db_connection", side_effect=RuntimeError("db unavailable")):
        result = await tool.execute(query="价格")
    assert result["success"] is False
    assert "授权" in result["error"]
    tool._retriever.retrieve.assert_not_awaited()


async def test_shared_authorization_read_failure_stops_search():
    """A failed shared grant read cannot look like a normal empty grant set."""
    tool = KnowledgeBaseTool()
    tool._retriever = MagicMock(retrieve=AsyncMock())
    auth_db = _db({})
    auth_db.__enter__.return_value.cursor.return_value.fetchone.return_value = {"sources": [], "share_owners": []}
    with tool_execution_scope(ToolExecutionContext(tenant_id="current", subagent_id="sales")), \
            patch("src.db.database.get_db_connection", side_effect=[auth_db, RuntimeError("db unavailable")]):
        result = await tool.execute(query="价格")
    assert result["success"] is False
    tool._retriever.retrieve.assert_not_awaited()


def test_malformed_history_does_not_block_search_inventory():
    """A damaged old call must lose dedup evidence without breaking the turn."""
    messages = [{"role": "assistant", "tool_calls": [{"id": "bad", "function": "corrupt"}]},
                {"role": "tool", "tool_call_id": "bad", "content": "{broken"}]
    assert context_inventory(messages) == (frozenset(), frozenset(), frozenset())


async def test_runner_rebuilds_inventory_before_each_call_including_restored_messages():
    """A second call in the same batch sees the first result, also after checkpoint restore."""
    chunk = {"doc_id": 7, "chunk_index": 1, "text": "已确认价格", "metadata": {}, "score": 0.9}
    chunk["fingerprint"] = chunk_fingerprint(chunk)
    document = {"title": "报价", "doc_metadata": {"raw_payload": {"price": 100}}}
    result = {"success": True, "_knowledge_result_format": 1, "results": [chunk],
              "documents": {"7": document}, "document_fingerprints": {"7": fingerprint(document)}}
    first_call = ToolCall("k1", "knowledge_base_search", {"query": "报价"})
    second_call = ToolCall("k2", "knowledge_base_search", {"query": "最新报价"})
    state = ExecutionState(
        Identity(None, None, "session-1"), "run-1", AgentMode.MASTER, "",
        [{"role": "assistant", "tool_calls": [first_call.message_call(), second_call.message_call()]}],
    )
    dispatcher = ToolDispatcher(
        catalog=SimpleNamespace(_get_tools=lambda: [{"name": "knowledge_base_search"}]),
        registry=SimpleNamespace(get_tool=lambda _name: SimpleNamespace(execution_target=None)),
        executor=None, control_tools=SimpleNamespace(context=ToolExecutionContext(), controls={}), local=SimpleNamespace(),
        skills=None, plan_manager=SimpleNamespace(get_plan=lambda _session: None,
                                                   get_next_pending_task=lambda _session: None),
        subagent_config=None, control=SimpleNamespace(command=lambda: None),
    )
    seen = []

    async def fake_execute(_tool, _executor, _call, context):
        seen.append(context.knowledge_search_inventory)
        return result

    dispatcher.browser.execute = fake_execute
    async for _ in dispatcher._dispatch(state, first_call):
        pass
    state.messages.append({"role": "tool", "tool_call_id": "k1", "content": json.dumps(result)})
    # A checkpoint restore creates a new state and dispatcher; the receipt survives.
    state = ExecutionState.restore(state.checkpoint())
    restored = ToolDispatcher(
        catalog=dispatcher.catalog, registry=dispatcher.registry, executor=None,
        control_tools=SimpleNamespace(context=ToolExecutionContext(), controls={}), local=SimpleNamespace(),
        skills=None, plan_manager=dispatcher.plan_manager, subagent_config=None,
        control=SimpleNamespace(command=lambda: None),
    )
    restored.browser.execute = fake_execute
    async for _ in restored._dispatch(state, second_call):
        pass
    assert seen[0] == (frozenset(), frozenset(), frozenset())
    assert ("7", 1, chunk["fingerprint"]) in seen[1][0]
    assert ("7", fingerprint(document)) in seen[1][1]


async def test_revoked_legacy_shared_document_is_warned_without_reusing_its_body():
    """Old receipts still identify revoked access, but cannot prove fresh content."""
    legacy = {"success": True, "documents": {"7": {"source_type": "pricing"}},
              "results": [{"doc_id": 7, "text": "旧报价", "metadata": {"owner_tenant_id": "former-owner"}}]}
    inventory = context_inventory(_messages(legacy))
    assert not inventory[0] and not inventory[1]
    tool = KnowledgeBaseTool()
    tool._retriever = MagicMock(retrieve=AsyncMock(return_value=[]))
    auth_db = _db({})
    auth_db.__enter__.return_value.cursor.return_value.fetchone.return_value = {
        "sources": [{"source_type": "pricing", "owner_tenant_id": None}], "share_owners": [],
    }
    with tool_execution_scope(ToolExecutionContext(
            tenant_id="current", subagent_id="sales", knowledge_search_inventory=inventory)), \
            patch("src.db.database.get_db_connection", return_value=auth_db):
        result = await tool.execute(query="最新报价")
    assert result["success"] is True
    assert result["matched_count"] == 0
    assert result["invalid_history_documents"] == [{"doc_id": "7"}]
