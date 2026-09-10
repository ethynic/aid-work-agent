"""
LLM 输出文本净化器 — 应对大模型返回的不合规 markdown。

线上案例（tr_45b66335492c4540）：模型把整段回复包进引用块（几乎每行前缀 `>`，
包括表格行），导致 wecom_kf 渠道表格检测失败（行以 `>` 开头而非 `|`），
微信侧收到原始 `>`/`|` 符号文本而非渲染长图；Web 端也会把整段渲染成灰色引用块。

处理策略（保守，只修明确的意外包裹，不动有意义的局部引用）：
1. 整段包裹剥离：非空行中 ≥80% 带引用标记且引用行 ≥3 行时，判定为意外包裹，
   剥离所有行的引用前缀（含嵌套 `> >`）；
2. 表格行救援：未达整段阈值时，仅剥离「剥掉引用前缀后以 `|` 开头」的行的引用
   前缀 —— markdown 表格是顶层结构，被包进引用块几乎必然是模型输出瑕疵，
   且会导致渠道表格检测/渲染失败。

净化是幂等的，重复调用无副作用。
"""
import re

# 行首引用标记：允许嵌套（> > text）与标记后无空格（>text）
_BLOCKQUOTE_PREFIX_RE = re.compile(r'^\s*(?:>\s?)+')

# 行是否带引用标记
_QUOTED_LINE_RE = re.compile(r'^\s*>')

# 整段包裹判定：引用行占比阈值 / 最少引用行数
_WRAP_RATIO_THRESHOLD = 0.8
_MIN_QUOTED_LINES = 3


def strip_blockquote_markers(text: str) -> str:
    """剥离每行行首的引用标记（支持嵌套与缺空格写法），其余内容不变。"""
    if not text:
        return text
    return '\n'.join(
        _BLOCKQUOTE_PREFIX_RE.sub('', line) if _QUOTED_LINE_RE.match(line) else line
        for line in text.split('\n')
    )


def sanitize_llm_markdown(text: str) -> str:
    """净化 LLM 输出的 markdown，修复意外引用包裹。

    Args:
        text: LLM 返回的原始 markdown 文本

    Returns:
        净化后的文本；无意外包裹时原样返回
    """
    if not text or '>' not in text:
        return text

    lines = text.split('\n')
    non_empty = [line for line in lines if line.strip()]
    if not non_empty:
        return text
    quoted_count = sum(1 for line in non_empty if _QUOTED_LINE_RE.match(line))

    if (
        quoted_count >= _MIN_QUOTED_LINES
        and quoted_count / len(non_empty) >= _WRAP_RATIO_THRESHOLD
    ):
        # 规则 1：整段意外包裹 → 全部剥离
        return strip_blockquote_markers(text)

    # 规则 2：只救援表格行（剥掉引用前缀后以 | 开头的行）
    return '\n'.join(
        _BLOCKQUOTE_PREFIX_RE.sub('', line)
        if _QUOTED_LINE_RE.match(line)
        and _BLOCKQUOTE_PREFIX_RE.sub('', line).startswith('|')
        else line
        for line in lines
    )
