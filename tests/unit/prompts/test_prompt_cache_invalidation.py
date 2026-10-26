"""删除数字员工时 PROMPT_REGISTRY 缓存失效的单元测试

背景（2026-10-26）：invalidate_prompt 只按 prompt_id 清 CONTENT/LABEL，
registry 缓存键按 scope_id（agent_id）组织且未被清除，导致删除后重建同
agent_id 的智能体在 TTL（300s）内 resolve 命中旧 prompt_id，拿不到
system_prompt 被 load_from_db 跳过，授权列表几分钟内看不到新智能体。
"""

import pytest
from unittest.mock import patch

pytestmark = pytest.mark.unit


class TestInvalidateRegistry:
    def test_deletes_registry_cache_key_with_sys_tenant(self):
        from src.prompts.prompt_resolver import PromptCache

        with patch("src.prompts.prompt_resolver.delete_cached") as mock_del:
            PromptCache().invalidate_registry(None, "subagent", "sales_x")

        mock_del.assert_called_once_with("prompt_reg", "_sys", "subagent", "sales_x")

    def test_deletes_registry_cache_key_with_tenant(self):
        from src.prompts.prompt_resolver import PromptCache

        with patch("src.prompts.prompt_resolver.delete_cached") as mock_del:
            PromptCache().invalidate_registry("t1", "subagent", "sales_x")

        mock_del.assert_called_once_with("prompt_reg", "t1", "subagent", "sales_x")


class TestDeleteDefinitionInvalidatesRegistry:
    def _delete(self, agent_id, prompt_found=True):
        from src.services import subagent_definition_service as svc

        with patch.object(svc.SubagentPromptSectionDB, "delete_sections") as m_sec, \
             patch.object(svc.PromptRegistryService, "get_prompt_by_scope") as m_get, \
             patch.object(svc.PromptRegistryService, "delete_prompt") as m_del_prompt, \
             patch.object(svc.SubagentDefinitionDB, "delete", return_value=True) as m_def, \
             patch("src.prompts.prompt_resolver.prompt_resolver") as m_resolver:
            m_get.return_value = {"id": "pid-1"} if prompt_found else None
            result = svc.SubagentDefinitionService.delete_definition(agent_id)

        assert result is True
        m_sec.assert_called_once_with(agent_id)
        m_def.assert_called_once_with(agent_id)
        if prompt_found:
            m_del_prompt.assert_called_once_with("pid-1")
        else:
            m_del_prompt.assert_not_called()
        return m_resolver

    def test_invalidates_registry_when_prompt_exists(self):
        m_resolver = self._delete("sales_x", prompt_found=True)
        m_resolver.cache.invalidate_registry.assert_called_once_with(
            None, "subagent", "sales_x"
        )

    def test_invalidates_registry_even_when_prompt_missing(self):
        # prompt 记录缺失（异常残留）时也要失效，防止更早的陈旧缓存存活
        m_resolver = self._delete("sales_x", prompt_found=False)
        m_resolver.cache.invalidate_registry.assert_called_once_with(
            None, "subagent", "sales_x"
        )
