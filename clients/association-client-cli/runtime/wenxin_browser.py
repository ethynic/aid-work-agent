"""文心采集用的常驻调试浏览器管理。

复用单一常开 CDP 实例避风控（见 memory wenxin-reuse-session-anti-captcha：
新开浏览器问 2 个就触发验证码，同一会话连问 20+ 不触发）。

cli 启动 collect 时调 ensure_wenxin_browser() 确保浏览器开着：
- 已开（9222 可达）→ 直接复用
- 未开 → spawn detached Chrome（独立 user-data-dir，不碰用户日常 Chrome），
  脱离父进程，cli 退出后浏览器常驻，后续 collect 复用

文心无需登录，全新 profile 可用；首次若弹验证码，由 wenxin_collect.py 上报。
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

import httpx

CDP_URL = "http://localhost:9222"
CDP_PORT = 9222
READY_TIMEOUT_SECONDS = 15


def _chrome_candidates() -> list[str]:
    """探测可用的 Chrome 可执行文件路径：系统 Chrome 优先，Playwright bundle 兜底。"""
    seen: list[str] = []
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    for c in (
        os.path.join(program_files, "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(program_files_x86, "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(local_appdata, "Google", "Chrome", "Application", "chrome.exe"),
    ):
        if c and Path(c).is_file():
            seen.append(c)
    # Playwright bundle chromium（复用 playwright_check.py 的探测逻辑）
    pw_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or os.path.join(local_appdata, "ms-playwright")
    pw_root = Path(pw_path)
    if pw_root.exists():
        for d in sorted(pw_root.glob("chromium-*")):
            exe = d / "chrome-win" / "chrome.exe"
            if exe.is_file():
                seen.append(str(exe))
    # 去重保序
    deduped: list[str] = []
    for c in seen:
        if c not in deduped:
            deduped.append(c)
    return deduped


def _user_data_dir() -> str:
    """独立 user-data-dir，与 cli-config.json 同根（%APPDATA%/association-client/）。"""
    base = os.environ.get("APPDATA", str(Path.home()))
    return str(Path(base) / "association-client" / "wenxin-chrome-profile")


async def _cdp_ready() -> bool:
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{CDP_URL}/json/version")
            return resp.status_code == 200
    except Exception:
        return False


async def ensure_wenxin_browser() -> str:
    """确保 9222 调试浏览器开着，返回 cdp url。已开复用；没开 spawn detached Chrome。"""
    if await _cdp_ready():
        return CDP_URL

    candidates = _chrome_candidates()
    if not candidates:
        raise RuntimeError(
            "PLAYWRIGHT_NOT_INSTALLED: 未找到 Chrome，请安装 Google Chrome 或运行 "
            "`playwright install chromium`"
        )
    exe = candidates[0]

    cmd = [
        exe,
        f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={_user_data_dir()}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate,IsolateOrigins,site-per-process",
        "about:blank",
    ]
    # detached：脱离父进程，cli 退出后浏览器常驻（Windows 用 DETACHED_PROCESS）
    creationflags = 0
    if os.name == "nt":
        creationflags = (
            getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        )
    subprocess.Popen(cmd, creationflags=creationflags, close_fds=True)

    # 轮询等就绪
    for _ in range(int(READY_TIMEOUT_SECONDS * 2)):
        if await _cdp_ready():
            return CDP_URL
        await asyncio.sleep(0.5)
    raise RuntimeError(
        f"WENXIN_BROWSER_NOT_READY: {CDP_URL} 未在 {READY_TIMEOUT_SECONDS}s 内就绪"
    )
