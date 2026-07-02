"""新增测试：P0-3 直连 deepseek / P2-2 脱敏 / P0-4 孤儿 tool 丢弃 / P0-2 tenant_id 过滤 / P1-3 INSERT...SELECT"""

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.settings import MidTermMemoryConfig
from src.memory.mid_term import (
    ContextCompressionService,
    _call_summary_llm_direct,
    _get_provider_api_key,
    sanitize_text,
)


# ============== P0-3：_call_summary_llm_direct 直连路径 ==============

@pytest.mark.asyncio
@pytest.mark.allow_direct_llm
async def test_call_summary_llm_direct_deepseek_success(monkeypatch):
    """配置了 DEEPSEEK_API_KEYS 时，_call_summary_llm_direct 应走 deepseek endpoint。

    mock httpx.AsyncClient 避免真实网络调用，验证：
    1. URL 指向 deepseek base_url + /chat/completions
    2. Authorization header 带 Bearer + api_key
    3. body.model 是 deepseek-chat
    4. 返回 content 从 choices[0].message.content 取
    """
    monkeypatch.setenv("DEEPSEEK_API_KEYS", "sk-test-deepseek-key")
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "choices": [{"message": {"content": "## 用户与背景\n- alice"}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 30},
            }

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["body"] = json
            return FakeResponse()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    content, usage = await _call_summary_llm_direct(
        provider="deepseek",
        model="deepseek-chat",
        messages_for_llm=[{"role": "user", "content": "test prompt"}],
        temperature=0.3,
        max_tokens=1500,
        timeout=30.0,
    )
    assert content == "## 用户与背景\n- alice"
    assert usage is not None
    assert "deepseek.com" in captured["url"]
    assert "/chat/completions" in captured["url"]
    assert captured["headers"]["Authorization"] == "Bearer sk-test-deepseek-key"
    assert captured["body"]["model"] == "deepseek-chat"
    assert captured["body"]["messages"][0]["content"] == "test prompt"


@pytest.mark.asyncio
async def test_call_summary_llm_direct_no_api_key_raises(monkeypatch):
    """provider 未配置 API key → _call_summary_llm_direct 抛 ValueError"""
    monkeypatch.delenv("DEEPSEEK_API_KEYS", raising=False)
    monkeypatch.delenv("QWEN_API_KEYS", raising=False)
    monkeypatch.delenv("ZHIPU_API_KEYS", raising=False)
    with pytest.raises(ValueError, match="API key not configured"):
        await _call_summary_llm_direct(
            provider="deepseek",
            model="deepseek-chat",
            messages_for_llm=[{"role": "user", "content": "x"}],
            temperature=0.3,
            max_tokens=100,
            timeout=10.0,
        )


def test_get_provider_api_key_picks_first_from_csv(monkeypatch):
    """多 key 用逗号分隔时，取第一个"""
    monkeypatch.setenv("DEEPSEEK_API_KEYS", "sk-first, sk-second,sk-third")
    assert _get_provider_api_key("deepseek") == "sk-first"


def test_get_provider_api_key_unconfigured_returns_none(monkeypatch):
    """未配置环境变量 → 返回 None"""
    monkeypatch.delenv("DEEPSEEK_API_KEYS", raising=False)
    assert _get_provider_api_key("deepseek") is None


@pytest.mark.asyncio
async def test_summary_llm_uses_direct_path_when_key_present(monkeypatch):
    """_call_summary_llm 在检测到 deepseek API key 时走直连路径（不走 gateway）"""
    monkeypatch.setattr(
        "src.memory.mid_term._get_provider_api_key",
        lambda provider: "sk-test-key" if provider == "deepseek" else None,
    )
    gateway = MagicMock()
    gateway.chat = AsyncMock(return_value={"content": "from gateway"})
    svc = ContextCompressionService(
        settings_cfg=MidTermMemoryConfig(),
        llm_gateway=gateway,
    )

    called_direct = {"flag": False}

    async def fake_direct(**kwargs):
        called_direct["flag"] = True
        return ("## 用户与背景\n- direct path", {"prompt_tokens": 10})

    monkeypatch.setattr("src.memory.mid_term._call_summary_llm_direct", fake_direct)
    monkeypatch.setattr(asyncio, "sleep", AsyncMock(return_value=None))

    result = await svc._call_summary_llm(None, [{"role": "user", "content": "x"}])

    assert result == "## 用户与背景\n- direct path"
    assert called_direct["flag"] is True
    # gateway 不应被调用
    assert gateway.chat.await_count == 0
    # 实际 provider 应记录为 deepseek
    assert svc._actual_provider == "deepseek"


