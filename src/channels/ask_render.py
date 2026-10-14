"""结构化提问（AskForm）渠道文字渲染与入站解析（设计 §7.2，纯渲染层）。

职责边界：
- ``render_segments``：按**实际渲染 UTF-8 字节**把 AskForm 分段为渠道消息列表
  （wecom/dingtalk/feishu 等无条数限制、只有单条字节限的渠道）。逻辑行优先
  （intro 行 / 题头行 / 选项行）；单逻辑行自身超限时在**合法字符边界**继续
  拆分跨消息（不破坏多字节序列），续段保留题号/选项号前缀（选项行续段如
  ``Q1 选项2（续）``，题头行续段为 ``Q1（续）``）；紧凑模式（省 description）
  作为压缩手段。题干与选项内容**永不丢失**——「不截断」指不丢内容，允许
  一行跨消息（设计 v6 口径）。
- ``render_single_message``：**按条数计费/限制**的渠道（wecom_kf / RPA，见
  ``SINGLE_MESSAGE_SOURCES``）把 AskForm 合并为**一条消息**整体发送，超长时
  依次精简 intro、题头重复操作提示、选项 description（评审修正 §7.2）。
- ``parse_reply``：入站单管线的解析半边（三分法）：
  * 无编号协议特征 → 自然语言，返回 ``None``（调用方沿既有 reply 路径透传）；
  * 明确使用协议（出现题号）但含无法解释项（未知题号、越界选项、同题矛盾等）
    → 协议错误，返回错误标记 + 纠正文案（一次，不计数）；text 题冒号后内容
    一律作为该题文本（不以「纯数字」推断携带选项，text 题本无选项）；
  * 可解析 → ``{"ok": True, "answers": [...]}``，选项序号→key、题号→question_id
    的映射在本层完成，selected_keys 为服务端选项 key。
- 精确指令识别：``is_skip_reply`` / ``is_resend_reply``。

约束：canonical 由服务端生成，本层不拼 canonical；本层不做任何 IO，不触达
数据库与网络；无害格式差异（全/半角、分隔符、大小写、重复选项）归一化而非拒绝。
"""

import re
from typing import Any, Dict, List, Optional, Tuple

# 各渠道默认分段字节上限（设计 §2.3；session 发送侧会用 adapter 现有配置覆盖）。
# wecom_personal_rpa 平台口径为 2000 字符；UTF-8 字节数 ≥ 字符数，按 2000 字节
# 分段可保证每段 ≤ 2000 字符（保守但不超限）。
_SOURCE_SEGMENT_LIMITS = {
    'wecom': 2048,
    'dingtalk': 4000,
    'feishu': 4000,
    'wecom_kf': 2048,
    'wecom_personal_rpa': 2000,
}


def source_segment_limit(source: str) -> int:
    """渠道默认分段字节上限；未知渠道按最保守的 2048 处理。"""
    return _SOURCE_SEGMENT_LIMITS.get(source or '', 2048)


# 按条数计费/限制回复条数的渠道：AskForm 整体渲染为一条消息发送（评审修正 §7.2），
# 不按题拆段、不为多段维护续传/额度管理。
SINGLE_MESSAGE_SOURCES = frozenset({'wecom_kf', 'wecom_personal_rpa'})


def _blen(text: str) -> int:
    """渲染 UTF-8 字节数（分段预算的唯一位移口径）。"""
    return len(text.encode('utf-8'))


# 全角数字/字母归一化（无害差异归一化，不拒绝）
_FULLWIDTH_TRANS = str.maketrans({
    '０': '0', '１': '1', '２': '2', '３': '3', '４': '4',
    '５': '5', '６': '6', '７': '7', '８': '8', '９': '9',
    'Ｑ': 'Q', 'ｑ': 'q', '：': ':', '，': ',', '、': ',', '；': ';',
})


def _normalize(text: str) -> str:
    return (text or '').translate(_FULLWIDTH_TRANS)


# 题号开头行：Q1 / q1 / Q1： / Q1、 / Q1: 等（冒号/顿号等分隔符可省略）。
# 题号捕获完整数字串：多位数（如 Q12，表单仅 1~5 题且 id 为 ^q[1-9]$）落入
# 「未知题号」协议错误，而不是被误解析为单题号+正文（如把 Q12：abc 当成
# Q1 选 2 加自定义文本，设计 §7.2 三分法：未知题号 → 协议错误）。
_QUESTION_LINE_RE = re.compile(r'^\s*[Qq]\s*(\d+)\s*[:：,，;；.．\-—]?\s*(.*)$')

