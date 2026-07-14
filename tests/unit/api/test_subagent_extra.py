"""
extra_md API 单元测试（Phase 4 起 DB 驱动）

验证：
- GET /api/v1/subagents/{name}/extra：无注册记录 → {content: None}；有记录 → 返回 production 内容
- PUT /api/v1/subagents/{name}/extra：自动 register + commit_version + set_label('production')
- DELETE /api/v1/subagents/{name}/extra：无记录幂等；有记录级联删除
- tenant_extra 不走 staging，直接标 production
- 无租户上下文 → GET 返回默认配置；PUT/DELETE 报 400
- API 签名保持不变（路径 /api/v1/subagents/{name}/extra）

参考：docs/infrastructure/prompt-lifecycle-design.md §八.2
"""

import pytest
from unittest.mock import patch, MagicMock
from fastapi import HTTPException

import src.api.subagent_extra as extra_module


# ============================================================
# 工具：构造 mock request / user
# ============================================================

def _make_request(tenant_id: str = "tenant_001", user_id: str = "user_001"):
    """构造一个带 tenant_id state 的 mock Request"""
    request = MagicMock()
    request.state.tenant_id = tenant_id
    return request


# ============================================================
# GET /extra
# ============================================================

class TestGetExtraMd:
    """GET /api/v1/subagents/{name}/extra"""

    @pytest.mark.asyncio
    async def test_no_tenant_context_returns_default(self):
        """无租户上下文 → 返回默认配置（content=None，不报错）"""
        request = MagicMock()
        request.state.tenant_id = None
        # get_current_user 也返回 None
        with patch.object(extra_module, "get_current_user", return_value=None):
            result = await extra_module.get_extra_md("any-agent", request)
        assert result["success"] is True
        assert result["content"] is None

    @pytest.mark.asyncio
    async def test_no_registry_returns_default(self):
        """DB 无注册记录 → 返回默认配置"""
        request = _make_request()
        with patch.object(extra_module, "get_current_user") as mock_user, \
             patch.object(extra_module.PromptRegistryService, "get_prompt_by_scope", return_value=None):
            mock_user.return_value = {"tenant_id": "tenant_001", "user_id": "user_001"}
            result = await extra_module.get_extra_md("travel-consultant", request)
        assert result["success"] is True
        assert result["content"] is None
        assert "未配置" in result["message"]

    @pytest.mark.asyncio
    async def test_registry_but_no_production_returns_default(self):
        """有注册记录但 production 内容为空 → 返回默认配置"""
        request = _make_request()
        registry = {"id": "p1", "scope": "tenant_extra"}
        with patch.object(extra_module, "get_current_user") as mock_user, \
             patch.object(extra_module.PromptRegistryService, "get_prompt_by_scope", return_value=registry), \
             patch("src.prompts.prompt_resolver.prompt_resolver") as mock_resolver:
            mock_user.return_value = {"tenant_id": "tenant_001"}
            mock_resolver.resolve.return_value = None
            result = await extra_module.get_extra_md("travel-consultant", request)
        assert result["content"] is None

    @pytest.mark.asyncio
    async def test_returns_production_content(self):
        """有 production 内容 → 返回内容"""
        request = _make_request()
        registry = {"id": "p1", "scope": "tenant_extra"}
        with patch.object(extra_module, "get_current_user") as mock_user, \
             patch.object(extra_module.PromptRegistryService, "get_prompt_by_scope", return_value=registry), \
             patch("src.prompts.prompt_resolver.prompt_resolver") as mock_resolver:
            mock_user.return_value = {"tenant_id": "tenant_001"}
            mock_resolver.resolve.return_value = "租户定制内容"
            result = await extra_module.get_extra_md("travel-consultant", request)
        assert result["content"] == "租户定制内容"

    @pytest.mark.asyncio
    async def test_content_is_stripped(self):
        """返回内容去除首尾空白"""
        request = _make_request()
        registry = {"id": "p1"}
        with patch.object(extra_module, "get_current_user") as mock_user, \
             patch.object(extra_module.PromptRegistryService, "get_prompt_by_scope", return_value=registry), \
             patch("src.prompts.prompt_resolver.prompt_resolver") as mock_resolver:
            mock_user.return_value = {"tenant_id": "tenant_001"}
            mock_resolver.resolve.return_value = "\n\n定制内容\n\n"
            result = await extra_module.get_extra_md("travel-consultant", request)
        assert result["content"] == "定制内容"

    @pytest.mark.asyncio
    async def test_get_calls_resolver_with_correct_args(self):
        """resolver 被调用时必须用 scope='tenant_extra' + 正确 scope_id + tenant_id

        这是租户隔离的关键契约：scope_id 内嵌 tenant_id，
        resolver 内部读 production label 时只会读到本租户的内容。
        """
        request = _make_request(tenant_id="tenant_XYZ")
        registry = {"id": "p1"}
        with patch.object(extra_module, "get_current_user") as mock_user, \
             patch.object(extra_module.PromptRegistryService, "get_prompt_by_scope", return_value=registry), \
             patch("src.prompts.prompt_resolver.prompt_resolver") as mock_resolver:
            mock_user.return_value = {"tenant_id": "tenant_XYZ"}
            mock_resolver.resolve.return_value = "内容"
            await extra_module.get_extra_md("my-agent", request)
        call_kwargs = mock_resolver.resolve.call_args.kwargs
        assert call_kwargs["scope"] == "tenant_extra"
        assert call_kwargs["scope_id"] == "extra:my-agent:tenant_XYZ"
        assert call_kwargs["tenant_id"] == "tenant_XYZ"


