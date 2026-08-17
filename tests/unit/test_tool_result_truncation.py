"""
qwen3.7-flash 上下文缓存优化 Phase 2「工具结果截断」测试

背景：单次 agent 运行 prompt 可膨胀到 244K（239K 是历史 + 工具结果，billing record 5551）。
Phase 1（显式末尾标记缓存）已落地，缓存未命中时按 100% 计费，截断超长工具结果可直接降低输入基数。
设计决策（用户 2026-08-17 确认）：单条工具结果 >12000 字符才截断，截断后保留前 6000 + 后 2000 + 省略标记；
所有工具超阈值即截（不限定白名单）；主智能体（agent.py 主循环）与子智能体（execute_as_subagent）两处都截。

覆盖：
1. _truncate_tool_content 函数边界（阈值 / 保头保尾 / 省略标记内容 / 精确边界 / 短尾越界 / 自定义参数）
2. 主智能体工具结果接入点：dict content 被 json.dumps(ensure_ascii=False) 后截断，短内容不截断，str 直接截断
3. 子智能体工具结果接入点：dict 被 json.dumps 后截断，短内容不截断

接入点验证方式：构造轻量 Agent 实例（Agent.__new__，跳过 __init__ 的 master_agent 副作用）+ monkeypatch
必要依赖，真实驱动 _process_message_impl / execute_as_subagent 走到「工具结果写入 messages」处，从第二轮
LLM 调用收到的 messages 中断言 role=tool 消息内容已被截断。
"""

import json

import pytest
from unittest.mock import AsyncMock, MagicMock

import src.core.agent as agent_module
from src.core.agent import Agent, AgentMode, _truncate_tool_content


pytestmark = pytest.mark.agent


def _spy_truncate(monkeypatch):
    """将模块级 _truncate_tool_content 替换为 spy（wrap 原函数），返回记录调用参数的 list。"""
    calls = []
    real_fn = agent_module._truncate_tool_content

    def spy(content, *args, **kwargs):
        calls.append(content)
        return real_fn(content, *args, **kwargs)

    monkeypatch.setattr(agent_module, "_truncate_tool_content", spy)
    return calls


def _tool_msg_from_llm_call(llm, call_index):
    """从第 call_index 次 LLM 调用的入参 messages 中提取 role=tool 消息。"""
    assert len(llm.chat_with_tools.await_args_list) > call_index, (
        f"LLM 调用次数不足（需要第 {call_index + 1} 次）"
    )
    call_args = llm.chat_with_tools.await_args_list[call_index]
    msgs = call_args.kwargs.get("messages")
    if msgs is None and call_args.args:
        msgs = call_args.args[1]
    return [m for m in msgs if m.get("role") == "tool"]


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


# ---------------------------------------------------------- 主智能体接入点

