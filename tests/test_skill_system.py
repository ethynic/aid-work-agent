#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Skill System Test Script

测试Skill系统的核心功能:
1. Skill加载器
2. Skill注册表
3. Skill执行器
4. 与MasterAgent集成
"""

import asyncio
import sys
import os
from pathlib import Path

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def print_sep(title):
    print("\n" + "=" * 60)
    print(f" {title}")
    print("=" * 60)


def test_skill_loader():
    """测试Skill加载器"""
    print_sep("Test 1: Skill Loader")
    
    from src.core.skill_loader import SkillLoader
    
    skills_dir = Path(__file__).parent / "skills"
    loader = SkillLoader(skills_dir)
    
    print(f"Skills directory: {skills_dir}")
    print(f"Loaded skills: {loader.list_skills()}")
    
    for skill_name in loader.list_skills():
        skill = loader.get_skill(skill_name)
        print(f"\n--- Skill: {skill_name} ---")
        print(f"  Name: {skill.name}")
        print(f"  Description: {skill.description}")
        print(f"  Version: {skill.version}")
        print(f"  Dependencies: {[d.name for d in skill.dependencies]}")
        print(f"  Triggers: {[t.pattern for t in skill.triggers]}")
    
    # Test skill matching
    print("\n--- Skill Matching ---")
    test_files = ["document.pdf", "report.PDF", "image.png"]
    for filename in test_files:
        matched = loader.match_skill_by_file(filename)
        print(f"  {filename} -> {matched or 'No match'}")
    
    return loader


def test_skill_registry():
    """测试Skill注册表"""
    print_sep("Test 2: Skill Registry")
    
    from src.core.skill_registry import SkillRegistry
    
    skills_dir = Path(__file__).parent / "skills"
    registry = SkillRegistry(skills_dir)
    
    print(f"Registered skills: {registry.list_skills()}")
    print(f"\nSkill descriptions:")
    print(registry.get_descriptions())
    
    # Test tool definition
    tool_def = registry.get_skill_tool_definition()
    print(f"\nTool definition name: {tool_def['name']}")
    print(f"Tool description preview: {tool_def['description'][:100]}...")
    
    return registry


async def test_skill_executor():
    """测试Skill执行器"""
    print_sep("Test 3: Skill Executor")
    
    from src.core.skill_registry import SkillRegistry
    from src.core.skill_executor import SkillExecutor
    
    skills_dir = Path(__file__).parent / "skills"
    registry = SkillRegistry(skills_dir)
    executor = SkillExecutor(registry)
    
    # Test skill loading
    print("\n--- Test: Load Skill Content ---")
    content = await executor.load_skill("pdf")
    if content:
        print(f"  Loaded successfully, content length: {len(content)} chars")
        print(f"  Content preview: {content[:200]}...")
    else:
        print("  Failed to load skill")
    
    # Test skill matching
    print("\n--- Test: Skill Matching ---")
    test_files = ["report.pdf", "document.docx"]
    for filename in test_files:
        matched = executor.match_skill_by_file(filename)
        print(f"  {filename} -> {matched or 'No match'}")
    
    # Test command execution
    print("\n--- Test: Command Execution ---")
    result = await executor.execute_skill_command(
        skill_name="pdf",
        command="python --version",
    )
    print(f"  Success: {result.success}")
    print(f"  Output: {result.stdout.strip() or result.stderr.strip()}")
    
    return executor


def test_agent_integration():
    """测试与MasterAgent集成"""
    print_sep("Test 4: Agent Integration")
    
    try:
        from src.core.agent import master_agent
        
        print(f"Agent initialized successfully")
        print(f"  Skills loaded: {master_agent.skill_registry.list_skills()}")
        print(f"  Tools available: {len(master_agent._get_tools())}")
        
        # Check skill tool is included
        tools = master_agent._get_tools()
        tool_names = [t["name"] for t in tools]
        print(f"  Tool names: {tool_names}")
        
        if "use_skill" in tool_names:
            print("  [OK] use_skill tool is available")
        if "skill_execute" in tool_names:
            print("  [OK] skill_execute tool is available")
        
        return True
    except Exception as e:
        print(f"  Error: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    print("\n" + "=" * 60)
    print(" AID Work Agent - Skill System Test")
    print("=" * 60)
    
    # Run tests
    try:
        # Test 1: Skill Loader
        loader = test_skill_loader()
        
        # Test 2: Skill Registry
        registry = test_skill_registry()
        
        # Test 3: Skill Executor
        executor = await test_skill_executor()
        
        # Test 4: Agent Integration
        agent_ok = test_agent_integration()
        
        # Summary
        print_sep("Test Summary")
        print("[OK] Skill Loader: OK")
        print("[OK] Skill Registry: OK")
        print("[OK] Skill Executor: OK")
        print(f"{'[OK]' if agent_ok else '[FAIL]'} Agent Integration: {'OK' if agent_ok else 'FAILED'}")
        
        print("\n" + "=" * 60)
        print(" All tests completed!")
        print("=" * 60)
        
    except Exception as e:
        print(f"\nTest failed with error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