# ============================================================
# PUT /extra
# ============================================================

class TestSaveExtraMd:
    """PUT /api/v1/subagents/{name}/extra"""

    @pytest.mark.asyncio
    async def test_no_tenant_context_returns_400(self):
        """无租户上下文 → HTTPException 400"""
        request = MagicMock()
        request.state.tenant_id = None
        body = extra_module.ExtraMdRequest(content="内容")
        with patch.object(extra_module, "get_current_user", return_value=None):
            with pytest.raises(HTTPException) as exc:
                await extra_module.save_extra_md("any-agent", body, request)
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_save_creates_and_commits_and_labels(self):
        """保存时自动 register → commit_version → set_label('production')"""
        request = _make_request()
        body = extra_module.ExtraMdRequest(content="新内容")
        registry = {"id": "prompt-uuid-1"}
        version_record = {"version": 1}

        with patch.object(extra_module, "get_current_user") as mock_user, \
             patch.object(extra_module.PromptRegistryService, "get_prompt_by_scope", return_value=None), \
             patch.object(extra_module.PromptRegistryService, "register_prompt", return_value=registry) as mock_register, \
             patch.object(extra_module.PromptRegistryService, "commit_version",
                          return_value={"version": version_record, "dedup": False}) as mock_commit, \
             patch.object(extra_module.PromptRegistryService, "set_label", return_value={"id": "label1"}) as mock_label:
            mock_user.return_value = {"tenant_id": "tenant_001", "user_id": "user_001"}
            result = await extra_module.save_extra_md("travel-consultant", body, request)

        assert result["success"] is True
        assert result["version"] == 1
        # 验证调用链
        mock_register.assert_called_once()
        mock_commit.assert_called_once()
        mock_label.assert_called_once()
        # set_label 标的是 production
        assert mock_label.call_args.kwargs["label"] == "production"
        assert mock_label.call_args.kwargs["version"] == 1

    @pytest.mark.asyncio
    async def test_save_existing_prompt_skips_register(self):
        """已有注册记录时不再 register，直接 commit + label"""
        request = _make_request()
        body = extra_module.ExtraMdRequest(content="更新内容")
        registry = {"id": "prompt-uuid-existing"}
        version_record = {"version": 2}

        with patch.object(extra_module, "get_current_user") as mock_user, \
             patch.object(extra_module.PromptRegistryService, "get_prompt_by_scope", return_value=registry), \
             patch.object(extra_module.PromptRegistryService, "register_prompt") as mock_register, \
             patch.object(extra_module.PromptRegistryService, "commit_version",
                          return_value={"version": version_record, "dedup": False}), \
             patch.object(extra_module.PromptRegistryService, "set_label"):
            mock_user.return_value = {"tenant_id": "tenant_001", "user_id": "user_001"}
            result = await extra_module.save_extra_md("travel-consultant", body, request)

        # register 不应被调用（已存在）
        mock_register.assert_not_called()
        assert result["version"] == 2

    @pytest.mark.asyncio
    async def test_save_uses_correct_scope_and_scope_id(self):
        """scope='tenant_extra', scope_id='extra:{name}:{tenant}'"""
        request = _make_request(tenant_id="tenant_X")
        body = extra_module.ExtraMdRequest(content="x")
        with patch.object(extra_module, "get_current_user") as mock_user, \
             patch.object(extra_module.PromptRegistryService, "get_prompt_by_scope", return_value=None), \
             patch.object(extra_module.PromptRegistryService, "register_prompt", return_value={"id": "p1"}) as mock_register, \
             patch.object(extra_module.PromptRegistryService, "commit_version",
                          return_value={"version": {"version": 1}, "dedup": False}), \
             patch.object(extra_module.PromptRegistryService, "set_label"):
            mock_user.return_value = {"tenant_id": "tenant_X"}
            await extra_module.save_extra_md("my-agent", body, request)
        # register_prompt 的参数
        kwargs = mock_register.call_args.kwargs
        assert kwargs["scope"] == "tenant_extra"
        assert kwargs["scope_id"] == "extra:my-agent:tenant_X"
        assert kwargs["tenant_id"] == "tenant_X"

    @pytest.mark.asyncio
    async def test_save_returns_500_when_commit_version_fails(self):
        """commit_version 返回 version=None（DB 失败）→ HTTPException 500，不调 set_label

        P0 边界：避免 AttributeError 并避免给坏数据打标签。
        """
        request = _make_request()
        body = extra_module.ExtraMdRequest(content="x")
        registry = {"id": "prompt-uuid-1"}
        with patch.object(extra_module, "get_current_user") as mock_user, \
             patch.object(extra_module.PromptRegistryService, "get_prompt_by_scope", return_value=registry), \
             patch.object(extra_module.PromptRegistryService, "commit_version",
                          return_value={"version": None, "dedup": False}) as mock_commit, \
             patch.object(extra_module.PromptRegistryService, "set_label") as mock_label:
            mock_user.return_value = {"tenant_id": "tenant_001", "user_id": "user_001"}
            with pytest.raises(HTTPException) as exc:
                await extra_module.save_extra_md("travel-consultant", body, request)
        assert exc.value.status_code == 500
        mock_commit.assert_called_once()
        # version 都没拿到，绝不能给坏数据打 production 标签
        mock_label.assert_not_called()

    @pytest.mark.asyncio
    async def test_save_dedup_skips_register_when_exists(self):
        """dedup=True（内容相同）→ 复用已有 latest version，不再 register"""
        request = _make_request()
        body = extra_module.ExtraMdRequest(content="same-content")
        registry = {"id": "prompt-uuid-existing"}
        with patch.object(extra_module, "get_current_user") as mock_user, \
             patch.object(extra_module.PromptRegistryService, "get_prompt_by_scope", return_value=registry), \
             patch.object(extra_module.PromptRegistryService, "register_prompt") as mock_register, \
             patch.object(extra_module.PromptRegistryService, "commit_version",
                          return_value={"version": {"version": 3}, "dedup": True}), \
             patch.object(extra_module.PromptRegistryService, "set_label") as mock_label:
            mock_user.return_value = {"tenant_id": "tenant_001", "user_id": "user_001"}
            result = await extra_module.save_extra_md("travel-consultant", body, request)
        assert result["version"] == 3
        mock_register.assert_not_called()
        # dedup 时仍需把 production 指到这个已有版本（保证标签存在）
        assert mock_label.call_args.kwargs["version"] == 3


