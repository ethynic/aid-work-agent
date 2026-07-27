"""
子智能体 LLM 配置覆盖单元测试

覆盖：
1. SUBAGENT.md frontmatter 解析 {provider}_model_code 平铺字段
2. serialize_to_subagent_md 回写包含 model_code 字段
3. DB JSONB <-> SubagentConfig 双向转换（含旧字符串格式兼容）
4. LLMGateway 接受 model_codes 并在 _build_provider 时覆盖
5. extract_llm_config / pack_llm_config 辅助函数
"""

import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from src.models.subagent import SubagentConfig, extract_llm_config, pack_llm_config


# ============== extract_llm_config / pack_llm_config ==============

class TestExtractPackLlmConfig:
    def test_extract_none(self):
        assert extract_llm_config(None) == (None, None)

    def test_extract_old_string_format(self):
        """旧字符串格式兼容：'deepseek' -> ('deepseek', None)"""
        assert extract_llm_config("deepseek") == ("deepseek", None)

    def test_extract_empty_string(self):
        assert extract_llm_config("") == (None, None)

    def test_extract_dict_full(self):
        raw = {"provider": "deepseek", "model_codes": {"deepseek": "v4-pro", "qwen": "qwen3.7-plus"}}
        provider, codes = extract_llm_config(raw)
        assert provider == "deepseek"
        assert codes == {"deepseek": "v4-pro", "qwen": "qwen3.7-plus"}

    def test_extract_dict_only_provider(self):
        raw = {"provider": "deepseek"}
        assert extract_llm_config(raw) == ("deepseek", None)

    def test_extract_dict_invalid_model_codes_type(self):
        """model_codes 非字典时返回 None"""
        raw = {"provider": "deepseek", "model_codes": "invalid"}
        assert extract_llm_config(raw) == ("deepseek", None)

    def test_extract_dict_filters_invalid_entries(self):
        """过滤 model_codes 中非字符串/空字符串值"""
        raw = {"provider": "deepseek", "model_codes": {"deepseek": "v4-pro", "qwen": "", "zhipu": None}}
        provider, codes = extract_llm_config(raw)
        assert codes == {"deepseek": "v4-pro"}

    def test_extract_unsupported_type(self):
        assert extract_llm_config(123) == (None, None)

    def test_pack_both_empty_returns_none(self):
        assert pack_llm_config(None, None) is None
        assert pack_llm_config("", None) is None
        assert pack_llm_config(None, {}) is None

    def test_pack_only_provider(self):
        result = pack_llm_config("deepseek", None)
        assert result == {"provider": "deepseek", "model_codes": {}}

    def test_pack_only_model_codes(self):
        result = pack_llm_config(None, {"deepseek": "v4-pro"})
        assert result == {"provider": None, "model_codes": {"deepseek": "v4-pro"}}

    def test_pack_full(self):
        result = pack_llm_config("deepseek", {"deepseek": "v4-pro", "qwen": "qwen3.7-plus"})
        assert result == {
            "provider": "deepseek",
            "model_codes": {"deepseek": "v4-pro", "qwen": "qwen3.7-plus"},
        }

    def test_roundtrip(self):
        """pack -> extract 往返一致"""
        original_provider = "deepseek"
        original_codes = {"deepseek": "v4-pro", "qwen": "qwen3.7-plus"}
        packed = pack_llm_config(original_provider, original_codes)
        provider, codes = extract_llm_config(packed)
        assert provider == original_provider
        assert codes == original_codes


# ============== SUBAGENT.md 解析与序列化 ==============

