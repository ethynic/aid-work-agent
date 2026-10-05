"""check_threshold 快路径双阈值判断测试（v3.2 新增）

覆盖：
- token 缓存优先（cached_tokens > 0 触发 token 阈值）
- 缓存 = 0 时不回退 count_tokens（让消息数阈值兜底）
- 消息数阈值兜底
- 双阈值都未达返回 (False, "")
- check_threshold 内部不加载 messages（只做 O(1) + COUNT）
- 异常容错（COUNT 查询失败时降级到 0）
- 有效阈值 = min(比例阈值, 绝对上限)（2026-10-04：绝对上限默认 60000，
  0/负数禁用；512K 档封顶触发，与 run_background_compression_scan 同公式）
"""

import pytest

from src.config.settings import MidTermMemoryConfig
from src.memory.mid_term import ContextCompressionService, SessionMeta


@pytest.fixture
def service(mid_term_settings):
    return ContextCompressionService(settings_cfg=mid_term_settings)


def _make_service(**overrides) -> ContextCompressionService:
    """按需覆盖配置构造 service（默认 header_keep=3 与 conftest fixture 对齐）"""
    return ContextCompressionService(
        settings_cfg=MidTermMemoryConfig(header_keep=3, **overrides)
    )


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
    """Isolate the per-service storage boundary, never mutate shared managers."""
    service._session_repository.count_messages = lambda session_id, source_type: count


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
    # cached=0, msg_count=10（< 200 消息阈值），不触发
    should, reason = service._eval_threshold(
        cached_tokens=0, msg_count=10, model_limit=10_000
    )
    assert should is False
    assert reason == ""


def test_eval_threshold_zero_cache_msg_count_falls_back(service):
    """缓存=0 时消息数兜底：msg_count >= 200 → True（消息数阈值）"""
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


# ========== 有效阈值 = min(比例阈值, 绝对上限)（2026-10-04 绝对上限）==========


def test_settings_default_absolute_is_60000():
    """配置默认值：绝对上限 60000（settings.py 与 config.yaml 同默认）"""
    assert MidTermMemoryConfig().token_threshold_absolute == 60_000


def test_effective_threshold_default_caps_large_model(service):
    """512K 档（_MODEL_CONTEXT_LIMITS 现役登记）比例阈值 358.4K → 被默认绝对上限 60000 封顶"""
    assert service._effective_token_threshold(512_000) == 60_000


def test_effective_threshold_absolute_zero_falls_back_to_ratio():
    """absolute=0 禁用 → 纯比例阈值（向后兼容旧部署）"""
    svc = _make_service(token_threshold_absolute=0)
    assert svc._effective_token_threshold(512_000) == 358_400


def test_effective_threshold_negative_disabled():
    """负数同样视为禁用（防手滑配 -1 导致 min 恒为负）"""
    svc = _make_service(token_threshold_absolute=-1)
    assert svc._effective_token_threshold(512_000) == 358_400


def test_effective_threshold_absolute_larger_keeps_ratio(service):
    """绝对上限 > 比例阈值 → 比例阈值生效（小模型档行为不变）"""
    assert service._effective_token_threshold(10_000) == 7_000


def test_eval_threshold_triggers_at_absolute_cap(service):
    """核心修复：512K 档会话 60k token（远未达比例阈值 358.4k）也触发压缩；
    旧公式下 117k 会话同样不触发——大会话膨胀到 10 万+ token 才压缩的问题口径"""
    should, reason = service._eval_threshold(
        cached_tokens=60_000, msg_count=50, model_limit=512_000
    )
    assert should is True
    assert "token_threshold(60000/60000" in reason
    assert "cached=True" in reason


def test_eval_threshold_below_absolute_cap_not_triggered(service):
    """59_999 < 60000 绝对上限且消息数未达 → 不触发（边界精确）"""
    should, reason = service._eval_threshold(
        cached_tokens=59_999, msg_count=50, model_limit=512_000
    )
    assert should is False
    assert reason == ""


def test_eval_threshold_absolute_disabled_restores_old_behavior():
    """absolute=0 时恢复纯比例行为：117k 会话在 512K 档不触发（旧行为复现口径）"""
    svc = _make_service(token_threshold_absolute=0)
    should, reason = svc._eval_threshold(
        cached_tokens=117_000, msg_count=50, model_limit=512_000
    )
    assert should is False
    assert reason == ""


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
    _patch_msg_count(service, 200)

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
