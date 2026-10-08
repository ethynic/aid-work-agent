"""
qwen3.7-flash 上下文缓存优化 Phase 2「工具结果截断」测试

背景：单次 agent 运行 prompt 可膨胀到 244K（239K 是历史 + 工具结果，billing record 5551）。
Phase 1（显式末尾标记缓存）已落地，缓存未命中时按 100% 计费，截断超长工具结果可直接降低输入基数。
设计决策（用户 2026-08-17 确认）：单条工具结果 >12000 字符才截断，截断后保留前 6000 + 后 2000 + 省略标记；
所有工具超阈值即截（不限定白名单）。

33f9dba1 重构后的接入点结构（本文件据此改写）：
1. _truncate_tool_content 函数边界（阈值 / 保头保尾 / 省略标记内容 / 精确边界 / 短尾越界 / 自定义参数）
   —— 纯函数，直接单测（test_agent_events.py 另有绿的单测，互不重叠）。
2. 引擎接入点：主/子智能体共用 AgentEngine 单一接入点（engine.py「工具结果写入 messages」处
   _truncate_tool_content，result.preserve_content 豁免）。preserve_content 由两处决定：
   - ToolDispatcher 普通工具路径：result.pop("_no_truncate")（tools.py；现行真实消费者是
     knowledge_base_tool / knowledge_file_search_tool 等普通注册工具）；
   - ControlToolAdapter：use_skill 专属豁免 + 控制工具结果 _no_truncate 键同样
     pop 生效（controls.py，见 TestControlToolNoTruncateExemption）。
   经 tests/unit/_agent_runtime_seam.py 以「真实 AgentEngine + 真实 ToolDispatcher /
   ControlToolAdapter + 假 registry/executor/model」驱动（与 test_agent_engine_acceptance.py
   同款 fake port 模式）。
3. skill_execute 工具把 stdout JSON 中声明的 _no_truncate 提升到返回结果顶层
   （TestSkillExecuteNoTruncateLift，工具级直测，保持原样）。

原「主智能体 / 子智能体两个接入点」测试类已合并：新架构中 master（_process_message_impl →
RuntimeExecution → AgentEngine）与 subagent（execute_as_subagent → 同一 execution.run）
共用同一条引擎路径，截断行为与 role 无关。
"""

import json

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.core.agent import _truncate_tool_content
from src.core.agent_engine import Outcome
from tests.unit._agent_runtime_seam import (
    FakeTool,
    Model,
    call,
    completion,
    execution_state,
    make_dispatcher,
    plan_manager_with_pending,
    run_engine,
    tool_messages_of,
    use_skill_control_tools,
)


pytestmark = pytest.mark.agent


# ---------------------------------------------------------------- 函数边界