# 纯编号选择语法：数字与分隔符（空格/逗号/顿号/斜杠/分号）交替，无其他文字
_PURE_NUMBER_RE = re.compile(r'^[\d\s,，;；/／]+$')

# 前导选择编号：数字串与其后分隔符的重复；捕获组 2 为剩余文字
_LEADING_SELECTION_RE = re.compile(r'^((?:\d+[\s,，;；/／]*)+)(.*)$')

# 精确指令（容忍末尾标点）
_SKIP_TOKENS = {'跳过'}
_RESEND_TOKENS = {'重发', '重新发送', '简化'}
_EDGE_PUNCT = ' \t\r\n。．.!！?？;；,，'


def _exact_token(tokens: set, text: str) -> bool:
    return (text or '').strip().strip(_EDGE_PUNCT) in tokens


def is_skip_reply(text: str) -> bool:
    """精确「跳过」指令（→ 结构化 skip 提交，唯一跳过入口）。"""
    return _exact_token(_SKIP_TOKENS, text)


def is_resend_reply(text: str) -> bool:
    """精确「重发 / 重新发送 / 简化」指令（→ 按发送事实续传缺段，不作答案）。"""
    return _exact_token(_RESEND_TOKENS, text)


# ==================== 出站渲染：render_segments ====================


class _Line:
    """逻辑行：文本 + 归属题号（None=intro/footer 等全局行）+ 选项号 + 是否题头行。"""

    __slots__ = ('text', 'qnum', 'onum', 'is_header')

    def __init__(self, text: str, qnum: Optional[int] = None, onum: Optional[int] = None,
                 is_header: bool = False):
        self.text = text
        self.qnum = qnum
        self.onum = onum
        self.is_header = is_header


def _type_hint(question: Dict[str, Any]) -> str:
    qtype = question.get('type')
    if qtype == 'text':
        return '（请直接回复文字）'
    if qtype == 'single':
        return '（单选，回复编号如"1"；也可直接回复文字）'
    return '（多选，回复编号如"1、3"；也可直接回复文字）'


def _footer_line(form: Dict[str, Any]) -> _Line:
    if form.get('allow_skip') is False:
        text = ('回复编号或文字作答（多题请带题号，如"Q1：1、3"）；'
                '本问题需明确答复后任务才能继续。')
    else:
        text = ('回复编号或文字作答（多题请带题号，如"Q1：1、3"）；'
                '回复"跳过"将按已知信息继续。')
    return _Line(text)


def _question_number(question: Dict[str, Any], position: int) -> int:
    """题号以服务端生成的 id（^q[1-9]$，按题序）为准；畸形 id 退回题序位置。"""
    match = re.match(r'^q([1-9])$', str(question.get('id') or ''))
    return int(match.group(1)) if match else position + 1


def _logical_lines(form: Dict[str, Any], compact: bool, include_intro: bool = True,
                   include_hints: bool = True) -> List[_Line]:
    """AskForm → 逻辑行序列（intro 行、题头行、选项行）。

    ``include_intro``/``include_hints`` 供单条合并（``render_single_message``）
    的渐进精简使用：分别省略 intro 行与题头重复操作提示（type hint）。
    """
    lines: List[_Line] = []
    title = str(form.get('title') or '').strip()
    intro = str(form.get('intro') or '').strip()
    if include_intro and (title or intro):
        lines.append(_Line(f'【{title}】{intro}' if title and intro else (title or intro)))
    for position, question in enumerate(form.get('questions') or []):
        if not isinstance(question, dict):
            continue
        qnum = _question_number(question, position)
        tag = str(question.get('tag') or '').strip()
        prompt = str(question.get('prompt') or '').strip()
        hint = _type_hint(question) if include_hints else ''
        header = f'Q{qnum}【{tag}】{prompt}{hint}' if tag else f'Q{qnum}{prompt}{hint}'
        lines.append(_Line(header, qnum=qnum, is_header=True))
        for index, option in enumerate(question.get('options') or [], start=1):
            if not isinstance(option, dict):
                continue
            row = f'{index}. {str(option.get("label") or "").strip()}'
            if not compact:
                description = str(option.get('description') or '').strip()
                if description:
                    row += f' — {description}'
            lines.append(_Line(row, qnum=qnum, onum=index))
    return lines


