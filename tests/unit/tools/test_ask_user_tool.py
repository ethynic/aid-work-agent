"""AskUserTool 单元测试：硬校验、服务端 id 生成与 AskForm 字段形状。

docs/system/design-ask-user-interactive-question.md §4.1：
- 硬校验失败返回 {"success": False, "error"}：questions 1~5 题、题型与选项匹配
  （single/multi 缺 options、text 带 options）、选项数 2~8、选项 label 非空、
  max_select 2..选项数、总载荷 ≤8KB（UTF-8 字节）；
- form_id/question_id/option key 由服务端按序生成，入参不含任何 id 字段；
- allow_skip 缺省 True、expires_at 恒为 null；文案长度是建议而非拒绝门。
"""

import json

import pytest

from src.tools.agent.ask_user_tool import MAX_FORM_BYTES, AskUserTool

pytestmark = pytest.mark.unit


def question(**overrides):
    base = {"prompt": "以下哪些任务移入 ideas_finished.md？", "type": "multi",
            "options": [{"label": "选项一"}, {"label": "选项二"}]}
    base.update(overrides)
    return base


async def execute_ok(arguments):
    result = await AskUserTool().execute(**arguments)
    assert result["success"] is True, result
    return result["form"]


async def execute_error(arguments):
    result = await AskUserTool().execute(**arguments)
    assert result["success"] is False
    assert isinstance(result.get("error"), str) and result["error"]
    return result["error"]


class TestServerGeneratedIds:
    async def test_generates_form_id_question_ids_and_option_keys(self):
        form = await execute_ok({"questions": [
            question(),
            question(prompt="选一个", type="single",
                     options=[{"label": "甲"}, {"label": "乙"}, {"label": "丙"}]),
        ]})
        assert form["form_id"].startswith("frm_")
        assert [item["id"] for item in form["questions"]] == ["q1", "q2"]
        assert [option["key"] for option in form["questions"][0]["options"]] == ["q1o1", "q1o2"]
        assert [option["key"] for option in form["questions"][1]["options"]] == ["q2o1", "q2o2", "q2o3"]
        # id 全表单唯一且符合 wire schema 约定
        keys = [option["key"] for item in form["questions"] for option in item["options"]]
        assert len(keys) == len(set(keys))

    async def test_caller_supplied_ids_are_never_echoed(self):
        """入参不含任何 id 字段：客户端即使塞入 id 也以服务端生成为准。"""
        form = await execute_ok({"questions": [
            question(options=[{"key": "evil", "label": "A"}, {"label": "B"}])]})
        assert form["form_id"] != "evil"
        assert all(option["key"].startswith("q1o") for option in form["questions"][0]["options"])

    async def test_five_questions_boundary_accepted_and_six_rejected(self):
        five = {"questions": [question(prompt=f"问题{i}", options=[{"label": "A"}, {"label": "B"}])
                              for i in range(1, 6)]}
        form = await execute_ok(five)
        assert [item["id"] for item in form["questions"]] == ["q1", "q2", "q3", "q4", "q5"]
        six = dict(five, questions=five["questions"] + [question(prompt="问题6")])
        assert "1~5" in await execute_error(six)

    async def test_allow_skip_defaults_true_and_expires_at_is_null(self):
        form = await execute_ok({"questions": [question()]})
        assert form["allow_skip"] is True and form["expires_at"] is None
        form = await execute_ok({"questions": [question()], "allow_skip": False})
        assert form["allow_skip"] is False

    async def test_optional_fields_only_emitted_when_supplied(self):
        form = await execute_ok({"questions": [question()]})
        assert "title" not in form and "intro" not in form
        item = form["questions"][0]
        assert "tag" not in item and "required" not in item
        assert item["allow_custom_text"] is True
        assert all("description" not in option for option in item["options"])
        assert "max_select" not in item

    async def test_multi_max_select_and_single_normalized(self):
        form = await execute_ok({"questions": [question(max_select=2)]})
        assert form["questions"][0]["max_select"] == 2
        # single 恒为 1：max_select 不落入输出
        form = await execute_ok({"questions": [
            question(type="single", max_select=2,
                     options=[{"label": "A"}, {"label": "B"}])]})
        assert "max_select" not in form["questions"][0]


