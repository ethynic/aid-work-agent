"""check_threshold 快路径双阈值判断测试（v3.2 新增）

覆盖：
- token 缓存优先（cached_tokens > 0 触发 token 阈值）
- 缓存 = 0 时不回退 count_tokens（让消息数阈值兜底）
- 消息数阈值兜底
- 双阈值都未达返回 (False, "")
- check_threshold 内部不加载 messages（只做 O(1) + COUNT）
- 异常容错（COUNT 查询失败时降级到 0）
"""

import pytest

from src.memory.mid_term import ContextCompressionService, SessionMeta


@pytest.fixture
def service(mid_term_settings):
    return ContextCompressionService(settings_cfg=mid_term_settings)


def _patch_meta(service, *, context_token_count=0):
    async def _fake(session_id, source_type):
        return SessionMeta(
            session_id=session_id,
            source_type=source_type,
            tenant_id="t1",
            user_id="u1",
            subagent_id=None,
            context_token_count=context_token_count,
        )
    service._resolve_session_meta = _fake


def _patch_msg_count(service, count):
    """mock COUNT 查询路径（chat 源走 MessageDB，channel 源走 ChannelSessionManager）"""
    import src.db.models as models_mod
    import src.channels.session as session_mod

    models_mod.MessageDB.count_messages_by_session = staticmethod(
        lambda session_id, include_compacted=False: count
    )
    # ChannelSessionManager 是单例，patch 实例方法
    session_mod.channel_session_manager.count_messages_by_session = lambda session_id, include_compacted=False: count


def test_eval_threshold_cached_tokens_triggers(service):
    """_eval_threshold: cached_tokens > token_threshold → True"""
    # model_limit=10000, threshold=0.7*10000=7000
    should, reason = service._eval_threshold(
        cached_tokens=8000, msg_count=10, model_limit=10_000
    )
    assert should is True
    assert "token_threshold" in reason
    assert "cached=True" in reason


def test_eval_threshold_zero_cache_no_token_check(service):
    """缓存=0 时不做 token 判断（即使 messages 多到理论上 token 高也不触发 token 路径）"""
    # cached=0, msg_count=10（< 150 消息阈值），不触发
    should, reason = service._eval_threshold(
        cached_tokens=0, msg_count=10, model_limit=10_000
    )
    assert should is False
    assert reason == ""


def test_eval_threshold_zero_cache_msg_count_falls_back(service):
    """缓存=0 时消息数兜底：msg_count >= 150 → True（消息数阈值）"""
    should, reason = service._eval_threshold(
        cached_tokens=0, msg_count=200, model_limit=10_000
    )
    assert should is True
    assert "message_threshold" in reason


def test_eval_threshold_both_below(service):
    """两个都未达 → (False, "")"""
    should, reason = service._eval_threshold(
        cached_tokens=100, msg_count=50, model_limit=10_000
    )
    assert should is False
    assert reason == ""


def test_eval_threshold_both_triggered_token_wins(service):
    """两个都达 → token 优先（reason 含 token_threshold）"""
    should, reason = service._eval_threshold(
        cached_tokens=9999, msg_count=200, model_limit=10_000
    )
    assert should is True
    assert "token_threshold" in reason


@pytest.mark.asyncio
async def test_check_threshold_below_returns_false_and_meta(service):
    """check_threshold: 未达阈值 → (False, "", meta)"""
    _patch_meta(service, context_token_count=100)
    _patch_msg_count(service, 50)
    service._model_limit_cache = 10_000

    should, reason, meta = await service.check_threshold("sess", "chat")
    assert should is False
    assert reason == ""
    assert meta.session_id == "sess"
    assert meta.context_token_count == 100


@pytest.mark.asyncio
async def test_check_threshold_token_triggered(service):
    """check_threshold: 缓存 token 达阈值 → True"""
    _patch_meta(service, context_token_count=9999)
    _patch_msg_count(service, 50)
    service._model_limit_cache = 10_000

    should, reason, meta = await service.check_threshold("sess", "chat")
    assert should is True
    assert "token_threshold" in reason


