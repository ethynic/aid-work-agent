#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Git Commit Helper Script
支持中文提交信息和空格的 Git 提交工具

使用方法:
    python scripts/commit.py "提交信息"
    python scripts/commit.py "feat: 添加用户登录功能"
    python scripts/commit.py "fix: 修复数据库连接超时问题"
    
可选参数:
    --push 或 -p: 提交后自动推送到远程仓库
    --status 或 -s: 提交前显示状态
    --help 或 -h: 显示帮助信息
"""

import subprocess
import sys
import argparse
import io
from pathlib import Path

# 设置标准输出编码为 UTF-8，解决 Windows 终端编码问题
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


def run_command(cmd: list, show_output: bool = True) -> tuple:
    """执行命令并返回结果
    
    Args:
        cmd: 命令列表
        show_output: 是否显示输出
        
    Returns:
        (success, stdout, stderr)
    """
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace'
        )
        
        if show_output:
            if result.stdout:
                print(result.stdout, end='')
            if result.stderr:
                print(result.stderr, end='', file=sys.stderr)
        
        return result.returncode == 0, result.stdout, result.stderr
    except Exception as e:
        print(f"❌ 命令执行失败: {e}", file=sys.stderr)
        return False, "", str(e)


def git_status():
    """显示 Git 状态"""
    print("\n📋 当前 Git 状态:")
    print("-" * 50)
    success, stdout, stderr = run_command(['git', 'status'])
    if not success:
        print("❌ 获取 Git 状态失败", file=sys.stderr)
    print("-" * 50)


def git_add_all():
    """添加所有更改到暂存区"""
    print("\n📤 添加更改到暂存区...")
    success, stdout, stderr = run_command(['git', 'add', '-A'])
    if success:
        print("✅ 更改已添加到暂存区")
    else:
        print("❌ 添加更改失败", file=sys.stderr)
        return False
    
    # 检查是否有更改需要提交
    success, stdout, stderr = run_command(['git', 'diff', '--cached', '--quiet'], show_output=False)
    if success:
        print("⚠️  没有更改需要提交")
        return False
    
    return True


def git_commit(message: str) -> bool:
    """执行 Git 提交
    
    Args:
        message: 提交信息
        
    Returns:
        是否成功
    """
    print(f"\n💾 提交更改...")
    print(f"📝 提交信息: {message}")
    
    # 使用 git commit 命令，直接传递消息作为参数
    # Python 的 subprocess 会正确处理 UTF-8 编码的中文
    success, stdout, stderr = run_command(['git', 'commit', '-m', message])
    
    if success:
        # 从输出中提取 commit hash
        if stdout and ']' in stdout:
            hash_start = stdout.find('[') + 1
            hash_end = stdout.find(']')
            if hash_start > 0 and hash_end > hash_start:
                commit_hash = stdout[hash_start:hash_end]
                print(f"✅ 提交成功 (hash: {commit_hash})")
            else:
                print("✅ 提交成功")
        else:
            print("✅ 提交成功")
        return True
    else:
        print("❌ 提交失败", file=sys.stderr)
        return False


def git_push() -> bool:
    """推送到远程仓库"""
    print("\n🚀 推送到远程仓库...")
    success, stdout, stderr = run_command(['git', 'push'])
    
    if success:
        print("✅ 推送成功")
        return True
    else:
        print("❌ 推送失败", file=sys.stderr)
        return False


def show_help():
    """显示帮助信息"""
    print(__doc__)
    print("\n示例:")
    print('  python scripts/commit.py "feat: 添加用户登录功能"')
    print('  python scripts/commit.py "fix: 修复数据库连接超时问题" --push')
    print('  python scripts/commit.py "update: 更新配置文件" -p')
    print('  python scripts/commit.py "docs: 更新 README" --status')


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description='Git Commit Helper - 支持中文提交信息',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python scripts/commit.py "feat: 添加用户登录功能"
  python scripts/commit.py "fix: 修复数据库连接超时问题" --push
  python scripts/commit.py "update: 更新配置文件" -p
  python scripts/commit.py "docs: 更新 README" --status
        """
    )
    
    parser.add_argument(
        'message',
        nargs='*',
        help='提交信息（支持中文和空格）'
    )
    
    parser.add_argument(
        '-p', '--push',
        action='store_true',
        help='提交后自动推送到远程仓库'
    )
    
    parser.add_argument(
        '-s', '--status',
        action='store_true',
        help='提交前显示 Git 状态'
    )
    
    args = parser.parse_args()
    
    # 如果没有提供提交信息，显示帮助
    if not args.message:
        show_help()
        sys.exit(1)
    
    # 将消息列表合并为字符串
    commit_message = ' '.join(args.message)
    
    # 显示状态
    if args.status:
        git_status()
    
    # 添加更改
    if not git_add_all():
        sys.exit(1)
    
    # 提交
    if not git_commit(commit_message):
        sys.exit(1)
    
    # 推送
    if args.push:
        if not git_push():
            sys.exit(1)
    
    print("\n🎉 完成！")


if __name__ == '__main__':
    main()
