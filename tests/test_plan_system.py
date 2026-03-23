#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试规划系统

验证:
1. 计划创建功能
2. MD文件持久化
3. 任务状态跟踪
4. 进度报告
"""

import asyncio
import sys
from pathlib import Path

# 设置控制台编码
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

from src.core.plan_manager import PlanManager
from src.models.plan import TaskStatus


async def test_plan_manager():
    """测试PlanManager功能"""
    print("=" * 60)
    print("测试 PlanManager")
    print("=" * 60)
    
    # 使用临时目录
    test_dir = Path(__file__).parent / "test_plans"
    test_dir.mkdir(exist_ok=True)
    
    manager = PlanManager(test_dir)
    
    # 测试1: 创建简单计划
    print("\n[测试1] 创建简单计划（单步）")
    print("-" * 60)
    
    plan = manager.create_plan(
        session_id="test_session_1",
        user_query="搜索今年春节贺岁档电影",
        steps=[
            {
                "step_number": 1,
                "description": "搜索2026年春节贺岁档电影信息",
                "tool": "web_search",
                "parameters": {"keyword": "2026年春节贺岁档电影"},
                "expected_output": "电影列表和票房信息",
            }
        ],
        execution_mode="sequential",
        available_tools=["web_search", "email_send"],
        available_skills=["pdf", "ocr"],
    )
    
    print(f"计划ID: {plan.plan_id}")
    print(f"任务数: {len(plan.tasks)}")
    print(f"是否简单任务: {manager.is_simple_task('test_session_1')}")
    
    # 检查MD文件是否创建
    md_file = test_dir / "test_session_1.md"
    if md_file.exists():
        print(f"[OK] MD文件已创建: {md_file}")
        print(f"文件大小: {md_file.stat().st_size} bytes")
    else:
        print(f"[FAIL] MD文件未创建")
    
    # 测试2: 创建多步计划
    print("\n[测试2] 创建多步计划")
    print("-" * 60)
    
    plan2 = manager.create_plan(
        session_id="test_session_2",
        user_query="搜索春节电影票房并发送邮件报告",
        steps=[
            {
                "step_number": 1,
                "description": "搜索2026年春节电影票房数据",
                "tool": "web_search",
                "parameters": {"keyword": "2026年春节电影票房排行榜"},
                "expected_output": "票房数据",
            },
            {
                "step_number": 2,
                "description": "整理票房数据摘要",
                "tool": "doc_summarize",
                "parameters": {"content": "上一步的结果"},
                "expected_output": "票房摘要",
            },
            {
                "step_number": 3,
                "description": "发送邮件报告",
                "tool": "email_send",
                "parameters": {
                    "to": ["user@example.com"],
                    "subject": "春节电影票房报告",
                    "body": "票房摘要内容"
                },
                "expected_output": "邮件发送成功",
            }
        ],
        execution_mode="sequential",
        available_tools=["web_search", "email_send", "doc_summarize"],
        available_skills=["pdf"],
    )
    
    print(f"计划ID: {plan2.plan_id}")
    print(f"任务数: {len(plan2.tasks)}")
    print(f"是否简单任务: {manager.is_simple_task('test_session_2')}")
    
    # 测试3: 任务状态跟踪
    print("\n[测试3] 任务状态跟踪")
    print("-" * 60)
    
    # 获取第一个任务
    task1 = manager.get_next_pending_task("test_session_2")
    if task1:
        print(f"待执行任务: {task1.task_id} - {task1.description}")
        
        # 标记开始
        manager.mark_task_running("test_session_2", task1.task_id)
        print(f"状态更新: {task1.status}")
        
        # 标记完成
        manager.mark_task_completed(
            "test_session_2", 
            task1.task_id,
            {"success": True, "data": "搜索结果数据"}
        )
        print(f"状态更新: {task1.status}")
    
    # 获取进度报告
    progress = manager.get_progress_report("test_session_2")
    print(f"\n进度报告:")
    print(f"  总任务: {progress['total_tasks']}")
    print(f"  已完成: {progress['completed']}")
    print(f"  进行中: {progress['running']}")
    print(f"  待执行: {progress['pending']}")
    print(f"  进度: {progress['progress_percentage']}%")
    
    # 测试4: 查看MD文件更新
    print("\n[测试4] MD文件更新")
    print("-" * 60)
    
    md_file2 = test_dir / "test_session_2.md"
    if md_file2.exists():
        content = md_file2.read_text(encoding="utf-8")
        # 检查是否包含状态更新
        if "completed" in content:
            print("[OK] MD文件已更新状态")
        print(f"文件大小: {md_file2.stat().st_size} bytes")
    
    # 测试5: 计划摘要
    print("\n[测试5] 计划摘要")
    print("-" * 60)
    summary = manager.get_plan_summary("test_session_2")
    print(summary)
    
    # 清理测试文件
    print("\n[清理] 删除测试文件...")
    for f in test_dir.glob("*.md"):
        f.unlink()
    test_dir.rmdir()
    
    print("\n" + "=" * 60)
    print("[OK] 测试完成!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_plan_manager())
