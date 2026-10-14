"""http_api ${client_token} 会话级托管解析单元测试

验证自动填表场景的凭证托管链路：
- 无租户/进程环境变量时，${client_token} 从会话级缓存解析（delegate_login 写入）
- 有工具上下文（tenant_id + session_id）才能读会话缓存；无上下文时占位符残留
- 非 client_token 变量不受会话缓存影响
- 缓存 miss / 异常时占位符残留（走既有 unresolved fail-fast）
"""
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.tools


def _make_ctx(tenant_id="tenant_001", session_id="sess_001",
              subagent_id="auto-form-filler"):
    ctx = MagicMock()
    ctx.tenant_id = tenant_id
    ctx.session_id = session_id
    ctx.subagent_id = subagent_id
    ctx.env_vars = {}
    return ctx


class TestSessionHeldClientToken:
    def test_resolved_from_session_cache(self):
        from src.tools.network.http_api import _get_session_held_client_token

        ctx = _make_ctx()
        with patch("src.tools.context.current_tool_execution_context",
                   return_value=ctx), \
             patch("src.core.cache_utils.get_cached",
                   return_value={"client_token": "tok_held_123"}):
            assert _get_session_held_client_token() == "tok_held_123"

    def test_none_without_tool_context(self):
        from src.tools.network.http_api import _get_session_held_client_token

        with patch("src.tools.context.current_tool_execution_context",
                   return_value=None):
            assert _get_session_held_client_token() is None

    def test_none_without_session_id(self):
        from src.tools.network.http_api import _get_session_held_client_token

        ctx = _make_ctx(session_id="")
        with patch("src.tools.context.current_tool_execution_context",
                   return_value=ctx):
            assert _get_session_held_client_token() is None

    def test_none_on_cache_miss(self):
        from src.tools.network.http_api import _get_session_held_client_token

        ctx = _make_ctx()
        with patch("src.tools.context.current_tool_execution_context",
                   return_value=ctx), \
             patch("src.core.cache_utils.get_cached", return_value=None):
            assert _get_session_held_client_token() is None


class TestSubstituteClientToken:
    def test_placeholder_resolved_from_session_cache(self):
        from src.tools.network.http_api import _substitute_env_vars

        ctx = _make_ctx()
        with patch("src.tools.context.current_tool_execution_context",
                   return_value=ctx), \
             patch("src.core.cache_utils.get_cached",
                   return_value={"client_token": "tok_held_123"}), \
             patch.dict("os.environ", {}, clear=False):
            result = _substitute_env_vars("Client-Authorize-Token: ${client_token}")

        assert result == "Client-Authorize-Token: tok_held_123"

    def test_placeholder_kept_without_context(self):
        """无工具上下文（后台调试）时占位符残留，走既有 unresolved fail-fast"""
        from src.tools.network.http_api import _substitute_env_vars

        with patch("src.tools.context.current_tool_execution_context",
                   return_value=None), \
             patch.dict("os.environ", {}, clear=False):
            result = _substitute_env_vars("Bearer ${client_token}")

        assert result == "Bearer ${client_token}"

    def test_tenant_env_takes_precedence_over_session_cache(self):
        """租户环境变量若已定义 client_token，优先生效（不走会话缓存）"""
        from src.tools.network.http_api import _substitute_env_vars

        ctx = _make_ctx()
        ctx.env_vars = {"client_token": "tok_from_tenant_env"}
        get_cached_mock = MagicMock()
        with patch("src.tools.context.current_tool_execution_context",
                   return_value=ctx), \
             patch("src.core.cache_utils.get_cached", get_cached_mock):
            result = _substitute_env_vars("Bearer ${client_token}")

        assert result == "Bearer tok_from_tenant_env"
        get_cached_mock.assert_not_called()

    def test_other_vars_not_affected(self):
        """非 client_token 变量不读会话缓存（避免误解析）"""
        from src.tools.network.http_api import _substitute_env_vars

        ctx = _make_ctx()
        with patch("src.tools.context.current_tool_execution_context",
                   return_value=ctx), \
             patch("src.core.cache_utils.get_cached") as get_cached_mock, \
             patch.dict("os.environ", {}, clear=False):
            result = _substitute_env_vars("Bearer ${SOME_OTHER_VAR}")

        assert result == "Bearer ${SOME_OTHER_VAR}"
        get_cached_mock.assert_not_called()

    def test_cache_error_degrades_to_placeholder(self):
        """会话缓存读异常时降级：占位符残留，不抛异常"""
        from src.tools.network.http_api import _substitute_env_vars

        ctx = _make_ctx()
        with patch("src.tools.context.current_tool_execution_context",
                   return_value=ctx), \
             patch("src.core.cache_utils.get_cached",
                   side_effect=Exception("redis down")), \
             patch.dict("os.environ", {}, clear=False):
            result = _substitute_env_vars("Bearer ${client_token}")

        assert result == "Bearer ${client_token}"
