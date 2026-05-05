"""
模板渲染器 — 支持 {variable} 占位符插值

转义规则（与 Python str.format 一致）：
- {{ → 字面 {
- }} → 字面 }
- {variable} → 替换为变量值
- {{{variable}}} → {变量值}（用于工具指南外层花括号）
"""

from typing import Dict


class _SafeDict(dict):
    """str.format_map 使用的安全字典：未知 key 返回原始占位符而非抛异常。"""

    def __missing__(self, key):
        return f"{{{key}}}"


def render_template(template: str, variables: Dict[str, str]) -> str:
    """
    渲染模板，将 {variable} 替换为变量值。

    使用 Python 内置的 str.format_map 实现插值，
    天然支持 {{ / }} 转义和 {{{var}}} 三重花括号。

    Args:
        template: 含 {variable} 占位符的模板字符串
        variables: 变量名 → 值的映射

    Returns:
        渲染后的字符串。未知变量保留原样。
    """
    return template.format_map(_SafeDict(variables))