class TestSubagentMdParsing:
    def test_parse_multi_provider_model_codes(self, tmp_path):
        """frontmatter 中多个 {provider}_model_code 字段被解析为 llm_model_codes 字典"""
        content = """---
name: 测试智能体
llm_provider: deepseek
deepseek_model_code: deepseek-v4-pro
qwen_model_code: qwen3.7-plus
---

body
"""
        path = tmp_path / "SUBAGENT.md"
        path.write_text(content, encoding="utf-8")

        from src.subagents.loader import SubagentLoader
        loader = SubagentLoader()
        config = loader.parse_subagent_md(path)

        assert config is not None
        assert config.llm_provider == "deepseek"
        assert config.llm_model_codes == {"deepseek": "deepseek-v4-pro", "qwen": "qwen3.7-plus"}

    def test_parse_no_model_codes(self, tmp_path):
        """没有 {provider}_model_code 字段时 llm_model_codes 为 None"""
        content = """---
name: 测试智能体
llm_provider: deepseek
---

body
"""
        path = tmp_path / "SUBAGENT.md"
        path.write_text(content, encoding="utf-8")

        from src.subagents.loader import SubagentLoader
        loader = SubagentLoader()
        config = loader.parse_subagent_md(path)

        assert config is not None
        assert config.llm_provider == "deepseek"
        assert config.llm_model_codes is None

    def test_parse_empty_model_code_value_ignored(self, tmp_path):
        """空字符串的 {provider}_model_code 被忽略"""
        content = """---
name: 测试智能体
llm_provider: deepseek
deepseek_model_code: ''
qwen_model_code: qwen3.7-plus
---

body
"""
        path = tmp_path / "SUBAGENT.md"
        path.write_text(content, encoding="utf-8")

        from src.subagents.loader import SubagentLoader
        loader = SubagentLoader()
        config = loader.parse_subagent_md(path)

        assert config is not None
        assert config.llm_model_codes == {"qwen": "qwen3.7-plus"}

    def test_serialize_includes_model_codes(self):
        """serialize_to_subagent_md 把 llm_model_codes 写回为 {provider}_model_code 字段"""
        config = SubagentConfig(
            name="测试智能体",
            llm_provider="deepseek",
            llm_model_codes={"deepseek": "deepseek-v4-pro", "qwen": "qwen3.7-plus"},
        )
        from src.subagents.loader import SubagentLoader
        md = SubagentLoader.serialize_to_subagent_md(config, body="body content")

        assert "llm_provider: deepseek" in md
        assert "deepseek_model_code: deepseek-v4-pro" in md
        assert "qwen_model_code: qwen3.7-plus" in md

    def test_serialize_omits_when_no_model_codes(self):
        """没有 llm_model_codes 时不写 {provider}_model_code 字段"""
        config = SubagentConfig(
            name="测试智能体",
            llm_provider="deepseek",
        )
        from src.subagents.loader import SubagentLoader
        md = SubagentLoader.serialize_to_subagent_md(config, body="body content")

        assert "llm_provider: deepseek" in md
        assert "_model_code" not in md


# ============== LLMGateway model_codes 覆盖 ==============

