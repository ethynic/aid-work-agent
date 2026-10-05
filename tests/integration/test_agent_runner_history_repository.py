"""Real PostgreSQL scope/ordering/cache checks, disposable database required."""
import uuid

import pytest

from src.core.agent_engine.contracts import Identity
from src.services.agent_runner.runtime.history_repository import HistoryRepository


@pytest.fixture
def sessions():
    from src.db.database import get_db_connection
    session, tenant, user = (f"runner_test_{uuid.uuid4().hex}" for _ in range(3))
    with get_db_connection() as connection:
        cursor = connection.cursor()
        cursor.execute("SELECT current_database() AS name")
        assert cursor.fetchone()["name"].startswith("aid_test_"), "Disposable test DB required"
        cursor.execute("INSERT INTO chat_sessions (session_id, tenant_id, user_id) VALUES (%s,%s,%s)",
                       (session, tenant, user))
        cursor.execute("INSERT INTO channel_sessions (session_id, tenant_id, user_id,channel_type,channel_user_id) VALUES (%s,%s,%s,'feishu','platform-user')",
                       (session, tenant, user))
        for text, compacted in (("web-first", False), ("web-compacted", True), ("web-last", False)):
            cursor.execute("INSERT INTO chat_messages (message_id,session_id,role,content,compacted) VALUES (%s,%s,'user',%s,%s)",
                           (uuid.uuid4().hex, session, text, compacted))
        for text, compacted, recalled, status in (("channel-first", False, False, "active"),
                ("channel-compacted", True, False, "active"), ("channel-recalled", False, True, "active"),
                ("channel-invalid", False, False, "invalid"), ("channel-last", False, False, "active")):
            cursor.execute("INSERT INTO channel_messages (message_id,session_id,tenant_id,role,content,compacted,is_recalled,status) VALUES (%s,%s,%s,'user',%s,%s,%s,%s)",
                           (uuid.uuid4().hex, session, tenant, text, compacted, recalled, status))
        connection.commit()
    yield session, tenant, user
    with get_db_connection() as connection:
        cursor = connection.cursor()
        for table in ("chat_context_summaries", "chat_messages", "channel_messages", "chat_sessions", "channel_sessions"):
            cursor.execute(f"DELETE FROM {table} WHERE session_id = %s", (session,))
        connection.commit()


def test_real_web_history_enforces_tenant_user_and_filters_compacted_rows(sessions):
    session, tenant, user = sessions
    reader = HistoryRepository(Identity(tenant, user, session, "chat", "web"))
    reader.assert_authorized()
    assert [row["content"] for row in reader.read_web(session)] == ["web-first", "web-last"]
    assert [row["content"] for row in reader.read_web(session, limit=1)] == ["web-last"]
    for identity in (Identity("other", user, session), Identity(tenant, "other", session)):
        forbidden = HistoryRepository(identity)
        with pytest.raises(PermissionError, match="SESSION_ACCESS_DENIED"):
            forbidden.assert_authorized()
        assert forbidden.read_web(session) == []


def test_real_channel_history_filters_recalled_invalid_compacted_and_other_tenant(sessions):
    session, tenant, user = sessions
    reader = HistoryRepository(Identity(tenant, user, session, "feishu", "channel"))
    reader.assert_authorized()
    assert [row["content"] for row in reader.read_channel(session)] == ["channel-first", "channel-last"]
    assert [row["content"] for row in reader.read_channel(session, limit=1)] == ["channel-last"]
    forbidden = HistoryRepository(Identity("other", user, session, "feishu", "channel"))
    with pytest.raises(PermissionError, match="SESSION_ACCESS_DENIED"):
        forbidden.assert_authorized()
    assert forbidden.read_channel(session) == []


def test_same_session_id_in_both_stores_never_causes_cross_kind_fallback(sessions):
    session, tenant, user = sessions
    web = HistoryRepository(Identity(tenant, user, session))
    channel = HistoryRepository(Identity(tenant, user, session, "feishu", "channel"))
    with pytest.raises(ValueError, match="HISTORY_SESSION_KIND_MISMATCH"):
        web.read_channel(session)
    with pytest.raises(ValueError, match="HISTORY_SESSION_KIND_MISMATCH"):
        channel.read_web(session)
    with pytest.raises(ValueError, match="HISTORY_SESSION_KIND_MISMATCH"):
        web.read_web("different-session")


def test_context_token_updates_use_bound_tenant_session_and_kind(sessions):
    from src.db.database import get_db_connection
    session, tenant, user = sessions
    HistoryRepository(Identity(tenant, user, session)).update_context_tokens(123)
    HistoryRepository(Identity("other", user, session)).update_context_tokens(999)
    HistoryRepository(Identity(tenant, user, session, "feishu", "channel")).update_context_tokens(456)
    with get_db_connection() as connection:
        cursor = connection.cursor()
        for table, expected in (("chat_sessions", 123), ("channel_sessions", 456)):
            cursor.execute(f"SELECT context_token_count FROM {table} WHERE session_id = %s", (session,))
            assert cursor.fetchone()["context_token_count"] == expected


