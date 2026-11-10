"""PromptResolver 最新版直读单元测试（标签机制下线后）

验证：
- resolve 直读最新版本（get_latest → get_by_prompt_and_version），无标签查找路径
- 无 registry / 无任何版本 → None（调用方降级文件系统）
- 内容缓存命中时不查库
- PromptCache.invalidate_prompt 只清 content 键；label 缓存接口已整体移除
"""

import pytest
from unittest.mock import patch, MagicMock

pytestmark = pytest.mark.unit

RESOLVER = "src.prompts.prompt_resolver"


def _fake_cache(return_content=None, return_registry=None):
    """伪造模块级 get_cached/set_cached：按前缀区分 registry 与 content"""
    def fake_get_cached(*prefix):
        if prefix[0] == "prompt_content":
            return return_content
        if prefix[0] == "prompt_reg":
            return return_registry
        return None

    def fake_set_cached(*_prefix, value=None, ttl=None):
        return None

    return fake_get_cached, fake_set_cached


class TestResolveLatestVersion:
    """resolve()：registry → 最新版 → 内容"""

    def test_resolve_reads_latest_version_directly(self):
        """无标签路径：registry 命中后直接 get_latest，按其版本读内容"""
        from src.prompts.prompt_resolver import PromptResolver

        get_cached, set_cached = _fake_cache()
        registry = {"id": "uuid-1", "latest_version": 9}
        with patch(f"{RESOLVER}.get_cached", side_effect=get_cached), \
             patch(f"{RESOLVER}.set_cached", side_effect=set_cached), \
             patch(f"{RESOLVER}.PromptRegistryDB.get_by_scope", return_value=registry) as mock_scope, \
             patch(f"{RESOLVER}.PromptVersionDB.get_latest", return_value={"version": 7}) as mock_latest, \
             patch(f"{RESOLVER}.PromptVersionDB.get_by_prompt_and_version",
                   return_value={"version": 7, "content": "V7 内容"}) as mock_content:
            result = PromptResolver().resolve("subagent", "agent_a")

        assert result == "V7 内容"
        mock_scope.assert_called_once_with(None, "subagent", "agent_a")
        mock_latest.assert_called_once_with("uuid-1")
        # 内容按最新版本号读取（保存即生效，无第二指针）
        mock_content.assert_called_once_with("uuid-1", 7)

    def test_no_registry_returns_none(self):
        """registry 缺失 → None，不查版本表"""
        from src.prompts.prompt_resolver import PromptResolver

        get_cached, set_cached = _fake_cache()
        with patch(f"{RESOLVER}.get_cached", side_effect=get_cached), \
             patch(f"{RESOLVER}.set_cached", side_effect=set_cached), \
             patch(f"{RESOLVER}.PromptRegistryDB.get_by_scope", return_value=None), \
             patch(f"{RESOLVER}.PromptVersionDB.get_latest") as mock_latest:
            result = PromptResolver().resolve("subagent", "agent_missing")

        assert result is None
        mock_latest.assert_not_called()

    def test_registry_without_versions_returns_none(self):
        """registry 存在但无任何版本 → None"""
        from src.prompts.prompt_resolver import PromptResolver

        get_cached, set_cached = _fake_cache()
        with patch(f"{RESOLVER}.get_cached", side_effect=get_cached), \
             patch(f"{RESOLVER}.set_cached", side_effect=set_cached), \
             patch(f"{RESOLVER}.PromptRegistryDB.get_by_scope", return_value={"id": "uuid-1"}), \
             patch(f"{RESOLVER}.PromptVersionDB.get_latest", return_value=None), \
             patch(f"{RESOLVER}.PromptVersionDB.get_by_prompt_and_version") as mock_content:
            result = PromptResolver().resolve("subagent", "agent_empty")

        assert result is None
        mock_content.assert_not_called()

    def test_content_cache_hit_skips_db(self):
        """内容缓存命中 → 不查版本表"""
        from src.prompts.prompt_resolver import PromptResolver

        get_cached, set_cached = _fake_cache(return_content="缓存内容")
        with patch(f"{RESOLVER}.get_cached", side_effect=get_cached), \
             patch(f"{RESOLVER}.set_cached", side_effect=set_cached), \
             patch(f"{RESOLVER}.PromptRegistryDB.get_by_scope", return_value={"id": "uuid-1"}), \
             patch(f"{RESOLVER}.PromptVersionDB.get_latest", return_value={"version": 7}), \
             patch(f"{RESOLVER}.PromptVersionDB.get_by_prompt_and_version") as mock_content:
            result = PromptResolver().resolve("subagent", "agent_a")

        assert result == "缓存内容"
        mock_content.assert_not_called()

    def test_tenant_registry_key_uses_tenant_id(self):
        """租户 scope（tenant_extra）：registry 键带 tenant_id"""
        from src.prompts.prompt_resolver import PromptResolver

        seen_prefixes = []

        def fake_get_cached(*prefix):
            seen_prefixes.append(prefix)
            return None

        def fake_set_cached(*_prefix, **_kw):
            return None

        with patch(f"{RESOLVER}.get_cached", side_effect=fake_get_cached), \
             patch(f"{RESOLVER}.set_cached", side_effect=fake_set_cached), \
             patch(f"{RESOLVER}.PromptRegistryDB.get_by_scope", return_value=None), \
             patch(f"{RESOLVER}.PromptVersionDB.get_latest") as mock_latest:
            result = PromptResolver().resolve("tenant_extra", "extra:a:t1", tenant_id="t1")

        assert result is None
        mock_latest.assert_not_called()
        assert seen_prefixes[0] == ("prompt_reg", "t1", "tenant_extra", "extra:a:t1")


class TestPromptCacheInvalidation:
    """PromptCache：invalidate_prompt 只清 content；label 缓存接口整体移除"""

    def test_invalidate_prompt_only_clears_content(self):
        from src.prompts.prompt_resolver import PromptCache

        pattern = MagicMock()
        with patch(f"{RESOLVER}.delete_cached_pattern", pattern):
            PromptCache().invalidate_prompt("uuid-1")

        assert pattern.call_count == 1
        args = pattern.call_args.args
        assert args[0] == "prompt_content"
        assert args[1] == "uuid-1"

    def test_label_cache_api_removed(self):
        """标签第二指针机制已下线：label 缓存方法与 LABEL_TTL 不应存在"""
        from src.prompts.prompt_resolver import PromptCache

        assert not hasattr(PromptCache, "get_label_version")
        assert not hasattr(PromptCache, "set_label_version")
        assert not hasattr(PromptCache, "invalidate_label")
        assert not hasattr(PromptCache, "LABEL_TTL")
