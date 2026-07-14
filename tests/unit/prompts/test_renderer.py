"""
render_sections / render_template 单元测试

覆盖 Phase 4.0 引入的双花括号分段变量渲染器：
- 正常 {{var}} 替换
- 未注册变量原样保留
- 字面花括号、JSON、代码块原样保留
- ${VAR} 环境变量占位不被误伤
- 与 render_template（系统模板 {var} 单括号）的边界分离

参考：docs/infrastructure/prompt-lifecycle-design.md §4.1 / §4.1.1 / §八.1.8
"""

import pytest

from src.prompts.renderer import render_sections, render_template


# ============================================================
# render_sections — DB 分段变量渲染（{{var}} 双花括号）
# ============================================================

class TestRenderSectionsBasic:
    """正常路径：变量替换、多变量、空格宽容"""

    def test_replaces_single_variable(self):
        """单个 {{var}} 被替换为变量值"""
        result = render_sections("你好，{{name}}！", {"name": "小明"})
        assert result == "你好，小明！"

    def test_replaces_multiple_variables(self):
        """多个不同变量都被替换"""
        result = render_sections("{{a}} 和 {{b}}", {"a": "1", "b": "2"})
        assert result == "1 和 2"

    def test_replaces_repeated_variable(self):
        """同一变量多次出现都被替换"""
        result = render_sections("{{x}}+{{x}}={{x}}", {"x": "1"})
        assert result == "1+1=1"

    def test_allows_whitespace_around_variable_name(self):
        """{{ var }} 变量名两侧允许空白"""
        result = render_sections("{{ name }}", {"name": "小明"})
        assert result == "小明"

    def test_empty_template_returns_empty(self):
        """空模板返回空字符串"""
        assert render_sections("", {"a": "1"}) == ""

    def test_template_without_variables_returns_unchanged(self):
        """无 {{var}} 的模板原样返回"""
        template = "这是一个普通 prompt，没有任何变量。"
        assert render_sections(template, {"a": "1"}) == template


class TestRenderSectionsUnknownVariable:
    """未注册变量原样保留，不抛异常"""

    def test_unknown_variable_preserved_verbatim(self):
        """未提供的 {{unknown}} 保留为 {{unknown}}"""
        result = render_sections("你好，{{unknown}}", {})
        assert result == "你好，{{unknown}}"

    def test_unknown_variable_preserved_when_others_provided(self):
        """提供部分变量时，未提供的保留原样"""
        result = render_sections("{{a}} {{b}} {{c}}", {"a": "1", "c": "3"})
        assert result == "1 {{b}} 3"

    def test_empty_variables_dict_returns_template_unchanged(self):
        """variables 为空 dict 时，原样返回模板（快速路径）"""
        template = "含 {{var}} 的模板"
        assert render_sections(template, {}) == template


class TestRenderSectionsLiteralBraces:
    """字面花括号（JSON / 代码块 / 正则 / 字面）原样保留"""

    def test_literal_single_braces_preserved(self):
        """字面单花括号原样保留，不被解析"""
        template = '配置：{"name": "John"}'
        assert render_sections(template, {}) == template

    def test_json_block_preserved(self):
        """JSON 代码块原样保留，变量仍能替换"""
        template = '示例：```json\n{"key": "value"}\n```\n变量：{{var}}'
        result = render_sections(template, {"var": "X"})
        assert result == '示例：```json\n{"key": "value"}\n```\n变量：X'

    def test_python_code_block_preserved(self):
        """Python 代码块（含字典字面量）原样保留"""
        template = "代码：d = {'a': 1, 'b': {2, 3}}"
        assert render_sections(template, {}) == template

    def test_regex_pattern_preserved(self):
        """正则模式（含花括号量词）原样保留"""
        template = r"正则：\d{3}-\d{4}"
        assert render_sections(template, {}) == template

    def test_curl_command_preserved(self):
        """curl 命令（含 JSON body）原样保留"""
        template = '''curl -X POST -d '{"key": "value"}' http://example.com'''
        assert render_sections(template, {}) == template

    def test_unbalanced_braces_preserved(self):
        """不成对的花括号原样保留"""
        template = "这是单个 { 没有成对"
        assert render_sections(template, {}) == template

    def test_system_template_style_single_braces_not_parsed(self):
        """系统模板风格的 {var} 单括号不应被 render_sections 解析"""
        # render_sections 专管 {{var}}，{var} 应当字面保留
        template = "可用工具列表：{available_tools_list}"
        assert render_sections(template, {"available_tools_list": "X"}) == template


