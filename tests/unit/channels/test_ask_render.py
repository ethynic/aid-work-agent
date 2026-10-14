"""ask_render 单元测试（WP5，设计 §7.2）。

覆盖：
- render_segments：逻辑行优先分段、单行超限字符边界跨消息（内容不丢）、
  续段题号/选项号前缀（选项行 ``Q1 选项2（续）``、题头行 ``Q1（续）``）、
  紧凑模式省 description、末行操作提示（allow_skip=false 口径）；
- render_single_message：kf/RPA 单条合并、超长依次精简 intro → 重复操作
  提示 → 选项 description（评审修正 §7.2）；
- parse_reply 三分法：自然语言 None、协议错误纠正、可解析结构化（选项序号→key、
  题号→question_id）、混合编号+文字、重复选项去重、否定句归自然语言、
  text 题冒号后内容一律作为文本（金额/年份等合法数字不再被拒）；
- 精确指令识别（跳过 / 重发 / 简化）。
"""

import re

import pytest

from src.channels import ask_render

pytestmark = pytest.mark.channels


def _form(**overrides):
    form = {
        "form_id": "frm_1",
        "title": "归档范围",
        "intro": "除成本治理外，以下开发已完成。",
        "allow_skip": True,
        "questions": [
            {
                "id": "q1",
                "tag": "归档范围",
                "prompt": "以下哪些任务移入 ideas_finished.md？",
                "type": "multi",
                "options": [
                    {"key": "q1o1", "label": "queue_order 序列自愈", "description": "代码+测试+CR 完成"},
                    {"key": "q1o2", "label": "wecom_kf 限频修复", "description": "已上线"},
                    {"key": "q1o3", "label": "飞书钉钉接入 AgentRunner", "description": ""},
                ],
            },
            {"id": "q2", "tag": "后续", "prompt": "补充说明？", "type": "text"},
        ],
    }
    form.update(overrides)
    return form


# ==================== render_segments ====================


def test_render_segments_logical_lines_and_footer():
    segments = ask_render.render_segments(_form(), 2048)
    assert len(segments) == 1
    text = segments[0]
    assert text.startswith("【归档范围】除成本治理外")
    assert 'Q1【归档范围】以下哪些任务移入 ideas_finished.md？（多选，回复编号如"1、3"；也可直接回复文字）' in text
    assert "1. queue_order 序列自愈 — 代码+测试+CR 完成" in text
    assert "2. wecom_kf 限频修复 — 已上线" in text
    assert '回复"跳过"将按已知信息继续' in text


def test_render_segments_footer_without_skip():
    segments = ask_render.render_segments(_form(allow_skip=False), 2048)
    assert "本问题需明确答复后任务才能继续" in segments[-1]
    assert "跳过" not in segments[-1]


def test_render_segments_multiple_by_limit_keeps_all_content():
    form = _form()
    segments = ask_render.render_segments(form, 200)
    assert len(segments) > 1
    joined = "\n".join(segments)
    for segment in segments:
        assert len(segment.encode("utf-8")) <= 200
    # 内容永不丢失：题干、选项、描述、操作提示全部可找回
    assert "queue_order 序列自愈" in joined
    assert "代码+测试+CR 完成" in joined
    assert "Q2【后续】补充说明？" in joined
    assert "回复编号或文字作答" in joined


def test_render_segments_oversized_line_splits_at_char_boundary():
    prompt = "超" + "长题干内容" * 200
    form = {
        "form_id": "frm_big",
        "questions": [{
            "id": "q1", "tag": "长文本", "prompt": prompt, "type": "single",
            "options": [{"key": "q1o1", "label": "选" + "项" * 400, "description": ""}],
        }],
    }
    limit = 300
    segments = ask_render.render_segments(form, limit)
    assert len(segments) > 1
    for segment in segments:
        assert len(segment.encode("utf-8")) <= limit
    # 每段都是合法 UTF-8（字符边界切分，无 mojibake）——encode/decode 往返即校验
    for segment in segments:
        segment.encode("utf-8").decode("utf-8")
    # 续段保留题号/选项号前缀（题头行 Q1（续）、选项行 Q1 选项1（续））
    assert any(segment.startswith("Q1（续）") for segment in segments[1:])
    assert any(segment.startswith("Q1 选项1（续）") for segment in segments[1:])
    # 题干/选项原文完整（分段拼接会在跨消息切点插入前缀标记与换行，
    # 去除前缀标记与换行后内容必须可完整找回）
    reassembled = "\n".join(segments)
    reassembled = re.sub(r"(?:Q\d+)?(?: 选项\d+)?（续）\n", "", reassembled)
    reassembled = reassembled.replace("\n", "")
    assert prompt in reassembled
    assert "选" + "项" * 400 in reassembled


