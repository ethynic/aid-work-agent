"""ask_render 单元测试（WP5，设计 §7.2）。

覆盖：
- render_segments：逻辑行优先分段、单行超限字符边界跨消息（内容不丢）、
  续段题号前缀、紧凑模式省 description、末行操作提示（allow_skip=false 口径）；
- parse_reply 三分法：自然语言 None、协议错误纠正、可解析结构化（选项序号→key、
  题号→question_id）、混合编号+文字、重复选项去重、否定句归自然语言；
- 精确指令识别（跳过 / 重发 / 简化）。
"""

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
    # 续段保留题号前缀，且题干/选项原文完整（分段拼接会在跨消息切点插入换行，
    # 去除前缀标记与换行后内容必须可完整找回）
    assert any(segment.startswith("Q1（续）") for segment in segments[1:])
    reassembled = "\n".join(segments).replace("Q1（续）\n", "") \
        .replace("（续）\n", "").replace("\n", "")
    assert prompt in reassembled
    assert "选" + "项" * 400 in reassembled


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


def test_parse_text_question_with_selection_is_protocol_error():
    result = ask_render.parse_reply("Q2：1、2", _form())
    assert result["ok"] is False
    assert any("自由文本" in e for e in result["errors"])


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
