# -*- coding: utf-8 -*-
"""cached_subagent_registry 签名刷新单元测试（触发签名标记，对齐 skill 侧语义）

覆盖（M1 第二轮 CR P2：行为变更此前无任何直接测试）：
- 签名稳定 → 命中缓存（同实例）；
- 重建期间签名再变（S1→S2）→ 下次轮询必失配再重建（不驻留混合快照）；
- 重建重放 load_from_db（换新实例）。
"""

import pytest

import src.services.agent_runner.runtime.resource_cache as resource_cache

pytestmark = pytest.mark.agent


@pytest.fixture(autouse=True)
def _reset_subagent_caches():
    resource_cache._subagent_overlay_registries.clear()
    resource_cache._db_signatures.clear()
    yield
    resource_cache._subagent_overlay_registries.clear()
    resource_cache._db_signatures.clear()


@pytest.fixture
def stub_registry(monkeypatch):
    """替换 SubagentRegistry 为可数的桩（cached_subagent_registry 函数内 import）。"""
    loads = []

    class _StubRegistry:
        def __init__(self, subagents_dir):
            pass

        def load_from_db(self):
            loads.append(1)

    monkeypatch.setattr("src.subagents.registry.SubagentRegistry", _StubRegistry)
    return loads


def _signature_sequence(monkeypatch, values):
    calls = {"n": 0}

    def _fake_signature():
        index = min(calls["n"], len(values) - 1)
        calls["n"] += 1
        return values[index]

    monkeypatch.setattr(resource_cache, "_read_db_signature", _fake_signature)
    return calls


class TestSubagentCacheTriggerSignatureTagging:
    def test_stable_signature_hits_cache(self, stub_registry, monkeypatch):
        _signature_sequence(monkeypatch, ["S1"])
        r1 = resource_cache.cached_subagent_registry("subagents")
        r2 = resource_cache.cached_subagent_registry("subagents")
        assert r1 is r2
        assert len(stub_registry) == 1

    def test_mid_rebuild_change_rebuilds_again_not_stale_cached(self, stub_registry, monkeypatch):
        """重建期间 DB 签名再变（S1→S2）：以触发签名 S1 标记 → 下次轮询 S2≠S1
        必再重建并换新实例，最终收敛到 S2 快照后命中缓存。"""
        _signature_sequence(monkeypatch, ["S1", "S2"])
        r1 = resource_cache.cached_subagent_registry("subagents")  # 空 → 重建，标记 S1
        r2 = resource_cache.cached_subagent_registry("subagents")  # S2≠S1 → 再重建，标记 S2
        r3 = resource_cache.cached_subagent_registry("subagents")  # S2 命中

        assert r1 is not r2, "重建期间签名变化后，下一次调用必须再重建（不驻留混合快照）"
        assert r3 is r2
        assert len(stub_registry) == 2