def test_render_segments_option_row_break_carries_option_number():
    # 段间断点落在选项行上：新段以「Q1 选项2（续）」开头（选项号可定位）
    form = {
        "form_id": "frm_opt",
        "questions": [{
            "id": "q1", "tag": "选择", "prompt": "选择一项", "type": "single",
            "options": [{"key": "q1o1", "label": "一" * 60, "description": ""},
                        {"key": "q1o2", "label": "二" * 60, "description": ""}],
        }],
    }
    segments = ask_render.render_segments(form, 350)
    assert len(segments) > 1
    assert any(segment.startswith("Q1 选项2（续）\n2. " + "二" * 60)
               for segment in segments[1:])


def test_render_segments_compact_mode_drops_description_only():
    full = ask_render.render_segments(_form(), 2048)[0]
    compact = ask_render.render_segments(_form(), 2048, compact=True)[0]
    assert "代码+测试+CR 完成" in full
    assert "代码+测试+CR 完成" not in compact
    assert "queue_order 序列自愈" in compact  # label 保留
    assert len(compact) < len(full)


def test_render_segments_empty_form_returns_empty():
    assert ask_render.render_segments({"questions": []}, 2048) == []
    assert ask_render.render_segments({}, 2048) == []


# ==================== render_single_message（kf/RPA 单条合并，评审修正 §7.2） ====================


def _single_form():
    return {
        "form_id": "frm_s", "title": "采购意向",
        "intro": "请确认本次采购的意向范围，以便我们继续推进后续流程。",
        "allow_skip": True,
        "questions": [
            {"id": "q1", "tag": "范围", "prompt": "需要采购哪些设备？", "type": "multi",
             "options": [
                 {"key": "q1o1", "label": "服务器", "description": "用于承载核心业务系统与数据库"},
                 {"key": "q1o2", "label": "交换机", "description": "用于机房网络改造与万兆互联"},
             ]},
            {"id": "q2", "tag": "预算", "prompt": "预算大致范围？", "type": "text"},
        ],
    }


def _blen(text):
    return len(text.encode("utf-8"))


def test_render_single_message_merges_whole_form():
    text = ask_render.render_single_message(_single_form(), 65536)
    assert "【采购意向】" in text  # intro
    assert "（多选" in text  # 题头操作提示
    assert "—" in text  # 选项 description
    assert "回复编号或文字作答" in text  # 末尾操作提示
    # 与分段渲染的完整形态一致（单条 = 不分段的全量逻辑行）
    assert text == "\n".join(ask_render.render_segments(_single_form(), 65536))


def test_render_single_message_staged_compaction_order():
    # 依次精简：intro → 题头重复操作提示 → 选项 description；题干/选项/编号保留
    full = ask_render.render_single_message(_single_form(), 65536)

    no_intro = ask_render.render_single_message(_single_form(), _blen(full) - 1)
    assert "采购意向" not in no_intro  # intro 先精简
    assert "（多选" in no_intro and "—" in no_intro  # 提示与 description 保留

    no_hint = ask_render.render_single_message(_single_form(), _blen(no_intro) - 1)
    assert "（多选" not in no_hint  # 重复操作提示其次精简
    assert "—" in no_hint
    assert "Q1【范围】需要采购哪些设备？" in no_hint  # 题干保留

    no_desc = ask_render.render_single_message(_single_form(), _blen(no_hint) - 1)
    assert "—" not in no_desc  # description 最后精简
    # 问题、必要选项与清晰编号永不丢失
    assert "Q1【范围】需要采购哪些设备？" in no_desc
    assert "1. 服务器" in no_desc and "2. 交换机" in no_desc
    assert "Q2【预算】预算大致范围？" in no_desc
    assert "回复编号或文字作答" in no_desc


def test_render_single_message_still_over_limit_returns_minimal_text():
    # 全部精简后仍超限：返回最简文本，由 session 发送侧按渠道既有长文本方式兜底
    full = ask_render.render_single_message(_single_form(), 65536)
    no_intro = ask_render.render_single_message(_single_form(), _blen(full) - 1)
    no_hint = ask_render.render_single_message(_single_form(), _blen(no_intro) - 1)
    minimal = ask_render.render_single_message(_single_form(), _blen(no_hint) - 1)
    text = ask_render.render_single_message(_single_form(), 64)
    assert text == minimal and _blen(text) > 64


def test_render_single_message_empty_form():
    assert ask_render.render_single_message({"questions": []}, 2048) == ""


# ==================== parse_reply 三分法 ====================


def test_parse_natural_language_returns_none():
    assert ask_render.parse_reply("我觉得都可以，按默认处理", _form()) is None
    assert ask_render.parse_reply("不要继续了", _form()) is None
    assert ask_render.parse_reply("不要选1，我建议全部保留", _form()) is None


def test_parse_structured_answers_maps_numbers_to_keys():
    result = ask_render.parse_reply("Q1：1、3", _form())
    assert result == {"ok": True, "answers": [
        {"question_id": "q1", "selected_keys": ["q1o1", "q1o3"], "text": None},
    ]}


