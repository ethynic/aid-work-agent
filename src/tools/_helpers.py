"""
工具层公共 helper

为后续 Phase 2+ 所有工具改造提供两个统一能力：
- truncate_text：文本返回字段统一截断（超出加后缀 + truncated 标记）
- sanitize_error：错误返回统一脱敏（白名单机制，杜绝 str(e)/traceback/kwargs 泄漏）

设计依据：docs/tools/tool-overall-optimization-design.md #2 统一规范。
脱敏白名单模式参照 src/tools/ppt/ppt_process_tool.py::_format_user_error。

本模块只依赖标准库，刻意不拉起 src 的 db/config/agent 链，便于在工具 execute()
内部就近调用而不引入循环导入或启动开销。
"""

from typing import Optional, Tuple, Union

__all__ = ["truncate_text", "sanitize_error"]


def truncate_text(
    text: Optional[str], limit: int, suffix: str = "..."
) -> Tuple[str, bool]:
    """截断文本到指定字符上限。

    统一处理工具返回值中的文本型字段，避免把全文/大块内容灌入 LLM 上下文。
    配合返回字典中的 ``truncated`` 标记使用，让 LLM 据此判断是否需要续读。

    Args:
        text: 待截断文本。``None`` 或空串视为未超长，原样返回。
        limit: 字符上限（结果文本长度上限，不含后缀）。必须 > 0，否则原样返回。
        suffix: 超长时拼接的后缀，默认 ``"..."``。

    Returns:
        ``(截断后文本, 是否发生了截断)``。
        - 未超长：``(原文本, False)``。
        - 超长：``(text[:limit] + suffix, True)``。
    """
    # 空值或空串：不截断
    if not text:
        return (text if text is not None else "", False)
    # limit 非法（<=0）：不做截断，交由调用方约束，避免吞掉全部内容
    if limit <= 0:
        return (text, False)

    if len(text) <= limit:
        return (text, False)
    return (text[:limit] + suffix, True)


# safe_messages 的值允许三种形态（参照 ppt_process 的两种白名单写法 + 一个固定消息兜底）：
#   - str                       ：类型命中即返回该固定安全消息
#   - set[str] / frozenset[str]：类型命中后，str(error) 在集合内才透传原文，否则走 fallback
#   - tuple[set[str], str]      ：类型命中后，在集合内透传原文，否则返回该分类兜底消息
SafeMessageValue = Union[str, "set[str]", "frozenset[str]", Tuple["set[str]", str]]


def sanitize_error(
    error: Union[BaseException, str, None],
    safe_messages: Optional[dict] = None,
    fallback: str = "操作失败，请稍后重试",
) -> str:
    """通用错误脱敏：白名单命中才透传，其余一律兜底。

    绝不返回 ``str(e)`` 原文 / traceback / kwargs，避免把异常细节、明文凭证或
    调试信息灌入对话上下文（设计文档 #2 问题 C）。

    白名单机制（参照 ``ppt_process._format_user_error``）：

    - 按「异常类型」匹配。``safe_messages`` 形如 ``{异常类型: 值}``，值支持三种：
      1. ``str`` —— 类型命中即返回该固定安全消息；
      2. ``set[str]`` —— 类型命中后，仅当 ``str(error)`` 属于该集合时透传该消息字符串，
         否则继续匹配下一个类型 / 走 ``fallback``；
      3. ``(set[str], str)`` —— 同上，但未命中集合时返回第二位的分类兜底消息
         （便于像 ppt 那样保留「HTML 文件未通过安全检查」这类分类提示）。
    - 字符串入参（``error`` 为 str）：没有类型可匹配，改为扫描所有 ``set`` 形态白名单，
      命中即透传该字符串（已知安全消息以字符串形态到达时也安全），否则走 ``fallback``。
    - 都不命中或未提供白名单：返回 ``fallback``。

    Args:
        error: 异常对象或错误字符串。``None`` 视为兜底。
        safe_messages: 调用方按工具自身白名单提供的映射；为空则直接兜底。
        fallback: 都不命中时的兜底脱敏消息。

    Returns:
        脱敏后的用户可读错误字符串。
    """
    if not fallback:
        fallback = "操作失败，请稍后重试"

    # 字符串入参：无类型可匹配，扫描 set 形态白名单判断是否为已知安全消息
    if isinstance(error, str):
        if safe_messages:
            for value in safe_messages.values():
                if isinstance(value, (set, frozenset)) and error in value:
                    return error
                if isinstance(value, tuple) and len(value) == 2 and isinstance(
                    value[0], (set, frozenset)
                ) and error in value[0]:
                    return error
        return fallback

    # None 或非异常对象：无类型可匹配，直接兜底
    if not isinstance(error, BaseException):
        return fallback

    if not safe_messages:
        return fallback

    # 按异常类型匹配（MRO 友好：先精确类型，再父类型由 dict 顺序决定）
    message = str(error)
    for exc_type, value in safe_messages.items():
        if not isinstance(error, exc_type):
            continue

        # 形态 1：固定安全消息
        if isinstance(value, str):
            return value

        # 形态 2：集合白名单，命中才透传，否则继续往下匹配
        if isinstance(value, (set, frozenset)):
            if message in value:
                return message
            continue

        # 形态 3：(集合白名单, 分类兜底)，命中透传，否则返回分类兜底
        if isinstance(value, tuple) and len(value) == 2 and isinstance(
            value[0], (set, frozenset)
        ):
            allowed, category_fallback = value
            if message in allowed:
                return message
            return category_fallback if category_fallback else fallback

    # 所有类型 / 白名单都未命中：兜底
    return fallback
