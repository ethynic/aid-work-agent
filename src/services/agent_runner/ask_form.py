"""结构化提问（AskForm）判定表与 canonical 的纯函数层。

docs/system/design-ask-user-interactive-question.md §4.2/§5.4 的受理前校验与
服务端权威文本生成。本模块不触达数据库与请求对象：输入为 wait 快照中的 form
dict 与归一化后的答案 dict，输出为逐题错误明细或 canonical 文本，便于独立测试。
错误明细 DTO（{question_id,code,message}）与 contracts/runtime-host/v1
ask-form-errors.schema.json 对齐。
"""

# 服务端权威跳过文案（§4.2）：skip 受理合法时写入执行载荷的固定文本。
SKIP_TEXT = '（用户跳过了本次提问，请按已知信息继续）'
# 未回答题在 canonical 中的占位文本。
UNANSWERED_TEXT = '（未回答）'
# 表单级错误（不属于任何单题）在逐题明细中使用的伪题号。errors DTO 要求
# question_id 非空字符串，前端按题号匹配不到时可作表单级提示展示。
FORM_LEVEL_ID = 'form'

FORM_SKIP_NOT_ALLOWED = 'FORM_SKIP_NOT_ALLOWED'
FORM_ANSWER_INVALID = 'FORM_ANSWER_INVALID'


def _field(item, key):
    """兼容 pydantic 模型与 dict 两种答案载体（受理层传模型，测试传 dict）。"""
    return item.get(key) if isinstance(item, dict) else getattr(item, key)


def _error(question_id, code, message):
    return {'question_id': question_id, 'code': code, 'message': message}


def _questions(form):
    """form 题序的 {id: question} 视图；form 缺失或畸形时按空表单处理。"""
    questions = {}
    if not isinstance(form, dict):
        return questions
    for question in form.get('questions') or []:
        if isinstance(question, dict) and isinstance(question.get('id'), str):
            questions[question['id']] = question
    return questions


def _option_keys(question):
    return {option.get('key') for option in question.get('options') or []
            if isinstance(option, dict) and isinstance(option.get('key'), str)}


def _max_select(question):
    """multi 的可选上限：缺省=选项数（§4.1）；single 恒为 1。"""
    bound = question.get('max_select')
    return bound if isinstance(bound, int) and bound > 0 else len(_option_keys(question))


def normalize_answers(answers):
    """归一化（§4.2）：question_id 小写、去首尾空白，selected_keys 重复 key 去重。

    去重保持首次出现顺序（按一次计）；无法确定解释的输入交给判定表拒绝。
    """
    return [{'question_id': _field(item, 'question_id').strip().lower(),
             'selected_keys': list(dict.fromkeys(_field(item, 'selected_keys') or [])),
             'text': _field(item, 'text')} for item in answers or []]


def validate_answers(form, answers, *, skip=False):
    """提交判定表（§5.4，顺序即优先级）。返回逐题错误明细列表，空列表=通过。

    - skip=true × allow_skip=false → FORM_SKIP_NOT_ALLOWED（表单级）；
    - answers 提交（含 []）：先结构校验（逐题明细，支持多错误），通过后再
      遍历原 form 全部题检查 required（省略的题=未答，指明缺失题 question_id）；
    - 全空提交（含 []）且无 required 违反 = 合法普通提交（通过）；
    - 纯文本（answers=None 且 skip=false）不走本函数，防御性返回通过。
    """
    questions = _questions(form)
    if skip:
        if not (isinstance(form, dict) and form.get('allow_skip')):
            return [_error(FORM_LEVEL_ID, FORM_SKIP_NOT_ALLOWED, '该表单不允许整体跳过')]
        return []
    if answers is None:
        return []
    errors = []
    seen = {}
    for item in answers:
        qid = item['question_id']
        question = questions.get(qid)
        if question is None:
            errors.append(_error(qid, FORM_ANSWER_INVALID, '问题不存在于当前表单'))
            continue
        previous = seen.get(qid)
        if previous is not None:
            # 同题重复：完全相同的重复项按一次计；答案矛盾即无法确定解释。
            if (previous['selected_keys'] != item['selected_keys']
                    or previous['text'] != item['text']):
                errors.append(_error(qid, FORM_ANSWER_INVALID, '同一问题重复提交且答案矛盾'))
            continue
        seen[qid] = item
        errors.extend(_structural_errors(question, item))
    if errors:
        # 判定表顺序即优先级：归一化后仍有无法解释项时，不再叠加 required 明细。
        return errors
    errors.extend(_required_errors(questions, seen))
    return errors


def _structural_errors(question, item):
    """单题结构校验（§4.2 无法解释项）。text 题带选项时不再重复其余检查。"""
    qid, qtype = question['id'], question.get('type')
    keys, text = item['selected_keys'], item.get('text')
    if qtype == 'text':
        if keys:
            return [_error(qid, FORM_ANSWER_INVALID, '该题为自由文本，不能携带选项')]
        return []
    errors = []
    if any(key not in _option_keys(question) for key in keys):
        errors.append(_error(qid, FORM_ANSWER_INVALID, '选择的选项不属于该题'))
    if text and question.get('allow_custom_text') is False:
        errors.append(_error(qid, FORM_ANSWER_INVALID, '该题不允许自定义输入'))
    if not keys:
        return errors
    if qtype == 'single' and len(keys) > 1:
        errors.append(_error(qid, FORM_ANSWER_INVALID, '该题为单选，最多选择一项'))
    elif qtype == 'multi' and len(keys) > _max_select(question):
        errors.append(_error(qid, FORM_ANSWER_INVALID,
                             f'该题最多选择 {_max_select(question)} 项'))
    return errors


def _required_errors(questions, seen):
    """required 遍历整个 form：省略的题=未答；有选项或有非空白文本才算已答。"""
    errors = []
    for qid, question in questions.items():
        if not question.get('required'):
            continue
        answer = seen.get(qid)
        if not (answer and (answer['selected_keys'] or (answer.get('text') or '').strip())):
            errors.append(_error(qid, FORM_ANSWER_INVALID, '该题为必答'))
    return errors


def canonical_text(form, answers, *, skip=False):
    """服务端权威 canonical 文本（§4.2），写入执行载荷 answer 键并落库。

    按 form 题序输出全部题（未答输出占位文本）；选项写可读 label 多选「、」；
    自定义以「；自定义：」前缀。skip 路径为固定文案。须在判定表通过后调用。
    """
    if skip:
        return SKIP_TEXT
    by_qid = {item['question_id']: item for item in answers or []}
    lines = []
    for qid, question in _questions(form).items():
        parts = [_option_label(question, key) for key in by_qid.get(qid, {}).get('selected_keys') or []]
        parts = [label for label in parts if label]
        text = ((by_qid.get(qid, {}).get('text') or '')).strip()
        if parts and text:
            content = '、'.join(parts) + '；自定义：' + text
        elif parts:
            content = '、'.join(parts)
        elif text:
            content = text
        else:
            content = UNANSWERED_TEXT
        tag = question.get('tag') or question.get('prompt') or qid
        lines.append(f"{qid.upper()}【{tag}】{content}")
    return '\n'.join(lines)


def _option_label(question, key):
    for option in question.get('options') or []:
        if isinstance(option, dict) and option.get('key') == key:
            return option.get('label') or key
    return None