def test_parse_fullwidth_and_case_normalized():
    result = ask_render.parse_reply("q2：第二批等验证后", _form())
    assert result["ok"] is True
    assert result["answers"][0]["question_id"] == "q2"
    assert result["answers"][0]["text"] == "第二批等验证后"


def test_parse_mixed_numbers_and_text():
    result = ask_render.parse_reply("Q1：1、3 尽快处理\n验证通过后再看", _form())
    assert result["ok"] is True
    answer = result["answers"][0]
    assert answer["selected_keys"] == ["q1o1", "q1o3"]
    assert answer["text"] == "尽快处理\n验证通过后再看"  # 续行并入该题 text


def test_parse_duplicate_options_deduplicated():
    result = ask_render.parse_reply("Q1：1、1、3", _form())
    assert result["answers"][0]["selected_keys"] == ["q1o1", "q1o3"]


def test_parse_single_question_form_bare_numbers():
    single = _form(questions=_form()["questions"][:1])
    result = ask_render.parse_reply("1、3", single)
    assert result is not None and result["ok"] is True
    assert result["answers"][0]["selected_keys"] == ["q1o1", "q1o3"]


def test_parse_multi_question_bare_numbers_is_natural_language():
    # 多题表单下无题号编号无归属 → 自然语言（交 LLM 语义理解，不猜测）
    assert ask_render.parse_reply("1、3", _form()) is None


def test_parse_protocol_errors_reported_once():
    result = ask_render.parse_reply("Q9：1\nQ1：5", _form())
    assert result["ok"] is False
    assert any("Q9 不在当前问题中" in e for e in result["errors"])
    assert any("超出范围" in e for e in result["errors"])
    assert "回复格式未能识别" in result["correction"]


def test_parse_multidigit_question_number_is_unknown_not_misparsed():
    # 多位数题号（表单仅 q1/q2）必须报未知题号，不得静默拆成 q1+正文提交
    result = ask_render.parse_reply("Q12：abc", _form())
    assert result["ok"] is False
    assert any("Q12 不在当前问题中" in e for e in result["errors"])


def test_parse_text_question_accepts_numeric_answer_as_text():
    # 评审修正：text 题冒号后内容一律作为该题文本（金额/年份等合法数字不再
    # 被当选项编号拒绝）；纯编号语法同样按文本（text 题本无选项）
    for raw, expected in (("Q2：1000", "1000"), ("Q2：2026", "2026"), ("Q2：1、2", "1、2")):
        result = ask_render.parse_reply(raw, _form())
        assert result["ok"] is True, raw
        answer = result["answers"][0]
        assert answer["question_id"] == "q2"
        assert answer["selected_keys"] == []
        assert answer["text"] == expected


def test_parse_text_question_numeric_with_continuation_lines():
    result = ask_render.parse_reply("Q2：预算约 500 万元\n分两批支付", _form())
    assert result["ok"] is True
    assert result["answers"][0]["text"] == "预算约 500 万元\n分两批支付"


def test_parse_single_question_with_two_selections_is_error():
    form = _form(questions=[{**_form()["questions"][0], "type": "single"}])
    result = ask_render.parse_reply("Q1：1、2", form)
    assert result["ok"] is False
    assert any("单选" in e for e in result["errors"])


def test_parse_contradictory_duplicate_question_is_error():
    result = ask_render.parse_reply("Q1：1\nQ1：2", _form())
    assert result["ok"] is False
    assert any("矛盾" in e for e in result["errors"])


def test_parse_line_before_first_question_unattachable_is_error():
    # 首个题号行之前的非空行无法归属任何题目：整单拒绝（不静默丢弃）
    result = ask_render.parse_reply("先说明一下背景\nQ1：1", _form())
    assert result["ok"] is False


# ==================== 精确指令识别 ====================


def test_exact_instruction_tokens():
    assert ask_render.is_skip_reply("跳过")
    assert ask_render.is_skip_reply(" 跳过。")
    assert not ask_render.is_skip_reply("跳过这个问题")
    assert ask_render.is_resend_reply("重发")
    assert ask_render.is_resend_reply("简化")
    assert ask_render.is_resend_reply("重新发送！")
    assert not ask_render.is_resend_reply("重发一下剩余内容吧")


def test_submission_error_text_from_422_details():
    text = ask_render.submission_error_text([
        {"question_id": "q2", "code": "FORM_ANSWER_INVALID", "message": "该题为必答"},
        {"question_id": "form", "code": "FORM_SKIP_NOT_ALLOWED", "message": "该表单不允许整体跳过"},
    ])
    assert "Q2：该题为必答" in text
    assert "该表单不允许整体跳过" in text
    assert "q2" not in text.replace("Q2", "")  # 题号统一大写展示