@pytest.mark.asyncio
async def test_check_threshold_msg_count_triggered_when_cache_zero(service):
    """check_threshold: 缓存=0 时消息数兜底"""
    _patch_meta(service, context_token_count=0)
    _patch_msg_count(service, 200)
    service._model_limit_cache = 10_000

    should, reason, meta = await service.check_threshold("sess", "chat")
    assert should is True
    assert "message_threshold" in reason


@pytest.mark.asyncio
async def test_check_threshold_channel_source(service):
    """check_threshold: channel 源同样工作"""
    _patch_meta(service, context_token_count=0)
    # channel 源的 COUNT 查询路径
    import src.channels.session as session_mod
    session_mod.channel_session_manager.count_messages_by_session = lambda session_id, include_compacted=False: 200

    service._model_limit_cache = 10_000

    should, reason, meta = await service.check_threshold("sess_kf", "wecom_kf")
    assert should is True
    assert "message_threshold" in reason


@pytest.mark.asyncio
async def test_check_threshold_count_failure_degrades_to_zero(service, monkeypatch):
    """COUNT 查询抛异常 → 降级到 0，让 token 缓存兜底判断"""
    _patch_meta(service, context_token_count=9999)

    # 让 MessageDB.count_messages_by_session 抛异常
    def _boom(session_id, include_compacted=False):
        raise RuntimeError("db down")
    import src.db.models as models_mod
    monkeypatch.setattr(
        models_mod.MessageDB, "count_messages_by_session", staticmethod(_boom)
    )

    service._model_limit_cache = 10_000

    # 缓存 9999 >= 7000 仍能触发，不被 COUNT 异常影响
    should, reason, meta = await service.check_threshold("sess", "chat")
    assert should is True
    assert "token_threshold" in reason


@pytest.mark.asyncio
async def test_check_threshold_does_not_load_messages(service, monkeypatch):
    """check_threshold 不应触发 _load_messages（快路径要求）"""
    _patch_meta(service, context_token_count=100)
    _patch_msg_count(service, 50)
    service._model_limit_cache = 10_000

    # 让 _load_messages 抛异常验证未被调用
    async def _explode(*args, **kwargs):
        raise AssertionError("_load_messages 不应在 check_threshold 中被调用")
    monkeypatch.setattr(service, "_load_messages", _explode)

    should, reason, meta = await service.check_threshold("sess", "chat")
    assert should is False


@pytest.mark.asyncio
async def test_check_threshold_precise_check_when_cache_zero_and_many_messages(service, monkeypatch):
    """v3.2.1 P1-1: cached=0 + msg_count >= 80 时主动拉 messages + count_tokens 做精确判断。

    场景：Agent 异常未写入 context_token_count（缓存=0），但实际消息累积已撑爆 token。
    若没有这个保护，session 会因为 msg_count < 150 永不压缩。
    """
    _patch_meta(service, context_token_count=0)  # 缓存为 0
    _patch_msg_count(service, 100)  # 消息数 >= 80 但 < 150
    service._model_limit_cache = 10_000  # 阈值 7000

    # mock _load_messages 返回大消息列表（每条 1KB，100 条 = 100KB ≈ 30K+ token）
    big_messages = []
    for i in range(100):
        big_messages.append({"role": "user", "content": "x" * 1000, "id": 2 * i + 1})
        big_messages.append({"role": "assistant", "content": "y" * 1000, "id": 2 * i + 2})

    async def _fake_load(*args, **kwargs):
        return big_messages
    monkeypatch.setattr(service, "_load_messages", _fake_load)

    should, reason, meta = await service.check_threshold("sess_precise", "chat")

    # 应该触发（token 超过 7000 阈值）
    assert should is True, "cached=0 + msg_count>=80 时应主动拉 messages 做精确判断"
    assert "threshold" in reason or "precise" in reason.lower(), \
        f"reason 应反映精确计算路径，实际：{reason}"