class TestHardValidation:
    async def test_questions_shape(self):
        assert "1~5" in await execute_error({})
        assert "1~5" in await execute_error({"questions": []})
        assert "非列表" in await execute_error({"questions": "two"})

    async def test_prompt_required(self):
        assert "prompt" in await execute_error({"questions": [{"type": "text"}]})
        assert "prompt" in await execute_error({"questions": [question(prompt="   ")]})

    async def test_type_must_be_declared(self):
        assert "single/multi/text" in await execute_error({"questions": [{"prompt": "无题型"}]})
        assert "single/multi/text" in await execute_error(
            {"questions": [question(type="checkbox")]})

    async def test_choice_questions_need_two_to_eight_options(self):
        assert "2~8" in await execute_error({"questions": [question(options=None)]})
        assert "2~8" in await execute_error({"questions": [question(options=[])]})
        assert "2~8" in await execute_error(
            {"questions": [question(options=[{"label": "唯一"}])]})
        assert "2~8" in await execute_error(
            {"questions": [question(options=[{"label": str(n)} for n in range(9)])]})

    async def test_option_label_required(self):
        assert "label" in await execute_error(
            {"questions": [question(options=[{"label": " "}, {"label": "B"}])]})
        assert "label" in await execute_error(
            {"questions": [question(options=["A", "B"])]})

    async def test_text_question_forbids_options(self):
        error = await execute_error(
            {"questions": [question(type="text", options=[{"label": "A"}, {"label": "B"}])]})
        assert "text" in error and "options" in error

    async def test_text_question_allows_placeholder_without_options(self):
        form = await execute_ok({"questions": [
            question(type="text", options=None, placeholder="输入你的回答…")]})
        assert form["questions"][0]["placeholder"] == "输入你的回答…"
        assert "options" not in form["questions"][0]

    async def test_max_select_bounds(self):
        assert "max_select" in await execute_error(
            {"questions": [question(max_select=1)]})
        assert "max_select" in await execute_error(
            {"questions": [question(max_select=3)]})
        assert "max_select" in await execute_error(
            {"questions": [question(max_select=True)]})

    async def test_payload_over_8kb_rejected(self):
        error = await execute_error({"questions": [question(prompt="字" * (MAX_FORM_BYTES + 1))]})
        assert "8192" in error

    async def test_payload_at_limit_accepted(self):
        # 8KB 限的是 UTF-8 字节总量，中文题干约 3 字节/字：恰好在界内应通过
        form = await execute_ok({"questions": [question(prompt="字" * 2500)]})
        payload = len(json.dumps(form, ensure_ascii=False).encode("utf-8"))
        assert 7000 < payload <= MAX_FORM_BYTES


class TestAssembly:
    def test_control_set_carries_ask_user_definition_and_guide(self):
        from types import SimpleNamespace

        from src.tools.control_set import ControlToolDependencies, ToolControlSet
        skill_registry = SimpleNamespace(
            get_skill_tool_definition=lambda: {"name": "use_skill", "input_schema": {}})
        controls = ToolControlSet(ControlToolDependencies(
            plan_manager=None, skill_registry=skill_registry,
            skill_executor=None, tool_registry=None))
        names = [definition["name"] for definition in controls.definitions(
            available_subagents=None)]
        assert "ask_user" in names
        assert "present_options" not in names
        guides = controls.usage_guides()
        assert "### ask_user" in guides
        assert "present_options" not in guides and "clarify" in guides
        tool = controls.get("ask_user")
        assert tool is not None and tool.catalog is False
        assert "阻塞" in tool.usage_guide
