#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
合同审批流程自动化脚本

使用 Playwright 自动化操作 OA 系统，完整流程：
1. 登录 OA 系统
2. 在待办中搜索合同编号
3. 打开审批单据
4. 执行审批操作（通过/驳回）
5. 关闭浏览器

用法：
    python contract_approval.py --contract-no "HT-2024-001234" --approve
    python contract_approval.py --contract-no "HT-2024-001234" --reject
    python contract_approval.py --contract-no "HT-2024-001234" --approve --comment "同意"
"""

import argparse
import asyncio
import json
import os
import sys
import re
from pathlib import Path
from typing import Optional

# 加载 skill 目录下的 .env 文件
_SCRIPT_DIR = Path(__file__).resolve().parent
_ENV_FILE = _SCRIPT_DIR.parent / ".env"
if _ENV_FILE.exists():
    from dotenv import load_dotenv
    load_dotenv(_ENV_FILE)


def _result(success: bool, message: str, data: dict = None, debug: str = ""):
    """统一输出 JSON 结果"""
    output = {"success": success, "message": message}
    if data:
        output["data"] = data
    if debug:
        output["debug"] = debug
    print(json.dumps(output, ensure_ascii=False, indent=2))


def _get_config() -> dict:
    """获取配置，优先从环境变量读取"""
    url = os.getenv("CONTRACT_OA_URL", "")
    username = os.getenv("CONTRACT_OA_USERNAME", "")
    password = os.getenv("CONTRACT_OA_PASSWORD", "")
    headless = os.getenv("CONTRACT_OA_HEADLESS", "false").lower() == "true"

    if not url:
        return None
    return {
        "url": url,
        "username": username,
        "password": password,
        "headless": headless,
    }


def _sanitize_debug(msg: str) -> str:
    """过滤敏感信息"""
    sensitive_patterns = [
        r'password["\s:=]+\S+',
        r'passwd["\s:=]+\S+',
        r'secret["\s:=]+\S+',
        r'token["\s:=]+\S+',
        r'api[_-]?key["\s:=]+\S+',
        r'access[_-]?key["\s:=]+\S+',
        r'private[_-]?key["\s:=]+\S+',
        r'auth[_-]?token["\s:=]+\S+',
    ]
    sanitized = msg
    for pattern in sensitive_patterns:
        sanitized = re.sub(
            pattern,
            lambda m: m.group(0).split('=')[0] + '=***',
            sanitized,
            flags=re.IGNORECASE,
        )
    return sanitized


# 全局浏览器实例（复用会话）
_browser = None
_page = None
_context = None
_playwright = None


async def _get_browser(headless: bool = False):
    """获取或创建浏览器实例"""
    global _browser, _page, _context, _playwright
    if _browser and _page:
        return _page

    try:
        from playwright.async_api import async_playwright

        _playwright = await async_playwright().start()
        _browser = await _playwright.chromium.launch(
            headless=headless,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        _context = await _browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )
        _page = await _context.new_page()
        return _page
    except ImportError:
        _result(
            False,
            "未安装 Playwright，请运行: pip install playwright && python -m playwright install chromium",
        )
        sys.exit(1)


async def _close_browser():
    """关闭浏览器"""
    global _browser, _page, _context, _playwright
    try:
        if _page:
            await _page.close()
        if _context:
            await _context.close()
        if _browser:
            await _browser.close()
        if _playwright:
            await _playwright.stop()
    except Exception:
        pass
    finally:
        _browser = None
        _page = None
        _context = None
        _playwright = None



async def _run_full_workflow(contract_no: str, approve: bool, comment: str = ""):
    """执行完整的合同审批流程：登录 → 处理忽略弹窗 → 进入流程 → 搜索 → 打开单据 → 审批 → 关闭"""
    try:
        # 第1步：登录
        config = _get_config()
        if not config:
            _result(
                False,
                "环境变量未配置",
                debug="请创建 .env 文件并设置 CONTRACT_OA_URL、CONTRACT_OA_USERNAME、CONTRACT_OA_PASSWORD",
            )
            return
        if not config["username"] or not config["password"]:
            _result(
                False,
                "用户名或密码未配置",
                debug="请在 .env 文件中设置 CONTRACT_OA_USERNAME 和 CONTRACT_OA_PASSWORD",
            )
            return

        _result(True, "正在登录 OA 系统...", data={"step": "login", "status": "started"})

        page = await _get_browser(headless=config["headless"])
        await _do_login(page, config)

        # 等待页面响应
        await asyncio.sleep(3)

        # 登录后检查是否有"忽略"按钮，有则点击
        _result(True, "登录完成，检查页面状态...", data={"step": "login", "status": "checking"})
        await _handle_ignore_popup(page)
        _result(True, "登录成功", data={"step": "login", "status": "completed", "url": page.url})

        # 第3步：进入流程页面
        _result(True, "正在进入流程页面...", data={"step": "enter_workflow", "status": "started"})
        await _enter_workflow_page(page)
        await asyncio.sleep(2)
        _result(True, "已进入流程页面", data={"step": "enter_workflow", "status": "completed"})

        # 第4步：搜索合同
        _result(True, f"正在搜索合同 {contract_no}...", data={"step": "search", "status": "started"})
        search_found = await _do_search(page, contract_no)

        if not search_found:
            _result(
                False,
                f"未找到合同 {contract_no}",
                debug=f"在待办中搜索 {contract_no} 未找到匹配结果，queryTable 中无 tr 行。",
            )
            await _close_browser()
            return

        _result(True, f"找到合同 {contract_no}", data={"step": "search", "status": "completed"})

        # 第5步：打开审批单据
        _result(True, "正在打开审批单据...", data={"step": "open_document", "status": "started"})
        doc_opened = await _do_open_document(page, contract_no)

        if not doc_opened:
            _result(
                False,
                "无法打开审批单据",
                debug="点击搜索结果 tr 后未能打开审批单据页面。",
            )
            await _close_browser()
            return

        _result(True, "审批单据已打开", data={"step": "open_document", "status": "completed"})

        # 第6步：执行审批
        action = "approve" if approve else "reject"
        action_text = "通过" if approve else "驳回"
        _result(
            True,
            f"正在执行审批操作（{action_text}）...",
            data={"step": "approval", "action": action, "status": "started"},
        )

        approval_result = await _do_approval(page, action, comment)

        # 第7步：关闭浏览器
        await _close_browser()

        if approval_result:
            _result(
                True,
                f"合同 {contract_no} 审批操作（{action_text}）完成",
                data={
                    "step": "approval",
                    "action": action,
                    "status": "completed",
                    "contract_no": contract_no,
                },
            )
        else:
            _result(
                False,
                f"合同 {contract_no} {action_text}操作失败",
                debug="审批按钮点击后未检测到成功状态，请检查页面元素定位。",
            )

    except Exception as e:
        error_msg = str(e)
        await _close_browser()
        _result(False, "审批流程发生错误", debug=_sanitize_debug(error_msg))


async def _do_login(page, config: dict):
    """执行登录操作"""
    await page.goto(config["url"], wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(2)

    # 定位用户名输入框
    username_input = await _find_element(page, [
        'input[name="username"]', 'input[name="userAccount"]', 'input[name="account"]',
        'input[name="loginname"]', 'input[name="j_username"]', 'input[type="text"]',
        '#username', '#userAccount', '#loginname',
    ], label_selectors=['label:has-text("用户名") + input', 'label:has-text("账号") + input'])

    if not username_input:
        raise RuntimeError(f"无法定位用户名输入框。页面标题: {await page.title()}, URL: {page.url}")

    # 定位密码输入框
    password_input = await _find_element(page, [
        'input[name="password"]', 'input[name="userPassword"]', 'input[name="j_password"]',
        'input[type="password"]', '#password', '#userPassword',
    ])

    if not password_input:
        raise RuntimeError("无法定位密码输入框")

    # 定位登录按钮
    login_button = await _find_element(page, [
        'button[type="submit"]', 'input[type="submit"]',
        'button:has-text("登录")', 'button:has-text("登 录")', 'button:has-text("Login")',
        'input[value="登录"]', 'input[value="Login"]',
        '#loginBtn', '#login_btn', '.login-btn', '.btn-login',
    ])

    # 填写并提交
    await username_input.fill(config["username"])
    await asyncio.sleep(0.5)
    await password_input.fill(config["password"])
    await asyncio.sleep(0.5)

    if login_button:
        await login_button.click()
    else:
        await password_input.press("Enter")

    await asyncio.sleep(3)


async def _handle_ignore_popup(page):
    """登录后检查是否有'忽略'按钮，有则点击并等待主页加载"""
    try:
        ignore_btn = await page.query_selector('#btnIgnore')
        if ignore_btn and await ignore_btn.is_visible():
            _result(True, "发现'忽略'按钮，正在点击...", data={"step": "ignore_popup", "status": "started"})
            await ignore_btn.click()
            # 等待主菜单出现
            try:
                await page.wait_for_selector('#mainMenuUl', timeout=15000)
            except Exception:
                await asyncio.sleep(3)
            _result(True, "已点击忽略，页面加载完成", data={"step": "ignore_popup", "status": "completed"})
            return True
    except Exception:
        pass
    return False


async def _enter_workflow_page(page):
    """点击主菜单中的'流程'按钮，进入流程中心"""
    workflow_tab = await page.query_selector('#mainMenuUl li[displayname="流程"]')
    if workflow_tab and await workflow_tab.is_visible():
        await workflow_tab.click()
        # 等待流程页面加载（target="TAB" 表示可能在 iframe 或新标签页中打开）
        await asyncio.sleep(3)
    else:
        raise RuntimeError("无法定位'流程'菜单按钮 (#mainMenuUl li[displayname='流程'])")


async def _do_search(page, contract_no: str) -> bool:
    """在流程中心的待办中搜索合同编号，返回是否找到结果"""
    # 定位搜索框 #queryFiledValue
    search_input = await page.query_selector('#queryFiledValue')
    if not search_input or not await search_input.is_visible():
        # 可能在 iframe 中，尝试切换
        frames = page.frames
        for frame in frames:
            search_input = await frame.query_selector('#queryFiledValue')
            if search_input and await search_input.is_visible():
                break
        if not search_input or not await search_input.is_visible():
            raise RuntimeError("无法定位搜索输入框 (#queryFiledValue)")

    # 清空并输入合同编号
    await search_input.fill("")
    await asyncio.sleep(0.3)
    await search_input.fill(contract_no)
    await asyncio.sleep(0.5)

    # 点击查询按钮（img 标签，title="查询"，src="img/toolbar_find.png"）
    search_button = await page.query_selector('img[title="查询"][src*="toolbar_find"]')
    if not search_button:
        # 在 iframe 中查找
        for frame in page.frames:
            search_button = await frame.query_selector('img[title="查询"][src*="toolbar_find"]')
            if search_button:
                break

    if search_button:
        await search_button.click()
    else:
        # 回退：按 Enter 搜索
        await search_input.press("Enter")

    # 等待搜索结果加载
    await asyncio.sleep(3)

    # 检查 #queryTable 的 tbody 中是否有 tr 行
    for frame in page.frames:
        query_table = await frame.query_selector('#queryTable tbody')
        if query_table:
            rows = await query_table.query_selector_all('tr')
            if rows:
                return True

    return False


async def _do_open_document(page, contract_no: str) -> bool:
    """点击搜索结果中的 tr 行打开审批单据"""
    for frame in page.frames:
        query_table = await frame.query_selector('#queryTable tbody')
        if not query_table:
            continue
        rows = await query_table.query_selector_all('tr')
        for row in rows:
            try:
                text = await row.inner_text()
                if contract_no in text:
                    await row.click()
                    # 等待单据页面加载（可能在新标签页或 iframe 中）
                    await asyncio.sleep(5)
                    return True
            except Exception:
                continue
    return False


async def _do_approval(page, action: str, comment: str = "") -> bool:
    """执行审批操作（通过/驳回），返回是否成功"""
    action_text = "通过" if action == "approve" else "驳回"

    if action == "reject":
        _result(
            True,
            f"驳回操作暂未实现，跳过",
            data={"action": "reject", "status": "not_implemented"},
            debug="驳回审批操作需要您提供驳回按钮的页面元素定位信息。",
        )
        return False

    # 通过：点击 #lb_submit 提交按钮
    submit_btn = await page.query_selector('#lb_submit')
    if not submit_btn:
        # 在 iframe 中查找
        for frame in page.frames:
            submit_btn = await frame.query_selector('#lb_submit')
            if submit_btn:
                break

    if not submit_btn:
        _result(
            False,
            "无法定位提交按钮",
            debug="未找到 #lb_submit 元素，请检查审批单据页面是否已正确加载。",
        )
        return False

    await submit_btn.click()
    await asyncio.sleep(3)
    return True


async def _find_element(page, selectors: list, label_selectors: list = None):
    """按选择器列表依次尝试定位元素，返回第一个可见的元素"""
    for selector in selectors:
        try:
            el = await page.query_selector(selector)
            if el and await el.is_visible():
                return el
        except Exception:
            continue

    if label_selectors:
        for selector in label_selectors:
            try:
                el = await page.query_selector(selector)
                if el and await el.is_visible():
                    return el
            except Exception:
                continue

    return None


def main():
    parser = argparse.ArgumentParser(description="合同审批流程自动化")
    parser.add_argument("--contract-no", required=True, help="合同编号")
    parser.add_argument("--approve", action="store_true", help="通过审批")
    parser.add_argument("--reject", action="store_true", help="驳回审批")
    parser.add_argument("--comment", default="", help="审批意见（可选）")

    args = parser.parse_args()

    if not args.approve and not args.reject:
        parser.error("必须指定 --approve 或 --reject")
    if args.approve and args.reject:
        parser.error("--approve 和 --reject 不能同时指定")

    action = "approve" if args.approve else "reject"
    asyncio.run(_run_full_workflow(args.contract_no, args.approve, args.comment))


if __name__ == "__main__":
    main()
