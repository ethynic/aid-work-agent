"""测试浏览器工具的Markdown获取功能（整合版本）"""

import asyncio
import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from src.tools.browser.browser_tool import (
    BrowserOpenTool,
    BrowserGetContentTool,
    BrowserCloseTool
)


async def test_default_markdown():
    """测试默认返回Markdown内容"""
    
    print("测试1: 默认获取Markdown内容")
    print("=" * 60)
    
    open_tool = BrowserOpenTool()
    content_tool = BrowserGetContentTool()
    close_tool = BrowserCloseTool()
    
    # 打开网页
    open_result = await open_tool.execute(url="https://www.example.com")
    
    if not open_result.get("success"):
        print(f"[失败] 打开网页失败: {open_result.get('error')}")
        return
    
    print(f"[成功] 打开网页: {open_result.get('title')}")
    
    # 获取内容（默认应该是markdown格式）
    content_result = await content_tool.execute()
    
    if content_result.get("success"):
        print(f"[成功] 获取内容")
        print(f"格式: {content_result.get('format')}")
        print(f"最终URL: {content_result.get('url')}")
        print(f"页面标题: {content_result.get('title')}")
        print(f"内容长度: {content_result.get('content_length')} 字符")
        print(f"\n内容预览（前300字符）:")
        print(content_result.get('content', '')[:300])
    else:
        print(f"[失败] 获取内容失败: {content_result.get('error')}")
    
    # 关闭浏览器
    await close_tool.execute()
    print("\n浏览器已关闭")


async def test_explicit_text():
    """测试明确指定text格式"""
    
    print("\n测试2: 明确指定text格式")
    print("=" * 60)
    
    open_tool = BrowserOpenTool()
    content_tool = BrowserGetContentTool()
    close_tool = BrowserCloseTool()
    
    # 打开网页
    await open_tool.execute(url="https://www.example.com")
    
    # 获取text格式内容
    content_result = await content_tool.execute(format="text")
    
    if content_result.get("success"):
        print(f"[成功] 获取内容")
        print(f"格式: {content_result.get('format')}")
        print(f"内容长度: {content_result.get('content_length')} 字符")
        print(f"\n内容预览（前200字符）:")
        print(content_result.get('content', '')[:200])
    
    # 关闭浏览器
    await close_tool.execute()


async def test_clean_content():
    """测试内容清理功能"""
    
    print("\n测试3: 测试内容清理功能")
    print("=" * 60)
    
    open_tool = BrowserOpenTool()
    content_tool = BrowserGetContentTool()
    close_tool = BrowserCloseTool()
    
    # 打开一个内容更丰富的页面
    await open_tool.execute(url="https://www.python.org")
    
    # 获取清理后的内容
    content_result = await content_tool.execute(clean_content=True)
    
    if content_result.get("success"):
        print(f"[成功] 获取清理后的内容")
        print(f"格式: {content_result.get('format')}")
        print(f"最终URL: {content_result.get('url')}")
        print(f"内容长度: {content_result.get('content_length')} 字符")
        print(f"\n内容预览（前300字符）:")
        print(content_result.get('content', '')[:300])
    
    # 关闭浏览器
    await close_tool.execute()


async def test_with_selector():
    """测试使用选择器获取特定内容"""
    
    print("\n测试4: 使用选择器获取特定内容")
    print("=" * 60)
    
    open_tool = BrowserOpenTool()
    content_tool = BrowserGetContentTool()
    close_tool = BrowserCloseTool()
    
    # 打开网页
    await open_tool.execute(url="https://www.python.org")
    
    # 使用选择器获取特定内容
    content_result = await content_tool.execute(selector=".container")
    
    if content_result.get("success"):
        print(f"[成功] 获取特定元素内容")
        print(f"格式: {content_result.get('format')}")
        print(f"选择器: .container")
        print(f"内容长度: {content_result.get('content_length')} 字符")
        print(f"\n内容预览（前200字符）:")
        print(content_result.get('content', '')[:200])
    
    # 关闭浏览器
    await close_tool.execute()


async def main():
    """主测试函数"""
    print("开始测试浏览器Markdown获取功能（整合版本）\n")
    
    try:
        # 测试默认markdown格式
        await test_default_markdown()
        
        # 测试明确指定text格式
        await test_explicit_text()
        
        # 测试内容清理功能
        await test_clean_content()
        
        # 测试使用选择器
        await test_with_selector()
        
        print("\n" + "=" * 60)
        print("所有测试完成！")
        print("=" * 60)
        
    except Exception as e:
        print(f"测试出错: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