class TestLLMGatewayModelCodes:
    def test_gateway_stores_model_codes(self):
        """LLMGateway 构造时存储 model_codes"""
        with patch("src.llm.gateway.settings") as mock_settings:
            mock_settings.llm.provider = "deepseek"
            mock_settings.llm.failover.enabled = False
            mock_settings.llm.deepseek.get_effective_keys.return_value = ["fake-key"]
            mock_settings.llm.deepseek.max_concurrent_per_key = 1
            mock_settings.llm.deepseek.queue_timeout = 10.0

            from src.llm.gateway import LLMGateway
            gateway = LLMGateway(
                provider_name="deepseek",
                model_codes={"deepseek": "deepseek-v4-pro", "qwen": "qwen3.7-plus"},
            )
            assert gateway._model_codes == {"deepseek": "deepseek-v4-pro", "qwen": "qwen3.7-plus"}

    def test_gateway_default_model_codes_empty(self):
        """LLMGateway 不传 model_codes 时为空字典"""
        with patch("src.llm.gateway.settings") as mock_settings:
            mock_settings.llm.provider = "deepseek"
            mock_settings.llm.failover.enabled = False
            mock_settings.llm.deepseek.get_effective_keys.return_value = ["fake-key"]
            mock_settings.llm.deepseek.max_concurrent_per_key = 1
            mock_settings.llm.deepseek.queue_timeout = 10.0

            from src.llm.gateway import LLMGateway
            gateway = LLMGateway(provider_name="deepseek")
            assert gateway._model_codes == {}

    def test_build_provider_uses_override_model(self):
        """_build_provider 传入 model 时使用覆盖值，不传时使用 settings 默认值"""
        with patch("src.llm.gateway.settings") as mock_settings:
            mock_settings.llm.deepseek.model = "default-model"
            mock_settings.llm.deepseek.base_url = "https://api.deepseek.com"

            from src.llm.gateway import _build_provider
            provider_with_override = _build_provider("deepseek", "fake-key", model="override-model")
            assert provider_with_override.model == "override-model"

            provider_default = _build_provider("deepseek", "fake-key")
            assert provider_default.model == "default-model"

    def test_get_model_name_uses_override(self):
        """get_model_name 优先返回 model_codes 中的覆盖值"""
        with patch("src.llm.gateway.settings") as mock_settings:
            mock_settings.llm.provider = "deepseek"
            mock_settings.llm.failover.enabled = False
            mock_settings.llm.deepseek.get_effective_keys.return_value = ["fake-key"]
            mock_settings.llm.deepseek.max_concurrent_per_key = 1
            mock_settings.llm.deepseek.queue_timeout = 10.0
            mock_settings.llm.deepseek.model = "default-model"

            from src.llm.gateway import LLMGateway
            # 有覆盖值时返回覆盖值
            gateway = LLMGateway(
                provider_name="deepseek",
                model_codes={"deepseek": "deepseek-v4-pro"},
            )
            assert gateway.get_model_name() == "deepseek-v4-pro"

            # 无覆盖值时返回全局默认
            gateway_default = LLMGateway(provider_name="deepseek")
            assert gateway_default.get_model_name() == "default-model"


# ============== API update 请求清空语义 ==============

class TestUpdateDefinitionRequestClearing:
    """验证 UpdateDefinitionRequest 使用 exclude_unset 时支持显式传 null 清空。

    场景：用户在前端清空 llm_provider/llm_model_codes 后保存，前端发送 null 值，
    后端必须能区分「未传入字段」和「显式传 null 清空」，否则无法清空已有 LLM 配置。
    """

    def test_explicit_null_is_preserved(self):
        """显式传 null 时，model_dump(exclude_unset=True) 保留 null 字段"""
        from src.api.agent_definitions import UpdateDefinitionRequest

        body = UpdateDefinitionRequest.model_validate({
            "name": "xxx",
            "llm_provider": None,
            "llm_model_codes": None,
        })
        # exclude_unset 保留显式传入的 null 字段
        result = body.model_dump(exclude_unset=True)
        assert "llm_provider" in result
        assert result["llm_provider"] is None
        assert "llm_model_codes" in result
        assert result["llm_model_codes"] is None

    def test_omitted_field_is_excluded(self):
        """未传入的字段被 exclude_unset 排除（PATCH 语义）"""
        from src.api.agent_definitions import UpdateDefinitionRequest

        body = UpdateDefinitionRequest.model_validate({"name": "xxx"})
        result = body.model_dump(exclude_unset=True)
        # 未传入的 llm_provider/llm_model_codes 不应出现在结果中
        assert "llm_provider" not in result
        assert "llm_model_codes" not in result
        assert result.get("name") == "xxx"

    def test_normal_update_preserves_values(self):
        """正常更新场景，exclude_unset 保留传入的非空值"""
        from src.api.agent_definitions import UpdateDefinitionRequest

        body = UpdateDefinitionRequest.model_validate({
            "name": "xxx",
            "llm_provider": "deepseek",
            "llm_model_codes": {"deepseek": "deepseek-v4-pro"},
        })
        result = body.model_dump(exclude_unset=True)
        assert result["llm_provider"] == "deepseek"
        assert result["llm_model_codes"] == {"deepseek": "deepseek-v4-pro"}