@pytest.mark.asyncio
async def test_summary_llm_fallback_to_gateway_when_no_key(monkeypatch, mock_llm_for_summary):
    """_call_summary_llm 在无 API key 时 fallback 到主 gateway，并记录 warning"""
    monkeypatch.setattr(
        "src.memory.mid_term._get_provider_api_key",
        lambda provider: None,
    )
    monkeypatch.setattr(asyncio, "sleep", AsyncMock(return_value=None))

    svc = ContextCompressionService(
        settings_cfg=MidTermMemoryConfig(),
        llm_gateway=mock_llm_for_summary,
    )
    result = await svc._call_summary_llm(None, [{"role": "user", "content": "x"}])
    assert result is not None
    assert mock_llm_for_summary.chat.await_count == 1
    # actual_provider 应记录为主 gateway（fallback 后）
    assert svc._actual_provider is not None


# ============== P2-2：sanitize_text 脱敏 ==============

def test_sanitize_text_masks_password():
    """password=xxx 形式的敏感信息脱敏为 ***"""
    text = "user login with password=secret123 and continued"
    out = sanitize_text(text)
    assert "secret123" not in out
    assert "***" in out


def test_sanitize_text_masks_api_key():
    """api_key: xxx 形式脱敏"""
    text = "config: api_key: sk-abcdef123456 for provider"
    out = sanitize_text(text)
    assert "sk-abcdef123456" not in out
    assert "***" in out


def test_sanitize_text_masks_token():
    """token: xxx 形式脱敏（含 Bearer 场景）"""
    text = "Authorization: token: bearer_xyz_123"
    out = sanitize_text(text)
    assert "bearer_xyz_123" not in out


def test_sanitize_text_masks_secret():
    """secret: xxx 形式脱敏"""
    text = "oauth client_secret=super_secret_value"
    out = sanitize_text(text)
    assert "super_secret_value" not in out


def test_sanitize_text_preserves_normal_content():
    """正常文本不应被修改"""
    text = "用户问：今天的天气如何？assistant 回复：晴天 25 度"
    assert sanitize_text(text) == text


def test_sanitize_text_empty_returns_empty():
    assert sanitize_text("") == ""
    assert sanitize_text(None) is None


def test_format_messages_for_prompt_applies_sanitization():
    """_format_messages_for_prompt 对每条消息的 content 脱敏"""
    svc = ContextCompressionService(settings_cfg=MidTermMemoryConfig())
    msgs = [
        {"role": "user", "content": "my password=abc123 please help"},
        {"role": "assistant", "content": "sure thing"},
    ]
    out = svc._format_messages_for_prompt(msgs)
    assert "abc123" not in out
    assert "***" in out
    assert "sure thing" in out


# ============== P0-4：_drop_orphan_tool_messages ==============

def test_drop_orphan_tool_messages_no_orphans():
    """无孤儿 tool 的情况：全部 tool 都有前置 assistant(tool_calls)，原样返回"""
    msgs = [
        {"role": "user", "content": "ask"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "w", "arguments": "{}"}}]},
        {"role": "tool", "content": "result"},
    ]
    out = ContextCompressionService._drop_orphan_tool_messages(msgs)
    assert len(out) == len(msgs)


def test_drop_orphan_tool_messages_drops_unmatched():
    """孤儿 tool（前面是 user 或无 tool_calls 的 assistant）应降级为 assistant 文本保留。

    v3.2.2 变更：孤儿 tool 不再删除（删除会导致 COMPRESS 区内容流失、压缩死循环），
    而是降级为 assistant 文本消息保留，参与摘要。
    """
    msgs = [
        {"role": "user", "content": "ask"},  # 不是 assistant(tool_calls)
        {"role": "tool", "content": "orphan result", "id": 99},
        {"role": "user", "content": "next"},
    ]
    out = ContextCompressionService._drop_orphan_tool_messages(msgs)
    # 数量不变（降级而非删除）
    assert len(out) == 3
    # 孤儿 tool 降级为 assistant，不再有 role=tool
    assert all(m.get("role") != "tool" for m in out)
    # 降级后的消息 id 保留（用于压缩记录 compressed_ids）
    assert any(m.get("id") == 99 for m in out)
    # 降级后的消息 role=assistant
    downgraded = [m for m in out if m.get("id") == 99][0]
    assert downgraded["role"] == "assistant"


