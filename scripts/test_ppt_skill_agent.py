#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
guizang-ppt-skill Agent 端到端测试脚本

直接调用 Agent.process_message() 执行 PPT skill，
跟踪每一步的执行日志，找出失败原因。

使用方法：
    cd c:\repos\aid-work-agent
    python scripts/test_ppt_skill_agent.py

输出：
    - 控制台实时打印 Agent 的每一步事件
    - 最终生成分析报告
"""

import asyncio
import io
import json
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any

# Windows 控制台 UTF-8 输出
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    os.system("chcp 65001 >nul 2>&1")

# 添加项目根目录到 sys.path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


# ============================================================
# 事件跟踪器
# ============================================================
class EventTracker:
    """跟踪 Agent 执行事件"""

    def __init__(self):
        self.events: List[Dict[str, Any]] = []
        self.start_time = time.time()
        self.errors: List[str] = []
        self.tool_calls: List[Dict[str, Any]] = []
        self.llm_calls: List[Dict[str, Any]] = []

    def add_event(self, event: Dict[str, Any]):
        event["_elapsed"] = time.time() - self.start_time
        event["_timestamp"] = datetime.now().isoformat()
        self.events.append(event)

        event_type = event.get("type", "")

        if event_type == "tool_start":
            tool_name = event.get("toolName", "")
            tool_args = event.get("toolArgs", {})
            args_preview = json.dumps(tool_args, ensure_ascii=False)[:200]
            print(f"  [{event['_elapsed']:.1f}s] 🔧 工具开始: {tool_name}({args_preview})")
            self.tool_calls.append({
                "name": tool_name,
                "args": tool_args,
                "status": "started",
                "elapsed": event["_elapsed"],
            })

        elif event_type == "tool_result":
            tool_name = event.get("toolName", "")
            result = event.get("result", {})
            success = event.get("success", False)
            status = "✅" if success else "❌"
            result_preview = json.dumps(result, ensure_ascii=False)[:300] if isinstance(result, (dict, list)) else str(result)[:300]
            print(f"  [{event['_elapsed']:.1f}s] {status} 工具结果: {tool_name} -> {result_preview}")
            if not success:
                self.errors.append(f"工具 {tool_name} 失败: {result_preview}")

            # 更新 tool_calls 状态
            for tc in self.tool_calls:
                if tc["name"] == tool_name and tc["status"] == "started":
                    tc["status"] = "success" if success else "failed"
                    tc["result_preview"] = result_preview
                    break

        elif event_type == "progress":
            data = event.get("data", "")
            print(f"  [{event['_elapsed']:.1f}s] 📊 进度: {data}")

        elif event_type == "response":
            data = event.get("data", "")
            preview = data[:200] if data else "(空)"
            print(f"  [{event['_elapsed']:.1f}s] 💬 回复: {preview}")

        elif event_type == "llm_call":
            model = event.get("model", "")
            provider = event.get("provider", "")
            duration_ms = event.get("duration_ms", 0)
            usage = event.get("usage", {})
            self.llm_calls.append({
                "model": model,
                "provider": provider,
                "duration_ms": duration_ms,
                "usage": usage,
                "elapsed": event["_elapsed"],
            })
            print(f"  [{event['_elapsed']:.1f}s] 🧠 LLM调用: {provider}/{model}, {duration_ms}ms, usage={usage}")

        elif event_type == "error":
            data = event.get("data", "")
            print(f"  [{event['_elapsed']:.1f}s] ❌ 错误: {data}")
            self.errors.append(f"Agent错误: {data}")

        elif event_type == "complete":
            print(f"  [{event['_elapsed']:.1f}s] 🏁 Agent执行完成")

        elif event_type == "thinking":
            data = event.get("data", "")
            if data:
                preview = data[:150] if data else "(空)"
                print(f"  [{event['_elapsed']:.1f}s] 💭 思考: {preview}")

    def print_summary(self):
        """打印执行摘要"""
        total_time = time.time() - self.start_time

        print("\n" + "=" * 80)
        print("执行摘要")
        print("=" * 80)

        print(f"\n总耗时: {total_time:.1f}s")
        print(f"事件总数: {len(self.events)}")
        print(f"LLM调用次数: {len(self.llm_calls)}")
        print(f"工具调用次数: {len(self.tool_calls)}")
        print(f"错误数: {len(self.errors)}")

        if self.llm_calls:
            total_input_tokens = sum(c.get("usage", {}).get("input_tokens", 0) or 0 for c in self.llm_calls)
            total_output_tokens = sum(c.get("usage", {}).get("output_tokens", 0) or 0 for c in self.llm_calls)
            print(f"\nToken使用: input={total_input_tokens}, output={total_output_tokens}")

        if self.tool_calls:
            print(f"\n工具调用详情:")
            for i, tc in enumerate(self.tool_calls, 1):
                args_str = json.dumps(tc["args"], ensure_ascii=False)[:150]
                print(f"  {i}. [{tc['elapsed']:.1f}s] {tc['name']}({args_str}) -> {tc['status']}")
                if tc.get("result_preview"):
                    print(f"     结果: {tc['result_preview'][:200]}")

        if self.errors:
            print(f"\n错误列表:")
            for i, err in enumerate(self.errors, 1):
                print(f"  {i}. {err}")

        # 分析失败原因
        self._analyze_failures()

    def _analyze_failures(self):
        """分析失败原因"""
        print("\n" + "-" * 80)
        print("失败原因分析")
        print("-" * 80)

        if not self.errors:
            print("  无错误")
            return

        # 检查常见失败模式
        tool_call_names = [tc["name"] for tc in self.tool_calls]
        tool_call_results = {tc["name"]: tc for tc in self.tool_calls}

        # 1. use_skill 是否成功
        if "use_skill" in tool_call_names:
            use_skill_tc = tool_call_results.get("use_skill")
            if use_skill_tc and use_skill_tc["status"] == "failed":
                print("  ❌ use_skill 调用失败 - skill 可能未被加载")
            elif use_skill_tc and use_skill_tc["status"] == "success":
                print("  ✅ use_skill 调用成功 - skill 已加载到上下文")
        else:
            # 检查是否有自动映射
            auto_mapped = [tc for tc in self.tool_calls if tc["name"] == "guizang-ppt-skill"]
            if auto_mapped:
                print("  ⚠️ LLM 直接调用 guizang-ppt-skill 作为工具名（被自动映射为 use_skill）")
            else:
                print("  ⚠️ use_skill 未被调用 - LLM 可能没有识别到需要使用 skill")

        # 2. read 调用情况
        file_reads = [tc for tc in self.tool_calls if tc["name"] == "read"]
        if file_reads:
            print(f"  📖 read 调用了 {len(file_reads)} 次")
            for fr in file_reads:
                path = fr["args"].get("file_path", "")
                status = fr["status"]
                print(f"     - {path}: {status}")
        else:
            print("  ⚠️ read 未被调用 - LLM 可能没有读取模板/布局文件")

        # 3. cp / write / edit 调用情况
        cp_calls = [tc for tc in self.tool_calls if tc["name"] == "cp"]
        write_calls = [tc for tc in self.tool_calls if tc["name"] == "write"]
        edit_calls = [tc for tc in self.tool_calls if tc["name"] == "edit"]
        if cp_calls:
            print(f"  📋 cp 调用了 {len(cp_calls)} 次")
            for c in cp_calls:
                src = c["args"].get("source_file_path", "")
                print(f"     - source={src}: {c['status']}")
        if write_calls:
            print(f"  📝 write 调用了 {len(write_calls)} 次")
            for w in write_calls:
                args = w["args"]
                mode = args.get("mode", "overwrite")
                has_content = "content" in args and args["content"]
                print(f"     - mode={mode}, has_content={bool(has_content)}: {w['status']}")
        if edit_calls:
            print(f"  ✏️ edit 调用了 {len(edit_calls)} 次")
            for e in edit_calls:
                args = e["args"]
                mode = args.get("mode", "replace_string")
                print(f"     - mode={mode}: {e['status']}")
        if not (cp_calls or write_calls):
            print("  ⚠️ cp/write 未被调用 - HTML 文件可能未被生成")

        # 4. skill_complete 调用情况
        if "skill_complete" in tool_call_names:
            print("  ✅ skill_complete 被调用 - skill 正常结束")
        else:
            print("  ⚠️ skill_complete 未被调用 - skill 未正常结束")

        # 5. 超出最大迭代次数
        if len(self.llm_calls) >= 20:
            print("  ❌ LLM 调用达到 20 轮上限 - 工作流可能太复杂")

        # 6. 检查 skill_execute 调用
        skill_execs = [tc for tc in self.tool_calls if tc["name"] == "skill_execute"]
        if skill_execs:
            print(f"  🔨 skill_execute 调用了 {len(skill_execs)} 次")
            for se in skill_execs:
                cmd = se["args"].get("command", "无命令")[:100]
                print(f"     - 命令: {cmd}: {se['status']}")

        # 7. 检查 LLM 是否在回复中直接生成内容（而不是调用工具）
        responses = [e for e in self.events if e.get("type") == "response"]
        if responses:
            total_response_chars = sum(len(r.get("data", "")) for r in responses)
            print(f"  💬 LLM 直接回复: {len(responses)} 次, 总字符数: {total_response_chars}")
            file_output_calls = cp_calls + write_calls
            if total_response_chars > 5000 and not file_output_calls:
                print("     ⚠️ LLM 可能在回复中直接生成了 HTML，但没有通过 cp/write 写入文件")


# ============================================================
# 主测试函数
# ============================================================
async def run_test():
    """运行 Agent 端到端测试"""

    print("=" * 80)
    print("guizang-ppt-skill Agent 端到端测试")
    print("=" * 80)
    print(f"时间: {datetime.now().isoformat()}")
    print(f"项目根目录: {project_root}")

    # 1. 初始化日志
    from src.config.logging import setup_logging
    setup_logging(log_level="INFO", log_dir="log/agent")

    # 2. 检查配置
    from src.config.settings import settings
    print(f"\n--- 配置检查 ---")
    print(f"  LLM Provider: {settings.llm.provider}")
    print(f"  Model: {getattr(settings.llm, 'model', 'unknown')}")
    print(f"  Skills allowed: {settings.skills.master_agent.allowed}")

    # 3. 初始化 Agent（使用 master_agent 单例）
    print(f"\n--- 初始化 Agent ---")
    try:
        from src.core import master_agent
        agent = master_agent
        print(f"  ✅ master_agent 加载成功")
        print(f"  已注册 Skills: {agent.skill_registry.list_skills()}")
        print(f"  已注册 Tools: {agent.tool_registry.list_tools()}")
    except Exception as e:
        print(f"  ❌ master_agent 加载失败: {e}")
        traceback.print_exc()
        return

    # 4. 检查 guizang-ppt-skill 是否在注册表中
    skill_registry = agent.skill_registry
    if "guizang-ppt-skill" not in skill_registry:
        print(f"\n  ❌ guizang-ppt-skill 不在 SkillRegistry 中！")
        print(f"  已注册的 skills: {skill_registry.list_skills()}")
        return
    else:
        skill_obj = skill_registry.get("guizang-ppt-skill")
        print(f"  ✅ guizang-ppt-skill 已在注册表中")
        print(f"     dir: {skill_obj.dir}")
        print(f"     description: {skill_obj.description[:80]}...")

    # 5. 创建测试会话
    session_id = f"test-ppt-skill-{int(time.time())}"
    print(f"\n--- 创建测试会话 ---")
    print(f"  session_id: {session_id}")

    # 6. 构造测试用户
    from src.models.user import User
    user = User(
        user_id="test-user-ppt",
        name="PPT测试用户",
        role="employee",
    )

    # 7. 构造测试消息（明确指定参数，避免需求澄清阶段）
    test_message = (
        "使用做ppt的skill，帮我做一个关于AI科普的ppt，"
        "瑞士国际主义风，10页，普通观众，"
        "你直接搭建，不需要问我更多问题，"
        "直接开始执行所有步骤"
    )

    print(f"\n--- 发送测试消息 ---")
    print(f"  消息: {test_message}")

    # 8. 执行 Agent 并跟踪事件
    tracker = EventTracker()

    print(f"\n--- 开始执行 Agent Loop ---")
    print("-" * 80)

    try:
        async for event in agent.process_message(
            user_input=test_message,
            session_id=session_id,
            user=user,
        ):
            event_type = event.get("type", "")
            tracker.add_event(event)

    except Exception as e:
        print(f"\n❌ Agent 执行异常: {e}")
        traceback.print_exc()
        tracker.errors.append(f"Agent异常: {e}\n{traceback.format_exc()}")

    # 9. 打印执行摘要
    tracker.print_summary()

    # 10. 保存详细日志
    log_file = project_root / "log" / f"ppt-skill-test-{int(time.time())}.json"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with open(log_file, "w", encoding="utf-8") as f:
        json.dump({
            "session_id": session_id,
            "test_message": test_message,
            "start_time": datetime.fromtimestamp(tracker.start_time).isoformat(),
            "total_time": time.time() - tracker.start_time,
            "events": tracker.events,
            "tool_calls": tracker.tool_calls,
            "llm_calls": tracker.llm_calls,
            "errors": tracker.errors,
        }, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n详细日志已保存到: {log_file}")


# ============================================================
if __name__ == "__main__":
    asyncio.run(run_test())