# ============================================================
# DELETE /extra
# ============================================================

class TestDeleteExtraMd:
    """DELETE /api/v1/subagents/{name}/extra"""

    @pytest.mark.asyncio
    async def test_no_tenant_context_returns_400(self):
        """无租户上下文 → HTTPException 400"""
        request = MagicMock()
        request.state.tenant_id = None
        with patch.object(extra_module, "get_current_user", return_value=None):
            with pytest.raises(HTTPException) as exc:
                await extra_module.delete_extra_md("any-agent", request)
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_delete_nonexistent_is_idempotent(self):
        """无注册记录 → 幂等返回成功"""
        request = _make_request()
        with patch.object(extra_module, "get_current_user") as mock_user, \
             patch.object(extra_module.PromptRegistryService, "get_prompt_by_scope", return_value=None), \
             patch.object(extra_module.PromptRegistryService, "delete_prompt") as mock_delete:
            mock_user.return_value = {"tenant_id": "tenant_001"}
            result = await extra_module.delete_extra_md("travel-consultant", request)
        assert result["success"] is True
        # 无记录时不应调用 delete_prompt
        mock_delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_existing_cascades(self):
        """有记录 → 级联删除 prompt"""
        request = _make_request()
        registry = {"id": "prompt-uuid-to-delete"}
        with patch.object(extra_module, "get_current_user") as mock_user, \
             patch.object(extra_module.PromptRegistryService, "get_prompt_by_scope", return_value=registry), \
             patch.object(extra_module.PromptRegistryService, "delete_prompt", return_value=True) as mock_delete:
            mock_user.return_value = {"tenant_id": "tenant_001"}
            result = await extra_module.delete_extra_md("travel-consultant", request)
        assert result["success"] is True
        mock_delete.assert_called_once_with("prompt-uuid-to-delete")

    @pytest.mark.asyncio
    async def test_delete_returns_500_when_db_fails(self):
        """delete_prompt 返回 False → HTTPException 500"""
        request = _make_request()
        registry = {"id": "prompt-uuid"}
        with patch.object(extra_module, "get_current_user") as mock_user, \
             patch.object(extra_module.PromptRegistryService, "get_prompt_by_scope", return_value=registry), \
             patch.object(extra_module.PromptRegistryService, "delete_prompt", return_value=False):
            mock_user.return_value = {"tenant_id": "tenant_001"}
            with pytest.raises(HTTPException) as exc:
                await extra_module.delete_extra_md("travel-consultant", request)
        assert exc.value.status_code == 500


