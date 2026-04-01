#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM-Driven Agent Interactive Test Script (Enhanced with File Support)

Interactive test for the master agent with LLM-based intent understanding and planning.
This script supports file attachments simulation for testing file-related skills.
"""

import asyncio
import sys
import os
from pathlib import Path
from typing import Dict, Any, List
import shutil

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.core.agent import master_agent
from src.config.settings import settings
from src.models.message import Attachment


def print_sep(title):
    print("\n" + "=" * 60)
    print(f" {title}")
    print("=" * 60)


class FileAttachmentManager:
    """
    文件附件管理器
    
    用于模拟文件上传场景，管理测试文件的存放和访问
    """
    
    def __init__(self, upload_dir: str = "./test_uploads"):
        """
        初始化文件附件管理器
        
        Args:
            upload_dir: 模拟上传文件的存储目录
        """
        self.upload_dir = Path(upload_dir)
        self.upload_dir.mkdir(exist_ok=True, parents=True)
        self.uploaded_files: Dict[str, Path] = {}
        
        print(f"\n[File Manager] 上传目录: {self.upload_dir.absolute()}")
    
    def upload_file(self, file_path: str) -> Dict[str, Any]:
        """
        模拟文件上传
        
        将文件复制到上传目录，并返回附件信息
        
        Args:
            file_path: 原始文件路径
        
        Returns:
            附件信息字典
        """
        source_path = Path(file_path)
        
        if not source_path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")
        
        # 复制文件到上传目录
        dest_path = self.upload_dir / source_path.name
        if source_path != dest_path:
            shutil.copy2(source_path, dest_path)
        
        # 生成文件ID
        file_id = f"file_{len(self.uploaded_files) + 1}"
        self.uploaded_files[file_id] = dest_path
        
        # 构建附件信息
        attachment_info = {
            "type": self._get_file_type(source_path.suffix),
            "url": str(dest_path.absolute()),
            "name": source_path.name,
            "size": source_path.stat().st_size,
            "mime_type": self._get_mime_type(source_path.suffix),
            "file_id": file_id
        }
        
        print(f"\n[上传成功] 文件: {source_path.name}")
        print(f"  - 路径: {dest_path.absolute()}")
        print(f"  - 大小: {attachment_info['size']} bytes")
        print(f"  - 类型: {attachment_info['mime_type']}")
        
        return attachment_info
    
    def list_uploaded_files(self):
        """列出已上传的文件"""
        if not self.uploaded_files:
            print("\n[无上传文件]")
            return
        
        print("\n[已上传文件列表]")
        for file_id, path in self.uploaded_files.items():
            print(f"  {file_id}: {path.name} ({path.stat().st_size} bytes)")
    
    def clear_uploads(self):
        """清空上传目录"""
        for file_path in self.upload_dir.glob("*"):
            if file_path.is_file():
                file_path.unlink()
        self.uploaded_files.clear()
        print("\n[已清空上传目录]")
    
    def _get_file_type(self, suffix: str) -> str:
        """获取文件类型"""
        suffix = suffix.lower()
        type_map = {
            '.pdf': 'file',
            '.doc': 'file',
            '.docx': 'file',
            '.xls': 'file',
            '.xlsx': 'file',
            '.txt': 'file',
            '.png': 'image',
            '.jpg': 'image',
            '.jpeg': 'image',
            '.gif': 'image',
        }
        return type_map.get(suffix, 'file')
    
    def _get_mime_type(self, suffix: str) -> str:
        """获取MIME类型"""
        suffix = suffix.lower()
        mime_map = {
            '.pdf': 'application/pdf',
            '.doc': 'application/msword',
            '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            '.xls': 'application/vnd.ms-excel',
            '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            '.txt': 'text/plain',
            '.png': 'image/png',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.gif': 'image/gif',
        }
        return mime_map.get(suffix, 'application/octet-stream')


def print_help():
    """打印帮助信息"""
    print("\n可用命令:")
    print("  - 输入消息并按Enter与智能体对话")
    print("  - 'upload <文件路径>' - 上传文件")
    print("  - 'list' - 列出已上传的文件")
    print("  - 'clear' - 清空上传文件")
    print("  - 'quit' 或 'exit' - 退出")
    print("  - 'new' - 开始新会话")
    print("\n使用示例:")
    print("  You: upload tests/sample.pdf")
    print("  You: 帮我读取这个PDF文件的内容")
    print("  You: 或者直接说: 读取刚才上传的PDF")


async def interactive_mode():
    """Interactive chat mode with file attachment support"""
    print_sep("Interactive Chat Mode (支持文件附件)")
    
    print(f"LLM Provider: {settings.llm.provider}")
    print(f"Model: {settings.llm.zhipu.model if settings.llm.provider == 'zhipu' else settings.llm.qwen.model}")
    print(f"Registered Tools: {', '.join(master_agent.tool_registry.list_tools())}")
    
    # 初始化文件管理器
    file_manager = FileAttachmentManager()
    
    print_help()
    
    session_id = "interactive_session_001"
    current_attachments: List[Dict[str, Any]] = []
    
    while True:
        try:
            # Get user input
            user_input = input("\nYou: ").strip()
            
            # Check for commands
            if user_input.lower() in ["quit", "exit", "q"]:
                print("\nGoodbye!")
                break
            
            if user_input.lower() == "new":
                session_id = f"interactive_session_{os.urandom(4).hex()}"
                current_attachments.clear()
                print("\n[新会话已开始]")
                continue
            
            if user_input.lower() == "list":
                file_manager.list_uploaded_files()
                continue
            
            if user_input.lower() == "clear":
                file_manager.clear_uploads()
                current_attachments.clear()
                continue
            
            if user_input.lower() == "help":
                print_help()
                continue
            
            # Handle file upload
            if user_input.lower().startswith("upload "):
                file_path = user_input[7:].strip()
                try:
                    attachment_info = file_manager.upload_file(file_path)
                    current_attachments.append(attachment_info)
                    print(f"[当前会话附件数: {len(current_attachments)}]")
                except FileNotFoundError as e:
                    print(f"[错误] {e}")
                continue
            
            if not user_input:
                continue
            
            # Process message through agent
            print("\nAssistant: ", end="", flush=True)
            
            response_parts = []
            async for chunk in master_agent.process_message(
                user_input, 
                session_id,
                attachments=current_attachments if current_attachments else None
            ):
                print(chunk, end="", flush=True)
                response_parts.append(chunk)
            
            print()  # New line after response
            
            # 清空当前附件（已处理）
            # 注意：这里可以选择保留附件供后续对话使用
            # current_attachments.clear()
            
        except KeyboardInterrupt:
            print("\n\nGoodbye!")
            break
        except Exception as e:
            print(f"\n[Error] {e}")
            import traceback
            traceback.print_exc()
            
            # Check if it's an API key issue
            error_str = str(e).lower()
            if "api_key" in error_str or "authentication" in error_str or "unauthorized" in error_str:
                print("\n[Hint] Please check your API key configuration in .env file:")
                print("  ZHIPU_API_KEY=your_key_here")
                print("  or")
                print("  QWEN_API_KEY=your_key_here")


async def main():
    print("\n" + "=" * 60)
    print(" AID Work Agent - Interactive Test (支持文件附件)")
    print("=" * 60)
    
    # Check if API key is configured
    has_api_key = False
    if settings.llm.provider == "zhipu" and settings.llm.zhipu.api_key:
        has_api_key = True
        print(f"\n[OK] Zhipu API key configured")
    elif settings.llm.provider == "qwen" and settings.llm.qwen.get_effective_keys():
        has_api_key = True
        print(f"\n[OK] Qwen API key configured")
    
    if not has_api_key:
        print("\n[Warning] No API key configured!")
        print("Please set up your API key in .env file:")
        print("  ZHIPU_API_KEY=your_key_here")
        print("  or")
        print("  QWEN_API_KEY=your_key_here")
        print("\nContinuing anyway (errors may occur)...")
    
    # Start interactive mode directly
    await interactive_mode()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nSession ended.")
