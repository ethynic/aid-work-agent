"""
Excel ETL 敏感数据脱敏钩子（Q3 最简实现：占位符往返）

发送给 LLM 前，把文本中的身份证号/手机号替换为 [ID_n]/[TEL_n] 占位符；
抽取结果回写时代码按映射表还原真值，LLM 全程不见真实证件号/手机号。

设计要点：
- 同一真值在整个会话内稳定复用同一占位符（映射表随任务生命周期）；
- 已脱敏形式（含 ``*`` 或 ``XXXX`` 掩码，如 ``340203********1234``）天然不会被
  数字分段命中，mask 层直接放行（D10：脱敏身份证照抄 + warning 是 Phase 2 校验层的事）；
- 词边界处理：身份证/手机号不会被更长数字串（如 22 位订单号）中的子串误触发；
  手机号与身份证直接相连时也能各自正确切分（按 18/11 位贪心回溯分词）。
"""

import re
from typing import Any, Dict, List, Optional, Tuple

# 18 位身份证号：6 位地址码（首位非 0）+ 8 位出生日期（19xx/20xx、合法月日）
# + 3 位顺序码 + 1 位校验位（末位可为 X）
_ID_RE = re.compile(r"[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]")
# 手机号：1 开头 11 位（第二位 3-9，现行手机号段）
_TEL_RE = re.compile(r"1[3-9]\d{9}")
# 连续数字串（脱敏候选的最小单位，``*``/字母/中文都会截断数字串）
_RUN_RE = re.compile(r"\d+")
# 占位符（还原用；公开常量供 excel_extract 等下游识别占位符形式，避免正则漂移失配）
PLACEHOLDER_RE = re.compile(r"\[(?:ID|TEL)_\d+\]")


def _segment(s: str) -> Optional[List[Tuple[int, int, str]]]:
    """
    把纯数字串（末位可带 X 作身份证校验位）精确切分为 身份证(18)/手机号(11) 序列。

    整串恰好被敏感信息耗尽才返回切分结果（含偏移与类型），否则返回 None
    （意味着这是订单号等普通长数字串，放行不脱敏）。
    """
    n = len(s)
    if n < 11:
        return None
    memo: Dict[int, Optional[List[Tuple[int, int, str]]]] = {}

    def backtrack(i: int) -> Optional[List[Tuple[int, int, str]]]:
        if i == n:
            return []
        if i in memo:
            return memo[i]
        result = None
        # 优先按 18 位身份证切（最长匹配优先），失败再试 11 位手机号
        if i + 18 <= n and _ID_RE.fullmatch(s, i, i + 18):
            rest = backtrack(i + 18)
            if rest is not None:
                result = [(i, 18, "id")] + rest
        if result is None and i + 11 <= n and _TEL_RE.fullmatch(s, i, i + 11):
            rest = backtrack(i + 11)
            if rest is not None:
                result = [(i, 11, "tel")] + rest
        memo[i] = result
        return result

    return backtrack(0)


class MaskSession:
    """脱敏会话：映射表随任务生命周期，同一真值稳定复用同一占位符"""

    def __init__(self) -> None:
        self._id_map: Dict[str, str] = {}   # 身份证真值 -> 占位符
        self._tel_map: Dict[str, str] = {}  # 手机号真值 -> 占位符
        self._reverse: Dict[str, str] = {}  # 占位符 -> 真值

    def mask(self, text: str) -> str:
        """把文本中的身份证号/手机号替换为占位符，返回脱敏后文本"""
        if not text:
            return text
        pieces: List[str] = []
        last_end = 0
        for m in _RUN_RE.finditer(text):
            next_char = text[m.end()] if m.end() < len(text) else ""
            masked = self._mask_run(m.group(), next_char)
            if masked is None:
                continue
            replacement, consumed = masked
            pieces.append(text[last_end:m.start()])
            pieces.append(replacement)
            last_end = m.start() + consumed
        pieces.append(text[last_end:])
        return "".join(pieces)

    def unmask(self, text: str) -> str:
        """把文本中的占位符还原为真值；未知占位符原样保留"""
        if not text:
            return text
        return PLACEHOLDER_RE.sub(lambda m: self._reverse.get(m.group(), m.group()), text)

    def unmask_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """递归还原 dict 内所有字符串值中的占位符（Phase 2 抽取记录还原用）"""
        return self._unmask_value(record)

    def _mask_run(self, run: str, next_char: str) -> Optional[Tuple[str, int]]:
        """
        对单个数字串做敏感信息切分（next_char 为原文中紧随数字串的字符）。

        Returns:
            (替换后的占位符文本, 原文消耗长度) 或 None（非敏感数字串）。
            消耗长度可能比数字串长 1：紧随的 X/x 是身份证校验位时一并消耗。
        """
        tokens = _segment(run)
        extra = 0
        if tokens is None and len(run) >= 17 and next_char in ("X", "x"):
            # 17 位数字 + 紧随的 X/x 可能构成"校验位为 X"的身份证，补上重试
            tokens = _segment(run + "X")
            extra = 1
        if tokens is None:
            return None
        full = run + next_char if extra else run
        parts = [self._placeholder(full[start:start + length], kind) for start, length, kind in tokens]
        return "".join(parts), len(run) + extra

    def _placeholder(self, real: str, kind: str) -> str:
        """真值 -> 占位符（同一真值稳定复用同一编号）"""
        mapping = self._id_map if kind == "id" else self._tel_map
        ph = mapping.get(real)
        if ph is None:
            ph = f"[{kind.upper()}_{len(mapping) + 1}]"
            mapping[real] = ph
            self._reverse[ph] = real
        return ph

    def _unmask_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.unmask(value)
        if isinstance(value, dict):
            return {k: self._unmask_value(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._unmask_value(v) for v in value]
        return value


_default_session: Optional[MaskSession] = None


def _get_default_session() -> MaskSession:
    """进程级默认会话（延迟创建，供模块级便捷函数共用）"""
    global _default_session
    if _default_session is None:
        _default_session = MaskSession()
    return _default_session


def mask(text: str) -> str:
    """
    模块级便捷脱敏：单文本立即往返（unmask(mask(text)) 还原全等）。

    与 unmask() 共用进程级默认会话；任务级请自建 MaskSession 以绑定任务生命周期。
    """
    return _get_default_session().mask(text)


def unmask(text: str) -> str:
    """模块级便捷还原：还原 mask() 产生的占位符（共用默认会话的映射表）"""
    return _get_default_session().unmask(text)