class TestTruncateFunction:
    def test_below_threshold_not_truncated(self):
        """len(content) <= 12000 不截断，返回原串且无省略标记。"""
        content = "x" * 12000
        result = _truncate_tool_content(content)
        assert result == content
        assert "已截断" not in result

    def test_exact_threshold_not_truncated(self):
        """精确边界 12000 字符不截。"""
        content = "y" * 12000
        assert _truncate_tool_content(content) == content

    def test_over_threshold_truncated(self):
        """>12000 字符截断：长度变小，含省略标记。"""
        content = "z" * 20000
        result = _truncate_tool_content(content)
        assert len(result) < len(content)
        assert "已截断" in result
        assert "省略" in result

    def test_12001_truncates(self):
        """精确边界+1：12001 字符必须截。"""
        content = "w" * 12001
        result = _truncate_tool_content(content)
        assert "已截断" in result
        assert len(result) < len(content)

    def test_head_and_tail_preserved(self):
        """保头保尾：开头 == 原串前 6000，结尾 == 原串后 2000。"""
        # 前 6000 是 A，中间 10000 是 M，后 2000 是 B（总长 18000）
        content = "A" * 6000 + "M" * 10000 + "B" * 2000
        result = _truncate_tool_content(content)
        assert result.startswith("A" * 6000)
        assert result.endswith("B" * 2000)

    def test_omission_marker_counts(self):
        """省略标记含正确总数与省略数：共 len(content) 字符，省略 len - 6000 - 2000。"""
        content = "q" * 15000
        result = _truncate_tool_content(content)
        assert f"共 {len(content)} 字符" in result
        assert f"省略 {len(content) - 6000 - 2000} 字符" in result

    def test_short_tail_no_index_error(self):
        """短尾部边界：content 仅略超阈值时尾部 2000 截取正确（无越界/负数）。"""
        content = "p" * 12005  # 只超 5 字符
        result = _truncate_tool_content(content)
        assert result.startswith("p" * 6000)
        assert result.endswith("p" * 2000)
        assert "已截断" in result
        assert "省略 4005 字符" in result  # 12005 - 6000 - 2000

    def test_custom_params_respected(self):
        """参数化阈值：自定义 threshold/max_chars/head_chars 生效。"""
        content = "r" * 1000
        result = _truncate_tool_content(
            content, max_chars=400, head_chars=300, threshold=500
        )
        assert result.startswith("r" * 300)
        assert result.endswith("r" * 100)
        assert "已截断" in result
        assert "省略 600 字符" in result  # 1000 - 300 - 100


# ------------------------------------------------------------ 引擎接入点

