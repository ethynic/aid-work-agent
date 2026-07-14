"""
模板渲染器

提供两套渲染路径，服务不同场景：

1. **系统模板渲染**（`render_template`）
   - 用于 `src/prompts/templates/*.md`（master_agent.md / subagent_base.md 等）
   - 语法：`{variable}` 单花括号，依赖 Python `str.format_map`
   - 转义规则（与 Python str.format 一致）：
     - `{{` → 字面 `{`
     - `}}` → 字面 `}`
     - `{variable}` → 替换为变量值
     - `{{{variable}}}` → `{变量值}`（用于工具指南外层花括号）
   - 未知变量通过 `_SafeDict` 返回原始占位符而非抛异常
   - 限制：模板中含字面 `{` `}`（JSON 示例、代码块）需手工转义为 `{{` `}}`

2. **DB 分段变量渲染**（`render_sections`）
   - 用于 DB 子智能体的分段变量模板（`subagent_prompt_sections` 配套的 `prompt_versions.content`）
   - 语法：`{{variable}}` 双花括号，纯正则替换（不走 `str.format_map`）
   - 渲染规则：
     - `{{var}}` → 替换为变量值
     - `{{ var }}` → 允许变量名两侧空白
     - 未注册的 `{{xxx}}` → 原样保留
     - 字面 `{` `}` → 原样保留（不解析）
     - `${VAR}` → 原样保留（环境变量占位）
   - 优势：DB 模板常含 JSON 示例 / 代码块 / 正则 / curl 命令，双花括号渲染不会误解析字面花括号
"""

import re
from typing import Dict


class _SafeDict(dict):
    """str.format_map 使用的安全字典：未知 key 返回原始占位符而非抛异常。"""

    def __missing__(self, key):
        return f"{{{key}}}"


def render_template(template: str, variables: Dict[str, str]) -> str:
    """
    渲染系统模板，将 `{variable}` 替换为变量值。

    使用 Python 内置的 str.format_map 实现插值，
    天然支持 {{ / }} 转义和 {{{var}}} 三重花括号。

    Args:
        template: 含 `{variable}` 占位符的模板字符串
        variables: 变量名 → 值的映射

    Returns:
        渲染后的字符串。未知变量保留原样。
    """
    return template.format_map(_SafeDict(variables))


# 匹配 {{var}} 或 {{ var }} 形式的分段变量占位符
# 变量名规则：字母/下划线开头，后接字母/数字/下划线
_SECTION_VAR_PATTERN = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def render_sections(template: str, variables: Dict[str, str]) -> str:
    """
    渲染 DB 分段变量模板，将 `{{variable}}` 替换为变量值。

    与 `render_template` 的区别：
    - 只识别双花括号 `{{var}}`，不识别单花括号 `{var}`
    - 字面 `{` `}`（JSON / 代码块 / 正则）原样保留，不会被误解析
    - 未在 `variables` 中的 `{{xxx}}` 原样保留，不抛异常
    - 不依赖 `str.format_map`，因此不会因格式错误抛 KeyError/ValueError

    Args:
        template: 含 `{{variable}}` 占位符的模板字符串
        variables: 变量名 → 值的映射

    Returns:
        渲染后的字符串。未知变量保留原样。
    """
    if not variables:
        return template

    def _replace(match: re.Match) -> str:
        key = match.group(1)
        return variables.get(key, match.group(0))

    return _SECTION_VAR_PATTERN.sub(_replace, template)