# ============================================================
# _load_extra_md（agent.py）行为契约测试
# ============================================================

class TestLoadExtraMdContract:
    """_load_extra_md 行为契约（不直接测 agent，测契约）

    验证 _load_extra_md 的核心契约：
    - 调用 prompt_resolver.resolve(scope='tenant_extra', scope_id=...)
    - miss 时返回 None（不抛异常）
    - 返回内容 strip 首尾空白
    """

    def test_resolver_call_contract(self):
        """prompt_resolver.resolve 必须用正确的 scope 和 scope_id

        此契约由 TestGetExtraMd.test_get_calls_resolver_with_correct_args 验证，
        这里保留方法签名以标记契约存在。
        """
        # 见 TestGetExtraMd.test_get_calls_resolver_with_correct_args
        assert hasattr(extra_module, "get_extra_md")

    def test_no_file_io_in_source(self):
        """源代码中不应再有 Path() 或 read_text() 文件 IO"""
        import inspect
        from src.core.agent import Agent
        source = inspect.getsource(Agent._load_extra_md)
        assert "Path(" not in source, "_load_extra_md 不应再使用 Path()"
        assert "read_text" not in source, "_load_extra_md 不应再使用 read_text()"
        assert "exists()" not in source, "_load_extra_md 不应再调用 exists()"
        assert "prompt_resolver.resolve" in source, "_load_extra_md 应调用 prompt_resolver.resolve"

    def test_no_file_io_in_api_source(self):
        """API 源代码中不应再有文件 IO"""
        import inspect
        source = inspect.getsource(extra_module)
        assert "EXTRA_BASE_DIR" not in source, "subagent_extra.py 不应再有 EXTRA_BASE_DIR"
        assert "_resolve_extra_path" not in source, "subagent_extra.py 不应再有 _resolve_extra_path"
        assert "read_text" not in source, "subagent_extra.py 不应再有 read_text"
        assert "write_text" not in source, "subagent_extra.py 不应再有 write_text"
        assert ".unlink()" not in source, "subagent_extra.py 不应再有 .unlink()"
