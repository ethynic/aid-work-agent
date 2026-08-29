"""定时任务错误信息中的凭据脱敏。

在定时任务 API 与 DB 日志边界统一处理异常文本：保留错误类别和可操作上下文
（异常类型、任务/接口名、非敏感参数），只遮蔽凭据值，不记录凭据明文。
"""

import re
from typing import Optional


# 敏感键名 + 分隔符的前缀匹配：
# - 键名：单词键（password/passwd/secret/token）与复合键（api key、secret key 等），
#   复合键允许 `_`/`-`/空白重复连接（api__key、api - key、跨行 api\nkey），
#   覆盖 "api key: xxx" 这类自然语言写法；键名允许复数 s（tokens/passwords）；
# - bearer：覆盖 Authorization: Bearer <token> 头部（键名后的空白即分隔符）；
# - 分隔符：=/:/=>/is/纯空白，含全角 ：＝（中文日志）；键名可被成对引号包裹
#   （JSON/YAML 形态），引号后允许 `)]`（args['password']=v、[password]=v）；
#   分隔符前后允许 , :=> 等标点噪声（password:, v / password :: v 不因空值漏遮）；
#   键名后允许右括号/成对引号（含全角“”‘’）等包裹符（[password]=v、args['password']=v、
#   “password”：v 都是真实 dump 形态）；
# - 不加 (?<![\w-]) 前边界：db_password、X-Api-Key、中文前缀（如"密码password="，
#   \w 在 Unicode 下匹配 CJK）都是真实报错形态，去掉边界宁可多遮也不漏遮。
_SENSITIVE_CREDENTIAL_PREFIX_RE = re.compile(
    r"""
    (?P<key_quote>["']?)
    (?P<key>
        secret[\s_-]*key|password|passwd|secret|token|
        api[\s_-]*key|access[\s_-]*key|private[\s_-]*key|
        auth[\s_-]*token|bearer
    )s?
    (?P=key_quote)
    [\])"'“”‘’]*
    [\s:,=>：＝]*(?:=>|[:：=＝]|\bis\b|\s+)[\s:,=>：＝]*
    """,
    flags=re.IGNORECASE | re.VERBOSE,
)

# 非引号值的终止符：空白与常见结构符（JSON/字典的逗号、右花括号/方括号）。
# 例外：逗号后紧跟引号项视为列表值继续遮蔽（tokens: ['sk-a','sk-b'] 中
# 第二项没有键名认领，停在逗号会泄漏它）。
# 刻意不把 URL 参数分隔符 & 当作值边界：普通凭据值本身可含 &（如拼接进
# URL 的密码），提前截断会泄漏值的后半段，违反"宁可多遮"原则。因此
# "https://x.com/cb?token=abc&user=1" 会整体遮蔽为 "?token=***"，
# 牺牲少量可读性换取不泄漏。
_VALUE_STOP_CHARS = " \t\r\n,;}]"

# 值的成对引号：ASCII 同字符闭合；全角弯引号开/闭字符不同，需配对映射。
_VALUE_QUOTE_PAIRS = {'"': '"', "'": "'", "\u201c": "\u201d", "\u2018": "\u2019"}

# 容器值（凭据列表/dict dump）的配对括号。
_CONTAINER_CLOSE = {"[": "]", "{": "}"}


def _quoted_value_end(text: str, value_start: int, close_quote: str) -> int:
    """单次前向扫描引号值；未闭合（含被截断）时 fail-safe 遮到文本末尾。"""
    index = value_start + 1
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == close_quote:
            return index + 1
        index += 1
    return len(text)


def _skip_chars(text: str, index: int, chars: str) -> int:
    while index < len(text) and text[index] in chars:
        index += 1
    return index


def _quoted_item_ahead(text: str, index: int) -> bool:
    """index 处的逗号后（可跨空白）是否还有引号项（'a', 'b' 形式的列表值）。"""
    ahead = _skip_chars(text, index, ", \t")
    return ahead < len(text) and text[ahead] in _VALUE_QUOTE_PAIRS


def _quoted_item_is_dict_key(text: str, quote_end: int) -> bool:
    """引号项结束后若紧跟 :/：，判定为下一个 dict 的键名而非列表值。"""
    ahead = _skip_chars(text, quote_end, " \t")
    return ahead < len(text) and text[ahead] in ":："


def _container_value_end(text: str, value_start: int) -> int:
    """容器值（[...]/{...}）配对扫描，引号内容成段跳过；未闭合遮到末尾。"""
    open_char = text[value_start]
    close_char = _CONTAINER_CLOSE[open_char]
    depth = 0
    index = value_start
    while index < len(text):
        char = text[index]
        if char in _VALUE_QUOTE_PAIRS:
            index = _quoted_value_end(text, index, _VALUE_QUOTE_PAIRS[char])
            continue
        if char == open_char:
            depth += 1
        elif char == close_char:
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return len(text)


def _chained_quoted_items_end(text: str, quote_end: int) -> int:
    """引号值后若跟随 ', "next-item"' 链（无键名的裸列表），继续吞并遮蔽。"""
    end = quote_end
    while True:
        ahead = _skip_chars(text, end, ", \t")
        if (ahead > end and ahead < len(text)
                and text[ahead] in _VALUE_QUOTE_PAIRS
                and not _quoted_item_is_dict_key(text, _quoted_value_end(
                    text, ahead, _VALUE_QUOTE_PAIRS[text[ahead]]))):
            end = _quoted_value_end(text, ahead, _VALUE_QUOTE_PAIRS[text[ahead]])
        else:
            return end


def _credential_value_end(text: str, value_start: int) -> int:
    """返回凭据值的结束位置（含尾引号）；值缺失/截断时保守取到文本末尾。"""
    if value_start >= len(text):
        return value_start
    first = text[value_start]
    if first in _CONTAINER_CLOSE:
        return _container_value_end(text, value_start)
    if first in _VALUE_QUOTE_PAIRS:
        quote_end = _quoted_value_end(text, value_start, _VALUE_QUOTE_PAIRS[first])
        return _chained_quoted_items_end(text, quote_end)

    index = value_start
    while index < len(text):
        char = text[index]
        if char == "," and _quoted_item_ahead(text, index):
            index = _skip_chars(text, index, ", \t")
            continue
        if char in _VALUE_STOP_CHARS:
            break
        index += 1
    return index


def sanitize_scheduled_task_error(error_msg: Optional[str]) -> Optional[str]:
    """遮蔽常见凭据赋值；未闭合引号/截断文本宁可多遮，不泄漏值的后半段。

    单次线性扫描：正则定位键名+分隔符，值按引号/容器/终止符规则确定边界
    （凭据列表 ['a','b'] 整体遮蔽，第二个列表项没有键名认领、停在逗号会泄漏），
    统一渲染为 "<键名>=***"。None/空串原样返回。
    已知不覆盖（需解码识别，超出文本遮蔽范围）：URL userinfo
    （https://user:pass@host）与 Authorization: Basic <base64> 头。
    """
    if not error_msg:
        return error_msg

    parts = []
    cursor = 0
    while True:
        match = _SENSITIVE_CREDENTIAL_PREFIX_RE.search(error_msg, cursor)
        if match is None:
            parts.append(error_msg[cursor:])
            break
        parts.append(error_msg[cursor:match.start()])
        parts.append(f"{match.group('key')}=***")
        cursor = _credential_value_end(error_msg, match.end())
    return "".join(parts)