class TestRenderSectionsEnvVarPlaceholder:
    """${VAR} 环境变量占位不被误伤"""

    def test_env_var_placeholder_preserved(self):
        """${VAR} 形式的环境变量占位原样保留"""
        template = "数据库：${DATABASE_URL}"
        assert render_sections(template, {}) == template

    def test_env_var_with_default_preserved(self):
        """${VAR:-default} 形式的环境变量占位原样保留"""
        template = "超时：${TIMEOUT:-30}s"
        assert render_sections(template, {}) == template

    def test_env_var_does_not_clash_with_section_var(self):
        """${VAR} 和 {{var}} 同模板出现，各自处理"""
        template = "${DB_URL} 和 {{name}}"
        result = render_sections(template, {"name": "X"})
        assert result == "${DB_URL} 和 X"


class TestRenderSectionsEdgeCases:
    """边界用例：相邻变量、空值、特殊字符"""

    def test_adjacent_variables(self):
        """相邻的 {{a}}{{b}} 都能替换"""
        result = render_sections("{{a}}{{b}}", {"a": "X", "b": "Y"})
        assert result == "XY"

    def test_empty_string_value(self):
        """变量值为空字符串时正常替换为空"""
        result = render_sections("[{{x}}]", {"x": ""})
        assert result == "[]"

    def test_variable_value_containing_braces(self):
        """变量值本身含花括号时，原样插入（不二次解析）"""
        result = render_sections("{{x}}", {"x": '{"nested": true}'})
        assert result == '{"nested": true}'

    def test_underscore_and_digits_in_name(self):
        """变量名含下划线和数字"""
        result = render_sections("{{section_key_1}}", {"section_key_1": "v"})
        assert result == "v"

    def test_variable_starting_with_digit_not_matched(self):
        """变量名不能以数字开头：{{1abc}} 不被识别为变量"""
        template = "{{1abc}}"
        assert render_sections(template, {"1abc": "X"}) == template


# ============================================================
# render_template — 系统模板渲染（{var} 单花括号，str.format_map）
# ============================================================

class TestRenderTemplateSystemTemplate:
    """系统模板 render_template 保持 str.format_map 行为不变（Phase 4.0 未改动此函数）"""

    def test_replaces_single_brace_variable(self):
        """单个 {var} 替换"""
        assert render_template("{x}", {"x": "1"}) == "1"

    def test_escape_double_braces_to_literal(self):
        """{{ 转义为字面 {"""
        assert render_template("{{literal}}", {}) == "{literal}"

    def test_safe_dict_preserves_unknown_variable(self):
        """未知 {var} 通过 _SafeDict 原样保留"""
        assert render_template("{unknown}", {}) == "{unknown}"

    def test_triple_braces_for_value_in_braces(self):
        """{{{var}}} 渲染为 {值}（用于工具指南外层花括号）"""
        assert render_template("{{{x}}}", {"x": "1"}) == "{1}"


# ============================================================
# get_section_keys — 从模板解析变量名
# ============================================================

class TestGetSectionKeysRegex:
    """SubagentDefinitionService.get_section_keys 内部正则测试

    这里直接测试正则模式本身，避免依赖 DB。
    正则模式：r'\\{\\{\\s*([a-zA-Z_][a-zA-Z0-9_]*)\\s*\\}\\}'
    """

    @staticmethod
    def _extract(template: str):
        import re
        return re.findall(r'\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}', template)

    def test_extracts_double_brace_variables(self):
        """从 {{a}} {{b}} 解析出 ['a', 'b']"""
        assert self._extract("{{a}} {{b}}") == ["a", "b"]

    def test_does_not_match_single_brace(self):
        """单括号 {legacy} 不被识别（向后兼容性：Phase 4.0 后单括号视为字面）"""
        assert self._extract("{legacy}") == []

    def test_does_not_match_env_var(self):
        """${VAR} 不被识别"""
        assert self._extract("${DATABASE_URL}") == []

    def test_does_not_match_json_keys(self):
        """JSON 字面 {"key": ...} 不被识别"""
        assert self._extract('{"key": "value"}') == []

    def test_preserves_order_and_dedup(self):
        """多次出现的变量按首次出现顺序去重"""
        template = "{{b}} {{a}} {{b}} {{a}} {{c}}"
        # findall 不去重，但实际 get_section_keys 会去重保序
        raw = self._extract(template)
        assert raw == ["b", "a", "b", "a", "c"]
        # 模拟 get_section_keys 的去重逻辑
        seen = set()
        result = []
        for k in raw:
            if k not in seen:
                seen.add(k)
                result.append(k)
        assert result == ["b", "a", "c"]

    def test_allows_whitespace_around_name(self):
        """{{ var }} 带空格的变量名也能解析"""
        assert self._extract("{{ x }}") == ["x"]