def test_compression_storage_port_reads_counts_limits_and_keeps_web_channel_separate(sessions):
    from src.memory.session_repository import CompressionSessionRepository
    session, tenant, user = sessions
    repository = CompressionSessionRepository()
    assert repository.get_session(session, "chat")["user_id"] == user
    assert repository.get_session(session, "feishu")["channel_type"] == "feishu"
    for source, expected in (("chat", ["web-first", "web-last"]),
                             ("feishu", ["channel-first", "channel-last"])):
        assert [row["content"] for row in repository.load_messages(session, source)] == expected
        assert [row["content"] for row in repository.load_messages(session, source, limit=1)] == [expected[-1]]
        assert repository.count_messages(session, source) == len(expected)
        assert repository.get_session("not-present", source) is None
        assert repository.load_messages("not-present", source) == []
        assert repository.count_messages("not-present", source) == 0


@pytest.mark.parametrize("source", ["chat", "feishu"])
def test_real_compression_persist_marks_only_its_store_and_invalidates_cached_history(sessions, source, monkeypatch):
    from src.memory.mid_term import ContextCompressionService
    from src.memory.session_repository import CompressionSessionRepository
    from src.db.models import ContextSummaryDB
    from unittest.mock import Mock
    session, tenant, user = sessions
    repository = CompressionSessionRepository()
    from src.memory.mid_term import delete_cached_pattern
    invalidate = Mock(wraps=delete_cached_pattern)
    monkeypatch.setattr("src.memory.mid_term.delete_cached_pattern", invalidate)
    service = ContextCompressionService(session_repository=repository)
    rows = repository.load_messages(session, source)
    summary_id = service._persist_atomically(session, source, tenant, user, None,
        "compressed first turn", [rows[0]["id"]], 100, 20)
    assert ContextSummaryDB.get_active_by_session(session, source, tenant)["summary_id"] == summary_id
    assert [row["content"] for row in repository.load_messages(session, source)] == [rows[-1]["content"]]
    assert repository.count_messages(session, source) == 1
    other = "chat" if source == "feishu" else "feishu"
    assert repository.count_messages(session, other) == 2
    assert invalidate.call_count == 1
    assert invalidate.call_args.args[1] == session


def test_channel_bound_repository_excludes_foreign_message_tenant_but_preserves_verified_legacy_null(sessions):
    from src.db.database import get_db_connection
    session, tenant, user = sessions
    with get_db_connection() as connection:
        cursor = connection.cursor()
        # Explicitly model an older schema in this disposable DB; current schema forbids NULL.
        cursor.execute("ALTER TABLE channel_messages ALTER COLUMN tenant_id DROP NOT NULL")
        for text, message_tenant in (("foreign", "other-tenant"), ("legacy-null", None)):
            cursor.execute("INSERT INTO channel_messages (message_id,session_id,tenant_id,role,content,status) VALUES (%s,%s,%s,'user',%s,'active')", (uuid.uuid4().hex, session, message_tenant, text))
        connection.commit()
    try:
        repository = HistoryRepository(Identity(tenant, user, session, "feishu", "channel"))
        repository.assert_authorized()
        assert [row["content"] for row in repository.read_channel(session)] == ["channel-first", "channel-last", "legacy-null"]
        assert repository.count_messages(session, "feishu") == 3
        forbidden = HistoryRepository(Identity("other-tenant", user, session, "feishu", "channel"))
        with pytest.raises(PermissionError):
            forbidden.assert_authorized()
        assert forbidden.read_channel(session) == []
        assert forbidden.count_messages(session, "feishu") == 0
    finally:
        with get_db_connection() as connection:
            cursor = connection.cursor()
            cursor.execute("DELETE FROM channel_messages WHERE session_id = %s AND tenant_id IS NULL", (session,))
            cursor.execute("ALTER TABLE channel_messages ALTER COLUMN tenant_id SET NOT NULL")
            connection.commit()