class TestEngineToolResultTruncation:
    """AgentEngine 工具结果接入点（engine.py，master/subagent 共用单一接入点）。

    驱动方式：真实 AgentEngine + 真实 ToolDispatcher（普通工具经 executor 替身返回
    结果），从第二轮 LLM 调用收到的 messages 断言 role=tool 消息内容。
    """

    async def test_dict_content_truncated(self):
        """dict 超大 content：先 json.dumps(ensure_ascii=False) 再截断（非 str(dict) 的 Python 表示）。"""
        big_result = {"success": True, "content": "x" * 30000, "summary": "读取结果"}
        h = make_dispatcher(tools=[FakeTool("read")], executor_results=[big_result])
        model = Model(completion("", [call("c1")]), completion("完成"))
        state = execution_state()

        events = await run_engine(state, model, h.dispatcher)

        assert state.outcome == Outcome.COMPLETED
        tool_msgs = tool_messages_of(model)
        assert len(tool_msgs) == 1
        assert "已截断" in tool_msgs[0]["content"], "超大工具结果应被截断"
        # dict 被 json.dumps 后传入截断（dumps 输出不带空格、双引号，区别于 Python repr）
        expected_dumped = json.dumps(big_result, ensure_ascii=False)
        assert expected_dumped.startswith('{"success": true')
        assert tool_msgs[0]["content"] == _truncate_tool_content(expected_dumped)

    async def test_short_dict_content_not_truncated(self):
        """短 dict content：json.dumps 后长度未超阈值，不截断、无省略标记。"""
        short_result = {"success": True, "content": "ok"}
        h = make_dispatcher(tools=[FakeTool("read")], executor_results=[short_result])
        model = Model(completion("", [call("c1")]), completion("完成"))

        events = await run_engine(execution_state(), model, h.dispatcher)

        tool_msgs = tool_messages_of(model)
        assert len(tool_msgs) == 1
        assert "已截断" not in tool_msgs[0]["content"]
        assert tool_msgs[0]["content"] == json.dumps(short_result, ensure_ascii=False)

    async def test_str_content_truncated(self):
        """str 超大 content：直接截断。"""
        big_str = "y" * 20000
        h = make_dispatcher(tools=[FakeTool("read")], executor_results=[big_str])
        model = Model(completion("", [call("c1")]), completion("完成"))

        events = await run_engine(execution_state(), model, h.dispatcher)

        tool_msgs = tool_messages_of(model)
        assert len(tool_msgs) == 1
        assert "已截断" in tool_msgs[0]["content"]
        assert tool_msgs[0]["content"] == _truncate_tool_content(big_str)

    async def test_dict_no_truncate_flag_not_truncated(self):
        """普通工具经返回 dict 中 _no_truncate: True 声明"文档型输出不截断"：
        超长内容豁免截断，且内部键被 pop，不进入给 LLM 的 JSON。
        （现行真实消费者：knowledge_base_tool / knowledge_file_search_tool 检索结果，
        ToolDispatcher 普通工具路径负责 pop。）
        """
        big_result = {
            "success": True,
            "content": "x" * 30000,
            "configured": True,
            "_no_truncate": True,
        }
        h = make_dispatcher(tools=[FakeTool("read")], executor_results=[big_result])
        model = Model(completion("", [call("c1")]), completion("完成"))

        events = await run_engine(execution_state(), model, h.dispatcher)

        tool_msgs = tool_messages_of(model)
        assert len(tool_msgs) == 1
        assert "已截断" not in tool_msgs[0]["content"], "声明 _no_truncate 的超长工具结果应豁免截断"
        # 完整 JSON（不含 _no_truncate 内部键）原样传给 LLM
        expected = json.dumps(
            {"success": True, "content": "x" * 30000, "configured": True},
            ensure_ascii=False,
        )
        assert tool_msgs[0]["content"] == expected
        assert "_no_truncate" not in tool_msgs[0]["content"], "内部标记键不应进入给 LLM 的 JSON"

    async def test_use_skill_guide_not_truncated(self):
        """use_skill 加载的技能指南（超 12K 大技能）豁免截断：
        skill_version 解析链路依赖完整 JSON，且指南须完整喂给 LLM。
        接入点为 ControlToolAdapter（controls.py preserve_content = use_skill 专属）。
        """
        big_guide = "G" * 20000  # 超阈值大技能指南
        skill_result = {
            "success": True,
            "skill_name": "guizang-ppt",
            "skill_version": "1.0.0",
            "content": big_guide,
            "message": "✅ Skill 'guizang-ppt' (v1.0.0) loaded.",
        }
        h = make_dispatcher(
            extra_definitions=[{"name": "use_skill"}],
            executor_results=[None],  # 控制工具路径，不经 executor
            control_tools=use_skill_control_tools(skill_result),
        )
        model = Model(
            completion("", [call("c1", "use_skill", {"skill": "guizang-ppt"})]),
            completion("完成"),
        )
        state = execution_state()

        events = await run_engine(state, model, h.dispatcher)

        tool_msgs = tool_messages_of(model)
        assert len(tool_msgs) == 1
        expected = json.dumps(skill_result, ensure_ascii=False)
        assert tool_msgs[0]["content"] == expected, "use_skill 技能指南必须完整（豁免截断）"
        assert "已截断" not in tool_msgs[0]["content"]
        # 完整 JSON 可被解析出 skill_version（skill_execute 版本校验链路依赖）
        assert json.loads(tool_msgs[0]["content"])["skill_version"] == "1.0.0"
        assert state.loaded_skills == {"guizang-ppt": "1.0.0"}

    async def test_ordinary_tool_emits_one_standard_result_without_preview(self):
        """普通工具只执行一次；标准事件携带调用标识，正文仅由最终 LLM 回复。"""
        raw_result = {"success": True, "content": "工具产生的原始正文"}
        h = make_dispatcher(tools=[FakeTool("read")], executor_results=[raw_result])
        model = Model(completion("", [call("c1")]), completion("完成"))

        events = await run_engine(execution_state(), model, h.dispatcher)

        assert len(h.executor.calls) == 1
        starts = [event for event in events if event.get("type") == "tool_start"]
        results = [event for event in events if event.get("type") == "tool_result"]
        responses = [event.get("data") for event in events if event.get("type") == "response"]
        assert len(starts) == 1
        assert starts[0]["toolCallId"] == "c1"
        assert starts[0]["displayName"] == "read"
        assert len(results) == 1
        assert results[0]["result"] == raw_result
        assert results[0]["toolCallId"] == "c1"
        assert results[0]["displayName"] == "read"
        assert responses == ["完成"]
        assert not any("工具产生的原始正文" in str(event.get("data", "")) for event in events)

    async def test_tool_start_exposes_masked_args_but_executor_receives_raw(self):
        """事件源只携带脱敏后的参数副本（敏感键掩码），原始参数仍原样进入执行器。"""
        h = make_dispatcher(tools=[FakeTool("read")], executor_results=[{"success": True}])
        model = Model(
            completion("", [call("secret-call", "read",
                                 {"file_path": "sensitive/path.txt", "token": "raw-tok"})]),
            completion("完成"),
        )

        events = await run_engine(execution_state(), model, h.dispatcher)

        start = next(event for event in events if event.get("type") == "tool_start")
        # 事件源持脱敏副本：敏感键被掩码，普通键原样透传
        assert start["toolArgs"] == {"file_path": "sensitive/path.txt", "token": "***"}
        # 执行器仍收到原始参数，功能不受影响
        name, args, _context = h.executor.calls[0]
        assert (name, args) == ("read", {"file_path": "sensitive/path.txt", "token": "raw-tok"})

    async def test_scheduled_tool_uses_generic_plan_tracking(self):
        """定时工具不再走提前旁路，统一执行并完成当前计划任务。"""
        result = {"success": True, "name": "日报", "message": "已创建"}
        plan_manager = plan_manager_with_pending(task_id="task-1")
        tool = FakeTool("create_scheduled_task", display_name="创建定时任务")
        h = make_dispatcher(tools=[tool], executor_results=[result],
                            plan_manager=plan_manager)
        model = Model(
            completion("", [call("c1", "create_scheduled_task")]),
            completion("完成"),
        )
        state = execution_state(session_id="sess")

        events = await run_engine(state, model, h.dispatcher)

        assert len(h.executor.calls) == 1
        plan_manager.mark_task_running.assert_called_once_with("sess", "task-1")
        plan_manager.mark_task_completed.assert_called_once_with("sess", "task-1", result)
        tool_results = [event for event in events if event.get("type") == "tool_result"]
        assert len(tool_results) == 1
        assert tool_results[0]["result"] == result

        # 执行上下文派生自调用标识（真实链路中 session 由 RuntimeExecution 的基础上下文携带）
        derive_kwargs = h.control_tools.context.derived[0]
        assert derive_kwargs["tool_call_id"] == "c1"
        assert derive_kwargs["agent_execution_id"] == state.execution_id
        # 计划绑定按 state.identity.session_id 查询
        plan_manager.get_plan.assert_called_with("sess")

    async def test_failed_scheduled_tool_marks_plan_failed_and_enters_llm(self):
        """管理定时任务失败也走通用链：执行一次、失败计划、结果进入下一轮 LLM。"""
        result = {"success": False, "error": "无权管理该任务"}
        plan_manager = plan_manager_with_pending(task_id="task-2")
        tool = FakeTool("manage_scheduled_task", display_name="管理定时任务")
        h = make_dispatcher(tools=[tool], executor_results=[result],
                            plan_manager=plan_manager)
        model = Model(
            completion("", [call("scheduled-fail", "manage_scheduled_task")]),
            completion("失败说明"),
        )

        events = await run_engine(execution_state(session_id="sess"), model, h.dispatcher)

        assert len(h.executor.calls) == 1
        plan_manager.mark_task_completed.assert_not_called()
        plan_manager.mark_task_failed.assert_called_once_with(
            "sess", "task-2", "无权管理该任务"
        )
        result_event = next(event for event in events if event.get("type") == "tool_result")
        assert result_event["success"] is False
        assert result_event["toolCallId"] == "scheduled-fail"
        tool_msgs = tool_messages_of(model)
        assert json.loads(tool_msgs[0]["content"]) == result

    async def test_same_name_calls_are_correlated_only_by_tool_call_id(self):
        """同一轮两个同名调用分别透传 ID 和结果，不依赖工具名缓存关联。"""
        h = make_dispatcher(
            tools=[FakeTool("read")],
            executor_results=[
                {"success": True, "content": "first"},
                {"success": True, "content": "second"},
            ],
        )
        model = Model(
            completion("", [call("same-1", "read", {"file_path": "a"}),
                            call("same-2", "read", {"file_path": "b"})]),
            completion("完成"),
        )

        events = await run_engine(execution_state(), model, h.dispatcher)

        starts = [event for event in events if event.get("type") == "tool_start"]
        results = [event for event in events if event.get("type") == "tool_result"]
        assert [event["toolCallId"] for event in starts] == ["same-1", "same-2"]
        assert [event["toolCallId"] for event in results] == ["same-1", "same-2"]
        assert [event["result"]["content"] for event in results] == ["first", "second"]
        tool_msgs = tool_messages_of(model)
        assert [message["tool_call_id"] for message in tool_msgs] == ["same-1", "same-2"]
        assert json.loads(tool_msgs[0]["content"])["content"] == "first"
        assert json.loads(tool_msgs[1]["content"])["content"] == "second"


