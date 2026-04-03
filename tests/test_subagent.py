#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Subagent模块测试

测试subagent的加载、注册和基本功能。
"""

import asyncio
from pathlib import Path

# 添加src到路径
import sys
sys.path.insert(0, str(Path(__file__).parent))

from src.subagents.loader import SubagentLoader
from src.subagents.registry import SubagentRegistry
from src.subagents.protocol import SubagentTaskRecord
from src.subagents.factory import AgentFactory
from src.memory.short_term import ShortTermMemory


def test_loader():
    """测试SubagentLoader"""
    print("\n=== 测试 SubagentLoader ===")
    
    subagents_dir = Path(__file__).parent / "subagents"
    loader = SubagentLoader(subagents_dir)
    
    print(f"加载的Subagent数量: {len(loader.configs)}")
    for name, config in loader.configs.items():
        print(f"  - {name}: {config.description}")
        print(f"    能力: {config.capabilities}")
        print(f"    可委派给: {config.delegatable_to}")


def test_registry():
    """测试SubagentRegistry"""
    print("\n=== 测试 SubagentRegistry ===")
    
    subagents_dir = Path(__file__).parent / "subagents"
    registry = SubagentRegistry(subagents_dir)
    
    print(f"注册的Subagent数量: {len(registry)}")
    
    # 测试匹配
    print("\n测试匹配功能:")

    # 按能力匹配
    name = registry.match_by_capability("帮我审查这段代码的安全性")
    print(f"  能力匹配 '审查代码安全性': {name}")

    name = registry.match_by_capability("处理这份PDF简历")
    print(f"  能力匹配 '处理PDF简历': {name}")
    
    # 按文件匹配
    name = registry.match_by_file("main.py")
    print(f"  匹配文件 'main.py': {name}")
    
    name = registry.match_by_file("resume.pdf")
    print(f"  匹配文件 'resume.pdf': {name}")
    
    # 获取描述
    print("\n可用的Subagent:")
    print(registry.get_descriptions())


def test_protocol():
    """测试SubagentTaskRecord"""
    print("\n=== 测试 SubagentTaskRecord ===")
    
    record = SubagentTaskRecord.create(
        task_id="task_001",
        execution_id="exec_001",
        subagent_name="code-reviewer",
        task_description="审查代码安全性",
        task_parameters={"file": "main.py"}
    )
    
    print(f"任务记录创建: {record.task_id}")
    print(f"  状态: {record.status}")
    
    # 更新进度
    record.start()
    print(f"  开始执行，状态: {record.status}")
    
    record.update_progress(50, "分析代码结构")
    print(f"  进度: {record.progress_percent}%, 步骤: {record.current_step}")
    
    # 澄清请求
    record.request_clarification("是否需要关注性能问题？")
    print(f"  请求澄清，状态: {record.status}")
    print(f"  问题: {record.clarification_request}")
    
    # 回答澄清
    record.answer_clarification("是的，请重点关注性能")
    print(f"  收到回答，状态: {record.status}")
    
    # 完成
    record.complete(
        result={"issues_found": 3, "severity": "medium"},
        summary="发现3个中等严重程度的问题"
    )
    print(f"  完成，状态: {record.status}")
    print(f"  摘要: {record.summary}")


def test_factory():
    """测试AgentFactory"""
    print("\n=== 测试 AgentFactory ===")
    
    subagents_dir = Path(__file__).parent / "subagents"
    registry = SubagentRegistry(subagents_dir)
    
    factory = AgentFactory(registry)
    
    print("可用的智能体:")
    for name in factory.list_available_agents():
        info = factory.get_agent_info(name)
        print(f"  - {name}: {info['description']}")
        print(f"    可委派给: {info['delegatable_to']}")
    
    # 创建独立智能体
    print("\n创建独立HR智能体:")
    memory = ShortTermMemory()
    try:
        hr_agent = factory.create_standalone_agent("hr-expert", memory)
        print(f"  名称: {hr_agent.config.name}")
        print(f"  模式: {'独立' if not hr_agent.is_subagent_mode else '委派'}")
        print(f"  可委派给: {hr_agent.config.delegatable_to}")
    except Exception as e:
        print(f"  错误: {e}")


def test_delegation_tool():
    """测试委派工具定义"""
    print("\n=== 测试委派工具定义 ===")
    
    subagents_dir = Path(__file__).parent / "subagents"
    registry = SubagentRegistry(subagents_dir)
    
    # 获取全部可委派的工具定义
    tool_def = registry.get_delegation_tool_definition()
    if tool_def:
        print("委派工具定义:")
        print(f"  名称: {tool_def['name']}")
        print(f"  描述:\n{tool_def['description'][:200]}...")
        print(f"  可委派的智能体: {tool_def['input_schema']['properties']['subagent_name']['enum']}")
    
    # 获取受限的委派工具定义
    print("\n受限委派（只允许pdf-expert）:")
    tool_def = registry.get_delegation_tool_definition(["pdf-expert"])
    if tool_def:
        print(f"  可委派的智能体: {tool_def['input_schema']['properties']['subagent_name']['enum']}")


def main():
    """主测试函数"""
    print("=" * 60)
    print("Subagent 模块测试")
    print("=" * 60)
    
    test_loader()
    test_registry()
    test_protocol()
    test_factory()
    test_delegation_tool()
    
    print("\n" + "=" * 60)
    print("所有测试完成！")
    print("=" * 60)


if __name__ == "__main__":
    main()