class TestMasterAgentToolResultTruncation:
    """主智能体 _process_message_impl 工具结果接入点（agent.py 主循环）。"""

    def _make_agent(self, monkeypatch, tool_result_content):
        """构造轻量 Agent 实例 + patch 必要依赖。

        tool_result_content 是 tool_executor.execute 的返回值（作为工具结果 content 写入 messages）。
        """
        monkeypatch.setattr(Agent, "_get_pending_clarification", lambda self, sid: None)
        monkeypatch.setattr(Agent, "_ensure_tenant_skills_loaded", lambda self: None)
        monkeypatch.setattr(
            Agent, "_run_compression_phase", AsyncMock(return_value=None)
        )
        monkeypatch.setattr(
            Agent, "_handle_remember_intent", AsyncMock(return_value=None)
        )
        monkeypatch.setattr(Agent, "_build_messages", lambda self, sid: ([], []))
        monkeypatch.setattr(
            Agent, "_build_system_prompt", lambda self, *a, **k: "system"
        )
        monkeypatch.setattr(Agent, "_get_tools", lambda self: [{"name": "read"}])
        monkeypatch.setattr(
            Agent, "_get_tool_display_name", lambda self, n, a: "read"
        )
        monkeypatch.setattr(Agent, "_detect_source_type", lambda self: "chat")

        agent = Agent.__new__(Agent)
        agent.is_master = False
        agent.subagent_config = None
        agent._init_tenant_id = None
        agent._pending_compression_event = None
        agent.memory = MagicMock()
        agent.memory.get_context.return_value = []
        agent.memory.short_term = MagicMock(max_messages=10)
        agent.plan_manager = MagicMock()
        agent.plan_manager.get_plan.return_value = None
        agent.plan_manager.get_next_pending_task.return_value = None
        agent.tool_registry = MagicMock()
        agent.tool_registry.get_tool.return_value = None
        agent.skill_registry = MagicMock()
        agent.tool_executor = MagicMock()
        agent.tool_executor.execute = AsyncMock(return_value=tool_result_content)
        # LLM：第一轮返回工具调用(read)，第二轮返回普通回复（结束循环）
        agent.llm = MagicMock()
        agent.llm.chat_with_tools = AsyncMock(
            side_effect=[
                {
                    "tool_calls": [
                        {"id": "c1", "type": "function",
                         "function": {"name": "read", "arguments": "{}"}}
                    ],
                    "content": "",
                    "usage": {},
                    "request_id": "r1",
                },
                {"tool_calls": [], "content": "完成", "usage": {}, "request_id": "r2"},
            ]
        )
        agent.llm.get_model_name.return_value = "qwen3.7-flash"
        agent.llm.get_provider_name.return_value = "qwen"
        return agent

    async def _run_to_tool_messages(self, agent):
        """消费 _process_message_impl 生成器，返回第二轮 LLM 收到的 role=tool 消息。"""
        gen = agent._process_message_impl("帮我读取文件", "sess")
        async for _ in gen:  # 跑完整流程（工具轮 → 接入点 → 普通回复轮）
            pass
        return _tool_msg_from_llm_call(agent.llm, 1)

    async def test_dict_content_truncated(self, monkeypatch):
        """dict 超大 content：先 json.dumps(ensure_ascii=False) 再截断（非 str(dict) 的 Python 表示）。"""
        big_result = {"success": True, "content": "x" * 30000, "summary": "读取结果"}
        agent = self._make_agent(monkeypatch, big_result)
        _spy_truncate(monkeypatch)

        tool_msgs = await self._run_to_tool_messages(agent)
        assert len(tool_msgs) == 1
        assert "已截断" in tool_msgs[0]["content"], "超大工具结果应被截断"
        # dict 被 json.dumps 后传入截断（dumps 输出不带空格、双引号，区别于 Python repr）
        expected_dumped = json.dumps(big_result, ensure_ascii=False)
        assert expected_dumped.startswith('{"success": true')
        assert tool_msgs[0]["content"] == _truncate_tool_content(expected_dumped)

    async def test_short_dict_content_not_truncated(self, monkeypatch):
        """短 dict content：json.dumps 后长度未超阈值，不截断、无省略标记。"""
        short_result = {"success": True, "content": "ok"}
        agent = self._make_agent(monkeypatch, short_result)

        tool_msgs = await self._run_to_tool_messages(agent)
        assert len(tool_msgs) == 1
        assert "已截断" not in tool_msgs[0]["content"]
        assert tool_msgs[0]["content"] == json.dumps(short_result, ensure_ascii=False)

    async def test_str_content_truncated(self, monkeypatch):
        """str 超大 content：直接截断。"""
        big_str = "y" * 20000
        agent = self._make_agent(monkeypatch, big_str)

        tool_msgs = await self._run_to_tool_messages(agent)
        assert len(tool_msgs) == 1
        assert "已截断" in tool_msgs[0]["content"]
        assert tool_msgs[0]["content"] == _truncate_tool_content(big_str)

    async def test_dict_no_truncate_flag_not_truncated(self, monkeypatch):
        """工具经返回 dict 中 _no_truncate: True 声明"文档型输出不截断"：
        超长内容豁免截断，且内部键被 pop，不进入给 LLM 的 JSON。
        （load_api_config 的 API 说明文档场景：截断后 LLM 无法完成获取。）
        """
        big_result = {
            "success": True,
            "content": "x" * 30000,
            "configured": True,
            "_no_truncate": True,
        }
        agent = self._make_agent(monkeypatch, big_result)

        tool_msgs = await self._run_to_tool_messages(agent)
        assert len(tool_msgs) == 1
        assert "已截断" not in tool_msgs[0]["content"], "声明 _no_truncate 的超长工具结果应豁免截断"
        # 完整 JSON（不含 _no_truncate 内部键）原样传给 LLM
        expected = json.dumps(
            {"success": True, "content": "x" * 30000, "configured": True},
            ensure_ascii=False,
        )
        assert tool_msgs[0]["content"] == expected
        assert "_no_truncate" not in tool_msgs[0]["content"], "内部标记键不应进入给 LLM 的 JSON"

    async def test_travel_quote_internal_data_preserved(self, monkeypatch):
        """端到端：travel-quote generate.py 输出（大 rows + internal_data + _no_truncate 提升）
        主智能体接入点豁免截断，完整 JSON 原样喂给 LLM，internal_data 可 json.loads 提取
        用于原样回传 update_hotel.py（换酒店链路依赖）。"""
        big_rows = [{"成本类别": "住宿", "项目": f"酒店{i}", "费用小计": 100 + i} for i in range(300)]
        stdout = json.dumps({
            "success": True,
            "data": {
                "rows": big_rows,
                "合计_费用小计": 1000,
                "internal_data": {"items": [{"name": "x"}], "hotel_stays": [{"city": "贵阳"}]},
            },
            "_no_truncate": True,
        }, ensure_ascii=False)
        # skill_execute 工具提升后的返回：stdout（含 _no_truncate 声明）+ 顶层 _no_truncate
        skill_exec_result = {
            "success": True,
            "stdout": stdout,
            "stderr": "",
            "exit_code": 0,
            "duration": 0.1,
            "timed_out": False,
            "error": "",
            "_no_truncate": True,
        }
        agent = self._make_agent(monkeypatch, skill_exec_result)

        tool_msgs = await self._run_to_tool_messages(agent)
        assert len(tool_msgs) == 1
        assert "已截断" not in tool_msgs[0]["content"], "travel-quote 报价结果应豁免截断"
        parsed = json.loads(tool_msgs[0]["content"])
        assert "_no_truncate" not in parsed, "工具提升的标记键应被 pop，不进入给 LLM 的 JSON 顶层"
        assert parsed["stdout"].startswith('{"success": true')
        stdout_parsed = json.loads(parsed["stdout"])
        assert len(stdout_parsed["data"]["rows"]) == 300, "rows 明细须完整（LLM 复述依据）"
        assert stdout_parsed["data"]["internal_data"]["items"] == [{"name": "x"}], \
            "internal_data 须完整（update_hotel.py 原样回传依赖）"

    async def test_use_skill_guide_not_truncated(self, monkeypatch):
        """use_skill 加载的技能指南（超 12K 大技能）豁免截断：
        skill_version 解析链路依赖完整 JSON，且指南须完整喂给 LLM。
        这是 P1 修复——大技能（如 guizang-ppt 37K）若被截断会致
        _get_last_use_skill_version 解析失败 → skill_execute 版本校验拦截死循环。
        """
        big_guide = "G" * 20000  # 超阈值大技能指南
        skill_result = {
            "success": True,
            "skill_name": "guizang-ppt",
            "skill_version": "1.0.0",
            "content": big_guide,
            "message": "✅ Skill 'guizang-ppt' (v1.0.0) loaded.",
        }
        agent = self._make_agent(monkeypatch, None)
        # 构造 use_skill 工具：LLM 第一轮调用 use_skill
        skill_obj = MagicMock()
        skill_obj.dir = "/tmp/fake_skill"
        skill_obj.hooks = None
        agent.skill_registry.get.return_value = skill_obj
        use_skill_tool = MagicMock()
        use_skill_tool.execute = AsyncMock(return_value=skill_result)
        agent._use_skill_tool = use_skill_tool
        agent.llm.chat_with_tools = AsyncMock(
            side_effect=[
                {
                    "tool_calls": [
                        {"id": "c1", "type": "function",
                         "function": {"name": "use_skill", "arguments": "{\"skill\": \"guizang-ppt\"}"}}
                    ],
                    "content": "",
                    "usage": {},
                    "request_id": "r1",
                },
                {"tool_calls": [], "content": "完成", "usage": {}, "request_id": "r2"},
            ]
        )

        tool_msgs = await self._run_to_tool_messages(agent)
        assert len(tool_msgs) == 1
        expected = json.dumps(skill_result, ensure_ascii=False)
        assert tool_msgs[0]["content"] == expected, "use_skill 技能指南必须完整（豁免截断）"
        assert "已截断" not in tool_msgs[0]["content"]
        # 完整 JSON 可被 _get_last_use_skill_version 解析出 skill_version
        assert json.loads(tool_msgs[0]["content"])["skill_version"] == "1.0.0"


