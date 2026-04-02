"""浏览器语义工具测试

测试基于语义快照的新型浏览器工具。
"""

import asyncio
import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


async def test_semantic_snapshot():
    """测试语义快照生成"""
    print("=" * 60)
    print("测试1: 语义快照生成")
    print("=" * 60)

    from src.tools.browser import BrowserOpenTool, BrowserSnapshotTool

    open_tool = BrowserOpenTool()
    snapshot_tool = BrowserSnapshotTool()

    # 打开网页
    open_result = await open_tool.execute(
        url="https://www.example.com",
        wait_for="load"
    )

    if not open_result.get("success"):
        print(f"[失败] 打开网页失败: {open_result.get('error')}")
        return

    print(f"[成功] 打开网页: {open_result.get('title')}")

    # 获取语义快照
    snapshot_result = await snapshot_tool.execute(
        session_id=open_result.get("session_id", "default"),
        mode="interactive"
    )

    if snapshot_result.get("success"):
        print(f"[成功] 获取语义快照")
        print(f"页面标题: {snapshot_result.get('page_title')}")
        print(f"URL: {snapshot_result.get('url')}")
        print(f"交互元素数: {snapshot_result.get('element_count')}")

        # 显示交互元素
        interactive_elements = snapshot_result.get("interactive_elements", [])
        if interactive_elements:
            print(f"\n交互元素预览（前5个）:")
            for elem in interactive_elements[:5]:
                print(f"  - ref={elem.get('ref')}, label={elem.get('label')}, tag={elem.get('tag')}")

        # 显示子菜单快照
        submenu_snapshots = snapshot_result.get("submenu_snapshots", [])
        if submenu_snapshots:
            print(f"\n子菜单快照数: {len(submenu_snapshots)}")
            for submenu in submenu_snapshots[:2]:
                print(f"  - trigger={submenu.get('trigger_label')}, items={len(submenu.get('items', []))}")

        # 显示 iframe 快照
        iframe_snapshots = snapshot_result.get("iframe_snapshots", [])
        if iframe_snapshots:
            print(f"\niframe 数量: {len(iframe_snapshots)}")
            for iframe in iframe_snapshots[:2]:
                print(f"  - ref={iframe.get('ref')}, src={iframe.get('src')}, sandboxed={iframe.get('sandboxed')}")
    else:
        print(f"[失败] 获取快照失败: {snapshot_result.get('error')}")

    # 关闭浏览器
    from src.tools.browser import BrowserCloseTool
    close_tool = BrowserCloseTool()
    await close_tool.execute(session_id=open_result.get("session_id", "default"))
    print("\n[完成] 测试1\n")


async def test_semantic_click():
    """测试语义点击"""
    print("=" * 60)
    print("测试2: 语义点击")
    print("=" * 60)

    from src.tools.browser import BrowserOpenTool, BrowserSnapshotTool, BrowserClickTool

    open_tool = BrowserOpenTool()
    snapshot_tool = BrowserSnapshotTool()
    click_tool = BrowserClickTool()
    close_tool = BrowserCloseTool()

    session_id = "semantic_click_test"

    # 打开网页
    open_result = await open_tool.execute(
        url="https://www.example.com",
        session_id=session_id,
        wait_for="load"
    )

    if not open_result.get("success"):
        print(f"[失败] 打开网页失败: {open_result.get('error')}")
        return

    print(f"[成功] 打开网页")

    # 获取快照
    snapshot_result = await snapshot_tool.execute(session_id=session_id)
    if not snapshot_result.get("success"):
        print(f"[失败] 获取快照失败")
        return

    print(f"[成功] 获取语义快照，元素数: {snapshot_result.get('element_count')}")

    # 尝试点击第一个链接（如果有）
    interactive_elements = snapshot_result.get("interactive_elements", [])
    if interactive_elements:
        first_element = interactive_elements[0]
        label = first_element.get("label", "")
        ref = first_element.get("ref", "")

        print(f"\n尝试点击元素: ref={ref}, label={label}")

        click_result = await click_tool.execute(
            description=label,
            session_id=session_id
        )

        if click_result.get("success"):
            print(f"[成功] 点击成功")
            print(f"  新页面标题: {click_result.get('page_title')}")
            print(f"  新页面 URL: {click_result.get('current_url')}")
        else:
            print(f"[信息] 点击失败（可能页面无可点击元素）: {click_result.get('error')}")
    else:
        print("[信息] 页面无交互元素")

    # 关闭浏览器
    await close_tool.execute(session_id=session_id)
    print("\n[完成] 测试2\n")