@pytest.mark.asyncio
async def test_default_channel_compression_parses_text_metadata_and_keeps_tool_chain(sessions, monkeypatch):
    import json
    from unittest.mock import AsyncMock
    from src.config.settings import MidTermMemoryConfig
    from src.db.database import get_db_connection
    from src.memory.mid_term import ContextCompressionService, SessionMeta
    from src.memory.session_repository import CompressionSessionRepository
    session, tenant, user = sessions
    call = {"id": "compression-call", "type": "function", "function": {"name": "boss_filter_options", "arguments": "{}"}}
    rows = [("assistant", "", {"tool_calls": [call]}),
            ("tool", "visible BOSS options", {"tool_call_id": "compression-call", "tool_name": "boss_filter_options"}),
            ("assistant", "options read", {}), ("user", "latest question", {}), ("assistant", "latest answer", {})]
    with get_db_connection() as connection:
        cursor = connection.cursor()
        for role, content, metadata in rows:
            cursor.execute("INSERT INTO channel_messages (message_id,session_id,tenant_id,role,content,metadata,status) VALUES (%s,%s,%s,%s,%s,%s,'active')", (uuid.uuid4().hex, session, tenant, role, content, json.dumps(metadata)))
        connection.commit()
    repository = CompressionSessionRepository()
    messages = repository.load_messages(session, "feishu")
    assert next(message for message in messages if message["role"] == "tool")["metadata"]["tool_name"] == "boss_filter_options"
    service = ContextCompressionService(settings_cfg=MidTermMemoryConfig(header_keep=0, tail_keep=2), session_repository=repository)
    summary = AsyncMock(return_value=("BOSS options summary", {"prompt_tokens": 30, "completion_tokens": 10}, False))
    monkeypatch.setattr(service, "_call_summary_llm", summary)
    service._model_limit_cache = 10000
    result = await service.compress_now(session, "feishu", SessionMeta(session_id=session, source_type="feishu", tenant_id=tenant, user_id=user, context_token_count=999999))
    assert result is not None and result.compressed_message_count > 0
    summary.assert_awaited_once()
    processed = summary.call_args.args[1]
    assert any(message["role"] == "tool" for message in processed)
    assert "boss_filter_options" in service._format_messages_for_prompt(processed)
    assert repository.count_messages(session, "chat") == 2


def test_legacy_clarification_promotes_only_after_real_pg_session_owner_authorization(sessions):
    from src.services.agent_runner.runtime.clarification import ClarificationStore
    session, tenant, user = sessions
    class RedisBoundary:
        def __init__(self):
            self.values = {}
        def make_key(self, prefix, identifier):
            return f"{prefix}:{identifier}"
        def hset(self, key, field, value):
            self.values.setdefault(key, {})[field] = value
        def hget(self, key, field):
            return self.values.get(key, {}).get(field)
        def hgetall(self, key):
            return dict(self.values.get(key, {}))
        def expire(self, key, ttl):
            return True
        def delete(self, key):
            self.values.pop(key, None)
    redis = RedisBoundary()
    old_key = redis.make_key("pending_clarification", session)
    pending = {"subagent_name": "hiring", "question": "Which city?", "execution_id": "child-id", "task_description": "find candidates"}
    redis.values[old_key] = dict(pending)
    def authorize(identity):
        HistoryRepository(identity).assert_authorized(require_user=True)
    store = ClarificationStore(redis, authorize_legacy=authorize)
    for foreign in (Identity("other", user, session), Identity(tenant, "other", session)):
        with pytest.raises(PermissionError):
            store.read(foreign)
        assert redis.values[old_key] == pending
    identity = Identity(tenant, user, session)
    assert ClarificationStore(redis).read(identity) is None, "New runner cannot guess legacy ownership"
    assert store.read(identity) == pending
    assert old_key not in redis.values
    assert store.read(identity) == pending
    store.clear(identity)
    assert store.read(identity) is None


@pytest.mark.parametrize("source,kind", [("chat", "web"), ("feishu", "channel")])
def test_strict_history_summary_cannot_read_foreign_tenant_and_null_means_only_null(sessions, source, kind, monkeypatch):
    from src.config.settings import settings
    from src.db.database import get_db_connection
    from src.memory.manager import MemoryManager
    from src.memory.mid_term import ContextCompressionService
    from src.services.agent_runner.runtime.history import SessionHistory
    monkeypatch.setattr(settings.memory.mid_term, "enabled", True)
    session, tenant, user = sessions
    summary_id = ContextCompressionService()._persist_atomically(session, source, "foreign-tenant", user,
        None, "FOREIGN_SUMMARY_MUST_NOT_LEAK", [], 100, 20)
    repository = HistoryRepository(Identity(tenant, user, session, source, kind))
    repository.assert_authorized()
    memory = MemoryManager()
    memory.add_message(session, {"role": "user", "content": "current task"})
    history = SessionHistory(memory, source, repository, session_kind=kind)
    assert history.active_summary(session) is None
    assert "FOREIGN_SUMMARY_MUST_NOT_LEAK" not in str(history._build_messages(session))
    with get_db_connection() as connection:
        cursor = connection.cursor()
        cursor.execute("UPDATE chat_context_summaries SET tenant_id = %s, summary_text = 'OWN_SUMMARY' WHERE summary_id = %s", (tenant, summary_id))
        connection.commit()
    assert history.active_summary(session) == "OWN_SUMMARY"
    assert "OWN_SUMMARY" in str(history._build_messages(session))
    unscoped = HistoryRepository(Identity(None, user, session, source, kind))
    assert unscoped.active_summary(session, source) is None
    with get_db_connection() as connection:
        cursor = connection.cursor()
        cursor.execute("UPDATE chat_context_summaries SET tenant_id = NULL, summary_text = 'NULL_ONLY' WHERE summary_id = %s", (summary_id,))
        connection.commit()
    assert unscoped.active_summary(session, source) == "NULL_ONLY"
    assert repository.active_summary(session, source) is None
