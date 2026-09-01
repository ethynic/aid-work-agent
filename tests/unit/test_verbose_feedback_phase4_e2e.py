# -*- coding: utf-8 -*-
"""verbose Phase 4：首批长任务接入与全链路验收测试

对应 docs/plans/plan-agent-intermediate-feedback.md「Phase 4」与设计文档
agent-intermediate-feedback-design.md §5（首批策略）/§14（验收标准）：

1. 真实 SKILL.md（travel-quote）的 metadata.user_feedback
   能被 skill_loader 解析、经 resolve_feedback_policy 命中，且不渲染进
   Skill 正文 / Layer-1 描述（进 prompt 的两条路径）；
   excel-to-template 的提示语定义在 Excel 工具 get_user_feedback（2026-09-01
   用户裁决：该场景实际执行走 Excel 工具 fill_template，Skill metadata 不重复定义）；
2. Excel 工具仅 task=fill_template 返回 LongRunningFeedback；
   read / to_md / list_templates / 简单 export 无提示；
3. 全链路（Agent impl 级，全 mock）：travel-quote / fill_template 的
   policy verbose 先于 tool_start 与最终 response，每轮恰好 1 条，
   metadata.verboseMessages 按 eventId 去重、delivery 冻结；
4. 故障注入补充：SSE 消费端中途断开（aclose）后 producer 清理、无重复发送；
5. 并发：100 个长 turn 全部完成、final 成功率 100%、每轮 verbose 恰 1 条、
   asyncio task 数恢复基线。

驱动方式与 tests/unit/test_verbose_feedback.py 同款（Agent.__new__ 跳过
__init__ 副作用 + monkeypatch 重依赖 + AsyncMock LLM），全 mock、亚秒级。
"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.agent import Agent
from src.core.agent_events import make_verbose_event
from src.core.skill_loader import SkillLoader
from src.core.verbose_feedback import (
    LongRunningFeedback,
    VerboseFeedbackConfig,
    VerboseFeedbackState,
    iter_with_verbose_feedback,
    resolve_feedback_policy,
)

pytestmark = pytest.mark.agent

# 设计 §5 / 任务书给定的审核文案（逐字冻结，不得改写）
APPROVED_TRAVEL_MESSAGE = "正在生成报价单，这可能需要一点时间，请耐心等待。"
APPROVED_EXCEL_TOOL_MESSAGE = "正在读取 Excel 模板并生成实际文件，可能需要一些时间，请稍候。"

SKILLS_DIR = Path(__file__).resolve().parents[2] / "src" / "skills"


def _fast_config(enabled: bool = True) -> VerboseFeedbackConfig:
    """测试用配置（2026-09-01 产品决策：system watchdog 删除，无 delay 字段）。"""
    return VerboseFeedbackConfig(enabled=enabled)


def _load_real_skill(skill_dir_name: str):
    """用生产解析路径（SkillLoader.parse_skill_md）加载真实 SKILL.md。

    不走 load_skills（避免扫描全部 Skill / 触发 DB init_script），
    parse_skill_md 即 frontmatter→Skill 的唯一生产解析函数。
    """
    loader = SkillLoader(Path("__nonexistent_skills_dir__"))
    return loader.parse_skill_md(SKILLS_DIR / skill_dir_name / "SKILL.md")


# ============================================================
# 1. 首批 SKILL.md metadata 接入（真实文件断言）
# ============================================================


class TestFirstBatchSkillMetadata:
    def test_travel_quote_skill_md_declares_approved_feedback(self):
        skill = _load_real_skill("travel-quote")
        assert skill is not None, "travel-quote SKILL.md 必须可被生产解析路径解析"
        feedback = (skill.metadata or {}).get("user_feedback")
        assert feedback == {
            "long_running": True,
            "start_message": APPROVED_TRAVEL_MESSAGE,
        }

    def test_excel_to_template_skill_md_has_no_user_feedback(self):
        """excel-to-template 的提示语定义在 Excel 工具层（用户裁决 2026-09-01），
        Skill metadata 不得再定义 user_feedback（避免双层来源）。"""
        skill = _load_real_skill("excel-to-template-1.0.0")
        assert skill is not None
        assert (skill.metadata or {}).get("user_feedback") is None

    def test_real_skill_metadata_resolves_through_policy(self):
        """真实 Skill 对象经 registry 兜底分支（无 get_user_feedback 访问器）
        也能被 resolve_feedback_policy 命中，文案逐字等于审核文案；
        excel-to-template 无 metadata 定义 → 不命中（短任务）。"""
        skills = {
            "travel-quote": _load_real_skill("travel-quote"),
            "excel-to-template": _load_real_skill("excel-to-template-1.0.0"),
        }
        agent = SimpleNamespace(
            skill_registry=SimpleNamespace(get=lambda name: skills.get(name))
        )
        fb_travel = resolve_feedback_policy(
            "skill_execute", {"skill": "travel-quote"}, agent
        )
        assert fb_travel == LongRunningFeedback(start_message=APPROVED_TRAVEL_MESSAGE)
        assert (
            resolve_feedback_policy(
                "skill_execute", {"skill": "excel-to-template"}, agent
            )
            is None
        )

    def test_real_skill_metadata_not_rendered_into_prompt_paths(self):
        """metadata.user_feedback 不进 Skill 正文（Layer 2）与描述（Layer 1）。"""
        for dir_name in ("travel-quote", "excel-to-template-1.0.0"):
            skill = _load_real_skill(dir_name)
            assert "user_feedback" not in (skill.body or "")
            assert APPROVED_TRAVEL_MESSAGE not in skill.body
            assert APPROVED_EXCEL_TOOL_MESSAGE not in skill.body
            # Layer-1 描述仅 name+version+description，结构性不含 metadata
            assert "user_feedback" not in skill.description


# ============================================================
# 2. Excel 工具 get_user_feedback（仅 fill_template）
# ============================================================


class TestExcelToolFeedback:
    def _tool(self):
        from src.tools.excel.excel_process_tool import ExcelProcessTool

        return ExcelProcessTool()

    def test_fill_template_returns_approved_feedback(self):
        assert self._tool().get_user_feedback({"task": "fill_template"}) == (
            LongRunningFeedback(start_message=APPROVED_EXCEL_TOOL_MESSAGE)
        )

    def test_short_tasks_return_none(self):
        tool = self._tool()
        for task in ("read", "to_md", "list_templates", "export", "convert",
                     "merge", "", None):
            args = {} if task is None else {"task": task}
            assert tool.get_user_feedback(args) is None, task

    def test_comma_separated_multi_task_with_fill_template_marks_long(self):
        """task 支持逗号分隔多任务（与路由器同款解析），含 fill_template 即长任务。"""
        assert self._tool().get_user_feedback({"task": "read,fill_template"}) is not None

    def test_smart_template_fill_args_marks_long(self):
        """新调用约定（schema 无 task 字段）：data + file_paths 即智能模板填充。

        回归 tr_030d9e9e708f4ad5：该形态实测走 _fast_path_route → fill_template
        耗时 131s，但旧钩子只认 task 参数导致无等待提示。
        """
        args = {
            "file_paths": ["file_05006c623864"],
            "instruction": "按报价单模板生成40人红色主题研学报价单",
            "data": {"meta": {"产品类型": "高校研学成团定制"}, "rows": []},
        }
        assert self._tool().get_user_feedback(args) == (
            LongRunningFeedback(start_message=APPROVED_EXCEL_TOOL_MESSAGE)
        )

    def test_smart_template_fill_partial_args_return_none(self):
        """data / file_paths 缺一不可（与 _fast_path_route 判定一致），均为短任务。"""
        tool = self._tool()
        data = {"meta": {"人数": "40人"}}
        assert tool.get_user_feedback({"data": data}) is None
        assert tool.get_user_feedback({"file_paths": ["file_x"]}) is None
        assert tool.get_user_feedback({"data": {}, "file_paths": ["file_x"]}) is None
        assert tool.get_user_feedback({"data": data, "file_paths": []}) is None

    def test_resolves_through_policy_via_tool_registry(self):
        tool = self._tool()
        agent = SimpleNamespace(
            skill_registry=None,
            tool_registry=SimpleNamespace(
                get_tool=lambda name: tool if name == "excel_process" else None
            ),
            _tool_controls=None,
        )
        fb = resolve_feedback_policy("excel_process", {"task": "fill_template"}, agent)
        assert fb == LongRunningFeedback(start_message=APPROVED_EXCEL_TOOL_MESSAGE)
        assert resolve_feedback_policy("excel_process", {"task": "read"}, agent) is None

    def test_hook_not_exposed_in_tool_schema(self):
        schema = self._tool().to_tool_definition()
        dumped = json.dumps(schema, ensure_ascii=False)
        assert "get_user_feedback" not in dumped
        assert "user_feedback" not in dumped
        assert "start_message" not in dumped


# ============================================================
# 3. 全链路验收（Agent impl 级，全 mock）
# ============================================================


def _tool_call(name, arguments=None, call_id="c1"):
    return {
        "id": call_id, "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments or {})},
    }


def _llm_round(tool_calls=None, content="", request_id="r1"):
    return {
        "tool_calls": list(tool_calls or []),
        "content": content,
        "usage": {},
        "request_id": request_id,
    }


_ROUND_FINAL = _llm_round(content="完成", request_id="r2")


def _patch_impl_deps(monkeypatch):
    monkeypatch.setattr(Agent, "_get_pending_clarification", lambda self, sid: None)
    monkeypatch.setattr(Agent, "_ensure_tenant_skills_loaded", lambda self: None)
    monkeypatch.setattr(Agent, "_run_compression_phase", AsyncMock(return_value=None))
    monkeypatch.setattr(Agent, "_handle_remember_intent", AsyncMock(return_value=None))
    monkeypatch.setattr(Agent, "_build_messages", lambda self, sid: ([], []))
    monkeypatch.setattr(Agent, "_build_system_prompt", lambda self, *a, **k: "SYSTEM-PROMPT")
    monkeypatch.setattr(Agent, "_get_tools", lambda self: [{"name": "read"}])
    monkeypatch.setattr(Agent, "_get_tool_display_name", lambda self, n, a: n)
    monkeypatch.setattr(Agent, "_detect_source_type", lambda self: "chat")
    monkeypatch.setattr(
        Agent, "_check_skill_version_consistency",
        lambda self, sid, skill: {"success": False, "error": "version-mismatch"},
    )


def _make_agent(monkeypatch, llm_rounds, *, skills=None, tools=None):
    _patch_impl_deps(monkeypatch)
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
    agent.skill_registry = SimpleNamespace(
        get=lambda n: None,
        get_user_feedback=lambda n: skills.get(n),
    )
    agent.tool_registry = SimpleNamespace(get_tool=lambda n: (tools or {}).get(n))
    agent._tool_controls = MagicMock()
    agent._tool_controls.get.return_value = None
    agent.tool_executor = MagicMock()
    agent.tool_executor.execute = AsyncMock(return_value={"success": True, "content": "ok"})
    agent.llm = MagicMock()
    agent.llm.chat_with_tools = AsyncMock(side_effect=list(llm_rounds))
    agent.llm.get_model_name.return_value = "test-model"
    agent.llm.get_provider_name.return_value = "test-provider"
    return agent


async def _collect(gen):
    return [event async for event in gen]


def _verbose_entries_for(events):
    """复用 Web 端 metadata 折叠函数（设计 §10 同一生产代码路径）。"""
    from src.main import _build_verbose_metadata_entries

    return _build_verbose_metadata_entries(
        [e for e in events if e.get("type") == "verbose"]
    )


class TestEndToEndTravelQuote:
    async def test_policy_verbose_before_tool_start_and_final(self, monkeypatch):
        """验收 §14.1/§14.5：travel-quote 提示先于 tool_start 与 final，恰好一条。"""
        agent = _make_agent(
            monkeypatch,
            [
                _llm_round(tool_calls=[_tool_call("skill_execute", {"skill": "travel-quote"})]),
                _ROUND_FINAL,
            ],
            skills={"travel-quote": {"long_running": True, "start_message": APPROVED_TRAVEL_MESSAGE}},
        )
        events = await _collect(agent.process_message(
            "出一份报价单", "s1",
            verbose_config=_fast_config(),
            verbose_state=VerboseFeedbackState(),
        ))
        types = [e.get("type") for e in events]
        verbose_idx = [i for i, t in enumerate(types) if t == "verbose"]
        assert len(verbose_idx) == 1, types
        assert verbose_idx[0] < types.index("tool_start"), "提示必须在 tool_start 之前"
        assert verbose_idx[0] < types.index("response"), "提示必须先于最终 response"
        assert types[-1] != "verbose" and "response" in types
        verbose = events[verbose_idx[0]]
        assert verbose["source"] == "policy"
        assert verbose["data"] == APPROVED_TRAVEL_MESSAGE
        assert verbose["eventId"].startswith("verbose_")

    async def test_metadata_complete_dedup_and_delivery_frozen(self, monkeypatch):
        """验收 §14.11/设计 §10：verboseMessages 按 eventId 去重、delivery 冻结。"""
        agent = _make_agent(
            monkeypatch,
            [
                _llm_round(tool_calls=[_tool_call("skill_execute", {"skill": "travel-quote"})]),
                _ROUND_FINAL,
            ],
            skills={"travel-quote": {"long_running": True, "start_message": APPROVED_TRAVEL_MESSAGE}},
        )
        events = await _collect(agent.process_message(
            "出一份报价单", "s1",
            verbose_config=_fast_config(),
            verbose_state=VerboseFeedbackState(),
        ))
        verbose_events = [e for e in events if e.get("type") == "verbose"]
        verbose_events.append(dict(verbose_events[0]))  # 模拟重复透传，防御性去重
        entries = _verbose_entries_for(verbose_events)
        assert len(entries) == 1
        entry = entries[0]
        assert entry["eventId"] == verbose_events[0]["eventId"]
        assert entry["data"] == APPROVED_TRAVEL_MESSAGE
        assert entry["source"] == "policy"
        assert isinstance(entry["timestamp"], int)
        assert entry["delivery"] == "streamed"


class TestEndToEndExcelFillTemplate:
    async def test_fill_template_verbose_then_file_and_response(self, monkeypatch):
        """验收 §14.2：fill_template AI 路径有提示；提示先于最终文件/response。"""
        from src.tools.excel.excel_process_tool import ExcelProcessTool

        agent = _make_agent(
            monkeypatch,
            [
                _llm_round(tool_calls=[_tool_call("excel_process", {"task": "fill_template"})]),
                _ROUND_FINAL,
            ],
            tools={"excel_process": ExcelProcessTool()},
        )
        events = await _collect(agent.process_message(
            "把数据填进模板", "s2",
            verbose_config=_fast_config(),
            verbose_state=VerboseFeedbackState(),
        ))
        types = [e.get("type") for e in events]
        verbose_idx = [i for i, t in enumerate(types) if t == "verbose"]
        assert len(verbose_idx) == 1, types
        assert verbose_idx[0] < types.index("tool_start")
        assert verbose_idx[0] < types.index("response")
        assert events[verbose_idx[0]]["data"] == APPROVED_EXCEL_TOOL_MESSAGE
        assert events[verbose_idx[0]]["source"] == "policy"

    async def test_read_path_has_no_verbose(self, monkeypatch):
        """验收 §14.2：read 等短操作无 policy 提示；无策略 turn 不产生任何 verbose。"""
        from src.tools.excel.excel_process_tool import ExcelProcessTool

        agent = _make_agent(
            monkeypatch,
            [
                _llm_round(tool_calls=[_tool_call("excel_process", {"task": "read"})]),
                _ROUND_FINAL,
            ],
            tools={"excel_process": ExcelProcessTool()},
        )
        events = await _collect(agent.process_message(
            "读一下表格", "s3",
            verbose_config=_fast_config(),
            verbose_state=VerboseFeedbackState(),
        ))
        assert not [e for e in events if e.get("type") == "verbose"]
        assert "response" in [e.get("type") for e in events]


class TestSseDisconnectFaultInjection:
    async def test_consumer_disconnect_cleans_producer_and_stops_sending(self):
        """补充「SSE 断开」故障注入：消费端中途 aclose 后 producer 被清理，
        不再产生任何 verbose，无遗留 task。"""
        baseline = len([t for t in asyncio.all_tasks() if t is not asyncio.current_task()])

        async def slow_source():
            yield {"type": "progress", "data": "step1"}
            await asyncio.sleep(5.0)  # 模拟慢 LLM，SSE 客户端在期间断开
            yield {"type": "response", "content": "late"}

        config = VerboseFeedbackConfig(enabled=True)
        state = VerboseFeedbackState()
        gen = iter_with_verbose_feedback(
            slow_source(), surface="web", config=config, state=state
        )
        first = await gen.__anext__()
        assert first["type"] == "progress"
        await gen.aclose()  # 模拟 SSE 客户端断开 → generator 被关闭
        await asyncio.sleep(0.05)
        leftover = [t for t in asyncio.all_tasks()
                    if t is not asyncio.current_task() and not t.done()]
        assert len(leftover) == baseline, "断开后不得遗留 producer task"
        # 状态不被破坏：要么已正常 CLOSED，要么保持未发射（不得出现半发射残态）
        assert state.closed or state.event is None


# ============================================================
# 4. 并发验收：100 个长 turn
# ============================================================


class TestConcurrent100LongTurns:
    async def test_100_concurrent_long_turns_single_verbose_and_full_success(self):
        """计划 §8：100 个长 turn 并发（策略驱动），全部完成、每轮恰好 1 条
        policy verbose、final 成功率 100%、asyncio task 数恢复基线。总时长 < 60 秒。"""
        baseline = len([t for t in asyncio.all_tasks() if t is not asyncio.current_task()])
        config = VerboseFeedbackConfig(enabled=True)
        n = 100
        delays = [0.12 + (i % 8) * 0.02 for i in range(n)]  # 0.12~0.26s，全部为慢 turn

        async def run_turn(delay: float):
            async def source_gen():
                # 模拟真实编排：policy 事件先 try_emit 注册再 yield（tool_start 前）
                event = make_verbose_event(
                    event_id=f"verbose_{delay}", data=APPROVED_TRAVEL_MESSAGE,
                    source="policy",
                )
                state.try_emit(event)
                yield event
                await asyncio.sleep(delay)  # 模拟长工具/慢 LLM
                yield {"type": "progress", "data": "working"}
                yield {"type": "response", "content": "final-ok"}

            events = []
            state = VerboseFeedbackState()
            async for event in iter_with_verbose_feedback(
                source_gen(), surface="channel", config=config, state=state
            ):
                events.append(event)
            return events

        results = await asyncio.wait_for(
            asyncio.gather(*(run_turn(d) for d in delays)), timeout=55.0
        )
        assert len(results) == n
        verbose_total = 0
        final_ok = 0
        for events in results:
            types = [e.get("type") for e in events]
            verbose_events = [e for e in events if e.get("type") == "verbose"]
            verbose_ids = {e["eventId"] for e in verbose_events}
            assert len(verbose_events) == 1, "每轮 verbose 必须恰好一条"
            assert len(verbose_ids) == 1
            assert verbose_events[0]["source"] == "policy"
            assert types[-1] == "response" and events[-1].get("content") == "final-ok"
            verbose_total += len(verbose_events)
            final_ok += 1
        assert verbose_total == n
        assert final_ok == n, "final 成功率必须 100%"
        await asyncio.sleep(0)
        leftover = [t for t in asyncio.all_tasks()
                    if t is not asyncio.current_task() and not t.done()]
        assert len(leftover) <= baseline, "并发结束后 asyncio task 数必须恢复基线"