def test_drop_orphan_tool_messages_multi_tool_one_call():
    """一次 tool_calls 对应多个 tool 结果（multi-tool）：所有 tool 都应保留"""
    msgs = [
        {"role": "user", "content": "ask"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "w", "arguments": "{}"}},
            {"id": "c2", "type": "function", "function": {"name": "x", "arguments": "{}"}},
        ]},
        {"role": "tool", "content": "result1"},
        {"role": "tool", "content": "result2"},
    ]
    out = ContextCompressionService._drop_orphan_tool_messages(msgs)
    assert len(out) == 4  # 全部保留


def test_drop_orphan_tool_at_tail_start_idx(mid_term_settings):
    """P0-4 主场景：孤儿 tool 应降级为 assistant（v3.2.2：不再删除）。

    孤儿 tool 降级后保留 id，进入 COMPRESS 区参与摘要，不占着消息数却进不了压缩。
    同时验证 TAIL 边界对齐到安全切断点（user 消息）。
    """
    service = ContextCompressionService(settings_cfg=mid_term_settings)
    # 构造 35 条：header 3 + 1 user + 1 tool(孤儿) + 30 普通对话
    msgs = []
    for i in range(3):
        msgs.append({"role": "user", "content": f"h{i}", "id": i})
    msgs.append({"role": "user", "content": "mid user", "id": 3})
    msgs.append({"role": "tool", "content": "orphan result", "id": 4, "metadata": {"tool_name": "x"}})
    for i in range(30):
        msgs.append({"role": "user", "content": f"tail {i}", "id": 100 + i})
    assert len(msgs) == 35

    header, compress, tail = service._split_messages(msgs)
    # 孤儿 tool 降级为 assistant（不再有 role=tool）
    all_roles = [m.get("role") for m in header + compress + tail]
    assert "tool" not in all_roles
    # 4 号 id（降级后的孤儿 tool）保留在结果中（降级而非删除）
    all_ids = [m.get("id") for m in header + compress + tail]
    assert 4 in all_ids
    # 降级后的消息 role=assistant
    downgraded = [m for m in (header + compress + tail) if m.get("id") == 4][0]
    assert downgraded["role"] == "assistant"


# ============== P0-2：ContextSummaryDB tenant_id 过滤 ==============

def test_context_summary_db_get_active_filters_tenant(fake_db_connection):
    """get_active_by_session 传入 tenant_id 时 SQL 必须含 tenant_id 过滤"""
    mock_conn, mock_cursor = fake_db_connection
    mock_cursor.fetchone.return_value = None
    from src.db.models import ContextSummaryDB
    ContextSummaryDB.get_active_by_session("s1", "chat", tenant_id="tenant_a")
    sql = str(mock_cursor.execute.call_args.args[0]).lower()
    assert "tenant_id" in sql
    # 参数应含 tenant_a
    args = mock_cursor.execute.call_args.args[1]
    assert "tenant_a" in args


def test_context_summary_db_list_by_session_filters_tenant(fake_db_connection):
    """list_by_session 传入 tenant_id 时 SQL 必须含 tenant_id 过滤"""
    mock_conn, mock_cursor = fake_db_connection
    mock_cursor.fetchall.return_value = []
    from src.db.models import ContextSummaryDB
    ContextSummaryDB.list_by_session("s1", "chat", tenant_id="tenant_b")
    sql = str(mock_cursor.execute.call_args.args[0]).lower()
    assert "tenant_id" in sql
    args = mock_cursor.execute.call_args.args[1]
    assert "tenant_b" in args


def test_context_summary_db_get_by_id_filters_tenant(fake_db_connection):
    """get_by_id 传入 tenant_id 时 SQL 必须含 tenant_id 过滤"""
    mock_conn, mock_cursor = fake_db_connection
    mock_cursor.fetchone.return_value = None
    from src.db.models import ContextSummaryDB
    ContextSummaryDB.get_by_id("csum_x", tenant_id="tenant_c")
    sql = str(mock_cursor.execute.call_args.args[0]).lower()
    assert "tenant_id" in sql
    args = mock_cursor.execute.call_args.args[1]
    assert "tenant_c" in args


# ============== P1-4：单例线程安全 ==============

