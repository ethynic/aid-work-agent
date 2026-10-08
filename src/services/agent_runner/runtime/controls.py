"""Plan, skill and delegation effects, outside the execution state machine."""

import asyncio
import json
from src.core.agent_engine.contracts import AgentMode, DispatchResult
from src.core.agent_events import make_event
from src.core.agent_logger import log_skill_execute
from src.tools.context import tool_execution_scope


class ControlToolAdapter:
    def __init__(self, *, controls, skills, registry, plan_manager, clarification_store,
                 context, user_input, task_record=None, durable_owner=False):
        self.controls, self.skills, self.registry = controls, skills, registry
        self.plan_manager, self.clarification_store = plan_manager, clarification_store
        self.context, self.user_input, self.task_record = context, user_input, task_record
        self.durable_owner = durable_owner

    async def execute(self, state, call):
        args = json.loads(json.dumps(call.arguments))
        name = call.name
        if name == "skill_complete":
            yield DispatchResult({"success": True, "message": "skill_complete 已废弃，无需调用"})
            return
        if name == "create_plan":
            args.update(session_id=state.identity.session_id, user_query=self.user_input)
        elif name == "use_skill":
            skill = self.registry.get(args.get("skill", ""))
            args["_substitutions"] = {
                "session_id": state.identity.session_id,
                "skill_dir": str(skill.dir) if skill else "",
                "user_id": state.identity.user_id or "",
                "arguments": args.get("arguments", ""),
            }
        elif name == "skill_execute":
            blocked = self.skills.check(state, args.get("skill", ""))
            if blocked:
                yield DispatchResult(blocked, success=False)
                return
            args.update(session_id=state.identity.session_id, user_id=state.identity.user_id,
                        workdir=state.resources.get("workspace"))
            args["command"] = args.get("command") or None
        elif name == "delegate_to_subagent":
            args.update(session_id=state.identity.session_id, user_id=state.identity.user_id)
            yield make_event("progress", data=f"🚀 正在调用{args.get('subagent_name', '')}子智能体处理任务...")

        tool = self.controls.get(name)
        with tool_execution_scope(self.context.derive(tool_call_id=call.id)):
            result = await tool.execute(**args)
        success = result.get("success", True) if isinstance(result, dict) else True
        if name == "create_plan":
            plan = self.plan_manager.get_plan(state.identity.session_id)
            state.plan_ref = getattr(plan, "plan_id", None)
            yield make_event("progress", data="📋 执行计划已创建")
        elif name == "use_skill":
            skill = self.registry.get(args.get("skill", ""))
            if success and skill and skill.hooks:
                from src.core.skill_hooks import SkillHooks
                environment = await asyncio.to_thread(self.registry.environment, skill.name,
                    tenant_id=state.identity.tenant_id, env_vars=self.context.env_vars, context=self.context)
                hook = await SkillHooks.run_on_load(skill.hooks, skill.dir, env=environment)
                if hook:
                    result["content"] = result.get("content", "") + f"\n\n**Hook output:**\n{hook}"
            if success and result.get("skill_version"):
                state.loaded_skills[args.get("skill", "")] = result["skill_version"]
            yield make_event("progress", data=f"📦 已加载技能: {args.get('skill', '')}")
        elif name == "skill_execute":
            log_skill_execute(skill_name=args.get("skill", ""), command=args.get("command") or "",
                session_id=state.identity.session_id, user_id=state.identity.user_id or "",
                success=success, exit_code=result.get("exit_code", 0),
                stdout=result.get("stdout", ""), stderr=result.get("stderr", ""),
                error=result.get("error", ""), duration=result.get("duration", 0),
                input_content=str(args.get("content") or ""))
            preview = result.get("stdout", "")[:100] if success else (result.get("stderr") or result.get("error") or "未知错误")[-300:]
            yield make_event("progress", data=f"{'✅' if success else '❌'} 技能「{args.get('skill', '')}」执行{'完成' if success else '失败'}: {preview}")
        elif name == "clarify":
            question = result.get("question", "")
            yield make_event("progress", data=f"❓ 需要澄清: {question[:50]}...")
            if state.role == AgentMode.SUBAGENT:
                if self.task_record:
                    self.task_record.request_clarification(question)
                yield DispatchResult(result, wait={"kind": "clarification", "question": question,
                    "missing_info": args.get("missing_info", []), "tool_call_id": call.id})
                return
        elif name == "delegate_to_subagent":
            execution_id = result.get("execution_id")
            if execution_id:
                state.children.setdefault(call.id, {}).update(execution_id=execution_id, status=result.get("status"))
            if result.get("status") in {"waiting", "paused"}:
                yield DispatchResult(result, success=False, wait={"kind": "child_wait",
                    "tool_call_id": call.id, "execution_id": execution_id,
                    "child_wait": result.get("waiting"), "child_status": result["status"]})
                return
            if result.get("status") == "clarifying":
                question = result.get("question", "需要补充信息")
                if not self.durable_owner:
                    self.clarification_store.save(state.identity, {
                        "subagent_name": args.get("subagent_name", ""),
                        "execution_id": execution_id or "", "task_description": args.get("task_description", ""),
                        "question": question,
                    })
                yield make_event("clarification", subagentName=args.get("subagent_name", ""), question=question)
                yield make_event("response", data=f"\n❓ **{args.get('subagent_name', '')}** 需要补充信息：{question}\n请提供以上信息，系统将自动继续执行任务。")
                from .history_projection import waiting_messages
                yield make_event("tool_messages", messages=waiting_messages(state))
                yield DispatchResult(result, success=False, wait={"kind": "child_clarification",
                    "tool_call_id": call.id, "execution_id": execution_id, "question": question})
                return
            else:
                preview = (result.get("summary") or result.get("error") or "")[:100]
                yield make_event("progress", data=f"{'✅' if success else '❌'} {args.get('subagent_name', '')}子智能体任务{'完成' if success else '失败'}: {preview}...")
        if name == "delegate_to_subagent" and state.resources.get("clarification_reply_call_id") == call.id:
            self.clarification_store.clear(state.identity)
            content = result.get("result") or {}
            output = content if isinstance(content, str) else content.get("content", "")
            if not success:
                output = f"\n❌ 重新执行任务失败：{result.get('error') or '重新执行失败'}"
            yield DispatchResult(result, success=success, final_output=str(output).strip())
            return
        # 与普通工具路径（tools.py 的 _no_truncate pop）对齐：控制工具（如
        # skill_execute）声明的截断豁免同样生效，且该键不残留进 LLM 内容
        preserve = name == "use_skill" or bool(isinstance(result, dict) and result.pop("_no_truncate", False))
        yield DispatchResult(result, success=success, preserve_content=preserve)