# ------------------------------------------------------- skill_execute 声明提升

class TestSkillExecuteNoTruncateLift:
    """skill_execute 工具把脚本 stdout JSON 中声明的 _no_truncate 提升到返回结果顶层。

    travel-quote 场景：generate.py / update_hotel.py 输出的 rows（LLM 复述依据）+ internal_data
    （须原样回传给 update_hotel.py）超 12K 时不得截断；load_api_config 的 API 说明文档同理。
    脚本只需在 stdout JSON 中声明 _no_truncate，工具负责翻译给 agent 截断逻辑识别。
    """

    @staticmethod
    def _make_executor(stdout: str):
        result = MagicMock()
        for k, v in [("success", True), ("exit_code", 0), ("duration", 0.1),
                     ("timed_out", False), ("error", None), ("stderr", "")]:
            setattr(result, k, v)
        result.stdout = stdout
        executor = MagicMock()
        executor.execute_skill_command = AsyncMock(return_value=result)
        return executor

    async def _run_tool(self, stdout: str) -> dict:
        from src.tools.skill.skill_execute_tool import SkillExecuteTool
        registry = MagicMock()
        registry.get.return_value = MagicMock()
        tool = SkillExecuteTool(self._make_executor(stdout), registry)
        return await tool.execute(
            skill="travel-quote",
            command="python scripts/generate.py",
            user_id="u1",  # 传 user_id 规避 execute 内的 SessionDB 查询
        )

    async def test_declared_no_truncate_lifted_to_top_level(self):
        """脚本 stdout JSON 声明 _no_truncate -> 提升到返回结果顶层，stdout 原样保留。"""
        big_content = "A" * 30000
        stdout = json.dumps({
            "success": True,
            "data": {"rows": [{"费用小计": 100}], "internal_data": {"items": big_content}},
            "_no_truncate": True,
        }, ensure_ascii=False)
        resp = await self._run_tool(stdout)
        assert resp["_no_truncate"] is True, "脚本声明 _no_truncate 应提升到返回顶层"
        assert resp["stdout"] == stdout, "stdout 应原样保留"

    async def test_no_declaration_not_lifted(self):
        """脚本 stdout JSON 无声明 -> 返回结果不带 _no_truncate 键。"""
        resp = await self._run_tool(json.dumps({"success": True, "data": {"ok": 1}}, ensure_ascii=False))
        assert "_no_truncate" not in resp

    async def test_non_json_stdout_not_lifted(self):
        """脚本 stdout 非 JSON（普通文本/错误输出）-> 不加键。"""
        resp = await self._run_tool("plain text output")
        assert "_no_truncate" not in resp

    async def test_no_truncate_flag_lifted_preserved(self):
        """提升后的 _no_truncate 标记保留在返回结果顶层，供 agent 截断逻辑识别。"""
        stdout = json.dumps({"success": True, "data": {"x": "y" * 20000}, "_no_truncate": True}, ensure_ascii=False)
        resp = await self._run_tool(stdout)
        assert resp.get("_no_truncate") is True