def _fit_chunk(text: str, max_bytes: int) -> Tuple[str, str]:
    """按合法字符边界切出前缀块（UTF-8 字节 ≤ max_bytes），返回（块, 余部）。"""
    if max_bytes <= 0 or _blen(text) <= max_bytes:
        return text, ''
    low, high = 1, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if _blen(text[:mid]) <= max_bytes:
            low = mid
        else:
            high = mid - 1
    return text[:low], text[low:]


def _continuation_prefix(line: _Line) -> str:
    """跨消息续段前缀：选项行携带所属选项号（如 ``Q1 选项2（续）``），
    题头行续段为 ``Q1（续）``；无题号全局行为 ``（续）``。"""
    if line.qnum is None:
        return '（续）'
    if line.onum is not None and not line.is_header:
        return f'Q{line.qnum} 选项{line.onum}（续）'
    return f'Q{line.qnum}（续）'


def render_segments(form: Dict[str, Any], limit_bytes: int, compact: bool = False) -> List[str]:
    """AskForm → 渠道文字消息分段列表（设计 §7.2）。

    - 逻辑行优先：intro / 题头 / 选项行整体积累，按实际渲染字节分段；
    - 单行自身超限：行内按合法字符边界继续拆分（不破坏多字节序列），
      续块加题号/选项号前缀（``Q1（续）`` / ``Q1 选项2（续）``；无题号行如
      超长 intro 用 ``（续）``）；
    - 段间断点落在同一题的选项/续行上时，新段以对应续段前缀开头；
    - 紧凑模式省略 description；末段附操作提示（allow_skip=false 时改为
      「需明确答复」口径）；题干与选项内容永不丢失。
    """
    limit = int(limit_bytes)
    if limit < 64:
        raise ValueError(f'ask_render 分段字节上限过小: {limit}')
    lines = _logical_lines(form, compact)
    if not (form.get('questions') or []):
        return []
    lines.append(_footer_line(form))

    segments: List[str] = []
    buf: List[str] = []
    buf_bytes = 0
    open_qnum: Optional[int] = None

    def flush():
        nonlocal buf, buf_bytes
        if buf:
            segments.append('\n'.join(buf))
        buf, buf_bytes = [], 0

    for line in lines:
        text = line.text
        # 1) 尝试放入当前段
        cost = _blen(text) + (1 if buf else 0)
        if buf_bytes + cost <= limit:
            buf.append(text)
            buf_bytes += cost
            open_qnum = line.qnum if line.qnum is not None else open_qnum
            continue
        # 2) 需要换段：同题续行带题号/选项号前缀；题头行/全局行不带
        flush()
        break_prefix = _continuation_prefix(line) if (line.qnum is not None and not line.is_header) else None
        # 3) 行仍超限（含前缀开销）→ 行内字符边界切分跨消息
        budget = limit - ((break_prefix and _blen(break_prefix) + 1) or 0)
        chunk, rest = _fit_chunk(text, budget)
        buf.append((break_prefix + '\n' if break_prefix else '') + chunk)
        buf_bytes = _blen(buf[0])
        flush()
        cont_prefix = _continuation_prefix(line)
        while rest:
            chunk, rest = _fit_chunk(rest, limit - _blen(cont_prefix) - 1)
            buf.append(cont_prefix + '\n' + chunk)
            buf_bytes = _blen(buf[0])
            flush()
        open_qnum = line.qnum if line.qnum is not None else open_qnum
    flush()
    return segments


# 单条合并（render_single_message）的渐进精简阶段（评审修正 §7.2）：
# 依次精简 intro → 题头重复操作提示（type hint）→ 选项 description；
# 题干、选项 label、清晰编号与末尾操作提示在各阶段保留。
_SINGLE_STAGES = (
    {'compact': False, 'intro': True, 'hints': True},
    {'compact': False, 'intro': False, 'hints': True},
    {'compact': False, 'intro': False, 'hints': False},
    {'compact': True, 'intro': False, 'hints': False},
)


