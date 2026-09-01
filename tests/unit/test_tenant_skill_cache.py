"""TenantSkillCache 单元测试

核心回归点：缓存按 tenant_id 存「全量」skills，allowed 白名单在返回时过滤，
避免首个加载的 Agent 白名单污染后续不同白名单的 Agent（缓存污染事故根因）。
"""

import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.saas.services.tenant_skill_cache import TenantSkillCache

BASE_SKILLS = {
    "lead-management": MagicMock(),
    "after-sales-core": MagicMock(),
    "pre-sales-api": MagicMock(),
}


@pytest.fixture
def cache(monkeypatch, tmp_path):
    """构造 TenantSkillCache，mock 磁盘加载（基础 3 个 skill，无租户自定义 skill）"""

    def _fake_loader_cls(skills_dir):
        loader = MagicMock()
        loader.skills = dict(BASE_SKILLS)
        return loader

    monkeypatch.setattr(
        "src.saas.services.tenant_skill_cache.SkillLoader", _fake_loader_cls
    )
    monkeypatch.setattr(
        "src.saas.services.tenant_skill_cache.SkillResolver.get_tenant_skills_dir",
        lambda tid: Path("/nonexistent_tenant_skills"),
    )
    return TenantSkillCache(ttl_seconds=300), tmp_path


class TestTenantSkillCache:
    def test_first_load_filters_by_allowed(self, cache):
        cache_inst, base_dir = cache
        allowed = {"lead-management", "pre-sales-api"}
        result = cache_inst.get_or_load("tenant_x", base_dir, allowed)
        assert set(result.keys()) == allowed

    def test_allowed_none_returns_full(self, cache):
        cache_inst, base_dir = cache
        result = cache_inst.get_or_load("tenant_x", base_dir, None)
        assert set(result.keys()) == set(BASE_SKILLS.keys())

    def test_cache_hit_different_allowed_isolated(self, cache):
        """核心回归：缓存命中时不同 allowed 必须返回各自过滤结果，互不污染"""
        cache_inst, base_dir = cache

        # after-sales 白名单先加载并填充缓存
        after_allowed = {"lead-management", "after-sales-core"}
        r1 = cache_inst.get_or_load("tenant_x", base_dir, after_allowed)
        assert set(r1.keys()) == after_allowed

        # 缓存命中：pre-sales 白名单必须拿到自己的 pre-sales-api
        # （修复前会命中 after-sales 过滤后的缓存，拿不到 pre-sales-api）
        pre_allowed = {"lead-management", "pre-sales-api"}
        r2 = cache_inst.get_or_load("tenant_x", base_dir, pre_allowed)
        assert set(r2.keys()) == pre_allowed

        # 返回的过滤结果不应改变缓存内全量内容
        assert set(cache_inst._cache["tenant_x"][0].keys()) == set(BASE_SKILLS.keys())

    def test_ttl_expiry_reloads(self, cache):
        cache_inst, base_dir = cache
        cache_inst.get_or_load("tenant_x", base_dir, None)

        # 手动将缓存时间戳置为过期，下次访问应重新加载
        cached_full, _ = cache_inst._cache["tenant_x"]
        cache_inst._cache["tenant_x"] = (cached_full, time.time() - 301)

        result = cache_inst.get_or_load("tenant_x", base_dir, {"pre-sales-api"})
        assert set(result.keys()) == {"pre-sales-api"}