class TestControlToolNoTruncateExemption:
    """ControlToolAdapter 通用豁免（controls.py）：控制工具结果顶层声明的
    _no_truncate 同样生效（与 tools.py 普通工具路径对齐），且该键不残留进 LLM JSON。

    真实消费者：skill_execute（stdout JSON 中声明的 _no_truncate 被工具提升到
    返回结果顶层，经控制工具路径到达本接入点）。
    """

    async def test_skill_execute_no_truncate_exempt_and_key_popped(self):
        from types import SimpleNamespace

        from src.services.agent_runner.runtime.controls import ControlToolAdapter
        from tests.unit._agent_runtime_seam import FakeToolContext, no_plan_manager

        big_stdout = "x" * 15000

        async def _execute(**kwargs):
            return {"success": True, "stdout": big_stdout, "_no_truncate": True}

        adapter = ControlToolAdapter(
            controls={"skill_execute": SimpleNamespace(execute=_execute)},
            skills=MagicMock(check=MagicMock(return_value=None)),
            registry=SimpleNamespace(get=lambda name: None),
            plan_manager=no_plan_manager(),
            clarification_store=MagicMock(),
            context=FakeToolContext(), user_input="执行技能",
        )
        h = make_dispatcher(
            extra_definitions=[{"name": "skill_execute"}],
            executor_results=[None],
            control_tools=adapter,
        )
        model = Model(
            completion("", [call("c1", "skill_execute", {"skill": "s", "command": "ls"})]),
            completion("完成"),
        )

        await run_engine(execution_state(), model, h.dispatcher)

        tool_msgs = tool_messages_of(model)
        assert len(tool_msgs) == 1
        assert "已截断" not in tool_msgs[0]["content"], "声明 _no_truncate 的控制工具结果不应截断"
        payload = json.loads(tool_msgs[0]["content"])
        assert payload["stdout"] == big_stdout
        assert "_no_truncate" not in payload, "豁免标记键不得残留进 LLM 内容"

    async def test_control_tool_non_dict_result_no_crash(self):
        """控制工具返回非 dict（str）：不触发 pop、正常下发不截断路径。"""
        from types import SimpleNamespace

        from src.services.agent_runner.runtime.controls import ControlToolAdapter
        from tests.unit._agent_runtime_seam import FakeToolContext, no_plan_manager

        async def _execute(**kwargs):
            return "plain-text-result"

        adapter = ControlToolAdapter(
            controls={"skill_complete_probe": SimpleNamespace(execute=_execute)},
            skills=MagicMock(),
            registry=SimpleNamespace(get=lambda name: None),
            plan_manager=no_plan_manager(),
            clarification_store=MagicMock(),
            context=FakeToolContext(), user_input="探测",
        )
        h = make_dispatcher(
            extra_definitions=[{"name": "skill_complete_probe"}],
            executor_results=[None],
            control_tools=adapter,
        )
        model = Model(
            completion("", [call("c1", "skill_complete_probe", {})]),
            completion("完成"),
        )

        await run_engine(execution_state(), model, h.dispatcher)

        tool_msgs = tool_messages_of(model)
        assert len(tool_msgs) == 1
        assert tool_msgs[0]["content"] == "plain-text-result"