def render_single_message(form: Dict[str, Any], limit_bytes: int) -> str:
    """AskForm → 单条合并文本（wecom_kf/RPA 等按条数计费渠道，评审修正 §7.2）。

    一表单渲染为一条消息整体发送，不按题拆多条。超长时依次精简 intro、
    题头重复操作提示、选项 description（保留问题、必要选项与清晰编号）；
    全部阶段后仍超 limit_bytes 时返回最简文本，由 session 发送侧按渠道
    既有长文本方式兜底（kf 截断单条、RPA 既有超长分段）。
    """
    limit = int(limit_bytes)
    if limit < 64:
        raise ValueError(f'ask_render 单条字节上限过小: {limit}')
    if not (form.get('questions') or []):
        return ''
    text = ''
    for stage in _SINGLE_STAGES:
        lines = _logical_lines(form, compact=stage['compact'],
                               include_intro=stage['intro'], include_hints=stage['hints'])
        lines.append(_footer_line(form))
        text = '\n'.join(line.text for line in lines)
        if _blen(text) <= limit:
            break
    return text


# ==================== 入站解析：parse_reply ====================


def _parse_answer_body(rest: str, question: Dict[str, Any], qnum: int) -> Tuple[Dict[str, Any], List[str]]:
    """解析单题答案体（题号后的内容）。返回（entry, 错误列表）。

    entry: {"question_id": "q1", "selected_keys": [...], "text": str|None}
    归一化：全/半角、分隔符、去重保持首次顺序；编号归 selected，其余文字归 text。
    """
    qid = f'q{qnum}'
    qtype = question.get('type')
    options = [o for o in (question.get('options') or []) if isinstance(o, dict)]
    rest_stripped = rest.strip()
    # 单字符等长归一化（全角→半角），归一化串只用于匹配，原文余部按切位回取
    body = _normalize(rest_stripped)

    if qtype == 'text':
        # text 题本无选项：冒号后内容一律作为该题文本，不以「纯数字」推断
        # 携带选项（评审修正：金额/年份等合法数字答案不再被误拒）；服务端
        # selected_keys 非空的结构校验（ask_form）继续保留。
        if not body:
            return _entry(qid), [f'Q{qnum} 未包含答案内容']
        return _entry(qid, text=rest_stripped), []

    match = _LEADING_SELECTION_RE.match(body)
    numbers = [int(n) for n in re.findall(r'\d+', match.group(1))] if match else []
    remainder = rest_stripped[match.end(1):].strip() if match else rest_stripped

    if not numbers and not remainder:
        return _entry(qid), [f'Q{qnum} 未包含答案内容']
    if numbers:
        if any(n < 1 or n > len(options) for n in numbers):
            bound = f'1-{len(options)}' if options else '无'
            return _entry(qid), [f'Q{qnum} 的选项编号超出范围（{bound}）']
    unique_numbers = list(dict.fromkeys(numbers))  # 重复选项去重，按一次计
    keys = [options[n - 1].get('key') for n in unique_numbers]
    keys = [k for k in keys if isinstance(k, str)]

    errors: List[str] = []
    if qtype == 'single' and len(unique_numbers) > 1:
        errors.append(f'Q{qnum} 是单选题，只能选择一项')
    if qtype == 'multi':
        max_select = question.get('max_select')
        bound = max_select if isinstance(max_select, int) and max_select > 0 else len(options)
        if len(unique_numbers) > bound:
            errors.append(f'Q{qnum} 最多选择 {bound} 项')
    if remainder:
        if question.get('allow_custom_text') is False:
            errors.append(f'Q{qnum} 不允许自定义输入，请仅回复选项编号')
    if errors:
        return _entry(qid), errors
    return _entry(qid, selected_keys=keys, text=(remainder if remainder else None)), []


def _entry(qid: str, selected_keys: Optional[List[str]] = None, text: Optional[str] = None) -> Dict[str, Any]:
    return {'question_id': qid, 'selected_keys': list(selected_keys or []), 'text': text}