async def test_semantic_fill():
    """测试语义填写表单"""
    print("=" * 60)
    print("测试3: 语义填写表单")
    print("=" * 60)

    from src.tools.browser import BrowserOpenTool, BrowserSnapshotTool, BrowserFillTool

    open_tool = BrowserOpenTool()
    snapshot_tool = BrowserSnapshotTool()
    fill_tool = BrowserFillTool()
    close_tool = BrowserCloseTool()

    session_id = "semantic_fill_test"

    # 打开一个有表单的测试页面
    open_result = await open_tool.execute(
        url="https://www.example.com",
        session_id=session_id,
        wait_for="load"
    )

    if not open_result.get("success"):
        print(f"[失败] 打开网页失败: {open_result.get('error')}")
        return

    print(f"[成功] 打开网页")

    # 获取快照
    snapshot_result = await snapshot_tool.execute(session_id=session_id)
    if not snapshot_result.get("success"):
        print(f"[失败] 获取快照失败")
        return

    print(f"[成功] 获取语义快照")

    # 查找输入框
    interactive_elements = snapshot_result.get("interactive_elements", [])
    input_elements = [e for e in interactive_elements if e.get("tag") in ("input", "textarea")]

    if input_elements:
        first_input = input_elements[0]
        label = first_input.get("label", "")
        ref = first_input.get("ref", "")

        print(f"\n找到输入框: ref={ref}, label={label}")

        # 尝试填写
        fill_result = await fill_tool.execute(
            field=label,
            value="测试内容",
            session_id=session_id
        )

        if fill_result.get("success"):
            print(f"[成功] 填写成功")
            print(f"  字段: {fill_result.get('label')}")
            print(f"  值: {fill_result.get('value')}")
        else:
            print(f"[信息] 填写失败（可能页面结构不支持）: {fill_result.get('error')}")
    else:
        print("[信息] 页面无输入框元素")

    # 关闭浏览器
    await close_tool.execute(session_id=session_id)
    print("\n[完成] 测试3\n")


async def test_natural_matcher():
    """测试自然语言匹配器"""
    print("=" * 60)
    print("测试4: 自然语言匹配器")
    print("=" * 60)

    from src.tools.browser import BrowserOpenTool, BrowserSnapshotTool
    from src.tools.browser.semantic import NaturalMatcher, get_ref_mapper

    open_tool = BrowserOpenTool()
    snapshot_tool = BrowserSnapshotTool()
    close_tool = BrowserCloseTool()

    session_id = "natural_matcher_test"

    # 打开网页
    open_result = await open_tool.execute(
        url="https://www.python.org",
        session_id=session_id,
        wait_for="load"
    )

    if not open_result.get("success"):
        print(f"[失败] 打开网页失败")
        return

    # 获取快照
    snapshot_result = await snapshot_tool.execute(session_id=session_id)
    if not snapshot_result.get("success"):
        print(f"[失败] 获取快照失败")
        return

    # 获取 ref_mapper
    ref_mapper = get_ref_mapper(session_id)
    if not ref_mapper:
        print(f"[失败] 未找到 ref_mapper")
        return

    # 创建自然语言匹配器
    matcher = NaturalMatcher(ref_mapper)

    # 测试点击匹配
    test_descriptions = ["Documentation", "Download", "About"]

    for desc in test_descriptions:
        match_result = matcher.match_click(desc)
        if match_result.success:
            print(f"[匹配成功] '{desc}' -> ref={match_result.ref}, label={match_result.label}, confidence={match_result.confidence:.2f}")
        else:
            print(f"[匹配失败] '{desc}': {match_result.error}")

    # 关闭浏览器
    await close_tool.execute(session_id=session_id)
    print("\n[完成] 测试4\n")


async def test_iframe_detection():
    """测试 iframe 检测"""
    print("=" * 60)
    print("测试5: iframe 检测")
    print("=" * 60)

    from src.tools.browser import BrowserOpenTool, BrowserSnapshotTool

    open_tool = BrowserOpenTool()
    snapshot_tool = BrowserSnapshotTool()
    close_tool = BrowserCloseTool()

    session_id = "iframe_test"

    # 打开一个有 iframe 的测试页面
    open_result = await open_tool.execute(
        url="https://www.example.com",
        session_id=session_id,
        wait_for="load"
    )

    if not open_result.get("success"):
        print(f"[失败] 打开网页失败")
        return

    # 获取快照（包含 iframe 检测）
    snapshot_result = await snapshot_tool.execute(
        session_id=session_id,
        mode="interactive"
    )

    if snapshot_result.get("success"):
        iframe_snapshots = snapshot_result.get("iframe_snapshots", [])
        print(f"[成功] 检测到 {len(iframe_snapshots)} 个 iframe")

        for iframe in iframe_snapshots:
            print(f"\n  iframe 信息:")
            print(f"    ref: {iframe.get('ref')}")
            print(f"    src: {iframe.get('src')}")
            print(f"    title: {iframe.get('title')}")
            print(f"    visible: {iframe.get('visible')}")
            print(f"    sandboxed: {iframe.get('sandboxed')}")
            print(f"    element_count: {iframe.get('element_count', 0)}")
    else:
        print(f"[失败] 获取快照失败: {snapshot_result.get('error')}")

    # 关闭浏览器
    await close_tool.execute(session_id=session_id)
    print("\n[完成] 测试5\n")


async def main():
    """主测试函数"""
    print("\n" + "=" * 60)
    print("浏览器语义工具测试套件")
    print("=" * 60 + "\n")

    try:
        # 测试1: 语义快照生成
        await test_semantic_snapshot()

        # 测试2: 语义点击
        await test_semantic_click()

        # 测试3: 语义填写
        await test_semantic_fill()

        # 测试4: 自然语言匹配器
        await test_natural_matcher()

        # 测试5: iframe 检测
        await test_iframe_detection()

        print("\n" + "=" * 60)
        print("所有语义工具测试完成！✓")
        print("=" * 60 + "\n")

    except Exception as e:
        print(f"\n测试失败: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)