# ---------------------------------------------------------- 子智能体接入点

class TestSubagentToolResultTruncation:
    """子智能体 execute_as_subagent 工具结果接入点。"""

    def _make_agent(self, monkeypatch, tool_result_content):
        """构造轻量子智能体实例 + patch 必要依赖。"""
        monkeypatch.setattr(Agent, "_ensure_tenant_skills_loaded", lambda self: None)
        monkeypatch.setattr(
            Agent, "_build_multimodal_user_content", lambda self, text, paths: None
        )
        monkeypatch.setattr(
            Agent, "_build_system_prompt", lambda self, *a, **k: "system"
        )
        monkeypatch.setattr(Agent, "_get_tools", lambda self: [{"name": "read"}])
        monkeypatch.setattr(
            Agent, "_get_tool_display_name", lambda self, n, a: "read"
        )

        agent = Agent.__new__(Agent)
        agent.mode = AgentMode.SUBAGENT
        agent.subagent_config = MagicMock()
        agent.subagent_config.name = "测试子智能体"
        agent.subagent_config.dir_name = None
        agent.session_id = "sub_sess"
        agent.execution_id = "sub_exec"
        agent._init_tenant_id = None
        agent._init_user_id = None
        agent.parent_plan_manager = None
        agent.memory = MagicMock()
        agent.plan_manager = MagicMock()
        agent.plan_manager.get_plan.return_value = None
        agent.plan_manager.get_next_pending_task.return_value = None
        agent.tool_registry = MagicMock()
        agent.tool_registry.get_tool.return_value = None
        agent.skill_registry = MagicMock()
        agent.tool_executor = MagicMock()
        agent.tool_executor.execute = AsyncMock(return_value=tool_result_content)
        agent.llm = MagicMock()
        agent.llm.chat_with_tools = AsyncMock(
            side_effect=[
                {
                    "tool_calls": [
                        {"id": "c1", "type": "function",
                         "function": {"name": "read", "arguments": "{}"}}
                    ],
                    "content": "",
                    "usage": {},
                    "request_id": "r1",
                },
                {"tool_calls": [], "content": "完成", "usage": {}, "request_id": "r2"},
            ]
        )
        agent.llm.get_model_name.return_value = "qwen3.7-flash"
        agent.llm.get_provider_name.return_value = "qwen"
        return agent

    async def _run_to_tool_messages(self, agent):
        """执行 execute_as_subagent，返回第二轮 LLM 收到的 role=tool 消息。"""
        await agent.execute_as_subagent(
            task_description="处理任务", parent_session_id="parent_sess"
        )
        return _tool_msg_from_llm_call(agent.llm, 1)

    async def test_dict_content_truncated(self, monkeypatch):
        """子智能体：dict 超大 content 经 json.dumps(ensure_ascii=False) 后截断。"""
        big_result = {"success": True, "data": "z" * 25000}
        agent = self._make_agent(monkeypatch, big_result)
        _spy_truncate(monkeypatch)

        tool_msgs = await self._run_to_tool_messages(agent)
        assert len(tool_msgs) == 1
        assert "已截断" in tool_msgs[0]["content"]
        expected_dumped = json.dumps(big_result, ensure_ascii=False)
        assert tool_msgs[0]["content"] == _truncate_tool_content(expected_dumped)

    async def test_short_dict_content_not_truncated(self, monkeypatch):
        """子智能体：短 dict content 不截断。"""
        short_result = {"success": True, "data": "done"}
        agent = self._make_agent(monkeypatch, short_result)

        tool_msgs = await self._run_to_tool_messages(agent)
        assert len(tool_msgs) == 1
        assert "已截断" not in tool_msgs[0]["content"]
        assert tool_msgs[0]["content"] == json.dumps(short_result, ensure_ascii=False)

    async def test_dict_no_truncate_flag_not_truncated(self, monkeypatch):
        """子智能体：工具经返回 dict 中 _no_truncate: True 声明"文档型输出不截断"，
        超长内容豁免截断，内部键被 pop 不进入给 LLM 的 JSON（与主智能体一致的修复）。"""
        big_result = {
            "success": True,
            "data": "z" * 25000,
            "_no_truncate": True,
        }
        agent = self._make_agent(monkeypatch, big_result)

        tool_msgs = await self._run_to_tool_messages(agent)
        assert len(tool_msgs) == 1
        assert "已截断" not in tool_msgs[0]["content"], "声明 _no_truncate 的超长工具结果应豁免截断"
        expected = json.dumps({"success": True, "data": "z" * 25000}, ensure_ascii=False)
        assert tool_msgs[0]["content"] == expected
        assert "_no_truncate" not in tool_msgs[0]["content"], "内部标记键不应进入给 LLM 的 JSON"

    async def test_use_skill_guide_not_truncated(self, monkeypatch):
        """子智能体：use_skill 技能指南豁免截断（与主智能体一致的 P1 修复）。"""
        big_guide = "S" * 20000
        skill_result = {
            "success": True,
            "skill_name": "competitor-research",
            "skill_version": "1.0.0",
            "content": big_guide,
            "message": "loaded",
        }
        agent = self._make_agent(monkeypatch, None)
        use_skill_tool = MagicMock()
        use_skill_tool.execute = AsyncMock(return_value=skill_result)
        agent._use_skill_tool = use_skill_tool
        agent.llm.chat_with_tools = AsyncMock(
            side_effect=[
                {
                    "tool_calls": [
                        {"id": "c1", "type": "function",
                         "function": {"name": "use_skill", "arguments": "{\"skill\": \"competitor-research\"}"}}
                    ],
                    "content": "",
                    "usage": {},
                    "request_id": "r1",
                },
                {"tool_calls": [], "content": "完成", "usage": {}, "request_id": "r2"},
            ]
        )

        tool_msgs = await self._run_to_tool_messages(agent)
        assert len(tool_msgs) == 1
        expected = json.dumps(skill_result, ensure_ascii=False)
        assert tool_msgs[0]["content"] == expected, "子智能体 use_skill 技能指南必须完整（豁免截断）"
        assert "已截断" not in tool_msgs[0]["content"]
        assert json.loads(tool_msgs[0]["content"])["skill_version"] == "1.0.0"


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