def test_get_compression_service_is_singleton():
    """get_compression_service 多次调用返回同一实例"""
    import src.memory.mid_term as mt
    # 重置单例
    mt._compression_service = None
    s1 = mt.get_compression_service()
    s2 = mt.get_compression_service()
    assert s1 is s2


def test_get_compression_service_thread_safe():
    """并发调用 get_compression_service 仍只创建一个实例"""
    import threading
    import src.memory.mid_term as mt
    mt._compression_service = None
    results: list = []
    lock = threading.Lock()

    def worker():
        s = mt.get_compression_service()
        with lock:
            results.append(s)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # 所有线程拿到的是同一个实例
    assert len(results) == 10
    assert all(r is results[0] for r in results)


# ============== P1-6：重试抖动 ==============

@pytest.mark.asyncio
async def test_summary_llm_retry_uses_jitter(monkeypatch, mock_llm_for_summary):
    """重试间隔应使用 1.0 * attempt + random.random()（带抖动），而非固定 1.0"""
    import random as random_mod
    monkeypatch.setattr(
        "src.memory.mid_term._get_provider_api_key",
        lambda provider: None,
    )
    # mock asyncio.sleep 捕获传入的 delay
    sleep_delays: list = []

    async def fake_sleep(d):
        sleep_delays.append(d)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    # mock random.random 返回固定值
    monkeypatch.setattr(random_mod, "random", lambda: 0.5)

    mock_llm_for_summary.chat = AsyncMock(side_effect=RuntimeError("fail"))
    svc = ContextCompressionService(
        settings_cfg=MidTermMemoryConfig(),
        llm_gateway=mock_llm_for_summary,
    )
    await svc._call_summary_llm(None, [{"role": "user", "content": "x"}])

    # 重试 2 次（attempt=1 后 sleep，attempt=2 后 sleep），首次成功不 sleep
    # 重试间隔公式：1.0 * attempt + random()
    assert len(sleep_delays) == 2
    # attempt=1 → 1.0*1 + 0.5 = 1.5
    # attempt=2 → 1.0*2 + 0.5 = 2.5
    assert sleep_delays[0] == pytest.approx(1.5)
    assert sleep_delays[1] == pytest.approx(2.5)


# ============== P1-7：双层超时 ==============

@pytest.mark.asyncio
async def test_summary_llm_outer_timeout_is_double_inner(monkeypatch, mock_llm_for_summary):
    """外层 asyncio.wait_for 超时应为 summary_llm.timeout_sec * 2（P1-7）"""
    monkeypatch.setattr(
        "src.memory.mid_term._get_provider_api_key",
        lambda provider: None,
    )
    monkeypatch.setattr(asyncio, "sleep", AsyncMock(return_value=None))

    captured_timeouts: list = []

    class FakeGateway:
        provider_name = "main"

        async def chat(self, **kwargs):
            return {"content": "ok"}

    real_wait_for = asyncio.wait_for

    async def fake_wait_for(coro, timeout):
        captured_timeouts.append(timeout)
        return await real_wait_for(coro, timeout)

    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)

    svc = ContextCompressionService(
        settings_cfg=MidTermMemoryConfig(),
        llm_gateway=FakeGateway(),
    )
    # summary_llm.timeout_sec 默认 30，外层应为 60
    await svc._call_summary_llm(None, [{"role": "user", "content": "x"}])
    assert len(captured_timeouts) == 1
    assert captured_timeouts[0] == 60  # 30 * 2


# ============== P1-5：count_tokens 中英文区分 ==============

def test_count_tokens_chinese_weighted_higher():
    """中文文本 token 估算应高于等长 ASCII（1 字 ≈ 1.5 token vs 4 字符 ≈ 1 token）"""
    from src.memory.mid_term import count_tokens, count_text_tokens
    chinese = "你好世界" * 10  # 40 个中文字符
    ascii_text = "a" * 40
    cn_tokens = count_text_tokens(chinese)
    ascii_tokens = count_text_tokens(ascii_text)
    # 中文 40 * 1.5 = 60，ASCII 40/4 = 10
    assert cn_tokens == 60
    assert ascii_tokens == 10
    assert cn_tokens > ascii_tokens


def test_count_tokens_mixed_content():
    """混合中英文"""
    from src.memory.mid_term import count_text_tokens
    # 4 中文 + 8 ASCII = 4*1.5 + 8/4 = 6 + 2 = 8
    assert count_text_tokens("你好世界abcdefgh") == 8
