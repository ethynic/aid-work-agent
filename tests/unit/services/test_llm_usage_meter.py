from src.services.llm_usage_meter import (
    TokenUsage,
    install_usage_recorder,
    normalize_usage,
    record_usage,
    reset_usage_recorder,
    reset_usage_context,
    set_usage_context,
)
import asyncio
import pytest


def test_usage_aggregation_keeps_cached_as_input_subset():
    captured = []
    token = install_usage_recorder(
        lambda association, stage, usage: captured.append((association, stage, usage))
    )
    try:
        set_usage_context(association="协会一", stage="官网识别")
        record_usage({"prompt_tokens": 100, "cached_tokens": 40, "completion_tokens": 20, "total_tokens": 120})
        record_usage({"prompt_tokens": 50, "cached_tokens": 10, "completion_tokens": 5, "total_tokens": 55})
    finally:
        reset_usage_recorder(token)
    total = TokenUsage()
    for _, _, usage in captured:
        total.add(usage)
    assert total.as_dict() == {
        "input_tokens": 150,
        "cached_input_tokens": 50,
        "output_tokens": 25,
        "total_tokens": 175,
        "call_count": 2,
    }


def test_missing_or_invalid_usage_is_not_estimated():
    assert normalize_usage(None) is None
    assert normalize_usage({}) is None
    assert normalize_usage({"prompt_tokens": "100"}) is None
    assert normalize_usage({"prompt_tokens": 10, "completion_tokens": -1}) is None
    assert normalize_usage({"prompt_tokens": 10, "cached_tokens": 11, "completion_tokens": 1}) is None
    assert normalize_usage({"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}) is None


def test_total_is_recomputed_without_counting_cached_tokens_twice():
    usage = normalize_usage({
        "prompt_tokens": 100,
        "cached_tokens": 40,
        "completion_tokens": 20,
        "total_tokens": 160,
    })
    assert usage.as_dict() == {
        "input_tokens": 100,
        "cached_input_tokens": 40,
        "output_tokens": 20,
        "total_tokens": 120,
        "call_count": 1,
    }


def test_aggregated_child_usage_preserves_call_count():
    usage = normalize_usage({
        "prompt_tokens": 30,
        "cached_tokens": 5,
        "completion_tokens": 7,
        "total_tokens": 999,
        "call_count": 2,
    })
    assert usage.total_tokens == 37
    assert usage.call_count == 2


def test_reset_stops_recording_in_current_context():
    captured = []
    token = install_usage_recorder(
        lambda association, stage, usage: captured.append((association, stage, usage))
    )
    set_usage_context(association="协会一", stage="官网")
    reset_usage_recorder(token)
    record_usage({"prompt_tokens": 1, "completion_tokens": 1})
    assert captured == []


def test_nested_usage_context_restores_outer_association_and_stage():
    captured = []
    recorder_token = install_usage_recorder(
        lambda association, stage, usage: captured.append((association, stage))
    )
    outer = set_usage_context(association="outer", stage="outer-stage")
    try:
        inner = set_usage_context(association="inner", stage="inner-stage")
        try:
            record_usage({"prompt_tokens": 1, "completion_tokens": 1})
        finally:
            reset_usage_context(inner)
        record_usage({"prompt_tokens": 1, "completion_tokens": 1})
    finally:
        reset_usage_context(outer)
        reset_usage_recorder(recorder_token)
    assert captured == [("inner", "inner-stage"), ("outer", "outer-stage")]


def test_contextvar_usage_is_isolated_between_concurrent_tasks():
    async def collect(name, prompt):
        captured = []
        token = install_usage_recorder(
            lambda association, _stage, usage: captured.append((association, usage.input_tokens))
        )
        try:
            set_usage_context(association=name, stage="test")
            await asyncio.sleep(0)
            record_usage({"prompt_tokens": prompt, "completion_tokens": 1, "total_tokens": prompt + 1})
            return captured
        finally:
            reset_usage_recorder(token)

    async def runner():
        return await asyncio.gather(collect("协会一", 10), collect("协会二", 20))

    first, second = asyncio.run(runner())
    assert first == [("协会一", 10)]
    assert second == [("协会二", 20)]


def test_gateway_chat_records_provider_usage(monkeypatch):
    from src.llm.gateway import llm_gateway

    async def fake_call(_name, **_kwargs):
        return {
            "content": "ok",
            "usage": {
                "prompt_tokens": 30,
                "cached_tokens": 12,
                "completion_tokens": 7,
                "total_tokens": 37,
            },
        }

    monkeypatch.setattr(llm_gateway, "_failover_enabled", False)
    monkeypatch.setattr(llm_gateway, "_call_with_pool", fake_call)
    captured = []
    token = install_usage_recorder(
        lambda association, stage, usage: captured.append((association, stage, usage))
    )
    try:
        set_usage_context(association="协会一", stage="官网识别")
        asyncio.run(llm_gateway.chat(messages=[{"role": "user", "content": "x"}]))
    finally:
        reset_usage_recorder(token)
    assert captured[0][2].as_dict() == {
        "input_tokens": 30,
        "cached_input_tokens": 12,
        "output_tokens": 7,
        "total_tokens": 37,
        "call_count": 1,
    }


def test_gateway_exception_does_not_record_usage(monkeypatch):
    from src.llm.gateway import llm_gateway

    async def failed_call(_name, **_kwargs):
        raise RuntimeError("provider failed")

    monkeypatch.setattr(llm_gateway, "_failover_enabled", False)
    monkeypatch.setattr(llm_gateway, "_call_with_pool", failed_call)
    captured = []
    token = install_usage_recorder(lambda *_args: captured.append(True))
    try:
        try:
            asyncio.run(llm_gateway.chat(messages=[{"role": "user", "content": "x"}]))
        except RuntimeError:
            pass
    finally:
        reset_usage_recorder(token)
    assert captured == []


@pytest.mark.parametrize(
    "provider_class",
    [
        pytest.param(__import__("src.llm.providers.qwen", fromlist=["QwenProvider"]).QwenProvider, id="qwen"),
        pytest.param(__import__("src.llm.providers.zhipu", fromlist=["ZhipuProvider"]).ZhipuProvider, id="zhipu"),
        pytest.param(__import__("src.llm.providers.deepseek", fromlist=["DeepSeekProvider"]).DeepSeekProvider, id="deepseek"),
    ],
)
def test_provider_parser_preserves_top_level_cache_hit_tokens(provider_class):
    provider = provider_class.__new__(provider_class)
    result = provider._parse_response(
        {
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
                "prompt_cache_hit_tokens": 35,
            },
        }
    )
    assert result["usage"]["cached_tokens"] == 35
