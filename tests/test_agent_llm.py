#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM-Driven Agent Interactive Test Script

Interactive test for the master agent with LLM-based intent understanding and planning.
This script requires API keys to be configured.
"""

import asyncio
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.core.agent import master_agent
from src.config.settings import settings


def print_sep(title):
    print("\n" + "=" * 60)
    print(f" {title}")
    print("=" * 60)


async def interactive_mode():
    """Interactive chat mode - user inputs questions, agent responds"""
    print_sep("Interactive Chat Mode")
    
    print(f"LLM Provider: {settings.llm.provider}")
    print(f"Model: {settings.llm.zhipu.model if settings.llm.provider == 'zhipu' else settings.llm.qwen.model}")
    print(f"Registered Tools: {', '.join(master_agent.tool_registry.list_tools())}")
    print()
    print("Commands:")
    print("  - Type your message and press Enter to chat")
    print("  - Type 'quit' or 'exit' to stop")
    print("  - Type 'clear' to start a new session")
    print()
    
    session_id = "interactive_session_001"
    
    while True:
        try:
            # Get user input
            user_input = input("\nYou: ").strip()
            
            # Check for commands
            if user_input.lower() in ["quit", "exit", "q"]:
                print("\nGoodbye!")
                break
            
            if user_input.lower() == "clear":
                session_id = f"interactive_session_{os.urandom(4).hex()}"
                print("\n[New session started]")
                continue
            
            if not user_input:
                continue
            
            # Process message through agent
            print("\nAssistant: ", end="", flush=True)
            
            response_parts = []
            async for chunk in master_agent.process_message(user_input, session_id):
                print(chunk, end="", flush=True)
                response_parts.append(chunk)
            
            print()  # New line after response
            
        except KeyboardInterrupt:
            print("\n\nGoodbye!")
            break
        except Exception as e:
            print(f"\n[Error] {e}")
            
            # Check if it's an API key issue
            error_str = str(e).lower()
            if "api_key" in error_str or "authentication" in error_str or "unauthorized" in error_str:
                print("\n[Hint] Please check your API key configuration in .env file:")
                print("  ZHIPU_API_KEY=your_key_here")
                print("  or")
                print("  QWEN_API_KEY=your_key_here")


async def main():
    print("\n" + "=" * 60)
    print(" AID Work Agent - Interactive Test")
    print("=" * 60)
    
    # Check if API key is configured
    has_api_key = False
    if settings.llm.provider == "zhipu" and settings.llm.zhipu.api_key:
        has_api_key = True
        print(f"\n[OK] Zhipu API key configured")
    elif settings.llm.provider == "qwen" and settings.llm.qwen.api_key:
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