def _merge_entry(previous: Dict[str, Any], entry: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
    """同题两次出现：一致（集合相同且文本不矛盾）按一次合并；矛盾返回 False。"""
    same_selection = set(previous['selected_keys']) == set(entry['selected_keys'])
    prev_text = (previous.get('text') or '').strip()
    this_text = (entry.get('text') or '').strip()
    if same_selection and (not prev_text or not this_text or prev_text == this_text):
        merged = dict(previous)
        merged['text'] = prev_text or this_text or None
        return merged, True
    return previous, False


def _correction_text(errors: List[str]) -> str:
    detail = '\n'.join(f'- {e}' for e in errors)
    return (f'回复格式未能识别：\n{detail}\n'
            '请按题号回复（如"Q1：1、3"），也可直接回复文字说明。')


def submission_error_text(errors: Any) -> str:
    """服务端受理 422 的逐题明细（{question_id,code,message}）→ 用户纠正文案。

    渠道层不做二次校验，只把服务端权威错误如实转述；wait 未消费，用户修正后
    可直接重提（无累计状态）。表单级错误（question_id='form'）不带题号前缀。
    """
    lines: List[str] = []
    for item in errors or []:
        if not isinstance(item, dict):
            continue
        qid = str(item.get('question_id') or 'form')
        message = str(item.get('message') or item.get('code') or '校验失败')
        prefix = '' if qid == 'form' else f'{qid.upper()}：'
        lines.append(f'- {prefix}{message}')
    if not lines:
        return '提交的答案未通过校验，请修正后重新回复。'
    return '提交的答案未通过校验：\n' + '\n'.join(lines) + '\n请修正后直接重新回复即可。'


def parse_reply(text: str, form: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """入站单管线解析半边（三分法）。

    返回：
    - ``None``：无编号协议特征的自然语言（调用方沿既有 reply 路径透传，永不吞）；
    - ``{"ok": False, "errors": [...], "correction": str}``：协议错误（不提交，
      wait 不消费，一次纠正不计数）；
    - ``{"ok": True, "answers": [...]}``：可解析的结构化答案（选项序号→key、
      题号→question_id 已映射；空选/自由文本按 AnswerItem 形状给出）。

    按行解析：题号开头行开启新题答案；非题号行为上一题 text 续行；首个题号行
    之前的非空行无法归属任何题目，计入协议错误（不静默丢弃）。协议判定先扫描
    全文——避免普通闲聊行挡住后面的题号行。
    """
    if not isinstance(form, dict) or not (form.get('questions') or []):
        return None
    questions: Dict[str, Dict[str, Any]] = {}
    for position, question in enumerate(form.get('questions') or []):
        if isinstance(question, dict) and isinstance(question.get('id'), str):
            questions[question['id']] = question
    if not questions:
        return None

    raw = (text or '').strip()
    if not raw:
        return None
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    normalized_lines = [_normalize(line) for line in lines]
    if not lines:
        return None

    # 协议判定（全文扫描）：存在题号行 → 协议模式；否则仅单题选项表单的
    # 纯编号语法按该题解析（题头提示的「回复编号如"1、3"」用法）
    question_numbers = []
    for line in normalized_lines:
        match = _QUESTION_LINE_RE.match(line)
        if match:
            question_numbers.append(int(match.group(1)))
    if not question_numbers:
        if (len(questions) == 1
                and all(_PURE_NUMBER_RE.match(line) for line in normalized_lines)):
            qid, question = next(iter(questions.items()))
            match = re.match(r'^q([1-9])$', qid)
            if match and question.get('type') in ('single', 'multi'):
                entry, errors = _parse_answer_body(' '.join(lines), question, int(match.group(1)))
                if errors:
                    return {'ok': False, 'errors': errors, 'correction': _correction_text(errors)}
                return {'ok': True, 'answers': [entry]}
        return None

    entries: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    errors: List[str] = []
    current_qid: Optional[str] = None
    for original, line in zip(lines, normalized_lines):
        match = _QUESTION_LINE_RE.match(line)
        if match:
            qnum = int(match.group(1))
            qid = f'q{qnum}'
            question = questions.get(qid)
            if question is None:
                errors.append(f'Q{qnum} 不在当前问题中')
                current_qid = None
                continue
            entry, line_errors = _parse_answer_body(
                original[match.start(2):].strip(), question, qnum)
            errors.extend(line_errors)
            if qid in entries:
                merged, consistent = _merge_entry(entries[qid], entry)
                if not consistent:
                    errors.append(f'Q{qnum} 重复作答且内容矛盾')
                entries[qid] = merged
            else:
                entries[qid] = entry
                order.append(qid)
            current_qid = qid
            continue
        # 非题号行：上一题的 text 续行；尚无归属题目的行计入协议错误
        if current_qid is not None:
            entry = entries[current_qid]
            entry['text'] = f"{(entry.get('text') or '').strip()}\n{original}".strip() or None
        else:
            errors.append(f'无法归属到任何题目的一行：{original[:50]}')

    if errors:
        return {'ok': False, 'errors': errors, 'correction': _correction_text(errors)}
    return {'ok': True, 'answers': [entries[qid] for qid in order]}
