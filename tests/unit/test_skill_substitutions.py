"""
SkillSubstitutor 测试

覆盖变量替换、索引参数替换、<SKILL_ROOT> 替换
"""

import pytest

pytestmark = pytest.mark.skills

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "skill_substitutions",
    str(Path(__file__).parent.parent.parent / "src" / "core" / "skill_substitutions.py"),
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
SkillSubstitutor = _mod.SkillSubstitutor


class TestSkillRootSubstitution:
    """<SKILL_ROOT> 替换"""

    def test_basic_replacement(self):
        result = SkillSubstitutor.substitute(
            "<SKILL_ROOT>/assets/template.html",
            {"skill_dir": "/skills/ppt-skill", "arguments": ""},
        )
        assert result == "/skills/ppt-skill/assets/template.html"

    def test_multiple_occurrences(self):
        result = SkillSubstitutor.substitute(
            "cp <SKILL_ROOT>/a.html <SKILL_ROOT>/b.html",
            {"skill_dir": "/opt/skills/demo", "arguments": ""},
        )
        assert result == "cp /opt/skills/demo/a.html /opt/skills/demo/b.html"

    def test_no_skill_dir_no_replacement(self):
        """没有 skill_dir 时不替换"""
        result = SkillSubstitutor.substitute(
            "<SKILL_ROOT>/assets/template.html",
            {"arguments": ""},
        )
        assert "<SKILL_ROOT>" in result

    def test_empty_skill_dir_no_replacement(self):
        result = SkillSubstitutor.substitute(
            "<SKILL_ROOT>/assets/template.html",
            {"skill_dir": "", "arguments": ""},
        )
        assert "<SKILL_ROOT>" in result

    def test_no_skill_root_in_body(self):
        """body 中没有 <SKILL_ROOT> 时不受影响"""
        body = "Use file_read to read the template"
        result = SkillSubstitutor.substitute(
            body, {"skill_dir": "/skills/ppt", "arguments": ""}
        )
        assert result == body


class TestIndexedArguments:
    """$ARGUMENTS[N] 和 $N 替换"""

    def test_indexed_arg_replacement(self):
        result = SkillSubstitutor.substitute(
            "Hello $ARGUMENTS[0], your topic is $ARGUMENTS[1]",
            {"arguments": "Alice WebDesign"},
        )
        assert result == "Hello Alice, your topic is WebDesign"

    def test_short_form_indexed(self):
        result = SkillSubstitutor.substitute(
            "First: $0, Second: $1",
            {"arguments": "alpha beta"},
        )
        assert result == "First: alpha, Second: beta"

    def test_index_out_of_range(self):
        """索引越界时替换为空字符串"""
        result = SkillSubstitutor.substitute(
            "Only: $ARGUMENTS[5]",
            {"arguments": "one two"},
        )
        assert result == "Only: "


class TestFullArguments:
    """$ARGUMENTS 完整替换"""

    def test_full_arguments_replacement(self):
        result = SkillSubstitutor.substitute(
            "Args: $ARGUMENTS",
            {"arguments": "hello world 123"},
        )
        assert result == "Args: hello world 123"


class TestVarSubstitution:
    """${VAR_NAME} 替换"""

    def test_named_var_replacement(self):
        result = SkillSubstitutor.substitute(
            "Language: ${language}",
            {"arguments": "", "language": "Chinese"},
        )
        assert result == "Language: Chinese"

    def test_missing_var_kept_as_is(self):
        """未定义的变量保留原样"""
        result = SkillSubstitutor.substitute(
            "Value: ${unknown_var}",
            {"arguments": ""},
        )
        assert "${unknown_var}" in result


class TestSubstitutionOrder:
    """替换执行顺序：SKILL_ROOT → indexed args → full args → named vars"""

    def test_all_substitutions_together(self):
        result = SkillSubstitutor.substitute(
            "Dir: <SKILL_ROOT>, First: $1, All: $ARGUMENTS, Lang: ${lang}",
            {"skill_dir": "/skills/test", "arguments": "hello world", "lang": "zh"},
        )
        assert "/skills/test" in result
        assert "hello" in result
        assert "hello world" in result
        assert "zh" in result


class TestTenantEnvVarFallback:
    """skill 命令体 ${VAR} 从请求级租户环境变量兜底（os.environ 注入已废弃）"""

    def test_tenant_env_var_used(self):
        from src.tools.context import ToolExecutionContext, tool_execution_scope

        with tool_execution_scope(
            ToolExecutionContext(tenant_id="t1", env_vars={"API_KEY_XYZ": "tenant-key"})
        ):
            result = SkillSubstitutor.substitute(
                "curl -H 'X-Key: ${API_KEY_XYZ}'", {"arguments": ""},
            )
        assert result == "curl -H 'X-Key: tenant-key'"

    def test_tenant_env_var_preferred_over_process_env(self, monkeypatch):
        from src.tools.context import ToolExecutionContext, tool_execution_scope

        monkeypatch.setenv("API_KEY_XYZ", "process-key")
        with tool_execution_scope(
            ToolExecutionContext(tenant_id="t1", env_vars={"API_KEY_XYZ": "tenant-key"})
        ):
            result = SkillSubstitutor.substitute("K=${API_KEY_XYZ}", {"arguments": ""})
        assert result == "K=tenant-key"

    def test_missing_var_kept_as_is(self):
        from src.tools.context import ToolExecutionContext, tool_execution_scope

        with tool_execution_scope(
            ToolExecutionContext(tenant_id="t1", env_vars={})
        ):
            result = SkillSubstitutor.substitute("K=${MISSING_VAR_QQ}", {"arguments": ""})
        assert "${MISSING_VAR_QQ}" in result
